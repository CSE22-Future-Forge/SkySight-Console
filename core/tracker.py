"""Multi-object tracking, backed by the Ultralytics trackers.

Tracking is delegated to Ultralytics' own implementations — ByteTrack by
default, BoT-SORT available — rather than the hand-rolled greedy IoU + centroid
association this module used to carry. They are better at the job: a proper
Kalman-filter motion model, and a second low-score association pass that keeps a
target through brief occlusion instead of dropping it and issuing a new id.

The tracker classes are used *directly*, not through ``model.track()``. That
matters here because the console builds its detections in three different ways —
a plain single-pass model, SAHI-style sliding windows whose tiles are merged
with NMS first, and the learned RGB/thermal fusion — and only the first fits
``model.track()``, which ties tracking to one model call over one image. Feeding
an already-merged detection list into :meth:`BYTETracker.update` works for all
three, and keeps detection independent of tracking, which is what allows
detection to run on a worker thread.

One consequence is worth stating plainly, because an earlier revision got it
wrong: a track is only ever moved by a detection. Nothing is extrapolated
forward between detector results, so a box always sits where a detection put it.
Coasting boxes along a velocity estimate drew boxes where no detection existed,
which read as boxes floating over the scene.

The UI contract is unchanged: consumers get plain :class:`Track` objects.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

import numpy as np

#: Which packaged Ultralytics tracker config to drive.
BYTETRACK_CONFIG = "bytetrack.yaml"
BOTSORT_CONFIG = "botsort.yaml"

#: Detection passes a track may go unreported before the operator stops seeing
#: its box. The tracker keeps its own lost-track buffer; this is an additional
#: display-side bound on top of that.
DEFAULT_MAX_MISSED = 30


@dataclass
class Detection:
    """A single detector output in pixel coordinates."""

    x1: float
    y1: float
    x2: float
    y2: float
    score: float = 0.0
    source: str = "rgb"
    label: str = "person"
    #: Filled in by the tracker; detectors leave this None.
    track_id: int | None = None

    @property
    def box(self):
        return (self.x1, self.y1, self.x2, self.y2)

    @property
    def width(self):
        return max(0.0, self.x2 - self.x1)

    @property
    def height(self):
        return max(0.0, self.y2 - self.y1)

    @property
    def area(self):
        return self.width * self.height

    @property
    def center(self):
        return ((self.x1 + self.x2) * 0.5, (self.y1 + self.y2) * 0.5)

    def as_int(self):
        return (
            int(self.x1),
            int(self.y1),
            int(self.x2),
            int(self.y2),
        )

    def crop(self, image):
        h, w = image.shape[:2]
        x1 = int(np.clip(self.x1, 0, w - 1))
        y1 = int(np.clip(self.y1, 0, h - 1))
        x2 = int(np.clip(self.x2, x1 + 1, w))
        y2 = int(np.clip(self.y2, y1 + 1, h))
        return image[y1:y2, x1:x2].copy()


@dataclass
class Track:
    """Persistent identity handed to the operator."""

    track_id: int
    box: tuple
    score: float = 0.0
    source: str = "rgb"
    age: int = 0
    hits: int = 1
    missed: int = 0
    confirmed: bool = False
    label: str = "person"
    #: Monotonic time the track was last reported by a detection (for time-based
    #: expiry; negative means "not stamped yet").
    last_seen: float = -1.0

    @property
    def width(self):
        return self.box[2] - self.box[0]

    @property
    def height(self):
        return self.box[3] - self.box[1]

    def area(self):
        return self.width * self.height


def iou(a, b):
    """IoU of two ``(x1, y1, x2, y2)`` boxes."""
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    ix1, iy1 = max(ax1, bx1), max(ay1, by1)
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)
    iw, ih = max(0.0, ix2 - ix1), max(0.0, iy2 - iy1)
    inter = iw * ih
    if inter <= 0:
        return 0.0
    area_a = max(0.0, ax2 - ax1) * max(0.0, ay2 - ay1)
    area_b = max(0.0, bx2 - bx1) * max(0.0, by2 - by1)
    union = area_a + area_b - inter
    return inter / union if union > 0 else 0.0


def centroid_distance(a, b):
    acx, acy = (a[0] + a[2]) * 0.5, (a[1] + a[3]) * 0.5
    bcx, bcy = (b[0] + b[2]) * 0.5, (b[1] + b[3]) * 0.5
    return float(np.hypot(acx - bcx, acy - bcy))


def _load_tracker_config(name, device=None):
    """Load a packaged Ultralytics tracker config into a plain namespace.

    Mirrors what ``model.track()`` does internally, so the same YAML files and
    the same defaults apply whether tracking is driven from here or from
    Ultralytics itself.
    """
    from ultralytics.utils import YAML, IterableSimpleNamespace
    from ultralytics.utils.checks import check_yaml

    cfg = IterableSimpleNamespace(**YAML.load(check_yaml(name)))
    if device is not None:
        # Used only by BoT-SORT's ReID encoder.
        cfg.device = device
    return cfg


class Tracker:
    """Ultralytics-backed tracker, ByteTrack by default.

    Parameters
    ----------
    engine:
        ``"bytetrack"`` (default) or ``"botsort"``.
    config:
        Explicit Ultralytics tracker YAML. Overrides ``engine``.
    max_missed:
        Detection passes a track may go unreported before its box is dropped
        from the display.
    timeout_s:
        Optional wall-clock limit: drop a track this many seconds after it was
        last reported by a detection. ``None`` (default) keeps the frame-count
        rule only. Used by the thermal/fusion tab, where a slideshow briefly
        spawns stale tracks that should not linger.
    """

    def __init__(
        self,
        engine="bytetrack",
        config=None,
        max_missed=DEFAULT_MAX_MISSED,
        timeout_s=None,
        frame_shape=(480, 640),
        **_compat,
    ):
        self.engine = str(engine).lower()
        self.config = config or (
            BOTSORT_CONFIG if self.engine == "botsort" else BYTETRACK_CONFIG
        )
        self.max_missed = int(max_missed)
        self.timeout_s = float(timeout_s) if timeout_s else None
        self.frame_shape = tuple(frame_shape)

        self._engine = None
        self._tracks: dict[int, Track] = {}

    # -------------------------------------------------------------- lifecycle --
    def _ensure_engine(self):
        """Build the Ultralytics tracker on first use.

        Lazy so that constructing a tab — or importing this module, as the
        headless selftest does — costs nothing and needs no torch.
        """
        if self._engine is not None:
            return self._engine
        from ultralytics.trackers import BOTSORT, BYTETracker

        args = _load_tracker_config(self.config)
        cls = BOTSORT if self.engine == "botsort" else BYTETracker
        self._engine = cls(args)
        return self._engine

    def set_frame_shape(self, shape):
        self.frame_shape = tuple(shape)

    def reset(self):
        self._tracks.clear()
        self._engine = None

    @property
    def tracks(self):
        return list(self._tracks.values())

    # ------------------------------------------------------------------ frame --
    def _as_boxes(self, detections):
        """Our detections as an Ultralytics ``Boxes`` the tracker can consume."""
        import torch

        from ultralytics.engine.results import Boxes

        if detections:
            data = torch.tensor(
                [[d.x1, d.y1, d.x2, d.y2, d.score, 0.0] for d in detections],
                dtype=torch.float32,
            )
        else:
            data = torch.zeros((0, 6), dtype=torch.float32)
        return Boxes(data, self.frame_shape)

    def update(self, detections, now=None):
        """Associate ``detections`` with existing tracks; returns the tracks to draw.

        ``now`` is a monotonic timestamp used only by the optional
        ``timeout_s`` expiry; without it the tracker stays purely frame-driven.
        """
        now = time.monotonic() if now is None else now
        engine = self._ensure_engine()
        detections = list(detections or [])
        rows = np.asarray(engine.update(self._as_boxes(detections)), dtype=float)

        seen = set()
        for row in rows:
            x1, y1, x2, y2, raw_id, score = row[:6]
            track_id = int(raw_id)
            seen.add(track_id)

            track = self._tracks.get(track_id)
            if track is None:
                # Everything the tracker reports has already been through its
                # own activation gate, so it is a real track, not a maybe.
                track = Track(
                    track_id=track_id,
                    box=(float(x1), float(y1), float(x2), float(y2)),
                    confirmed=True,
                    last_seen=now,
                )
                self._tracks[track_id] = track
            else:
                track.box = (float(x1), float(y1), float(x2), float(y2))
                track.age += 1
                track.hits += 1
                track.missed = 0
                track.last_seen = now
            track.score = float(score)

            # Attribute source and label from the detection the row came from.
            # Rows the tracker carried over without a fresh detection (no
            # matching index) keep whatever they were last attributed to.
            index = int(row[7]) if row.shape[0] > 7 else -1
            if 0 <= index < len(detections):
                det = detections[index]
                track.source = det.source
                track.label = det.label
                track.score = det.score

        for track_id in [t for t in self._tracks if t not in seen]:
            track = self._tracks[track_id]
            track.age += 1
            track.missed += 1
            if track.missed > self.max_missed or self._expired(track, now):
                del self._tracks[track_id]

        return [t for t in self._tracks.values() if t.confirmed]

    def _expired(self, track, now):
        """True when a track has outlived the optional wall-clock timeout."""
        return (
            self.timeout_s is not None
            and track.last_seen >= 0.0
            and (now - track.last_seen) >= self.timeout_s
        )

    def predict(self, now=None):
        """The tracks as they stand, unchanged, minus any past their timeout.

        A box moves only when a detection moves it, so there is nothing to
        extrapolate on a frame without a new detector result. This exists so the
        pipeline can advance a frame while a slow model is still working.
        """
        if self.timeout_s is not None:
            now = time.monotonic() if now is None else now
            for track_id in [t for t, tr in self._tracks.items() if self._expired(tr, now)]:
                del self._tracks[track_id]
        return [t for t in self._tracks.values() if t.confirmed]

    def snapshot(self):
        """Track state for the operator-facing table, ordered by ID."""
        return sorted(self._tracks.values(), key=lambda t: t.track_id)
