"""Offline map files, /maps/* (spec 7.2).

The .pmtiles archive must honour HTTP Range: MapLibre GL JS reads it in small
byte ranges, and phones resume an interrupted download with it.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

from fastapi import APIRouter, Request, Response
from fastapi.responses import FileResponse, StreamingResponse

from ground_station.core.services import ServiceError

router = APIRouter(prefix="/maps")

WEB_DIR = Path(__file__).resolve().parent.parent / "ui" / "web"
CHUNK = 256 * 1024
_RANGE = re.compile(r"^bytes=(\d*)-(\d*)$")
_sha_cache: dict[tuple, str] = {}


def _map_path(request: Request) -> Path:
    m = request.app.state.manifest
    return m.paths.map_file(m.settings)


def file_sha256(path: Path) -> str:
    st = path.stat()
    key = (str(path), st.st_size, st.st_mtime_ns)
    if key not in _sha_cache:
        h = hashlib.sha256()
        with path.open("rb") as f:
            for block in iter(lambda: f.read(1 << 20), b""):
                h.update(block)
        _sha_cache.clear()
        _sha_cache[key] = h.hexdigest()
    return _sha_cache[key]


def _style_version() -> str:
    style = WEB_DIR / "style.json"
    if not style.is_file():
        return "none"
    return hashlib.sha256(style.read_bytes()).hexdigest()[:12]


def _safe_child(root: Path, *parts: str) -> Path:
    path = root.joinpath(*parts).resolve()
    if root.resolve() not in path.parents:
        raise ServiceError("not_found", "No such file", 404)
    return path


@router.get("/manifest")
def manifest(request: Request):
    path = _map_path(request)
    if not path.is_file():
        raise ServiceError("no_map", "No offline map installed on the ground station", 404)
    return {"file": path.name, "size": path.stat().st_size, "sha256": file_sha256(path),
            "style_version": _style_version()}


def _iter_file(path: Path, start: int, length: int):
    with path.open("rb") as f:
        f.seek(start)
        remaining = length
        while remaining > 0:
            block = f.read(min(CHUNK, remaining))
            if not block:
                break
            remaining -= len(block)
            yield block


@router.api_route("/{name}.pmtiles", methods=["GET", "HEAD"])
def pmtiles(name: str, request: Request):
    path = _map_path(request)
    if f"{name}.pmtiles" != path.name or not path.is_file():
        raise ServiceError("no_map", "No offline map installed on the ground station", 404)
    size = path.stat().st_size
    headers = {"Accept-Ranges": "bytes", "Cache-Control": "no-cache", "Access-Control-Allow-Origin": "*",
               "Access-Control-Expose-Headers": "Content-Range, Content-Length, Accept-Ranges"}

    header = request.headers.get("range")
    if not header:
        headers["Content-Length"] = str(size)
        if request.method == "HEAD":
            return Response(status_code=200, headers=headers, media_type="application/octet-stream")
        return StreamingResponse(_iter_file(path, 0, size), headers=headers, media_type="application/octet-stream")

    m = _RANGE.match(header.strip())
    start = end = None
    if m and (m.group(1) or m.group(2)):
        if m.group(1):
            start = int(m.group(1))
            end = int(m.group(2)) if m.group(2) else size - 1
        else:  # suffix range: last N bytes
            start = max(0, size - int(m.group(2)))
            end = size - 1
        end = min(end, size - 1)
    if start is None or start > end or start >= size:
        return Response(status_code=416, headers={**headers, "Content-Range": f"bytes */{size}"})

    length = end - start + 1
    headers.update({"Content-Range": f"bytes {start}-{end}/{size}", "Content-Length": str(length)})
    if request.method == "HEAD":
        return Response(status_code=206, headers=headers, media_type="application/octet-stream")
    return StreamingResponse(_iter_file(path, start, length), status_code=206, headers=headers,
                             media_type="application/octet-stream")


@router.get("/style.json")
def style(request: Request):
    path = WEB_DIR / "style.json"
    if not path.is_file():
        raise ServiceError("not_found", "No map style bundled", 404)
    base = str(request.base_url).rstrip("/")
    map_name = _map_path(request).name
    text = (path.read_text(encoding="utf-8")
            .replace("{{PMTILES_URL}}", f"pmtiles://{base}/maps/{map_name}")
            .replace("{{BASE_URL}}", base))
    return Response(text, media_type="application/json", headers={"Access-Control-Allow-Origin": "*"})


@router.get("/glyphs/{fontstack}/{glyph_range}.pbf")
def glyphs(fontstack: str, glyph_range: str):
    # MapLibre asks for comma-separated fallback stacks; serve the first font we have.
    for font in (f.strip() for f in fontstack.split(",")):
        path = _safe_child(WEB_DIR / "glyphs", font, f"{glyph_range}.pbf")
        if path.is_file():
            return FileResponse(path, media_type="application/x-protobuf",
                                headers={"Access-Control-Allow-Origin": "*"})
    raise ServiceError("not_found", "No such glyph range", 404)


@router.get("/sprites/{filename}")
def sprites(filename: str):
    path = _safe_child(WEB_DIR / "sprites", filename)
    if not path.is_file():
        raise ServiceError("not_found", "No such sprite file", 404)
    return FileResponse(path, headers={"Access-Control-Allow-Origin": "*"})
