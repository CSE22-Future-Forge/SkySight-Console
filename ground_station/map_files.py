"""Installing the offline map (.pmtiles): import a ready file, or build one with Planetiler.

Building is the only thing in the ground station that touches the internet, and only
when the coordinator explicitly starts it (one-time setup, see tools/build_map.md).
It mirrors tools/build_map.sh in Python so the packaged exe needs no bash, just Java 21+.

Nothing here touches Qt: callers pass plain callbacks, so this runs on any thread.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable

PMTILES_MAGIC = b"PMTiles"
PMTILES_VERSION = 3
COPY_CHUNK = 1 << 20
DOWNLOAD_CHUNK = 256 * 1024
DOWNLOAD_ATTEMPTS = 40
DOWNLOAD_TIMEOUT_S = 60
RETRY_PAUSE_S = 3
JAVA_MIN = 21
JAVA_HEAP = "-Xmx2g"

# (file name, url) - keep in step with tools/build_map.sh
SOURCES = [
    ("planetiler.jar", "https://github.com/onthegomap/planetiler/releases/latest/download/planetiler.jar"),
    ("sri-lanka-latest.osm.pbf", "https://download.geofabrik.de/asia/sri-lanka-latest.osm.pbf"),
    ("lake_centerline.shp.zip", "https://github.com/acalcutt/osm-lakelines/releases/download/v12/lake_centerline.shp.zip"),
    ("natural_earth_vector.sqlite.zip", "https://naciscdn.org/naturalearth/packages/natural_earth_vector.sqlite.zip"),
    ("water-polygons-split-3857.zip", "https://osmdata.openstreetmap.de/download/water-polygons-split-3857.zip"),
]

Log = Callable[[str], None]
Progress = Callable[[int, int], None]  # (done, total); total 0 = unknown


class MapError(Exception):
    pass


class Cancelled(MapError):
    def __init__(self):
        super().__init__("Cancelled")


def _never() -> bool:
    return False


# ------------------------------------------------------------------ install ---
def install_target(paths, settings) -> Path:
    """Where an imported/built map goes: the map_file setting, inside the data folder unless absolute.

    Unlike Paths.map_file this never points at the machine-wide ProgramData copy, which
    the operator usually cannot write; a copy in the data folder takes precedence over it.
    """
    rel = Path(str(settings.get("map_file")))
    return rel if rel.is_absolute() else paths.root / rel


def check_pmtiles(path: Path) -> None:
    try:
        with Path(path).open("rb") as f:
            head = f.read(len(PMTILES_MAGIC) + 1)
    except OSError as exc:
        raise MapError(f"Cannot read {path}: {exc}") from exc
    if not head.startswith(PMTILES_MAGIC):
        raise MapError(f"{Path(path).name} is not a PMTiles map.")
    if head[-1] != PMTILES_VERSION:
        raise MapError(f"{Path(path).name} is PMTiles version {head[-1]}; version {PMTILES_VERSION} is needed.")


def install_map(src: Path, dest: Path, progress: Progress | None = None,
                cancelled: Callable[[], bool] = _never) -> Path:
    """Validate ``src`` and copy it over ``dest`` atomically (a half-copied map is never served)."""
    src, dest = Path(src), Path(dest)
    check_pmtiles(src)
    if src.resolve() == dest.resolve():
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    total = src.stat().st_size
    done = 0
    try:
        with src.open("rb") as fin, part.open("wb") as fout:
            while block := fin.read(COPY_CHUNK):
                if cancelled():
                    raise Cancelled()
                fout.write(block)
                done += len(block)
                if progress:
                    progress(done, total)
        _replace(part, dest)
    finally:
        part.unlink(missing_ok=True)
    return dest


def _replace(part: Path, dest: Path) -> None:
    try:
        os.replace(part, dest)
    except PermissionError as exc:
        raise MapError(f"Could not replace {dest}: it is in use (a phone may be downloading it). "
                       "Try again in a minute.") from exc


# --------------------------------------------------------------------- java ---
def java_version(java: str) -> int | None:
    """Major version of a java executable, or None if it does not run."""
    try:
        out = subprocess.run([java, "-version"], capture_output=True, text=True, timeout=20,
                             creationflags=_NO_WINDOW).stderr
    except (OSError, subprocess.SubprocessError):
        return None
    m = re.search(r'version "(\d+)(?:\.(\d+))?', out)
    if not m:
        return None
    major = int(m.group(1))
    return int(m.group(2) or 0) if major == 1 else major  # "1.8.0" is Java 8


def _java_candidates() -> list[str]:
    exe = "java.exe" if sys.platform == "win32" else "java"
    found = []
    if os.environ.get("JAVA_HOME"):
        found.append(str(Path(os.environ["JAVA_HOME"]) / "bin" / exe))
    if shutil.which("java"):
        found.append(shutil.which("java"))
    if sys.platform == "win32":
        for base in {os.environ.get("ProgramFiles", r"C:\Program Files"), os.environ.get("ProgramW6432", "")}:
            if not base:
                continue
            for vendor in ("Java", "Eclipse Adoptium", "Microsoft", "Zulu", "Amazon Corretto", "BellSoft"):
                found += [str(p) for p in Path(base, vendor).glob(f"*/bin/{exe}")]
    return list(dict.fromkeys(p for p in found if Path(p).is_file()))


def find_java() -> tuple[str, int]:
    """The newest Java on this machine that can run Planetiler: (path, major)."""
    versions = [(v, p) for p in _java_candidates() if (v := java_version(p)) is not None]
    if not versions:
        raise MapError(f"Java was not found. Planetiler needs Java {JAVA_MIN} or newer: install a JDK "
                       "(for example Eclipse Temurin 21) and try again.")
    best, path = max(versions)
    if best < JAVA_MIN:
        raise MapError(f"Planetiler needs Java {JAVA_MIN} or newer; the newest found is Java {best} ({path}). "
                       f"Install JDK {JAVA_MIN}+ or point JAVA_HOME at one.")
    return path, best


_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


# ----------------------------------------------------------------- download ---
def _remote_size(url: str) -> int | None:
    try:
        with urllib.request.urlopen(urllib.request.Request(url, method="HEAD"), timeout=DOWNLOAD_TIMEOUT_S) as r:
            size = r.headers.get("Content-Length")
            return int(size) if size else None
    except (OSError, ValueError):
        return None


def download(url: str, dest: Path, log: Log, progress: Progress | None = None,
             cancelled: Callable[[], bool] = _never, attempts: int = DOWNLOAD_ATTEMPTS,
             retry_pause_s: float = RETRY_PAUSE_S) -> Path:
    """Fetch ``url`` to ``dest``, resuming a partial file with HTTP Range across attempts."""
    want = _remote_size(url)
    for attempt in range(1, attempts + 1):
        have = dest.stat().st_size if dest.exists() else 0
        if want is not None and have == want:
            log(f"[ok] {dest.name} ({have:,} bytes)")
            return dest
        if want is not None and have > want:  # the file changed upstream; start over
            dest.unlink()
            have = 0
        if cancelled():
            raise Cancelled()
        log(f"[get] {dest.name} attempt {attempt} ({have:,}/{want or 0:,} bytes)")
        req = urllib.request.Request(url, headers={"Range": f"bytes={have}-"} if have else {})
        try:
            with urllib.request.urlopen(req, timeout=DOWNLOAD_TIMEOUT_S) as r:
                if have and r.status != 206:  # server ignored the range
                    have = 0
                with dest.open("ab" if have else "wb") as f:
                    done = have
                    while block := r.read(DOWNLOAD_CHUNK):
                        if cancelled():
                            raise Cancelled()
                        f.write(block)
                        done += len(block)
                        if progress:
                            progress(done, want or 0)
            if want is None:  # without a known length, one clean finish is the best signal we get
                log(f"[ok] {dest.name} ({dest.stat().st_size:,} bytes)")
                return dest
        except urllib.error.HTTPError as exc:
            if exc.code == 416 and want is None:
                return dest
            log(f"[retry] {dest.name}: HTTP {exc.code}")
        except (OSError, TimeoutError) as exc:
            log(f"[retry] {dest.name}: {exc}")
        if attempt < attempts:
            time.sleep(retry_pause_s)
    raise MapError(f"{dest.name} did not finish downloading after {attempts} attempts. "
                   "Check the internet connection and start again; it resumes.")


# -------------------------------------------------------------------- build ---
def build_map(paths, dest: Path, log: Log, progress: Progress | None = None,
              cancelled: Callable[[], bool] = _never, stage: Log | None = None) -> Path:
    """Download the sources, run Planetiler and install the result at ``dest``."""
    stage = stage or (lambda _s: None)
    java, major = find_java()
    log(f"Using Java {major}: {java}")
    work = paths.root / "map_build"
    src = work / "sources"
    for d in (src, work / "tmp"):
        d.mkdir(parents=True, exist_ok=True)

    for i, (name, url) in enumerate(SOURCES, 1):
        stage(f"Downloading {i}/{len(SOURCES)}: {name}")
        download(url, src / name, log, progress, cancelled)

    stage("Building the map with Planetiler (a few minutes)")
    if progress:
        progress(0, 0)
    out = work / "srilanka.pmtiles"
    # --output must be relative: Planetiler parses "C:/..." as a URI with scheme "C".
    cmd = [java, JAVA_HEAP, "--enable-native-access=ALL-UNNAMED", "-jar", str(src / "planetiler.jar"),
           f"--osm-path={src / 'sri-lanka-latest.osm.pbf'}",
           f"--lake-centerlines-path={src / 'lake_centerline.shp.zip'}",
           f"--natural-earth-path={src / 'natural_earth_vector.sqlite.zip'}",
           f"--water-polygons-path={src / 'water-polygons-split-3857.zip'}",
           f"--tmpdir={work / 'tmp'}", f"--output={out.name}", "--force"]
    _run(cmd, work, log, cancelled)
    if not out.is_file():
        raise MapError("Planetiler finished without writing srilanka.pmtiles; see the log above.")

    stage("Installing the map")
    check_pmtiles(out)
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        shutil.move(str(out), str(dest.with_name(dest.name + ".part")))
        _replace(dest.with_name(dest.name + ".part"), dest)
    finally:
        dest.with_name(dest.name + ".part").unlink(missing_ok=True)
    log(f"[done] {dest} ({dest.stat().st_size:,} bytes)")
    return dest


def _run(cmd: list[str], cwd: Path, log: Log, cancelled: Callable[[], bool]) -> None:
    proc = subprocess.Popen(cmd, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                            encoding="utf-8", errors="replace", creationflags=_NO_WINDOW)

    def watch():  # readline blocks, so cancellation is watched separately
        while proc.poll() is None:
            if cancelled():
                proc.kill()
                return
            time.sleep(0.3)

    threading.Thread(target=watch, daemon=True).start()
    assert proc.stdout is not None
    for line in proc.stdout:
        log(line.rstrip())
    code = proc.wait()
    if cancelled():
        raise Cancelled()
    if code != 0:
        raise MapError(f"Planetiler failed (exit code {code}); see the log above.")
