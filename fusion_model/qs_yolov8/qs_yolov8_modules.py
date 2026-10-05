"""QS-YOLOv8 custom modules (CDB, C2f_CDB, VSFFM), taken from Fusion_vs_RGB_QSYOLOv8_LLVIP.ipynb.

Import this before building or loading a QS-YOLOv8 model. The trained .pt files were saved from a notebook, so
they reference these classes as __main__.CDB etc.; they are therefore also attached to __main__.
"""
import inspect, re

import torch
import torch.nn as nn
import ultralytics.nn.tasks as tasks
from ultralytics.nn.modules.conv import Conv


class CDB(nn.Module):
    # Channel-Decoupled Bottleneck with a 3:1 split for optimal detail preservation.
    def __init__(self, c1, c2, shortcut=True, g=1, e=0.5):
        super().__init__()
        self.c_a = int(c1 * 0.75)
        self.c_b = c1 - self.c_a
        self.cv1 = Conv(self.c_b, self.c_b, 3, 1)
        self.cv2 = Conv(self.c_b, self.c_b, 3, 1)
        self.cv3 = Conv(c1, c2, 1, 1)
        self.add = shortcut and c1 == c2

    def forward(self, x):
        x_a, x_b = torch.split(x, [self.c_a, self.c_b], dim=1)
        x_b = self.cv2(self.cv1(x_b))
        out = self.cv3(torch.cat((x_a, x_b), dim=1))
        return x + out if self.add else out


class C2f_CDB(nn.Module):
    # C2f module built with CDB instead of standard bottlenecks.
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


class VSFFM(nn.Module):
    # Serial multi-scale depthwise convolutions (7x7 -> 5x5 -> 3x3).
    def __init__(self, c1, c2):
        super().__init__()
        self.dw1 = Conv(c1, c1, k=7, s=2, p=3, g=c1)
        self.pw1 = Conv(c1, c2, k=1, s=1)
        self.dw2 = Conv(c2, c2, k=5, s=1, p=2, g=c2)
        self.pw2 = Conv(c2, c2, k=1, s=1)
        self.dw3 = Conv(c2, c2, k=3, s=1, p=1, g=c2)

    def forward(self, x):
        x1 = self.pw1(self.dw1(x))
        x2 = self.pw2(self.dw2(x1))
        return self.dw3(x2)


import __main__
for _c in (CDB, C2f_CDB, VSFFM):
    setattr(__main__, _c.__name__, _c)
if not hasattr(tasks, "VSFFM"):
    _src = inspect.getsource(tasks.parse_model)
    _src = re.sub(r"\bConv\b,", "Conv, CDB, C2f_CDB, VSFFM,", _src)
    tasks.CDB, tasks.C2f_CDB, tasks.VSFFM = CDB, C2f_CDB, VSFFM
    exec(_src, tasks.__dict__)
