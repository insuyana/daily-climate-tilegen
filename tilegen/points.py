"""Series HORARIAS de ERA5 / ERA5-Land en puntos (pedido extra, aparte de los cubos).

Para validar contra estaciones meteorológicas hace falta el valor de cada hora en
el lugar de cada estación, no el promedio diario de una escena. Esto lee los
mismos stores horarios de Earth Data Hub que usa el source ``era5_edh``
(``edh_hourly_store`` de cada dataset YAML, misma clave en ~/.netrc) y escribe
un parquet largo por dataset y año:

    point_id, dataset, time_utc, variable, value, px_lat, px_lon

- Píxel: el más cercano a cada punto, por separado en cada grilla. Si ése no
  tiene datos (ERA5-Land es sólo tierra: un punto en la costa puede caer en el
  mar), se toma el píxel CON datos más cercano y se avisa en el log. px_lat /
  px_lon dicen siempre cuál se usó (lon en -180..180, como los puntos).
- Tiempo: tal cual viene del store, en UTC. ``tp`` es lo acumulado en la hora
  que TERMINA en time_utc (ERA5 estampa las acumulaciones al final de la hora).
- Unidades originales del store (K, m, m3/m3, m/s). ``wind_speed`` se calcula
  acá como sqrt(u10² + v10²).
- Lectura: los puntos se agrupan por el chunk espacial del store en que cae su
  píxel, y cada grupo baja su chunk una sola vez por año y variable.
- Re-correr: un año se saltea si su archivo ya existe con el mismo período,
  las mismas variables y los mismos puntos (anotados en la metadata del
  parquet). El año en curso, incompleto, se rehace cuando el store suma horas.
"""
from __future__ import annotations

import hashlib
import logging
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd

log = logging.getLogger("tilegen.points")

#: variables que se piden en el store (las de suelo: capa 1 = 0-7 cm,
#: 2 = 7-28 cm, 3 = 28-100 cm, 4 = 100-289 cm)
STORE_VARS = {
    "era5": ["t2m", "tp", "u10", "v10", "swvl1", "swvl2", "swvl3", "swvl4"],
    "era5-land": ["swvl1", "swvl2", "swvl3", "swvl4"],
}
#: derivadas: nombre -> variables del store que necesita
DERIVED = {"wind_speed": ("u10", "v10")}
#: pedidas pero ausentes de EDH (ver fg10_max en era5.yaml)
NOT_IN_EDH = {"fg10", "i10fg"}

#: radio (en píxeles) donde buscar un píxel con datos si el más cercano está vacío
FALLBACK_RADIUS = 5


def default_variables(dataset):
    out = list(STORE_VARS[dataset])
    for name, need in DERIVED.items():
        if all(v in out for v in need):
            out.append(name)
    return out


def read_points(path, storage_options=None):
    p = pd.read_csv(path, storage_options=storage_options)
    missing = {"point_id", "lat", "lon"} - set(p.columns)
    if missing:
        raise ValueError(f"points CSV without columns {sorted(missing)}")
    if p["point_id"].duplicated().any():
        dup = p.loc[p["point_id"].duplicated(), "point_id"].tolist()
        raise ValueError(f"repeated point_id in points CSV: {dup[:5]}")
    return p.reset_index(drop=True)


def points_hash(points):
    s = points[["point_id", "lat", "lon"]].to_csv(index=False, float_format="%.6f")
    return hashlib.sha1(s.encode()).hexdigest()[:12]


def _coords(ds):
    lat = next(c for c in ("latitude", "lat") if c in ds.coords)
    lon = next(c for c in ("longitude", "lon") if c in ds.coords)
    tdim = next(d for d in ("valid_time", "time") if d in ds.dims)
    return lat, lon, tdim


def _chunks(da):
    """Tamaño de chunk del store (t, lat, lon); sin encoding, la variable entera."""
    ch = da.encoding.get("chunks") or da.encoding.get("preferred_chunks")
    if isinstance(ch, dict):
        ch = tuple(ch.get(d, n) for d, n in zip(da.dims, da.shape))
    return tuple(ch) if ch else tuple(da.shape)


def _nearest(lat_vals, lon_vals, plat, plon):
    """Índices (i, j) del píxel más cercano; lon con vuelta en 0/360."""
    i = np.abs(lat_vals[None, :] - plat[:, None]).argmin(axis=1)
    if lon_vals.max() > 180:
        plon = plon % 360
    d = np.abs(lon_vals[None, :] - plon[:, None])
    d = np.minimum(d, 360 - d)
    j = d.argmin(axis=1)
    return i, j


def _km(lat1, lon1, lat2, lon2):
    lat1, lon1, lat2, lon2 = map(np.radians, (lat1, lon1, lat2, lon2))
    a = (np.sin((lat2 - lat1) / 2) ** 2
         + np.cos(lat1) * np.cos(lat2) * np.sin((lon2 - lon1) / 2) ** 2)
    return 6371.0 * 2 * np.arcsin(np.sqrt(a))


def _lon180(x):
    return (np.asarray(x, dtype="float64") + 180) % 360 - 180


def select_pixels(ds, points, probe_var, probe_time):
    """Píxel de cada punto: (i, j) en la grilla del store.

    El más cercano; si en ``probe_time`` vale NaN, el más cercano con datos
    dentro de FALLBACK_RADIUS píxeles. La máscara de tierra/mar no cambia con el
    tiempo, así que alcanza con mirar una hora.
    """
    lat, lon, tdim = _coords(ds)
    lat_vals = ds[lat].values.astype("float64")
    lon_vals = ds[lon].values.astype("float64")
    i, j = _nearest(lat_vals, lon_vals, points["lat"].values, points["lon"].values)

    snap = ds[probe_var].sel({tdim: probe_time})
    vals = _pick(snap, i, j)
    for k in np.flatnonzero(np.isnan(vals)):
        pid = points["point_id"].iat[k]
        i0, i1 = max(0, i[k] - FALLBACK_RADIUS), min(len(lat_vals), i[k] + FALLBACK_RADIUS + 1)
        j0, j1 = max(0, j[k] - FALLBACK_RADIUS), min(len(lon_vals), j[k] + FALLBACK_RADIUS + 1)
        win = snap.isel({lat: slice(i0, i1), lon: slice(j0, j1)}).values
        ii, jj = np.nonzero(~np.isnan(win))
        if ii.size == 0:
            log.warning("%s: no pixel with data within %d px; keeping the nearest "
                        "(its series will be empty)", pid, FALLBACK_RADIUS)
            continue
        dist = _km(points["lat"].iat[k], points["lon"].iat[k],
                   lat_vals[i0 + ii], lon_vals[j0 + jj])
        best = dist.argmin()
        log.warning("%s: nearest pixel has no data (sea?) -> using the nearest pixel "
                    "with data, %.1f km away (%.3f, %.3f)", pid, dist[best],
                    lat_vals[i0 + ii[best]], _lon180(lon_vals[j0 + jj[best]]))
        i[k], j[k] = i0 + ii[best], j0 + jj[best]
    return i, j


def _pick(da2d, i, j):
    """Valores de un DataArray 2D (lat, lon) en los píxeles (i, j), leyendo sólo
    la caja de cada chunk espacial."""
    ci, cj = _chunks(da2d)[-2:]
    out = np.full(len(i), np.nan, dtype="float64")
    lat, lon = da2d.dims[-2:]
    for (gi, gj), idx in _groups(i, j, ci, cj).items():
        a0, a1 = i[idx].min(), i[idx].max() + 1
        b0, b1 = j[idx].min(), j[idx].max() + 1
        box = da2d.isel({lat: slice(a0, a1), lon: slice(b0, b1)}).values
        out[idx] = box[i[idx] - a0, j[idx] - b0]
    return out


def _groups(i, j, ci, cj):
    """{(chunk_i, chunk_j): índices de los puntos cuyo píxel cae ahí}."""
    g = {}
    for k, key in enumerate(zip(i // ci, j // cj)):
        g.setdefault(key, []).append(k)
    return {k: np.array(v) for k, v in g.items()}


def _with_retry(fn, tries=4, wait=5.0):
    for n in range(tries):
        try:
            return fn()
        except (OSError, RuntimeError, TimeoutError) as e:   # red / HTTP de EDH
            if n == tries - 1:
                raise
            log.warning("EDH read failed (%s), retrying in %.0f s", e, wait * (n + 1))
            time.sleep(wait * (n + 1))


def read_year(ds, store_vars, i, j, t0, t1, workers=8):
    """{variable: array (horas, puntos)} y las horas leídas, entre t0 y t1 inclusive."""
    lat, lon, tdim = _coords(ds)
    times = pd.DatetimeIndex(ds[tdim].values)
    h0 = times.searchsorted(t0, side="left")
    h1 = times.searchsorted(t1, side="right")
    tsel = slice(h0, h1)
    hours = times[tsel]
    ci, cj = _chunks(ds[store_vars[0]])[-2:]
    groups = _groups(i, j, ci, cj)

    def one(v, idx):
        a0, a1 = i[idx].min(), i[idx].max() + 1
        b0, b1 = j[idx].min(), j[idx].max() + 1
        box = ds[v].isel({tdim: tsel, lat: slice(a0, a1), lon: slice(b0, b1)})
        vals = _with_retry(lambda: box.values)
        return v, idx, vals[:, i[idx] - a0, j[idx] - b0]

    out = {v: np.full((len(hours), len(i)), np.nan, dtype="float32") for v in store_vars}
    tasks = [(v, idx) for v in store_vars for idx in groups.values()]
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for v, idx, vals in ex.map(lambda a: one(*a), tasks):
            out[v][:, idx] = vals
    return out, hours


def schema():
    import pyarrow as pa

    cat = pa.dictionary(pa.int32(), pa.string())
    return pa.schema([
        ("point_id", cat), ("dataset", cat),
        ("time_utc", pa.timestamp("us", tz="UTC")),
        ("variable", cat), ("value", pa.float32()),
        ("px_lat", pa.float64()), ("px_lon", pa.float64()),
    ])


def long_table(dataset, point_ids, variable, arr, hours, px_lat, px_lon):
    """Tabla larga de UNA variable: una fila por (punto, hora), punto por punto.

    Se arma directo en Arrow (sin pandas) para no duplicar en memoria los ~3
    millones de filas por variable y año.
    """
    import pyarrow as pa

    n_t, n_p = arr.shape
    zeros = pa.array(np.zeros(n_t * n_p, dtype="int32"))
    t = np.asarray(hours.values, dtype="datetime64[us]")
    return pa.Table.from_arrays([
        pa.DictionaryArray.from_arrays(
            pa.array(np.repeat(np.arange(n_p, dtype="int32"), n_t)),
            pa.array([str(p) for p in point_ids])),
        pa.DictionaryArray.from_arrays(zeros, pa.array([dataset])),
        pa.array(np.tile(t, n_p), type=pa.timestamp("us", tz="UTC")),
        pa.DictionaryArray.from_arrays(zeros, pa.array([variable])),
        pa.array(arr.T.reshape(-1).astype("float32")),
        pa.array(np.repeat(px_lat, n_t)),
        pa.array(np.repeat(px_lon, n_t)),
    ], schema=schema())


def _meta(t0, t1, variables, phash):
    return {"tilegen.t0": str(t0), "tilegen.t1": str(t1),
            "tilegen.variables": ",".join(variables), "tilegen.points": phash}


def _done(fs, path, want):
    """¿El archivo ya está y cubre exactamente lo pedido?"""
    import pyarrow.parquet as pq

    if not fs.exists(path):
        return False
    try:
        with fs.open(path, "rb") as f:
            md = pq.read_schema(f).metadata or {}
    except Exception as e:  # archivo roto: se rehace
        log.warning("%s unreadable (%s): redoing it", path, e)
        return False
    have = {k.decode(): v.decode() for k, v in md.items() if k.startswith(b"tilegen.")}
    return have == want


def extract(ds, dataset, points, start, end, out, variables=None, fs=None,
            workers=8, overwrite=False):
    """Escribe ``out/<dataset>/<año>.parquet`` para cada año entre start y end.

    ``ds`` es el store horario ya abierto. Devuelve {año: 'written'|'skipped'}.
    """
    import fsspec
    import pyarrow.parquet as pq

    if fs is None:
        fs, out = fsspec.core.url_to_fs(out)
    variables = list(variables) if variables else default_variables(dataset)
    for v in [v for v in variables if v in NOT_IN_EDH]:
        log.warning("%s: %s is not in the EDH hourly store (see fg10_max in era5.yaml)"
                    " -> skipped", dataset, v)
    variables = [v for v in variables if v not in NOT_IN_EDH]
    unknown = [v for v in variables if v not in ds and v not in DERIVED]
    if unknown:
        log.warning("%s: %s not in the store -> skipped", dataset, unknown)
    variables = [v for v in variables if v not in unknown]
    derived = [v for v in variables if v in DERIVED]
    for v in derived:
        if not all(n in ds for n in DERIVED[v]):
            raise ValueError(f"{dataset}: {v} needs {DERIVED[v]}, not all in the store")
    store_vars = list(dict.fromkeys(
        [v for v in variables if v not in DERIVED]
        + [n for v in derived for n in DERIVED[v]]))
    if not store_vars:
        log.warning("%s: no variables to extract", dataset)
        return {}

    _, _, tdim = _coords(ds)
    times = pd.DatetimeIndex(ds[tdim].values)
    start = max(pd.Timestamp(start), times[0])
    last = pd.Timestamp(end) + pd.Timedelta(hours=23) if end is not None else times[-1]
    end_h = min(last, times[-1])
    if end_h < start:
        raise ValueError(f"{dataset}: store ends {times[-1]}, before {start}")
    if last > times[-1]:
        log.info("%s: store ends %s, extracting up to there", dataset, times[-1])

    i, j = select_pixels(ds, points, store_vars[0], start)
    lat, lon, _ = _coords(ds)
    px_lat = ds[lat].values[i].astype("float64")
    px_lon = _lon180(ds[lon].values[j])
    phash = points_hash(points)

    status = {}
    for year in range(start.year, end_h.year + 1):
        t0 = max(start, pd.Timestamp(f"{year}-01-01"))
        t1 = min(end_h, pd.Timestamp(f"{year}-12-31 23:00"))
        path = f"{out.rstrip('/')}/{dataset}/{year}.parquet"
        want = _meta(t0, t1, variables, phash)
        if not overwrite and _done(fs, path, want):
            log.info("%s %d: already written, skipping", dataset, year)
            status[year] = "skipped"
            continue
        t_start = time.time()
        vals, hours = read_year(ds, store_vars, i, j, t0, t1, workers=workers)
        for v in derived:
            a, b = (vals[n] for n in DERIVED[v])
            vals[v] = np.sqrt(a.astype("float64") ** 2 + b ** 2).astype("float32")
        # local primero y después una sola subida: nunca queda un archivo a
        # medias en el destino que el próximo run tome por terminado
        with tempfile.TemporaryDirectory() as tmp:
            local = Path(tmp) / f"{dataset}_{year}.parquet"
            rows = 0
            with pq.ParquetWriter(local, schema().with_metadata(want),
                                  compression="zstd") as w:
                for v in variables:   # un row group por variable
                    w.write_table(long_table(dataset, points["point_id"], v, vals[v],
                                             hours, px_lat, px_lon))
                    rows += vals[v].size
            fs.makedirs(f"{out.rstrip('/')}/{dataset}", exist_ok=True)
            fs.put_file(str(local), path)
            size = local.stat().st_size
        log.info("%s %d: %d rows, %.0f MB -> %s (%.0f s)", dataset, year, rows,
                 size / 1e6, path, time.time() - t_start)
        status[year] = "written"
    return status
