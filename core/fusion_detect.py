"""RGB + thermal fusion detector adapter for the fusion tab.

Wraps :class:`fusion_model.pipeline.FusionDetector` (learned infrared fusion +
fused-image person detector) behind the console's detector contract.

The learned fusion costs a few hundred milliseconds per pair — by far the
slowest backend here. It does *not* run its own thread: it declares
``runs_off_thread = True`` and lets :class:`~core.detect.DetectionPipeline` do
the threading, which keeps one place responsible for rate limiting, dropping
stale frames and hit/miss bookkeeping. :meth:`infer` is therefore a plain
blocking call and is only ever invoked from the pipeline's worker.

The heavy network is imported/loaded lazily, on the first inference, so
selecting this model in the dropdown never blocks the UI either.
"""

from __future__ import annotations

import threading

from .detect import BaseDetector, register
from .tracker import Detection


class FusionModelDetector(BaseDetector):
    key = "fusion"
    label = "Fusion Model"
    description = (
        "Combines the RGB and thermal views, then detects people "
        "(runs in the background)"
    )
    requires_model = True
    default_source = "fusion"
    runs_off_thread = True

    def __init__(self, conf=0.25, device=None, weights_dir=None, size=None,
                 max_side=640, imgsz=640, iou=0.7, **_ignored):
        super().__init__(conf, source="fusion")
        self.device = device
        self._weights_dir = weights_dir
        self._size = tuple(size) if size else None
        self._max_side = int(max_side)
        self._imgsz = int(imgsz)
        self._iou = float(iou)

        self._fd = None
        self._thermal = None
        #: Written by the UI thread via :meth:`set_thermal`, read by the
        #: pipeline's worker inside :meth:`infer`.
        self._lock = threading.Lock()
        self.last_error = ""

    # ------------------------------------------------------------- contract ---
    def available(self):
        try:
            from fusion_model.pipeline import FusionDetector
        except Exception:
            return False
        return self._probe_available(FusionDetector)

    @staticmethod
    def _probe_available(cls):
        try:
            fd = cls()
        except Exception:
            return False
        return fd.available()

    def set_thermal(self, thermal_bgr):
        """Supply the thermal frame that pairs with the next RGB frame."""
        with self._lock:
            self._thermal = thermal_bgr

    def infer(self, frame_bgr):
        """Fuse the queued (RGB, thermal) pair and detect on the result.

        Blocking and slow by design — the pipeline calls this on its worker.
        Returns the previous detections unchanged when either payload is
        missing, so a half-open sensor never wipes the tracker's state.
        """
        with self._lock:
            thermal = self._thermal
        if frame_bgr is None or thermal is None:
            return []
        fd = self._ensure_pipeline()
        fd.conf = self.conf  # pick up slider changes without a rebuild
        res = fd(frame_bgr, thermal)
        self.last_error = ""
        return [
            Detection(x1, y1, x2, y2, score=float(score), source=self.source,
                      label="person")
            for x1, y1, x2, y2, score in res["boxes"]
        ]

    def close(self):
        fd, self._fd = self._fd, None
        if fd is not None:
            try:
                fd.close()
            except Exception:
                pass

    def _ensure_pipeline(self):
        if self._fd is not None:
            return self._fd
        from fusion_model.pipeline import FusionDetector

        self._fd = FusionDetector(
            weights_dir=self._weights_dir,
            size=self._size,
            max_side=self._max_side,
            imgsz=self._imgsz,
            conf=self.conf,
            iou=self._iou,
            device=self.device,
        )
        return self._fd


# Self-register so `import core.fusion_detect` (or importing the `core`
# package) adds the `fusion` key to the detector registry. `core/__init__`
# imports this module after `detect` to avoid a circular import.
register(FusionModelDetector)
