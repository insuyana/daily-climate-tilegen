"""Series horarias en puntos (tilegen extract-points).

Lo que se protege: que cada punto tome su píxel más cercano (con la vuelta de
lon 0..360 del store), que caiga al píxel con datos más cercano si el suyo está
vacío (la costa en ERA5-Land), que los valores no se crucen entre puntos ni
horas al pasar a la tabla larga, y que re-correr saltee lo que ya está.
"""
import logging

import numpy as np
import pandas as pd
import pyarrow.parquet as pq
import pytest
import xarray as xr

from tilegen import points as pts


def _store(desde="2019-12-31 22:00", horas=6, chunks=(4, 2, 2), mar=None):
    """Store horario falso 4x4 en lon 0..360. Cada valor codifica
    (hora, fila, columna) para poder verificar que no se cruzan."""
    t = pd.date_range(desde, periods=horas, freq="h")
    lat = np.array([-30.0, -30.25, -30.5, -30.75])
    lon = np.array([297.0, 297.25, 297.5, 297.75])          # = -63 .. -62.25
    h, i, j = np.meshgrid(np.arange(horas), np.arange(4), np.arange(4), indexing="ij")
    base = (1000 * h + 10 * i + j).astype("float32")
    data = {}
    for v, off in (("t2m", 0), ("tp", 0.5), ("u10", 0), ("v10", 0), ("swvl1", 0.25)):
        a = base + off
        if v == "u10":
            a = np.full_like(base, 3.0)
        if v == "v10":
            a = np.full_like(base, 4.0)
        if mar is not None:
            a = a.copy()
            a[:, mar[0], mar[1]] = np.nan
        data[v] = (("valid_time", "latitude", "longitude"), a)
    ds = xr.Dataset(data, coords={"valid_time": t, "latitude": lat, "longitude": lon})
    for v in ds.data_vars:
        ds[v].encoding["chunks"] = chunks
    return ds


def _puntos(*filas):
    return pd.DataFrame(filas, columns=["point_id", "source", "lat", "lon"])


# --- selección de píxel ---------------------------------------------------

def test_pixel_mas_cercano_con_lon_negativa():
    ds = _store()
    p = _puntos(("a", "x", -30.26, -62.74), ("b", "x", -30.70, -62.30))
    i, j = pts.select_pixels(ds, p, "t2m", ds.valid_time.values[0])
    assert list(i) == [1, 3] and list(j) == [1, 3]


def test_si_el_pixel_esta_vacio_toma_el_mas_cercano_con_datos(caplog):
    ds = _store(mar=(0, 0))                      # esquina "en el mar"
    p = _puntos(("costa", "x", -30.0, -63.0), ("tierra", "x", -30.5, -62.5))
    with caplog.at_level(logging.WARNING, logger="tilegen.points"):
        i, j = pts.select_pixels(ds, p, "swvl1", ds.valid_time.values[0])
    # el vecino con datos más cercano es (0,1) o (1,0); a esa latitud un grado
    # de lon mide menos que uno de lat, así que gana la misma fila
    assert (i[0], j[0]) == (0, 1)
    assert (i[1], j[1]) == (2, 2)                # el otro punto no se mueve
    assert "costa" in caplog.text and "km" in caplog.text


# --- extracción -----------------------------------------------------------

def _leer(path):
    return pq.read_table(path).to_pandas()


def test_tabla_larga_valores_y_coordenadas(tmp_path):
    ds = _store()
    p = _puntos(("a", "x", -30.26, -62.74), ("b", "x", -30.70, -62.30))
    st = pts.extract(ds, "era5", p, "2020-01-01", "2020-01-01", str(tmp_path),
                     variables=["t2m", "tp", "wind_speed"], workers=2)
    assert st == {2020: "written"}
    df = _leer(tmp_path / "era5" / "2020.parquet")
    # store: 22:00 del 31-dic .. 03:00 del 1-ene -> 4 horas de 2020
    assert len(df) == 2 * 4 * 3
    assert set(df["variable"]) == {"t2m", "tp", "wind_speed"}
    assert str(df["time_utc"].dt.tz) == "UTC"
    a = df[(df.point_id == "a") & (df.variable == "t2m")].sort_values("time_utc")
    # hora 2 del store = 00:00 del 1-ene; píxel (1,1)
    assert a["value"].tolist() == [1000 * h + 11 for h in (2, 3, 4, 5)]
    assert a["time_utc"].iloc[0] == pd.Timestamp("2020-01-01 00:00", tz="UTC")
    assert a["px_lat"].iloc[0] == -30.25 and a["px_lon"].iloc[0] == pytest.approx(-62.75)
    b_tp = df[(df.point_id == "b") & (df.variable == "tp")].sort_values("time_utc")
    assert b_tp["value"].iloc[0] == 2000 + 33 + 0.5   # tp tal cual, sin desacumular
    assert np.allclose(df[df.variable == "wind_speed"]["value"], 5.0)


def test_un_archivo_por_anio(tmp_path):
    ds = _store()
    p = _puntos(("a", "x", -30.26, -62.74))
    st = pts.extract(ds, "era5", p, "2019-12-31", None, str(tmp_path), variables=["t2m"])
    assert st == {2019: "written", 2020: "written"}
    assert len(_leer(tmp_path / "era5" / "2019.parquet")) == 2
    assert len(_leer(tmp_path / "era5" / "2020.parquet")) == 4


def test_rafaga_se_avisa_y_se_saltea(tmp_path, caplog):
    ds = _store()
    p = _puntos(("a", "x", -30.26, -62.74))
    with caplog.at_level(logging.WARNING, logger="tilegen.points"):
        pts.extract(ds, "era5", p, "2020-01-01", None, str(tmp_path),
                    variables=["t2m", "fg10"])
    assert "fg10" in caplog.text
    assert set(_leer(tmp_path / "era5" / "2020.parquet")["variable"]) == {"t2m"}


def test_varios_puntos_en_el_mismo_pixel(tmp_path):
    ds = _store()
    p = _puntos(("a", "x", -30.26, -62.74), ("a2", "x", -30.24, -62.76))
    pts.extract(ds, "era5", p, "2020-01-01", None, str(tmp_path), variables=["t2m"])
    df = _leer(tmp_path / "era5" / "2020.parquet")
    va = df[df.point_id == "a"].sort_values("time_utc")["value"].tolist()
    vb = df[df.point_id == "a2"].sort_values("time_utc")["value"].tolist()
    assert va == vb and len(va) == 4


# --- re-correr ------------------------------------------------------------

def test_re_correr_saltea_lo_escrito(tmp_path):
    ds = _store()
    p = _puntos(("a", "x", -30.26, -62.74))
    kw = dict(variables=["t2m"], workers=1)
    pts.extract(ds, "era5", p, "2019-12-31", None, str(tmp_path), **kw)
    assert pts.extract(ds, "era5", p, "2019-12-31", None, str(tmp_path), **kw) == {
        2019: "skipped", 2020: "skipped"}


def test_el_anio_incompleto_se_rehace_cuando_el_store_crece(tmp_path):
    p = _puntos(("a", "x", -30.26, -62.74))
    kw = dict(variables=["t2m"], workers=1)
    pts.extract(_store(horas=6), "era5", p, "2019-12-31", None, str(tmp_path), **kw)
    st = pts.extract(_store(horas=10), "era5", p, "2019-12-31", None, str(tmp_path), **kw)
    assert st == {2019: "skipped", 2020: "written"}
    assert len(_leer(tmp_path / "era5" / "2020.parquet")) == 8


def test_cambiar_los_puntos_rehace(tmp_path):
    ds = _store()
    kw = dict(variables=["t2m"], workers=1)
    pts.extract(ds, "era5", _puntos(("a", "x", -30.26, -62.74)), "2020-01-01", None,
                str(tmp_path), **kw)
    st = pts.extract(ds, "era5", _puntos(("a", "x", -30.26, -62.74),
                                         ("b", "x", -30.7, -62.3)),
                     "2020-01-01", None, str(tmp_path), **kw)
    assert st == {2020: "written"}


def test_csv_con_point_id_repetido(tmp_path):
    f = tmp_path / "p.csv"
    _puntos(("a", "x", 1, 2), ("a", "x", 3, 4)).to_csv(f, index=False)
    with pytest.raises(ValueError, match="repeated"):
        pts.read_points(f)


# --- red ------------------------------------------------------------------

def test_un_502_de_edh_se_reintenta(monkeypatch):
    """aiohttp.ClientResponseError no es OSError: antes cortaba la corrida."""
    import aiohttp
    from multidict import CIMultiDict, CIMultiDictProxy
    from yarl import URL

    monkeypatch.setattr(pts.time, "sleep", lambda s: None)
    intentos = []

    def lee():
        intentos.append(1)
        if len(intentos) < 3:
            info = aiohttp.RequestInfo(url=URL("https://edh/x"), method="GET",
                                       headers=CIMultiDictProxy(CIMultiDict()))
            raise aiohttp.ClientResponseError(info, (), status=502, message="Bad Gateway")
        return "ok"

    assert pts._with_retry(lee) == "ok" and len(intentos) == 3


def test_un_error_de_programacion_no_se_reintenta(monkeypatch):
    monkeypatch.setattr(pts.time, "sleep", lambda s: None)
    with pytest.raises(KeyError):
        pts._with_retry(lambda: {}["x"])
