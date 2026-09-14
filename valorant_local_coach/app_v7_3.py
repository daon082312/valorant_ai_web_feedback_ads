from __future__ import annotations

import threading
import tkinter as tk

import app_v7 as rootbase
import app_v7_2 as v72
from video_review_v73 import analyze_video as analyze_video_v73

# app_v7.App._analysis_worker resolves this module global.
rootbase.analyze_video = analyze_video_v73
v72.base.analyze_video = analyze_video_v73

TEXT = v72.TEXT
MUTED = v72.MUTED
PANEL2 = v72.PANEL2
FONT = v72.FONT


class App(v72.App):
    """v7.3: reliable chat input + recalibrated shot-time aim scoring."""

    def __init__(self):
        super().__init__()
        self.title("VALORANT Video Coach v7.3")
        self._repair_chat_entry()
        self.after(120, self._focus_chat_if_clicked)

    def _repair_chat_entry(self) -> None:
        """Use native Tk Entry to avoid CTkEntry focus/IME issues on Windows/Python 3.13."""
        old = getattr(self, "chat_entry", None)
        if old is None:
            return
        parent = old.master
        try:
            old.destroy()
        except Exception:
            pass
        self.chat_entry = tk.Entry(
            parent,
            bg="#171d27",
            fg=TEXT,
            insertbackground=TEXT,
            selectbackground="#3a4555",
            selectforeground=TEXT,
            relief="flat",
            bd=0,
            highlightthickness=1,
            highlightbackground="#2a3340",
            highlightcolor="#ff4655",
            font=(FONT, 11),
            takefocus=True,
        )
        self.chat_entry.pack(side="left", fill="x", expand=True, ipady=10, before=self.chat_send)
        self.chat_entry.bind("<Return>", self._chat_enter)
        self.chat_entry.bind("<Button-1>", lambda _e: self.after_idle(self.chat_entry.focus_force))
        self.chat_entry.bind("<FocusIn>", lambda _e: self.chat_entry.configure(highlightbackground="#ff4655"))
        self.chat_entry.bind("<FocusOut>", lambda _e: self.chat_entry.configure(highlightbackground="#2a3340"))
        self._append_chat("v7.3 · 채팅 입력창을 Windows 표준 입력 위젯으로 변경했습니다. Ollama가 없어도 내장 코치가 답합니다.\n\n")

    def _chat_enter(self, _event=None):
        self.send_chat()
        return "break"

    def _focus_chat_if_clicked(self) -> None:
        try:
            if self.chat_entry.winfo_exists():
                self.chat_entry.configure(state="normal")
        except Exception:
            pass

    def send_chat(self) -> None:
        try:
            self.chat_entry.configure(state="normal")
            q = self.chat_entry.get().strip()
        except Exception:
            return
        if not q:
            self.chat_entry.focus_force()
            return
        if self.chat_busy:
            self._append_chat("AI · 이전 답변을 생성 중입니다. 잠시 후 전송 버튼을 다시 눌러 주세요.\n\n")
            self.chat_entry.focus_force()
            return

        self.chat_entry.delete(0, "end")
        self.chat_entry.focus_force()
        self._append_chat(f"나 · {q}\n")
        previous = list(self.chat_history)
        self.chat_history.append({"role": "user", "content": q})
        self.chat_busy = True
        try:
            self.chat_send.configure(state="disabled", text="…")
        except Exception:
            pass

        def worker():
            source = "builtin"
            try:
                reply = self.chat_client.ask(q, self.last_report or {}, previous)
                text = reply.text
                source = getattr(reply, "source", "builtin")
            except Exception as exc:
                text = f"AI 응답 오류: {type(exc).__name__}: {exc}"
            try:
                self.after(0, lambda: self._deliver_chat_v73(text, source))
            except Exception:
                pass

        threading.Thread(target=worker, daemon=True).start()

    def _deliver_chat_v73(self, text: str, source: str) -> None:
        self.chat_history.append({"role": "assistant", "content": text})
        source_label = "Ollama" if source == "ollama" else "내장 코치"
        self._append_chat(f"AI · {text}\n   [{source_label}]\n\n")
        self.chat_busy = False
        try:
            self.chat_send.configure(state="normal", text="전송")
            self.chat_entry.configure(state="normal")
            self.chat_entry.focus_force()
        except Exception:
            pass

    def _deliver_report(self, report):
        super()._deliver_report(report)
        score = report.get("aim_score")
        conf = float(report.get("aim_confidence") or 0.0)
        if score is not None:
            self.metric_vars["aim"].set(f"{float(score):.0f}/100 · {conf*100:.0f}%")
        self._append_chat(
            f"AI · 분석 완료. 에임 판정 방식: {report.get('aim_method')} / 신뢰도 {conf*100:.0f}%. 결과에 대해 바로 질문할 수 있습니다.\n\n"
        )


if __name__ == "__main__":
    App().mainloop()
