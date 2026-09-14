from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass

import cv2
import numpy as np

from skill_usage_detector import DEFAULT_SLOT_ROIS


DEFAULT_AMMO_ROI = {"x": 0.875, "y": 0.865, "w": 0.115, "h": 0.12}


@dataclass(slots=True)
class ModeState:
    mode: str
    confidence: float
    skill_hud_active_slots: int
    gameplay_hud_visible: bool
    reason: str


class BrawlModeDetector:
    """Very conservative HUD-only detector for deathmatch/brawl-style no-ability play.

    False brawl classification is intentionally treated as worse than missing a
    brawl classification. Once any plausible skill HUD is seen in a session, auto
    mode stays normal for that session. Manual override remains available.
    """

    def __init__(self, config: dict):
        self._lock = threading.Lock()
        self._history: deque[tuple[float, bool, int]] = deque(maxlen=320)
        self._last_process_ts = 0.0
        self._state = ModeState("unknown", 0.0, 0, False, "warming_up")
        self._ever_skill_hud_seen = False
        self.update_config(config)

    def update_config(self, config: dict) -> None:
        self.config = config
        self.enabled = bool(config.get("game_mode_auto_detection", True))
        override = str(config.get("game_mode_override", "auto")).strip().lower()
        self.override = override if override in {"auto", "normal", "brawl"} else "auto"
        self.fps = max(1.0, min(8.0, float(config.get("game_mode_detection_fps", 3))))
        self.brawl_seconds = max(8.0, min(25.0, float(config.get("game_mode_brawl_confirm_seconds", 12.0))))
        self.normal_seconds = max(1.0, min(8.0, float(config.get("game_mode_normal_confirm_seconds", 1.5))))
        self.min_samples = max(12, int(config.get("game_mode_min_samples", 24)))
        self.skill_rois = config.get("skill_hud_rois") or DEFAULT_SLOT_ROIS
        self.ammo_roi = config.get("ammo_hud_roi") or DEFAULT_AMMO_ROI

    def clear(self) -> None:
        with self._lock:
            self._history.clear()
            self._state = ModeState("unknown", 0.0, 0, False, "warming_up")
            self._ever_skill_hud_seen = False
        self._last_process_ts = 0.0

    @property
    def state(self) -> ModeState:
        with self._lock:
            s = self._state
            return ModeState(s.mode, s.confidence, s.skill_hud_active_slots, s.gameplay_hud_visible, s.reason)

    @staticmethod
    def _crop(frame: np.ndarray, roi: dict) -> np.ndarray | None:
        h, w = frame.shape[:2]
        try:
            x1 = max(0, min(w - 1, int(w * float(roi["x"]))))
            y1 = max(0, min(h - 1, int(h * float(roi["y"]))))
            x2 = max(x1 + 1, min(w, int(w * (float(roi["x"]) + float(roi["w"])))))
            y2 = max(y1 + 1, min(h, int(h * (float(roi["y"]) + float(roi["h"])))))
        except Exception:
            return None
        crop = frame[y1:y2, x1:x2]
        return crop if crop.size else None

    @staticmethod
    def _hud_content_score(crop: np.ndarray | None) -> float:
        if crop is None or crop.size == 0:
            return 0.0
        gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
        if gray.shape[0] < 8 or gray.shape[1] < 8:
            return 0.0
        gray = cv2.resize(gray, (72, 72), interpolation=cv2.INTER_AREA)
        contrast = float(np.std(gray))
        edges = cv2.Canny(gray, 55, 130)
        edge_ratio = float(np.count_nonzero(edges)) / float(edges.size)
        bright_ratio = float(np.count_nonzero(gray >= 165)) / float(gray.size)
        return contrast * 0.70 + edge_ratio * 115.0 + bright_ratio * 24.0

    def _sample(self, frame: np.ndarray) -> tuple[bool, int]:
        ammo_score = self._hud_content_score(self._crop(frame, self.ammo_roi))
        gameplay_visible = ammo_score >= 12.0
        active_slots = 0
        for slot, default_roi in DEFAULT_SLOT_ROIS.items():
            roi = self.skill_rois.get(slot, default_roi) if isinstance(self.skill_rois, dict) else default_roi
            score = self._hud_content_score(self._crop(frame, roi))
            if score >= 13.0:
                active_slots += 1
        return gameplay_visible, active_slots

    def process(self, frame_bgr: np.ndarray, timestamp: float | None = None) -> ModeState:
        ts = float(timestamp or time.time())
        if self.override in {"normal", "brawl"}:
            state = ModeState(self.override, 1.0, 0, True, "manual_override")
            with self._lock:
                self._state = state
            return state
        if not self.enabled or frame_bgr is None or frame_bgr.size == 0:
            return self.state
        if ts - self._last_process_ts < 1.0 / self.fps:
            return self.state
        self._last_process_ts = ts

        gameplay_visible, active_slots = self._sample(frame_bgr)
        with self._lock:
            if gameplay_visible and active_slots >= 1:
                self._ever_skill_hud_seen = True
            self._history.append((ts, gameplay_visible, active_slots))
            cutoff = ts - max(self.brawl_seconds, self.normal_seconds) - 1.0
            while self._history and self._history[0][0] < cutoff:
                self._history.popleft()
            history = list(self._history)
            previous = self._state.mode
            ever_skill = self._ever_skill_hud_seen

        valid = [(t, slots) for t, visible, slots in history if visible]
        if len(valid) < max(8, self.min_samples // 2):
            state = ModeState(previous if previous != "unknown" else "unknown", 0.0, active_slots, gameplay_visible, "warming_up")
        elif ever_skill:
            state = ModeState("normal", 0.99, active_slots, gameplay_visible, "ability_hud_seen")
        else:
            normal_cut = ts - self.normal_seconds
            normal_samples = [slots for t, slots in valid if t >= normal_cut]
            normal_ratio = (
                sum(1 for slots in normal_samples if slots >= 1) / len(normal_samples)
                if len(normal_samples) >= max(4, int(self.fps * self.normal_seconds * 0.6)) else 0.0
            )
            if normal_ratio >= 0.45:
                state = ModeState("normal", round(normal_ratio, 3), active_slots, gameplay_visible, "ability_hud_present")
            else:
                brawl_cut = ts - self.brawl_seconds
                brawl_valid = [(t, slots) for t, slots in valid if t >= brawl_cut]
                required = max(self.min_samples, int(self.fps * self.brawl_seconds * 0.72))
                span = (brawl_valid[-1][0] - brawl_valid[0][0]) if len(brawl_valid) >= 2 else 0.0
                brawl_ratio = (
                    sum(1 for _, slots in brawl_valid if slots == 0) / len(brawl_valid)
                    if len(brawl_valid) >= required else 0.0
                )
                if len(brawl_valid) >= required and span >= self.brawl_seconds * 0.80 and brawl_ratio >= 0.96:
                    state = ModeState("brawl", round(brawl_ratio, 3), active_slots, gameplay_visible, "sustained_no_ability_hud")
                else:
                    state = ModeState("normal" if previous in {"normal", "unknown"} else previous, max(normal_ratio, brawl_ratio), active_slots, gameplay_visible, "conservative_normal")

        with self._lock:
            self._state = state
        return state
