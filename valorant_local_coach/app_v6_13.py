from __future__ import annotations

import json
import time
from pathlib import Path

from app_v6_12 import App as V612App
from main import WORK_DIR, save_config
from match_lifecycle_detector import MatchLifecycleDetector


V613_DEFAULTS = {
    "match_end_auto_detection": True,
    "match_end_detection_fps": 2,
    "match_end_confirm_seconds": 7.0,
    "match_end_result_threshold": 24.0,
    "match_end_max_result_motion": 7.5,
}


class App(V612App):
    """v6.13: non-gun self kills + automatic per-match final evaluation."""

    def __init__(self):
        self._match_finalizing = False
        self._auto_match_count = 0
        self._last_auto_match_path = None
        super().__init__()
        changed = False
        for key, value in V613_DEFAULTS.items():
            if key not in self.config_data:
                self.config_data[key] = value
                changed = True
        if changed:
            save_config(self.config_data)
        self.match_detector = MatchLifecycleDetector(self.config_data)

    def start_session(self) -> None:
        self._match_finalizing = False
        self._auto_match_count = 0
        self._last_auto_match_path = None
        if hasattr(self, "match_detector"):
            self.match_detector.update_config(self.config_data)
            self.match_detector.clear()
        super().start_session()

    def _thread_frame(self, sample) -> None:
        super()._thread_frame(sample)
        if not self.session_running or not hasattr(self, "match_detector"):
            return
        try:
            state = self.match_detector.process(sample.frame_bgr, sample.timestamp)
            if self._gameplay_active and state.phase == "ended" and not self._match_finalizing:
                self._match_finalizing = True
                self.after(0, lambda s=state: self._auto_finalize_match(s))
        except Exception as exc:
            print(f"[MatchEnd] detect failed: {type(exc).__name__}: {exc}")

    @staticmethod
    def _score_text(value) -> str:
        return "--" if value is None else f"{float(value):.1f}/100"

    def _auto_finalize_match(self, lifecycle_state) -> None:
        """Evaluate one match without stopping screen/input capture."""
        try:
            if not self.session_running or not self._gameplay_active:
                return

            mode = "brawl" if getattr(self, "_brawl_mode", False) else "normal"
            summary = self.engine.session_summary()
            vision = self.vision.summary(60 * 60 * 6) if hasattr(self, "vision") else {}
            combat = self.combat_detector.summary() if hasattr(self, "combat_detector") else {"kills": 0, "deaths": 0, "kd_ratio": 0}
            metrics = self._session_learning_metrics(summary, vision)
            comparison = self.personal_profile.compare(metrics, mode)

            session_json = self.engine.save_session()
            movement = metrics.get("movement_score")
            aim = metrics.get("aim_score")
            skill = metrics.get("skill_score")
            skill_text = "—" if mode == "brawl" else self._score_text(skill)

            self._append_feedback("\n경기 종료 자동 감지 · 한 판 평가\n", "heading")
            self._append_feedback(
                f"무빙 {self._score_text(movement)} · 에임 {self._score_text(aim)} · 스킬 {skill_text} · "
                f"내 킬 {combat.get('kills', 0)} · 내 데스 {combat.get('deaths', 0)} · K/D {combat.get('kd_ratio', 0)}\n"
            )

            priorities = summary.get("priorities") or []
            if priorities:
                self._append_feedback("이번 판 우선 피드백\n", "heading")
                for item in priorities[:4]:
                    self._append_feedback(f"• {item}\n")

            if comparison and comparison.get("messages"):
                self._append_feedback("개인 학습 비교\n", "heading")
                for message in comparison["messages"][:5]:
                    self._append_feedback(f"• {message}\n", "muted")

            valid_for_learning = (
                bool(self.config_data.get("personal_learning_enabled", True))
                and int(metrics.get("fight_count") or 0) >= 1
                and int(metrics.get("shots") or 0) >= 2
            )
            learned_baseline = None
            if valid_for_learning:
                learned_baseline = self.personal_profile.learn(metrics, mode)
                self._append_feedback(
                    f"개인 학습 완료 · {('난투' if mode == 'brawl' else '일반')} 유효 경기 "
                    f"{learned_baseline['session_count']}개 누적\n",
                    "good",
                )
            else:
                self._append_feedback("개인 학습 제외 · 유효 교전/사격 데이터가 부족합니다.\n", "muted")

            report = {
                "saved_at": time.time(),
                "detected_automatically": True,
                "match_end_detection": {
                    "confidence": float(getattr(lifecycle_state, "confidence", 0.0)),
                    "reason": getattr(lifecycle_state, "reason", "unknown"),
                    "end_screen_score": float(getattr(lifecycle_state, "end_screen_score", 0.0)),
                },
                "mode": mode,
                "scores": {"movement": movement, "aim": aim, "skill": skill},
                "combat": combat,
                "learning_comparison": comparison,
                "learned_baseline": learned_baseline,
                "session_json": str(session_json),
            }
            out_dir = Path(WORK_DIR) / "data" / "match_reports"
            out_dir.mkdir(parents=True, exist_ok=True)
            report_path = out_dir / f"match_{time.strftime('%Y%m%d_%H%M%S')}.json"
            report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
            self._last_auto_match_path = str(report_path)
            self._auto_match_count += 1
            self._append_feedback(f"판 평가 JSON · {report_path}\n", "muted")

            self.engine.start_session()
            self._reset_visible_counters()
            if hasattr(self, "skill_detector"):
                self.skill_detector.clear()
                self.skill_detector.update_config(self.config_data)
            if hasattr(self, "ammo_detector"):
                self.ammo_detector.clear()
            if hasattr(self, "shot_detector"):
                self.shot_detector.clear()
            if hasattr(self, "combat_detector"):
                self.combat_detector.clear()
            if hasattr(self, "vision"):
                self.vision.clear()
            if hasattr(self, "mode_detector"):
                self.mode_detector.clear()
            self._brawl_mode = False
            self._mode_notice = "unknown"
            self._last_mode_notice_rendered = None
            self._set_engine_skill_evaluation(True)
            self._combat_ui_seen = 0
            self._gameplay_active = False
            self._gameplay_visible_since = None
            self.match_detector.clear()

            lang = "en" if self.config_data.get("language") == "en" else "ko"
            self.status_var.set(
                "Match reviewed · waiting for next match HUD"
                if lang == "en"
                else "한 판 평가 완료 · 다음 게임 HUD 대기"
            )
        except Exception as exc:
            self._append_feedback(f"자동 판 평가 실패 · {type(exc).__name__}: {exc}\n", "muted")
        finally:
            self._match_finalizing = False

    def _chat_context(self) -> dict:
        context = super()._chat_context()
        context["automatic_match_review"] = {
            "enabled": bool(self.config_data.get("match_end_auto_detection", True)),
            "completed_matches": int(self._auto_match_count),
            "last_report": self._last_auto_match_path,
        }
        context["combat_detection_note"] = (
            "Self kills are inferred from the local center kill-confirm marker. Recent gunfire only boosts confidence, "
            "so credited ability/environmental kills can count too. Uncredited environmental deaths cannot be claimed as kills from screen-only evidence."
        )
        return context


if __name__ == "__main__":
    App().mainloop()
