"""ERA5 via the Copernicus CDS API (requires ~/.cdsapirc).

Uses the daily-statistics derived dataset so no local hourly aggregation is
needed. Requests are sequential (parallel_fetch = False) to be polite with
the queue.

Un pedido por (ESTADÍSTICA, mes): la cola del CDS tarda lo mismo con una
variable que con cuatro, así que lo que hay que bajar es la cantidad de
pedidos. `daily_statistic` es un campo suelto del pedido y no va por variable,
así que lo que se puede juntar en una llamada es lo que comparte estadística:
t2m_mean + sd_mean + rsn_mean en una, t2m_max + fg10_max en otra, y así. El
NetCDF vuelve con todas adentro y se parte acá en un archivo por variable, que
es lo que el resto del código espera encontrar. Ver `grupos_de_pedido`.

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


def _desempacar(path):
    """Los .nc que trae el archivo del CDS: uno solo, o varios si vino zipeado.

    Medido el 2026-11-03 contra el CDS: un pedido de dos variables vuelve como
    zip con un .nc por variable (`snow_depth_..._daily-mean.nc`,
    `snow_density_..._daily-mean.nc`), cada uno con una sola variable adentro y
    con su nombre corto (`sd`, `rsn`). Igual se contempla el .nc suelto con
    todo junto, que es como viene un pedido de una sola variable: las dos
    formas se ven igual acá, una lista de archivos para mirar.
    """
    with open(path, "rb") as f:
        if f.read(2) != b"PK":
            return [path]
    salida = []
    with zipfile.ZipFile(path) as z:
        for i, miembro in enumerate(n for n in z.namelist() if n.endswith(".nc")):
            destino = path.with_suffix(f".{i}.nc")
            with z.open(miembro) as src, open(destino, "wb") as dst:
                shutil.copyfileobj(src, dst)
            salida.append(destino)
    path.unlink()
    return salida


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

    def _estadistica(self, v):
        return getattr(self.cfg.variables[v], "daily_statistic", None)

    def _nombre_corto(self, v):
        """Cómo se llama la variable DENTRO del NetCDF que manda el CDS.

        Hace falta sólo para partir un pedido junto. Sin este dato la variable
        se pide sola, porque no habría manera de separarla del resto.
        """
        return getattr(self.cfg.variables[v], "cds_short_name", None)

    def _target(self, v, y, m, tag):
        return self.workdir / f"{self.cfg.name}_{v}_{y}{m:02d}{tag}.nc"

    def grupos_de_pedido(self, dias):
        """Junta en un solo pedido las variables que comparten daily_statistic.

        Los días de todas las del grupo se piden de una: un día de más sale
        gratis (el costo es el turno en la cola, no el tamaño) y además deja el
        archivo servido para la escena que venía más atrasada.
        """
        grupos, sueltas = {}, []
        for v, ds_ in sorted(dias.items()):
            stat = self._estadistica(v)
            if stat and self._nombre_corto(v):
                vs, fechas = grupos.setdefault(stat, ([], set()))
                vs.append(v)
                fechas.update(ds_)
            else:
                sueltas.append(([v], sorted(ds_)))
        return [(vs, sorted(fechas)) for vs, fechas in grupos.values()] + sueltas

    def granules(self, variables, dates):
        # `juntos` sólo se llena cuando quien llama trajo más de una variable,
        # o sea desde grupos_de_pedido. El loop por escena pide de a una y cae
        # en el camino de siempre: un pedido para esa variable nada más.
        juntos = list(variables) if len(variables) > 1 else None
        out = []
        for v in variables:
            for (y, m), ds_ in sorted(month_groups(dates).items()):
                out.append(Granule(f"{self.cfg.name}:{v}:{y}-{m:02d}", v, sorted(ds_),
                                   juntos=juntos))
        return out

    def fetch(self, granule):
        v = granule.variable
        y, m = granule.dates[0].year, granule.dates[0].month
        area = self.fetch_area
        # el tag lleva la ventana -> con cds_fetch_area todas las escenas apuntan
        # al MISMO archivo, así que la primera lo baja y el resto lo cachea.
        tag = "" if area is None else "_" + "_".join(str(int(x)) for x in area)
        target = self._target(v, y, m, tag)
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
                juntos = granule.juntos or [v]
                if len(juntos) > 1:
                    self._retrieve_juntas(juntos, y, m, days, area, tag)
                else:
                    self._retrieve(v, y, m, days, area, target)
                # el pedido junto deja servidas a todas las del grupo: anotarlas
                # para que la escena siguiente no vuelva a pedir lo mismo.
                for w in juntos:
                    _pedidos[self._target(w, y, m, tag)] = set(days)
        return self._assets(granule, target)

    def _pedido(self, variables, y, m, days, area):
        """El cuerpo del pedido al CDS para una variable o para un grupo.

        `daily_statistic` sale de la primera: el grupo se arma justamente con
        las que la comparten.
        """
        nombres = list(dict.fromkeys(
            self.cfg.variables[w].cds_variable for w in variables))
        request = {
            "variable": nombres,
            "daily_statistic": self._estadistica(variables[0]),
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
        return request

    def _bajar(self, request, destino, etiqueta, y, m, days):
        """Hace el pedido a un temporal al lado de `destino` y lo devuelve.

        A un temporal y después rename: si el pedido se corta, el archivo bueno
        anterior sigue en su lugar.
        """
        import cdsapi

        log.info("CDS request %s %d-%02d, %d day(s) (may sit in the queue for a while)",
                 etiqueta, y, m, len(days))
        tmp = destino.with_suffix(".part.nc")
        cdsapi.Client(quiet=True).retrieve(self.cfg.cds_dataset, request, str(tmp))
        return tmp

    def _retrieve(self, v, y, m, days, area, target):
        tmp = self._bajar(self._pedido([v], y, m, days, area), target, v, y, m, days)
        _ensure_netcdf(tmp)
        tmp.replace(target)

    def _retrieve_juntas(self, variables, y, m, days, area, tag):
        """Un pedido para todo el grupo, partido después en un archivo por variable.

        Lo que queda en disco es exactamente lo mismo que si se hubieran pedido
        por separado, así que el resto del código no se entera: el loop por
        escena encuentra el archivo de su variable y no vuelve a pedir nada.
        """
        import xarray as xr

        destino = self.workdir / f"{self.cfg.name}_juntas_{y}{m:02d}{tag}.nc"
        tmp = self._bajar(self._pedido(variables, y, m, days, area), destino,
                          "+".join(variables), y, m, days)
        piezas = _desempacar(tmp)
        faltan = list(variables)
        try:
            for pieza in piezas:
                with nc_lock, xr.open_dataset(pieza) as ds:
                    for w in list(faltan):
                        corto = self._nombre_corto(w)
                        if corto not in ds.data_vars:
                            continue
                        salida = self._target(w, y, m, tag)
                        parcial = salida.with_suffix(".part.nc")
                        ds[[corto]].to_netcdf(parcial)
                        parcial.replace(salida)
                        faltan.remove(w)
        finally:
            for pieza in piezas:
                pieza.unlink(missing_ok=True)
        if faltan:
            # No se corta la corrida: quien llama (el adelanto en cli.py) lo
            # loguea y el loop por escena las vuelve a pedir de a una.
            raise FileNotFoundError(
                f"el CDS no devolvió {', '.join(faltan)} en el pedido junto de "
                f"{y}-{m:02d}; revisá cds_short_name en el YAML del dataset")

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
