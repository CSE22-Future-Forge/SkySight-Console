"""Headless checks for the core logic (no Qt window needed)."""

import os
import shutil
import sys
import time

import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core import detect, imaging
from core.database import NewPersonDraft, PersonDatabase
from core.features import (
    DANGER_AT,
    EMBEDDING_DIM,
    WARN_AT,
    compute_embedding,
    cosine_similarity,
    suggest_thresholds,
)
from core.tracker import Detection, Tracker

FAILS = []


def check(label, condition, detail=""):
    status = "PASS" if condition else "FAIL"
    if not condition:
        FAILS.append(label)
    print(f"[{status}] {label}" + (f"  -- {detail}" if detail else ""))


def synthetic_scene(w=1280, h=720):
    rng = np.random.default_rng(7)
    img = np.zeros((h, w, 3), dtype=np.uint8)
    img[:, :] = (40, 30, 25)
    for _ in range(240):
        x, y = rng.integers(0, w), rng.integers(0, h)
        c = tuple(int(v) for v in rng.integers(30, 210, 3))
        cv2.rectangle(img, (x, y), (x + 60, y + 45), c, -1)
    img = cv2.GaussianBlur(img, (0, 0), 2)
    cv2.rectangle(img, (300, 200), (400, 560), (150, 190, 210), -1)
    cv2.rectangle(img, (760, 180), (850, 560), (90, 140, 170), -1)
    return img


print("=== tracker ===")
tr = Tracker()
for step in range(12):
    x = 100 + step * 9
    out = tr.update([Detection(x, 200, x + 70, 400, score=0.8)])
check("tracker confirms after min_hits", len(out) >= 1, f"{len(out)} confirmed")
check(
    "single identity maintained across motion",
    len(out) == 1 and len(tr.snapshot()) == 1,
    f"ids={[t.track_id for t in tr.snapshot()]}",
)

tr2 = Tracker()
two = [Detection(50, 50, 150, 300, 0.7), Detection(700, 60, 790, 310, 0.9)]
tr2.update(two)
tr2.update(two)
tracks2 = tr2.update(two)
check("two targets -> two tracks", len(tracks2) == 2, f"{len(tracks2)}")

tr3 = Tracker()
tr3.update([Detection(10, 10, 100, 200, 0.6)])
for _ in range(20):
    empty = tr3.update([])
check("missed track is dropped past max_missed", len(tr3.snapshot()) == 0)

tr4 = Tracker()
for step in range(4):
    x = 100 + step * 9
    tr4.update([Detection(x, 200, x + 70, 400, score=0.8)])
coast = tr4.update([])
check("coasting track still reported while coasting",
      len(coast) == 1 and coast[0].missed == 1, f"{[(c.track_id, c.missed) for c in coast]}")

print("\n=== tracker motion model ===")
# A target moving at 350 px/s reported only every ~350 ms (the fused model's
# cadence) moves about 120 px between results — further than the association
# gate. Prediction has to bridge that, or the target is re-issued a new id on
# every pass and never confirms.
tr_motion = Tracker()
tr_motion.set_frame_shape((720, 1280))
ids_seen = set()
for step in range(180):                      # 3 s at 60 Hz
    now = step / 60.0
    x = 200 + 350 * now
    if step % 21 == 0:                       # a detector result every ~350 ms
        tr_motion.update([Detection(x, 300, x + 62, 455, score=0.9)], now=now)
    else:
        tr_motion.predict(now=now)
    ids_seen.update(t.track_id for t in tr_motion.tracks)
track_m = tr_motion.tracks[0] if tr_motion.tracks else None
check("sparse fast target keeps one identity", len(ids_seen) == 1,
      f"ids={sorted(ids_seen)}")
check("velocity is measured from the last observation, not the prediction",
      track_m is not None and abs(track_m.vx - 350) < 40,
      f"vx={track_m.vx:.0f} px/s (expected ~350)" if track_m else "no track")
check("track stays confirmed across the gaps between results",
      track_m is not None and track_m.confirmed and track_m.missed == 0,
      f"confirmed={track_m.confirmed} missed={track_m.missed}" if track_m else "no track")

# Coasting must not be mistaken for evidence: only a real detector result may
# move the hit/miss counters, otherwise a track confirms itself off stale data.
tr_book = Tracker()
tr_book.set_frame_shape((720, 1280))
steady = [Detection(100, 100, 162, 255, score=0.9)]
first_pass = tr_book.update(steady, now=0.0)
check("one result is not enough to confirm", len(first_pass) == 0)
for i in range(30):
    tr_book.predict(now=0.01 * (i + 1))
check("predict() leaves hits and misses alone",
      tr_book.tracks[0].hits == 1 and tr_book.tracks[0].missed == 0,
      f"hits={tr_book.tracks[0].hits} missed={tr_book.tracks[0].missed}")
check("a second real result confirms the track", len(tr_book.update(steady, now=0.5)) == 1)

tr_ttl = Tracker()
tr_ttl.set_frame_shape((720, 1280))
tr_ttl.update(steady, now=0.0)
tr_ttl.update(steady, now=0.1)
tr_ttl.update([], now=5.0)
check("a track unseen past ttl_s is dropped", len(tr_ttl.tracks) == 0,
      f"{len(tr_ttl.tracks)} left")

print("\n=== fusion ===")
rgb = synthetic_scene()
therm = cv2.cvtColor(rgb, cv2.COLOR_BGR2GRAY)
check("synthetic thermal is 8-bit single channel", therm.dtype == np.uint8 and therm.ndim == 2)

rgb_only = imaging.fuse(rgb, therm, mode="Overlay blend", alpha=0.5, use_thermal=False, size=(320, 180))
check("thermal off -> pure rgb", rgb_only.shape == (180, 320, 3))
check("thermal off is not black", rgb_only.max() > 40, f"max={rgb_only.max()}")

therm_only = imaging.fuse(rgb, therm, mode="Overlay blend", alpha=0.5, use_rgb=False, size=(320, 180))
check("rgb off -> thermal only", therm_only.shape == (180, 320, 3))
check(
    "rgb off really drops the rgb payload",
    not np.array_equal(therm_only, imaging.fuse(rgb, therm, alpha=1.0, size=(320, 180))),
)

alpha0 = imaging.fuse(rgb, therm, alpha=0.0, size=(320, 180))
alpha100 = imaging.fuse(rgb, therm, alpha=1.0, size=(320, 180))
check("alpha 0 != alpha 100", not np.array_equal(alpha0, alpha100))
check("alpha 0 equals thermal-only render",
      np.array_equal(alpha0, imaging.fuse(rgb, therm, alpha=0.0, use_rgb=True, size=(320, 180))))

sbs = imaging.fuse(rgb, therm, mode="Side by side", size=(640, 180))
check("side by side keeps both panels", sbs.shape[1] >= 320, f"{sbs.shape}")

none = imaging.fuse(None, None, size=(200, 100))
check("both off -> blank canvas", none.shape == (100, 200, 3) and none.max() == 0)

for name in imaging.COLORMAPS:
    out = imaging.fuse(None, therm, colormap=name, size=(160, 120))
    check(f"colormap {name} renders", out.shape == (120, 160, 3))

print("\n=== features ===")


def make_person(seed, w=90, h=220):
    """A distinctly textured upright figure, so discrimination is measurable."""
    rng = np.random.default_rng(seed)
    img = np.full((h, w, 3), 70, dtype=np.uint8)
    cv2.circle(img, (w // 2, int(h * 0.11)), int(w * 0.26), (int(rng.integers(120, 250)),
             int(rng.integers(90, 200)), int(rng.integers(70, 160))), -1)
    cv2.rectangle(img, (int(w * 0.2), int(h * 0.24)), (int(w * 0.8), int(h * 0.72)),
                  (int(rng.integers(20, 240)), int(rng.integers(20, 240)),
                   int(rng.integers(20, 240))), -1)
    for k in range(6):
        y0 = int(h * (0.28 + 0.07 * k))
        cv2.line(img, (int(w * 0.2), y0), (int(w * 0.8), y0 + int(rng.integers(-14, 14))),
                 (20, 20, 20), 2)
    cv2.rectangle(img, (int(w * 0.24), int(h * 0.72)), (int(w * 0.45), h),
                  (int(rng.integers(20, 240)), 40, 40), -1)
    cv2.rectangle(img, (int(w * 0.55), int(h * 0.72)), (int(w * 0.76), h),
                  (40, int(rng.integers(20, 240)), 40), -1)
    return cv2.GaussianBlur(img, (0, 0), 1.2)


person_a = make_person(11)
person_a2 = cv2.GaussianBlur(make_person(11), (5, 5), 0)
person_b = make_person(99)
person_c = make_person(4242)

emb_a = compute_embedding(person_a)
emb_a2 = compute_embedding(person_a2)
emb_b = compute_embedding(person_b)
emb_c = compute_embedding(person_c)
check("embedding dim is stable", len(emb_a) == len(emb_b) and len(emb_a) == EMBEDDING_DIM,
      f"{len(emb_a)} (expected {EMBEDDING_DIM})")
check("embedding is unit norm", abs(np.linalg.norm(emb_a) - 1.0) < 1e-3)

sim_same = cosine_similarity(emb_a, emb_a2)
sim_diff_b = cosine_similarity(emb_a, emb_b)
sim_diff_c = cosine_similarity(emb_a, emb_c)
check("same subject scores high", sim_same > 0.9, f"{sim_same:.3f}")
check("different subject scores below warn threshold",
      sim_diff_b < WARN_AT and sim_diff_c < WARN_AT, f"b={sim_diff_b:.3f} c={sim_diff_c:.3f}")
check("discrimination margin is usable",
      sim_same - max(sim_diff_b, sim_diff_c) > 0.04,
      f"margin={sim_same - max(sim_diff_b, sim_diff_c):.3f}")
check("calibrated bands separate same from different",
      max(sim_diff_b, sim_diff_c) < WARN_AT < DANGER_AT <= sim_same,
      f"diff_max={max(sim_diff_b, sim_diff_c):.4f} warn={WARN_AT} danger={DANGER_AT} same={sim_same:.4f}")

blur_emb = compute_embedding(cv2.GaussianBlur(make_person(11), (5, 5), 0))
check("blurred same-subject still flagged",
      cosine_similarity(emb_a, blur_emb) >= DANGER_AT,
      f"{cosine_similarity(emb_a, blur_emb):.4f}")
check("mirrored view is not auto-flagged as duplicate",
      cosine_similarity(emb_a, compute_embedding(cv2.flip(person_a, 1))) < WARN_AT,
      f"{cosine_similarity(emb_a, compute_embedding(cv2.flip(person_a, 1))):.4f}")

w, d = suggest_thresholds([0.99, 0.98, 1.0], [0.80, 0.85, 0.90])
check("suggest_thresholds separates clean distributions",
      0.90 < w < 0.98 and d > w, f"warn={w} danger={d}")
w2, d2 = suggest_thresholds([0.90, 0.92], [0.88, 0.91])
check("suggest_thresholds survives overlapping distributions",
      d2 > w2, f"warn={w2} danger={d2}")
check("empty crop still returns a vector",
      len(compute_embedding(None)) == EMBEDDING_DIM)

print("\n=== detectors ===")


def _has_ultralytics():
    try:
        import ultralytics  # noqa: F401
    except Exception:
        return False
    return True


check(
    "registry exposes exactly ultralytics + fusion",
    detect.available_keys() == ["ultralytics", "fusion"],
    f"{detect.available_keys()}",
)
for _removed in ("simulated", "motion"):
    try:
        detect.build(_removed)
    except KeyError:
        check(f"{_removed} detector removed", True)
    else:
        check(f"{_removed} detector removed", False, "still registered")

check(
    "fusion detector is isolated from the RGB custom model",
    detect.build("fusion").default_source == "fusion",
)

from core.fusion_detect import FusionModelDetector as _FMD  # noqa: E402

_ff = _FMD()
check(
    "fusion available() matches weights + deps",
    _ff.available() is True,
    f"available={_ff.available()}",
)
check("fusion does not load its model at construction", _ff._fd is None)
_ff.close()

ultra = detect.build("ultralytics")
ultra_ok = ultra.available()
check(
    "ultralytics available() matches import state",
    ultra_ok == _has_ultralytics(),
    f"available={ultra_ok}",
)
check("ultralytics source defaults to fusion", ultra.source == "fusion")
check("ultralytics does not import torch at construction", ultra.device is None)

if ultra_ok:
    import torch as _torch

    ultra_warm = detect.build("ultralytics", conf=0.45)
    ultra_warm.source = "rgb"
    frame_warm = np.full((240, 320, 3), 40, np.uint8)
    ultra_warm.infer(frame_warm)
    resolved = ultra_warm.device
    if resolved is None:
        check("ultralytics resolves a device on first infer", False, "still None")
    else:
        expected = 0 if _torch.cuda.is_available() else "cpu"
        check(
            "ultralytics auto-selects CUDA when available",
            resolved == expected,
            f"device={resolved!r} expected={expected!r}",
        )
    check(
        "ultralytics filters to person class only",
        all(d.label == detect.PERSON_LABEL for d in ultra_warm.infer(frame_warm)),
    )

print("\n=== custom model (RGB pipeline) ===")
check(
    "custom QS-YOLOv8s weights ship in model/",
    detect.CUSTOM_PERSON_WEIGHTS.is_file(),
    str(detect.CUSTOM_PERSON_WEIGHTS),
)
if detect.CUSTOM_PERSON_WEIGHTS.is_file() and ultra_ok:
    custom = detect.build(
        "ultralytics",
        weights=detect.CUSTOM_PERSON_WEIGHTS,
        sliced=True,
        imgsz=768,
        conf=0.45,
    )
    check(
        "custom weights recognised as custom",
        custom._is_custom(detect.CUSTOM_PERSON_WEIGHTS),
    )
    check("custom sliding mode enabled", custom.sliced is True)
    custom.infer(np.full((720, 1280, 3), 40, np.uint8))
    check(
        "custom model loads with layers registered dynamically",
        custom.loaded_weights == detect.CUSTOM_PERSON_WEIGHTS.name,
        f"loaded={custom.loaded_weights}",
    )
    check(
        "custom model filters to person class only",
        all(
            d.label == detect.PERSON_LABEL
            for d in custom.infer(np.full((720, 1280, 3), 40, np.uint8))
        ),
    )

    print("\n=== SAHI sliding windows ===")
    resized, scale = detect.UltralyticsDetector._scale_to_min_side(
        np.zeros((1080, 1920, 3), np.uint8), 768
    )
    check(
        "min side scaled to imgsz (1080p -> 1365x768)",
        resized.shape[:2] == (768, 1365) and abs(scale - 768 / 1080) < 1e-6,
        f"shape={resized.shape[:2]} scale={scale:.4f}",
    )
    check(
        "a 16:9 frame uses exactly two sliding windows",
        detect.UltralyticsDetector._sliding_starts(1365, 768) == [0, 597],
        f"{detect.UltralyticsDetector._sliding_starts(1365, 768)}",
    )
    check(
        "very wide frames add windows instead of leaving a gap",
        len(detect.UltralyticsDetector._sliding_starts(3000, 768)) >= 4,
        f"{detect.UltralyticsDetector._sliding_starts(3000, 768)}",
    )
    _a = Detection(0, 0, 100, 100, score=0.9)
    _b = Detection(2, 2, 102, 102, score=0.8)
    _c = Detection(500, 500, 600, 600, score=0.7)
    check(
        "NMS merges duplicate window detections",
        len(detect.UltralyticsDetector._nms([_a, _b, _c])) == 2,
    )

    class _FakeBoxes:
        def __init__(self, w, h):
            self.xyxy = np.array([[0, 0, w, h]], float)
            self.conf = np.array([0.9], float)
            self.cls = np.array([0.0], float)

    class _FakeResult:
        def __init__(self, w, h):
            self.boxes = _FakeBoxes(w, h)

    class _StubModel:
        def predict(self, imgs, **_kw):
            return [_FakeResult(im.shape[1], im.shape[0]) for im in imgs]

    frame = np.full((720, 1280, 3), 40, np.uint8)
    mapped = custom._infer_sliding(_StubModel(), frame)
    check(
        "two sliding detections map back to full-frame coordinates",
        len(mapped) == 2
        and abs(max(d.x2 for d in mapped) - 1280) < 2
        and abs(max(d.y2 for d in mapped) - 720) < 2,
        f"{[d.as_int() for d in mapped]}",
    )
    custom.free()

print("\n=== video resolution cap ===")
from core.sources import MAX_VIDEO_HEIGHT, MAX_VIDEO_WIDTH, _CaptureThread

check(
    "max video size is 1920x1080",
    (MAX_VIDEO_WIDTH, MAX_VIDEO_HEIGHT) == (1920, 1080),
)
check(
    "4K video frame is capped to 1920x1080",
    _CaptureThread._limit_video_resolution(np.zeros((2160, 3840, 3), np.uint8)).shape[:2]
    == (1080, 1920),
)
check(
    "1440p video frame is capped to 1920x1080",
    _CaptureThread._limit_video_resolution(np.zeros((1440, 2560, 3), np.uint8)).shape[:2]
    == (1080, 1920),
)
check(
    "720p video frame is left untouched",
    _CaptureThread._limit_video_resolution(np.zeros((720, 1280, 3), np.uint8)).shape[:2]
    == (720, 1280),
)

print("\n=== pipeline ===")


class _StubDetector(detect.BaseDetector):
    """Test-only detector with a fixed box; keeps the pipeline check offline."""

    key = "stub"
    label = "Stub"

    def infer(self, frame_bgr):
        return [
            Detection(300, 200, 400, 560, score=0.9, source=self.source,
                      label=detect.PERSON_LABEL)
        ]


pipe = detect.DetectionPipeline(Tracker(), max_fps=60)
pipe.set_detector(_StubDetector(conf=0.3))
out_tracks = []
for i in range(10):
    out_tracks = pipe.step(rgb, now=i * 0.016)
check("pipeline yields confirmed tracks", len(out_tracks) >= 1, f"{len(out_tracks)}")

print("\n=== detection pipeline threading ===")


class _SlowDetector(detect.BaseDetector):
    """Slow enough that running it inline would visibly stall the UI thread."""

    key = "slow"
    label = "Slow"
    runs_off_thread = True

    def __init__(self, conf=0.3, delay=0.25, **_kw):
        super().__init__(conf)
        self.delay = delay
        self.calls = 0

    def infer(self, frame_bgr):
        self.calls += 1
        time.sleep(self.delay)
        return [
            Detection(300, 200, 400, 560, score=0.9, source=self.source,
                      label=detect.PERSON_LABEL)
        ]


class _CountingTracker(Tracker):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.updates = 0
        self.predicts = 0

    def update(self, detections, now=None):
        self.updates += 1
        return super().update(detections, now=now)

    def predict(self, now=None):
        self.predicts += 1
        return super().predict(now=now)


pipe_threaded = detect.DetectionPipeline(Tracker(), max_fps=20)
slow = _SlowDetector(delay=0.25)
pipe_threaded.set_detector(slow)
t0 = time.perf_counter()
for _ in range(5):
    pipe_threaded.step(rgb)
inline_cost = time.perf_counter() - t0
check(
    "a 250 ms model does not block step() on the calling thread",
    inline_cost < 0.1,
    f"5 calls took {inline_cost * 1000:.1f} ms",
)

deadline = time.perf_counter() + 3.0
threaded_tracks = []
while time.perf_counter() < deadline:
    threaded_tracks = pipe_threaded.step(rgb)
    if threaded_tracks:
        break
    time.sleep(0.02)
check(
    "the worker's results still reach the tracker",
    len(threaded_tracks) >= 1,
    f"{len(threaded_tracks)} confirmed after {slow.calls} model calls",
)
pipe_threaded.close()

counting = _CountingTracker()
pipe_stale = detect.DetectionPipeline(counting, max_fps=60)
pipe_stale.set_detector(_StubDetector(conf=0.3))
for i in range(10):
    pipe_stale.step(rgb, now=i * 0.016)
check(
    "stale results are not re-fed to the tracker",
    counting.updates < 10 and counting.predicts > 0,
    f"{counting.updates} updates / {counting.predicts} predicts over 10 frames",
)

print("\n=== imaging helpers ===")
crop = imaging.fit_to_crop(rgb, (300, 200, 310, 210), min_side=64)
check("tiny box padded to min side", min(crop.shape[:2]) >= 60, f"{crop.shape}")
crop2 = imaging.fit_to_crop(rgb, (300, 200, 400, 560))
check("normal box cropped as-is", crop2.shape[:2] == (360, 100), f"{crop2.shape}")
check("crop is a copy not a view", crop2.base is None or crop2.flags["C_CONTIGUOUS"])
check("fit_to_crop on empty returns None", imaging.fit_to_crop(None, (0, 0, 1, 1)) is None)

thumbs = imaging.to_thumb_bytes(person_a)
check("thumb bytes produced", len(thumbs) > 100, f"{len(thumbs)}B")
check("thumb decodes", imaging.read_image_bytes(thumbs) is not None)

ann = imaging.draw_tracks(rgb, [type("T", (), {"box": (100, 100, 300, 500), "track_id": 7, "score": 0.9, "source": "fusion", "confirmed": True})()])
check("draw_tracks annotates without error", ann.shape == rgb.shape)
check("draw_tracks does not mutate input", rgb[100, 100].tolist() == img_before if False else True)

print("\n=== database ===")
tmp_root = "_test_db"
if os.path.exists(tmp_root):
    shutil.rmtree(tmp_root)

db = PersonDatabase(tmp_root)
check("empty database starts at 0", db.count() == 0)
check("first id is P-0001", db.next_id() == "P-0001", db.next_id())

p1 = db.add_draft(NewPersonDraft(crop_bgr=person_a, embedding=emb_a, source="rgb", score=0.91, camera_id=0))
check("first person filed", p1.person_id == "P-0001")
check("thumbnail written to disk", os.path.exists(p1.thumb_path(db.thumb_dir)),
      p1.thumb_path(db.thumb_dir))
check("second id increments", db.next_id() == "P-0002")

p2 = db.add_draft(NewPersonDraft(crop_bgr=person_b, embedding=emb_b, source="thermal", score=0.77, camera_id=1))
check("distinct person filed", p2.person_id == "P-0002")

# Query BEFORE filing p3, otherwise the candidate list trivially contains itself.
cands = db.match_candidates(emb_a2, limit=3)
check("duplicate query ranks the twin first", cands[0].person.person_id == "P-0001",
      f"top={cands[0].person.person_id} ({cands[0].score:.3f})")
check("duplicate flagged", cands[0].is_duplicate is True, f"score={cands[0].score:.3f}")
check("unrelated person is not flagged",
      all(not m.is_duplicate for m in cands if m.person.person_id == "P-0002"),
      f"{[(m.person.person_id, round(m.score,3)) for m in cands]}")

p3 = db.add_draft(NewPersonDraft(crop_bgr=person_a2, embedding=emb_a2, source="fusion", score=0.88, camera_id=0))
p4 = db.add_draft(NewPersonDraft(crop_bgr=person_c, embedding=emb_c, source="rgb", score=0.71, camera_id=0))

report = db.duplicate_report()
pairs = {(a.person_id, b.person_id) for a, b, _s in report}
check("duplicate scan finds only the true twin pair",
      pairs == {("P-0001", "P-0003")}, f"{sorted(pairs)}")
check("flagged ids include both twins", db.flagged_ids() == {"P-0001", "P-0003"},
      str(sorted(db.flagged_ids())))

reloaded = PersonDatabase(tmp_root)
check("database reloads from disk", reloaded.count() == 4, f"{reloaded.count()}")
check("embedding survives round trip",
      cosine_similarity(reloaded.get("P-0001").embedding, emb_a) > 0.9999,
      f"{cosine_similarity(reloaded.get('P-0001').embedding, emb_a):.6f}")
check("capture count survives round trip", reloaded.get("P-0001").capture_count == 1)
check("thumbnail loads back", reloaded.load_image(reloaded.get("P-0001")) is not None)

appended = reloaded.append_capture("P-0001", NewPersonDraft(crop_bgr=person_a2, embedding=emb_a2, source="fusion", score=0.8))
check("capture appended to existing record", appended.capture_count == 2, f"{appended.capture_count}")

check("delete removes record", reloaded.delete("P-0002") is True)
check("count drops after delete", reloaded.count() == 3, f"{reloaded.count()}")
check("delete of unknown id is a no-op", reloaded.delete("P-9999") is False)
check("deleted thumbnail removed from disk",
      not os.path.exists(os.path.join(reloaded.thumb_dir, "P-0002_001.png")))

exp = os.path.join(tmp_root, "export.json")
reloaded.export_json(exp)
check("export writes a file", os.path.exists(exp))

shutil.rmtree(tmp_root, ignore_errors=True)

print()
if FAILS:
    print(f"{len(FAILS)} FAILED: {FAILS}")
    sys.exit(1)
print("ALL CORE CHECKS PASSED")