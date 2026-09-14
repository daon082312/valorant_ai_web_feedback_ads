from __future__ import annotations

from dataclasses import dataclass
import math
import statistics

import cv2
import numpy as np


COLOR_RANGES = {
    "red": [((0, 105, 105), (10, 255, 255)), ((170, 105, 105), (179, 255, 255))],
    "purple": [((128, 65, 80), (170, 255, 255))],
    "yellow": [((16, 105, 110), (42, 255, 255))],
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


class VideoAimAnalyzer:
    """Shot-synchronized offline aim evaluator with automatic outline-color selection."""

    def __init__(self, config: dict):
        self.config = config
        self.min_area = max(10, int(config.get("vision_min_component_area", 16)))
        self.frames: list[AimFrame] = []
        self.color_votes: dict[str, float] = {k: 0.0 for k in COLOR_RANGES}
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

    def _components(self, mask: np.ndarray, ox: int, oy: int, frame_h: int, color: str) -> list[Target]:
        n, _, stats, _ = cv2.connectedComponentsWithStats(mask, 8)
        found: list[Target] = []
        for i in range(1, n):
            x, y, w, h, area = [int(v) for v in stats[i]]
            if area < self.min_area or h < max(12, int(frame_h * 0.025)) or w < 2:
                continue
            aspect = w / max(1.0, h)
            if not (0.05 <= aspect <= 1.45):
                continue
            if h > frame_h * 0.68:
                continue
            density = area / max(1.0, float(w * h))
            if density > 0.72:
                continue
            size = min(1.0, h / max(28.0, frame_h * 0.16))
            shape = max(0.0, 1.0 - abs(aspect - 0.38) / 1.0)
            conf = 0.30 + 0.34 * size + 0.20 * min(1.0, density / 0.24) + 0.16 * shape
            if conf >= 0.46:
                found.append(Target(x + ox, y + oy, w, h, round(min(0.98, conf), 3), color))
        found.sort(key=lambda t: (t.confidence, t.h), reverse=True)
        return found[:6]

    def process(self, frame: np.ndarray, video_seconds: float) -> None:
        h, w = frame.shape[:2]
        if not self.frame_w:
            self.set_frame_size(w, h)
        x1, x2 = int(w * 0.10), int(w * 0.92)
        y1, y2 = int(h * 0.10), int(h * 0.86)
        roi = frame[y1:y2, x1:x2]
        hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)

        by_color: dict[str, list[Target]] = {}
        for color, ranges in COLOR_RANGES.items():
            mask = self._mask(hsv, ranges)
            mh, mw = mask.shape
            mask[int(mh * 0.90):, :] = 0
            mask[:int(mh * 0.34), :int(mw * 0.28)] = 0
            targets = self._components(mask, x1, y1, h, color)
            by_color[color] = targets
            self.color_votes[color] += sum(t.confidence * min(2.0, t.h / max(20.0, h * 0.06)) for t in targets)

        merged = sorted([t for values in by_color.values() for t in values], key=lambda t: (t.confidence, t.h), reverse=True)[:10]
        self.frames.append(AimFrame(round(video_seconds, 3), merged))

    def preferred_color(self) -> str | None:
        color, score = max(self.color_votes.items(), key=lambda kv: kv[1])
        return color if score >= 2.0 else None

    def _nearest_frames(self, t: float, window: float = 0.38) -> list[AimFrame]:
        return [f for f in self.frames if abs(f.video_seconds - t) <= window]

    def score_shots(self, shot_times: list[float]) -> dict:
        preferred = self.preferred_color()
        if not self.frame_w or not self.frame_h:
            return {"aim_score": None, "headline_score": None, "aim_error_px": None,
                    "headline_error_px": None, "evaluated_shots": 0, "preferred_enemy_color": preferred}

        shot_scores: list[float] = []
        headline_scores: list[float] = []
        errors: list[float] = []
        vertical_errors: list[float] = []

        for shot in shot_times:
            frames = self._nearest_frames(shot)
            all_targets = [(f, t) for f in frames for t in f.targets]
            candidates: list[tuple[float, Target, int]] = []
            for f, target in all_targets:
                tx = target.x + target.w * 0.5
                ty = target.y + target.h * 0.5
                support = 0
                for f2, other in all_targets:
                    if f2 is f or other.color != target.color:
                        continue
                    ox = other.x + other.w * 0.5
                    oy = other.y + other.h * 0.5
                    if math.hypot(tx - ox, ty - oy) <= max(28.0, target.h * 0.65):
                        support += 1
                color_bonus = 0.10 if preferred and target.color == preferred else 0.0
                temporal = max(0.0, 1.0 - abs(f.video_seconds - shot) / 0.38)
                persistence = min(0.16, support * 0.055)
                candidates.append((target.confidence + color_bonus + temporal * 0.12 + persistence, target, support))
            if not candidates:
                continue
            _, target, support = max(candidates, key=lambda x: x[0])
            if target.confidence < 0.50 or (support < 1 and target.confidence < 0.82):
                continue

            cx, cy = self.frame_w / 2.0, self.frame_h / 2.0
            hx = target.x + target.w * 0.50
            hy = target.y + target.h * 0.18
            dx, dy = cx - hx, cy - hy
            dist = math.hypot(dx, dy)
            norm = max(22.0, target.h * 0.62)
            nerr = dist / norm
            verr = abs(dy) / max(18.0, target.h * 0.60)
            shot_scores.append(max(0.0, min(100.0, 100.0 - nerr * 42.0)))
            headline_scores.append(max(0.0, min(100.0, 100.0 - verr * 50.0)))
            errors.append(dist)
            vertical_errors.append(abs(dy))

        return {
            "aim_score": round(statistics.fmean(shot_scores), 1) if shot_scores else None,
            "headline_score": round(statistics.fmean(headline_scores), 1) if headline_scores else None,
            "aim_error_px": round(statistics.median(errors), 1) if errors else None,
            "headline_error_px": round(statistics.median(vertical_errors), 1) if vertical_errors else None,
            "evaluated_shots": len(shot_scores),
            "preferred_enemy_color": preferred,
            "color_votes": {k: round(v, 2) for k, v in self.color_votes.items()},
        }

    def set_frame_size(self, width: int, height: int) -> None:
        self.frame_w = int(width)
        self.frame_h = int(height)
