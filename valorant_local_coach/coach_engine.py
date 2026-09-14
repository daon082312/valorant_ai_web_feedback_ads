from __future__ import annotations

import json
import statistics
import threading
import time
from collections import Counter, deque
from dataclasses import asdict, dataclass
from pathlib import Path

import cv2

from input_tracker import KeyEvent, ShotEvent, SkillEvent
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
    movement_score: int
    skill_timing_score: int | None
    title: str
    movement_messages: list[str]
    skill_messages: list[str]
    skill_uses: list[dict]

    @property
    def score(self) -> int:
        return self.movement_score


class CoachEngine:
    def __init__(self, config: dict, root_dir: Path):
        self.config = config; self.root_dir = root_dir; self.session_started_at = 0.0; self._lock = threading.Lock()
        self._shots: deque[ShotEvent] = deque(maxlen=5000); self._key_events: deque[KeyEvent] = deque(maxlen=20000); self._skill_events: deque[SkillEvent] = deque(maxlen=5000); self._frames: deque[tuple[float, float]] = deque(maxlen=3000); self._fights: list[FightFeedback] = []; self._last_finalized_shot_ts = 0.0; self._training_dir: Path | None = None; self._last_training_save_ms = 0.0; self.pro_profile = ProMovementProfile(self.root_dir / "pro_baseline.json")

    @property
    def fights(self) -> list[FightFeedback]:
        with self._lock: return list(self._fights)

    def reload_pro_profile(self) -> None: self.pro_profile.reload()

    def start_session(self) -> None:
        now = time.time()
        with self._lock:
            self.session_started_at = now; self._shots.clear(); self._key_events.clear(); self._skill_events.clear(); self._frames.clear(); self._fights.clear(); self._last_finalized_shot_ts = 0.0
        stamp = time.strftime("%Y%m%d_%H%M%S", time.localtime(now)); self._training_dir = self.root_dir / "data" / "training_frames" / stamp
        if self.config.get("save_training_frames", True): self._training_dir.mkdir(parents=True, exist_ok=True)

    def on_shot(self, event: ShotEvent) -> None:
        with self._lock: self._shots.append(event)
    def on_key_event(self, event: KeyEvent) -> None:
        with self._lock: self._key_events.append(event)
    def on_skill(self, event: SkillEvent) -> None:
        with self._lock: self._skill_events.append(event)

    def on_frame(self, sample: FrameSample) -> None:
        with self._lock:
            self._frames.append((sample.timestamp, sample.motion_score))
            if not self._shots or not self.config.get("save_training_frames", True): return
            nearest_shot = self._shots[-1]; delta_ms = abs(sample.timestamp - nearest_shot.timestamp) * 1000.0; interval = float(self.config.get("training_frame_interval_ms", 120)); current_ms = sample.timestamp * 1000.0
            if delta_ms <= 250 and (current_ms - self._last_training_save_ms) >= interval and self._training_dir is not None:
                self._last_training_save_ms = current_ms; labels=[f"moving{int(nearest_shot.moving)}",f"walk{int(nearest_shot.walking)}",f"crouch{int(nearest_shot.crouching)}",f"opp{int(nearest_shot.opposite_tap_recent)}"]; filename=self._training_dir / f"{int(current_ms)}_{'_'.join(labels)}.jpg"; cv2.imwrite(str(filename), sample.frame_bgr, [int(cv2.IMWRITE_JPEG_QUALITY),82])

    def _frame_motion_between(self,start,end): return [score for ts,score in self._frames if start<=ts<=end]
    @staticmethod
    def _median_stop_to_shot(events):
        values=[float(s.stop_to_shot_ms) for s in events if s.stop_to_shot_ms is not None and 0<=float(s.stop_to_shot_ms)<=1200]; return round(statistics.median(values),1) if values else None
    def _fight_metrics(self,events):
        total=max(1,len(events)); moving=sum(1 for s in events if s.moving); walk=sum(1 for s in events if s.walking); crouch=sum(1 for s in events if s.crouching); opposite=sum(1 for s in events if s.opposite_tap_recent); crouch_ratio=crouch/total; long_burst=int(self.config.get("long_burst_shot_count",6))
        return {"moving_shot_ratio":moving/total,"walk_shot_ratio":walk/total,"crouch_shot_ratio":crouch_ratio,"median_stop_to_shot_ms":self._median_stop_to_shot(events),"opposite_tap_ratio":opposite/total,"crouch_spray_ratio":1.0 if len(events)>=long_burst and crouch_ratio>=0.5 else 0.0}

    def _skill_events_for_fight_unlocked(self,start,end):
        pre=float(self.config.get("skill_pre_fight_seconds",4.0)); post=float(self.config.get("skill_post_fight_seconds",1.5)); return [e for e in self._skill_events if start-pre<=e.timestamp<=end+post]
    def skill_events_for_fight(self,fight):
        with self._lock: return list(self._skill_events_for_fight_unlocked(fight.started_at,fight.ended_at))

    def _skill_feedback(self,start,end):
        events=self._skill_events_for_fight_unlocked(start,end)
        if not events: return None,["이 교전 주변에는 HUD에서 확인된 스킬 사용이 없었습니다. 스킬이 꼭 필요했던 상황인지는 현재 측정값만으로 단정하지 않습니다."],[]
        uses=[]; pre_count=during_count=late_count=0
        for event in events:
            relative=event.timestamp-start
            if event.timestamp<start-0.15: phase="교전 전 셋업"; pre_count+=1
            elif event.timestamp<=end+0.45: phase="교전 중"; during_count+=1
            else: phase="교전 후"; late_count+=1
            uses.append({"slot_id":event.slot_id,"label":event.label,"binding":event.binding,"relative_seconds":round(relative,2),"phase":phase,"moving":event.moving,"walking":event.walking,"crouching":event.crouching,"source":getattr(event,"source","unknown"),"confidence":getattr(event,"confidence",None)})
        messages=[]; score=82
        if pre_count: score+=min(10,pre_count*5); messages.append(f"교전 전에 스킬을 {pre_count}회 사용해 진입/교전을 준비한 패턴이 기록됐습니다.")
        if during_count: messages.append(f"교전 중 스킬 사용이 {during_count}회 있었습니다. 저장된 교전 클립에서 실제 효과와 타이밍을 함께 확인하세요.")
        if late_count and pre_count==0 and during_count==0: score-=15; messages.append(f"스킬 {late_count}회가 교전 종료 뒤에만 사용됐습니다. 필요한 스킬이었다면 반응이 늦었을 가능성을 점검하세요.")
        elif late_count: score-=min(6,late_count*2); messages.append(f"교전 후 HUD에서 확인된 스킬 사용도 {late_count}회 있었습니다. 후속 교전 대비인지 늦은 사용인지 클립으로 확인하세요.")
        repeated=Counter(u["slot_id"] for u in uses)
        if max(repeated.values(),default=0)>=2: messages.append("같은 스킬 슬롯의 반복 사용이 HUD 변화로 확인됐습니다. 실제 적중/효과는 교전 화면 분석으로 추가 확인이 필요합니다.")
        if any(u["slot_id"]=="ultimate" for u in uses): messages.append("궁극기 사용이 HUD 변화로 확인됐습니다. 실제 킬/공간 확보 가치는 교전 영상 Vision 분석이 추가되어야 정확합니다.")
        return max(0,min(100,score)),messages[:5],uses

    def _build_feedback(self,events):
        start=events[0].timestamp; end=events[-1].timestamp; moving_shots=sum(1 for s in events if s.moving); walk_shots=sum(1 for s in events if s.walking); crouch_shots=sum(1 for s in events if s.crouching); total=max(1,len(events)); ratio=moving_shots/total; walk_ratio=walk_shots/total; crouch_ratio=crouch_shots/total; stop_ms=self._median_stop_to_shot(events); opposite_ratio=sum(1 for s in events if s.opposite_tap_recent)/total; motion=self._frame_motion_between(start-0.15,end+0.35); avg_motion=statistics.fmean(motion) if motion else 0.0; peak_motion=max(motion) if motion else 0.0; long_burst=int(self.config.get("long_burst_shot_count",6)); crouch_spray=len(events)>=long_burst and crouch_ratio>=0.5
        messages=[]; deductions=0; moving_warn=float(self.config.get("moving_shot_warn_ratio",0.25))
        if ratio>=0.5: deductions+=30; messages.append(f"이 교전에서 {moving_shots}/{len(events)}발을 WASD 입력 중 발사했습니다. 첫 탄 전에 이동 입력을 끊는 타이밍을 우선 교정하세요.")
        elif ratio>=moving_warn: deductions+=17; messages.append(f"이동 중 발사 비율이 {ratio*100:.0f}%입니다. 피킹과 첫 탄 사이의 정지 구간을 조금 더 분리하세요.")
        else: messages.append("WASD를 끊고 사격하는 비율은 비교적 안정적이었습니다.")
        if walk_ratio>=float(self.config.get("walk_shot_warn_ratio",0.18)): deductions+=8; messages.append(f"Shift 워크 상태 사격이 {walk_ratio*100:.0f}%입니다. 조용한 접근용 워크와 실제 교전 사격을 분리해 보세요.")
        if crouch_spray: deductions+=12; messages.append(f"{len(events)}발 교전의 절반 이상을 Ctrl로 앉은 채 발사했습니다. 앉은 스프레이가 습관화되면 재피킹과 회피가 어려워질 수 있습니다.")
        elif crouch_ratio>=float(self.config.get("crouch_shot_warn_ratio",0.55)): deductions+=7; messages.append(f"Ctrl 앉은 사격 비율이 {crouch_ratio*100:.0f}%로 높은 편입니다.")
        elif crouch_shots: messages.append(f"Ctrl 앉은 사격은 {crouch_shots}/{len(events)}발로 제한적으로 사용했습니다.")
        if stop_ms is not None:
            too_fast=float(self.config.get("stop_to_shot_too_fast_ms",35)); slow=float(self.config.get("stop_to_shot_slow_ms",320))
            if stop_ms<too_fast: deductions+=8; messages.append(f"이동키 해제 후 첫 탄까지 중앙값이 {stop_ms:.0f}ms입니다. 너무 즉시 쏘는 패턴이 있어 완전 정지 전 발사 가능성을 확인하세요.")
            elif stop_ms>slow: deductions+=5; messages.append(f"정지 후 첫 탄까지 중앙값이 {stop_ms:.0f}ms입니다. 정확도는 유지하되 피킹-사격 연결을 조금 더 빠르게 만들 여지가 있습니다.")
            else: messages.append(f"정지→사격 연결 중앙값은 {stop_ms:.0f}ms였습니다.")
        if opposite_ratio>=0.25: messages.append(f"사격 전 A↔D/W↔S 반대방향 탭이 {opposite_ratio*100:.0f}%에서 감지되었습니다. 방향전환 후 정지 패턴을 잘 활용하고 있습니다.")
        if len(events)>=long_burst: deductions+=12; messages.append(f"한 교전에서 {len(events)}발을 연속 발사했습니다. 중거리에서는 짧은 버스트 후 재이동하는 패턴도 섞어 보세요.")
        if avg_motion>=float(self.config.get("camera_motion_warn_threshold",18.0)): deductions+=10; messages.append("사격 구간 화면 움직임이 큰 편입니다. 플릭 뒤 조준 안정 구간을 짧게 확보하세요.")
        reference_notes=self.pro_profile.compare(self._fight_metrics(events))
        if reference_notes: messages.append(f"참고 프로필 비교: {reference_notes[0]}")
        movement_score=max(0,min(100,100-deductions)); title="프로 스타일에 가까운 안정적 무빙" if movement_score>=88 else "무빙 타이밍 개선 여지" if movement_score>=68 else "정지/워크/앉기 타이밍 교정 필요"; skill_score,skill_messages,skill_uses=self._skill_feedback(start,end)
        return FightFeedback(start,end,len(events),moving_shots,ratio,walk_shots,walk_ratio,crouch_shots,crouch_ratio,stop_ms,opposite_ratio,avg_motion,peak_motion,crouch_spray,movement_score,skill_score,title,messages[:6],skill_messages,skill_uses)

    def poll_finalized_fight(self):
        gap=float(self.config.get("fight_gap_seconds",1.8)); minimum=int(self.config.get("min_shots_per_fight",2)); now=time.time()
        with self._lock:
            candidates=[s for s in self._shots if s.timestamp>self._last_finalized_shot_ts]
            if not candidates: return None
            group=[candidates[0]]; closed=False
            for shot in candidates[1:]:
                if shot.timestamp-group[-1].timestamp>gap: closed=True; break
                group.append(shot)
            if not closed and now-group[-1].timestamp<gap: return None
            self._last_finalized_shot_ts=group[-1].timestamp
            if len(group)<minimum: return None
            feedback=self._build_feedback(group); self._fights.append(feedback); return feedback

    def _key_hold_seconds(self,key,ended_at,key_events):
        events=[e for e in key_events if e.key==key]; total=0.0; pressed_at=None
        for e in events:
            if e.pressed and pressed_at is None: pressed_at=e.timestamp
            elif not e.pressed and pressed_at is not None: total+=max(0.0,e.timestamp-pressed_at); pressed_at=None
        if pressed_at is not None: total+=max(0.0,ended_at-pressed_at)
        return round(total,2)

    def session_summary(self):
        with self._lock: fights=list(self._fights); shots=list(self._shots); key_events=list(self._key_events); skills=list(self._skill_events)
        ended_at=time.time(); total_shots=len(shots); moving_shots=sum(1 for s in shots if s.moving); walk_shots=sum(1 for s in shots if s.walking); crouch_shots=sum(1 for s in shots if s.crouching); opposite_shots=sum(1 for s in shots if s.opposite_tap_recent); ratio=moving_shots/max(1,total_shots); walk_ratio=walk_shots/max(1,total_shots); crouch_ratio=crouch_shots/max(1,total_shots); opposite_ratio=opposite_shots/max(1,total_shots); stop_ms=self._median_stop_to_shot(shots); crouch_spray_ratio=sum(1 for f in fights if f.crouch_spray)/max(1,len(fights)); scores=[f.movement_score for f in fights]; average_score=round(statistics.fmean(scores),1) if scores else None
        movement_metrics={"moving_shot_ratio":ratio,"walk_shot_ratio":walk_ratio,"crouch_shot_ratio":crouch_ratio,"crouch_spray_ratio":crouch_spray_ratio,"median_stop_to_shot_ms":stop_ms,"opposite_tap_ratio":opposite_ratio,"shift_hold_seconds":self._key_hold_seconds("shift",ended_at,key_events),"ctrl_hold_seconds":self._key_hold_seconds("ctrl",ended_at,key_events)}
        priorities=[]
        if ratio>=float(self.config.get("moving_shot_warn_ratio",0.25)): priorities.append("WASD를 놓고 첫 탄을 발사하는 정지 타이밍")
        if walk_ratio>=float(self.config.get("walk_shot_warn_ratio",0.18)): priorities.append("Shift 워크와 교전 사격 분리")
        if crouch_ratio>=float(self.config.get("crouch_shot_warn_ratio",0.55)) or crouch_spray_ratio>=0.35: priorities.append("Ctrl 앉은 스프레이 의존도 줄이기")
        if stop_ms is not None and stop_ms>float(self.config.get("stop_to_shot_slow_ms",320)): priorities.append("정지 후 첫 탄 연결 속도")
        if fights and statistics.fmean([f.shots for f in fights])>=float(self.config.get("long_burst_shot_count",6)): priorities.append("긴 스프레이 후 재이동/버스트 전환")
        reference_notes=self.pro_profile.compare(movement_metrics)
        if not priorities and reference_notes: priorities.append(reference_notes[0])
        if not priorities: priorities.append("기본 무빙-사격 연결은 안정적입니다. 실제 적/크로스헤어 Vision 모델을 추가하면 더 정밀하게 평가할 수 있습니다.")
        skill_counts=Counter(e.label for e in skills)
        return {"started_at":self.session_started_at,"ended_at":ended_at,"fight_count":len(fights),"shots":total_shots,"shot_detection":"ammo_hud_confirmed" if self.config.get("shot_hud_detection",False) else "input_fallback","moving_shots":moving_shots,"walk_shots":walk_shots,"crouch_shots":crouch_shots,"moving_shot_ratio":ratio,"average_fight_score":average_score,"movement_metrics":movement_metrics,"skill_usage":{"detection":"hud_confirmed","total":len(skills),"by_label":dict(skill_counts)},"reference_profile":self.pro_profile.data,"reference_comparison":reference_notes,"priorities":priorities[:4],"fights":[asdict(f) for f in fights]}

    def save_session(self):
        summary=self.session_summary(); sessions_dir=self.root_dir/"data"/"sessions"; sessions_dir.mkdir(parents=True,exist_ok=True); stamp=time.strftime("%Y%m%d_%H%M%S"); path=sessions_dir/f"session_{stamp}.json"; path.write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding="utf-8"); return path
