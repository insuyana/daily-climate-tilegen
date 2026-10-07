"""ERA5 via the Copernicus CDS API (requires ~/.cdsapirc).

Uses the daily-statistics derived dataset so no local hourly aggregation is
needed. One CDS request per (variable, month) — the CDS queue penalizes many
small requests, so monthly batching is the sweet spot. Requests are
sequential (parallel_fetch = False) to be polite with the queue.

If the pipeline runs with a bbox, it sets ``self.area`` (tile-aligned) and
only that window is requested from CDS — much faster than global.

VENTANA COMPARTIDA (``cds_fetch_area`` en el YAML del dataset)
-------------------------------------------------------------
Con ``area`` por escena, un backfill largo hace un pedido por (variable, mes) y
**por escena**: 380 meses x 21 escenas = ~8.000 pedidos. La cola del CDS tarda
~20 min por pedido haga lo que haga, así que el costo está en la CANTIDAD de
pedidos y no en su tamaño — ningún paralelismo lo arregla (medido el 26-ago-2026
con fg10_max: 3 gránulos/hora, ~110 días proyectados).

Con ``cds_fetch_area`` se pide UNA ventana que cubre todas las escenas y el
recorte a cada escena se hace local, en ``asset_to_dataarray``. Son 380 pedidos
en vez de 8.000. El archivo se cachea por nombre (el tag lleva la ventana), así
que la primera escena lo baja y las otras 20 lo leen del disco. Para que exista
cuando llegan, ``shared_downloads`` le dice al pipeline que no lo borre.
"""
from __future__ import annotations

import logging
import shutil
import threading
import zipfile

from ..utils import month_groups, nc_lock
from .base import Asset, DataSource, Granule

log = logging.getLogger("tilegen.era5")

# Días pedidos al CDS en este proceso, por archivo. Compartido entre escenas
# (cada una tiene su instancia del source) porque con cds_fetch_area todas
# apuntan al mismo archivo.
_pedidos: dict = {}
_cache_lock = threading.Lock()


def _dates_in(path):
    import xarray as xr

    with nc_lock, xr.open_dataset(path) as ds:
        tdim = "valid_time" if "valid_time" in ds else "time"
        return {t.astype("datetime64[D]").astype(object) for t in ds[tdim].values}


def _ensure_netcdf(path):
    """CDS sometimes wraps the NetCDF in a zip; unwrap it in place."""
    with open(path, "rb") as f:
        if f.read(2) != b"PK":
            return
    extracted = path.with_suffix(".extracted.nc")
    with zipfile.ZipFile(path) as z:
        member = next(n for n in z.namelist() if n.endswith(".nc"))
        with z.open(member) as src, open(extracted, "wb") as dst:
            shutil.copyfileobj(src, dst)
    path.unlink()
    extracted.rename(path)


class Era5CdsSource(DataSource):
    parallel_fetch = False
    area = None  # (minx, miny, maxx, maxy), set by the pipeline when using --bbox

    @property
    def fetch_area(self):
        """La ventana que se le pide al CDS: la común si está configurada."""
        shared = self.cfg.cds_fetch_area
        return tuple(shared) if shared else self.area

    @property
    def shared_downloads(self):
        return self.cfg.cds_fetch_area is not None

    def granules(self, variables, dates):
        out = []
        for v in variables:
            for (y, m), ds_ in sorted(month_groups(dates).items()):
                out.append(Granule(f"{self.cfg.name}:{v}:{y}-{m:02d}", v, sorted(ds_)))
        return out

    def fetch(self, granule):
        v = granule.variable
        y, m = granule.dates[0].year, granule.dates[0].month
        area = self.fetch_area
        # el tag lleva la ventana -> con cds_fetch_area todas las escenas apuntan
        # al MISMO archivo, así que la primera lo baja y el resto lo cachea.
        tag = "" if area is None else "_" + "_".join(str(int(x)) for x in area)
        target = self.workdir / f"{self.cfg.name}_{v}_{y}{m:02d}{tag}.nc"
        wanted = set(granule.dates)
        with _cache_lock:
            have = _dates_in(target) if target.exists() else set()
            asked = _pedidos.get(target, set())
            # Re-bajar si al archivo le faltan días, salvo que ya se hayan pedido
            # en ESTA corrida (el CDS no los tiene todavía: no insistir con cada
            # escena). Antes el primer archivo del mes se reusaba para siempre y
            # el mes en curso quedaba congelado en los días de esa primera bajada.
            if not wanted <= have and not wanted <= asked:
                days = sorted(wanted | have | asked)
                self._retrieve(v, y, m, days, area, target)
                _pedidos[target] = set(days)
        return self._assets(granule, target)

    def _retrieve(self, v, y, m, days, area, target):
        import cdsapi

        vcfg = self.cfg.variables[v]
        request = {
            "variable": [vcfg.cds_variable],
            "daily_statistic": vcfg.daily_statistic,
            "time_zone": "utc+00:00",
            "frequency": "1_hourly",
            "year": [str(y)],
            "month": [f"{m:02d}"],
            "day": [f"{d.day:02d}" for d in days],
        }
        # product_type sale del YAML en vez de estar fijo acá: sólo el derived
        # de single-levels lo tiene (ahí hay ensemble además de reanalysis).
        # El de ERA5-Land no publica ese campo, y mandarlo rompe el pedido.
        if getattr(self.cfg, "cds_product_type", None):
            request["product_type"] = self.cfg.cds_product_type
        if area is not None:
            minx, miny, maxx, maxy = area
            request["area"] = [maxy, minx, miny, maxx]  # N, W, S, E
        log.info("CDS request %s %d-%02d, %d day(s) (may sit in the queue for a while)",
                 v, y, m, len(days))
        # a un temporal y después rename: si el pedido se corta, el archivo
        # bueno anterior sigue en su lugar
        tmp = target.with_suffix(".part.nc")
        cdsapi.Client(quiet=True).retrieve(self.cfg.cds_dataset, request, str(tmp))
        _ensure_netcdf(tmp)
        tmp.replace(target)

    def _assets(self, granule, target):
        import xarray as xr

        wanted = set(granule.dates)
        assets = []
        with xr.open_dataset(target) as ds:
            tdim = "valid_time" if "valid_time" in ds else "time"
            for i, t in enumerate(ds[tdim].values):
                d = t.astype("datetime64[D]").astype(object)
                if d in wanted:
                    assets.append(Asset(variable=granule.variable, date=d,
                                        path=target, time_index=i))
        return assets
