"""OpenVINO-backed person detector for the CPU and Intel iGPU compute options.

Loads the prebuilt OpenVINO IR models that ship in ``model/openvino/`` and runs
single-pass inference on the OpenVINO ``CPU`` or ``GPU`` (Intel iGPU) device.
No sliding windows / SAHI here — the Compute selector only swaps the execution
backend, not the detector geometry.

Two output layouts are handled:

* patched QS-YOLOv8 IR — ``(5, N)`` = ``cx, cy, w, h, score`` (already decoded);
* YOLO26 end-to-end IR — ``(N, 6)`` = ``x1, y1, x2, y2, score, class`` (NMS
  already applied inside the graph).

Boxes come back in the letterboxed model space and are mapped to the original
frame, then filtered to the person class.
"""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

from .detect import BaseDetector, register
from .tracker import Detection

MODEL_DIR = Path(__file__).resolve().parent.parent / "model" / "openvino"

#: Person class index in the COCO-style label sets the IR models were exported
#: with (the custom QS person model is single-class, so 0 either way).
PERSON_CLASS = 0

#: Named IR models available to the Compute selector.
MODELS = {
    "qs_person_int8": MODEL_DIR / "qs_person_int8.xml",
    "yolo26n_e2e": MODEL_DIR / "yolo26n_e2e.xml",
}


class OpenVINODetector(BaseDetector):
    key = "openvino"
    label = "OpenVINO"
    description = "OpenVINO INT8 person detection (CPU / Intel iGPU)"
    requires_model = True
    default_source = "rgb"
    runs_off_thread = True

    def __init__(self, conf=0.65, model="qs_person_int8", device="CPU", iou=0.45,
                 imgsz=None, source=None, **_ignored):
        super().__init__(conf, source)
        self.model_name = model
        self.model_path = Path(MODELS.get(model, model))
        self.device = str(device).upper()  # "CPU" or "GPU"
        self.iou = float(iou)
        self.imgsz = int(imgsz) if imgsz else None
        self.loaded_weights = self.model_path.name
        self.description = (
            f"OpenVINO {self.model_path.stem} on {self.device} (INT8, single-pass)"
        )
        self._compiled = None
        self._out = None

    # ------------------------------------------------------------- contract ---
    def available(self):
        try:
            import openvino  # noqa: F401
        except Exception:
            return False
        return self.model_path.is_file()

    def _ensure_model(self):
        if self._compiled is not None:
            return self._compiled
        import openvino as ov

        core = ov.Core()
        model = core.read_model(str(self.model_path))
        if self.imgsz is None:
            self.imgsz = int(model.input(0).shape[2])
        self._compiled = core.compile_model(model, self.device)
        self._out = self._compiled.output(0)
        return self._compiled

    # ---------------------------------------------------------------- helpers ---
    @staticmethod
    def _letterbox(im, size, color=114):
        h, w = im.shape[:2]
        r = min(size / h, size / w)
        nw, nh = int(round(w * r)), int(round(h * r))
        canvas = np.full((size, size, 3), color, np.uint8)
        dw, dh = (size - nw) // 2, (size - nh) // 2
        canvas[dh:dh + nh, dw:dw + nw] = cv2.resize(im, (nw, nh), interpolation=cv2.INTER_LINEAR)
        return canvas, r, dw, dh

    @staticmethod
    def _nms(boxes, scores, thr):
        if len(boxes) <= 1:
            return list(range(len(boxes)))
        order = scores.argsort()[::-1]
        keep = []
        while order.size:
            i = order[0]
            keep.append(int(i))
            if order.size == 1:
                break
            rest = order[1:]
            x1 = np.maximum(boxes[i, 0], boxes[rest, 0])
            y1 = np.maximum(boxes[i, 1], boxes[rest, 1])
            x2 = np.minimum(boxes[i, 2], boxes[rest, 2])
            y2 = np.minimum(boxes[i, 3], boxes[rest, 3])
            inter = np.clip(x2 - x1, 0, None) * np.clip(y2 - y1, 0, None)
            a = (boxes[i, 2] - boxes[i, 0]) * (boxes[i, 3] - boxes[i, 1])
            b = (boxes[rest, 2] - boxes[rest, 0]) * (boxes[rest, 3] - boxes[rest, 1])
            order = rest[inter / (a + b - inter + 1e-9) <= thr]
        return keep

    def _decode(self, out, r, dw, dh, w0, h0):
        """(boxes Nx4 xyxy in frame pixels, scores N) for persons above ``conf``."""
        arr = np.asarray(out)
        if arr.ndim == 3:
            arr = arr[0]

        if arr.ndim == 2 and arr.shape[1] == 6:
            # YOLO26 end-to-end: x1, y1, x2, y2, score, class (letterbox pixels).
            keep = arr[(arr[:, 4] > self.conf) & (arr[:, 5].astype(int) == PERSON_CLASS)]
            if keep.size == 0:
                return np.zeros((0, 4)), np.zeros((0,))
            boxes = keep[:, :4].astype(np.float32)
            scores = keep[:, 4].astype(np.float32)
            boxes[:, [0, 2]] = (boxes[:, [0, 2]] - dw) / r
            boxes[:, [1, 3]] = (boxes[:, [1, 3]] - dh) / r
        else:
            # Patched QS-YOLOv8: cx, cy, w, h, score (letterbox pixels).
            p = arr if arr.shape[0] == 5 else arr.T
            scores_all = p[4]
            m = scores_all > self.conf
            if not np.any(m):
                return np.zeros((0, 4)), np.zeros((0,))
            cx, cy, bw, bh = p[0][m], p[1][m], p[2][m], p[3][m]
            scores = scores_all[m].astype(np.float32)
            boxes = np.stack(
                [(cx - bw / 2 - dw) / r, (cy - bh / 2 - dh) / r,
                 (cx + bw / 2 - dw) / r, (cy + bh / 2 - dh) / r], 1
            ).astype(np.float32)

        boxes[:, [0, 2]] = boxes[:, [0, 2]].clip(0, w0)
        boxes[:, [1, 3]] = boxes[:, [1, 3]].clip(0, h0)
        keep = self._nms(boxes, scores, self.iou)
        return boxes[keep], scores[keep]

    def infer(self, frame_bgr):
        comp = self._ensure_model()
        h0, w0 = frame_bgr.shape[:2]
        lb, r, dw, dh = self._letterbox(frame_bgr, self.imgsz)
        x = cv2.cvtColor(lb, cv2.COLOR_BGR2RGB).transpose(2, 0, 1)[None].astype(np.float32) / 255.0
        out = comp([x])[self._out]
        boxes, scores = self._decode(out, r, dw, dh, w0, h0)
        return [
            Detection(float(x1), float(y1), float(x2), float(y2), score=float(s),
                      source=self.source, label="person")
            for (x1, y1, x2, y2), s in zip(boxes, scores)
        ]

    def close(self):
        self._compiled = None
        self._out = None


register(OpenVINODetector)
