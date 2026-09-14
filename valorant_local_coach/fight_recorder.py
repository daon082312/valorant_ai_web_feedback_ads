from __future__ import annotations

import json
import threading
import time
from collections import deque
from dataclasses import asdict
from pathlib import Path

import cv2
import numpy as np

from input_tracker import SkillEvent
from screen_capture import FrameSample


class FightRecorder:
    """Keeps a short local frame buffer and writes fight clips after combat ends."""

    def __init__(self, root_dir: Path, fps: int = 12, buffer_seconds: float = 30.0, jpeg_quality: int = 72):
        self.root_dir = root_dir
        self.fps = max(2, min(30, int(fps)))
        self.buffer_seconds = max(8.0, float(buffer_seconds))
        self.jpeg_quality = max(45, min(92, int(jpeg_quality)))
        self._lock = threading.Lock()
        self._frames: deque[tuple[float, bytes]] = deque()
        self._session_dir: Path | None = None
        self._fight_index = 0

    def start_session(self) -> None:
        stamp = time.strftime("%Y%m%d_%H%M%S")
        self._session_dir = self.root_dir / "data" / "fight_clips" / stamp
        self._session_dir.mkdir(parents=True, exist_ok=True)
        with self._lock:
            self._frames.clear()
            self._fight_index = 0

    def on_frame(self, sample: FrameSample) -> None:
        ok, encoded = cv2.imencode(
            ".jpg",
            sample.frame_bgr,
            [int(cv2.IMWRITE_JPEG_QUALITY), self.jpeg_quality],
        )
        if not ok:
            return
        cutoff = sample.timestamp - self.buffer_seconds
        payload = encoded.tobytes()
        with self._lock:
            self._frames.append((sample.timestamp, payload))
            while self._frames and self._frames[0][0] < cutoff:
                self._frames.popleft()

    def _frames_between(self, start: float, end: float) -> list[tuple[float, bytes]]:
        with self._lock:
            return [(ts, payload) for ts, payload in self._frames if start <= ts <= end]

    def save_fight(
        self,
        started_at: float,
        ended_at: float,
        *,
        pre_seconds: float,
        post_seconds: float,
        skill_events: list[SkillEvent],
        feedback_payload: dict,
    ) -> tuple[Path | None, Path | None]:
        if self._session_dir is None:
            return None, None

        clip_start = started_at - max(0.0, pre_seconds)
        clip_end = ended_at + max(0.0, post_seconds)
        frames = self._frames_between(clip_start, clip_end)
        if len(frames) < 3:
            return None, None

        decoded_first = cv2.imdecode(np.frombuffer(frames[0][1], np.uint8), cv2.IMREAD_COLOR)
        if decoded_first is None:
            return None, None
        h, w = decoded_first.shape[:2]

        with self._lock:
            self._fight_index += 1
            index = self._fight_index

        stamp = time.strftime("%H%M%S", time.localtime(started_at))
        stem = f"fight_{index:03d}_{stamp}"
        mp4_path = self._session_dir / f"{stem}.mp4"
        writer = cv2.VideoWriter(
            str(mp4_path),
            cv2.VideoWriter_fourcc(*"mp4v"),
            float(self.fps),
            (w, h),
        )
        video_path: Path | None = mp4_path
        if not writer.isOpened():
            avi_path = self._session_dir / f"{stem}.avi"
            writer = cv2.VideoWriter(
                str(avi_path),
                cv2.VideoWriter_fourcc(*"MJPG"),
                float(self.fps),
                (w, h),
            )
            video_path = avi_path if writer.isOpened() else None

        if video_path is not None:
            try:
                for _ts, payload in frames:
                    frame = cv2.imdecode(np.frombuffer(payload, np.uint8), cv2.IMREAD_COLOR)
                    if frame is None:
                        continue
                    if frame.shape[1] != w or frame.shape[0] != h:
                        frame = cv2.resize(frame, (w, h), interpolation=cv2.INTER_AREA)
                    writer.write(frame)
            finally:
                writer.release()

        metadata_path = self._session_dir / f"{stem}.json"
        metadata = {
            "clip_start": clip_start,
            "clip_end": clip_end,
            "fight_start": started_at,
            "fight_end": ended_at,
            "video": str(video_path) if video_path else None,
            "skill_events": [asdict(event) for event in skill_events],
            "feedback": feedback_payload,
        }
        metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
        return video_path, metadata_path
