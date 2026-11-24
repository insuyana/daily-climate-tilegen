"""CHIRPS-style sources: one download per file for the whole run, cropped by every scene."""
import datetime as dt
from types import SimpleNamespace

import pytest

from tilegen import cli
from tilegen.sources import http_geotiff
from tilegen.sources.base import Granule
from tilegen.sources.http_geotiff import HttpGeotiffSource

CFG = SimpleNamespace(name="chirps", variables={"precip": SimpleNamespace(url="http://x/{year}-{month}-{day}.tif")})
D1, D2 = dt.date(2026, 10, 8), dt.date(2026, 10, 9)


class FakeSource(HttpGeotiffSource):
    calls: list = []
    absent: set = set()

    def _download(self, url, dst):
        FakeSource.calls.append(url)
        if any(a in url for a in FakeSource.absent):
            raise FileNotFoundError(url)
        dst.write_bytes(b"tif")


@pytest.fixture(autouse=True)
def clean():
    FakeSource.calls, FakeSource.absent = [], set()
    http_geotiff._shared_files.clear()
    http_geotiff._failed.clear()
    yield
    http_geotiff._shared_files.clear()
    http_geotiff._failed.clear()


def scene_source(tmp_path, shared=True):
    s = FakeSource(CFG, tmp_path)  # every scene has its own instance, all on the same dir
    if shared:
        s.share_between_scenes()
    return s


def test_second_scene_reads_the_file_from_disk(tmp_path):
    a, b = scene_source(tmp_path), scene_source(tmp_path)
    g = Granule("chirps:precip:2026-10-09", "precip", [D2])
    assert a.fetch(g)[0].path == b.fetch(g)[0].path
    assert len(FakeSource.calls) == 1


def test_release_deletes_what_was_kept(tmp_path):
    s = scene_source(tmp_path)
    path = s.fetch(Granule("k", "precip", [D2]))[0].path
    assert path.exists()
    s.release()
    assert not path.exists() and not http_geotiff._shared_files


def test_a_missing_file_is_asked_once_and_every_scene_still_sees_it_missing(tmp_path):
    FakeSource.absent = {"2026-10-9"}
    g = Granule("k", "precip", [D2])
    for _ in range(3):
        with pytest.raises(FileNotFoundError):
            scene_source(tmp_path).fetch(g)
    assert len(FakeSource.calls) == 1


def test_a_failed_download_is_not_retried_by_the_next_scene(tmp_path, monkeypatch):
    def boom(self, url, dst):
        FakeSource.calls.append(url)
        raise RuntimeError("429")
    monkeypatch.setattr(FakeSource, "_download", boom)
    g = Granule("k", "precip", [D2])
    for _ in range(3):
        with pytest.raises(RuntimeError):
            scene_source(tmp_path).fetch(g)
    assert len(FakeSource.calls) == 1


def test_single_scene_keeps_nothing_and_remembers_nothing(tmp_path):
    s = scene_source(tmp_path, shared=False)
    assert s.shared_downloads is False
    s.fetch(Granule("k", "precip", [D2]))
    assert not http_geotiff._shared_files


def test_prefetch_asks_once_per_file_for_all_scenes(tmp_path):
    pend = {"precip": [D1, D2]}
    trabajos = []
    for _ in range(5):
        src = scene_source(tmp_path)
        trabajos.append((SimpleNamespace(source=src), SimpleNamespace(missing=pend)))
    cli._pedir_una_vez(trabajos)
    assert len(FakeSource.calls) == 2  # 2 days x 1 variable, not x 5 scenes
    for p, _ in trabajos:  # the scenes' own fetches add no downloads
        p.source.fetch(Granule("k", "precip", [D1]))
    assert len(FakeSource.calls) == 2


def test_retry_after_header():
    r = SimpleNamespace(headers={"Retry-After": "12"})
    assert http_geotiff._retry_after(r) == 12
    assert http_geotiff._retry_after(SimpleNamespace(headers={})) == 60
    assert http_geotiff._retry_after(SimpleNamespace(headers={"Retry-After": "9999"})) == 300
