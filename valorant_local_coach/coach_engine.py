from __future__ import annotations

import json
import statistics
import threading
import time
from collections import deque
from dataclasses import asdict, dataclass
from pathlib import Path

import cv2

from input_tracker import KeyEvent, ShotEvent
from pro_profile import ProMovementProfile
from screen_capture import FrameSample


@dataclass(slots=True)
class FightFeedback:
    started_at: float
    ended_at: float
    shots: int
    moving_shots: int
    moving_shot_ratio: float
    walk_shots: int
    walk_shot_ratio: float
    crouch_shots: int
    crouch_shot_ratio: float
    median_stop_to_shot_ms: float | None
    opposite_tap_ratio: float
    average_motion: float
    peak_motion: float
    crouch_spray: bool
    score: int
    title: str
    messages: list[str]


class CoachEngine:
    def __init__(self, config: dict, root_dir: Path):
        self.config = config
        self.root_dir = root_dir
        self.session_started_at = 0.0
        self._lock = threading.Lock()
        self._shots: deque[ShotEvent] = deque(maxlen=5000)
        self._key_events: deque[KeyEvent] = deque(maxlen=20000)
        self._frames: deque[tuple[float, float]] = deque(maxlen=3000)
        self._last_frame_image = None
        self._fights: list[FightFeedback] = []
        self._last_finalized_shot_ts = 0.0
        self._training_dir: Path | None = None
        self._last_training_save_ms = 0.0
        self.pro_profile = ProMovementProfile(self.root_dir / "pro_baseline.json")

    @property
    def fights(self) -> list[FightFeedback]:
        with self._lock:
            return list(self._fights)

    def reload_pro_profile(self) -> None:
        self.pro_profile.reload()

    def start_session(self) -> None:
        now = time.time()
        with self._lock:
            self.session_started_at = now
            self._shots.clear()
            self._key_events.clear()
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

    def on_key_event(self, event: KeyEvent) -> None:
        with self._lock:
            self._key_events.append(event)

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
                labels = [
                    f"moving{int(nearest_shot.moving)}",
                    f"walk{int(nearest_shot.walking)}",
                    f"crouch{int(nearest_shot.crouching)}",
                    f"opp{int(nearest_shot.opposite_tap_recent)}",
                ]
                filename = self._training_dir / f"{int(current_ms)}_{'_'.join(labels)}.jpg"
                cv2.imwrite(str(filename), sample.frame_bgr, [int(cv2.IMWRITE_JPEG_QUALITY), 82])

    def _frame_motion_between(self, start: float, end: float) -> list[float]:
        return [score for ts, score in self._frames if start <= ts <= end]

    @staticmethod
    def _median_stop_to_shot(events: list[ShotEvent]) -> float | None:
        values = [
            float(shot.stop_to_shot_ms)
            for shot in events
            if shot.stop_to_shot_ms is not None and 0 <= float(shot.stop_to_shot_ms) <= 1200
        ]
        if not values:
            return None
        return round(statistics.median(values), 1)

    def _fight_metrics(self, events: list[ShotEvent]) -> dict:
        total = max(1, len(events))
        moving = sum(1 for shot in events if shot.moving)
        walk = sum(1 for shot in events if shot.walking)
        crouch = sum(1 for shot in events if shot.crouching)
        opposite = sum(1 for shot in events if shot.opposite_tap_recent)
        crouch_ratio = crouch / total
        long_burst = int(self.config.get("long_burst_shot_count", 6))
        crouch_spray = len(events) >= long_burst and crouch_ratio >= 0.5
        return {
            "moving_shot_ratio": moving / total,
            "walk_shot_ratio": walk / total,
            "crouch_shot_ratio": crouch_ratio,
            "median_stop_to_shot_ms": self._median_stop_to_shot(events),
            "opposite_tap_ratio": opposite / total,
            "crouch_spray_ratio": 1.0 if crouch_spray else 0.0,
        }

    def _build_feedback(self, events: list[ShotEvent]) -> FightFeedback:
        start = events[0].timestamp
        end = events[-1].timestamp
        moving_shots = sum(1 for shot in events if shot.moving)
        walk_shots = sum(1 for shot in events if shot.walking)
        crouch_shots = sum(1 for shot in events if shot.crouching)
        total = max(1, len(events))
        ratio = moving_shots / total
        walk_ratio = walk_shots / total
        crouch_ratio = crouch_shots / total
        stop_ms = self._median_stop_to_shot(events)
        opposite_ratio = sum(1 for shot in events if shot.opposite_tap_recent) / total
        motion = self._frame_motion_between(start - 0.15, end + 0.35)
        avg_motion = statistics.fmean(motion) if motion else 0.0
        peak_motion = max(motion) if motion else 0.0
        long_burst = int(self.config.get("long_burst_shot_count", 6))
        crouch_spray = len(events) >= long_burst and crouch_ratio >= 0.5

        messages: list[str] = []
        deductions = 0

        moving_warn = float(self.config.get("moving_shot_warn_ratio", 0.25))
        if ratio >= 0.5:
            deductions += 30
            messages.append(
                f"이 교전에서 {moving_shots}/{len(events)}발을 WASD 입력 중 발사했습니다. "
                "첫 탄 전에 이동 입력을 끊는 타이밍을 우선 교정하세요."
            )
        elif ratio >= moving_warn:
            deductions += 17
            messages.append(
                f"이동 중 발사 비율이 {ratio * 100:.0f}%입니다. 피킹과 첫 탄 사이의 정지 구간을 조금 더 분리하세요."
            )
        else:
            messages.append("WASD를 끊고 사격하는 비율은 비교적 안정적이었습니다.")

        if walk_ratio >= float(self.config.get("walk_shot_warn_ratio", 0.18)):
            deductions += 8
            messages.append(
                f"Shift 워크 상태 사격이 {walk_ratio * 100:.0f}%입니다. 조용한 접근용 워크와 실제 교전 사격을 분리해 보세요."
            )

        if crouch_spray:
            deductions += 12
            messages.append(
                f"{len(events)}발 교전의 절반 이상을 Ctrl로 앉은 채 발사했습니다. "
                "앉은 스프레이가 습관화되면 재피킹과 회피가 어려워질 수 있으니 필요할 때만 사용하세요."
            )
        elif crouch_ratio >= float(self.config.get("crouch_shot_warn_ratio", 0.55)):
            deductions += 7
            messages.append(f"Ctrl 앉은 사격 비율이 {crouch_ratio * 100:.0f}%로 높은 편입니다.")
        elif crouch_shots:
            messages.append(f"Ctrl 앉은 사격은 {crouch_shots}/{len(events)}발로 제한적으로 사용했습니다.")

        if stop_ms is not None:
            too_fast = float(self.config.get("stop_to_shot_too_fast_ms", 35))
            slow = float(self.config.get("stop_to_shot_slow_ms", 320))
            if stop_ms < too_fast:
                deductions += 8
                messages.append(
                    f"이동키 해제 후 첫 탄까지 중앙값이 {stop_ms:.0f}ms입니다. 너무 즉시 쏘는 패턴이 있어 완전 정지 전 발사 가능성을 확인하세요."
                )
            elif stop_ms > slow:
                deductions += 5
                messages.append(
                    f"정지 후 첫 탄까지 중앙값이 {stop_ms:.0f}ms입니다. 정확도는 유지하되 피킹-사격 연결을 조금 더 빠르게 만들 여지가 있습니다."
                )
            else:
                messages.append(f"정지→사격 연결 중앙값은 {stop_ms:.0f}ms였습니다.")

        if opposite_ratio >= 0.25:
            messages.append(
                f"사격 전 A↔D/W↔S 반대방향 탭이 {opposite_ratio * 100:.0f}%에서 감지되었습니다. 방향전환 후 정지 패턴을 잘 활용하고 있습니다."
            )

        if len(events) >= long_burst:
            deductions += 12
            messages.append(
                f"한 교전에서 {len(events)}발을 연속 입력했습니다. 중거리에서는 짧은 버스트 후 재이동하는 패턴도 섞어 보세요."
            )

        motion_warn = float(self.config.get("camera_motion_warn_threshold", 18.0))
        if avg_motion >= motion_warn:
            deductions += 10
            messages.append("사격 구간 화면 움직임이 큰 편입니다. 플릭 뒤 조준 안정 구간을 짧게 확보하세요.")

        metrics = self._fight_metrics(events)
        reference_notes = self.pro_profile.compare(metrics)
        if reference_notes:
            messages.append(f"참고 프로필 비교: {reference_notes[0]}")

        score = max(0, min(100, 100 - deductions))
        if score >= 88:
            title = "프로 스타일에 가까운 안정적 무빙"
        elif score >= 68:
            title = "무빙 타이밍 개선 여지"
        else:
            title = "정지/워크/앉기 타이밍 교정 필요"

        return FightFeedback(
            started_at=start,
            ended_at=end,
            shots=len(events),
            moving_shots=moving_shots,
            moving_shot_ratio=ratio,
            walk_shots=walk_shots,
            walk_shot_ratio=walk_ratio,
            crouch_shots=crouch_shots,
            crouch_shot_ratio=crouch_ratio,
            median_stop_to_shot_ms=stop_ms,
            opposite_tap_ratio=opposite_ratio,
            average_motion=avg_motion,
            peak_motion=peak_motion,
            crouch_spray=crouch_spray,
            score=score,
            title=title,
            messages=messages[:6],
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

    def _key_hold_seconds(self, key: str, ended_at: float) -> float:
        events = [event for event in self._key_events if event.key == key]
        total = 0.0
        pressed_at: float | None = None
        for event in events:
            if event.pressed and pressed_at is None:
                pressed_at = event.timestamp
            elif not event.pressed and pressed_at is not None:
                total += max(0.0, event.timestamp - pressed_at)
                pressed_at = None
        if pressed_at is not None:
            total += max(0.0, ended_at - pressed_at)
        return round(total, 2)

    def session_summary(self) -> dict:
        with self._lock:
            fights = list(self._fights)
            shots = list(self._shots)

        ended_at = time.time()
        total_shots = len(shots)
        moving_shots = sum(1 for shot in shots if shot.moving)
        walk_shots = sum(1 for shot in shots if shot.walking)
        crouch_shots = sum(1 for shot in shots if shot.crouching)
        opposite_shots = sum(1 for shot in shots if shot.opposite_tap_recent)
        ratio = moving_shots / max(1, total_shots)
        walk_ratio = walk_shots / max(1, total_shots)
        crouch_ratio = crouch_shots / max(1, total_shots)
        opposite_ratio = opposite_shots / max(1, total_shots)
        stop_ms = self._median_stop_to_shot(shots)
        crouch_spray_fights = sum(1 for fight in fights if fight.crouch_spray)
        crouch_spray_ratio = crouch_spray_fights / max(1, len(fights))
        scores = [fight.score for fight in fights]
        average_score = round(statistics.fmean(scores), 1) if scores else None

        movement_metrics = {
            "moving_shot_ratio": ratio,
            "walk_shot_ratio": walk_ratio,
            "crouch_shot_ratio": crouch_ratio,
            "crouch_spray_ratio": crouch_spray_ratio,
            "median_stop_to_shot_ms": stop_ms,
            "opposite_tap_ratio": opposite_ratio,
            "shift_hold_seconds": self._key_hold_seconds("shift", ended_at),
            "ctrl_hold_seconds": self._key_hold_seconds("ctrl", ended_at),
        }

        priorities: list[str] = []
        if ratio >= float(self.config.get("moving_shot_warn_ratio", 0.25)):
            priorities.append("WASD를 놓고 첫 탄을 발사하는 정지 타이밍")
        if walk_ratio >= float(self.config.get("walk_shot_warn_ratio", 0.18)):
            priorities.append("Shift 워크와 교전 사격 분리")
        if crouch_ratio >= float(self.config.get("crouch_shot_warn_ratio", 0.55)) or crouch_spray_ratio >= 0.35:
            priorities.append("Ctrl 앉은 스프레이 의존도 줄이기")
        if stop_ms is not None and stop_ms > float(self.config.get("stop_to_shot_slow_ms", 320)):
            priorities.append("정지 후 첫 탄 연결 속도")
        if fights and statistics.fmean([fight.shots for fight in fights]) >= float(self.config.get("long_burst_shot_count", 6)):
            priorities.append("긴 스프레이 후 재이동/버스트 전환")

        reference_notes = self.pro_profile.compare(movement_metrics)
        if not priorities and reference_notes:
            priorities.append(reference_notes[0])
        if not priorities:
            priorities.append("기본 무빙-사격 연결은 안정적입니다. 실제 적/크로스헤어 Vision 모델을 추가하면 더 정밀하게 평가할 수 있습니다.")

        return {
            "started_at": self.session_started_at,
            "ended_at": ended_at,
            "fight_count": len(fights),
            "shots": total_shots,
            "moving_shots": moving_shots,
            "walk_shots": walk_shots,
            "crouch_shots": crouch_shots,
            "moving_shot_ratio": ratio,
            "average_fight_score": average_score,
            "movement_metrics": movement_metrics,
            "reference_profile": self.pro_profile.data,
            "reference_comparison": reference_notes,
            "priorities": priorities[:4],
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
