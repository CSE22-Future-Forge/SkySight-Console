"""Image plumbing: colour maps, RGB/thermal fusion, annotation overlays.

Everything here is plain OpenCV + numpy so it can be unit-checked headlessly.
The fusion rules in :func:`fuse` are the single source of truth for what the
operator sees, including the "kill RGB completely" case used for night ops.
"""

from __future__ import annotations

from datetime import datetime, timezone

import cv2
import numpy as np

# ------------------------------------------------------------- colour maps ---
COLORMAPS = {
    "Inferno": cv2.COLORMAP_INFERNO,
    "Ironbow": cv2.COLORMAP_BONE,
    "Ember": cv2.COLORMAP_HOT,
    "Arctic": cv2.COLORMAP_OCEAN,
    "Plasma": cv2.COLORMAP_PLASMA,
    "Grey": None,
}

FUSION_MODES = (
    "Overlay blend",
    "Side by side",
    "Thermal only",
    "RGB only",
    "Difference",
)


# --------------------------------------------------------------- utilities ---
def normalize_thermal(frame):
    """Coerce a thermal frame to a usable 8-bit single channel image.

    Real radiometric payloads may arrive as uint8, uint16 or float. Percentile
    clipping keeps a hot target from flattening everything else to white.
    """
    if frame is None:
        return None
    if frame.ndim == 3:
        frame = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    if frame.dtype == np.uint8:
        return frame
    data = frame.astype(np.float32)
    lo, hi = np.percentile(data, (1.0, 99.0))
    if hi - lo < 1e-6:
        lo, hi = float(data.min()), float(data.max() or 1.0)
    if hi - lo < 1e-6:
        return np.zeros(data.shape, dtype=np.uint8)
    scaled = (data - lo) / (hi - lo)
    return np.clip(scaled * 255.0, 0, 255).astype(np.uint8)


def thermal_to_bgr(frame, colormap="Inferno"):
    """Colourise an 8-bit thermal image."""
    gray = normalize_thermal(frame)
    if gray is None:
        return None
    lut = COLORMAPS.get(colormap, cv2.COLORMAP_INFERNO)
    if lut is None:
        return cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)
    return cv2.applyColorMap(gray, lut)


#: One-entry memo for :func:`_thermal_view`.
_THERMAL_VIEW = {"frame": None, "colormap": None, "view": None}


def _thermal_view(frame, colormap):
    """``thermal_to_bgr`` with a one-entry cache.

    The fusion tab composites on every render tick while a thermal frame only
    arrives at the payload's own (much lower) rate, so the same frame is
    colourised repeatedly — and on a non-uint8 payload that also means repeating
    the percentile normalisation, which is not cheap. Keyed on the frame object
    itself, which is safe because a new capture is always a new array and
    holding the reference keeps it alive.

    The result is shared: callers must not draw into it.
    """
    cache = _THERMAL_VIEW
    if cache["frame"] is frame and cache["colormap"] == colormap:
        return cache["view"]
    view = thermal_to_bgr(frame, colormap)
    cache["frame"], cache["colormap"], cache["view"] = frame, colormap, view
    return view


def fit(image, width, height):
    """Resize preserving aspect ratio, centred on a black canvas."""
    if image is None or image.size == 0:
        return np.zeros((height, width, 3), dtype=np.uint8)
    ih, iw = image.shape[:2]
    if ih == 0 or iw == 0:
        return np.zeros((height, width, 3), dtype=np.uint8)
    scale = min(width / iw, height / ih)
    nw, nh = max(1, int(iw * scale)), max(1, int(ih * scale))
    resized = cv2.resize(image, (nw, nh), interpolation=cv2.INTER_AREA)
    canvas = np.zeros((height, width, 3), dtype=np.uint8)
    y0, x0 = (height - nh) // 2, (width - nw) // 2
    canvas[y0 : y0 + nh, x0 : x0 + nw] = resized
    return canvas


def fit_scale_offset(src_w, src_h, dst_w, dst_h):
    """Scale + centring offset used by :func:`fit`, for mapping coordinates.

    Returns ``(scale, offset_x, offset_y)`` so a point ``(x, y)`` in the source
    image maps to ``(x * scale + offset_x, y * scale + offset_y)`` in the
    destination letterboxed canvas.
    """
    if src_w <= 0 or src_h <= 0:
        return 1.0, 0.0, 0.0
    scale = min(dst_w / float(src_w), dst_h / float(src_h))
    return scale, (dst_w - src_w * scale) / 2.0, (dst_h - src_h * scale) / 2.0


def letterbox_pair(a, b):
    """Put two images side by side at a common height."""
    if a is None or b is None:
        return a if b is None else b
    ha, hb = a.shape[0], b.shape[0]
    height = max(ha, hb)
    left = fit(a, int(a.shape[1] * height / max(1, ha)), height)
    right = fit(b, int(b.shape[1] * height / max(1, hb)), height)
    return np.hstack([left, right])


# ----------------------------------------------------------------- fusion ---
def fuse(
    rgb_bgr,
    thermal_frame,
    *,
    mode="Overlay blend",
    alpha=0.5,
    use_rgb=True,
    use_thermal=True,
    colormap="Inferno",
    size=(960, 540),
):
    """Compose the operator-facing image.

    ``alpha`` is the RGB weight (0 = pure thermal, 1 = pure RGB). RGB and
    thermal can each be switched off independently; with RGB off the output is
    thermal-only regardless of mode, which is the night-ops requirement.
    """
    w, h = size
    rgb = rgb_bgr if use_rgb else None
    thermal = thermal_frame if use_thermal else None

    if rgb is None and thermal is None:
        return np.zeros((h, w, 3), dtype=np.uint8)

    if thermal is not None and rgb is None:
        return fit(_thermal_view(thermal, colormap), w, h)

    if rgb is not None and thermal is None:
        return fit(rgb, w, h)

    rgb_view = rgb
    therm_view = _thermal_view(thermal, colormap)
    if therm_view.shape[0] != rgb_view.shape[0]:
        scale = rgb_view.shape[0] / therm_view.shape[0]
        therm_view = cv2.resize(
            therm_view,
            (max(1, int(therm_view.shape[1] * scale)), rgb_view.shape[0]),
            interpolation=cv2.INTER_LINEAR,
        )

    if mode == "Side by side":
        return fit(letterbox_pair(rgb_view, therm_view), w, h)
    if mode == "Thermal only":
        return fit(therm_view, w, h)
    if mode == "RGB only":
        return fit(rgb_view, w, h)
    if mode == "Difference":
        gray_rgb = cv2.cvtColor(rgb_view, cv2.COLOR_BGR2GRAY).astype(np.float32)
        gray_th = cv2.cvtColor(therm_view, cv2.COLOR_BGR2GRAY).astype(np.float32)
        diff = cv2.normalize(np.abs(gray_rgb - gray_th), None, 0, 255, cv2.NORM_MINMAX)
        return fit(cv2.applyColorMap(diff.astype(np.uint8), cv2.COLORMAP_INFERNO), w, h)

    if rgb_view.shape != therm_view.shape:
        therm_view = cv2.resize(
            therm_view, (rgb_view.shape[1], rgb_view.shape[0]), interpolation=cv2.INTER_LINEAR
        )
    a = float(np.clip(alpha, 0.0, 1.0))
    blended = cv2.addWeighted(rgb_view, a, therm_view, 1.0 - a, 0.0)
    return fit(blended, w, h)


# --------------------------------------------------------------- overlays ---
def _text(img, text, org, scale=0.5, color=(255, 255, 255), thickness=1):
    # Draw the glyphs only — no black outline/backing behind the text.
    cv2.putText(img, text, org, cv2.FONT_HERSHEY_SIMPLEX, scale, color, thickness, cv2.LINE_AA)


def draw_tracks(image, tracks, show_ids=True, show_score=True,
                scale=1.0, offset=(0.0, 0.0), copy=True):
    """Annotate a frame with current tracks.

    ``scale`` (a scalar or ``(sx, sy)``) and ``offset`` map track boxes from the
    detector's coordinate space onto the composed view, so the fusion tab can
    draw on a resized/letterboxed composite and still line the boxes up.

    ``copy=False`` draws straight into ``image`` and is what the live render
    path uses, so a frame is not duplicated just to be annotated. Pass your own
    buffer, not one the detector may still be reading.
    """
    if image is None:
        return image
    out = image.copy() if copy else image
    h, w = out.shape[:2]
    thickness = max(1, int(round(min(h, w) / 500)))
    if isinstance(scale, (int, float)):
        sx = sy = float(scale)
    else:
        sx, sy = float(scale[0]), float(scale[1])
    ox, oy = float(offset[0]), float(offset[1])

    for t in tracks:
        bx1, by1, bx2, by2 = t.box
        x1 = int(round(bx1 * sx + ox))
        y1 = int(round(by1 * sy + oy))
        x2 = int(round(bx2 * sx + ox))
        y2 = int(round(by2 * sy + oy))
        x1, y1 = max(0, x1), max(0, y1)
        x2, y2 = min(w - 1, x2), min(h - 1, y2)
        if x2 <= x1 or y2 <= y1:
            continue

        color = (56, 189, 248) if t.source == "rgb" else (251, 146, 60)
        if t.source == "fusion":
            color = (167, 139, 250)
        if not t.confirmed:
            color = tuple(int(c * 0.45) for c in color)

        cv2.rectangle(out, (x1, y1), (x2, y2), color, thickness, cv2.LINE_AA)

        if show_ids:
            label = f"P-{t.track_id:03d}"
            if show_score:
                label += f" {t.score * 100:3.0f}%"
            (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.45, 1)
            ty = max(th + 6, y1)
            cv2.rectangle(out, (x1, ty - th - 6), (x1 + tw + 10, ty + 2), color, -1)
            _text(out, label, (x1 + 5, ty - 4), 0.45, (10, 12, 16), 1)
    return out


def stamp(image, lines, corner="tl", color=(147, 163, 184), copy=True):
    """Burn a small telemetry block (top-left / top-right) into a frame.

    ``copy=False`` draws into ``image`` directly; see :func:`draw_tracks`.
    """
    if image is None or not lines:
        return image
    out = image.copy() if copy else image
    h, w = out.shape[:2]
    scale = 0.5 if min(h, w) >= 700 else 0.4
    step = int(20 * scale / 0.5)
    pad = 12
    ys = list(range(pad + step, pad + step * len(lines) + step, step))
    xs = [pad if corner == "tl" else w - pad - 150 for _ in lines]
    for text, (x, y) in zip(lines, zip(xs, ys)):
        _text(out, text, (x, y), scale, color, 1)
    return out


def fit_to_crop(image, box, min_side=64):
    """Crop ``box`` from ``image``, padding out boxes too small to be useful.

    Small far-away detections would otherwise produce a useless crop for the
    feature vector, so they are grown symmetrically (and re-clipped) instead.
    """
    if image is None or image.size == 0:
        return None
    h, w = image.shape[:2]
    x1, y1, x2, y2 = (float(v) for v in box)

    if x2 - x1 < min_side:
        cx = (x1 + x2) / 2.0
        half = min_side / 2.0
        x1, x2 = cx - half, cx + half
    if y2 - y1 < min_side:
        cy = (y1 + y2) / 2.0
        half = min_side / 2.0
        y1, y2 = cy - half, cy + half

    x1 = int(np.clip(x1, 0, w - 1))
    y1 = int(np.clip(y1, 0, h - 1))
    x2 = int(np.clip(x2, x1 + 2, w))
    y2 = int(np.clip(y2, y1 + 2, h))
    return image[y1:y2, x1:x2].copy()


def time_label(utc=False):
    """Timestamp burned into frames so stills are self-documenting."""
    now = datetime.now(timezone.utc) if utc else datetime.now()
    return now.strftime("%Y-%m-%d %H:%M:%S UTC" if utc else "%Y-%m-%d %H:%M:%S")


def placeholder(w, h, message, sub=None, color=(99, 116, 138)):
    """Dark 'no signal' canvas so the layout never collapses."""
    out = np.zeros((h, w, 3), dtype=np.uint8)
    out[:, :] = (18, 22, 28)
    scale = 0.9 if min(h, w) >= 600 else 0.6
    (tw, th), _ = cv2.getTextSize(message, cv2.FONT_HERSHEY_SIMPLEX, scale, 2)
    _text(out, message, ((w - tw) // 2, (h - th) // 2), scale, color, 2)
    if sub:
        scale2 = 0.5
        (tw2, th2), _ = cv2.getTextSize(sub, cv2.FONT_HERSHEY_SIMPLEX, scale2, 1)
        _text(out, sub, ((w - tw2) // 2, (h - th2) // 2 + th + 18), scale2, (70, 84, 100), 1)
    return out


def to_qimage(bgr, copy=True):
    """numpy BGR -> QImage.

    Uses ``Format_BGR888``, which is byte-for-byte what OpenCV already holds,
    so the per-frame BGR->RGB conversion is skipped entirely. With ``copy=True``
    (the default) Qt owns an independent buffer and the caller may reuse
    ``bgr`` immediately.

    ``copy=False`` returns a QImage that *views* the caller's memory: it is only
    valid while that array is alive and unmodified. Use it when the image is
    consumed straight away (``QPixmap.fromImage`` deep-copies), and never hold
    the result across a frame.
    """
    from PySide6.QtGui import QImage

    if bgr is None or getattr(bgr, "size", 0) == 0:
        return QImage()
    if bgr.dtype != np.uint8:
        bgr = np.clip(bgr, 0, 255).astype(np.uint8)
    if bgr.ndim == 2:
        bgr = np.ascontiguousarray(bgr)
        h, w = bgr.shape[:2]
        image = QImage(bgr.data, w, h, w, QImage.Format_Grayscale8)
    elif bgr.shape[2] == 4:
        # Format_BGR888 has no alpha channel to map a 4-channel buffer onto.
        bgr = np.ascontiguousarray(cv2.cvtColor(bgr, cv2.COLOR_BGRA2BGR))
        h, w, ch = bgr.shape
        image = QImage(bgr.data, w, h, ch * w, QImage.Format_BGR888)
    else:
        bgr = np.ascontiguousarray(bgr)
        h, w, ch = bgr.shape
        image = QImage(bgr.data, w, h, ch * w, QImage.Format_BGR888)
    return image.copy() if copy else image


def to_thumb_bytes(bgr, max_side=192):
    """PNG-encode a downscaled crop for review grid / database thumbnails."""
    if bgr is None or bgr.size == 0:
        return b""
    h, w = bgr.shape[:2]
    scale = min(1.0, max_side / max(1, max(h, w)))
    if scale < 1.0:
        bgr = cv2.resize(bgr, (max(1, int(w * scale)), max(1, int(h * scale))), interpolation=cv2.INTER_AREA)
    ok, buf = cv2.imencode(".png", bgr)
    return buf.tobytes() if ok else b""


def read_image_bytes(data):
    arr = np.frombuffer(data, dtype=np.uint8)
    return cv2.imdecode(arr, cv2.IMREAD_COLOR) if arr.size else None