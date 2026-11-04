"""Base classes for data sources.

A *granule* is the unit of download (one HTTP file, one CDS request).
Fetching a granule yields *assets*: single-variable, single-date rasters
ready for tiling. To add a new source, subclass DataSource, implement
``granules()`` and ``fetch()``, and register it in sources/__init__.py.
"""
from __future__ import annotations

import datetime as dt
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from ..config import DatasetCfg


@dataclass
class Granule:
    key: str                 # human-readable id, e.g. "chirps:precip:2024-01-15"
    variable: str
    dates: list
    #: Las variables que viajan en el MISMO pedido al origen, esta incluida.
    #: None = va sola, que es el caso de casi todas las fuentes. Sólo lo llena
    #: quien junta varias para hacer menos pedidos (ver Era5CdsSource).
    juntos: Optional[list] = None


@dataclass
class Asset:
    variable: str
    date: dt.date
    path: Path                        # local GeoTIFF or NetCDF
    band: int = 1                     # GeoTIFF band
    time_index: Optional[int] = None  # NetCDF time step


class DataSource(ABC):
    #: whether granules can be fetched concurrently (False for queued APIs like CDS)
    parallel_fetch = True

    #: si el archivo descargado sirve para MÁS de una escena, el pipeline no lo
    #: borra al terminar el gránulo: la escena siguiente lo lee del disco en vez
    #: de volver a pedirlo. Sólo lo pone en True una fuente que descarga una
    #: ventana común a todas las escenas (ver Era5CdsSource.cds_fetch_area).
    #: Contrapartida: los archivos quedan en workdir/downloads y hay que podarlos.
    shared_downloads = False

    def __init__(self, cfg: DatasetCfg, workdir: Path):
        self.cfg = cfg
        self.workdir = workdir
        workdir.mkdir(parents=True, exist_ok=True)

    @abstractmethod
    def granules(self, variables: list[str], dates: list) -> list[Granule]:
        """Split the requested (variables x dates) into download units."""

    def grupos_de_pedido(self, dias: dict) -> list[tuple[list[str], list]]:
        """Cómo repartir {variable: días pendientes} en llamadas al origen.

        Por defecto cada variable va sola, que es lo que hacen las fuentes
        donde un pedido cuesta lo que pesa. Una fuente con cola, donde el
        costo es la CANTIDAD de pedidos, puede juntar varias en la misma
        llamada (ver Era5CdsSource.grupos_de_pedido).
        """
        return [([v], sorted(ds_)) for v, ds_ in sorted(dias.items())]

    @abstractmethod
    def fetch(self, granule: Granule) -> list[Asset]:
        """Download one granule; return the assets it contains.

        Raise FileNotFoundError if the granule does not exist at the source
        (e.g. date not yet published) — the pipeline logs and skips it.
        """
