from __future__ import annotations

import statistics
import time

from app_v6_11 import App as V611App
from main import save_config
from personal_learning import PersonalCoachProfile


V612_DEFAULTS = {
    "personal_learning_enabled": True,
    "gameplay_hud_confirm_seconds": 1.2,
    "game_mode_brawl_confirm_seconds": 12.0,
    "game_mode_normal_confirm_seconds": 1.5,
    "game_mode_min_samples": 24,
}


class App(V611App):
    """v6.12: self-only K/D, gameplay gate, personal learning and session scores."""

    def __init__(self):
        self._gameplay_active = False
        self._gameplay_visible_since = None
        self._personal_last_compare = None
        super().__init__()

        changed = False
        for key, value in V612_DEFAULTS.items():
            if key not in self.config_data:
                self.config_data[key] = value
                changed = True
        if float(self.config_data.get("game_mode_brawl_confirm_seconds", 0) or 0) < 12.0:
            self.config_data["game_mode_brawl_confirm_seconds"] = 12.0
            changed = True
        if int(self.config_data.get("game_mode_min_samples", 0) or 0) < 24:
            self.config_data["game_mode_min_samples"] = 24
            changed = True
        if changed:
            save_config(self.config_data)

        if hasattr(self, "mode_detector"):
            self.mode_detector.update_config(self.config_data)
        if hasattr(self, "combat_detector"):
            self.combat_detector.update_config(self.config_data)
        self.personal_profile = PersonalCoachProfile(self.engine.root_dir)

    def _current_skill_score(self):
        if getattr(self, "_brawl_mode", False):
            return None
        scores = [float(f.skill_timing_score) for f in self.engine.fights if getattr(f, "skill_timing_score", None) is not None]
        return round(statistics.fmean(scores), 1) if scores else None

    def _reset_visible_counters(self) -> None:
        self.shot_count = 0
        self.moving_shot_count = 0
        self.walk_shot_count = 0
        self.crouch_shot_count = 0
        self.skill_count = 0
        self.confirmed_bullets_fired = 0
        self.last_ammo_reading = None
        for var in (self.shots_var, self.move_var, self.walk_var, self.crouch_var):
            var.set("0")
        self.skill_var.set("—" if getattr(self, "_brawl_mode", False) else "0")

    def _activate_gameplay(self, timestamp: float) -> None:
        if self._gameplay_active:
            return
        self._gameplay_active = True
        self.engine.start_session()
        self._reset_visible_counters()
        if hasattr(self, "skill_detector"):
            self.skill_detector.clear()
            if not getattr(self, "_brawl_mode", False):
                self.skill_detector.update_config(self.config_data)
        if hasattr(self, "ammo_detector"):
            self.ammo_detector.clear()
        if hasattr(self, "shot_detector"):
            self.shot_detector.clear()
        if hasattr(self, "combat_detector"):
            self.combat_detector.clear()
        if hasattr(self, "vision"):
            self.vision.clear()
        self._combat_ui_seen = 0
        if hasattr(self, "_set_engine_skill_evaluation"):
            self._set_engine_skill_evaluation(not getattr(self, "_brawl_mode", False))

        lang = "en" if self.config_data.get("language") == "en" else "ko"
        self.status_var.set("Game detected · coaching active" if lang == "en" else "게임 감지 · 피드백 시작")
        self._append_feedback(
            "\n게임 HUD 확인 · 지금부터 플레이 데이터를 평가하고 학습합니다.\n" if lang == "ko" else "\nGameplay HUD confirmed · coaching and learning start now.\n",
            "muted",
        )

    def _thread_frame(self, sample) -> None:
        super()._thread_frame(sample)
        if not self.session_running or self._gameplay_active:
            return
        try:
            state = self.mode_detector.state if hasattr(self, "mode_detector") else None
            visible = bool(state and state.gameplay_hud_visible)
            if visible:
                if self._gameplay_visible_since is None:
                    self._gameplay_visible_since = sample.timestamp
                confirm = max(0.5, float(self.config_data.get("gameplay_hud_confirm_seconds", 1.2)))
                if sample.timestamp - self._gameplay_visible_since >= confirm:
                    self._activate_gameplay(sample.timestamp)
            else:
                self._gameplay_visible_since = None
        except Exception:
            pass

    def start_session(self) -> None:
        self._gameplay_active = False
        self._gameplay_visible_since = None
        self._personal_last_compare = None
        super().start_session()
        lang = "en" if self.config_data.get("language") == "en" else "ko"
        self.status_var.set("Waiting for gameplay HUD" if lang == "en" else "게임 HUD 대기 중")

    def _render_fight(self, fight) -> None:
        if not self._gameplay_active:
            return
        super()._render_fight(fight)

        if not bool(self.config_data.get("personal_learning_enabled", True)):
            return
        mode = "brawl" if getattr(self, "_brawl_mode", False) else "normal"
        aim = self.vision.aim_summary_between(fight.started_at - 0.15, fight.ended_at + 0.45)
        metrics = {
            "movement_score": fight.movement_score,
            "aim_score": aim.get("aim_score"),
            "skill_score": None if mode == "brawl" else fight.skill_timing_score,
            "moving_shot_ratio": fight.moving_shot_ratio,
            "stop_to_shot_ms": fight.median_stop_to_shot_ms,
            "avg_shots_per_fight": fight.shots,
        }
        learned = self.personal_profile.compare(metrics, mode)
        if learned.get("session_count", 0) >= 2 and learned.get("messages"):
            self._append_feedback("• 개인 학습 · " + " / ".join(learned["messages"][:2]) + "\n", "muted")

    def _poll_combat_ui(self) -> None:
        try:
            if not hasattr(self, "combat_detector"):
                return
            summary = self.combat_detector.summary()
            vision = self.vision.summary(45.0) if hasattr(self, "vision") else {}
            aim = vision.get("aim_score")
            aim_text = "--" if aim is None else f"{float(aim):.0f}"
            skill = self._current_skill_score()
            skill_text = "—" if getattr(self, "_brawl_mode", False) else ("--" if skill is None else f"{skill:.0f}")
            self.profile_var.set(f"내 K {summary['kills']} · 내 D {summary['deaths']} · AIM {aim_text} · SKILL {skill_text}")

            if not self._gameplay_active:
                return
            events = summary.get("events", [])
            while self._combat_ui_seen < len(events):
                event = events[self._combat_ui_seen]
                self._combat_ui_seen += 1
                if event.get("kind") == "kill":
                    self._append_feedback(f"내 킬 감지 · 신뢰도 {float(event.get('confidence', 0)) * 100:.0f}%\n", "good")
                else:
                    self._append_feedback(f"내 데스 감지 · 신뢰도 {float(event.get('confidence', 0)) * 100:.0f}%\n", "muted")
        except Exception:
            pass
        finally:
            self.after(350, self._poll_combat_ui)

    def _session_learning_metrics(self, summary: dict, vision: dict) -> dict:
        fights = summary.get("fights", []) or []
        avg_shots = statistics.fmean(float(f.get("shots", 0)) for f in fights) if fights else None
        movement = summary.get("average_fight_score")
        skill = summary.get("average_skill_score")
        if skill is None:
            skill = (summary.get("skill_usage") or {}).get("average_score")
        if skill is None and not getattr(self, "_brawl_mode", False):
            raw_skill = [float(f.get("skill_timing_score")) for f in fights if f.get("skill_timing_score") is not None]
            skill = round(statistics.fmean(raw_skill), 1) if raw_skill else None
        metrics = summary.get("movement_metrics") or {}
        return {
            "movement_score": movement,
            "aim_score": vision.get("aim_score"),
            "skill_score": None if getattr(self, "_brawl_mode", False) else skill,
            "moving_shot_ratio": metrics.get("moving_shot_ratio"),
            "stop_to_shot_ms": metrics.get("median_stop_to_shot_ms"),
            "avg_shots_per_fight": avg_shots,
            "fight_count": summary.get("fight_count", 0),
            "shots": summary.get("shots", 0),
        }

    def stop_session(self) -> None:
        was_running = bool(self.session_running)
        active = bool(self._gameplay_active)
        summary = self.engine.session_summary() if was_running else None
        vision = self.vision.summary(60 * 60 * 6) if was_running and hasattr(self, "vision") else {}
        mode = "brawl" if getattr(self, "_brawl_mode", False) else "normal"
        metrics = self._session_learning_metrics(summary or {}, vision)
        comparison = self.personal_profile.compare(metrics, mode) if active else None

        super().stop_session()
        if not was_running:
            return

        if not active:
            self._append_feedback("\n게임 플레이 HUD가 확인되지 않아 이번 세션은 점수/학습에서 제외했습니다.\n", "muted")
            return

        movement = metrics.get("movement_score")
        aim = metrics.get("aim_score")
        skill = metrics.get("skill_score")
        movement_text = "--" if movement is None else f"{float(movement):.1f}/100"
        aim_text = "--" if aim is None else f"{float(aim):.1f}/100"
        skill_text = "—" if mode == "brawl" else ("--" if skill is None else f"{float(skill):.1f}/100")
        combat = self.combat_detector.summary() if hasattr(self, "combat_detector") else {"kills": 0, "deaths": 0}

        self._append_feedback("\n세션 최종 점수\n", "heading")
        self._append_feedback(f"무빙 {movement_text} · 에임 {aim_text} · 스킬 {skill_text} · 내 킬 {combat['kills']} · 내 데스 {combat['deaths']}\n")

        if comparison and comparison.get("messages"):
            self._append_feedback("개인 학습 비교\n", "heading")
            for message in comparison["messages"][:5]:
                self._append_feedback(f"• {message}\n", "muted")

        valid_for_learning = (
            bool(self.config_data.get("personal_learning_enabled", True))
            and int(metrics.get("fight_count") or 0) >= 1
            and int(metrics.get("shots") or 0) >= 2
        )
        if valid_for_learning:
            baseline = self.personal_profile.learn(metrics, mode)
            self._append_feedback(f"개인 학습 완료 · {('난투' if mode == 'brawl' else '일반')} 유효 세션 {baseline['session_count']}개 누적\n", "good")
        else:
            self._append_feedback("개인 학습 제외 · 유효 교전/사격 데이터가 부족합니다.\n", "muted")

    def _chat_context(self) -> dict:
        context = super()._chat_context()
        mode = "brawl" if getattr(self, "_brawl_mode", False) else "normal"
        context["gameplay_active"] = bool(self._gameplay_active)
        context["personal_learning"] = {
            "enabled": bool(self.config_data.get("personal_learning_enabled", True)),
            "mode": mode,
            "baseline": self.personal_profile.baseline(mode),
        }
        context["combat_detection_note"] = "Kill/death values count only conservative local-player HUD signals: center self-kill confirmation and self-death combat-report transition."
        return context


if __name__ == "__main__":
    App().mainloop()
