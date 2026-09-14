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


class VideoAimAnalyzerV74:
    """Offline aim evaluator with a tolerant head-zone model."""

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
            if not (0.05 <= aspect <= 1.45) or h > frame_h * 0.62:
                continue
            density = area / max(1.0, float(w * h))
            if not (0.020 <= density <= 0.78):
                continue
            gx, gy = x + ox, y + oy
            head_x = gx + w * 0.5
            head_y = gy + h * 0.20
            if head_x < frame_w * 0.08 or head_x > frame_w * 0.92:
                continue
            if head_y < frame_h * 0.08 or head_y > frame_h * 0.84:
                continue
            size = min(1.0, h / max(24.0, frame_h * 0.13))
            shape = max(0.0, 1.0 - abs(aspect - 0.40) / 1.05)
            conf = 0.26 + 0.34 * size + 0.22 * min(1.0, density / 0.24) + 0.18 * shape
            if conf >= 0.42:
                found.append(Target(gx, gy, w, h, round(min(0.98, conf), 3), color))
        found.sort(key=lambda t: (t.confidence, t.h), reverse=True)
        return found[:8]

    def process(self, frame: np.ndarray, video_seconds: float) -> None:
        h, w = frame.shape[:2]
        if not self.frame_w:
            self.set_frame_size(w, h)
        x1, x2 = int(w * 0.06), int(w * 0.96)
        y1, y2 = int(h * 0.07), int(h * 0.88)
        hsv = cv2.cvtColor(frame[y1:y2, x1:x2], cv2.COLOR_BGR2HSV)
        merged: list[Target] = []
        for color, ranges in COLOR_RANGES.items():
            mask = self._mask(hsv, ranges)
            mh, mw = mask.shape
            mask[int(mh * 0.91):, :] = 0
            mask[:int(mh * 0.34), :int(mw * 0.29)] = 0
            targets = self._components(mask, x1, y1, h, w, color)
            merged.extend(targets)
            self.color_votes[color] += sum(t.confidence * min(2.0, t.h / max(20.0, h * 0.06)) for t in targets)
        merged.sort(key=lambda t: (t.confidence, t.h), reverse=True)
        self.frames.append(AimFrame(round(video_seconds, 3), merged[:12]))

    def preferred_color(self) -> str | None:
        configured_vote = float(self.color_votes.get(self.configured_color, 0.0))
        if configured_vote >= 0.9:
            return self.configured_color
        color, score = max(self.color_votes.items(), key=lambda kv: kv[1])
        return color if score >= 1.6 else None

    def _nearby(self, t: float, window: float) -> list[AimFrame]:
        return [f for f in self.frames if abs(f.video_seconds - t) <= window]

    @staticmethod
    def _center(target: Target) -> tuple[float, float]:
        return target.x + target.w * 0.5, target.y + target.h * 0.5

    def _head_zone(self, target: Target) -> tuple[float, float, float, float]:
        center_x = target.x + target.w * 0.5
        half_w = max(6.0, target.w * 0.42, target.h * 0.08)
        y_top = target.y + target.h * 0.08
        y_bottom = target.y + target.h * 0.31
        return center_x - half_w, center_x + half_w, y_top, y_bottom

    def _head_center(self, target: Target) -> tuple[float, float]:
        x1, x2, y1, y2 = self._head_zone(target)
        return (x1 + x2) * 0.5, (y1 + y2) * 0.5

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
                if math.hypot(tx - ox, ty - oy) <= max(24.0, min(target.h, other.h) * 0.72):
                    support += 1
                    break
        return support

    def _zone_distance(self, target: Target) -> tuple[float, float]:
        cx, cy = self.frame_w / 2.0, self.frame_h / 2.0
        x1, x2, y1, y2 = self._head_zone(target)
        dx = x1 - cx if cx < x1 else cx - x2 if cx > x2 else 0.0
        dy = y1 - cy if cy < y1 else cy - y2 if cy > y2 else 0.0
        return math.hypot(dx, dy), abs(dy)

    def _best(self, t: float, window: float = 0.24):
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
                hx, hy = self._head_center(target)
                screen_dist = math.hypot(cx - hx, cy - hy) / max(1.0, float(self.frame_h))
                if screen_dist > 0.27:
                    continue
                proximity = max(0.0, 1.0 - screen_dist / 0.20)
                color_bonus = 0.14 if preferred == target.color else 0.0
                persistence = min(0.24, support * 0.075)
                score = target.confidence * 0.50 + temporal * 0.32 + proximity * 0.50 + color_bonus + persistence
                if target.confidence >= 0.46 and (support >= 1 or target.confidence >= 0.80):
                    candidates.append((score, proximity, -dt, target, support, f.video_seconds))
        if not candidates:
            return None
        _, _, _, target, support, frame_time = max(candidates, key=lambda x: (x[0], x[1], x[2]))
        return target, support, frame_time

    def _score_target(self, target: Target):
        dist, vdist = self._zone_distance(target)
        ratio = dist / max(1.0, float(self.frame_h))
        vratio = vdist / max(1.0, float(self.frame_h))
        aim = 100.0 / (1.0 + (ratio / 0.105) ** 1.32)
        headline = 100.0 / (1.0 + (vratio / 0.085) ** 1.38)
        return max(0.0, min(100.0, aim)), max(0.0, min(100.0, headline)), dist, vdist

    @staticmethod
    def _dedupe_anchors(times: list[float], min_gap: float = 0.12) -> list[float]:
        out: list[float] = []
        for t in sorted(float(x) for x in times):
            if not out or t - out[-1] >= min_gap:
                out.append(t)
        return out

    def _fallback(self, max_events: int = 36) -> list[dict]:
        events: list[dict] = []
        last_t = -999.0
        preferred = self.preferred_color()
        for frame in self.frames:
            if frame.video_seconds - last_t < 0.45:
                continue
            neighbors = self._nearby(frame.video_seconds, 0.30)
            valid = []
            for target in frame.targets:
                support = self._support(frame, target, neighbors)
                if support < 2 or target.confidence < 0.50:
                    continue
                dist, _ = self._zone_distance(target)
                ratio = dist / max(1.0, float(self.frame_h))
                if ratio > 0.18:
                    continue
                score = target.confidence + min(0.22, support * 0.06) + (0.10 if preferred == target.color else 0.0) + max(0.0, 0.18 - ratio * 0.8)
                valid.append((score, target, support))
            if not valid:
                continue
            _, target, support = max(valid, key=lambda x: x[0])
            aim, headline, dist, vdist = self._score_target(target)
            events.append({"video_seconds": round(frame.video_seconds, 3), "aim_score": round(aim, 1), "headline_score": round(headline, 1), "aim_error_px": round(dist, 1), "headline_error_px": round(vdist, 1), "target_color": target.color, "support": support, "source": "persistent_target_low_confidence_v74"})
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
        primary_count = 0
        for shot in anchors:
            result = self._best(shot, 0.24)
            if result is None:
                continue
            target, support, frame_time = result
            aim, headline, dist, vdist = self._score_target(target)
            evidence.append({"video_seconds": round(shot, 3), "frame_seconds": round(frame_time, 3), "aim_score": round(aim, 1), "headline_score": round(headline, 1), "aim_error_px": round(dist, 1), "headline_error_px": round(vdist, 1), "target_color": target.color, "support": support, "source": "ammo_confirmed_shot_v74"})
            primary_count += 1
        kill_anchor_count = 0
        for t in self._dedupe_anchors(kill_times or [], 0.50):
            if any(abs(float(e["video_seconds"]) - t) <= 0.35 for e in evidence):
                continue
            result = self._best(t, 0.30)
            if result is None:
                continue
            target, support, frame_time = result
            aim, headline, dist, vdist = self._score_target(target)
            evidence.append({"video_seconds": round(t, 3), "frame_seconds": round(frame_time, 3), "aim_score": round(aim, 1), "headline_score": round(headline, 1), "aim_error_px": round(dist, 1), "headline_error_px": round(vdist, 1), "target_color": target.color, "support": support, "source": "kill_anchor_v74"})
            kill_anchor_count += 1
        anchored_count = len(evidence)
        if anchored_count > 0:
            method = "shot_synchronized_v74" if primary_count > 0 else "kill_anchor_v74"
            if primary_count > 0 and kill_anchor_count > 0:
                method = "shot_plus_kill_anchor_v74"
        else:
            evidence = self._fallback()
            method = "persistent_target_fallback_v74"
        if not evidence:
            return self._empty(preferred)
        aims = [float(e["aim_score"]) for e in evidence]
        headlines = [float(e["headline_score"]) for e in evidence]
        errors = [float(e["aim_error_px"]) for e in evidence]
        verr = [float(e["headline_error_px"]) for e in evidence]
        aim_score = 0.82 * statistics.median(aims) + 0.18 * statistics.fmean(aims)
        headline_score = 0.82 * statistics.median(headlines) + 0.18 * statistics.fmean(headlines)
        confidence = 0.24 + min(0.46, primary_count * 0.10) + min(0.16, kill_anchor_count * 0.05) + min(0.12, len(evidence) * 0.02)
        if primary_count >= 3:
            confidence += 0.08
        if method == "persistent_target_fallback_v74":
            confidence = min(confidence, 0.50)
        confidence = max(0.12, min(0.96, confidence))
        ranked = sorted(evidence, key=lambda e: float(e["aim_score"]))
        chosen = ranked[:3] + sorted(evidence, key=lambda e: float(e["aim_score"]), reverse=True)[:2]
        aim_events = []
        seen = set()
        for e in chosen:
            key = round(float(e["video_seconds"]), 2)
            if key in seen:
                continue
            seen.add(key)
            aim_events.append(dict(e))
        return {"aim_score": round(aim_score, 1), "headline_score": round(headline_score, 1), "aim_error_px": round(statistics.median(errors), 1), "headline_error_px": round(statistics.median(verr), 1), "evaluated_shots": primary_count, "kill_anchor_count": kill_anchor_count, "shot_anchor_count": len(anchors), "evaluated_events": len(evidence), "preferred_enemy_color": preferred, "configured_enemy_color": self.configured_color, "color_votes": {k: round(v, 2) for k, v in self.color_votes.items()}, "method": method, "confidence": round(confidence, 3), "aim_events": aim_events}

    def _empty(self, preferred):
        return {"aim_score": None, "headline_score": None, "aim_error_px": None, "headline_error_px": None, "evaluated_shots": 0, "kill_anchor_count": 0, "shot_anchor_count": 0, "evaluated_events": 0, "preferred_enemy_color": preferred, "configured_enemy_color": self.configured_color, "color_votes": {k: round(v, 2) for k, v in self.color_votes.items()}, "method": "no_reliable_enemy_evidence_v74", "confidence": 0.0, "aim_events": []}

    def set_frame_size(self, width: int, height: int) -> None:
        self.frame_w = int(width)
        self.frame_h = int(height)
