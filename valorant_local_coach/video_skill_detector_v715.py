from __future__ import annotations

from collections import Counter

import cv2
import numpy as np

from video_review import DEFAULT_SLOT_ROIS


class RobustUtilityDetectorV715:
    """More tolerant offline ability-HUD change detector."""

    def __init__(self, config: dict):
        self.rois = config.get("skill_hud_rois") or DEFAULT_SLOT_ROIS
        configured = float(config.get("video_skill_change_threshold", 11.0))
        self.threshold = max(5.0, configured * 0.64)
        self.cooldown = max(0.65, min(1.5, float(config.get("video_skill_cooldown_seconds", 1.4)) * 0.72))
        self._baseline: dict[str, np.ndarray] = {}
        self._previous: dict[str, np.ndarray] = {}
        self._noise: dict[str, float] = {}
        self._streak: dict[str, int] = Counter()
        self._last_event: dict[str, float] = {}
        self._last_global_event = -999.0

    @staticmethod
    def _crop(frame: np.ndarray, roi: dict) -> np.ndarray | None:
        h, w = frame.shape[:2]
        x1 = max(0, min(w - 1, int(w * float(roi.get("x", 0.0)))))
        y1 = max(0, min(h - 1, int(h * float(roi.get("y", 0.0)))))
        x2 = max(x1 + 1, min(w, int(w * (float(roi.get("x", 0.0)) + float(roi.get("w", 0.0))))))
        y2 = max(y1 + 1, min(h, int(h * (float(roi.get("y", 0.0)) + float(roi.get("h", 0.0))))))
        crop = frame[y1:y2, x1:x2]
        if crop.size == 0:
            return None
        gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
        gray = cv2.resize(gray, (64, 44), interpolation=cv2.INTER_AREA)
        return cv2.GaussianBlur(gray, (3, 3), 0)

    @staticmethod
    def _texture(crop: np.ndarray) -> float:
        edges = cv2.Canny(crop, 45, 120)
        return float(np.std(crop)) * 0.72 + float(np.count_nonzero(edges)) / max(1, edges.size) * 100.0

    def process(self, frame: np.ndarray, video_seconds: float) -> list[dict]:
        events: list[dict] = []
        for slot, roi in self.rois.items():
            crop = self._crop(frame, roi)
            if crop is None:
                continue
            base = self._baseline.get(slot)
            prev = self._previous.get(slot)
            if base is None or prev is None:
                self._baseline[slot] = crop.astype(np.float32)
                self._previous[slot] = crop.copy()
                self._noise[slot] = 1.5
                continue

            direct = float(np.mean(cv2.absdiff(prev, crop)))
            base_u8 = np.clip(base, 0, 255).astype(np.uint8)
            baseline_diff = float(np.mean(cv2.absdiff(base_u8, crop)))
            noise = float(self._noise.get(slot, 1.5))
            threshold = max(self.threshold, noise * 2.15 + 1.0)
            texture = self._texture(crop)
            hud_visible = texture >= 8.0
            moderate = hud_visible and direct >= threshold and baseline_diff >= threshold * 0.82
            strong = hud_visible and ((direct >= threshold * 1.42 and baseline_diff >= threshold * 0.72) or baseline_diff >= threshold * 1.68)

            self._streak[slot] = int(self._streak.get(slot, 0)) + 1 if moderate else 0
            last = float(self._last_event.get(slot, -999.0))
            ready = video_seconds - last >= self.cooldown and video_seconds - self._last_global_event >= 0.20
            if ready and (strong or self._streak[slot] >= 2):
                strength = max(direct, baseline_diff)
                confidence = 0.58 + max(0.0, strength - threshold) / max(18.0, threshold * 2.3)
                events.append({
                    "slot_id": slot,
                    "video_seconds": round(float(video_seconds), 2),
                    "confidence": round(max(0.52, min(0.96, confidence)), 3),
                    "change_score": round(strength, 2),
                    "direct_change": round(direct, 2),
                    "baseline_change": round(baseline_diff, 2),
                    "source": "ability_hud_transition_v715",
                })
                self._last_event[slot] = float(video_seconds)
                self._last_global_event = float(video_seconds)
                self._baseline[slot] = crop.astype(np.float32)
                self._streak[slot] = 0
                self._noise[slot] = max(1.0, noise * 0.78)
            else:
                self._noise[slot] = noise * 0.94 + min(direct, 7.0) * 0.06
                cv2.accumulateWeighted(crop.astype(np.float32), self._baseline[slot], 0.035)
            self._previous[slot] = crop.copy()
        return events
