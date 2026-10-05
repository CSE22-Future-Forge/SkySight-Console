"""Detector abstraction.

The console never imports a model directly — it asks the registry for a
detector by key. The registry ships a single detector:

* ``ultralytics`` — person detection running on CUDA when a GPU is present.
  It loads the packaged YOLO26 weights by default, or a custom ``.pt`` model
  (with its custom layers registered dynamically) when one is supplied. The
  model is loaded lazily on the first inference.

* ``fusion`` — the isolated RGB + thermal learned-fusion pipeline
  (infrared fusion + fused-image person detector); see
  :mod:`core.fusion_detect` and the ``fusion_model/`` package.
"""

from __future__ import annotations

import math
import sys
import threading
import time
from pathlib import Path

import cv2
import numpy as np

from .tracker import Detection

PERSON_CLASSES = {0}
PERSON_LABEL = "person"

#: Project-root ``model`` folder holding the custom QS-YOLOv8s detector.
MODEL_DIR = Path(__file__).resolve().parent.parent / "model"
CUSTOM_PERSON_WEIGHTS = MODEL_DIR / "qs_yolov8s_person.pt"


class BaseDetector:
    """Contract every detector must satisfy."""

    key = "base"
    label = "Base"
    description = ""
    requires_model = False
    default_source = "rgb"
    #: True when :meth:`infer` is slow enough that running it on a worker
    #: thread is worth it (see :class:`DetectionPipeline`). Detectors that are
    #: effectively instant — and the test stubs — leave this False and are
    #: called inline, so their results are available on the same frame.
    runs_off_thread = False

    def __init__(self, conf=0.65, source=None, **_ignored):
        self.conf = float(conf)
        self.source = source or self.default_source

    def infer(self, frame_bgr):
        """Return a list of :class:`~core.tracker.Detection`."""
        raise NotImplementedError

    def close(self):
        pass


class UltralyticsDetector(BaseDetector):
    """Adapter for the Ultralytics detector — inert until installed.

    Intentionally lazy: neither torch nor ultralytics is imported at
    construction time, and the device is only resolved on the first
    :meth:`infer` call, so merely importing this module stays cheap.
    """

    key = "ultralytics"
    label = "Ultralytics"
    description = "YOLO26 person detection — uses CUDA when available"
    requires_model = True
    default_source = "fusion"
    runs_off_thread = True

    #: Checked in order; the first one present next to the app wins.
    WEIGHT_CANDIDATES = (
        "yolo26n.pt",
        "yolo26s.pt",
        "yolo26m.pt",
        "yolo26l.pt",
        "yolo26x.pt",
    )

    def __init__(
        self,
        conf=0.65,
        weights=None,
        device=None,
        source=None,
        imgsz=640,
        sliced=False,
        **_ignored,
    ):
        super().__init__(conf, source)
        self.weights = weights
        self.device = device
        self.imgsz = int(imgsz)
        # SAHI-style sliding-window inference: the frame is scaled so its
        # shorter side matches ``imgsz``, then covered by square ``imgsz``
        # windows that are fed to the model at native resolution.
        self.sliced = bool(sliced)
        self._model = None
        self.loaded_weights = None

    def available(self):
        try:
            import ultralytics  # noqa: F401
        except Exception:
            return False
        return True

    @staticmethod
    def _is_custom(weights):
        """True when the weights come from the bundled custom model folder."""
        p = Path(str(weights))
        return (p.resolve().parent == MODEL_DIR.resolve()) and (
            p.parent / "qs_yolov8.py"
        ).is_file()

    def _ensure_layers(self, path):
        """Dynamically register a custom model's layers before loading it.

        Custom checkpoints pickle their layer classes by module path, so the
        module that defines them (``qs_yolov8.py``, shipped next to the
        weights) has to be importable first. Importing it runs its
        :func:`register`, which teaches Ultralytics the custom layers.
        """
        if not self._is_custom(path):
            return
        model_dir = str(Path(str(path)).resolve().parent)
        if model_dir not in sys.path:
            sys.path.insert(0, model_dir)
        import importlib

        importlib.import_module("qs_yolov8")

    def _bundled_weights(self):
        """Resolve weights in MODEL_DIR or next to the app, else the packaged YOLO26 nano."""
        if self.weights:
            return self.weights
        root = Path(__file__).resolve().parent.parent
        for name in self.WEIGHT_CANDIDATES:
            in_model = MODEL_DIR / name
            if in_model.is_file():
                return str(in_model)
            local = root / name
            if local.is_file():
                return str(local)
        return str(MODEL_DIR / self.WEIGHT_CANDIDATES[0])

    def _ensure_model(self):
        if self._model is not None:
            return self._model
        import torch  # imported lazily on purpose
        from ultralytics import YOLO  # imported lazily on purpose

        # device=None would leave Ultralytics on CPU; pick the GPU when present.
        if self.device is None:
            self.device = 0 if torch.cuda.is_available() else "cpu"
        path = self._bundled_weights()
        self._ensure_layers(path)
        self._model = YOLO(path)
        self.loaded_weights = Path(path).name
        return self._model

    def _boxes_from_result(self, res, offset=(0, 0)):
        """Extract person detections from one Ultralytics result, with an offset."""
        dets = []
        boxes = getattr(res, "boxes", None)
        if boxes is None:
            return dets
        dx, dy = offset
        for xyxy, score, cls in zip(
            boxes.xyxy.tolist(), boxes.conf.tolist(), boxes.cls.tolist()
        ):
            if int(cls) not in PERSON_CLASSES:
                continue
            x1, y1, x2, y2 = xyxy
            dets.append(
                Detection(
                    x1 + dx,
                    y1 + dy,
                    x2 + dx,
                    y2 + dy,
                    score=float(score),
                    source=self.source,
                    label=PERSON_LABEL,
                )
            )
        return dets

    @staticmethod
    def _nms(dets, iou_thr=0.5):
        """Greedy IoU NMS, used to merge overlapping slice detections."""
        if len(dets) <= 1:
            return dets
        boxes = np.array([[d.x1, d.y1, d.x2, d.y2] for d in dets], dtype=float)
        scores = np.array([d.score for d in dets], dtype=float)
        order = scores.argsort()[::-1]
        keep = []
        while order.size > 0:
            i = int(order[0])
            keep.append(i)
            if order.size == 1:
                break
            rest = order[1:]
            xx1 = np.maximum(boxes[i, 0], boxes[rest, 0])
            yy1 = np.maximum(boxes[i, 1], boxes[rest, 1])
            xx2 = np.minimum(boxes[i, 2], boxes[rest, 2])
            yy2 = np.minimum(boxes[i, 3], boxes[rest, 3])
            inter = np.maximum(0.0, xx2 - xx1) * np.maximum(0.0, yy2 - yy1)
            area_i = (boxes[i, 2] - boxes[i, 0]) * (boxes[i, 3] - boxes[i, 1])
            area_r = (boxes[rest, 2] - boxes[rest, 0]) * (boxes[rest, 3] - boxes[rest, 1])
            iou = inter / (area_i + area_r - inter + 1e-9)
            order = rest[iou <= iou_thr]
        return [dets[i] for i in keep]

    @staticmethod
    def _scale_to_min_side(frame_bgr, min_side):
        """Scale so the shorter side equals ``min_side``; returns (image, scale)."""
        h, w = frame_bgr.shape[:2]
        short = min(h, w)
        if short <= 0 or short == min_side:
            return frame_bgr, 1.0
        scale = min_side / float(short)
        new_w = max(1, int(round(w * scale)))
        new_h = max(1, int(round(h * scale)))
        return cv2.resize(frame_bgr, (new_w, new_h), interpolation=cv2.INTER_LINEAR), scale

    @staticmethod
    def _sliding_starts(length, window):
        """Start offsets of square ``window`` windows covering ``length``.

        Two windows anchored at the two ends are enough whenever the long side
        is at most twice the window (a 16:9 frame after the min-side resize
        lands exactly there); wider frames get extra evenly spaced windows so
        the coverage never leaves a gap.
        """
        if length <= window:
            return [0]
        count = max(2, int(math.ceil(length / float(window))))
        span = length - window
        return [int(round(i * span / (count - 1))) for i in range(count)]

    def _infer_sliding(self, model, frame_bgr):
        """SAHI-style sliding-window inference, then NMS-merge the hits.

        The frame is first scaled so its shorter side is ``imgsz``. A square
        ``imgsz`` window (whose height covers the scaled short side exactly) is
        then slid along the long axis — two windows for a normal 16:9 source —
        and every window is fed to the model at native ``imgsz`` resolution, so
        small targets are never downscaled with the whole frame. Boxes are
        mapped back to original coordinates and de-duplicated with NMS.
        """
        h, w = frame_bgr.shape[:2]
        if h == 0 or w == 0:
            return []
        window = self.imgsz
        scaled, scale = self._scale_to_min_side(frame_bgr, window)
        sh, sw = scaled.shape[:2]
        horizontal = sw >= sh
        length = sw if horizontal else sh
        starts = self._sliding_starts(length, window)

        tiles, offsets = [], []
        for start in starts:
            if horizontal:
                x0, y0 = min(start, max(0, sw - window)), 0
            else:
                x0, y0 = 0, min(start, max(0, sh - window))
            crop = scaled[y0 : y0 + window, x0 : x0 + window]
            if crop.shape[0] < 2 or crop.shape[1] < 2:
                continue
            tiles.append(crop)
            offsets.append((x0, y0))
        if not tiles:
            return []

        results = model.predict(
            tiles,
            conf=self.conf,
            device=self.device,
            imgsz=self.imgsz,
            verbose=False,
        )
        inv = 1.0 / scale if scale else 1.0
        dets = []
        for res, (x0, y0) in zip(results, offsets):
            for d in self._boxes_from_result(res, (x0, y0)):
                dets.append(
                    Detection(
                        d.x1 * inv,
                        d.y1 * inv,
                        d.x2 * inv,
                        d.y2 * inv,
                        score=d.score,
                        source=self.source,
                        label=PERSON_LABEL,
                    )
                )
        return self._nms(dets)

    def infer(self, frame_bgr):
        model = self._ensure_model()
        if self.sliced:
            return self._infer_sliding(model, frame_bgr)
        results = model.predict(
            frame_bgr,
            conf=self.conf,
            device=self.device,
            imgsz=self.imgsz,
            verbose=False,
        )
        dets = []
        for res in results:
            dets.extend(self._boxes_from_result(res))
        return dets

    def close(self):
        """Called when the pipeline swaps this detector out; free its model."""
        self.free()

    def free(self):
        """Release CUDA memory held by the loaded model."""
        model, self._model = self._model, None
        self.loaded_weights = None
        if model is not None:
            # Ultralytics keeps a Predictor that also holds a model reference.
            try:
                model.predictor = None
            except Exception:
                pass
        try:
            import gc

            import torch

            gc.collect()
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
                torch.cuda.synchronize()
        except Exception:
            pass

    _source = "fusion"


_REGISTRY: dict[str, type[BaseDetector]] = {}


def register(detector_cls):
    """Add a detector to the registry (used by the future YOLO entry point)."""
    _REGISTRY[detector_cls.key] = detector_cls
    return detector_cls


def available_keys():
    return list(_REGISTRY)


def build(key, **kwargs):
    if key not in _REGISTRY:
        raise KeyError(f"unknown detector: {key}")
    return _REGISTRY[key](**kwargs)


for _cls in (UltralyticsDetector,):
    register(_cls)


class DetectionPipeline:
    """Runs a detector on a worker thread and hands results to the tracker.

    Inference is rate limited to ``max_fps`` and always runs on the *newest*
    frame; frames that arrive while a model is busy replace the queued one
    instead of piling up. The UI thread only ever collects finished results, so
    a slow model cannot stall rendering, and it never blocks the capture thread.

    The tracker is advanced on every call, but the two cases differ:

    * a new detector result arrived -> :meth:`Tracker.update`, which is the only
      thing that counts hits and misses;
    * nothing new -> :meth:`Tracker.predict`, which reports the tracks as they
      stand. Boxes are deliberately *not* extrapolated between results: a box
      only moves when a detection moves it.

    Re-feeding a stale result (what this pipeline used to do) inflated the hit
    count at display rate and let a track be confirmed without any fresh
    evidence, so that is deliberately avoided now.

    Detectors that are effectively instant (``runs_off_thread`` False) are still
    called inline, preserving the original synchronous behaviour for them.
    """

    def __init__(self, tracker, max_fps=20.0):
        self.tracker = tracker
        self.max_fps = max_fps
        self.detector = None
        self.inference_ms = 0.0
        self.last_error = ""
        self.detections: list[Detection] = []

        self._last_submit = 0.0
        #: Bumped once per completed detector result. ``step`` compares it to
        #: ``_consumed`` to tell a fresh result from a re-read of the old one.
        self._generation = 0
        self._consumed = -1
        #: Opaque caller value passed alongside a frame and handed back with
        #: the detections it produced. Callers use it to remember what else
        #: belongs to that frame — the tab keeps the full-resolution capture
        #: there so a crop is cut from the same moment its box was measured on.
        #: Detection trails the display by a frame or two, so without this a
        #: crop on a moving scene is taken from the wrong moment and the person
        #: sits outside their own box.
        self.result_context = None

        self._lock = threading.Lock()
        self._event = threading.Event()
        self._pending = None
        self._running = False
        self._thread = None

    def set_detector(self, detector):
        self._stop_worker()
        if self.detector is not None and self.detector is not detector:
            self.detector.close()
        self.detector = detector
        self._last_submit = 0.0
        self._generation = 0
        self._consumed = -1
        self.detections = []
        self.inference_ms = 0.0
        self.result_context = None

    # ---------------------------------------------------------------- worker ---
    def _ensure_worker(self):
        if self._thread is not None and self._thread.is_alive():
            return
        self._running = True
        self._thread = threading.Thread(
            target=self._loop, name="detect-pipeline", daemon=True
        )
        self._thread.start()

    def _stop_worker(self):
        self._running = False
        self._event.set()
        thread, self._thread = self._thread, None
        if thread is not None and thread.is_alive():
            thread.join(timeout=3.0)

    def _submit(self, frame_bgr, context=None):
        self._ensure_worker()
        with self._lock:
            # Latest frame wins: a queued frame the model has not started on is
            # worthless once a newer one exists.
            self._pending = (frame_bgr, context)
        self._event.set()

    def _loop(self):
        while self._running:
            self._event.wait(timeout=0.25)
            self._event.clear()
            if not self._running:
                break
            with self._lock:
                job, self._pending = self._pending, None
                detector = self.detector
            if job is None or detector is None:
                continue
            frame, context = job
            if frame is None:
                continue
            t0 = time.perf_counter()
            try:
                dets = detector.infer(frame)
            except Exception as exc:  # a model failure must not kill the feed
                with self._lock:
                    self.detections = []
                    self.inference_ms = 0.0
                    self.last_error = str(exc)
                    self._generation += 1
                continue
            ms = (time.perf_counter() - t0) * 1000.0
            with self._lock:
                self.detections = dets
                self.inference_ms = ms
                self.last_error = ""
                self.result_context = context
                self._generation += 1

    # ------------------------------------------------------------------ step ---
    def step(self, frame_bgr, now=None, context=None):
        """Advance detection + tracking by one frame.

        Never blocks on the model: work is submitted at most ``max_fps`` times a
        second and collected when the worker finishes it.

        ``context`` is opaque here. It travels with the frame to the worker and
        comes back on :attr:`result_context` with the detections it produced,
        which is how a caller ties a result to the frame it was measured on.
        """
        now = time.monotonic() if now is None else now
        if frame_bgr is None:
            return []

        h, w = frame_bgr.shape[:2]
        self.tracker.set_frame_shape((h, w))

        detector = self.detector
        if detector is not None and (now - self._last_submit) >= (
            1.0 / max(self.max_fps, 1.0)
        ):
            self._last_submit = now
            if getattr(detector, "runs_off_thread", False):
                self._submit(frame_bgr, context)
            else:
                # Effectively instant: run it here and use the result now.
                t0 = time.perf_counter()
                try:
                    self.detections = detector.infer(frame_bgr)
                except Exception as exc:
                    self.detections = []
                    self.inference_ms = 0.0
                    self.last_error = str(exc)
                else:
                    self.inference_ms = (time.perf_counter() - t0) * 1000.0
                self.result_context = context
                self._generation += 1

        with self._lock:
            generation = self._generation
            detections = self.detections
            inference_ms = self.inference_ms

        if generation != self._consumed:
            self._consumed = generation
            self.inference_ms = inference_ms
            return self.tracker.update(detections, now=now)
        return self.tracker.predict(now=now)

    def close(self):
        self._stop_worker()
        if self.detector is not None:
            self.detector.close()
            self.detector = None