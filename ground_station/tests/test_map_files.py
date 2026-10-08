"""Offline map install: import validation, atomic copy, resumable download, Java detection."""

import http.server
import threading

import pytest

from ground_station import map_files

PMTILES = b"PMTiles\x03" + bytes(range(256)) * 64


@pytest.fixture
def pmtiles_file(tmp_path):
    path = tmp_path / "usb" / "srilanka.pmtiles"
    path.parent.mkdir()
    path.write_bytes(PMTILES)
    return path


def test_install_target_is_in_the_data_folder(manifest):
    assert map_files.install_target(manifest.paths, manifest.settings) == manifest.paths.maps / "srilanka.pmtiles"
    manifest.settings.set("map_file", str(manifest.paths.root / "elsewhere.pmtiles"))
    assert map_files.install_target(manifest.paths, manifest.settings) == manifest.paths.root / "elsewhere.pmtiles"


def test_install_copies_and_reports_progress(manifest, pmtiles_file):
    dest = map_files.install_target(manifest.paths, manifest.settings)
    seen = []
    map_files.install_map(pmtiles_file, dest, lambda d, t: seen.append((d, t)))
    assert dest.read_bytes() == PMTILES
    assert seen[-1] == (len(PMTILES), len(PMTILES))
    assert not dest.with_name(dest.name + ".part").exists()
    assert manifest.paths.map_file(manifest.settings) == dest


@pytest.mark.parametrize("content,msg", [(b"<html>not a map", "not a PMTiles"), (b"PMTiles\x02xx", "version 2")])
def test_import_rejects_non_pmtiles(tmp_path, content, msg):
    bad = tmp_path / "bad.pmtiles"
    bad.write_bytes(content)
    with pytest.raises(map_files.MapError, match=msg):
        map_files.install_map(bad, tmp_path / "out.pmtiles")
    assert not (tmp_path / "out.pmtiles").exists()


def test_cancelled_import_keeps_the_old_map(tmp_path, pmtiles_file):
    dest = tmp_path / "maps" / "srilanka.pmtiles"
    dest.parent.mkdir()
    dest.write_bytes(b"PMTiles\x03old")
    with pytest.raises(map_files.Cancelled):
        map_files.install_map(pmtiles_file, dest, cancelled=lambda: True)
    assert dest.read_bytes() == b"PMTiles\x03old"
    assert not dest.with_name(dest.name + ".part").exists()


class _RangeHandler(http.server.BaseHTTPRequestHandler):
    body = PMTILES
    cut_first_at: int | None = None  # drop the connection after this many bytes, once

    def log_message(self, *a):
        pass

    def do_HEAD(self):
        self.send_response(200)
        self.send_header("Content-Length", str(len(self.body)))
        self.end_headers()

    def do_GET(self):
        start = 0
        rng = self.headers.get("Range")
        if rng:
            start = int(rng.split("=")[1].split("-")[0])
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {start}-{len(self.body) - 1}/{len(self.body)}")
        else:
            self.send_response(200)
        data = self.body[start:]
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        cut = type(self).cut_first_at
        if cut is not None:
            type(self).cut_first_at = None
            self.wfile.write(data[:cut])
            self.wfile.flush()
            self.connection.shutdown(2)
            return
        self.wfile.write(data)


@pytest.fixture
def http_url():
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _RangeHandler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}/file"
    srv.shutdown()


def test_download_resumes_after_a_dropped_connection(tmp_path, http_url):
    _RangeHandler.cut_first_at = 5000
    dest = tmp_path / "srilanka.osm.pbf"
    lines = []
    map_files.download(http_url, dest, lines.append, retry_pause_s=0)
    assert dest.read_bytes() == PMTILES
    assert any("attempt 2 (5,000/" in line for line in lines)


def test_download_skips_a_complete_file(tmp_path, http_url):
    dest = tmp_path / "f"
    dest.write_bytes(PMTILES)
    lines = []
    map_files.download(http_url, dest, lines.append)
    assert lines == [f"[ok] f ({len(PMTILES):,} bytes)"]


def test_download_gives_up_offline(tmp_path):
    with pytest.raises(map_files.MapError, match="did not finish"):
        map_files.download("http://127.0.0.1:9/none", tmp_path / "f", lambda _l: None, attempts=2, retry_pause_s=0)


@pytest.mark.parametrize("stderr,major", [('java version "1.8.0_251"', 8), ('openjdk version "21.0.2" 2024-01-16', 21),
                                          ('java version "22" 2024-03-19', 22), ("garbage", None)])
def test_java_version_parsing(monkeypatch, stderr, major):
    class Done:
        pass

    def fake_run(*a, **kw):
        d = Done()
        d.stderr = stderr
        return d

    monkeypatch.setattr(map_files.subprocess, "run", fake_run)
    assert map_files.java_version("java") == major


def test_find_java_prefers_newest_and_rejects_old(monkeypatch):
    versions = {"old": 8, "new": 22, "mid": 21}
    monkeypatch.setattr(map_files, "_java_candidates", lambda: list(versions))
    monkeypatch.setattr(map_files, "java_version", versions.get)
    assert map_files.find_java() == ("new", 22)
    versions.pop("new"), versions.pop("mid")
    with pytest.raises(map_files.MapError, match="Java 21 or newer; the newest found is Java 8"):
        map_files.find_java()
