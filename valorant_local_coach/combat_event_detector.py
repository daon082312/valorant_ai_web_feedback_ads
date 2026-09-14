from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass

import cv2
import numpy as np

try:
    cv2.setNumThreads(1)
    cv2.ocl.setUseOpenCL(False)
except Exception:
    pass


@dataclass(slots=True)
class CombatEvent:
    timestamp: float
    kind: str
    confidence: float
    score: float
    source: str = "self_hud"


class CombatEventDetector:
    """Conservative screen-only detector for *the local player's* kill/death.

    Kill: uses the local center kill-confirm marker. Recent gunfire is only a
    confidence boost, so ability/environmental credited kills can still count.
    Death: requires the local combat-report change together with disappearance of
    the local ammo/gameplay HUD after it had recently been visible.
    """

    def __init__(self, config: dict):
        self._lock = threading.Lock()
        self._events: deque[CombatEvent] = deque(maxlen=300)
        self.update_config(config)
        self.clear()

    def update_config(self, config: dict) -> None:
        self.config = config
        self.enabled = bool(config.get("combat_event_detection", True))
        self.fps = max(2.0, min(10.0, float(config.get("combat_event_fps", 4))))
        self.kill_threshold = max(3.0, float(config.get("kill_hud_change_threshold", 10.0)))
        self.death_threshold = max(3.0, float(config.get("death_hud_change_threshold", 13.0)))
        self.kill_cooldown = max(0.8, float(config.get("kill_event_cooldown_seconds", 1.6)))
        self.death_cooldown = max(2.0, float(config.get("death_event_cooldown_seconds", 4.0)))

    def clear(self) -> None:
        self._last_process_ts = 0.0
        self._kill_base = None
        self._kill_core_base = None
        self._death_base = None
        self._kill_noise = 1.5
        self._death_noise = 1.8
        self._kill_evidence = 0
        self._death_evidence = 0
        self._last_kill_ts = 0.0
        self._last_death_ts = 0.0
        self._last_alive_hud_ts = 0.0
        with self._lock:
            self._events.clear()

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
    def _change(a: np.ndarray | None, b: np.ndarray | None) -> float:
        if a is None or b is None or a.shape != b.shape:
            return 0.0
        return float(np.mean(cv2.absdiff(a, b)))

    @staticmethod
    def _edge_density(gray: np.ndarray | None) -> float:
        if gray is None:
            return 0.0
        edge = cv2.Canny(gray, 55, 130)
        return float(np.count_nonzero(edge)) / float(edge.size)

    @staticmethod
    def _hud_content(gray: np.ndarray | None) -> float:
        if gray is None or gray.size == 0:
            return 0.0
        contrast = float(np.std(gray))
        edge = cv2.Canny(gray, 55, 130)
        edge_ratio = float(np.count_nonzero(edge)) / float(edge.size)
        bright = float(np.count_nonzero(gray >= 165)) / float(gray.size)
        return contrast * 0.65 + edge_ratio * 100.0 + bright * 22.0

    def process(self, frame_bgr: np.ndarray, timestamp: float | None = None, *, recent_fire: bool = False) -> list[CombatEvent]:
        ts = float(timestamp or time.time())
        if not self.enabled or frame_bgr is None or frame_bgr.size == 0:
            return []
        if ts - self._last_process_ts < 1.0 / self.fps:
            return []
        self._last_process_ts = ts

        # Local player's center kill-confirm area; deliberately excludes top-right global killfeed.
        # Two nested ROIs make a generic HUD animation less likely to become a kill.
        kill = self._crop(frame_bgr, 0.455, 0.605, 0.090, 0.150, (72, 76))
        kill_core = self._crop(frame_bgr, 0.476, 0.640, 0.048, 0.082, (48, 48))
        # Right-side combat report that appears on the local player's death.
        death = self._crop(frame_bgr, 0.72, 0.18, 0.27, 0.66, (96, 112))
        # Local ammo HUD: present while alive/in-control, normally disappears on death/spectate transition.
        ammo = self._crop(frame_bgr, 0.855, 0.835, 0.14, 0.16, (80, 64))
        if kill is None or kill_core is None or death is None or ammo is None:
            return []

        ammo_content = self._hud_content(ammo)
        alive_hud = ammo_content >= 10.5
        if alive_hud:
            self._last_alive_hud_ts = ts
        recently_alive = (ts - self._last_alive_hud_ts) <= 2.6

        out: list[CombatEvent] = []
        if self._kill_base is None:
            self._kill_base = kill.copy()
            self._kill_core_base = kill_core.copy()
        if self._death_base is None:
            self._death_base = death.copy()

        kill_change = self._change(self._kill_base, kill)
        kill_core_change = self._change(getattr(self, "_kill_core_base", None), kill_core)
        death_change = self._change(self._death_base, death)
        kill_edges = self._edge_density(kill)
        kill_core_edges = self._edge_density(kill_core)
        death_edges = self._edge_density(death)

        if self._kill_evidence == 0 and kill_change < max(self.kill_threshold, self._kill_noise * 3.0):
            self._kill_base = cv2.addWeighted(self._kill_base, 0.96, kill, 0.04, 0)
            self._kill_core_base = cv2.addWeighted(self._kill_core_base, 0.96, kill_core, 0.04, 0)
            self._kill_noise = self._kill_noise * 0.96 + min(kill_change, 8.0) * 0.04
        if self._death_evidence == 0 and alive_hud and death_change < max(self.death_threshold, self._death_noise * 3.0):
            self._death_base = cv2.addWeighted(self._death_base, 0.98, death, 0.02, 0)
            self._death_noise = self._death_noise * 0.96 + min(death_change, 9.0) * 0.04

        kill_gate = max(self.kill_threshold, self._kill_noise * 3.5 + 2.0)
        core_gate = max(6.5, kill_gate * 0.58)
        self_kill_marker = (
            ts - self._last_kill_ts >= self.kill_cooldown
            and kill_change >= kill_gate
            and kill_core_change >= core_gate
            and kill_edges >= 0.060
            and kill_core_edges >= 0.075
        )
        if self_kill_marker:
            self._kill_evidence += 1
        else:
            self._kill_evidence = max(0, self._kill_evidence - 1)

        if self._kill_evidence >= 2:
            fire_boost = 0.07 if recent_fire else 0.0
            conf = min(0.98, 0.66 + fire_boost + (kill_change - kill_gate) / 38.0 + min(0.10, kill_core_edges))
            source = "self_kill_confirm_gun" if recent_fire else "self_kill_confirm_nonfire"
            out.append(CombatEvent(ts, "kill", round(max(0.60, conf), 3), round(kill_change, 2), source))
            self._last_kill_ts = ts
            self._kill_evidence = 0
            self._kill_base = kill.copy()
            self._kill_core_base = kill_core.copy()

        death_gate = max(self.death_threshold, self._death_noise * 3.5 + 2.5)
        self_death_transition = recently_alive and not alive_hud
        if self_death_transition and ts - self._last_death_ts >= self.death_cooldown and death_change >= death_gate and death_edges >= 0.080:
            self._death_evidence += 1
        else:
            self._death_evidence = max(0, self._death_evidence - 1)

        if self._death_evidence >= 2:
            conf = min(0.98, 0.66 + (death_change - death_gate) / 42.0 + min(0.12, death_edges))
            out.append(CombatEvent(ts, "death", round(max(0.60, conf), 3), round(death_change, 2), "self_death_report"))
            self._last_death_ts = ts
            self._death_evidence = 0
            self._death_base = death.copy()

        if out:
            with self._lock:
                self._events.extend(out)
        return out

    def events_between(self, start: float, end: float) -> list[CombatEvent]:
        with self._lock:
            return [e for e in self._events if start <= e.timestamp <= end]

    def summary(self) -> dict:
        with self._lock:
            events = list(self._events)
        kills = [e for e in events if e.kind == "kill"]
        deaths = [e for e in events if e.kind == "death"]
        return {
            "kills": len(kills),
            "deaths": len(deaths),
            "kd_ratio": round(len(kills) / max(1, len(deaths)), 2),
            "events": [
                {"timestamp": e.timestamp, "kind": e.kind, "confidence": e.confidence, "score": e.score, "source": e.source}
                for e in events
            ],
            "detector": "self_only_local_hud_heuristic",
        }
