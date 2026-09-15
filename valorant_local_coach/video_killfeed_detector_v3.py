from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import statistics

import cv2
import numpy as np


@dataclass(slots=True)
class KillfeedEvent:
    video_seconds: float
    confidence: float
    score: float
    row_index: int
    source: str = "top_right_killfeed_self_highlight_v3_novel_row"


@dataclass(slots=True)
class PendingKill:
    first_time: float
    last_time: float
    row_hash: int
    row_index: int
    hits: int
    best_highlight: float
    best_change: float
    best_separation: float


class VideoKillfeedDetectorV3:
    """Balanced top-right killfeed detector."""

    def __init__(self, config: dict):
        self.config = config
        roi = config.get("killfeed_roi") or {"x": 0.60, "y": 0.045, "w": 0.39, "h": 0.34}
        self.roi = (
            float(roi.get("x", 0.60)), float(roi.get("y", 0.045)),
            float(roi.get("w", 0.39)), float(roi.get("h", 0.34)),
        )
        self.rows = max(4, min(7, int(config.get("killfeed_rows", 6))))
        self.change_threshold = max(3.8, float(config.get("killfeed_change_threshold_v3", 6.8)))
        self.highlight_threshold = max(0.09, float(config.get("killfeed_highlight_threshold_v3", 0.145)))
        self.cooldown = max(0.10, float(config.get("killfeed_event_cooldown_seconds_v3", 0.18)))
        self.confirm_window = max(0.15, min(0.75, float(config.get("killfeed_confirm_window_seconds_v3", 0.50))))
        self.match_distance = max(8, min(24, int(config.get("killfeed_pending_hash_distance_v3", 18))))
        self.duplicate_distance = max(4, min(14, int(config.get("killfeed_duplicate_hash_distance_v3", 8))))
        self.duplicate_window = max(2.0, min(10.0, float(config.get("killfeed_duplicate_window_seconds_v3", 5.5))))
        self.novel_distance = max(5, min(18, int(config.get("killfeed_novel_hash_distance_v3", 10))))

        self._prev_rows: list[np.ndarray | None] = [None] * self.rows
        self._prev_hashes: list[int | None] = [None] * self.rows
        self._score_history: deque[float] = deque(maxlen=120)
        self._events: list[KillfeedEvent] = []
        self._recent_hashes: deque[tuple[float, int]] = deque(maxlen=48)
        self._last_event = -999.0
        self._pending: PendingKill | None = None

    @staticmethod
    def _crop_ratio(frame: np.ndarray, roi) -> np.ndarray | None:
        h, w = frame.shape[:2]
        x, y, rw, rh = roi
        x1 = max(0, min(w - 1, int(round(w * x))))
        y1 = max(0, min(h - 1, int(round(h * y))))
        x2 = max(x1 + 1, min(w, int(round(w * (x + rw)))))
        y2 = max(y1 + 1, min(h, int(round(h * (y + rh)))))
        crop = frame[y1:y2, x1:x2]
        return crop if crop.size else None

    @staticmethod
    def _row_signature(row: np.ndarray) -> np.ndarray:
        gray = cv2.cvtColor(row, cv2.COLOR_BGR2GRAY)
        return cv2.resize(gray, (96, 18), interpolation=cv2.INTER_AREA)

    @staticmethod
    def _hash_row(row: np.ndarray) -> int:
        h, w = row.shape[:2]
        x1, x2 = int(w * 0.06), int(w * 0.91)
        core = row[:, x1:max(x1 + 2, x2)]
        gray = cv2.cvtColor(core, cv2.COLOR_BGR2GRAY)
        small = cv2.resize(gray, (21, 9), interpolation=cv2.INTER_AREA)
        diff = small[:, 1:] > small[:, :-1]
        value = 0
        for bit in diff.flatten():
            value = (value << 1) | int(bool(bit))
        return value

    @staticmethod
    def _hamming(a: int, b: int) -> int:
        return int((a ^ b).bit_count())

    @staticmethod
    def _content_score(row: np.ndarray) -> float:
        gray = cv2.cvtColor(row, cv2.COLOR_BGR2GRAY)
        gray = cv2.GaussianBlur(gray, (3, 3), 0)
        edges = cv2.Canny(gray, 52, 132)
        return float(np.std(gray)) * 0.6 + float(np.count_nonzero(edges)) / max(1, edges.size) * 95.0

    @staticmethod
    def _highlight_score(row: np.ndarray) -> float:
        h, w = row.shape[:2]
        if h < 8 or w < 20:
            return 0.0
        x1, x2 = int(w * 0.10), int(w * 0.58)
        y1, y2 = int(h * 0.06), int(h * 0.94)
        block = row[y1:y2, x1:x2]
        if block.size == 0:
            return 0.0
        bh, bw = block.shape[:2]
        hsv = cv2.cvtColor(block, cv2.COLOR_BGR2HSV)
        gray = cv2.cvtColor(block, cv2.COLOR_BGR2GRAY)
        edges = cv2.Canny(cv2.GaussianBlur(gray, (3, 3), 0), 42, 122)
        thick = max(1, min(4, int(round(min(bh, bw) * 0.08))))
        perimeter = np.zeros((bh, bw), dtype=np.uint8)
        perimeter[:thick, :] = 1
        perimeter[-thick:, :] = 1
        perimeter[:, :thick] = 1
        perimeter[:, -thick:] = 1
        inside = perimeter.astype(bool)
        count = int(np.count_nonzero(inside))
        if count <= 0:
            return 0.0
        edge_ratio = float(np.count_nonzero(edges[inside])) / count
        sat = hsv[:, :, 1]
        val = hsv[:, :, 2]
        bright_ratio = float(np.count_nonzero((val[inside] >= 162) & (sat[inside] <= 158))) / count
        vivid_ratio = float(np.count_nonzero((val[inside] >= 130) & (sat[inside] >= 84))) / count
        return edge_ratio * 0.56 + bright_ratio * 0.26 + vivid_ratio * 0.18

    def _prune_recent(self, now: float) -> None:
        while self._recent_hashes and now - self._recent_hashes[0][0] > self.duplicate_window:
            self._recent_hashes.popleft()

    def _is_recent_duplicate(self, now: float, row_hash: int) -> bool:
        self._prune_recent(now)
        return any(self._hamming(row_hash, old_hash) <= self.duplicate_distance for _t, old_hash in self._recent_hashes)

    def _is_novel_against_previous(self, row_hash: int) -> bool:
        prev = [h for h in self._prev_hashes if h is not None]
        if not prev:
            return True
        return all(self._hamming(row_hash, h) > self.novel_distance for h in prev)

    def _baseline(self, observations, exclude_idx: int | None = None) -> float:
        peers = [o[4] for o in observations if o[0] != exclude_idx and o[3] >= 11.0]
        history = list(self._score_history)
        peer_med = statistics.median(peers) if peers else 0.0
        hist_med = statistics.median(history) if len(history) >= 8 else 0.0
        if peer_med > 0 and hist_med > 0:
            return min(peer_med, hist_med)
        return max(peer_med, hist_med)

    def _own_like(self, highlight: float, reference: float) -> tuple[bool, bool, float]:
        separation = highlight - reference
        moderate = highlight >= self.highlight_threshold and (
            separation >= 0.030 or highlight >= self.highlight_threshold * 1.12
        )
        strong = highlight >= self.highlight_threshold * 1.28 or separation >= 0.070
        return moderate, strong, separation

    def _emit(self, now: float, first_time: float, row_hash: int, row_index: int,
              highlight: float, change: float, separation: float, hits: int,
              strong: bool = False) -> list[KillfeedEvent]:
        if self._is_recent_duplicate(now, row_hash):
            return []
        if now - self._last_event < self.cooldown:
            return []
        confidence = 0.63 if not strong else 0.72
        confidence += min(0.15, max(0.0, highlight - self.highlight_threshold) * 1.8)
        confidence += min(0.10, max(0.0, change - self.change_threshold) / 38.0)
        confidence += min(0.07, max(0.0, separation) * 1.1)
        confidence += min(0.04, max(0, hits - 1) * 0.02)
        event = KillfeedEvent(
            video_seconds=round(first_time, 2),
            confidence=round(min(0.98, confidence), 3),
            score=round(highlight, 3),
            row_index=int(row_index),
        )
        self._events.append(event)
        self._recent_hashes.append((now, row_hash))
        self._last_event = now
        return [event]

    def _match_pending(self, observations):
        p = self._pending
        if p is None:
            return None
        matches = []
        for obs in observations:
            idx, _row, _change, content, highlight, row_hash = obs
            if content < 11.0:
                continue
            hd = self._hamming(row_hash, p.row_hash)
            if hd <= self.match_distance:
                reference = self._baseline(observations, idx)
                moderate, _strong, sep = self._own_like(highlight, reference)
                if moderate:
                    matches.append((hd, abs(idx - p.row_index), obs, sep))
        return min(matches, key=lambda x: (x[0], x[1])) if matches else None

    def process(self, frame: np.ndarray, video_seconds: float, *, recent_fire: bool = False):
        crop = self._crop_ratio(frame, self.roi)
        if crop is None:
            return []
        ch, _cw = crop.shape[:2]
        row_h = ch / float(self.rows)
        observations = []
        current_sigs: list[np.ndarray | None] = [None] * self.rows
        current_hashes: list[int | None] = [None] * self.rows

        for idx in range(self.rows):
            y1 = max(0, int(round(idx * row_h)))
            y2 = min(ch, max(y1 + 1, int(round((idx + 1) * row_h))))
            row = crop[y1:y2, :]
            if row.size == 0:
                continue
            sig = self._row_signature(row)
            row_hash = self._hash_row(row)
            current_sigs[idx] = sig
            current_hashes[idx] = row_hash
            prev = self._prev_rows[idx]
            change = float(np.mean(cv2.absdiff(prev, sig))) if prev is not None else 0.0
            content = self._content_score(row)
            highlight = self._highlight_score(row)
            observations.append((idx, row, change, content, highlight, row_hash))

        emitted: list[KillfeedEvent] = []

        if self._pending is not None:
            p = self._pending
            age = video_seconds - p.first_time
            match = self._match_pending(observations)
            if match is not None and age <= self.confirm_window:
                _hd, _delta, obs, sep = match
                idx, _row, change, _content, highlight, row_hash = obs
                p.last_time = video_seconds
                p.hits += 1
                p.row_hash = row_hash
                p.row_index = idx
                p.best_highlight = max(p.best_highlight, highlight)
                p.best_change = max(p.best_change, change)
                p.best_separation = max(p.best_separation, sep)
                if p.hits >= 2:
                    emitted.extend(self._emit(
                        video_seconds, p.first_time, p.row_hash, p.row_index,
                        p.best_highlight, p.best_change, p.best_separation, p.hits,
                    ))
                    self._pending = None
            elif age > self.confirm_window:
                self._pending = None

        changed = [o for o in observations if o[3] >= 11.5 and o[2] >= self.change_threshold]
        if changed:
            obs = min(changed, key=lambda o: o[0])
            idx, _row, change, _content, highlight, row_hash = obs
            reference = self._baseline(observations, idx)
            moderate, strong, sep = self._own_like(highlight, reference)
            novel = self._is_novel_against_previous(row_hash)

            if moderate and novel and not self._is_recent_duplicate(video_seconds, row_hash):
                if strong:
                    emitted.extend(self._emit(
                        video_seconds, video_seconds, row_hash, idx,
                        highlight, change, sep, 1, strong=True,
                    ))
                    self._pending = None
                elif self._pending is None or self._hamming(row_hash, self._pending.row_hash) > self.match_distance:
                    self._pending = PendingKill(
                        first_time=video_seconds,
                        last_time=video_seconds,
                        row_hash=row_hash,
                        row_index=idx,
                        hits=1,
                        best_highlight=highlight,
                        best_change=change,
                        best_separation=sep,
                    )

        pending_hash = self._pending.row_hash if self._pending is not None else None
        for idx, _row, _change, content, highlight, row_hash in observations:
            if content < 11.0:
                continue
            if pending_hash is not None and self._hamming(row_hash, pending_hash) <= self.match_distance:
                continue
            self._score_history.append(highlight)

        self._prev_rows = current_sigs
        self._prev_hashes = current_hashes
        return emitted

    def summary(self) -> dict:
        return {
            "kills": len(self._events),
            "events": [
                {
                    "video_seconds": e.video_seconds,
                    "kind": "kill",
                    "confidence": e.confidence,
                    "score": e.score,
                    "row_index": e.row_index,
                    "source": e.source,
                }
                for e in self._events
            ],
            "detector": "top_right_killfeed_self_highlight_v3_novel_row",
            "deaths_analyzed": False,
        }
