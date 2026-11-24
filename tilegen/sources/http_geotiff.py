"""Generic source for datasets published as one GeoTIFF per variable per day
over HTTP(S) — covers CHIRPS, CHIRTS and most UCSB-CHC style archives.

Per-variable config: ``url`` (template with {year}, {month}, {day}) and
optional ``gzip: true`` when files are .gz-compressed.
"""
from __future__ import annotations

import logging
import time

import requests

from ..utils import gunzip, retry
from .base import Asset, DataSource, Granule

log = logging.getLogger("tilegen.http")

# Con varias escenas, cada archivo global se baja UNA vez y las escenas lo recortan
# del disco (como ERA5 con cds_fetch_area). Estado de la corrida, compartido entre
# las instancias de cada escena: qué archivos quedaron y qué descargas ya fallaron
# (una escena no vuelve a golpear al servidor por un archivo que ya dio 429 o 404).
_shared_files: set = set()
_failed: dict = {}


def _retry_after(response, default=60, cap=300):
    """Segundos que pide el servidor con Retry-After (si es un número), con tope."""
    try:
        return min(float(response.headers["Retry-After"]), cap)
    except (KeyError, ValueError):
        return default


class HttpGeotiffSource(DataSource):
    parallel_fetch = True

    def share_between_scenes(self):
        self.shared_downloads = True  # el pipeline deja de borrar el archivo al terminar el gránulo

    def release(self):
        for path in _shared_files:
            path.unlink(missing_ok=True)
        _shared_files.clear()
        _failed.clear()

    def granules(self, variables, dates):
        return [Granule(f"{self.cfg.name}:{v}:{d:%Y-%m-%d}", v, [d])
                for v in variables for d in dates]

    @retry(times=4, delay=10.0, exceptions=(requests.RequestException,))
    def _download(self, url, dst):
        tmp = dst.with_name(dst.name + ".part")
        with requests.get(url, stream=True, timeout=180) as r:
            if r.status_code == 404:
                raise FileNotFoundError(url)
            if r.status_code == 429:  # rate limit: esperar lo que pide el servidor, no 10 s
                wait = _retry_after(r)
                log.warning("429 de %s: espero %.0fs", url, wait)
                time.sleep(wait)
            r.raise_for_status()
            with open(tmp, "wb") as f:
                for chunk in r.iter_content(1 << 20):
                    f.write(chunk)
        tmp.rename(dst)
        log.info("downloaded %s (%.1f MB)", dst.name, dst.stat().st_size / 1e6)

    def fetch(self, granule):
        v, d = granule.variable, granule.dates[0]
        vcfg = self.cfg.variables[v]
        url = vcfg.url.format(year=d.year, month=d.month, day=d.day)
        dst = self.workdir / f"{self.cfg.name}_{v}_{d:%Y%m%d}.tif"
        if self.shared_downloads and dst in _failed:
            raise _failed[dst]
        if not dst.exists():
            try:
                if getattr(vcfg, "gzip", False):
                    raw = dst.with_suffix(".tif.gz")
                    self._download(url, raw)
                    gunzip(raw, dst)
                    raw.unlink()
                else:
                    self._download(url, dst)
            except Exception as exc:
                if self.shared_downloads:
                    _failed[dst] = exc
                raise
        if self.shared_downloads:
            _shared_files.add(dst)
        return [Asset(variable=v, date=d, path=dst)]
