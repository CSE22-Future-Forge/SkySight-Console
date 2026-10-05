"""Lightweight multi-object tracker.

Deliberately dependency-free (numpy only): greedy IoU association with a
centroid-distance fallback, plus a constant-velocity motion model so a track
keeps moving sensibly between detector results.

Why the motion model matters here: the detectors in this console run at very
different rates — the fused RGB/thermal model takes a few hundred milliseconds
per pair, the sliding-window RGB model about a tenth of a second — while the
display refreshes far faster. Two things follow, and both are handled by
predicting:

* between two detector results the target has moved, so matching a new box
  against the track's *last* box fails once the motion exceeds the gate and the
  person is handed a brand-new id. Matching against the predicted box keeps the
  identity instead.
* the display has to draw *something* on the frames in between. The tracker is
  advanced with :meth:`Tracker.predict`, which moves boxes along their velocity
  without touching hit/miss bookkeeping.

The split between :meth:`Tracker.update` (a detector result arrived — count
hits and misses) and :meth:`Tracker.predict` (no result — just move) is what
keeps ``hits`` honest: a track can only be confirmed by detections that were
actually observed, never by re-feeding a stale result.

If ByteTrack/BoT-SORT become available later, only :meth:`Tracker.update` needs
to be re-pointed at the new engine — the UI consumes plain :class:`Track`
objects either way.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

import numpy as np

#: Nothing in a person-tracking scene moves faster than this on screen; a match
#: implying more is a mis-association, so the velocity estimate is clamped.
MAX_SPEED_PX_S = 4000.0


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
    label: str = "person"
    #: Constant-velocity estimate in px/s, used to coast between results.
    vx: float = 0.0
    vy: float = 0.0
    #: True once a real velocity has been measured. Until then the estimate is
    #: unknown rather than zero, which matters twice: the first measurement must
    #: not be smoothed away from zero, and an unmatched track may widen its
    #: search (see :meth:`Tracker.update`).
    has_velocity: bool = False
    #: Monotonic time of the last detection that matched this track.
    seen_at: float = 0.0
    #: Monotonic time of the last update/predict step, for the next dt.
    step_at: float = 0.0
    #: Centre of the box the last *detection* reported. Kept separately from
    #: ``box`` because ``box`` coasts ahead between results — measuring velocity
    #: against it would score a correctly predicted target as stationary and
    #: smooth the estimate down to nothing.
    last_cx: float = 0.0
    last_cy: float = 0.0

    @property
    def width(self):
        return self.box[2] - self.box[0]

    @property
    def height(self):
        return self.box[3] - self.box[1]

    def area(self):
        return self.width * self.height

    def advance(self, dt):
        """Move the box along its velocity by ``dt`` seconds."""
        if dt <= 0.0 or (self.vx == 0.0 and self.vy == 0.0):
            return
        x1, y1, x2, y2 = self.box
        dx, dy = self.vx * dt, self.vy * dt
        self.box = (x1 + dx, y1 + dy, x2 + dx, y2 + dy)


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
    """Greedy IoU + centroid tracker with constant-velocity prediction.

    Parameters
    ----------
    iou_threshold:
        Minimum IoU for a match.
    centroid_ratio:
        Fallback gate expressed as a fraction of the frame diagonal, so the
        tolerance adapts to resolution instead of being a hard pixel count.
        The gate is additionally capped relative to the track's own height
        (see ``size_gate_ratio``) so a far-away target cannot be matched to a
        detection a whole person-width away just because the frame is large.
    max_missed:
        Detector passes a track survives unmatched before it is dropped.
    min_hits:
        Detections required before a track is surfaced as confirmed.
    ttl_s:
        Wall-clock limit on how long a track may go unseen, so expiry does not
        depend on how fast the display happens to be running.
    velocity_smoothing:
        EMA weight for a newly measured velocity (0 = ignore, 1 = trust it).
    size_gate_ratio:
        Largest box-height ratio between a track and a detection that may be
        associated at all. Stops an identity being handed to a much bigger or
        smaller object that happens to overlap.
    reacquire_speed:
        Screen speed in px/s a target is assumed capable of while a track still
        has no velocity estimate. Only used to size the first gate (see
        :meth:`update`); once velocity is known, prediction does the work.
    max_reacquire_ratio:
        Hard ceiling on that first gate, as a fraction of the frame diagonal.
    """

    def __init__(
        self,
        iou_threshold=0.25,
        centroid_ratio=0.06,
        max_missed=15,
        min_hits=2,
        frame_shape=(480, 640),
        ttl_s=0.6,
        velocity_smoothing=0.5,
        size_gate_ratio=2.5,
        reacquire_speed=1600.0,
        max_reacquire_ratio=0.35,
    ):
        self.iou_threshold = iou_threshold
        self.centroid_ratio = centroid_ratio
        self.max_missed = max_missed
        self.min_hits = min_hits
        self.frame_shape = frame_shape
        self.ttl_s = ttl_s
        self.velocity_smoothing = velocity_smoothing
        self.size_gate_ratio = size_gate_ratio
        self.reacquire_speed = float(reacquire_speed)
        self.max_reacquire_ratio = float(max_reacquire_ratio)

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

    # ------------------------------------------------------------- prediction --
    def predict(self, now=None):
        """Advance every track along its velocity. No hit/miss bookkeeping.

        Called on frames where no new detector result arrived, so boxes keep
        moving smoothly (and stay matchable) instead of freezing until the next
        result lands.
        """
        now = time.monotonic() if now is None else now
        for track in self._tracks:
            dt = now - track.step_at
            track.step_at = now
            if dt > 0.0:
                track.advance(dt)
        return [t for t in self._tracks if t.confirmed]

    # ------------------------------------------------------------------ update --
    def update(self, detections, now=None):
        """Advance the tracker with a fresh detector result; returns confirmed tracks."""
        now = time.monotonic() if now is None else now
        h, w = self.frame_shape[:2]
        diag = float(np.hypot(w, h)) or 1.0
        frame_gate = self.centroid_ratio * diag

        # Move every track to `now` first, so association compares the new
        # detections against where each target is expected to be.
        for track in self._tracks:
            dt = now - track.step_at
            track.step_at = now
            if dt > 0.0:
                track.advance(dt)

        pairs = []
        for ti, track in enumerate(self._tracks):
            tbox = track.box
            # A frame-relative gate is far too loose for a small, distant
            # target: cap it against the track's own height so the fallback
            # can only reach about one body-length.
            gate = min(frame_gate, self.size_gate_ratio * max(track.height, 1.0))
            if not track.has_velocity:
                # This track has never been matched, so there is no velocity to
                # predict with and the box is simply where it was last seen.
                # With a tight gate a fast target can never be re-acquired: it
                # is re-issued a new id on every detector pass and so never
                # reaches min_hits to confirm. Widen the gate to cover the
                # ground it could plausibly have covered since — but only for
                # this as-yet-unmatched case, so an established track keeps the
                # tight gate that protects it through crossings.
                gate = max(
                    gate,
                    min(
                        self.reacquire_speed * (now - track.seen_at),
                        self.max_reacquire_ratio * diag,
                    ),
                )
            for di, det in enumerate(detections):
                if not self._size_compatible(track, det):
                    continue
                dbox = det.box
                overlap = iou(tbox, dbox)
                dist = centroid_distance(tbox, dbox)
                if overlap >= self.iou_threshold:
                    pairs.append((overlap, -dist, ti, di))
                elif dist <= gate:
                    pairs.append((0.0, -dist, ti, di))

        pairs.sort(reverse=True)
        used_tracks, used_dets = set(), set()
        for _overlap, _negdist, ti, di in pairs:
            if ti in used_tracks or di in used_dets:
                continue
            used_tracks.add(ti)
            used_dets.add(di)
            self._attach(self._tracks[ti], detections[di], now)

        # Only a detector pass that failed to match counts as a miss — this is
        # what stops a track being confirmed (or kept alive) by a stale result.
        for ti, track in enumerate(self._tracks):
            if ti in used_tracks:
                continue
            track.age += 1
            track.missed += 1
            # Bleed off velocity so a lost target coasts to a stop rather than
            # sliding off at its last known speed.
            track.vx *= 0.8
            track.vy *= 0.8

        for di, det in enumerate(detections):
            if di not in used_dets:
                self._spawn(det, now)

        self._tracks = [
            t
            for t in self._tracks
            if t.missed <= self.max_missed and (now - t.seen_at) <= self.ttl_s
        ]
        return [t for t in self._tracks if t.confirmed]

    def _size_compatible(self, track, det):
        """Reject wild box-size changes — the same person never doubles in size."""
        th = max(track.height, 1.0)
        dh = max(det.height, 1.0)
        ratio = dh / th
        return (1.0 / self.size_gate_ratio) <= ratio <= self.size_gate_ratio

    def _attach(self, track, det, now):
        dt = now - track.seen_at
        new_cx, new_cy = det.center
        if dt > 1e-3:
            # Measured against the last *observed* centre, not the coasting box.
            mvx = (new_cx - track.last_cx) / dt
            mvy = (new_cy - track.last_cy) / dt
            speed = float(np.hypot(mvx, mvy))
            if speed > MAX_SPEED_PX_S:
                scale = MAX_SPEED_PX_S / speed
                mvx, mvy = mvx * scale, mvy * scale
            if track.has_velocity:
                a = self.velocity_smoothing
                track.vx = (1.0 - a) * track.vx + a * mvx
                track.vy = (1.0 - a) * track.vy + a * mvy
            else:
                # First measurement: there is no prior to smooth against, and
                # easing in from zero would under-predict the next position
                # enough to fall outside the association gate.
                track.vx, track.vy = mvx, mvy
                track.has_velocity = True

        track.last_cx, track.last_cy = new_cx, new_cy
        track.box = det.box
        track.score = det.score
        track.source = det.source
        track.label = det.label
        track.age += 1
        track.hits += 1
        track.missed = 0
        track.seen_at = now
        track.step_at = now
        if track.hits >= self.min_hits:
            track.confirmed = True

    def _spawn(self, det, now):
        cx, cy = det.center
        self._tracks.append(
            Track(
                track_id=self._next_id,
                box=det.box,
                score=det.score,
                source=det.source,
                label=det.label,
                confirmed=self.min_hits <= 1,
                seen_at=now,
                step_at=now,
                last_cx=cx,
                last_cy=cy,
            )
        )
        self._next_id += 1

    def snapshot(self):
        """Track state for the operator-facing table, ordered by ID."""
        return sorted(self.tracks, key=lambda t: t.track_id)
