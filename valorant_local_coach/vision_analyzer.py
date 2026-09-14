from __future__ import annotations

import json
import statistics
import threading
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

try:
    cv2.setNumThreads(1)
    cv2.ocl.setUseOpenCL(False)
except Exception:
    pass


@dataclass(slots=True)
class Detection:
    kind: str
    x: int
    y: int
    w: int
    h: int
    confidence: float


@dataclass(slots=True)
class VisionSample:
    timestamp: float
    enemies: int
    allies: int
    minimap_enemies: int
    minimap_allies: int
    headline_score: float | None
    headline_error_px: float | None
    aim_score: float | None
    aim_error_px: float | None
    detections: list[Detection]


ENEMY_RANGES = {
    'red': [((0, 115, 120), (9, 255, 255)), ((171, 115, 120), (179, 255, 255))],
    'purple': [((132, 70, 90), (168, 255, 255))],
    'yellow': [((18, 120, 125), (39, 255, 255))],
}
ALLY_RANGES = {
    'cyan': [((78, 70, 90), (105, 255, 255))],
    'green': [((42, 65, 80), (78, 255, 255))],
}


class VisionAnalyzer:
    """Lightweight local CV for HUD review."""

    def __init__(self, root_dir: Path, config: dict):
        self.root_dir = Path(root_dir)
        self.config = config
        self.enemy_color = str(config.get('enemy_outline_color', 'red')).lower()
        self.ally_color = str(config.get('ally_outline_color', 'cyan')).lower()
        self.minimap_enemy_color = str(config.get('minimap_enemy_color', 'red')).lower()
        self.minimap_ally_color = str(config.get('minimap_ally_color', 'cyan')).lower()
        self.vision_fps = max(1.0, min(12.0, float(config.get('vision_analysis_fps', 4))))
        self.min_component_area = max(8, int(config.get('vision_min_component_area', 16)))
        self.store_minimap_history = bool(config.get('store_minimap_history', False))
        self._last_analysis_ts = 0.0
        self._lock = threading.Lock()
        self._samples: deque[VisionSample] = deque(maxlen=1200)
        self._minimap_frames: deque[tuple[float, bytes]] = deque(maxlen=720)
        self._latest_minimap = None
        self._latest_annotated = None

    def update_config(self, config: dict) -> None:
        self.config = config
        self.enemy_color = str(config.get('enemy_outline_color', 'red')).lower()
        self.ally_color = str(config.get('ally_outline_color', 'cyan')).lower()
        self.minimap_enemy_color = str(config.get('minimap_enemy_color', 'red')).lower()
        self.minimap_ally_color = str(config.get('minimap_ally_color', 'cyan')).lower()
        self.vision_fps = max(1.0, min(12.0, float(config.get('vision_analysis_fps', 4))))
        self.min_component_area = max(8, int(config.get('vision_min_component_area', 16)))
        self.store_minimap_history = bool(config.get('store_minimap_history', False))

    def clear(self) -> None:
        with self._lock:
            self._samples.clear()
            self._minimap_frames.clear()
            self._latest_minimap = None
            self._latest_annotated = None
        self._last_analysis_ts = 0.0

    @staticmethod
    def _mask_ranges(hsv: np.ndarray, ranges) -> np.ndarray:
        mask = np.zeros(hsv.shape[:2], dtype=np.uint8)
        for lower, upper in ranges:
            mask = cv2.bitwise_or(mask, cv2.inRange(hsv, np.array(lower, np.uint8), np.array(upper, np.uint8)))
        kernel = np.ones((3, 3), np.uint8)
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel, iterations=1)
        mask = cv2.dilate(mask, kernel, iterations=1)
        return mask

    def _components(self, mask: np.ndarray, offset_x: int, offset_y: int, kind: str, frame_h: int) -> list[Detection]:
        count, _labels, stats, _centers = cv2.connectedComponentsWithStats(mask, 8)
        detections: list[Detection] = []
        for idx in range(1, count):
            x, y, w, h, area = [int(v) for v in stats[idx]]
            if area < self.min_component_area or h < 8 or w < 2:
                continue
            aspect = w / max(1.0, float(h))
            if aspect > 2.6 or h > frame_h * 0.82:
                continue
            density = area / max(1.0, float(w * h))
            size_term = min(1.0, h / max(24.0, frame_h * 0.18))
            density_term = min(1.0, density / 0.22)
            confidence = round(0.28 + 0.38 * size_term + 0.34 * density_term, 3)
            if confidence < 0.36:
                continue
            detections.append(Detection(kind, x + offset_x, y + offset_y, w, h, min(0.98, confidence)))
        detections.sort(key=lambda d: (d.confidence, d.h), reverse=True)
        return detections[:10]

    def _minimap_roi(self, frame: np.ndarray) -> tuple[np.ndarray, tuple[int, int, int, int]]:
        h, w = frame.shape[:2]
        roi = self.config.get('minimap_roi') or {}
        x = int(w * float(roi.get('x', 0.0)))
        y = int(h * float(roi.get('y', 0.0)))
        rw = int(w * float(roi.get('w', 0.30)))
        rh = int(h * float(roi.get('h', 0.36)))
        x = max(0, min(w - 1, x)); y = max(0, min(h - 1, y))
        rw = max(40, min(w - x, rw)); rh = max(40, min(h - y, rh))
        return frame[y:y + rh, x:x + rw].copy(), (x, y, rw, rh)

    def _minimap_counts(self, minimap: np.ndarray) -> tuple[int, int]:
        if minimap.size == 0:
            return 0, 0
        hsv = cv2.cvtColor(minimap, cv2.COLOR_BGR2HSV)
        enemy_ranges = ENEMY_RANGES.get(self.minimap_enemy_color, ENEMY_RANGES['red'])
        ally_ranges = ALLY_RANGES.get(self.minimap_ally_color, ALLY_RANGES['cyan'])
        enemy_mask = self._mask_ranges(hsv, enemy_ranges)
        ally_mask = self._mask_ranges(hsv, ally_ranges)

        def small_components(mask: np.ndarray) -> int:
            n, _labels, stats, _centers = cv2.connectedComponentsWithStats(mask, 8)
            hits = 0
            max_area = max(20, int(mask.size * 0.012))
            for i in range(1, n):
                x, y, w, h, area = [int(v) for v in stats[i]]
                if 5 <= area <= max_area and 2 <= w <= max(5, minimap.shape[1] // 7) and 2 <= h <= max(5, minimap.shape[0] // 7):
                    hits += 1
            return min(10, hits)

        return small_components(enemy_mask), small_components(ally_mask)

    def analyze(self, frame_bgr: np.ndarray, timestamp: float | None = None) -> VisionSample | None:
        ts = float(timestamp or time.time())
        if ts - self._last_analysis_ts < 1.0 / self.vision_fps:
            return None
        self._last_analysis_ts = ts
        if frame_bgr is None or frame_bgr.size == 0:
            return None

        frame = frame_bgr
        h, w = frame.shape[:2]
        minimap, minimap_box = self._minimap_roi(frame)
        mm_enemy, mm_ally = self._minimap_counts(minimap)

        x1, x2 = int(w * 0.08), int(w * 0.94)
        y1, y2 = int(h * 0.10), int(h * 0.88)
        gameplay = frame[y1:y2, x1:x2]
        hsv = cv2.cvtColor(gameplay, cv2.COLOR_BGR2HSV)
        enemy_mask = self._mask_ranges(hsv, ENEMY_RANGES.get(self.enemy_color, ENEMY_RANGES['red']))
        ally_mask = self._mask_ranges(hsv, ALLY_RANGES.get(self.ally_color, ALLY_RANGES['cyan']))
        mx, my, mw, mh = minimap_box
        rx1 = max(0, mx - x1); ry1 = max(0, my - y1)
        rx2 = min(enemy_mask.shape[1], mx + mw - x1); ry2 = min(enemy_mask.shape[0], my + mh - y1)
        if rx2 > rx1 and ry2 > ry1:
            enemy_mask[ry1:ry2, rx1:rx2] = 0
            ally_mask[ry1:ry2, rx1:rx2] = 0
        enemies = self._components(enemy_mask, x1, y1, 'enemy', h)
        allies = self._components(ally_mask, x1, y1, 'ally', h)

        headline_score = None
        headline_error = None
        aim_score = None
        aim_error = None
        usable_enemies = [d for d in enemies if d.confidence >= 0.48 and d.h >= max(18, int(h * 0.035))]
        if usable_enemies:
            crosshair_x = w / 2.0
            crosshair_y = h / 2.0
            head_points = [(d.x + d.w * 0.5, d.y + d.h * 0.18) for d in usable_enemies[:4]]
            vertical_errors = [abs(crosshair_y - hy) for _hx, hy in head_points]
            distances = [((crosshair_x - hx) ** 2 + (crosshair_y - hy) ** 2) ** 0.5 for hx, hy in head_points]
            headline_error = round(min(vertical_errors), 1)
            aim_error = round(min(distances), 1)
            headline_score = round(max(0.0, min(100.0, 100.0 - (headline_error / max(1.0, h)) * 520.0)), 1)
            aim_score = round(max(0.0, min(100.0, 100.0 - (aim_error / max(1.0, h)) * 715.0)), 1)

        detections = enemies + allies
        sample = VisionSample(
            timestamp=ts,
            enemies=len(enemies),
            allies=len(allies),
            minimap_enemies=mm_enemy,
            minimap_allies=mm_ally,
            headline_score=headline_score,
            headline_error_px=headline_error,
            aim_score=aim_score,
            aim_error_px=aim_error,
            detections=detections,
        )

        annotated = None
        if self.config.get('show_vision_boxes_in_preview', False):
            annotated = frame.copy()
            for d in detections:
                color = (70, 70, 255) if d.kind == 'enemy' else (255, 220, 50)
                cv2.rectangle(annotated, (d.x, d.y), (d.x + d.w, d.y + d.h), color, 2)
                cv2.putText(annotated, f'{d.kind} {d.confidence:.2f}', (d.x, max(14, d.y - 5)), cv2.FONT_HERSHEY_SIMPLEX, 0.42, color, 1, cv2.LINE_AA)
            cv2.rectangle(annotated, (mx, my), (mx + mw, my + mh), (180, 180, 180), 1)
            cv2.drawMarker(annotated, (w // 2, h // 2), (240, 240, 240), cv2.MARKER_CROSS, 16, 1)

        encoded = None
        if self.store_minimap_history:
            ok, payload = cv2.imencode('.jpg', minimap, [int(cv2.IMWRITE_JPEG_QUALITY), 72])
            if ok:
                encoded = payload.tobytes()
        with self._lock:
            self._samples.append(sample)
            self._latest_minimap = minimap
            self._latest_annotated = annotated
            if encoded is not None:
                self._minimap_frames.append((ts, encoded))
                cutoff = ts - 30.0
                while self._minimap_frames and self._minimap_frames[0][0] < cutoff:
                    self._minimap_frames.popleft()
        return sample

    def latest_minimap(self):
        with self._lock:
            return None if self._latest_minimap is None else self._latest_minimap.copy()

    def latest_annotated(self):
        with self._lock:
            return None if self._latest_annotated is None else self._latest_annotated.copy()

    def samples_between(self, start: float, end: float) -> list[VisionSample]:
        with self._lock:
            return [s for s in self._samples if start <= s.timestamp <= end]

    def summary(self, seconds: float = 30.0) -> dict:
        cutoff = time.time() - max(1.0, seconds)
        with self._lock:
            samples = [s for s in self._samples if s.timestamp >= cutoff]
        headline = [float(s.headline_score) for s in samples if s.headline_score is not None]
        error = [float(s.headline_error_px) for s in samples if s.headline_error_px is not None]
        aim = [float(s.aim_score) for s in samples if s.aim_score is not None]
        aim_error = [float(s.aim_error_px) for s in samples if s.aim_error_px is not None]
        return {
            'sample_count': len(samples),
            'enemy_detection_frames': sum(1 for s in samples if s.enemies > 0),
            'ally_detection_frames': sum(1 for s in samples if s.allies > 0),
            'max_enemies': max((s.enemies for s in samples), default=0),
            'max_allies': max((s.allies for s in samples), default=0),
            'minimap_max_enemies': max((s.minimap_enemies for s in samples), default=0),
            'minimap_max_allies': max((s.minimap_allies for s in samples), default=0),
            'headline_score': round(statistics.fmean(headline), 1) if headline else None,
            'headline_error_px': round(statistics.fmean(error), 1) if error else None,
            'aim_score': round(statistics.fmean(aim), 1) if aim else None,
            'aim_error_px': round(statistics.fmean(aim_error), 1) if aim_error else None,
            'detector': 'local_color_heuristic',
            'enemy_outline_color': self.enemy_color,
            'ally_outline_color': self.ally_color,
            'minimap_enemy_color': self.minimap_enemy_color,
            'minimap_ally_color': self.minimap_ally_color,
        }

    def aim_summary_between(self, start: float, end: float) -> dict:
        samples = self.samples_between(start, end)
        aim = [float(s.aim_score) for s in samples if s.aim_score is not None]
        errors = [float(s.aim_error_px) for s in samples if s.aim_error_px is not None]
        return {
            'sample_count': len(samples),
            'target_visible_samples': len(aim),
            'aim_score': round(statistics.fmean(aim), 1) if aim else None,
            'aim_error_px': round(statistics.fmean(errors), 1) if errors else None,
            'best_aim_error_px': round(min(errors), 1) if errors else None,
        }

    def _closest_minimap(self, target: float) -> tuple[float, bytes] | None:
        with self._lock:
            frames = list(self._minimap_frames)
        if not frames:
            return None
        return min(frames, key=lambda item: abs(item[0] - target))

    def save_fight_assets(self, fight_started: float, fight_ended: float, output_dir: Path, stem: str) -> dict:
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        mid = (fight_started + fight_ended) / 2.0
        targets = {'pre': fight_started - 1.2, 'fight': mid, 'post': fight_ended + 0.7}
        saved = {}
        for label, target in targets.items():
            item = self._closest_minimap(target)
            if not item:
                continue
            ts, payload = item
            path = output_dir / f'{stem}_minimap_{label}.jpg'
            path.write_bytes(payload)
            saved[label] = {'path': str(path), 'timestamp': ts}

        fight_samples = self.samples_between(fight_started - 1.5, fight_ended + 1.0)
        headline = [s.headline_score for s in fight_samples if s.headline_score is not None]
        errors = [s.headline_error_px for s in fight_samples if s.headline_error_px is not None]
        data = {
            'detector': 'local_color_heuristic',
            'enemy_outline_color': self.enemy_color,
            'ally_outline_color': self.ally_color,
            'minimap_enemy_color': self.minimap_enemy_color,
            'minimap_ally_color': self.minimap_ally_color,
            'enemy_detection_frames': sum(1 for s in fight_samples if s.enemies > 0),
            'ally_detection_frames': sum(1 for s in fight_samples if s.allies > 0),
            'minimap_max_enemies': max((s.minimap_enemies for s in fight_samples), default=0),
            'minimap_max_allies': max((s.minimap_allies for s in fight_samples), default=0),
            'headline_score': round(statistics.fmean(headline), 1) if headline else None,
            'headline_error_px': round(statistics.fmean(errors), 1) if errors else None,
            'minimap_snapshots': saved,
            'note': 'Color/shape heuristic. Low-confidence scenes may be unrecognized; this is not a trained identity detector.',
        }
        path = output_dir / f'{stem}_vision.json'
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
        data['vision_json'] = str(path)
        return data
