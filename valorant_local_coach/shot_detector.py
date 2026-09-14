from __future__ import annotations

import threading
import time
from dataclasses import dataclass

import cv2
import numpy as np


DEFAULT_AMMO_ROI = {"x": 0.865, "y": 0.835, "w": 0.115, "h": 0.125}


@dataclass(slots=True)
class ShotConfirmation:
    timestamp: float
    confidence: float
    change_score: float


class AmmoShotDetector:
    """Confirm actual firing from ammo-HUD state changes.

    Mouse input is only firing intent. A round is counted only when the bottom-right
    ammo HUD also changes while firing is held or immediately after a click.
    """

    def __init__(self, config: dict):
        self._lock = threading.Lock()
        self._previous: np.ndarray | None = None
        self._last_process_ts = 0.0
        self._last_shot_ts = 0.0
        self._armed_until = 0.0
        self._noise = 0.15
        self.update_config(config)

    def update_config(self, config: dict) -> None:
        self.config = config
        self.enabled = bool(config.get("shot_hud_detection", True))
        self.fps = max(6.0, min(30.0, float(config.get("shot_hud_detection_fps", 12))))
        self.min_change = max(0.10, float(config.get("shot_hud_change_threshold", 0.55)))
        self.max_change = max(self.min_change + 0.5, float(config.get("shot_hud_max_change", 18.0)))
        self.cooldown = max(0.035, min(0.25, float(config.get("shot_hud_event_cooldown_seconds", 0.055))))
        self.arm_window = max(0.15, min(1.0, float(config.get("shot_hud_candidate_window_seconds", 0.45))))
        roi = config.get("ammo_hud_roi") or DEFAULT_AMMO_ROI
        self.roi = {k: float(roi.get(k, DEFAULT_AMMO_ROI[k])) for k in ("x", "y", "w", "h")}

    def clear(self) -> None:
        with self._lock:
            self._previous = None; self._last_shot_ts = 0.0; self._armed_until = 0.0; self._noise = 0.15
        self._last_process_ts = 0.0

    def register_candidate(self, timestamp: float | None = None) -> None:
        ts = float(timestamp or time.time())
        with self._lock:
            self._armed_until = max(self._armed_until, ts + self.arm_window)

    @staticmethod
    def _crop_binary(frame: np.ndarray, roi: dict) -> np.ndarray | None:
        h, w = frame.shape[:2]
        x1 = max(0, min(w - 1, int(w * roi["x"]))); y1 = max(0, min(h - 1, int(h * roi["y"])))
        x2 = max(x1 + 1, min(w, int(w * (roi["x"] + roi["w"])))); y2 = max(y1 + 1, min(h, int(h * (roi["y"] + roi["h"]))))
        crop = frame[y1:y2, x1:x2]
        if crop.size == 0: return None
        gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY); gray = cv2.resize(gray, (112, 64), interpolation=cv2.INTER_AREA); gray = cv2.GaussianBlur(gray, (3, 3), 0)
        return cv2.threshold(gray, 155, 255, cv2.THRESH_BINARY)[1]

    def process(self, frame_bgr: np.ndarray, timestamp: float | None, *, firing_held: bool) -> list[ShotConfirmation]:
        ts = float(timestamp or time.time())
        if not self.enabled or frame_bgr is None or frame_bgr.size == 0: return []
        if ts - self._last_process_ts < 1.0 / self.fps: return []
        self._last_process_ts = ts
        current = self._crop_binary(frame_bgr, self.roi)
        if current is None: return []
        with self._lock:
            previous = self._previous; self._previous = current; armed = bool(firing_held or ts <= self._armed_until); last_shot = self._last_shot_ts; noise = self._noise
        if previous is None or previous.shape != current.shape: return []
        xor = cv2.bitwise_xor(previous, current); change_score = float(cv2.countNonZero(xor)) * 100.0 / float(xor.size)
        if not armed:
            with self._lock: self._noise = self._noise * 0.95 + min(change_score, 3.0) * 0.05
            return []
        threshold = max(self.min_change, noise * 3.0 + 0.15)
        if ts - last_shot < self.cooldown or change_score < threshold or change_score > self.max_change: return []
        confidence = max(0.55, min(0.99, 0.58 + (change_score - threshold) / max(2.5, threshold * 5.0)))
        with self._lock:
            self._last_shot_ts = ts; self._noise = max(0.10, self._noise * 0.82)
        return [ShotConfirmation(ts, round(confidence, 3), round(change_score, 3))]
