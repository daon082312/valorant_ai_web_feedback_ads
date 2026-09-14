from __future__ import annotations

from collections import deque
from dataclasses import dataclass
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


class VideoCombatDetectorV3:
    """Conservative offline self K/D detector for VALORANT video review.

    Kill: requires a short, synchronized transient in nested lower-center self-kill
    confirmation ROIs. The top-right global kill feed is never inspected.

    Death: uses a state machine: confirmed alive ammo HUD -> ammo HUD disappears ->
    right-side combat report persists. Once dead, another death cannot be counted
    until the alive ammo HUD has returned for several samples.

    The detector intentionally prefers missed events over false positives. The UI
    supports manual correction of detected K/D after analysis.
    """

    KILL_OUTER = (0.447, 0.585, 0.106, 0.170)
    KILL_CORE = (0.472, 0.628, 0.056, 0.092)
    DEATH_REPORT = (0.715, 0.165, 0.275, 0.670)
    AMMO = (0.860, 0.850, 0.135, 0.130)

    def __init__(self, config: dict):
        self.config = config
        self.kill_cooldown = max(1.0, float(config.get("kill_event_cooldown_seconds", 1.6)))
        self.death_cooldown = max(3.0, float(config.get("death_event_cooldown_seconds", 4.0)))
        self._outer_hist: deque[float] = deque(maxlen=72)
        self._core_hist: deque[float] = deque(maxlen=72)
        self._report_hist: deque[float] = deque(maxlen=72)
        self._ammo_hist: deque[float] = deque(maxlen=72)
        self._events: list[VideoCombatEvent] = []
        self._last_kill = -999.0
        self._last_death = -999.0
        self._kill_evidence = 0
        self._kill_latched = False
        self._alive_confirmed = False
        self._alive_run = 0
        self._ammo_missing_run = 0
        self._death_report_run = 0
        self._dead_latched = False

    @staticmethod
    def _crop(frame: np.ndarray, roi, size=(88, 72)) -> np.ndarray | None:
        h, w = frame.shape[:2]
        x, y, rw, rh = roi
        x1 = max(0, min(w - 1, int(round(w * x))))
        y1 = max(0, min(h - 1, int(round(h * y))))
        x2 = max(x1 + 1, min(w, int(round(w * (x + rw)))))
        y2 = max(y1 + 1, min(h, int(round(h * (y + rh)))))
        crop = frame[y1:y2, x1:x2]
        if crop.size == 0:
            return None
        gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
        return cv2.GaussianBlur(cv2.resize(gray, size, interpolation=cv2.INTER_AREA), (3, 3), 0)

    @staticmethod
    def _content(gray: np.ndarray | None) -> float:
        if gray is None or gray.size == 0:
            return 0.0
        edges = cv2.Canny(gray, 50, 130)
        edge_ratio = float(np.count_nonzero(edges)) / float(edges.size)
        contrast = float(np.std(gray))
        bright = float(np.count_nonzero(gray >= 175)) / float(gray.size)
        return contrast * 0.52 + edge_ratio * 132.0 + bright * 24.0

    @staticmethod
    def _ammo_digit_score(gray: np.ndarray | None) -> float:
        if gray is None or gray.size == 0:
            return 0.0
        _, bw = cv2.threshold(gray, 165, 255, cv2.THRESH_BINARY)
        n, _, stats, _ = cv2.connectedComponentsWithStats(bw, 8)
        hits = 0
        area_sum = 0
        H, W = gray.shape[:2]
        for i in range(1, n):
            _x, _y, cw, ch, area = [int(v) for v in stats[i]]
            aspect = cw / max(1.0, float(ch))
            if 4 <= area <= max(900, int(H * W * 0.10)) and 2 <= cw <= max(32, int(W * 0.38)) and 7 <= ch <= max(48, int(H * 0.82)) and 0.08 <= aspect <= 1.25:
                hits += 1
                area_sum += area
        return float(min(5.0, hits + min(1.0, area_sum / 260.0)))

    @staticmethod
    def _robust_z(value: float, hist: deque[float], min_scale: float) -> float:
        if len(hist) < 10:
            return 0.0
        vals = list(hist)
        med = statistics.median(vals)
        mad = statistics.median(abs(x - med) for x in vals)
        scale = max(min_scale, 1.4826 * mad)
        return (value - med) / scale

    def process(self, frame: np.ndarray, video_seconds: float, *, recent_fire: bool = False) -> list[VideoCombatEvent]:
        t = float(video_seconds)
        out: list[VideoCombatEvent] = []
        outer = self._content(self._crop(frame, self.KILL_OUTER, (88, 80)))
        core = self._content(self._crop(frame, self.KILL_CORE, (64, 64)))
        report = self._content(self._crop(frame, self.DEATH_REPORT, (120, 132)))
        ammo_crop = self._crop(frame, self.AMMO, (104, 64))
        ammo = self._ammo_digit_score(ammo_crop)
        outer_z = self._robust_z(outer, self._outer_hist, 1.8)
        core_z = self._robust_z(core, self._core_hist, 1.6)
        report_z = self._robust_z(report, self._report_hist, 2.2)

        ammo_baseline = statistics.median(self._ammo_hist) if len(self._ammo_hist) >= 10 else ammo
        alive_now = ammo >= max(1.4, ammo_baseline * 0.48)
        if alive_now:
            self._alive_run += 1
            self._ammo_missing_run = 0
            self._death_report_run = 0
            if self._alive_run >= 4:
                self._alive_confirmed = True
                self._dead_latched = False
        else:
            self._alive_run = 0
            if self._alive_confirmed:
                self._ammo_missing_run += 1

        report_candidate = (
            self._alive_confirmed
            and not alive_now
            and self._ammo_missing_run >= 3
            and len(self._report_hist) >= 10
            and report_z >= 3.0
            and report >= 12.0
        )
        if report_candidate:
            self._death_report_run += 1
        elif not self._dead_latched:
            self._death_report_run = max(0, self._death_report_run - 1)

        if (not self._dead_latched and self._death_report_run >= 4
                and t - self._last_death >= self.death_cooldown):
            conf = min(0.98, 0.72 + min(0.20, max(0.0, report_z - 3.0) * 0.055))
            event = VideoCombatEvent(round(t, 2), "death", round(conf, 3), round(report_z, 2), "self_death_alive_to_report_v3")
            self._events.append(event); out.append(event)
            self._last_death = t
            self._dead_latched = True
            self._alive_confirmed = False
            self._death_report_run = 0

        outer_gate = 3.15 if recent_fire else 3.75
        core_gate = 2.85 if recent_fire else 3.45
        kill_candidate = (
            not self._dead_latched
            and t - self._last_kill >= self.kill_cooldown
            and len(self._outer_hist) >= 10
            and len(self._core_hist) >= 10
            and outer_z >= outer_gate
            and core_z >= core_gate
            and outer >= 13.0
            and core >= 12.0
            and core / max(1.0, outer) >= 1.28
        )
        if kill_candidate:
            self._kill_evidence += 1
        else:
            self._kill_evidence = max(0, self._kill_evidence - 1)

        if self._kill_evidence >= 2 and not self._kill_latched:
            fire_bonus = 0.05 if recent_fire else 0.0
            conf = min(0.98, 0.69 + fire_bonus + min(0.13, max(0.0, outer_z - outer_gate) * 0.035) + min(0.10, max(0.0, core_z - core_gate) * 0.03))
            event = VideoCombatEvent(round(t, 2), "kill", round(conf, 3), round(min(outer_z, core_z), 2), "self_kill_nested_transient_v3")
            self._events.append(event); out.append(event)
            self._last_kill = t
            self._kill_latched = True
            self._kill_evidence = 0
        if self._kill_latched and outer_z < 1.0 and core_z < 1.0:
            self._kill_latched = False

        if not self._kill_latched:
            self._outer_hist.append(outer)
            self._core_hist.append(core)
        if not self._dead_latched:
            self._report_hist.append(report)
        if alive_now or not self._alive_confirmed:
            self._ammo_hist.append(ammo)
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
                 "score": e.score, "source": e.source, "manual": False}
                for e in self._events
            ],
            "detector": "offline_self_hud_state_machine_v3",
            "manual_correctable": True,
        }
