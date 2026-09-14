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
    x: int; y: int; w: int; h: int; confidence: float; color: str

@dataclass(slots=True)
class AimFrame:
    video_seconds: float
    targets: list[Target]

class VideoAimAnalyzerV72:
    def __init__(self, config: dict):
        self.config = config
        self.min_area = max(7, int(config.get("vision_min_component_area", 16)) // 2)
        self.frames: list[AimFrame] = []
        self.color_votes = {k: 0.0 for k in COLOR_RANGES}
        self.frame_w = 0; self.frame_h = 0

    @staticmethod
    def _mask(hsv, ranges):
        out = np.zeros(hsv.shape[:2], np.uint8)
        for lo, hi in ranges:
            out |= cv2.inRange(hsv, np.array(lo, np.uint8), np.array(hi, np.uint8))
        kernel = np.ones((3, 3), np.uint8)
        out = cv2.morphologyEx(out, cv2.MORPH_CLOSE, kernel, iterations=1)
        return cv2.dilate(out, kernel, iterations=1)

    def _components(self, mask, ox, oy, frame_h, color):
        n, _, stats, _ = cv2.connectedComponentsWithStats(mask, 8)
        found = []
        for i in range(1, n):
            x, y, w, h, area = [int(v) for v in stats[i]]
            if area < self.min_area or h < max(9, int(frame_h * 0.018)) or w < 2:
                continue
            aspect = w / max(1.0, h)
            if not (0.04 <= aspect <= 1.60) or h > frame_h * 0.72:
                continue
            density = area / max(1.0, float(w * h))
            if density > 0.82:
                continue
            size = min(1.0, h / max(22.0, frame_h * 0.13))
            shape = max(0.0, 1.0 - abs(aspect - 0.38) / 1.10)
            conf = 0.26 + 0.34 * size + 0.22 * min(1.0, density / 0.25) + 0.18 * shape
            if conf >= 0.40:
                found.append(Target(x + ox, y + oy, w, h, round(min(0.98, conf), 3), color))
        found.sort(key=lambda t: (t.confidence, t.h), reverse=True)
        return found[:8]

    def process(self, frame, video_seconds: float):
        h, w = frame.shape[:2]
        if not self.frame_w:
            self.set_frame_size(w, h)
        x1, x2 = int(w * 0.08), int(w * 0.94)
        y1, y2 = int(h * 0.08), int(h * 0.88)
        hsv = cv2.cvtColor(frame[y1:y2, x1:x2], cv2.COLOR_BGR2HSV)
        merged = []
        for color, ranges in COLOR_RANGES.items():
            mask = self._mask(hsv, ranges)
            mh, mw = mask.shape
            mask[int(mh * 0.91):, :] = 0
            mask[:int(mh * 0.36), :int(mw * 0.29)] = 0
            targets = self._components(mask, x1, y1, h, color)
            merged.extend(targets)
            self.color_votes[color] += sum(t.confidence * min(2.2, t.h / max(18.0, h * 0.055)) for t in targets)
        merged.sort(key=lambda t: (t.confidence, t.h), reverse=True)
        self.frames.append(AimFrame(round(video_seconds, 3), merged[:12]))

    def preferred_color(self):
        color, score = max(self.color_votes.items(), key=lambda kv: kv[1])
        return color if score >= 1.2 else None

    def _nearby(self, t, window):
        return [f for f in self.frames if abs(f.video_seconds - t) <= window]

    @staticmethod
    def _center(target):
        return target.x + target.w * 0.5, target.y + target.h * 0.5

    def _support(self, frame, target, frames):
        tx, ty = self._center(target); support = 0
        for f in frames:
            if f is frame: continue
            for other in f.targets:
                if other.color != target.color: continue
                ox, oy = self._center(other)
                if math.hypot(tx - ox, ty - oy) <= max(24.0, min(target.h, other.h) * 0.72):
                    support += 1; break
        return support

    def _best(self, t, window=0.55):
        preferred = self.preferred_color(); frames = self._nearby(t, window); candidates = []
        for f in frames:
            temporal = max(0.0, 1.0 - abs(f.video_seconds - t) / max(0.01, window))
            for target in f.targets:
                support = self._support(f, target, frames)
                score = target.confidence + (0.11 if preferred == target.color else 0.0) + temporal * 0.15 + min(0.22, support * 0.055)
                if target.confidence >= 0.46 and (support >= 1 or target.confidence >= 0.76):
                    candidates.append((score, target, support, f.video_seconds))
        if not candidates: return None
        _, target, support, frame_time = max(candidates, key=lambda x: x[0])
        return target, support, frame_time

    def _score_target(self, target):
        cx, cy = self.frame_w / 2.0, self.frame_h / 2.0
        hx = target.x + target.w * 0.5; hy = target.y + target.h * 0.18
        dx, dy = cx - hx, cy - hy; dist = math.hypot(dx, dy)
        aim = max(0.0, min(100.0, 100.0 - (dist / max(22.0, target.h * 0.66)) * 40.0))
        headline = max(0.0, min(100.0, 100.0 - (abs(dy) / max(18.0, target.h * 0.62)) * 48.0))
        return aim, headline, dist, abs(dy)

    def _fallback(self, max_events=80):
        events = []; last_t = -999.0; preferred = self.preferred_color()
        for frame in self.frames:
            if frame.video_seconds - last_t < 0.28: continue
            neighbors = self._nearby(frame.video_seconds, 0.34); valid = []
            for target in frame.targets:
                support = self._support(frame, target, neighbors)
                if target.confidence < 0.48 or (support < 1 and target.confidence < 0.78): continue
                hx = target.x + target.w * 0.5; hy = target.y + target.h * 0.18
                distance = math.hypot(self.frame_w / 2.0 - hx, self.frame_h / 2.0 - hy)
                score = target.confidence + min(0.18, support * 0.05) + (0.08 if preferred == target.color else 0.0) - min(0.20, distance / max(1.0, self.frame_h) * 0.4)
                valid.append((score, target, support))
            if not valid: continue
            _, target, support = max(valid, key=lambda x: x[0])
            aim, headline, dist, vdist = self._score_target(target)
            events.append({"video_seconds": round(frame.video_seconds, 3), "aim_score": round(aim, 1), "headline_score": round(headline, 1), "aim_error_px": round(dist, 1), "headline_error_px": round(vdist, 1), "target_color": target.color, "support": support, "source": "persistent_target"})
            last_t = frame.video_seconds
            if len(events) >= max_events: break
        return events

    def score(self, shot_times, kill_times=None):
        preferred = self.preferred_color()
        if not self.frame_w or not self.frame_h: return self._empty(preferred)
        evidence = []
        for shot in shot_times:
            result = self._best(shot, 0.58)
            if result is None: continue
            target, support, frame_time = result
            aim, headline, dist, vdist = self._score_target(target)
            evidence.append({"video_seconds": round(shot, 3), "frame_seconds": round(frame_time, 3), "aim_score": round(aim, 1), "headline_score": round(headline, 1), "aim_error_px": round(dist, 1), "headline_error_px": round(vdist, 1), "target_color": target.color, "support": support, "source": "ammo_confirmed_shot"})
        primary_count = len(evidence)
        for t in kill_times or []:
            if any(abs(float(e["video_seconds"]) - t) <= 0.45 for e in evidence): continue
            result = self._best(t, 0.72)
            if result is None: continue
            target, support, frame_time = result
            aim, headline, dist, vdist = self._score_target(target)
            evidence.append({"video_seconds": round(t, 3), "frame_seconds": round(frame_time, 3), "aim_score": round(aim, 1), "headline_score": round(headline, 1), "aim_error_px": round(dist, 1), "headline_error_px": round(vdist, 1), "target_color": target.color, "support": support, "source": "kill_anchor"})
        method = "shot_synchronized"
        minimum_primary = 2 if len(shot_times) >= 2 else 1
        if len(evidence) < minimum_primary:
            existing = [float(e["video_seconds"]) for e in evidence]
            for item in self._fallback():
                if all(abs(float(item["video_seconds"]) - t) > 0.35 for t in existing):
                    evidence.append(item); existing.append(float(item["video_seconds"]))
            method = "persistent_target_fallback" if primary_count == 0 else "shot_plus_persistent_fallback"
        elif any(e.get("source") == "kill_anchor" for e in evidence):
            method = "shot_plus_kill_anchor"
        if not evidence: return self._empty(preferred)
        aims = [float(e["aim_score"]) for e in evidence]; headlines = [float(e["headline_score"]) for e in evidence]
        errors = [float(e["aim_error_px"]) for e in evidence]; verr = [float(e["headline_error_px"]) for e in evidence]
        aim_score = 0.65 * statistics.median(aims) + 0.35 * statistics.fmean(aims)
        headline_score = 0.65 * statistics.median(headlines) + 0.35 * statistics.fmean(headlines)
        confidence = min(0.96, 0.30 + min(0.44, len(evidence) * 0.045) + (0.18 if primary_count >= 2 else 0.0))
        if method == "persistent_target_fallback": confidence = min(confidence, 0.68)
        ranked = sorted(evidence, key=lambda e: float(e["aim_score"])); chosen = ranked[:4] + sorted(evidence, key=lambda e: float(e["aim_score"]), reverse=True)[:2]
        aim_events = []; seen = set()
        for e in chosen:
            key = round(float(e["video_seconds"]), 2)
            if key in seen: continue
            seen.add(key); aim_events.append(dict(e))
        return {"aim_score": round(aim_score, 1), "headline_score": round(headline_score, 1), "aim_error_px": round(statistics.median(errors), 1), "headline_error_px": round(statistics.median(verr), 1), "evaluated_shots": primary_count, "evaluated_events": len(evidence), "preferred_enemy_color": preferred, "color_votes": {k: round(v, 2) for k, v in self.color_votes.items()}, "method": method, "confidence": round(confidence, 3), "aim_events": aim_events}

    def _empty(self, preferred):
        return {"aim_score": None, "headline_score": None, "aim_error_px": None, "headline_error_px": None, "evaluated_shots": 0, "evaluated_events": 0, "preferred_enemy_color": preferred, "color_votes": {k: round(v, 2) for k, v in self.color_votes.items()}, "method": "no_reliable_enemy_evidence", "confidence": 0.0, "aim_events": []}

    def set_frame_size(self, width, height):
        self.frame_w = int(width); self.frame_h = int(height)
