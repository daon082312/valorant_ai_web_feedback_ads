from __future__ import annotations

import json
import time
from pathlib import Path

from app_v6_7 import App as V67App
from combat_event_detector import CombatEventDetector
from main import WORK_DIR, save_config


PERF_DEFAULTS = {
    "preview_fps": 3,
    "vision_analysis_fps": 2,
    "show_vision_boxes_in_preview": False,
    "store_minimap_history": False,
    "combat_event_detection": True,
    "combat_event_fps": 4,
    "kill_hud_change_threshold": 10.0,
    "death_hud_change_threshold": 13.0,
    "kill_event_cooldown_seconds": 1.6,
    "death_event_cooldown_seconds": 4.0,
}


class App(V67App):
    """v6.8: aim evaluation, conservative kill/death HUD detection, lower CPU load."""

    def __init__(self):
        super().__init__()
        changed = False
        for key, value in PERF_DEFAULTS.items():
            if key in {"preview_fps", "vision_analysis_fps", "show_vision_boxes_in_preview", "store_minimap_history"}:
                if self.config_data.get(key) != value:
                    self.config_data[key] = value
                    changed = True
            elif key not in self.config_data:
                self.config_data[key] = value
                changed = True
        if changed:
            save_config(self.config_data)

        self.vision.update_config(self.config_data)
        self.combat_detector = CombatEventDetector(self.config_data)
        self._combat_ui_seen = 0
        self._last_combat_summary_path = None
        self.after(350, self._poll_combat_ui)

    def _thread_frame(self, sample) -> None:
        super()._thread_frame(sample)
        try:
            recent_fire = self.input_tracker.fire_hint_active(sample.timestamp, 0.70)
            self.combat_detector.process(sample.frame_bgr, sample.timestamp, recent_fire=recent_fire)
        except Exception as exc:
            print(f"[CombatHUD] detect failed: {type(exc).__name__}: {exc}")

    def _poll_combat_ui(self) -> None:
        try:
            summary = self.combat_detector.summary() if hasattr(self, "combat_detector") else {"kills": 0, "deaths": 0, "events": []}
            vision = self.vision.summary(45.0) if hasattr(self, "vision") else {}
            aim = vision.get("aim_score")
            aim_text = "--" if aim is None else f"{float(aim):.0f}"
            self.profile_var.set(f"K {summary['kills']} · D {summary['deaths']} · AIM {aim_text}")

            events = summary.get("events", [])
            while self._combat_ui_seen < len(events):
                event = events[self._combat_ui_seen]
                self._combat_ui_seen += 1
                if event.get("kind") == "kill":
                    self._append_feedback(f"킬 감지 · 신뢰도 {float(event.get('confidence', 0)) * 100:.0f}%\n", "good")
                else:
                    self._append_feedback(f"데스 감지 · 신뢰도 {float(event.get('confidence', 0)) * 100:.0f}%\n", "muted")
        except Exception:
            pass
        finally:
            self.after(350, self._poll_combat_ui)

    def _render_fight(self, fight) -> None:
        super()._render_fight(fight)
        aim = self.vision.aim_summary_between(fight.started_at - 0.15, fight.ended_at + 0.45)
        events = self.combat_detector.events_between(fight.started_at - 0.4, fight.ended_at + 1.5)
        kills = sum(1 for e in events if e.kind == "kill")
        deaths = sum(1 for e in events if e.kind == "death")
        score = aim.get("aim_score")
        error = aim.get("aim_error_px")

        if score is None:
            self._append_feedback("• 에임 · 적 후보가 충분히 잡히지 않아 이번 교전 에임 점수는 계산하지 않았습니다.\n", "muted")
        else:
            if score >= 85:
                note = "적 머리 추정점 근처에 크로스헤어를 안정적으로 유지했습니다."
            elif score >= 65:
                note = "크로스헤어가 적 머리 높이에 근접했지만 좌우/상하 보정 거리를 더 줄일 수 있습니다."
            else:
                note = "적이 보일 때 크로스헤어와 머리 추정점 사이 거리가 큰 편입니다. 피킹 전 프리에임 위치를 우선 교정하세요."
            self._append_feedback(f"• 에임 · {score:.0f}/100 · 평균 머리 오차 {error:.0f}px · {note}\n")

        if kills or deaths:
            self._append_feedback(f"• 결과 · 이 교전 구간 HUD 감지: 킬 {kills} · 데스 {deaths}\n", "muted")
            if deaths and score is not None and score < 65:
                self._append_feedback("• 데스 피드백 · 적 노출 순간 크로스헤어 보정 거리가 컸습니다. 무빙보다 프리에임/첫 조준 보정을 먼저 줄여보세요.\n")
            elif deaths and fight.moving_shot_ratio >= 0.25:
                self._append_feedback("• 데스 피드백 · 이동 입력 중 사격 비율이 높았습니다. 첫 탄 전에 정지 구간을 더 확실히 만드세요.\n")
            elif deaths and fight.shots >= int(self.config_data.get("long_burst_shot_count", 6)):
                self._append_feedback("• 데스 피드백 · 긴 스프레이가 이어졌습니다. 짧은 버스트 후 재이동을 섞는 편이 좋습니다.\n")
            elif kills and score is not None and score >= 75:
                self._append_feedback("• 킬 피드백 · 에임 정렬 상태가 비교적 좋았던 교전입니다. 같은 프리에임 높이를 유지하세요.\n", "good")

    def start_session(self) -> None:
        if hasattr(self, "combat_detector"):
            self.combat_detector.clear()
        self._combat_ui_seen = 0
        super().start_session()

    def stop_session(self) -> None:
        was_running = bool(self.session_running)
        super().stop_session()
        if not was_running:
            return
        combat = self.combat_detector.summary()
        vision = self.vision.summary(60 * 60 * 6)
        aim_score = vision.get("aim_score")
        aim_error = vision.get("aim_error_px")
        aim_text = "--" if aim_score is None else f"{float(aim_score):.1f}/100"
        error_text = "--" if aim_error is None else f"{float(aim_error):.0f}px"
        self._append_feedback("\n에임/전투 요약\n", "heading")
        self._append_feedback(f"에임 {aim_text} · 평균 머리 오차 {error_text} · 킬 {combat['kills']} · 데스 {combat['deaths']} · K/D {combat['kd_ratio']}\n")

        payload = {
            "saved_at": time.time(),
            "aim": {
                "score": aim_score,
                "error_px": aim_error,
                "headline_score": vision.get("headline_score"),
                "target_detection_frames": vision.get("enemy_detection_frames", 0),
            },
            "combat": combat,
            "note": "Kill/death are conservative local HUD-change estimates; uncertain events are intentionally ignored.",
        }
        out_dir = Path(WORK_DIR) / "data" / "sessions"
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / f"combat_{time.strftime('%Y%m%d_%H%M%S')}.json"
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        self._last_combat_summary_path = str(path)
        self._append_feedback(f"에임/킬·데스 JSON · {path}\n", "muted")

    def _chat_context(self) -> dict:
        context = super()._chat_context()
        context["aim"] = self.vision.summary(120.0)
        context["combat_events"] = self.combat_detector.summary()
        context["combat_detection_note"] = "Kill/death values are conservative local HUD visual estimates, not game-memory data."
        return context


if __name__ == "__main__":
    App().mainloop()
