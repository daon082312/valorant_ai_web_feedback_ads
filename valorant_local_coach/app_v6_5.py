from __future__ import annotations

import threading

from app_v6_4 import App as V64App
from local_chat import ChatReply, LocalCoachChat


LOSS_WORDS = ("loss", "lose", "lost", "패배", "졌", "원인")


class NoLossCoachChat(LocalCoachChat):
    """Chat client with match-loss analysis intentionally removed."""

    @staticmethod
    def _system_prompt(context_text: str, language: str = "ko") -> str:
        prompt = LocalCoachChat._system_prompt(context_text, language)
        prompt = prompt.replace(
            "- 패배 원인 질문에는 측정된 요인을 가능성 순서로 설명하고 인과를 확정하지 않는다.\n",
            "",
        )
        return prompt

    @staticmethod
    def _fallback_reply(user_text: str, context: dict) -> str:
        q = str(user_text or "").casefold()
        english = str(context.get("language") or "ko") == "en"
        if any(word in q for word in LOSS_WORDS):
            return (
                "This coach focuses on movement, utility timing, head-line placement, minimap signals and uploaded-video measurements."
                if english
                else "이 코치는 무빙, 스킬 사용 타이밍, 헤드라인, 미니맵 신호와 영상 측정값 중심으로 피드백합니다."
            )
        text = LocalCoachChat._fallback_reply(user_text, context)
        return (
            text.replace("패배 요인과 ", "")
            .replace("패배 요인", "개선 항목")
            .replace("measured loss factors", "improvement priorities")
            .replace("loss factors", "improvement priorities")
        )


class App(V64App):
    """v6.5: stable chat input, no auto video recording, no loss analysis."""

    def __init__(self):
        super().__init__()
        self._last_match_result = None
        try:
            if hasattr(self, "round_loss_btn"):
                self.round_loss_btn.pack_forget()
        except Exception:
            pass
        self.chat_client = NoLossCoachChat(self.config_data)

    def _chat_context(self) -> dict:
        context = super()._chat_context()
        context.pop("match_result", None)
        context.pop("loss_analysis", None)
        return context

    def open_loss_analysis(self) -> None:
        return

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
                self._v6_chat_status.set(
                    "이전 답변 생성 중…"
                    if self.config_data.get("language") != "en"
                    else "Previous answer is still generating…"
                )
            return

        entry.delete(0, "end")
        self._v6_append_chat(
            ("나 · " if self.config_data.get("language") != "en" else "Me · ") + question + "\n",
            "user",
        )
        history_before = list(self.chat_history)
        self.chat_history.append({"role": "user", "content": question})
        context = self._chat_context()
        self.chat_busy = True
        entry.configure(state="normal")
        if self._v6_chat_send_btn is not None:
            self._v6_chat_send_btn.configure(state="disabled")
        if self._v6_chat_status is not None:
            self._v6_chat_status.set(
                "AI 연결 확인 중…"
                if self.config_data.get("language") != "en"
                else "Checking local AI…"
            )

        def worker() -> None:
            try:
                status = self.chat_client.status()
                if not status.get("online") or not status.get("model_installed"):
                    fallback = self.chat_client._fallback_reply(question, context)
                    note = (
                        "Ollama/모델을 사용할 수 없어 내장 코치로 답변했습니다."
                        if self.config_data.get("language") != "en"
                        else "Ollama/model unavailable; answered with the built-in coach."
                    )
                    reply = ChatReply(f"{fallback}\n\n({note})", "builtin", self.chat_client.model, False)
                else:
                    reply = self.chat_client.ask(question, context, history_before)
            except Exception as exc:
                fallback = self.chat_client._fallback_reply(question, context)
                reply = ChatReply(
                    f"{fallback}\n\n(내장 코치로 전환됨: {type(exc).__name__})",
                    "builtin",
                    self.chat_client.model,
                    False,
                )
            self.after(0, lambda r=reply: self._v6_deliver_chat(r))

        threading.Thread(target=worker, daemon=True, name="LocalCoachChatV65").start()


if __name__ == "__main__":
    App().mainloop()
