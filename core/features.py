"""Feature vectors for person re-identification.

PLACEHOLDER IMPLEMENTATION.
The vector is a classical appearance descriptor — HOG (oriented-gradient
histograms over a person-shaped aspect grid) plus an HSV colour histogram plus
a few geometry terms. It exists so the database, duplicate-warning and review
flows are fully wired and testable *today*, without downloading a model.

It is a genuine classical re-ID baseline and it does separate clearly different
subjects, but it is far weaker than a learned embedding: it cannot tell twins
apart and it degrades on low-contrast thermal crops. That is expected and is
precisely what the custom head replaces.

Swap plan: replace the body of :func:`compute_embedding` with a forward pass of
the custom Ultralytics re-ID head and keep the return contract —

    List[float], L2-normalised, length :data:`EMBEDDING_DIM`

Everything downstream (JSON persistence, cosine search, thresholds, the whole
database UI) then keeps working without modification.
"""

from __future__ import annotations

import cv2
import numpy as np

HOG_CELLS = (8, 4)
HOG_BINS = 9
COLOR_BINS = (8, 8)
STATS_DIM = 4

HOG_DIM = HOG_CELLS[0] * HOG_CELLS[1] * HOG_BINS
COLOR_DIM = COLOR_BINS[0] * COLOR_BINS[1]
EMBEDDING_DIM = HOG_DIM + COLOR_DIM + STATS_DIM

# Relative importance of each block before the final L2 normalisation.
W_HOG = 1.0
W_COLOR = 0.65
W_STATS = 0.30

# Score bands, calibrated against the measured distribution of this descriptor
# (same subject ~0.99, different subject <=0.95). Mirrored and part-occluded
# views sit just below the warn band on purpose: for duplicate *filing* the
# safe default is to under-flag and let the operator decide.
SIMILAR_AT = 0.88
WARN_AT = 0.96
DANGER_AT = 0.985

HOG_SIZE = (64, 128)  # width, height — roughly upright body proportions


def _prepare(crop_bgr):
    """Normalise any incoming crop into a 3-channel uint8 BGR image."""
    if crop_bgr is None or getattr(crop_bgr, "size", 0) == 0:
        return None
    if crop_bgr.ndim == 2:
        crop_bgr = cv2.cvtColor(crop_bgr, cv2.COLOR_GRAY2BGR)
    elif crop_bgr.ndim == 3 and crop_bgr.shape[2] == 4:
        crop_bgr = cv2.cvtColor(crop_bgr, cv2.COLOR_BGRA2BGR)
    elif crop_bgr.ndim == 3 and crop_bgr.shape[2] == 1:
        crop_bgr = cv2.cvtColor(crop_bgr, cv2.COLOR_GRAY2BGR)
    if crop_bgr.dtype != np.uint8:
        crop_bgr = np.clip(crop_bgr, 0, 255).astype(np.uint8)
    return crop_bgr


def _hog(gray):
    """L2-normalised per-cell orientation histograms."""
    img = cv2.resize(gray, HOG_SIZE, interpolation=cv2.INTER_AREA).astype(np.float32)

    gx = cv2.Sobel(img, cv2.CV_32F, 1, 0, ksize=1)
    gy = cv2.Sobel(img, cv2.CV_32F, 0, 1, ksize=1)
    magnitude = np.sqrt(gx * gx + gy * gy)

    # Unsigned orientation keeps the histogram shift-invariant in sign.
    angle = np.arctan2(gy, gx)
    angle = np.where(angle < 0.0, angle + np.pi, angle)
    scaled = np.clip((angle * (HOG_BINS / np.pi)).astype(np.int32), 0, HOG_BINS - 1)

    cells_y, cells_x = HOG_CELLS
    height, width = magnitude.shape
    cell_h = max(1, height // cells_y)
    cell_w = max(1, width // cells_x)

    hog = np.zeros((cells_y * cells_x, HOG_BINS), dtype=np.float32)
    for i in range(cells_y):
        for j in range(cells_x):
            y0, x0 = i * cell_h, j * cell_w
            block_mag = magnitude[y0 : y0 + cell_h, x0 : x0 + cell_w]
            block_bin = scaled[y0 : y0 + cell_h, x0 : x0 + cell_w]
            if block_mag.size == 0:
                continue
            hist, _ = np.histogram(
                block_bin, bins=HOG_BINS, weights=block_mag, range=(0, HOG_BINS)
            )
            hog[i * cells_x + j] = hist

    norms = np.linalg.norm(hog, axis=1, keepdims=True)
    hog = hog / np.maximum(norms, 1e-6)
    return hog.ravel()


def _color_hist(hsv):
    hist = cv2.calcHist([hsv], [0, 1], None, list(COLOR_BINS), [0, 180, 0, 256])
    hist = cv2.normalize(hist, hist, 0, 1, cv2.NORM_MINMAX)
    return hist.astype(np.float32).ravel()


def _descriptor(crop_bgr):
    img = _prepare(crop_bgr)
    if img is None:
        return np.zeros(EMBEDDING_DIM, dtype=np.float32)

    if min(img.shape[:2]) < 12:
        img = cv2.resize(
            img,
            (max(HOG_SIZE[0], img.shape[1]), max(HOG_SIZE[1], img.shape[0])),
            interpolation=cv2.INTER_LINEAR,
        )

    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)

    # Resize to the person grid, then let the colour histogram see full detail.
    grid = cv2.resize(img, HOG_SIZE, interpolation=cv2.INTER_AREA)

    h, w = img.shape[:2]
    aspect = w / float(max(1, h))
    gray_norm = gray.astype(np.float32) / 255.0
    fill = float(gray_norm.mean())
    sat = float(hsv[:, :, 1].mean()) / 255.0
    stats = np.array([aspect, fill, sat, 1.0 / (1.0 + aspect)], dtype=np.float32)

    return np.concatenate(
        [
            _hog(cv2.cvtColor(grid, cv2.COLOR_BGR2GRAY)) * W_HOG,
            _color_hist(hsv) * W_COLOR,
            stats * W_STATS,
        ]
    ).astype(np.float32)


def compute_embedding(crop_bgr):
    """Return an L2-normalised feature vector for one person crop."""
    vec = _descriptor(crop_bgr)
    norm = float(np.linalg.norm(vec))
    if norm > 1e-6:
        vec = vec / norm
    return [round(float(v), 5) for v in vec]


def cosine_similarity(a, b):
    """Cosine similarity in ``[-1, 1]`` (both inputs are unit vectors)."""
    va = np.asarray(a, dtype=np.float32)
    vb = np.asarray(b, dtype=np.float32)
    if va.size == 0 or vb.size == 0 or va.size != vb.size:
        return 0.0
    denom = float(np.linalg.norm(va) * np.linalg.norm(vb))
    if denom < 1e-8:
        return 0.0
    return float(np.clip(np.dot(va, vb) / denom, -1.0, 1.0))


def similarity_label(score, warn_at=WARN_AT, danger_at=DANGER_AT):
    """Human wording for a similarity score, used across the database UI."""
    if score >= danger_at:
        return "LIKELY DUPLICATE"
    if score >= warn_at:
        return "POSSIBLE MATCH"
    if score >= SIMILAR_AT:
        return "SIMILAR"
    return "DISTINCT"


def suggest_thresholds(same_pairs, diff_pairs, headroom=0.015):
    """Derive warn/danger bands from labelled similarity samples.

    Call this once the real embedding is in place: collect a handful of
    same-subject pairs (different frames of one person) and different-subject
    pairs, then feed both lists in. ``warn`` is placed just above the worst
    different-subject score so genuine duplicates surface without flooding the
    operator, and ``danger`` just under the weakest same-subject score.

    Returns ``(warn_at, danger_at)``.
    """
    same = sorted(float(s) for s in same_pairs) or [1.0]
    diff = sorted(float(s) for s in diff_pairs) or [0.0]

    warn = min(0.995, max(diff[-1] + headroom, SIMILAR_AT))
    danger = max(warn + 0.001, min(same[0] - headroom, 1.0))
    if danger <= warn:
        # Overlapping distributions: fall back to a hard split rather than
        # emitting an inverted band that would flag every pair as duplicate.
        split = (diff[-1] + same[0]) / 2.0 if same[0] > diff[-1] else warn
        warn, danger = split, min(0.999, split + 0.02)
    return round(warn, 4), round(danger, 4)


def augment(crop_bgr):
    """Cheap test-time augmentation set, so one capture yields several views."""
    img = _prepare(crop_bgr)
    if img is None:
        return []
    h, w = img.shape[:2]
    variants = [img, cv2.flip(img, 1)]
    for angle in (8, -8):
        m = cv2.getRotationMatrix2D((w * 0.5, h * 0.5), angle, 1.0)
        variants.append(cv2.warpAffine(img, m, (w, h), borderMode=cv2.BORDER_REPLICATE))
    return variants