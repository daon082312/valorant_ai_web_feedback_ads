from __future__ import annotations

import threading
import time
from dataclasses import dataclass

import cv2
import numpy as np

try:
    cv2.setNumThreads(1)
    cv2.ocl.setUseOpenCL(False)
except Exception:
    pass


@dataclass(slots=True)
class MatchLifecycleState:
    phase: str
    confidence: float
    gameplay_hud_visible: bool
    end_screen_score: float
    reason: str


class MatchLifecycleDetector:
    """Low-cost, screen-only match-end detector.

    It deliberately waits for a sustained transition from live gameplay HUD to a
    large result/summary screen. Short round-end/death screens should recover to
    gameplay/buy HUD before the confirmation window expires.
    """

    def __init__(self, config: dict):
        self._lock = threading.Lock()
        self.update_config(config)
        self.clear()

    def update_config(self, config: dict) -> None:
        self.config = config
        self.enabled = bool(config.get("match_end_auto_detection", True))
        self.fps = max(1.0, min(5.0, float(config.get("match_end_detection_fps", 2))))
        self.confirm_seconds = max(4.0, min(15.0, float(config.get("match_end_confirm_seconds", 7.0))))
        self.result_threshold = max(12.0, float(config.get("match_end_result_threshold", 24.0)))

    def clear(self) -> None:
        self._last_process_ts = 0.0
        self._last_gameplay_hud_ts = 0.0
        self._ever_gameplay_hud = False
        self._end_evidence = 0
        self._state = MatchLifecycleState("unknown", 0.0, False, 0.0, "warming_up")

    @property
    def state(self) -> MatchLifecycleState:
        with self._lock:
            s = self._state
            return MatchLifecycleState(s.phase, s.confidence, s.gameplay_hud_visible, s.end_screen_score, s.reason)

    @staticmethod
    def _crop(frame: np.ndarray, x: float, y: float, w: float, h: float, size: tuple[int, int]) -> np.ndarray | None:
        fh, fw = frame.shape[:2]
        x1 = max(0, min(fw - 1, int(fw * x)))
        y1 = max(0, min(fh - 1, int(fh * y)))
        x2 = max(x1 + 1, min(fw, int(fw * (x + w))))
        y2 = max(y1 + 1, min(fh, int(fh * (y + h))))
        roi = frame[y1:y2, x1:x2]
        if roi.size == 0:
            return None
        gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
        return cv2.resize(gray, size, interpolation=cv2.INTER_AREA)

    @staticmethod
    def _content_score(gray: np.ndarray | None) -> float:
        if gray is None or gray.size == 0:
            return 0.0
        contrast = float(np.std(gray))
        edge = cv2.Canny(gray, 55, 135)
        edge_ratio = float(np.count_nonzero(edge)) / float(edge.size)
        bright = float(np.count_nonzero(gray >= 170)) / float(gray.size)
        dark = float(np.count_nonzero(gray <= 55)) / float(gray.size)
        return contrast * 0.55 + edge_ratio * 120.0 + bright * 22.0 + dark * 5.0

    def process(self, frame_bgr: np.ndarray, timestamp: float | None = None) -> MatchLifecycleState:
        ts = float(timestamp or time.time())
        if not self.enabled or frame_bgr is None or frame_bgr.size == 0:
            return self.state
        if ts - self._last_process_ts < 1.0 / self.fps:
            return self.state
        self._last_process_ts = ts

        ammo = self._crop(frame_bgr, 0.855, 0.835, 0.14, 0.16, (80, 64))
        ammo_score = self._content_score(ammo)
        gameplay_visible = ammo_score >= 15.0

        center = self._crop(frame_bgr, 0.17, 0.23, 0.66, 0.42, (180, 104))
        upper = self._crop(frame_bgr, 0.20, 0.05, 0.60, 0.20, (160, 60))
        center_score = self._content_score(center)
        upper_score = self._content_score(upper)
        result_score = center_score * 0.72 + upper_score * 0.28

        if gameplay_visible:
            self._ever_gameplay_hud = True
            self._last_gameplay_hud_ts = ts
            self._end_evidence = 0
            state = MatchLifecycleState("gameplay", 0.98, True, round(result_score, 2), "live_hud_visible")
        elif not self._ever_gameplay_hud:
            state = MatchLifecycleState("unknown", 0.0, False, round(result_score, 2), "no_gameplay_seen")
        else:
            absent_for = max(0.0, ts - self._last_gameplay_hud_ts)
            candidate = result_score >= self.result_threshold
            if candidate and absent_for >= 2.0:
                self._end_evidence += 1
            else:
                self._end_evidence = max(0, self._end_evidence - 1)

            required_samples = max(4, int(self.fps * self.confirm_seconds * 0.55))
            if absent_for >= self.confirm_seconds and self._end_evidence >= required_samples:
                conf = min(0.99, 0.72 + (result_score - self.result_threshold) / 80.0 + min(0.12, absent_for / 60.0))
                state = MatchLifecycleState("ended", round(max(0.72, conf), 3), False, round(result_score, 2), "sustained_post_match_screen")
            elif absent_for >= 2.0 and candidate:
                conf = min(0.90, 0.45 + absent_for / max(12.0, self.confirm_seconds * 2.0))
                state = MatchLifecycleState("ending", round(conf, 3), False, round(result_score, 2), "possible_result_screen")
            else:
                state = MatchLifecycleState("transition", 0.25, False, round(result_score, 2), "temporary_hud_absence")

        with self._lock:
            self._state = state
        return state
