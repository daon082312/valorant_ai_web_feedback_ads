from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Callable

import cv2
import mss
import numpy as np


@dataclass(slots=True)
class FrameSample:
    timestamp: float
    frame_bgr: np.ndarray
    motion_score: float


class ScreenCapture:
    def __init__(
        self,
        on_frame: Callable[[FrameSample], None],
        fps: int = 12,
        width: int = 960,
        monitor_index: int = 1,
    ):
        self.on_frame = on_frame
        self.fps = max(2, min(60, int(fps)))
        self.width = max(320, int(width))
        self.monitor_index = max(1, int(monitor_index))
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    def _motion_score(self, previous_gray: np.ndarray | None, gray: np.ndarray) -> float:
        if previous_gray is None or previous_gray.shape != gray.shape:
            return 0.0
        diff = cv2.absdiff(previous_gray, gray)
        return float(np.mean(diff))

    def _run(self) -> None:
        delay = 1.0 / self.fps
        previous_gray = None
        with mss.mss() as sct:
            monitors = sct.monitors
            index = self.monitor_index if self.monitor_index < len(monitors) else 1
            monitor = monitors[index]

            while not self._stop.is_set():
                started = time.perf_counter()
                raw = np.asarray(sct.grab(monitor), dtype=np.uint8)
                frame = cv2.cvtColor(raw, cv2.COLOR_BGRA2BGR)

                if frame.shape[1] != self.width:
                    scale = self.width / frame.shape[1]
                    height = max(1, int(frame.shape[0] * scale))
                    frame = cv2.resize(frame, (self.width, height), interpolation=cv2.INTER_AREA)

                gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                h, w = gray.shape[:2]
                y1, y2 = int(h * 0.12), int(h * 0.88)
                x1, x2 = int(w * 0.12), int(w * 0.88)
                center_gray = gray[y1:y2, x1:x2]
                score = self._motion_score(previous_gray, center_gray)
                previous_gray = center_gray.copy()

                self.on_frame(FrameSample(time.time(), frame, score))

                elapsed = time.perf_counter() - started
                self._stop.wait(max(0.0, delay - elapsed))

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True, name="ScreenCapture")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)
        self._thread = None
