from __future__ import annotations

import copy
import threading
import time

from app_v6_5 import App as V65App, NoLossCoachChat
from local_chat import ChatReply
from main import ACCENT, save_config
from mode_detector import BrawlModeDetector


MODE_DEFAULTS = {
    "game_mode_auto_detection": True,
    "game_mode_override": "auto",
    "game_mode_detection_fps": 3,
    "game_mode_brawl_confirm_seconds": 6.0,
    "game_mode_normal_confirm_seconds": 3.0,
    "game_mode_min_samples": 12,
}


class BrawlAwareCoachChat(NoLossCoachChat):
    @staticmethod
    def _system_prompt(context_text: str, language: str = "ko") -> str:
        prompt = NoLossCoachChat._system_prompt(context_text, language)
        if language == "en":
            return prompt + "\nIf the context says game_mode=brawl, do not evaluate utility/ability use or timing."
        return prompt + "\n컨텍스트의 game_mode가 brawl이면 스킬 사용/타이밍을 평가하거나 개선점으로 제시하지 않는다."

    @staticmethod
    def _fallback_reply(user_text: str, context: dict) -> str:
        if context.get("game_mode") == "brawl":
            q = str(user_text or "").casefold()
            if any(word in q for word in ("스킬", "skill", "ability", "utility", "궁극")):
                return (
                    "Brawl mode is active, so utility evaluation is intentionally excluded. I can still review movement, shooting, head-line placement, and minimap measurements."
                    if context.get("language") == "en"
                    else "난투 모드로 인식되어 스킬 평가는 제외됩니다. 무빙, 사격, 헤드라인, 미니맵 측정값은 계속 피드백할 수 있습니다."
                )
        return NoLossCoachChat._fallback_reply(user_text, context)


class App(V65App):
    """v6.6: automatic brawl-mode detection and utility-evaluation suppression."""

    def __init__(self):
        super().__init__()
        changed = False
        for key, value in MODE_DEFAULTS.items():
            if key not in self.config_data:
                self.config_data[key] = value
                changed = True
        if changed:
            save_config(self.config_data)

        self.mode_detector = BrawlModeDetector(self.config_data)
        self._brawl_mode = False
        self._mode_notice = "unknown"
        self._last_mode_notice_rendered = None
        self.chat_client = BrawlAwareCoachChat(self.config_data)
        self.after(250, self._poll_mode_notice)

    @property
    def brawl_mode(self) -> bool:
        return bool(self._brawl_mode)

    def _set_engine_skill_evaluation(self, enabled: bool) -> None:
        if hasattr(self.engine, "set_skill_evaluation_enabled"):
            self.engine.set_skill_evaluation_enabled(enabled)
            return
        lock = getattr(self.engine, "_lock", None)
        if lock is None:
            return
        with lock:
            if not enabled:
                events = getattr(self.engine, "_skill_events", None)
                if events is not None:
                    events.clear()
                for fight in getattr(self.engine, "_fights", []):
                    fight.skill_timing_score = None
                    fight.skill_messages.clear()
                    fight.skill_uses.clear()

    def _set_brawl_mode_from_capture(self, enabled: bool) -> None:
        enabled = bool(enabled)
        if enabled == self._brawl_mode:
            return
        self._brawl_mode = enabled
        self._set_engine_skill_evaluation(not enabled)
        if hasattr(self, "skill_detector"):
            self.skill_detector.clear()
            if enabled:
                self.skill_detector.enabled = False
            else:
                self.skill_detector.update_config(self.config_data)
        self._mode_notice = "brawl" if enabled else "normal"

    def _thread_skill(self, event) -> None:
        if self._brawl_mode:
            return
        super()._thread_skill(event)

    def _thread_frame(self, sample) -> None:
        if str(self.config_data.get("game_mode_override", "auto")) != self.mode_detector.override:
            self.mode_detector.update_config(self.config_data)
        state = self.mode_detector.process(sample.frame_bgr, sample.timestamp)
        if state.mode == "brawl":
            self._set_brawl_mode_from_capture(True)
        elif state.mode == "normal":
            self._set_brawl_mode_from_capture(False)
        super()._thread_frame(sample)

    def _poll_mode_notice(self) -> None:
        try:
            notice = self._mode_notice
            if notice != self._last_mode_notice_rendered:
                self._last_mode_notice_rendered = notice
                lang = "en" if self.config_data.get("language") == "en" else "ko"
                if notice == "brawl":
                    self.skill_var.set("—")
                    self.status_var.set("Brawl detected · utility review off" if lang == "en" else "난투 감지 · 스킬 평가 제외")
                    self._append_feedback(
                        "\n난투 모드 감지 · 이 세션에서는 스킬 평가는 제외합니다.\n" if lang == "ko" else "\nBrawl mode detected · utility evaluation is disabled for this session.\n",
                        "muted",
                    )
                elif notice == "normal":
                    self.skill_var.set(str(self.skill_count))
                    if self.session_running:
                        self.status_var.set("Analyzing · optimized local mode" if lang == "en" else "분석 중 · 최적화 로컬 모드")
        finally:
            self.after(250, self._poll_mode_notice)

    def _render_fight(self, fight) -> None:
        if not self._brawl_mode:
            return super()._render_fight(fight)
        duration = max(0.0, fight.ended_at - fight.started_at)
        stop_text = "--" if fight.median_stop_to_shot_ms is None else f"{fight.median_stop_to_shot_ms:.0f}ms"
        self._append_feedback(f"\n{time.strftime('%H:%M:%S')} · {fight.title}\n", "heading")
        self._append_feedback(
            f"무빙 {fight.movement_score}/100   ·   사격 입력 {fight.shots}회   ·   교전 {duration:.1f}s\n"
            f"WASD중 {fight.moving_shot_ratio * 100:.0f}%   ·   Shift {fight.walk_shot_ratio * 100:.0f}%   ·   Ctrl {fight.crouch_shot_ratio * 100:.0f}%   ·   정지→첫 탄 {stop_text}\n"
        )
        for message in fight.movement_messages:
            self._append_feedback(f"• 무빙 · {message}\n")
        self._append_feedback("난투 모드 · 스킬 평가는 제외됨\n", "muted")

    def start_session(self) -> None:
        self.mode_detector.update_config(self.config_data)
        self.mode_detector.clear()
        self._brawl_mode = False
        self._mode_notice = "unknown"
        self._last_mode_notice_rendered = None
        self._set_engine_skill_evaluation(True)
        if hasattr(self, "skill_detector"):
            self.skill_detector.update_config(self.config_data)
        super().start_session()

    def stop_session(self) -> None:
        if not self._brawl_mode:
            return super().stop_session()
        if not self.session_running:
            return

        self.session_running = False
        self.input_tracker.stop()
        self.capture.stop()
        path = self.engine.save_session()
        summary = self.engine.session_summary()

        try:
            self.start_btn.configure(state="normal", bg=ACCENT)
            self.stop_btn.configure(state="disabled")
        except Exception:
            pass
        self.round_start_btn.configure(state="normal", fg_color=ACCENT)
        self.round_stop_btn.configure(state="disabled")
        self.keys_var.set("INPUT  -")
        self.status_var.set("세션 종료 · 난투 · 저장 완료" if self.config_data.get("language") != "en" else "Session saved · brawl")

        m = summary["movement_metrics"]
        score = summary["average_fight_score"]
        score_text = "--" if score is None else f"{score:.1f}"
        stop_text = "--" if m["median_stop_to_shot_ms"] is None else f"{m['median_stop_to_shot_ms']:.0f}ms"
        priorities = "\n".join(f"• {item}" for item in summary["priorities"])
        bullets = int(getattr(self, "confirmed_bullets_fired", 0) or 0)
        shot_text = f"HUD 확인 총 사격 {bullets}발" if bullets else f"사격 입력 {summary['shots']}회"
        self._append_feedback("\n세션 요약 · 난투 모드\n", "heading")
        self._append_feedback(
            f"교전 {summary['fight_count']}회 · {shot_text}\n"
            f"WASD중 사격 {m['moving_shot_ratio'] * 100:.0f}% · Shift {m['walk_shot_ratio'] * 100:.0f}% · Ctrl {m['crouch_shot_ratio'] * 100:.0f}%\n"
            f"정지→첫 탄 {stop_text} · 반대방향 탭 {m['opposite_tap_ratio'] * 100:.0f}% · 평균 무빙 점수 {score_text}\n"
            f"우선 개선:\n{priorities}\n"
            f"난투 모드에서는 스킬 평가를 제외했습니다.\n"
            f"세션 JSON · {path}\n"
        )

    def _chat_context(self) -> dict:
        context = super()._chat_context()
        context["game_mode"] = "brawl" if self._brawl_mode else "normal_or_unknown"
        context["skill_evaluation_enabled"] = not self._brawl_mode
        if self._brawl_mode:
            summary = context.get("session_summary")
            if isinstance(summary, dict):
                summary = copy.deepcopy(summary)
                summary.pop("skill_usage", None)
                summary["skill_evaluation_enabled"] = False
                for fight in summary.get("fights", []) or []:
                    if isinstance(fight, dict):
                        fight.pop("skill_timing_score", None)
                        fight.pop("skill_messages", None)
                        fight.pop("skill_uses", None)
                context["session_summary"] = summary
            latest = context.get("latest_fight")
            if isinstance(latest, dict):
                latest = copy.deepcopy(latest)
                latest.pop("skill_timing_score", None)
                latest.pop("skill_messages", None)
                latest.pop("skill_uses", None)
                context["latest_fight"] = latest
        return context

    def _v6_send_chat(self) -> None:
        entry = self._v6_chat_entry
        if entry is None or not entry.winfo_exists():
            return
        question = entry.get().strip()
        if not question:
            self._focus_chat_input()
            return
        if self.chat_busy:
            if self._v6_chat_status is not None:
                self._v6_chat_status.set("이전 답변 생성 중…" if self.config_data.get("language") != "en" else "Previous answer is still generating…")
            return

        entry.delete(0, "end")
        self._v6_append_chat(("나 · " if self.config_data.get("language") != "en" else "Me · ") + question + "\n", "user")
        history_before = list(self.chat_history)
        self.chat_history.append({"role": "user", "content": question})
        context = self._chat_context()
        self.chat_busy = True
        entry.configure(state="normal")
        if self._v6_chat_send_btn is not None:
            self._v6_chat_send_btn.configure(state="disabled")

        def worker() -> None:
            try:
                status = self.chat_client.status()
                if not status.get("online") or not status.get("model_installed"):
                    fallback = self.chat_client._fallback_reply(question, context)
                    reply = ChatReply(fallback, "builtin", self.chat_client.model, False)
                else:
                    reply = self.chat_client.ask(question, context, history_before)
            except Exception:
                reply = ChatReply(self.chat_client._fallback_reply(question, context), "builtin", self.chat_client.model, False)
            self.after(0, lambda r=reply: self._v6_deliver_chat(r))

        threading.Thread(target=worker, daemon=True, name="LocalCoachChatV66").start()


if __name__ == "__main__":
    App().mainloop()
