"""Threaded capture sources.

One :class:`StreamManager` owns one camera. It wraps the capture loop in a
:class:`~PySide6.QtCore.QThread` so neither tab ever blocks on a camera read,
and it forwards ``numpy`` frames (not QImages) so fusion, detection and
thumbnail extraction all work on real pixel data.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np
from PySide6.QtCore import QObject, QThread, Signal

#: Video files bigger than this get downscaled once, on capture, so detection,
#: fusion and display all work on a bounded frame size. Aspect ratio is kept.
MAX_VIDEO_WIDTH = 1920
MAX_VIDEO_HEIGHT = 1080


@dataclass
class Frame:
    """One captured frame plus the stats the status bar reports."""

    source: str
    camera_id: int
    bgr: np.ndarray
    fps: float = 0.0
    index: int = 0
    wall_time: float = field(default_factory=time.time)
    #: Original capture frame, before any processing downscale. Detection and
    #: display use ``bgr``; crops pull from this higher-resolution copy.
    raw_bgr: np.ndarray | None = None

    @property
    def crop_bgr(self):
        """Full-resolution frame for crops (falls back to the processed one)."""
        return self.raw_bgr if self.raw_bgr is not None else self.bgr

    @property
    def size(self):
        return (self.bgr.shape[1], self.bgr.shape[0]) if self.bgr is not None else (0, 0)


class _CaptureThread(QThread):
    frameReady = Signal(object)
    failed = Signal(str)
    ended = Signal()

    def __init__(self, source, camera_id, parent=None):
        super().__init__(parent)
        self.source = source
        # camera_id can be int (camera) or str (video path)
        try:
            # int-like?
            if isinstance(camera_id, str) and camera_id.isdigit():
                self.camera_id = int(camera_id)
                self._is_video = False
                self._path = None
            elif isinstance(camera_id, int) or (isinstance(camera_id, str) and camera_id.lstrip('-+').isdigit() and not Path(camera_id).suffix):
                self.camera_id = int(camera_id)
                self._is_video = False
                self._path = None
            else:
                # treat as path
                self._path = str(camera_id)
                self.camera_id = self._path
                self._is_video = True
        except Exception:
            self._path = str(camera_id)
            self.camera_id = self._path
            self._is_video = True
        self._running = False
        self._cap = None
        # Backpressure gate. The capture thread hands over one frame at a time
        # and waits until the UI has finished processing it. Without this a
        # video file (read far faster than real time) floods the Qt event queue
        # and the whole interface stalls.
        self._ready = threading.Event()
        self._ready.set()

    def run(self):
        self._running = True
        root = Path(__file__).resolve().parent.parent
        cap_src = self._path if getattr(self, '_is_video', False) else self.camera_id
        # also try relative to root
        if getattr(self, '_is_video', False) and isinstance(cap_src, str) and not Path(cap_src).is_absolute():
            cand = root / cap_src
            if cand.exists():
                cap_src = str(cand)
        cap = cv2.VideoCapture(cap_src)
        self._cap = cap
        if not getattr(self, '_is_video', False):
            # Keep only the freshest frame buffered so live feeds stay current.
            try:
                cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
            except Exception:
                pass
        if not cap.isOpened():
            msg = f"Cannot open source: {cap_src}"
            if not getattr(self, '_is_video', False):
                msg = f"No stream on camera {self.camera_id}. Check the device id or that no other application is holding it."
            self.failed.emit(msg)
            self._running = False
            self.ended.emit()
            return

        # Video files decode far faster than real time. Pace them to their own
        # frame rate so playback looks natural instead of pinning the CPU.
        is_video = getattr(self, '_is_video', False)
        frame_interval = 0.0
        if is_video:
            native_fps = cap.get(cv2.CAP_PROP_FPS) or 0.0
            if native_fps <= 0.0 or native_fps > 240.0:
                native_fps = 30.0
            frame_interval = 1.0 / native_fps

        index = 0
        window_s, window_n = time.monotonic(), 0
        fps = 0.0
        next_t = time.monotonic()
        try:
            while self._running:
                ok, frame = cap.read()
                if not ok or frame is None:
                    if is_video:
                        # loop video
                        cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                        ok, frame = cap.read()
                        if not ok or frame is None:
                            self.failed.emit(f"Video ended and could not loop: {cap_src}")
                            break
                    else:
                        self.failed.emit(f"Camera {self.camera_id} stopped delivering frames.")
                        break

                raw = frame
                if is_video:
                    frame = self._limit_video_resolution(frame)

                index += 1
                window_n += 1
                now = time.monotonic()
                elapsed = now - window_s
                if elapsed >= 0.5:
                    fps = window_n / elapsed
                    window_s, window_n = now, 0

                # Wait for the UI to release the previous frame before emitting
                # the next one, so the event queue never backs up.
                if self._running:
                    self._ready.wait(1.0)
                if not self._running:
                    break
                self._ready.clear()
                self.frameReady.emit(
                    Frame(self.source, self.camera_id, frame, fps, index, raw_bgr=raw)
                )

                if frame_interval:
                    next_t += frame_interval
                    delay = next_t - time.monotonic()
                    if delay > 0:
                        time.sleep(delay)
                    else:
                        next_t = time.monotonic()
        finally:
            self._release()
            self.ended.emit()

    def mark_ready(self):
        """Called by the UI once it has finished processing a frame."""
        self._ready.set()

    @staticmethod
    def _limit_video_resolution(frame):
        """Downscale a video frame that exceeds the configured maximum size."""
        h, w = frame.shape[:2]
        if w <= MAX_VIDEO_WIDTH and h <= MAX_VIDEO_HEIGHT:
            return frame
        scale = min(MAX_VIDEO_WIDTH / float(w), MAX_VIDEO_HEIGHT / float(h))
        new_size = (max(1, int(round(w * scale))), max(1, int(round(h * scale))))
        return cv2.resize(frame, new_size, interpolation=cv2.INTER_AREA)

    def stop(self):
        self._running = False
        # Unblock a capture thread parked on the backpressure gate.
        self._ready.set()

    def _release(self):
        if self._cap is not None:
            try:
                self._cap.release()
            finally:
                self._cap = None


class StreamManager(QObject):
    """Owns the lifetime of one camera thread."""

    frameReady = Signal(object)
    statusChanged = Signal(str)
    failed = Signal(str)
    statsChanged = Signal(dict)

    def __init__(self, name, parent=None):
        super().__init__(parent)
        self.name = name
        self._thread: _CaptureThread | None = None
        self._camera_id: int | None = None
        self._last_frame: Frame | None = None

    @property
    def is_running(self):
        return self._thread is not None and self._thread.isRunning()

    @property
    def camera_id(self):
        return self._camera_id

    @property
    def last_frame(self):
        return self._last_frame

    def open(self, camera_id=None, video_path=None, path=None):
        self.close()
        src = path or video_path or camera_id
        if src is None:
            self.failed.emit("No input source provided")
            return
        self._camera_id = src
        self.statusChanged.emit("connecting")
        thread = _CaptureThread(self.name, src, self)
        thread.frameReady.connect(self._on_frame)
        thread.failed.connect(self._on_failed)
        thread.ended.connect(self._on_ended)
        self._thread = thread
        thread.start()

    def close(self):
        thread = self._thread
        if thread is None:
            return
        thread.stop()
        if not thread.wait(2500):
            thread.terminate()
            thread.wait(500)
        self._thread = None
        self.statusChanged.emit("stopped")

    def _on_frame(self, frame: Frame):
        self._last_frame = frame
        # This slot runs on the UI thread. The connected tab consumes the frame
        # synchronously inside this emit, so we can release the capture thread
        # for the next frame as soon as it returns.
        self.frameReady.emit(frame)
        self.statsChanged.emit(
            {
                "fps": frame.fps,
                "width": frame.size[0],
                "height": frame.size[1],
                "frames": frame.index,
            }
        )
        thread = self._thread
        if thread is not None:
            thread.mark_ready()

    def _on_failed(self, message):
        self.failed.emit(message)

    def _on_ended(self):
        self.statusChanged.emit("stopped")