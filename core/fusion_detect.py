"""RGB + thermal fusion detector adapter for the fusion tab.

Wraps :class:`fusion_model.pipeline.FusionDetector` (learned infrared fusion +
fused-image person detector) behind the console's detector contract.

The learned fusion costs a few hundred milliseconds per pair, far slower than
the render loop, so inference runs on a private worker thread: :meth:`infer`
hands the worker the latest RGB frame plus the thermal frame the tab supplied
via :meth:`set_thermal`, and immediately returns the most recent detections.
The tracker coasts between results exactly as it does when a detector is rate
limited. This keeps the Qt event loop responsive regardless of fusion speed.

The heavy network is imported/loaded lazily, on the worker's first job, so
selecting this model in the dropdown never blocks the UI either.
"""

from __future__ import annotations

import threading
import time

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

    def __init__(self, conf=0.25, device=None, weights_dir=None, size=(640, 512),
                 imgsz=640, iou=0.7, **_ignored):
        super().__init__(conf, source="fusion")
        self.device = device
        self._weights_dir = weights_dir
        self._size = tuple(size)
        self._imgsz = int(imgsz)
        self._iou = float(iou)

        self._fd = None
        self._thermal = None
        self._dets: list[Detection] = []
        self._lock = threading.Lock()
        self._event = threading.Event()
        self._running = False
        self._thread = None
        self._pending = None
        self.inference_ms = 0.0
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
        """Queue (RGB, thermal) for the worker and return the latest results."""
        if frame_bgr is None:
            return list(self._dets)
        self._ensure_worker()
        with self._lock:
            self._pending = (frame_bgr, self._thermal)
            dets = list(self._dets)
        self._event.set()
        return dets

    def close(self):
        self._running = False
        self._event.set()
        thread, self._thread = self._thread, None
        if thread is not None:
            thread.join(timeout=3.0)
        fd, self._fd = self._fd, None
        if fd is not None:
            try:
                fd.close()
            except Exception:
                pass

    # ---------------------------------------------------------------- worker ---
    def _ensure_worker(self):
        if self._thread is not None and self._thread.is_alive():
            return
        self._running = True
        self._thread = threading.Thread(target=self._loop, name="fusion-detect", daemon=True)
        self._thread.start()

    def _loop(self):
        while self._running:
            self._event.wait(timeout=0.25)
            self._event.clear()
            if not self._running:
                break
            with self._lock:
                job = self._pending
                self._pending = None
            if job is None:
                continue
            rgb, thermal = job
            if rgb is None or thermal is None:
                with self._lock:
                    self._dets = []
                continue
            try:
                fd = self._ensure_pipeline()
                fd.conf = self.conf  # pick up slider changes without a rebuild
                t0 = time.perf_counter()
                res = fd(rgb, thermal)
                ms = (time.perf_counter() - t0) * 1000.0
                source = self.source
                dets = [
                    Detection(x1, y1, x2, y2, score=float(score), source=source, label="person")
                    for x1, y1, x2, y2, score in res["boxes"]
                ]
                with self._lock:
                    self._dets = dets
                    self.inference_ms = ms
                    self.last_error = ""
            except Exception as exc:  # a model failure must not kill the feed
                with self._lock:
                    self._dets = []
                    self.inference_ms = 0.0
                    self.last_error = str(exc)

    def _ensure_pipeline(self):
        if self._fd is not None:
            return self._fd
        from fusion_model.pipeline import FusionDetector

        self._fd = FusionDetector(
            weights_dir=self._weights_dir,
            size=self._size,
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
