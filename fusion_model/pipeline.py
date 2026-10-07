"""RGB + thermal fusion inference pipeline (isolated package copy).

    visible + infrared  ->  MulFS-CAP fusion  ->  QS-YOLOv8  ->  person boxes

The two models are trained separately; this class chains them at inference time
and reproduces exactly the preprocessing used to build the training data:

  * both images -> grayscale (PIL "L") -> resized preserving aspect ratio so the
    long side is ``max_side`` (default 640), rounded to a multiple of 16
  * MulFS-CAP fusion at that size (vectorised kernels, numerically identical to
    the official code); single pass, no sliding windows / SAHI
  * fused image -> 8-bit -> JPEG q95 round-trip (the detector was trained on
    JPEGs; disable with ``jpeg_roundtrip=False``)
  * QS-YOLOv8 (trained on fused images) at imgsz 640
  * boxes are mapped back to the ORIGINAL image resolution

Unlike the original standalone ``pipeline.py`` this copy

  * imports its modules as package-relative paths (no ``sys.path`` mutation,
    so it cannot shadow the RGB ``model/qs_yolov8.py`` module), and
  * loads the heavy networks lazily on the first call, and
  * accepts ``numpy`` BGR frames directly (as well as PIL images / paths), so
    the console can hand it live camera frames.

Standalone command line (run from the project root):

    python -m fusion_model.pipeline --vis vis/ --ir ir/ --out results/
"""
import argparse
import io
import json
import os
import time
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
_CKPT_KEYS = [
    "bfe", "vis_mfe", "ir_mfe", "pafe", "mn_vis", "mn_ir",
    "vis_dgfp", "ir_dgfp", "mhcsab_vis", "mhcsab_ir", "fusion_block",
]


class FusionDetector:
    """RGB + thermal -> MulFS-CAP fused image -> QS-YOLOv8 person boxes."""

    def __init__(
        self,
        weights_dir=HERE / "weights",
        mulfs_ckpt=None,
        det_weights=None,
        size=None,
        max_side=640,
        imgsz=640,
        conf=0.25,
        iou=0.7,
        device=None,
        jpeg_roundtrip=True,
        window_chunk=128,
    ):
        # ``size`` pins the fusion canvas exactly; otherwise the frames are
        # scaled (aspect preserved) so the long side is ``max_side``, rounded to
        # a multiple of 16. No sliding windows / SAHI anywhere in this pipeline.
        self._fixed = tuple(size) if size else None
        self.W, self.H = self._fixed if self._fixed else (0, 0)
        if self._fixed:
            assert self.W % 16 == 0 and self.H % 16 == 0, "fusion size must be divisible by 16"
        self.max_side = int(max_side)
        self.imgsz, self.conf, self.iou, self.jpeg = imgsz, conf, iou, jpeg_roundtrip
        self.device = device  # resolved on the first call if None
        self.window_chunk = int(window_chunk)

        weights_dir = Path(weights_dir) if weights_dir else HERE / "weights"
        self.mulfs_ckpt = Path(mulfs_ckpt) if mulfs_ckpt else weights_dir / "mulfs_cap_llvip_e20.pth"
        self.det_weights = Path(det_weights) if det_weights else weights_dir / "qsyolov8_llvip_fused.pt"

        self._loaded = False
        self.M = None
        self.f = None
        self.det = None

    # ------------------------------------------------------------------ loading
    def available(self):
        """True when the runtime dependencies and both weight files are present."""
        for path in (self.mulfs_ckpt, self.det_weights):
            if not path.is_file():
                return False
        import importlib.util

        for mod in ("PIL", "torch", "ultralytics"):
            if importlib.util.find_spec(mod) is None:
                return False
        return True

    def _ensure_loaded(self):
        if self._loaded:
            return
        import torch

        from .mulfs_cap import mulfs_fast
        from .mulfs_cap.model import model as M

        # Importing this module attaches CDB / C2f_CDB / VSFFM to __main__ so the
        # fused checkpoint can be unpickled (side effect only).
        import importlib

        importlib.import_module(f"{__package__}.qs_yolov8.qs_yolov8_modules")

        if self.device is None:
            self.device = "cuda:0" if torch.cuda.is_available() else "cpu"
        dev = torch.device(self.device)
        os.environ.setdefault("MULFS_FAST_CHUNK", str(self.window_chunk))

        mulfs_fast.install(M)
        ck = torch.load(str(self.mulfs_ckpt), map_location=dev, weights_only=False)
        self.f = dict(
            bfe=M.base(), vis_mfe=M.FeatureExtractor(), ir_mfe=M.FeatureExtractor(),
            pafe=M.FeatureExtractor(), mn_vis=M.Enhance(), mn_ir=M.Enhance(),
            vis_dgfp=M.DictionaryRepresentationModule(), ir_dgfp=M.DictionaryRepresentationModule(),
            mhcsab_vis=M.MHCSAB(), mhcsab_ir=M.MHCSAB(), fusion_block=M.FusionMoudle(),
        )
        for k in _CKPT_KEYS:
            self.f[k].load_state_dict({kk.replace("module.", ""): v for kk, v in ck[k].items()})
            self.f[k].to(dev).eval()
        self.M = M

        from ultralytics import YOLO

        self.det = YOLO(str(self.det_weights))
        self._loaded = True

    # ------------------------------------------------------------------ helpers
    @staticmethod
    def _image_size(img):
        """(width, height) of a path / PIL image / numpy frame without resizing."""
        from PIL import Image

        if isinstance(img, Image.Image):
            return img.size
        if isinstance(img, np.ndarray):
            h, w = img.shape[:2]
            return w, h
        with Image.open(img) as im:
            return im.size

    def _target_size(self, w, h):
        """Fusion canvas: aspect preserved, long side ``max_side``, multiple of 16."""
        if self._fixed:
            return self._fixed
        long_side = max(int(w), int(h))
        if long_side <= 0 or self.max_side <= 0:
            return 640, 512
        scale = self.max_side / float(long_side)
        tw = max(16, int(int(w) * scale / 16.0 + 0.5) * 16)
        th = max(16, int(int(h) * scale / 16.0 + 0.5) * 16)
        return tw, th

    def _load_gray(self, img, size):
        from PIL import Image

        if isinstance(img, Image.Image):
            im = img
        elif isinstance(img, np.ndarray):
            if img.ndim == 3:
                im = Image.fromarray(np.ascontiguousarray(img[:, :, ::-1]))  # BGR -> RGB
            else:
                im = Image.fromarray(img)
        else:
            im = Image.open(img)
        orig = im.size
        g = im.convert("L").resize(size, Image.BILINEAR)
        import torch

        t = torch.from_numpy(np.asarray(g, dtype=np.float32) / 255.0)[None, None].to(self.device)
        return t, orig

    def _sync(self):
        if self.device == "cpu" or (hasattr(self.device, "type") and self.device.type == "cpu"):
            return
        try:
            import torch

            if torch.cuda.is_available():
                torch.cuda.synchronize()
        except Exception:
            pass

    @property
    def _torch_device(self):
        import torch

        if self.device is None:
            self.device = "cuda:0" if torch.cuda.is_available() else "cpu"
        return torch.device(self.device)

    # -------------------------------------------------------------------- stages
    def fuse(self, vis, ir):
        """Fuse a registered (vis, ir) pair; returns (uint8 HxW, original size, ms)."""
        import torch

        self._ensure_loaded()
        # The fusion canvas follows the visible frame's aspect ratio, capped to
        # ``max_side`` on the long edge (unless an explicit ``size`` was given).
        w0, h0 = self._image_size(vis)
        self.W, self.H = self._target_size(w0, h0)
        v, orig = self._load_gray(vis, (self.W, self.H))
        r, orig_ir = self._load_gray(ir, (self.W, self.H))
        if orig != orig_ir:
            print(f"warning: visible {orig} and infrared {orig_ir} sizes differ; "
                  f"both resized to {self.W}x{self.H}")
        M, f = self.M, self.f
        self._sync()
        t0 = time.time()
        with torch.no_grad():
            v1, i1 = f["bfe"](v), f["bfe"](r)
            vfe, ife = f["vis_mfe"](v1), f["ir_mfe"](i1)
            vf, irf = f["pafe"](v1), f["pafe"](i1)
            vdp, _ = f["vis_dgfp"](f["mn_vis"](vf))
            idp, _ = f["ir_dgfp"](f["mn_ir"](irf))
            corr = M.CMAP(M.window_partition(vdp, 16, 16), M.df_window_partition(idp, 26, 16),
                          f["mhcsab_vis"], f["mhcsab_ir"], True)
            fused = f["fusion_block"](vfe, M.feature_reorganization(corr, ife)).clamp(0, 1)
        self._sync()
        ms = (time.time() - t0) * 1000
        return (fused[0, 0].cpu().numpy() * 255).round().astype(np.uint8), orig, ms

    def detect(self, fused_u8):
        """Detect on a fused HxW uint8 image; returns (Nx5 [x1,y1,x2,y2,conf], ms)."""
        from PIL import Image

        self._ensure_loaded()
        img = fused_u8
        if self.jpeg:  # same JPEG round-trip as the training images
            buf = io.BytesIO()
            Image.fromarray(img).save(buf, format="JPEG", quality=95)
            img = np.asarray(Image.open(io.BytesIO(buf.getvalue())).convert("L"))
        bgr = np.repeat(img[..., None], 3, axis=2)  # grayscale -> 3 channels, as cv2.imread does
        self._sync()
        t0 = time.time()
        res = self.det.predict(bgr, imgsz=self.imgsz, conf=self.conf, iou=self.iou,
                               device=self._torch_device, verbose=False)[0]
        self._sync()
        ms = (time.time() - t0) * 1000
        b = res.boxes
        out = (np.concatenate([b.xyxy.cpu().numpy(), b.conf.cpu().numpy()[:, None]], 1)
               if len(b) else np.zeros((0, 5)))
        return out, ms

    def __call__(self, vis, ir):
        fused, (w0, h0), ms_f = self.fuse(vis, ir)
        boxes, ms_d = self.detect(fused)
        sx, sy = w0 / self.W, h0 / self.H
        boxes_orig = boxes.copy()
        boxes_orig[:, [0, 2]] *= sx
        boxes_orig[:, [1, 3]] *= sy
        return dict(
            boxes=boxes_orig.round(2),
            boxes_fused_res=boxes.round(2),
            fused=fused,
            orig_size=(w0, h0),
            fusion_ms=round(ms_f, 2),
            detect_ms=round(ms_d, 2),
            total_ms=round(ms_f + ms_d, 2),
        )

    # ------------------------------------------------------------------- cleanup
    def close(self):
        """Release the fusion + detector networks and any CUDA memory."""
        self.f = None
        self.det = None
        self.M = None
        self._loaded = False
        try:
            import gc

            import torch

            gc.collect()
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
                torch.cuda.synchronize()
        except Exception:
            pass


# ---------------------------------------------------------------------- CLI ---
def draw(vis_path, boxes, out_path):
    from PIL import Image, ImageDraw

    im = Image.open(vis_path).convert("RGB")
    d = ImageDraw.Draw(im)
    for x1, y1, x2, y2, c in boxes:
        d.rectangle([x1, y1, x2, y2], outline=(0, 255, 0), width=3)
        d.text((x1 + 3, y1 + 2), f"person {c:.2f}", fill=(0, 255, 0))
    im.save(out_path)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--vis", required=True, help="visible image or folder")
    ap.add_argument("--ir", required=True, help="infrared image or folder (same file names)")
    ap.add_argument("--out", default="results")
    ap.add_argument("--weights-dir", default=str(HERE / "weights"))
    ap.add_argument("--mulfs-ckpt", default=None)
    ap.add_argument("--det", default=None)
    ap.add_argument("--max-side", type=int, default=640,
                    help="fusion long-side cap, aspect preserved (default 640)")
    ap.add_argument("--width", type=int, default=None,
                    help="force an exact fusion width (with --height); overrides --max-side")
    ap.add_argument("--height", type=int, default=None,
                    help="force an exact fusion height (with --width)")
    ap.add_argument("--imgsz", type=int, default=640)
    ap.add_argument("--conf", type=float, default=0.25)
    ap.add_argument("--device", default=None, help="e.g. cuda:0 or cpu")
    ap.add_argument("--no-jpeg", action="store_true", help="skip the JPEG round-trip before detection")
    ap.add_argument("--save-fused", action="store_true", help="also save the fused images")
    a = ap.parse_args(argv)

    fixed = (a.width, a.height) if a.width and a.height else None
    fd = FusionDetector(a.weights_dir, a.mulfs_ckpt, a.det, size=fixed, max_side=a.max_side,
                        imgsz=a.imgsz, conf=a.conf, device=a.device,
                        jpeg_roundtrip=not a.no_jpeg)
    vis, ir, out = Path(a.vis), Path(a.ir), Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    pairs = ([(p, ir / p.name) for p in sorted(vis.iterdir())
              if p.suffix.lower() in (".jpg", ".jpeg", ".png", ".bmp")]
             if vis.is_dir() else [(vis, ir)])
    missing = [str(r) for _, r in pairs if not r.exists()]
    assert not missing, f"{len(missing)} infrared images missing, e.g. {missing[:3]}"

    fd(*pairs[0])  # warm-up (CUDA init, kernel selection)
    all_res, t_f, t_d = {}, [], []
    for i, (v, r) in enumerate(pairs):
        res = fd(v, r)
        t_f.append(res["fusion_ms"])
        t_d.append(res["detect_ms"])
        all_res[v.name] = dict(boxes_xyxy_conf=res["boxes"].tolist(),
                               fusion_ms=res["fusion_ms"], detect_ms=res["detect_ms"])
        draw(v, res["boxes"], out / f"{v.stem}_det.jpg")
        if a.save_fused:
            from PIL import Image

            Image.fromarray(res["fused"]).save(out / f"{v.stem}_fused.png")
        if (i + 1) % 100 == 0 or i + 1 == len(pairs):
            print(f"{i + 1}/{len(pairs)}  fusion {np.mean(t_f):.1f} ms  "
                  f"detection {np.mean(t_d):.1f} ms  -> {1000 / (np.mean(t_f) + np.mean(t_d)):.1f} FPS")
    (out / "detections.json").write_text(json.dumps(all_res, indent=1))
    print(f"saved {len(pairs)} annotated images + detections.json to {out}")


if __name__ == "__main__":
    main()
