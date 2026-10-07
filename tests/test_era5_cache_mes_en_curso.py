"""El archivo mensual cacheado no puede congelar el mes en curso.

El bug (sep-2026): el primer archivo bajado de un mes quedaba en el workdir y
``fetch`` lo reusaba para siempre (``if not target.exists()``). El 7-sep se bajó
septiembre con un solo día publicado; desde ahí el cron diario leyó ese mismo
archivo todos los días, no encontró días nuevos, escribió 0 y terminó "ok".
ERA5 y ERA5-Land quedaron un mes sin actualizarse.

Lo que se fija:
  1. si al archivo le faltan días pedidos, se vuelve a bajar (con la unión);
  2. si ya tiene todo, NO se pide nada (un backfill reanudado no re-baja meses);
  3. dentro de una corrida, si el CDS no trajo un día, las escenas siguientes
     no lo vuelven a pedir (21 pedidos de ~20 min por nada).
"""
import datetime as dt
import sys
import types

import numpy as np
import pandas as pd
import pytest
import xarray as xr

import tilegen.sources.era5 as era5
from tilegen.sources.base import Granule
from tilegen.sources.era5 import Era5CdsSource


class _Var:
    cds_variable = "2m_temperature"
    daily_statistic = "daily_mean"
    units = "K"


class _Cfg:
    name = "era5"
    cds_dataset = "derived-era5-single-levels-daily-statistics"
    cds_product_type = "reanalysis"
    cds_fetch_area = [-170, -57, -32, 35]
    variables = {"t2m_mean": _Var()}


def _nc(path, days):
    t = pd.to_datetime([dt.date(2026, 9, d) for d in days])
    xr.Dataset({"t2m": (("valid_time", "latitude", "longitude"),
                        np.zeros((len(t), 2, 2), "float32"))},
               coords={"valid_time": t, "latitude": [0.0, 1.0],
                       "longitude": [0.0, 1.0]}).to_netcdf(path)


@pytest.fixture
def cds(monkeypatch):
    """CDS falso: devuelve los días pedidos, salvo los que 'no publicó'."""
    estado = {"pedidos": [], "publicado_hasta": 30}

    class _Client:
        def __init__(self, quiet=True):
            pass

        def retrieve(self, dataset, request, target):
            dias = [int(d) for d in request["day"]]
            estado["pedidos"].append(dias)
            _nc(target, [d for d in dias if d <= estado["publicado_hasta"]])

    mod = types.ModuleType("cdsapi")
    mod.Client = _Client
    monkeypatch.setitem(sys.modules, "cdsapi", mod)
    monkeypatch.setattr(era5, "_pedidos", {})  # cada test es una corrida nueva
    return estado


def _g(*days):
    return Granule("era5:t2m_mean:2026-09", "t2m_mean",
                   [dt.date(2026, 9, d) for d in days])


def _fechas(assets):
    return sorted(a.date.day for a in assets)


def test_archivo_viejo_incompleto_se_vuelve_a_bajar(tmp_path, cds, monkeypatch):
    src = Era5CdsSource(_Cfg(), tmp_path)
    _nc(src.workdir / "era5_t2m_mean_202609_-170_-57_-32_35.nc", [1])  # el del 7-sep
    assets = src.fetch(_g(1, 2, 3))
    assert cds["pedidos"] == [[1, 2, 3]]
    assert _fechas(assets) == [1, 2, 3]


def test_archivo_completo_no_se_pide(tmp_path, cds):
    src = Era5CdsSource(_Cfg(), tmp_path)
    _nc(src.workdir / "era5_t2m_mean_202609_-170_-57_-32_35.nc", [1, 2, 3])
    assert _fechas(src.fetch(_g(2, 3))) == [2, 3]
    assert cds["pedidos"] == []


def test_dia_no_publicado_se_pide_una_vez_por_corrida(tmp_path, cds):
    cds["publicado_hasta"] = 2
    peru = Era5CdsSource(_Cfg(), tmp_path)
    bolivia = Era5CdsSource(_Cfg(), tmp_path)
    assert _fechas(peru.fetch(_g(1, 2, 3))) == [1, 2]
    assert _fechas(bolivia.fetch(_g(1, 2, 3))) == [1, 2]
    assert len(cds["pedidos"]) == 1


def test_escena_que_pide_otros_dias_amplia_el_archivo(tmp_path, cds):
    """Escenas con pendientes distintos: la segunda pide la unión, no pisa."""
    a = Era5CdsSource(_Cfg(), tmp_path)
    b = Era5CdsSource(_Cfg(), tmp_path)
    a.fetch(_g(1, 2))
    assert _fechas(b.fetch(_g(3))) == [3]
    assert cds["pedidos"] == [[1, 2], [1, 2, 3]]
    assert _fechas(a.fetch(_g(1, 2))) == [1, 2]
