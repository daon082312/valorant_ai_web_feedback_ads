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
    source: str = "top_right_killfeed_self_highlight_v2_confirmed"


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


class VideoKillfeedDetectorV2:
    """Conservative top-right killfeed detector.

    v2 fixes v1's over-counting by requiring a new highlighted row to persist
    across sampled frames and by actually using perceptual row hashes to reject
    the same killfeed item when it animates or shifts downward.
    """

    def __init__(self, config: dict):
        self.config = config
        roi = config.get("killfeed_roi") or {"x": 0.60, "y": 0.045, "w": 0.39, "h": 0.34}
        self.roi = (
            float(roi.get("x", 0.60)), float(roi.get("y", 0.045)),
            float(roi.get("w", 0.39)), float(roi.get("h", 0.34)),
        )
        self.rows = max(4, min(7, int(config.get("killfeed_rows", 6))))
        self.change_threshold = max(4.5, float(config.get("killfeed_change_threshold_v2", 8.5)))
        self.highlight_threshold = max(0.10, float(config.get("killfeed_highlight_threshold_v2", 0.165)))
        self.cooldown = max(0.40, float(config.get("killfeed_event_cooldown_seconds_v2", 0.55)))
        self.confirm_window = max(0.16, min(0.60, float(config.get("killfeed_confirm_window_seconds", 0.42))))
        self.hash_distance = max(8, min(28, int(config.get("killfeed_hash_distance", 18))))
        self.duplicate_window = max(4.0, min(15.0, float(config.get("killfeed_duplicate_window_seconds", 9.0))))

        self._prev_rows: list[np.ndarray | None] = [None] * self.rows
        self._score_history: deque[float] = deque(maxlen=120)
        self._events: list[KillfeedEvent] = []
        self._recent_hashes: deque[tuple[float, int]] = deque(maxlen=40)
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
        x1, x2 = int(w * 0.08), int(w * 0.78)
        core = row[:, x1:max(x1 + 2, x2)]
        gray = cv2.cvtColor(core, cv2.COLOR_BGR2GRAY)
        small = cv2.resize(gray, (17, 8), interpolation=cv2.INTER_AREA)
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
        edges = cv2.Canny(gray, 55, 135)
        return float(np.std(gray)) * 0.6 + float(np.count_nonzero(edges)) / max(1, edges.size) * 95.0

    @staticmethod
    def _highlight_score(row: np.ndarray) -> float:
        h, w = row.shape[:2]
        if h < 8 or w < 20:
            return 0.0
        x1, x2 = int(w * 0.12), int(w * 0.55)
        y1, y2 = int(h * 0.08), int(h * 0.92)
        block = row[y1:y2, x1:x2]
        if block.size == 0:
            return 0.0
        bh, bw = block.shape[:2]
        hsv = cv2.cvtColor(block, cv2.COLOR_BGR2HSV)
        gray = cv2.cvtColor(block, cv2.COLOR_BGR2GRAY)
        edges = cv2.Canny(cv2.GaussianBlur(gray, (3, 3), 0), 45, 125)
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
        bright_ratio = float(np.count_nonzero((val[inside] >= 168) & (sat[inside] <= 150))) / count
        vivid_ratio = float(np.count_nonzero((val[inside] >= 135) & (sat[inside] >= 90))) / count
        return edge_ratio * 0.58 + bright_ratio * 0.25 + vivid_ratio * 0.17

    def _prune_recent(self, now: float) -> None:
        while self._recent_hashes and now - self._recent_hashes[0][0] > self.duplicate_window:
            self._recent_hashes.popleft()

    def _is_recent_duplicate(self, now: float, row_hash: int) -> bool:
        self._prune_recent(now)
        return any(self._hamming(row_hash, old_hash) <= self.hash_distance for _t, old_hash in self._recent_hashes)

    def _baseline(self, observations, exclude_idx: int | None = None) -> float:
        peers = [o[4] for o in observations if o[0] != exclude_idx and o[3] >= 12.0]
        history = list(self._score_history)
        peer_med = statistics.median(peers) if peers else 0.0
        hist_med = statistics.median(history) if len(history) >= 8 else 0.0
        if peer_med > 0 and hist_med > 0:
            return min(peer_med, hist_med)
        return max(peer_med, hist_med)

    def _own_like(self, highlight: float, reference: float) -> tuple[bool, float]:
        separation = highlight - reference
        own = highlight >= self.highlight_threshold and (
            separation >= 0.045 or highlight >= self.highlight_threshold * 1.22
        )
        return own, separation

    def _match_pending(self, observations):
        p = self._pending
        if p is None:
            return None
        matches = []
        for obs in observations:
            idx, row, _change, content, highlight, row_hash = obs
            if content < 12.0:
                continue
            hd = self._hamming(row_hash, p.row_hash)
            if hd <= self.hash_distance:
                reference = self._baseline(observations, idx)
                own, sep = self._own_like(highlight, reference)
                if own:
                    matches.append((hd, abs(idx - p.row_index), obs, sep))
        if not matches:
            return None
        return min(matches, key=lambda x: (x[0], x[1]))

    def _emit_pending(self, video_seconds: float) -> list[KillfeedEvent]:
        p = self._pending
        if p is None:
            return []
        if self._is_recent_duplicate(video_seconds, p.row_hash):
            self._pending = None
            return []
        if video_seconds - self._last_event < self.cooldown:
            return []
        confidence = 0.66
        confidence += min(0.15, max(0.0, p.best_highlight - self.highlight_threshold) * 1.7)
        confidence += min(0.10, max(0.0, p.best_change - self.change_threshold) / 40.0)
        confidence += min(0.06, max(0.0, p.best_separation) * 1.1)
        confidence += min(0.03, max(0, p.hits - 2) * 0.015)
        event = KillfeedEvent(
            video_seconds=round(p.first_time, 2),
            confidence=round(min(0.97, confidence), 3),
            score=round(p.best_highlight, 3),
            row_index=int(p.row_index),
        )
        self._events.append(event)
        self._recent_hashes.append((video_seconds, p.row_hash))
        self._last_event = video_seconds
        self._pending = None
        return [event]

    def process(self, frame: np.ndarray, video_seconds: float, *, recent_fire: bool = False):
        crop = self._crop_ratio(frame, self.roi)
        if crop is None:
            return []
        ch, _cw = crop.shape[:2]
        row_h = ch / float(self.rows)
        observations = []
        current_sigs: list[np.ndarray | None] = [None] * self.rows

        for idx in range(self.rows):
            y1 = max(0, int(round(idx * row_h)))
            y2 = min(ch, max(y1 + 1, int(round((idx + 1) * row_h))))
            row = crop[y1:y2, :]
            if row.size == 0:
                continue
            sig = self._row_signature(row)
            current_sigs[idx] = sig
            prev = self._prev_rows[idx]
            change = float(np.mean(cv2.absdiff(prev, sig))) if prev is not None else 0.0
            content = self._content_score(row)
            highlight = self._highlight_score(row)
            row_hash = self._hash_row(row)
            observations.append((idx, row, change, content, highlight, row_hash))

        emitted: list[KillfeedEvent] = []

        if self._pending is not None:
            age = video_seconds - self._pending.first_time
            match = self._match_pending(observations)
            if match is not None and age <= self.confirm_window:
                _hd, _row_delta, obs, sep = match
                idx, _row, change, _content, highlight, row_hash = obs
                self._pending.last_time = video_seconds
                self._pending.hits += 1
                self._pending.row_hash = row_hash
                self._pending.row_index = idx
                self._pending.best_highlight = max(self._pending.best_highlight, highlight)
                self._pending.best_change = max(self._pending.best_change, change)
                self._pending.best_separation = max(self._pending.best_separation, sep)
                if self._pending.hits >= 2:
                    emitted.extend(self._emit_pending(video_seconds))
            elif age > self.confirm_window:
                self._pending = None

        if self._pending is None:
            changed = [o for o in observations if o[3] >= 13.0 and o[2] >= self.change_threshold]
            if changed:
                obs = min(changed, key=lambda o: o[0])
                idx, _row, change, _content, highlight, row_hash = obs
                reference = self._baseline(observations, idx)
                own, sep = self._own_like(highlight, reference)
                if own and not self._is_recent_duplicate(video_seconds, row_hash):
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
            if content < 12.0:
                continue
            if pending_hash is not None and self._hamming(row_hash, pending_hash) <= self.hash_distance:
                continue
            self._score_history.append(highlight)

        self._prev_rows = current_sigs
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
            "detector": "top_right_killfeed_self_highlight_v2_confirmed_dedup",
            "deaths_analyzed": False,
        }
