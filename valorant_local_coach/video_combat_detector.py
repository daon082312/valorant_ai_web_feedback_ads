from __future__ import annotations

from dataclasses import dataclass
from collections import deque
import statistics

import cv2
import numpy as np


@dataclass(slots=True)
class VideoCombatEvent:
    video_seconds: float
    kind: str
    confidence: float
    score: float
    source: str


class VideoCombatDetector:
    """Offline, video-calibrated detector for the local player's kill/death events.

    Unlike the live detector, this detector builds rolling baselines from the video
    itself. Kills are inferred from a transient local kill-confirm signal around the
    lower-center HUD. Deaths use an alive->combat-report transition and are counted
    once until the local alive HUD returns.
    """

    KILL_ROIS = (
        (0.455, 0.585, 0.090, 0.125),
        (0.455, 0.620, 0.090, 0.125),
        (0.455, 0.655, 0.090, 0.125),
    )

    def __init__(self, config: dict):
        self.config = config
        self.kill_cooldown = max(0.9, float(config.get("kill_event_cooldown_seconds", 1.4)))
        self.death_cooldown = max(2.0, float(config.get("death_event_cooldown_seconds", 3.0)))
        self._kill_history: deque[float] = deque(maxlen=48)
        self._report_history: deque[float] = deque(maxlen=48)
        self._ammo_history: deque[float] = deque(maxlen=48)
        self._events: list[VideoCombatEvent] = []
        self._last_kill = -999.0
        self._last_death = -999.0
        self._kill_active = False
        self._kill_evidence = 0
        self._dead_state = False
        self._death_evidence = 0
        self._alive_evidence = 0

    @staticmethod
    def _crop(frame: np.ndarray, roi, size=(80, 64)) -> np.ndarray | None:
        h, w = frame.shape[:2]
        x, y, rw, rh = roi
        x1 = max(0, min(w - 1, int(w * x)))
        y1 = max(0, min(h - 1, int(h * y)))
        x2 = max(x1 + 1, min(w, int(w * (x + rw))))
        y2 = max(y1 + 1, min(h, int(h * (y + rh))))
        crop = frame[y1:y2, x1:x2]
        if crop.size == 0:
            return None
        gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
        return cv2.resize(gray, size, interpolation=cv2.INTER_AREA)

    @staticmethod
    def _content(gray: np.ndarray | None) -> float:
        if gray is None or gray.size == 0:
            return 0.0
        blur = cv2.GaussianBlur(gray, (3, 3), 0)
        edges = cv2.Canny(blur, 55, 135)
        edge_ratio = float(np.count_nonzero(edges)) / float(edges.size)
        contrast = float(np.std(blur))
        bright = float(np.count_nonzero(blur >= 175)) / float(blur.size)
        return contrast * 0.55 + edge_ratio * 125.0 + bright * 28.0

    @staticmethod
    def _ammo_digit_score(gray: np.ndarray | None) -> float:
        """Score whether a crop looks like the local ammo-number HUD, not generic UI."""
        if gray is None or gray.size == 0:
            return 0.0
        g = cv2.GaussianBlur(gray, (3, 3), 0)
        _, bw = cv2.threshold(g, 175, 255, cv2.THRESH_BINARY)
        n, _, stats, _ = cv2.connectedComponentsWithStats(bw, 8)
        hits = 0
        for i in range(1, n):
            x, y, w, h, area = [int(v) for v in stats[i]]
            if 5 <= area <= 900 and 2 <= w <= 34 and 6 <= h <= 46 and 0.10 <= w / max(1.0, h) <= 1.25:
                hits += 1
        return float(min(4, hits))

    @staticmethod
    def _robust_z(value: float, history: deque[float], min_scale: float = 1.5) -> float:
        if len(history) < 8:
            return 0.0
        values = list(history)
        med = statistics.median(values)
        mad = statistics.median(abs(x - med) for x in values)
        scale = max(min_scale, 1.4826 * mad)
        return (value - med) / scale

    def process(self, frame: np.ndarray, video_seconds: float, *, recent_fire: bool = False) -> list[VideoCombatEvent]:
        out: list[VideoCombatEvent] = []

        kill_scores = [self._content(self._crop(frame, roi, (72, 72))) for roi in self.KILL_ROIS]
        kill_signal = max(kill_scores) if kill_scores else 0.0
        kill_z = self._robust_z(kill_signal, self._kill_history, 1.6)

        report = self._content(self._crop(frame, (0.70, 0.16, 0.29, 0.68), (112, 128)))
        ammo_crop = self._crop(frame, (0.865, 0.865, 0.125, 0.105), (96, 56))
        ammo = self._ammo_digit_score(ammo_crop)
        report_z = self._robust_z(report, self._report_history, 2.0)
        ammo_med = statistics.median(self._ammo_history) if len(self._ammo_history) >= 8 else ammo

        kill_candidate = (
            video_seconds - self._last_kill >= self.kill_cooldown
            and len(self._kill_history) >= 8
            and kill_z >= (3.0 if recent_fire else 3.6)
            and kill_signal >= 14.0
        )
        if kill_candidate:
            self._kill_evidence += 1
        else:
            self._kill_evidence = max(0, self._kill_evidence - 1)

        if self._kill_evidence >= 2 and not self._kill_active:
            conf = min(0.98, 0.63 + min(0.24, max(0.0, kill_z - 3.0) * 0.06) + (0.06 if recent_fire else 0.0))
            event = VideoCombatEvent(round(video_seconds, 2), "kill", round(conf, 3), round(kill_z, 2), "video_local_kill_confirm")
            self._events.append(event); out.append(event)
            self._last_kill = video_seconds
            self._kill_active = True
            self._kill_evidence = 0
        elif self._kill_active and kill_z < 1.1:
            self._kill_active = False

        alive_now = len(self._ammo_history) < 8 or ammo >= max(1.0, ammo_med * 0.50)
        if alive_now:
            self._alive_evidence += 1
            self._death_evidence = 0
            if self._alive_evidence >= 2:
                self._dead_state = False
        else:
            self._alive_evidence = 0
            if (not self._dead_state and video_seconds - self._last_death >= self.death_cooldown
                    and len(self._report_history) >= 8 and report_z >= 2.5 and report >= 10.0):
                self._death_evidence += 1
            else:
                self._death_evidence = max(0, self._death_evidence - 1)

        if self._death_evidence >= 2 and not self._dead_state:
            conf = min(0.98, 0.66 + min(0.26, max(0.0, report_z - 2.5) * 0.07))
            event = VideoCombatEvent(round(video_seconds, 2), "death", round(conf, 3), round(report_z, 2), "video_self_death_transition")
            self._events.append(event); out.append(event)
            self._last_death = video_seconds
            self._dead_state = True
            self._death_evidence = 0

        if not self._kill_active:
            self._kill_history.append(kill_signal)
        self._report_history.append(report)
        if alive_now or not self._dead_state:
            self._ammo_history.append(ammo)
        return out

    def summary(self) -> dict:
        kills = [e for e in self._events if e.kind == "kill"]
        deaths = [e for e in self._events if e.kind == "death"]
        return {
            "kills": len(kills),
            "deaths": len(deaths),
            "kd_ratio": round(len(kills) / max(1, len(deaths)), 2),
            "events": [
                {"video_seconds": e.video_seconds, "kind": e.kind, "confidence": e.confidence,
                 "score": e.score, "source": e.source}
                for e in self._events
            ],
            "detector": "offline_video_calibrated_self_hud_v2",
        }
