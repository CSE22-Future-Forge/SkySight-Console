"""Detection image files: full frame, person crop (+20% margin) and phone thumbnail.

Layout (spec 4.2): images/<mission_id>/<detection_id>_{frame,crop,thumb}.jpg.
Paths returned are relative to the images root so the data dir can move.
cv2.imdecode/imencode are used instead of imread/imwrite because the latter
fail on non-ASCII Windows paths.
"""

from __future__ import annotations

import base64
from pathlib import Path

import cv2
import numpy as np

CROP_MARGIN = 0.20
CROP_MIN_SIDE = 64
THUMB_LONG_SIDE = 320
THUMB_QUALITY = 80
FRAME_QUALITY = 90


class ImageError(ValueError):
    pass


def _decode(data: bytes, what: str):
    img = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        raise ImageError(f"{what} is not a decodable image")
    return img


def _read_source(path: str | None, b64: str | None, what: str):
    if b64:
        try:
            return _decode(base64.b64decode(b64, validate=False), what)
        except (ValueError, TypeError) as exc:
            raise ImageError(f"{what} base64 is invalid") from exc
    if path:
        p = Path(path)
        if not p.is_file():
            raise ImageError(f"{what} not found: {path}")
        return _decode(p.read_bytes(), what)
    return None


def _write_jpeg(img, path: Path, quality: int) -> None:
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, quality])
    if not ok:
        raise ImageError(f"could not encode {path.name}")
    path.write_bytes(buf.tobytes())


def crop_with_margin(frame, x: int, y: int, w: int, h: int):
    fh, fw = frame.shape[:2]
    mx, my = int(round(w * CROP_MARGIN)), int(round(h * CROP_MARGIN))
    x0, y0, x1, y1 = x - mx, y - my, x + w + mx, y + h + my
    # Tiny aerial targets: grow the window around the centre so the crop has context.
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    half_w = max((x1 - x0) / 2, CROP_MIN_SIDE / 2)
    half_h = max((y1 - y0) / 2, CROP_MIN_SIDE / 2)
    x0, x1 = int(max(0, cx - half_w)), int(min(fw, cx + half_w))
    y0, y1 = int(max(0, cy - half_h)), int(min(fh, cy + half_h))
    if x1 <= x0 or y1 <= y0:
        raise ImageError("bbox lies outside the frame")
    return frame[y0:y1, x0:x1].copy()


def make_thumb(crop):
    h, w = crop.shape[:2]
    scale = THUMB_LONG_SIDE / max(h, w)
    interp = cv2.INTER_AREA if scale < 1 else cv2.INTER_CUBIC
    return cv2.resize(crop, (max(1, round(w * scale)), max(1, round(h * scale))), interpolation=interp)


def store_detection_images(images_root: Path, mission_id: str, detection_id: str, images, bbox) -> dict:
    """Write frame/crop/thumb (+ optional thermal crop). Returns relative paths."""
    frame = _read_source(images.frame_path, images.frame_b64, "frame")
    thermal = _read_source(images.thermal_crop_path, images.thermal_crop_b64, "thermal crop")
    crop = crop_with_margin(frame, bbox.x, bbox.y, bbox.w, bbox.h)

    folder = images_root / mission_id
    folder.mkdir(parents=True, exist_ok=True)
    rel = {
        "frame_path": f"{mission_id}/{detection_id}_frame.jpg",
        "crop_path": f"{mission_id}/{detection_id}_crop.jpg",
        "thumb_path": f"{mission_id}/{detection_id}_thumb.jpg",
        "thermal_crop_path": f"{mission_id}/{detection_id}_thermal.jpg" if thermal is not None else None,
    }
    _write_jpeg(frame, images_root / rel["frame_path"], FRAME_QUALITY)
    _write_jpeg(crop, images_root / rel["crop_path"], FRAME_QUALITY)
    _write_jpeg(make_thumb(crop), images_root / rel["thumb_path"], THUMB_QUALITY)
    if thermal is not None:
        _write_jpeg(thermal, images_root / rel["thermal_crop_path"], FRAME_QUALITY)
    return rel


def remove_files(images_root: Path, rel_paths: dict) -> None:
    for rel in rel_paths.values():
        if rel:
            (images_root / rel).unlink(missing_ok=True)
