"""Lightweight multi-object tracker.

Deliberately dependency-free (numpy only): greedy IoU association with a
centroid-distance fallback. This is the "lightweight tracker" stage that runs
after detection and before the operator review/database step.

If ByteTrack/BoT-SORT become available later, only :meth:`Tracker.update` needs
to be re-pointed at the new engine — the UI consumes plain :class:`Track`
objects either way.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np


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
    temp_c: float | None = None
    label: str = "person"

    @property
    def width(self):
        return self.box[2] - self.box[0]

    @property
    def height(self):
        return self.box[3] - self.box[1]

    def area(self):
        return self.width * self.height


def iou(a, b):
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


class Tracker:
    """Greedy IoU + centroid tracker.

    Parameters
    ----------
    iou_threshold:
        Minimum IoU for a match.
    centroid_ratio:
        Fallback gate expressed as a fraction of the frame diagonal, so the
        tolerance adapts to resolution instead of being a hard pixel count.
    max_missed:
        Frames a track survives without a detection before it is dropped.
    min_hits:
        Hits required before a track is surfaced as confirmed.
    """

    def __init__(
        self,
        iou_threshold=0.25,
        centroid_ratio=0.06,
        max_missed=15,
        min_hits=2,
        frame_shape=(480, 640),
    ):
        self.iou_threshold = iou_threshold
        self.centroid_ratio = centroid_ratio
        self.max_missed = max_missed
        self.min_hits = min_hits
        self.frame_shape = frame_shape

        self._tracks: list[Track] = []
        self._next_id = 1

    def set_frame_shape(self, shape):
        self.frame_shape = shape

    def reset(self):
        self._tracks.clear()
        self._next_id = 1

    @property
    def tracks(self):
        return list(self._tracks)

    def update(self, detections):
        """Advance the tracker by one frame and return confirmed tracks."""
        h, w = self.frame_shape[:2]
        diag = float(np.hypot(w, h)) or 1.0
        centroid_gate = self.centroid_ratio * diag

        unmatched_tracks = list(range(len(self._tracks)))
        unmatched_dets = list(range(len(detections)))
        pairs = []

        for ti in unmatched_tracks:
            tbox = self._tracks[ti].box
            for di in unmatched_dets:
                dbox = detections[di].box
                overlap = iou(tbox, dbox)
                dist = centroid_distance(tbox, dbox)
                if overlap >= self.iou_threshold:
                    pairs.append((overlap, -dist, ti, di))
                elif dist <= centroid_gate:
                    pairs.append((0.0, -dist, ti, di))

        pairs.sort(reverse=True)
        used_tracks, used_dets = set(), set()
        for _overlap, _negdist, ti, di in pairs:
            if ti in used_tracks or di in used_dets:
                continue
            used_tracks.add(ti)
            used_dets.add(di)
            self._attach(self._tracks[ti], detections[di])

        for ti, track in enumerate(self._tracks):
            if ti in used_tracks:
                continue
            track.age += 1
            track.missed += 1

        for di, det in enumerate(detections):
            if di not in used_dets:
                self._spawn(det)

        self._tracks = [t for t in self._tracks if t.missed <= self.max_missed]
        return [t for t in self._tracks if t.confirmed]

    def _attach(self, track, det):
        track.box = det.box
        track.score = det.score
        track.source = det.source
        track.label = det.label
        track.age += 1
        track.hits += 1
        track.missed = 0
        if track.hits >= self.min_hits:
            track.confirmed = True

    def _spawn(self, det):
        self._tracks.append(
            Track(
                track_id=self._next_id,
                box=det.box,
                score=det.score,
                source=det.source,
                label=det.label,
                confirmed=self.min_hits <= 1,
            )
        )
        self._next_id += 1

    def snapshot(self):
        """Track state for the operator-facing table, ordered by ID."""
        return sorted(self.tracks, key=lambda t: t.track_id)