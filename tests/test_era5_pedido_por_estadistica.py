"""Pedirle al CDS varias variables en la misma llamada.

La cola del CDS cobra por PEDIDO y no por tamaño: esperar turno tarda lo mismo
con una variable que con cuatro. Con 8 variables y una ventana que agarra dos
meses eran 16 esperas, y la corrida se cortaba por tiempo antes de terminar.

`daily_statistic` es un campo suelto del pedido, no va por variable, así que lo
único que se puede juntar es lo que comparte estadística.

Los dos riesgos son:
  1. que el archivo que deja el pedido junto NO se llame igual que el que busca
     el loop por escena -> no pega el caché, se vuelve a pedir todo y el cambio
     no sirve para nada;
  2. que al partir el NetCDF se escriba una variable con los datos de otra, que
     es el peor de todos los errores posibles porque no se nota.
"""
import datetime as dt

import numpy as np
import pytest
import xarray as xr

from tilegen.sources.era5 import Era5CdsSource

VENTANA = [-170, -57, -32, 35]


class _Var:
    def __init__(self, cds_variable, daily_statistic, cds_short_name=None):
        self.cds_variable = cds_variable
        self.daily_statistic = daily_statistic
        if cds_short_name is not None:
            self.cds_short_name = cds_short_name


class _Cfg:
    name = "era5"
    cds_dataset = "derived-era5-single-levels-daily-statistics"
    cds_product_type = "reanalysis"
    cds_fetch_area = VENTANA
    variables = {
        "t2m_mean": _Var("2m_temperature", "daily_mean", "t2m"),
        "sd_mean": _Var("snow_depth", "daily_mean", "sd"),
        "rsn_mean": _Var("snow_density", "daily_mean", "rsn"),
        "t2m_max": _Var("2m_temperature", "daily_maximum", "t2m"),
        "fg10_max": _Var("10m_wind_gust_since_previous_post_processing",
                         "daily_maximum", "fg10"),
        # sin cds_short_name a propósito: es el caso de era5-land, que todavía
        # no tiene los nombres verificados.
        "snowc_mean": _Var("snow_cover", "daily_mean"),
    }


def _src(tmp_path):
    return Era5CdsSource(_Cfg(), tmp_path)


def _dias(*n):
    return [dt.date(2026, 10, d) for d in n]


# --- qué se junta con qué -------------------------------------------------

def test_las_que_comparten_estadistica_van_en_un_pedido(tmp_path):
    grupos = _src(tmp_path).grupos_de_pedido({
        "t2m_mean": set(_dias(1)), "sd_mean": set(_dias(1)), "rsn_mean": set(_dias(1)),
        "t2m_max": set(_dias(1)), "fg10_max": set(_dias(1)),
    })
    assert sorted(sorted(vs) for vs, _ in grupos) == [
        ["fg10_max", "t2m_max"],              # daily_maximum
        ["rsn_mean", "sd_mean", "t2m_mean"],  # daily_mean
    ]


def test_los_dias_del_grupo_se_juntan(tmp_path):
    """Una variable más atrasada que la otra arrastra al grupo entero.

    Un día de más sale gratis (el costo es el turno), y deja el archivo servido
    para la escena que venía más atrás.
    """
    grupos = _src(tmp_path).grupos_de_pedido({
        "sd_mean": set(_dias(1, 2)), "rsn_mean": set(_dias(1, 2, 3, 4)),
    })
    assert len(grupos) == 1
    _, fechas = grupos[0]
    assert fechas == _dias(1, 2, 3, 4)


def test_sin_nombre_corto_la_variable_va_sola(tmp_path):
    """Sin ese dato no se podría separar del NetCDF que vuelve con todo junto."""
    grupos = _src(tmp_path).grupos_de_pedido({
        "sd_mean": set(_dias(1)), "snowc_mean": set(_dias(1)),
    })
    assert ([ "snowc_mean"], _dias(1)) in [(list(vs), f) for vs, f in grupos]
    assert ["sd_mean"] in [list(vs) for vs, _ in grupos]
    assert len(grupos) == 2


def test_el_pedido_lleva_las_variables_del_grupo(tmp_path):
    pedido = _src(tmp_path)._pedido(
        ["t2m_mean", "sd_mean", "rsn_mean"], 2026, 10, _dias(1, 2), VENTANA)
    assert pedido["variable"] == ["2m_temperature", "snow_depth", "snow_density"]
    assert pedido["daily_statistic"] == "daily_mean"
    assert pedido["day"] == ["01", "02"]
    assert pedido["area"] == [35, -170, -57, -32]   # N, W, S, E


def test_el_loop_por_escena_sigue_pidiendo_de_a_una(tmp_path):
    """Cuando llega una sola variable no hay grupo: el camino de siempre."""
    gs = _src(tmp_path).granules(["sd_mean"], _dias(1, 2))
    assert len(gs) == 1
    assert gs[0].juntos is None


# --- partir el NetCDF que vuelve ------------------------------------------

def _netcdf_falso(path, cortos):
    """Un archivo como el que manda el CDS: varias variables, mismas fechas."""
    t = np.array(["2026-10-01", "2026-10-02"], dtype="datetime64[ns]")
    lat, lon = np.array([0.0, 0.25]), np.array([-60.0, -59.75])
    ds = xr.Dataset(
        {c: (("valid_time", "latitude", "longitude"),
             np.full((2, 2, 2), float(i), dtype="float32"))
         for i, c in enumerate(cortos)},
        coords={"valid_time": t, "latitude": lat, "longitude": lon},
    )
    ds.to_netcdf(path)


def _parchear_bajada(src, cortos, monkeypatch):
    def falso(request, destino, etiqueta, y, m, days):
        tmp = destino.with_suffix(".part.nc")
        _netcdf_falso(tmp, cortos)
        return tmp
    monkeypatch.setattr(src, "_bajar", falso)


def test_el_pedido_junto_deja_un_archivo_por_variable(tmp_path, monkeypatch):
    """Y con el nombre EXACTO que busca el loop por escena, o no pega el caché."""
    src = _src(tmp_path)
    _parchear_bajada(src, ["t2m", "sd", "rsn"], monkeypatch)
    tag = "_-170_-57_-32_35"
    src._retrieve_juntas(["t2m_mean", "sd_mean", "rsn_mean"], 2026, 10,
                         _dias(1, 2), VENTANA, tag)

    for v in ("t2m_mean", "sd_mean", "rsn_mean"):
        destino = src._target(v, 2026, 10, tag)
        assert destino.exists(), f"falta el archivo de {v}"
        # el que arma fetch() para una granule suelta tiene que ser el mismo
        assert destino.name == f"era5_{v}_202610{tag}.nc"

    # y el temporal del pedido junto no queda dando vueltas
    assert not list(tmp_path.glob("*juntas*"))


def test_cada_archivo_lleva_SU_variable_y_no_la_del_vecino(tmp_path, monkeypatch):
    """El error silencioso que hay que evitar: sd con los datos de rsn."""
    src = _src(tmp_path)
    _parchear_bajada(src, ["t2m", "sd", "rsn"], monkeypatch)
    tag = "_-170_-57_-32_35"
    src._retrieve_juntas(["t2m_mean", "sd_mean", "rsn_mean"], 2026, 10,
                         _dias(1, 2), VENTANA, tag)

    # el falso le puso a cada variable un valor distinto según su orden
    for v, corto, valor in (("t2m_mean", "t2m", 0.0), ("sd_mean", "sd", 1.0),
                            ("rsn_mean", "rsn", 2.0)):
        with xr.open_dataset(src._target(v, 2026, 10, tag)) as ds:
            assert list(ds.data_vars) == [corto]
            assert float(ds[corto].max()) == valor


def test_si_falta_una_variable_se_queja_en_vez_de_escribir_cualquier_cosa(
        tmp_path, monkeypatch):
    """Un cds_short_name mal puesto tiene que doler acá y no dos meses después.

    Quien llama (el adelanto de cli.py) lo loguea y el loop por escena vuelve a
    pedir de a una, así que la corrida no se corta.
    """
    src = _src(tmp_path)
    _parchear_bajada(src, ["t2m", "sd"], monkeypatch)   # falta rsn
    with pytest.raises(FileNotFoundError, match="rsn_mean"):
        src._retrieve_juntas(["t2m_mean", "sd_mean", "rsn_mean"], 2026, 10,
                             _dias(1, 2), VENTANA, "_-170_-57_-32_35")


def test_el_yaml_real_tiene_el_nombre_corto_de_cada_variable():
    """Una variable nueva sin cds_short_name se pediría sola y nadie lo notaría.

    No rompería nada, pero perdería el ahorro sin hacer ruido: el síntoma es
    una corrida más lenta, que es justo lo que no se mira.
    """
    from pathlib import Path
    from tilegen.config import load_dataset

    dcfg = load_dataset(Path("tilegen/conf"), "era5")
    sin_nombre = [v for v, c in dcfg.variables.items()
                  if getattr(c, "daily_statistic", None)
                  and not getattr(c, "cds_short_name", None)]
    assert sin_nombre == [], f"les falta cds_short_name: {sin_nombre}"


def test_la_forma_real_del_cds_es_un_zip_con_un_nc_por_variable(tmp_path, monkeypatch):
    """Medido el 2026-11-03: un pedido de dos variables vuelve zipeado.

    Adentro hay un .nc por variable, con nombres armados a partir del nombre
    largo (`snow_depth_stream-oper_daily-mean.nc`) y una sola variable adentro,
    con el nombre corto. Por eso el reparto busca por el contenido y no por el
    nombre del archivo, que trae partes que no controlamos.
    """
    import zipfile

    src = _src(tmp_path)

    def falso(request, destino, etiqueta, y, m, days):
        tmp = destino.with_suffix(".part.nc")
        sueltos = []
        for nombre, corto in (("snow_depth_stream-oper_daily-mean.nc", "sd"),
                              ("snow_density_0_daily-mean.nc", "rsn")):
            pieza = tmp_path / nombre
            _netcdf_falso(pieza, [corto])
            sueltos.append(pieza)
        with zipfile.ZipFile(tmp, "w") as z:
            for pieza in sueltos:
                z.write(pieza, pieza.name)
                pieza.unlink()
        return tmp

    monkeypatch.setattr(src, "_bajar", falso)
    tag = "_-170_-57_-32_35"
    src._retrieve_juntas(["sd_mean", "rsn_mean"], 2026, 10, _dias(1, 2), VENTANA, tag)

    for v, corto in (("sd_mean", "sd"), ("rsn_mean", "rsn")):
        destino = src._target(v, 2026, 10, tag)
        assert destino.exists(), f"falta el archivo de {v}"
        with xr.open_dataset(destino) as ds:
            assert list(ds.data_vars) == [corto]
    assert not list(tmp_path.glob("*juntas*"))
