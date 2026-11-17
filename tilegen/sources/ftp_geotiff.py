"""Same as http_geotiff (one GeoTIFF per variable per day) but fetched over FTP.

Only the download step differs; URL templating, gzip and Asset building are
inherited. Use ``ftp://...`` URLs in the dataset YAML.
"""
from __future__ import annotations

import logging
import shutil
import urllib.error
import urllib.request

from ..utils import retry
from .http_geotiff import HttpGeotiffSource

log = logging.getLogger("tilegen.ftp")


class FtpGeotiffSource(HttpGeotiffSource):
    # not plain OSError: FileNotFoundError is one and must not be retried
    @retry(times=4, delay=10.0, exceptions=(urllib.error.URLError, TimeoutError, ConnectionError))
    def _download(self, url, dst):
        tmp = dst.with_name(dst.name + ".part")
        try:
            with urllib.request.urlopen(url, timeout=180) as r, open(tmp, "wb") as f:
                shutil.copyfileobj(r, f, 1 << 20)
        except urllib.error.URLError as e:
            # FTP 550 (no such file) arrives as URLError("ftp error: ... 550 ...")
            if "550" in str(e.reason):
                raise FileNotFoundError(url) from e
            raise
        tmp.rename(dst)
        log.info("downloaded %s (%.1f MB)", dst.name, dst.stat().st_size / 1e6)
