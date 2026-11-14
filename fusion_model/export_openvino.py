"""Try to export the MulFS-CAP fusion network to OpenVINO (ONNX -> IR).

Experimental: the fusion net is a custom torch module (unfold/fold + attention
+ cached window-index gathers), so the ONNX export may or may not succeed.

Run from the project root:

    python -m fusion_model.export_openvino

Writes ``fusion_model/export/mulfs_fuse_512x640.onnx`` and ``.xml/.bin`` and
prints the max abs difference between the torch and IR outputs.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import torch
import torch.nn as nn

from .pipeline import FusionDetector, _CKPT_KEYS

HERE = Path(__file__).resolve().parent
OUT = HERE / "export"


class FuseNet(nn.Module):
    """vis(1,1,H,W) + ir(1,1,H,W) -> fused(1,1,H,W) through the trained modules."""

    def __init__(self, M, modules):
        super().__init__()
        self.M = M
        for name, mod in modules.items():
            setattr(self, name, mod)

    def forward(self, vis, ir):
        M = self.M
        v1 = self.bfe(vis)
        i1 = self.bfe(ir)
        vfe = self.vis_mfe(v1)
        ife = self.ir_mfe(i1)
        vf = self.pafe(v1)
        irf = self.pafe(i1)
        vdp, _ = self.vis_dgfp(self.mn_vis(vf))
        idp, _ = self.ir_dgfp(self.mn_ir(irf))
        corr = M.CMAP(M.window_partition(vdp, 16, 16),
                      M.df_window_partition(idp, 26, 16),
                      self.mhcsab_vis, self.mhcsab_ir, True)
        return self.fusion_block(vfe, M.feature_reorganization(corr, ife)).clamp(0, 1)


def _patch_for_export():
    """Make the fusion kernels traceable by removing data-dependent indexing.

    The vectorised MHCSAB reads ``int(bidx.max())`` (the batch count) which the
    dynamo exporter cannot specialise; for the single-pair pipeline it is always
    1, so pin it.
    """
    import torch.nn.functional as F

    from .mulfs_cap import mulfs_fast as mf

    if getattr(mf, "_export_patched", False):
        return

    def _add_pe(m, x, mha, bidx):
        pe = mf._pe_table(x.shape[2], 1, x.device, x.dtype)[bidx]
        return F.dropout(x + pe[None], p=m.dropout, training=mha.training)

    mf._add_pe = _add_pe
    mf._export_patched = True


def build():
    fd = FusionDetector(device="cpu", max_side=640)
    fd._ensure_loaded()
    _patch_for_export()
    net = FuseNet(fd.M, {k: fd.f[k] for k in _CKPT_KEYS}).eval()
    return fd, net


def main():
    W, H = 640, 512
    OUT.mkdir(parents=True, exist_ok=True)
    print("building fusion detector (cpu) ...", flush=True)
    fd, net = build()

    vis = torch.rand(1, 1, H, W)
    ir = torch.rand(1, 1, H, W)
    with torch.no_grad():
        ref = net(vis, ir).numpy()
    print("torch output:", ref.shape, "range", float(ref.min()), float(ref.max()), flush=True)

    onnx_path = OUT / f"mulfs_fuse_{H}x{W}.onnx"
    print("exporting ONNX ->", onnx_path, flush=True)
    torch.onnx.export(
        net, (vis, ir), str(onnx_path),
        input_names=["vis", "ir"], output_names=["fused"],
        opset_version=17, do_constant_folding=True, dynamo=True,
    )
    print("ONNX written:", onnx_path.stat().st_size, "bytes", flush=True)

    import openvino as ov

    print("converting ONNX -> IR ...", flush=True)
    ov_model = ov.convert_model(str(onnx_path))
    ir_path = OUT / f"mulfs_fuse_{H}x{W}.xml"
    ov.save_model(ov_model, str(ir_path))
    print("IR written:", ir_path, flush=True)

    core = ov.Core()
    compiled = core.compile_model(ov_model, "CPU")
    out = compiled([vis.numpy(), ir.numpy()])[compiled.output(0)]
    diff = float(np.abs(out - ref).max())
    print(f"IR vs torch max abs diff: {diff:.6f}", flush=True)
    print("OK" if diff < 1e-3 else "MISMATCH", flush=True)


if __name__ == "__main__":
    sys.exit(main())
