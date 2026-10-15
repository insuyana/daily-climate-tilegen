"""CPC Global Daily Temperature via NOAA PSL.

NOAA PSL publishes CPC global tmax/tmin as one NetCDF per variable per year,
1979 to present, on a 0.5 deg grid. A granule here is one (variable, year).

Each year file is downloaded WHOLE from downloads.psl.noaa.gov, once per run,
and every scene crops it locally. Until v0.4.4 this read over OPeNDAP
(psl.noaa.gov/thredds), letting the server apply each scene's bbox: since
2026-10-12 THREDDS answers this server with 429 Too Many Requests even to a
single request, while the plain download host still serves it. The files are
compressed NetCDF4, ~63 MB per variable-year (the ~300 MB is in memory), so the
whole current year costs ~125 MB a day, and one download serves all scenes.

The year file stays in the workdir. A later run asks again with
If-Modified-Since: a past year comes back 304 and is not downloaded again; the
current year comes back new whenever NOAA appended days.

Grid quirks this source has to absorb (measured against the live files):

- longitude runs 0..360 (0.25 .. 359.75), not -180..180
- latitude DESCENDS (89.75 .. -89.75)
- CPC interpolates station data, so it has no ocean: a coastal scene comes
  back with a large NaN fraction, and that is the source being honest rather
  than a fetch going wrong.
"""
from __future__ import annotations

import email.utils
import logging
import os
import threading
from collections import defaultdict

import requests

from ..utils import nc_lock, retry
from .base import Asset, DataSource, Granule

log = logging.getLogger("tilegen.cpc")


class CpcPslSource(DataSource):
    parallel_fetch = True
    area = None  # (minx, miny, maxx, maxy), set by the pipeline

    _ds_cache = {}
    _ds_lock = threading.Lock()

    def _open(self, url):
        import xarray as xr

        with self._ds_lock:
            if url not in self._ds_cache:
                local = self._download(url, self.workdir / url.rsplit("/", 1)[-1])
                with nc_lock:
                    self._ds_cache[url] = xr.open_dataset(local)
            return self._ds_cache[url]

    @retry(times=5, delay=30.0, exceptions=(requests.RequestException,))
    def _download(self, url, dst):
        """Bring ``url`` to ``dst`` unless the copy there is already current.

        404 -> FileNotFoundError (a year NOAA has not started yet). Anything else
        that goes wrong over HTTP, a 429 included, is retried and then raised:
        never as "not published", which would leave the run ending ok with
        nothing written, the way it did from 2026-10-12 on.
        """
        headers = {}
        if dst.exists():
            headers["If-Modified-Since"] = email.utils.formatdate(
                dst.stat().st_mtime, usegmt=True)
        with requests.get(url, stream=True, timeout=300, headers=headers) as r:
            if r.status_code == 404:
                raise FileNotFoundError(f"{url} not available (404)")
            if r.status_code == 304:
                log.info("CPC %s already current", dst.name)
                return dst
            r.raise_for_status()
            # the pid keeps two processes (cron and a backfill share the workdir)
            # from writing into each other's half-file
            tmp = dst.with_name(f"{dst.name}.{os.getpid()}.part")
            with open(tmp, "wb") as f:
                for chunk in r.iter_content(1 << 20):
                    f.write(chunk)
            modified = r.headers.get("Last-Modified")
        tmp.replace(dst)
        if modified:  # the mtime is what the next If-Modified-Since sends
            ts = email.utils.parsedate_to_datetime(modified).timestamp()
            os.utime(dst, (ts, ts))
        log.info("CPC downloaded %s (%.1f MB)", dst.name, dst.stat().st_size / 1e6)
        return dst

    def _url(self, v: str, year: int) -> str:
        vcfg = self.cfg.variables[v]
        stem = getattr(vcfg, "source_file", None) or v
        return f"{self.cfg.base_url.rstrip('/')}/{stem}.{year}.nc"

    def _crop(self, da):
        """Same shape as the EDH crop: convert the bbox, never the data.

        Rewriting the coordinate would force a full-globe read before the
        subset; selecting first only decompresses the chunks the scene needs.
        """
        if self.area is None:
            return da
        minx, miny, maxx, maxy = self.area
        lon = next(c for c in ("longitude", "lon") if c in da.coords)
        lat = next(c for c in ("latitude", "lat") if c in da.coords)
        if float(da[lon].max()) > 180:  # CPC is on 0..360
            if minx < 0 <= maxx:
                raise ValueError("bbox crossing lon 0 not supported for CPC")
            minx, maxx = minx % 360, maxx % 360
        lat_desc = float(da[lat][0]) > float(da[lat][-1])
        return da.sel({lon: slice(minx, maxx),
                       lat: slice(maxy, miny) if lat_desc else slice(miny, maxy)})

    def granules(self, variables, dates):
        out = []
        for v in variables:
            by_year = defaultdict(list)
            for d in dates:
                by_year[d.year].append(d)
            for y, ds_ in sorted(by_year.items()):
                out.append(Granule(f"{self.cfg.name}:{v}:{y}", v, sorted(ds_)))
        return out

    def _read_year(self, granule):
        import pandas as pd

        v = granule.variable
        year = granule.dates[0].year
        # No OSError -> FileNotFoundError translation here: requests' errors are
        # OSErrors too, and that is how a 429 passed for "not published yet".
        ds = self._open(self._url(v, year))

        stem = getattr(self.cfg.variables[v], "source_file", None) or v
        name = stem if stem in ds else next(iter(ds.data_vars))
        da = ds[name]
        tdim = next(d for d in ("valid_time", "time") if d in da.dims)
        tvals = pd.DatetimeIndex(ds[tdim].values)
        wanted = pd.DatetimeIndex([pd.Timestamp(d) for d in granule.dates])
        present = wanted.intersection(tvals)
        if present.empty:
            raise FileNotFoundError(
                f"dates not yet in CPC {year} file (ends {tvals[-1].date()})")
        da = self._crop(da.sel({tdim: present}))
        if da.sizes.get(next(c for c in ("longitude", "lon") if c in da.coords), 0) == 0:
            raise ValueError(f"bbox {self.area} falls outside the CPC grid")
        with nc_lock:
            da = da.load()
        log.info("CPC read %s (%d day(s), %s px)", granule.key,
                 da.sizes[tdim], "x".join(str(s) for s in da.shape[1:]))
        return da.rename({tdim: "time"}).astype("float32")

    def fetch(self, granule):
        import xarray as xr

        v = granule.variable
        y = granule.dates[0].year
        tag = "" if self.area is None else "_" + "_".join(str(int(x)) for x in self.area)
        target = self.workdir / f"cpc_{v}_{y}{tag}.nc"
        wanted = set(granule.dates)
        have = set()
        if target.exists():
            with nc_lock, xr.open_dataset(target) as ds:
                have = {t.astype("datetime64[D]").astype(object) for t in ds["time"].values}
        # same care as edh.py: a crop left by an earlier run (--keep-local) may
        # lack the new days -> crop again
        if not wanted <= have:
            da = self._read_year(granule)
            tmp = target.with_suffix(".part.nc")
            with nc_lock:
                da.to_netcdf(tmp)
            tmp.replace(target)

        assets = []
        with nc_lock, xr.open_dataset(target) as ds:
            for i, t in enumerate(ds["time"].values):
                d = t.astype("datetime64[D]").astype(object)
                if d in wanted:
                    assets.append(Asset(variable=v, date=d, path=target, time_index=i))
        return assets
