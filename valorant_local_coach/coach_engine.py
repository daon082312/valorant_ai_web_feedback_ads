from __future__ import annotations

import json
import statistics
import threading
import time
from collections import deque
from dataclasses import asdict, dataclass
from pathlib import Path

import cv2

from input_tracker import ShotEvent
from screen_capture import FrameSample


@dataclass(slots=True)
class FightFeedback:
    started_at: float
    ended_at: float
    shots: int
    moving_shots: int
    moving_shot_ratio: float
    average_motion: float
    peak_motion: float
    score: int
    title: str
    messages: list[str]


class CoachEngine:
    def __init__(self, config: dict, root_dir: Path):
        self.config = config
        self.root_dir = root_dir
        self.session_started_at = 0.0
        self._lock = threading.Lock()
        self._shots: deque[ShotEvent] = deque(maxlen=250)
        self._frames: deque[tuple[float, float]] = deque(maxlen=1500)
        self._last_frame_image = None
        self._fights: list[FightFeedback] = []
        self._last_finalized_shot_ts = 0.0
        self._training_dir: Path | None = None
        self._last_training_save_ms = 0.0

    @property
    def fights(self) -> list[FightFeedback]:
        with self._lock:
            return list(self._fights)

    def start_session(self) -> None:
        now = time.time()
        with self._lock:
            self.session_started_at = now
            self._shots.clear()
            self._frames.clear()
            self._fights.clear()
            self._last_finalized_shot_ts = 0.0
        stamp = time.strftime("%Y%m%d_%H%M%S", time.localtime(now))
        self._training_dir = self.root_dir / "data" / "training_frames" / stamp
        if self.config.get("save_training_frames", True):
            self._training_dir.mkdir(parents=True, exist_ok=True)

    def on_shot(self, event: ShotEvent) -> None:
        with self._lock:
            self._shots.append(event)

    def on_frame(self, sample: FrameSample) -> None:
        with self._lock:
            self._frames.append((sample.timestamp, sample.motion_score))
            self._last_frame_image = sample.frame_bgr

            if not self._shots or not self.config.get("save_training_frames", True):
                return
            nearest_shot = self._shots[-1]
            delta_ms = abs(sample.timestamp - nearest_shot.timestamp) * 1000.0
            interval = float(self.config.get("training_frame_interval_ms", 120))
            current_ms = sample.timestamp * 1000.0
            should_save = delta_ms <= 250 and (current_ms - self._last_training_save_ms) >= interval
            if should_save and self._training_dir is not None:
                self._last_training_save_ms = current_ms
                filename = self._training_dir / f"{int(current_ms)}_moving{int(nearest_shot.moving)}.jpg"
                cv2.imwrite(str(filename), sample.frame_bgr, [int(cv2.IMWRITE_JPEG_QUALITY), 82])

    def _frame_motion_between(self, start: float, end: float) -> list[float]:
        return [score for ts, score in self._frames if start <= ts <= end]

    def _build_feedback(self, events: list[ShotEvent]) -> FightFeedback:
        start = events[0].timestamp
        end = events[-1].timestamp
        moving_shots = sum(1 for shot in events if shot.moving)
        ratio = moving_shots / max(1, len(events))
        motion = self._frame_motion_between(start - 0.15, end + 0.35)
        avg_motion = statistics.fmean(motion) if motion else 0.0
        peak_motion = max(motion) if motion else 0.0

        messages: list[str] = []
        deductions = 0

        moving_warn = float(self.config.get("moving_shot_warn_ratio", 0.25))
        if ratio >= 0.5:
            deductions += 32
            messages.append(
                f"이 교전에서 {moving_shots}/{len(events)}발을 이동키 입력 중 발사했습니다. "
                "첫 탄 전에 이동키를 놓고 짧게 정지하는 습관을 우선 교정하세요."
            )
        elif ratio >= moving_warn:
            deductions += 18
            messages.append(
                f"이동 중 발사 비율이 {ratio * 100:.0f}%입니다. 피킹 후 첫 탄 직전에 정지 타이밍을 조금 더 분리하세요."
            )
        else:
            messages.append("사격 시 이동 입력 억제가 비교적 안정적이었습니다.")

        long_burst = int(self.config.get("long_burst_shot_count", 6))
        if len(events) >= long_burst:
            deductions += 16
            messages.append(
                f"한 교전에서 {len(events)}발을 연속 입력했습니다. 중거리에서는 2~4발 단위로 끊어 반동을 재설정해 보세요."
            )

        motion_warn = float(self.config.get("camera_motion_warn_threshold", 18.0))
        if avg_motion >= motion_warn:
            deductions += 14
            messages.append(
                "사격 구간의 화면 움직임이 큰 편입니다. 급한 플릭 뒤에는 조준을 멈추는 짧은 안정 구간을 만들어 보세요."
            )
        elif avg_motion <= motion_warn * 0.45:
            messages.append("사격 구간의 화면 흔들림은 낮은 편이었습니다.")

        score = max(0, min(100, 100 - deductions))
        if score >= 85:
            title = "안정적인 교전"
        elif score >= 65:
            title = "개선 여지가 있는 교전"
        else:
            title = "움직임/사격 타이밍 교정 필요"

        return FightFeedback(
            started_at=start,
            ended_at=end,
            shots=len(events),
            moving_shots=moving_shots,
            moving_shot_ratio=ratio,
            average_motion=avg_motion,
            peak_motion=peak_motion,
            score=score,
            title=title,
            messages=messages[:4],
        )

    def poll_finalized_fight(self) -> FightFeedback | None:
        gap = float(self.config.get("fight_gap_seconds", 1.8))
        minimum = int(self.config.get("min_shots_per_fight", 2))
        now = time.time()

        with self._lock:
            candidates = [shot for shot in self._shots if shot.timestamp > self._last_finalized_shot_ts]
            if not candidates:
                return None
            if now - candidates[-1].timestamp < gap:
                return None

            group = [candidates[0]]
            for shot in candidates[1:]:
                if shot.timestamp - group[-1].timestamp > gap:
                    break
                group.append(shot)

            self._last_finalized_shot_ts = group[-1].timestamp
            if len(group) < minimum:
                return None

            feedback = self._build_feedback(group)
            self._fights.append(feedback)
            return feedback

    def session_summary(self) -> dict:
        with self._lock:
            fights = list(self._fights)
            shots = list(self._shots)

        total_shots = len(shots)
        moving_shots = sum(1 for shot in shots if shot.moving)
        ratio = moving_shots / max(1, total_shots)
        scores = [fight.score for fight in fights]
        average_score = round(statistics.fmean(scores), 1) if scores else None

        priorities: list[str] = []
        if ratio >= float(self.config.get("moving_shot_warn_ratio", 0.25)):
            priorities.append("이동키를 놓은 뒤 첫 탄을 발사하는 정지 타이밍")
        if fights and statistics.fmean([fight.shots for fight in fights]) >= float(self.config.get("long_burst_shot_count", 6)):
            priorities.append("긴 스프레이를 줄이고 짧은 버스트 사용")
        if fights and statistics.fmean([fight.average_motion for fight in fights]) >= float(self.config.get("camera_motion_warn_threshold", 18.0)):
            priorities.append("플릭 직후 조준 안정 구간 확보")
        if not priorities:
            priorities.append("현재 기본 사격 습관은 안정적입니다. 다음 단계로 적 위치/크로스헤어 모델 학습을 권장합니다.")

        return {
            "started_at": self.session_started_at,
            "ended_at": time.time(),
            "fight_count": len(fights),
            "shots": total_shots,
            "moving_shots": moving_shots,
            "moving_shot_ratio": ratio,
            "average_fight_score": average_score,
            "priorities": priorities[:3],
            "fights": [asdict(fight) for fight in fights],
        }

    def save_session(self) -> Path:
        summary = self.session_summary()
        sessions_dir = self.root_dir / "data" / "sessions"
        sessions_dir.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d_%H%M%S")
        path = sessions_dir / f"session_{stamp}.json"
        path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        return path
