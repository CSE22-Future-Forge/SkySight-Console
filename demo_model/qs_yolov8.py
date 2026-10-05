"""QS-YOLOv8s person detector -- layers and architecture only.

Weights: qs_yolov8s_person.pt (this folder). Trained on person-only VisDrone
(pedestrian + people merged into "person"), 640 px, 100 epochs, COCO-initialised;
VisDrone-person val (standalone model.val, imgsz 640): mAP50 0.627, mAP50-95 0.277,
P 0.734, R 0.552.
Source run: adverse_weather/ablation/runs/detect/stage00_baseline_clean_s0.

Requires: ultralytics (trained with 8.4.78) and torch.

Usage in your app:

    import qs_yolov8                      # registers the custom layers (must come first)
    from ultralytics import YOLO

    model = YOLO("qs_yolov8s_person.pt")
    results = model.predict("image.jpg", imgsz=640, conf=0.25)
    for box in results[0].boxes:          # class 0 = person
        print(box.xyxy.tolist(), float(box.conf))

Use imgsz=640 (the training size). Only qs_yolov8.py has to be importable
(same folder as your script, or on sys.path); nothing else from the training
code is needed.

build_model() rebuilds the same architecture from scratch (untrained), e.g. to
inspect it or to train it again.
"""

import inspect
import re
import sys

import torch
import torch.nn as nn
from ultralytics.nn.modules.conv import Conv

__all__ = ["CDB", "C2f_CDB", "VSFFM", "ARCH_YAML", "register", "build_model"]


# =====================================================================
# Custom layers (verbatim from stage00.Baseline.py; attribute names must not
# change or the saved weights will no longer load)
# =====================================================================

# 1. CDB: Channel-Decoupled Bottleneck
class CDB(nn.Module):
    """Channel-Decoupled Bottleneck with a 3:1 split for optimal detail preservation."""
    def __init__(self, c1, c2, shortcut=True, g=1, e=0.5):
        super().__init__()
        self.c_a = int(c1 * 0.75) # Detail preserving branch (75% of channels)
        self.c_b = c1 - self.c_a  # Semantic enhancing branch (25% of channels)

        # Deeper transformation on the semantic branch
        self.cv1 = Conv(self.c_b, self.c_b, 3, 1)
        self.cv2 = Conv(self.c_b, self.c_b, 3, 1)

        # Feature fusion
        self.cv3 = Conv(c1, c2, 1, 1)
        self.add = shortcut and c1 == c2

    def forward(self, x):
        x_a, x_b = torch.split(x, [self.c_a, self.c_b], dim=1)
        x_b = self.cv2(self.cv1(x_b))
        out = self.cv3(torch.cat((x_a, x_b), dim=1))
        return x + out if self.add else out

# 2. C2f_CDB: C2f Module utilizing the Channel-Decoupled Bottleneck
class C2f_CDB(nn.Module):
    """C2f module built with CDB instead of standard bottlenecks."""
    def __init__(self, c1, c2, n=1, shortcut=False, g=1, e=0.5):
        super().__init__()
        self.c = int(c2 * e)
        self.cv1 = Conv(c1, 2 * self.c, 1, 1)
        self.cv2 = Conv((2 + n) * self.c, c2, 1)
        self.m = nn.ModuleList(CDB(self.c, self.c, shortcut, g, e=1.0) for _ in range(n))

    def forward(self, x):
        y = list(self.cv1(x).chunk(2, 1))
        y.extend(m(y[-1]) for m in self.m)
        return self.cv2(torch.cat(y, 1))

# 3. VSFFM: Visual Serial Feature Fusion Module
class VSFFM(nn.Module):
    """Serial multi-scale depthwise convolutions (7x7 -> 5x5 -> 3x3)."""
    def __init__(self, c1, c2):
        super().__init__()
        # 7x7 Depthwise convolution with stride 2 for downsampling to P5/32
        self.dw1 = Conv(c1, c1, k=7, s=2, p=3, g=c1)
        self.pw1 = Conv(c1, c2, k=1, s=1)

        # 5x5 Depthwise convolution
        self.dw2 = Conv(c2, c2, k=5, s=1, p=2, g=c2)
        self.pw2 = Conv(c2, c2, k=1, s=1)

        # 3x3 Depthwise convolution
        self.dw3 = Conv(c2, c2, k=3, s=1, p=1, g=c2)

    def forward(self, x):
        x1 = self.pw1(self.dw1(x))
        x2 = self.pw2(self.dw2(x1))
        return self.dw3(x2)


# =====================================================================
# Architecture (QS-YOLOv8s, P2/P3/P4 detection head, 1 class: person)
# =====================================================================

ARCH_YAML = """
nc: 1
scales:
  s: [0.33, 0.50, 1024]

backbone:
  # [from, repeats, module, arguments]
  - [-1, 1, Conv, [64, 3, 2]]        # 0: P1/2
  - [-1, 1, Conv, [128, 3, 2]]       # 1: P2/4
  - [-1, 3, C2f, [128, True]]        # 2
  - [-1, 1, Conv, [256, 3, 2]]       # 3: P3/8
  - [-1, 6, C2f, [256, True]]        # 4
  - [-1, 1, Conv, [512, 3, 2]]       # 5: P4/16
  - [-1, 6, C2f_CDB, [512, True]]    # 6: Channel-Decoupled Bottleneck on P4
  - [-1, 1, VSFFM, [1024]]           # 7: P5/32 Serial Feature Fusion downsampling
  - [-1, 3, C2f_CDB, [1024, True]]   # 8: Channel-Decoupled Bottleneck on P5
  - [-1, 1, SPPF, [1024, 5]]         # 9
  - [-1, 1, Conv, [256, 1, 1]]       # 10: HFCC (Compresses redundant P5 semantic channels)

head:
  # Top-down FPN
  - [-1, 1, nn.Upsample, [None, 2, 'nearest']]   # 11
  - [[-1, 6], 1, Concat, [1]]        # 12: Cat with P4
  - [-1, 3, C2f, [512]]              # 13 (top-down P4)

  - [-1, 1, nn.Upsample, [None, 2, 'nearest']]   # 14
  - [[-1, 4], 1, Concat, [1]]        # 15: Cat with P3
  - [-1, 3, C2f, [256]]              # 16 (top-down P3)

  - [-1, 1, nn.Upsample, [None, 2, 'nearest']]   # 17
  - [[-1, 2], 1, Concat, [1]]        # 18: Cat with high-resolution P2
  - [-1, 3, C2f, [128]]              # 19 (P2 out)

  # Bottom-up PAN
  - [-1, 1, Conv, [128, 3, 2]]       # 20
  - [[-1, 16], 1, Concat, [1]]       # 21: Cat with top-down P3
  - [-1, 3, C2f, [256]]              # 22 (Final P3 feature map)

  - [-1, 1, Conv, [256, 3, 2]]       # 23
  - [[-1, 13], 1, Concat, [1]]       # 24: Cat with top-down P4
  - [-1, 3, C2f, [512]]              # 25 (Final P4 feature map)

  # Detection head. P2/4 is layer 19; P3/8 is 22; P4/16 is 25.
  # P2 is included here so the head matches stage01 exactly. See
  # USE_P2_HEAD in stage01.IA-YOLO.py for the measured cost (+4.6 GFLOPs) and
  # for why the shared classification-branch width narrows to 64.
  - [[19, 22, 25], 1, Detect, [nc]]  # 26: P2/4 + P3/8 + P4/16
"""


# =====================================================================
# Registration with Ultralytics
# =====================================================================

def register():
    """Make the custom layers loadable and buildable. Runs on import.

    * Loading weights: the classes are also exposed as __main__.CDB etc., for
      any older checkpoint pickled from the training script (run as __main__).
      The shipped qs_yolov8s_person.pt references qs_yolov8.* directly.
    * Building from ARCH_YAML: Ultralytics' parse_model only knows its own
      layers, so VSFFM joins the Conv family (width scaling) and C2f_CDB the C2f
      family (repeat count injection), exactly as in training.
    """
    main = sys.modules["__main__"]
    for cls in (CDB, C2f_CDB, VSFFM):
        setattr(main, cls.__name__, cls)

    import ultralytics.nn.tasks as tasks
    if getattr(tasks, "_qs_yolov8_registered", False):
        return
    src = inspect.getsource(tasks.parse_model)
    src = re.sub(r"\bConv,", "Conv, VSFFM,", src)
    src = re.sub(r"\bC2f,", "C2f, C2f_CDB,", src)
    if "VSFFM" not in src or "C2f_CDB" not in src:
        raise RuntimeError("this Ultralytics version's parse_model layout is not recognised "
                           "(trained with 8.4.78); loading the .pt still works, build_model() does not")
    tasks.CDB, tasks.C2f_CDB, tasks.VSFFM = CDB, C2f_CDB, VSFFM
    exec(src, tasks.__dict__)
    tasks._qs_yolov8_registered = True


def build_model(nc=1):
    """Untrained QS-YOLOv8s from ARCH_YAML as an Ultralytics YOLO object."""
    import os
    import tempfile
    from ultralytics import YOLO
    register()
    # The file name must contain "yolov8s" so Ultralytics picks the 's' scale.
    path = os.path.join(tempfile.mkdtemp(), "qs_yolov8s.yaml")
    with open(path, "w") as f:
        f.write(ARCH_YAML.replace("nc: 1", f"nc: {nc}", 1))
    return YOLO(path)


register()
