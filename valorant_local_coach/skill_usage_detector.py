from __future__ import annotations

import threading
import time

import cv2
import numpy as np

from input_tracker import SkillEvent


DEFAULT_SLOT_ROIS = {
    "skill1": {"x": 0.365, "y": 0.875, "w": 0.060, "h": 0.105},
    "skill2": {"x": 0.430, "y": 0.875, "w": 0.060, "h": 0.105},
    "skill3": {"x": 0.495, "y": 0.875, "w": 0.060, "h": 0.105},
    "ultimate": {"x": 0.560, "y": 0.875, "w": 0.065, "h": 0.105},
}


class SkillUsageDetector:
    """Confirm ability use from a persistent visual change in the ability HUD."""

    def __init__(self, config: dict):
        self._lock = threading.Lock()
        self._pending: dict[str, SkillEvent] = {}
        self._previous: dict[str, np.ndarray] = {}
        self._baseline: dict[str, np.ndarray] = {}
        self._evidence: dict[str, int] = {}
        self._noise: dict[str, float] = {}
        self._last_confirmed: dict[str, float] = {}
        self._last_process_ts = 0.0
        self.update_config(config)

    def update_config(self, config: dict) -> None:
        self.config = config
        self.enabled = bool(config.get("skill_hud_use_detection", True))
        self.fps = max(2.0, min(15.0, float(config.get("skill_hud_detection_fps", 8))))
        self.min_change = max(2.0, float(config.get("skill_hud_change_threshold", 8.0)))
        self.confirm_window = max(0.25, min(2.0, float(config.get("skill_hud_confirm_window_seconds", 1.15))))
        self.cooldown = max(0.5, min(5.0, float(config.get("skill_hud_event_cooldown_seconds", 1.25))))
        rois = config.get("skill_hud_rois") or DEFAULT_SLOT_ROIS
        self.rois = {}
        for slot, default in DEFAULT_SLOT_ROIS.items():
            item = rois.get(slot, default) if isinstance(rois, dict) else default
            self.rois[slot] = {k: float(item.get(k, default[k])) for k in ("x", "y", "w", "h")}

    def clear(self) -> None:
        with self._lock:
            self._pending.clear(); self._previous.clear(); self._baseline.clear(); self._evidence.clear(); self._noise.clear(); self._last_confirmed.clear()
        self._last_process_ts = 0.0

    def register_candidate(self, event: SkillEvent) -> None:
        if not self.enabled:
            return
        with self._lock:
            self._pending[event.slot_id] = event
            prev = self._previous.get(event.slot_id)
            if prev is not None:
                self._baseline[event.slot_id] = prev.copy()
            else:
                self._baseline.pop(event.slot_id, None)
            self._evidence[event.slot_id] = 0

    @staticmethod
    def _crop(frame: np.ndarray, roi: dict) -> np.ndarray | None:
        h, w = frame.shape[:2]
        x1 = max(0, min(w - 1, int(w * roi["x"]))); y1 = max(0, min(h - 1, int(h * roi["y"])))
        x2 = max(x1 + 1, min(w, int(w * (roi["x"] + roi["w"])))); y2 = max(y1 + 1, min(h, int(h * (roi["y"] + roi["h"]))))
        crop = frame[y1:y2, x1:x2]
        if crop.size == 0: return None
        gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY); gray = cv2.resize(gray, (48, 48), interpolation=cv2.INTER_AREA)
        return cv2.GaussianBlur(gray, (3, 3), 0)

    def process(self, frame_bgr: np.ndarray, timestamp: float | None = None) -> list[SkillEvent]:
        ts = float(timestamp or time.time())
        if not self.enabled or frame_bgr is None or frame_bgr.size == 0: return []
        if ts - self._last_process_ts < 1.0 / self.fps: return []
        self._last_process_ts = ts
        confirmed: list[SkillEvent] = []
        current = {slot: crop for slot, roi in self.rois.items() if (crop := self._crop(frame_bgr, roi)) is not None}
        with self._lock:
            stale = [slot for slot, event in self._pending.items() if ts - event.timestamp > self.confirm_window]
            for slot in stale:
                self._pending.pop(slot, None); self._baseline.pop(slot, None); self._evidence.pop(slot, None)
            for slot, crop in current.items():
                prev = self._previous.get(slot); self._previous[slot] = crop
                if prev is None or prev.shape != crop.shape: continue
                frame_diff = float(np.mean(cv2.absdiff(prev, crop))); noise = float(self._noise.get(slot, 1.5)); pending = self._pending.get(slot); last = float(self._last_confirmed.get(slot, 0.0))
                if pending is None:
                    self._noise[slot] = noise * 0.94 + frame_diff * 0.06; continue
                age = ts - pending.timestamp; threshold = max(self.min_change, noise * 2.7 + 1.0)
                if age < 0.08 or age > self.confirm_window or ts - last < self.cooldown: continue
                baseline = self._baseline.get(slot)
                if baseline is None or baseline.shape != crop.shape:
                    baseline = prev; self._baseline[slot] = baseline.copy()
                diff = float(np.mean(cv2.absdiff(baseline, crop)))
                if diff >= threshold: self._evidence[slot] = self._evidence.get(slot, 0) + 1
                else: self._evidence[slot] = 0; continue
                if self._evidence.get(slot, 0) < 2: continue
                confidence = max(0.55, min(0.99, 0.55 + (diff - threshold) / max(10.0, threshold * 3.0)))
                confirmed.append(SkillEvent(timestamp=ts, slot_id=slot, label=pending.label, binding=pending.binding, moving=pending.moving, walking=pending.walking, crouching=pending.crouching, keys=pending.keys, source="hud_confirmed", confidence=round(confidence, 3)))
                self._last_confirmed[slot] = ts; self._pending.pop(slot, None); self._baseline.pop(slot, None); self._evidence.pop(slot, None); self._noise[slot] = max(1.0, noise * 0.75)
        return confirmed
