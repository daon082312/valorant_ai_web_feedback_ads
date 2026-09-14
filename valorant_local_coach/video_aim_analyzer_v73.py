from __future__ import annotations

from dataclasses import dataclass
import math
import statistics

import cv2
import numpy as np

COLOR_RANGES = {
    "red": [((0, 92, 92), (11, 255, 255)), ((169, 92, 92), (179, 255, 255))],
    "purple": [((126, 55, 72), (171, 255, 255))],
    "yellow": [((15, 92, 100), (44, 255, 255))],
}


@dataclass(slots=True)
class Target:
    x: int
    y: int
    w: int
    h: int
    confidence: float
    color: str


@dataclass(slots=True)
class AimFrame:
    video_seconds: float
    targets: list[Target]


class VideoAimAnalyzerV73:
    """Offline aim evaluator tuned for shot-time crosshair placement."""

    def __init__(self, config: dict):
        self.config = config
        self.min_area = max(8, int(config.get("vision_min_component_area", 16)) // 2)
        self.frames: list[AimFrame] = []
        self.color_votes = {k: 0.0 for k in COLOR_RANGES}
        configured = str(config.get("enemy_outline_color", "red")).strip().lower()
        self.configured_color = configured if configured in COLOR_RANGES else "red"
        self.frame_w = 0
        self.frame_h = 0

    @staticmethod
    def _mask(hsv: np.ndarray, ranges) -> np.ndarray:
        out = np.zeros(hsv.shape[:2], np.uint8)
        for lo, hi in ranges:
            out |= cv2.inRange(hsv, np.array(lo, np.uint8), np.array(hi, np.uint8))
        kernel = np.ones((3, 3), np.uint8)
        out = cv2.morphologyEx(out, cv2.MORPH_CLOSE, kernel, iterations=1)
        return cv2.dilate(out, kernel, iterations=1)

    def _components(self, mask: np.ndarray, ox: int, oy: int, frame_h: int, frame_w: int, color: str) -> list[Target]:
        n, _, stats, _ = cv2.connectedComponentsWithStats(mask, 8)
        found: list[Target] = []
        for i in range(1, n):
            x, y, w, h, area = [int(v) for v in stats[i]]
            if area < self.min_area or h < max(10, int(frame_h * 0.020)) or w < 2:
                continue
            aspect = w / max(1.0, float(h))
            if not (0.05 <= aspect <= 1.35) or h > frame_h * 0.62:
                continue
            density = area / max(1.0, float(w * h))
            if not (0.025 <= density <= 0.72):
                continue
            gx = x + ox
            gy = y + oy
            head_x = gx + w * 0.5
            head_y = gy + h * 0.18
            if head_x < frame_w * 0.10 or head_x > frame_w * 0.90:
                continue
            if head_y < frame_h * 0.10 or head_y > frame_h * 0.82:
                continue
            size = min(1.0, h / max(26.0, frame_h * 0.14))
            shape = max(0.0, 1.0 - abs(aspect - 0.38) / 0.95)
            conf = 0.27 + 0.34 * size + 0.22 * min(1.0, density / 0.24) + 0.17 * shape
            if conf >= 0.43:
                found.append(Target(gx, gy, w, h, round(min(0.98, conf), 3), color))
        found.sort(key=lambda t: (t.confidence, t.h), reverse=True)
        return found[:8]

    def process(self, frame: np.ndarray, video_seconds: float) -> None:
        h, w = frame.shape[:2]
        if not self.frame_w:
            self.set_frame_size(w, h)
        x1, x2 = int(w * 0.07), int(w * 0.95)
        y1, y2 = int(h * 0.08), int(h * 0.87)
        hsv = cv2.cvtColor(frame[y1:y2, x1:x2], cv2.COLOR_BGR2HSV)
        merged: list[Target] = []
        for color, ranges in COLOR_RANGES.items():
            mask = self._mask(hsv, ranges)
            mh, mw = mask.shape
            mask[int(mh * 0.90):, :] = 0
            mask[:int(mh * 0.35), :int(mw * 0.30)] = 0
            targets = self._components(mask, x1, y1, h, w, color)
            merged.extend(targets)
            self.color_votes[color] += sum(t.confidence * min(2.0, t.h / max(20.0, h * 0.06)) for t in targets)
        merged.sort(key=lambda t: (t.confidence, t.h), reverse=True)
        self.frames.append(AimFrame(round(video_seconds, 3), merged[:12]))

    def preferred_color(self) -> str | None:
        configured_vote = float(self.color_votes.get(self.configured_color, 0.0))
        if configured_vote >= 1.0:
            return self.configured_color
        color, score = max(self.color_votes.items(), key=lambda kv: kv[1])
        return color if score >= 1.8 else None

    def _nearby(self, t: float, window: float) -> list[AimFrame]:
        return [f for f in self.frames if abs(f.video_seconds - t) <= window]

    @staticmethod
    def _center(target: Target) -> tuple[float, float]:
        return target.x + target.w * 0.5, target.y + target.h * 0.5

    @staticmethod
    def _head(target: Target) -> tuple[float, float]:
        return target.x + target.w * 0.5, target.y + target.h * 0.18

    def _support(self, frame: AimFrame, target: Target, frames: list[AimFrame]) -> int:
        tx, ty = self._center(target)
        support = 0
        for f in frames:
            if f is frame:
                continue
            for other in f.targets:
                if other.color != target.color:
                    continue
                ox, oy = self._center(other)
                if math.hypot(tx - ox, ty - oy) <= max(22.0, min(target.h, other.h) * 0.65):
                    support += 1
                    break
        return support

    def _best(self, t: float, window: float = 0.22):
        if not self.frame_h:
            return None
        preferred = self.preferred_color()
        frames = self._nearby(t, window)
        candidates = []
        cx, cy = self.frame_w / 2.0, self.frame_h / 2.0
        for f in frames:
            dt = abs(f.video_seconds - t)
            temporal = max(0.0, 1.0 - dt / max(0.01, window))
            for target in f.targets:
                support = self._support(f, target, frames)
                hx, hy = self._head(target)
                screen_dist = math.hypot(cx - hx, cy - hy) / max(1.0, float(self.frame_h))
                if screen_dist > 0.30:
                    continue
                proximity = max(0.0, 1.0 - screen_dist / 0.22)
                color_bonus = 0.16 if preferred == target.color else 0.0
                persistence = min(0.22, support * 0.07)
                score = target.confidence * 0.58 + temporal * 0.28 + proximity * 0.36 + color_bonus + persistence
                if target.confidence >= 0.48 and (support >= 1 or target.confidence >= 0.82):
                    candidates.append((score, -dt, proximity, target, support, f.video_seconds))
        if not candidates:
            return None
        _, _, _, target, support, frame_time = max(candidates, key=lambda x: (x[0], x[1], x[2]))
        return target, support, frame_time

    def _score_target(self, target: Target):
        cx, cy = self.frame_w / 2.0, self.frame_h / 2.0
        hx, hy = self._head(target)
        dx, dy = cx - hx, cy - hy
        dist = math.hypot(dx, dy)
        ratio = dist / max(1.0, float(self.frame_h))
        vratio = abs(dy) / max(1.0, float(self.frame_h))
        aim = 100.0 / (1.0 + (ratio / 0.080) ** 1.45)
        headline = 100.0 / (1.0 + (vratio / 0.060) ** 1.55)
        return max(0.0, min(100.0, aim)), max(0.0, min(100.0, headline)), dist, abs(dy)

    @staticmethod
    def _dedupe_anchors(times: list[float], min_gap: float = 0.11) -> list[float]:
        out: list[float] = []
        for t in sorted(float(x) for x in times):
            if not out or t - out[-1] >= min_gap:
                out.append(t)
        return out

    def _fallback(self, max_events: int = 50) -> list[dict]:
        events: list[dict] = []
        last_t = -999.0
        preferred = self.preferred_color()
        for frame in self.frames:
            if frame.video_seconds - last_t < 0.40:
                continue
            neighbors = self._nearby(frame.video_seconds, 0.28)
            valid = []
            cx, cy = self.frame_w / 2.0, self.frame_h / 2.0
            for target in frame.targets:
                support = self._support(frame, target, neighbors)
                if support < 2 or target.confidence < 0.50:
                    continue
                hx, hy = self._head(target)
                ratio = math.hypot(cx - hx, cy - hy) / max(1.0, float(self.frame_h))
                if ratio > 0.22:
                    continue
                score = target.confidence + min(0.20, support * 0.055) + (0.12 if preferred == target.color else 0.0) + max(0.0, 0.16 - ratio * 0.65)
                valid.append((score, target, support))
            if not valid:
                continue
            _, target, support = max(valid, key=lambda x: x[0])
            aim, headline, dist, vdist = self._score_target(target)
            events.append({
                "video_seconds": round(frame.video_seconds, 3),
                "aim_score": round(aim, 1),
                "headline_score": round(headline, 1),
                "aim_error_px": round(dist, 1),
                "headline_error_px": round(vdist, 1),
                "target_color": target.color,
                "support": support,
                "source": "persistent_target_low_confidence",
            })
            last_t = frame.video_seconds
            if len(events) >= max_events:
                break
        return events

    def score(self, shot_times: list[float], kill_times: list[float] | None = None) -> dict:
        preferred = self.preferred_color()
        if not self.frame_w or not self.frame_h:
            return self._empty(preferred)

        anchors = self._dedupe_anchors(shot_times)
        evidence: list[dict] = []
        for shot in anchors:
            result = self._best(shot, 0.22)
            if result is None:
                continue
            target, support, frame_time = result
            aim, headline, dist, vdist = self._score_target(target)
            evidence.append({
                "video_seconds": round(shot, 3), "frame_seconds": round(frame_time, 3),
                "aim_score": round(aim, 1), "headline_score": round(headline, 1),
                "aim_error_px": round(dist, 1), "headline_error_px": round(vdist, 1),
                "target_color": target.color, "support": support, "source": "ammo_confirmed_shot_v73",
            })

        primary_count = len(evidence)
        for t in self._dedupe_anchors(kill_times or [], 0.50):
            if any(abs(float(e["video_seconds"]) - t) <= 0.35 for e in evidence):
                continue
            result = self._best(t, 0.32)
            if result is None:
                continue
            target, support, frame_time = result
            aim, headline, dist, vdist = self._score_target(target)
            evidence.append({
                "video_seconds": round(t, 3), "frame_seconds": round(frame_time, 3),
                "aim_score": round(aim, 1), "headline_score": round(headline, 1),
                "aim_error_px": round(dist, 1), "headline_error_px": round(vdist, 1),
                "target_color": target.color, "support": support, "source": "kill_anchor_v73",
            })

        method = "shot_synchronized_v73"
        if primary_count == 0:
            evidence.extend(self._fallback())
            method = "persistent_target_fallback_v73"
        elif primary_count < 2 and len(anchors) >= 2:
            existing = [float(e["video_seconds"]) for e in evidence]
            extras = []
            for item in self._fallback(max_events=12):
                if all(abs(float(item["video_seconds"]) - t) > 0.45 for t in existing):
                    extras.append(item)
                    if len(extras) >= 4:
                        break
            evidence.extend(extras)
            method = "shot_plus_sparse_fallback_v73"
        elif any(str(e.get("source", "")).startswith("kill_anchor") for e in evidence):
            method = "shot_plus_kill_anchor_v73"

        if not evidence:
            return self._empty(preferred)

        aims = [float(e["aim_score"]) for e in evidence]
        headlines = [float(e["headline_score"]) for e in evidence]
        errors = [float(e["aim_error_px"]) for e in evidence]
        verr = [float(e["headline_error_px"]) for e in evidence]
        aim_score = 0.75 * statistics.median(aims) + 0.25 * statistics.fmean(aims)
        headline_score = 0.75 * statistics.median(headlines) + 0.25 * statistics.fmean(headlines)

        confidence = 0.25 + min(0.42, primary_count * 0.09) + min(0.18, len(evidence) * 0.025)
        if primary_count >= 3:
            confidence += 0.10
        if method == "persistent_target_fallback_v73":
            confidence = min(confidence, 0.52)
        elif method == "shot_plus_sparse_fallback_v73":
            confidence = min(confidence, 0.68)
        confidence = max(0.15, min(0.96, confidence))

        ranked = sorted(evidence, key=lambda e: float(e["aim_score"]))
        chosen = ranked[:4] + sorted(evidence, key=lambda e: float(e["aim_score"]), reverse=True)[:2]
        aim_events = []
        seen = set()
        for e in chosen:
            key = round(float(e["video_seconds"]), 2)
            if key in seen:
                continue
            seen.add(key)
            aim_events.append(dict(e))

        return {
            "aim_score": round(aim_score, 1),
            "headline_score": round(headline_score, 1),
            "aim_error_px": round(statistics.median(errors), 1),
            "headline_error_px": round(statistics.median(verr), 1),
            "evaluated_shots": primary_count,
            "shot_anchor_count": len(anchors),
            "evaluated_events": len(evidence),
            "preferred_enemy_color": preferred,
            "configured_enemy_color": self.configured_color,
            "color_votes": {k: round(v, 2) for k, v in self.color_votes.items()},
            "method": method,
            "confidence": round(confidence, 3),
            "aim_events": aim_events,
        }

    def _empty(self, preferred):
        return {
            "aim_score": None, "headline_score": None, "aim_error_px": None, "headline_error_px": None,
            "evaluated_shots": 0, "shot_anchor_count": 0, "evaluated_events": 0,
            "preferred_enemy_color": preferred, "configured_enemy_color": self.configured_color,
            "color_votes": {k: round(v, 2) for k, v in self.color_votes.items()},
            "method": "no_reliable_enemy_evidence_v73", "confidence": 0.0, "aim_events": [],
        }

    def set_frame_size(self, width: int, height: int) -> None:
        self.frame_w = int(width)
        self.frame_h = int(height)
