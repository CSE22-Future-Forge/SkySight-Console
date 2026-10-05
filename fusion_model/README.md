# Isolated fusion pipeline (RGB + thermal → MulFS-CAP → QS-YOLOv8)

Self-contained copy of the learned fusion chain, kept apart from the
RGB-only `model/` folder so its modules and weights never mix with the RGB
QS-YOLOv8s detector.

| File | Role |
|---|---|
| `pipeline.py` | Runs the whole chain (`FusionDetector`); accepts NumPy BGR frames, PIL images or paths |
| `mulfs_cap/model/model.py` | MulFS-CAP architecture (official code, patched) |
| `mulfs_cap/mulfs_fast.py` | Vectorised MulFS-CAP kernels (same maths, much faster) |
| `qs_yolov8/qs_yolov8_modules.py` | QS-YOLOv8 custom layers (CDB, C2f_CDB, VSFFM) — needed to load the fused detector |
| `qs_yolov8/qs_yolov8_llvip.yaml` | Detector architecture (reference only; not needed for inference) |
| `weights/mulfs_cap_llvip_e20.pth` | Trained MulFS-CAP (LLVIP, 20 epochs) |
| `weights/qsyolov8_llvip_fused.pt` | Trained QS-YOLOv8 on fused images |

## In the console

`core/fusion_detect.py` exposes this as the `fusion` detector. It runs on a
background thread (the fusion costs a few hundred ms), so the RGB + thermal tab
stays responsive. Select **Fusion Model** in the tab's Model dropdown.

## Standalone

Run from the project root:

```bash
python -m fusion_model.pipeline --vis path/to/visible --ir path/to/infrared --out results/ --save-fused
```

`--vis` / `--ir` are single images or folders with matching file names. Output:
`*_det.jpg` (boxes on the visible image), `*_fused.png` (with `--save-fused`),
`detections.json` (boxes `[x1, y1, x2, y2, conf]` in original-image pixels plus
per-stage time).

```python
from fusion_model.pipeline import FusionDetector
fd = FusionDetector()                    # loads both models lazily
r = fd("visible.jpg", "infrared.jpg")    # or fd(rgb_ndarray, ir_ndarray)
# r["boxes"] (Nx5), r["fused"], r["total_ms"]
```

Checked on LLVIP test images with the trained weights: fused images are
pixel-identical to the detector's training images, and the detections match
running the detector on those images (box coordinates within 0.005 px).
