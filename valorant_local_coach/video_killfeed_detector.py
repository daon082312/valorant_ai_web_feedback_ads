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
    source: str = "top_right_killfeed_self_highlight"


class VideoKillfeedDetector:
    """Detect only the local player's kills from VALORANT's top-right killfeed.

    VALORANT visually highlights the local player's own kills in the killfeed. This
    detector does not OCR Riot IDs and does not infer deaths. It looks for a newly
    appearing killfeed row whose main-killer block has a stronger highlight border
    than ordinary rows, then ignores lower rows that only shifted position.
    """

    def __init__(self, config: dict):
        self.config = config
        roi = config.get("killfeed_roi") or {"x": 0.60, "y": 0.045, "w": 0.39, "h": 0.34}
        self.roi = (
            float(roi.get("x", 0.60)), float(roi.get("y", 0.045)),
            float(roi.get("w", 0.39)), float(roi.get("h", 0.34)),
        )
        self.rows = max(4, min(7, int(config.get("killfeed_rows", 6))))
        self.change_threshold = max(3.5, float(config.get("killfeed_change_threshold", 7.0)))
        self.highlight_threshold = max(0.08, float(config.get("killfeed_highlight_threshold", 0.145)))
        self.cooldown = max(0.35, float(config.get("killfeed_event_cooldown_seconds", 0.55)))
        self._prev_rows: list[np.ndarray | None] = [None] * self.rows
        self._score_history: deque[float] = deque(maxlen=90)
        self._events: list[KillfeedEvent] = []
        self._last_event = -999.0

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
    def _content_score(row: np.ndarray) -> float:
        gray = cv2.cvtColor(row, cv2.COLOR_BGR2GRAY)
        gray = cv2.GaussianBlur(gray, (3, 3), 0)
        edges = cv2.Canny(gray, 55, 135)
        return float(np.std(gray)) * 0.6 + float(np.count_nonzero(edges)) / max(1, edges.size) * 95.0

    @staticmethod
    def _highlight_score(row: np.ndarray) -> float:
        """Score the main killer block while excluding far-left assist portraits."""
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
        if not np.any(inside):
            return 0.0
        edge_ratio = float(np.count_nonzero(edges[inside])) / float(np.count_nonzero(inside))
        sat = hsv[:, :, 1]
        val = hsv[:, :, 2]
        bright_ratio = float(np.count_nonzero((val[inside] >= 168) & (sat[inside] <= 150))) / float(np.count_nonzero(inside))
        vivid_ratio = float(np.count_nonzero((val[inside] >= 135) & (sat[inside] >= 90))) / float(np.count_nonzero(inside))
        return edge_ratio * 0.58 + bright_ratio * 0.25 + vivid_ratio * 0.17

    def process(self, frame: np.ndarray, video_seconds: float, *, recent_fire: bool = False):
        crop = self._crop_ratio(frame, self.roi)
        if crop is None:
            return []
        ch, cw = crop.shape[:2]
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
            observations.append((idx, row, change, content, highlight))

        active_scores = [o[4] for o in observations if o[3] >= 12.0]
        peer_median = statistics.median(active_scores) if active_scores else 0.0
        history_median = statistics.median(self._score_history) if len(self._score_history) >= 8 else peer_median
        reference = max(0.0, min(peer_median, history_median) if history_median > 0 else peer_median)

        changed_rows = [o for o in observations if o[3] >= 12.0 and o[2] >= self.change_threshold]
        emitted = []
        own_candidate = False
        if changed_rows:
            idx, row, change, content, highlight = min(changed_rows, key=lambda o: o[0])
            separation = highlight - reference
            own_like = highlight >= self.highlight_threshold and (
                separation >= 0.035 or highlight >= self.highlight_threshold * 1.15
            )
            if own_like:
                own_candidate = True
                confidence = 0.58
                confidence += min(0.20, max(0.0, highlight - self.highlight_threshold) * 1.8)
                confidence += min(0.14, max(0.0, change - self.change_threshold) / 35.0)
                confidence += min(0.05, max(0.0, separation) * 1.2)
                if recent_fire:
                    confidence += 0.03
                if video_seconds - self._last_event >= self.cooldown:
                    event = KillfeedEvent(
                        video_seconds=round(video_seconds, 2),
                        confidence=round(min(0.98, confidence), 3),
                        score=round(highlight, 3),
                        row_index=int(idx),
                    )
                    self._events.append(event)
                    self._last_event = video_seconds
                    emitted.append(event)

        if not own_candidate:
            for _idx, _row, _change, content, highlight in observations:
                if content >= 12.0:
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
            "detector": "top_right_killfeed_self_highlight_v1",
            "deaths_analyzed": False,
        }
