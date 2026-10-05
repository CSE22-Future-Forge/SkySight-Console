"""
mulfs_fast.py — vectorised, numerically-equivalent replacements for the per-window Python
loops in the official MulFS-CAP code (github.com/YR0211/MulFS-CAP, commit ce3a6ef).

Same weights, same maths, same checkpoints. Replaced:
  model.df_window_partition     256-iteration copy loop      -> one cached gather
  model.CMAP                    per-window MHCSAB + attention -> all windows as one batch
  model.feature_reorganization  window x batch double loop    -> one einsum
  loss.L_correspondence.forward window x batch loop + nonzero -> one broadcast comparison

Quirks of the original that are preserved on purpose (so results are identical):
  * MHCSAB's positional encoding is indexed by the *batch* position (x.size(1) of a
    [seq, batch, emb] tensor), not by sequence position. We reproduce that exactly.
  * Zero-padded positions in df_window_partition are treated as pixel 0 by the loss.

Usage:
    import model.model as M, loss.loss as L, mulfs_fast
    mulfs_fast.install(M, L)      # fast path
    mulfs_fast.uninstall()        # back to the original code
"""
import math
import os

import torch
import torch.nn as nn
import torch.nn.functional as F

_M = None          # the MulFS-CAP model module
_L = None          # the MulFS-CAP loss module
_ORIG = {}
_IDX_CACHE = {}
_PE_CACHE = {}


# ---------------------------------------------------------------- window partition
def _window_index(H, W, lw, sw, device):
    """[N, lw*lw] source flat index (+1) of every large-window position; 0 = zero padding.
    Built once by running the ORIGINAL partition on an index image, so all its edge/corner
    special cases are reproduced exactly."""
    key = (H, W, lw, sw, str(device))
    if key not in _IDX_CACHE:
        base = (torch.arange(H * W, dtype=torch.float64) + 1).reshape(1, 1, H, W)
        win = _ORIG["df_window_partition"](base, lw, sw, is_bewindow=False)   # [1, lw*lw, N]
        _IDX_CACHE[key] = win[0].t().round().long().to(device)
    return _IDX_CACHE[key]


def df_window_partition(x, large_window_size, small_window_size, is_bewindow=True):
    B, C, H, W = x.shape
    lw = int(large_window_size)
    idx = _window_index(H, W, lw, int(small_window_size), x.device)            # [N, K]
    N, K = idx.shape
    g = F.pad(x.reshape(B, C, H * W), (1, 0))[:, :, idx]                       # [B, C, N, K]
    if is_bewindow:
        return g.permute(2, 0, 1, 3).reshape(N, B, C, lw, lw)
    return g.permute(0, 1, 3, 2).reshape(B, C * K, N)


# ---------------------------------------------------------------- batched MHCSAB
def _pe_table(E, n, device, dtype):
    key = (E, str(device), dtype)
    if key not in _PE_CACHE or _PE_CACHE[key].shape[0] < n:
        pe = _M.PositionalEncoding(E, 0.0, max_len=max(5000, n)).pe[0]         # identical formula
        _PE_CACHE[key] = pe.to(device=device, dtype=dtype)
    return _PE_CACHE[key]


def _add_pe(m, x, mha, bidx):
    pe = _pe_table(x.shape[2], int(bidx.max()) + 1, x.device, x.dtype)[bidx]   # [NB, E]
    return F.dropout(x + pe[None], p=m.dropout, training=mha.training)


def _self_attention(m, x, mha, bidx):
    xpe = _add_pe(m, x, mha, bidx)
    return mha(xpe, xpe, x)[0] + x


def _cross_attention(m, q, kv, mha, bidx):
    return mha(_add_pe(m, q, mha, bidx), _add_pe(m, kv, mha, bidx), kv)[0] + q


def _mhcsab(m, x, bidx):
    """MHCSAB.forward for a stack of windows x:[NB, 8, w, w]; bidx[i] = original batch index."""
    ws = x.shape[3]
    L, S = m.largesize, m.smallsize
    fold_l = nn.Fold((ws, ws), (L, L), stride=L)
    fold_s = nn.Fold((ws, ws), (S, S), stride=S)
    lw_ = nn.Unfold((L, L), stride=L)(m.LargeScaleEncoder(x)).permute(2, 0, 1)
    sw_ = nn.Unfold((S, S), stride=S)(m.SmallScaleEncoder(x)).permute(2, 0, 1)
    lws = _self_attention(m, lw_, m.SA_large, bidx)
    sws = _self_attention(m, sw_, m.SA_small, bidx)
    ls, ss = lws.shape, sws.shape
    l2s = m.mapping_l2s(lws.reshape(ls[0] * ls[1], ls[2])).reshape(ls[0], ls[1], ss[2])
    s2l = m.mapping_s2l(sws.reshape(ss[0] * ss[1], ss[2])).reshape(ss[0], ss[1], ls[2])
    lc = _cross_attention(m, lws, s2l, m.CA_large, bidx)
    sc = _cross_attention(m, sws, l2s, m.CA_small, bidx)
    return m.Decoder(torch.cat([fold_l(lc.permute(1, 2, 0)), fold_s(sc.permute(1, 2, 0))], dim=1))


def CMAP(fixed_windows, moving_windows, vis_MHCSA, ir_MHCSA, is_vis_fixed, chunk=None):
    N, B, C, wf, _ = fixed_windows.shape
    wm = moving_windows.shape[3]
    fm, mm = (vis_MHCSA, ir_MHCSA) if is_vis_fixed else (ir_MHCSA, vis_MHCSA)
    chunk = chunk or int(os.environ.get("MULFS_FAST_CHUNK", "0")) or N           # windows per batch
    fw = fixed_windows.reshape(N * B, C, wf, wf)
    mw = moving_windows.reshape(N * B, C, wm, wm)
    bidx = torch.arange(B, device=fw.device).repeat(N)                          # row n*B+b -> b
    out = []
    for s in range(0, N, chunk):
        sl = slice(s * B, min(N, s + chunk) * B)
        fp = _mhcsab(fm, fw[sl], bidx[sl]).flatten(2).transpose(1, 2)           # [nb, wf^2, C]
        mp = _mhcsab(mm, mw[sl], bidx[sl]).flatten(2).transpose(1, 2)           # [nb, wm^2, C]
        out.append(torch.softmax(torch.bmm(fp / math.sqrt(fp.shape[2]), mp.transpose(1, 2)), dim=-1))
    return torch.cat(out).reshape(N, B, wf * wf, wm * wm)


# ---------------------------------------------------------------- feature reorganisation
def feature_reorganization(similaritys, x):
    N, B, S2, L2 = similaritys.shape
    sw, lw = int(round(math.sqrt(S2))), int(round(math.sqrt(L2)))
    _, C, H, W = x.shape
    xw = df_window_partition(x, lw, sw).reshape(N, B, C, L2)
    sample = torch.einsum("nbsl,nbcl->nbcs", similaritys, xw).reshape(N, B, C, sw, sw)
    return _M.resume(H, W, sw, sw, C)(sample)


# ---------------------------------------------------------------- correspondence loss
def _correspondence_forward(self, correspondence_matrixs, index_r):
    N, B, S2, L2 = correspondence_matrixs.shape
    sw, lw = int(round(math.sqrt(S2))), int(round(math.sqrt(L2)))
    dev = correspondence_matrixs.device
    H, W = self.height, self.weight
    s_abs = nn.Unfold((sw, sw), stride=sw)(torch.arange(H * W, device=dev, dtype=torch.float64)
                                           .reshape(1, 1, H, W))[0].t().round().long()   # [N, S2]
    l_abs = (_window_index(H, W, lw, sw, dev) - 1).clamp(min=0)                            # [N, L2]
    zero_pair = (s_abs == 0)[:, :, None] & (l_abs == 0)[:, None, :]                          # [N, S2, L2]
    P = torch.clamp(correspondence_matrixs, 1e-6, 1 - 1e-6)
    l_cm = torch.zeros(N, B, device=dev)
    l_c = torch.zeros(N, B, device=dev)
    q_all = torch.arange(H * W, device=dev)
    for b in range(B):
        src, ident = index_r[b, 0], index_r[b, 1]
        valid = ident == q_all                               # pixel q is inside the warped image
        gt =valid[l_abs][:, None, :] & (src[l_abs][:, None, :] == s_abs[:, :, None]) & ~zero_pair
        c_num = gt.sum(dim=(1, 2))
        assert bool((c_num != 0).all()), "empty ground-truth correspondence window (same assert as original)"
        gtf = gt.to(P.dtype)
        l_cm[:, b] = -(torch.log(P[:, b]) * gtf).sum(dim=(1, 2)) / c_num
        l_c[:, b] = (correspondence_matrixs[:, b] - gtf).abs().mean(dim=(1, 2))
    return l_cm.mean(), l_c.mean()


# ---------------------------------------------------------------- install / uninstall
def install(model_module, loss_module=None):
    global _M, _L
    _M, _L = model_module, loss_module
    if "df_window_partition" not in _ORIG:
        _ORIG["df_window_partition"] = model_module.df_window_partition
        _ORIG["CMAP"] = model_module.CMAP
        _ORIG["feature_reorganization"] = model_module.feature_reorganization
        if loss_module is not None:
            _ORIG["L_correspondence.forward"] = loss_module.L_correspondence.forward
    model_module.df_window_partition = df_window_partition
    model_module.CMAP = CMAP
    model_module.feature_reorganization = feature_reorganization
    if loss_module is not None:
        loss_module.L_correspondence.forward = _correspondence_forward


def uninstall():
    if not _ORIG:
        return
    _M.df_window_partition = _ORIG["df_window_partition"]
    _M.CMAP = _ORIG["CMAP"]
    _M.feature_reorganization = _ORIG["feature_reorganization"]
    if _L is not None and "L_correspondence.forward" in _ORIG:
        _L.L_correspondence.forward = _ORIG["L_correspondence.forward"]


def is_installed():
    return _M is not None and _M.CMAP is CMAP
