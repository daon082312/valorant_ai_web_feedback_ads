from __future__ import annotations

import queue
import threading
import tkinter as tk

import app_v7 as rootbase
import app_v7_2 as v72
import app_v7_16 as v716
from video_review_v717 import analyze_video as analyze_video_v717

rootbase.analyze_video = analyze_video_v717
v72.base.analyze_video = analyze_video_v717

TEXT = rootbase.TEXT
BORDER = rootbase.BORDER
ACCENT = rootbase.ACCENT
ACCENT_HOVER = rootbase.ACCENT_HOVER
FONT = rootbase.FONT


class App(v716.App):
    """v7.17: guaranteed immediate coach reply + balanced killfeed v3."""

    def __init__(self):
        self._ollama_results_v717: queue.Queue[tuple[str, str]] = queue.Queue()
        super().__init__()
        self.title("VALORANT Video Coach v7.17")
        self.after_idle(self._rebuild_chat_input_v717)
        self.after(120, self._poll_ollama_v717)
        self._append_chat(
            "AI · v7.17은 질문을 보내면 내장 코치가 즉시 답하고, Ollama가 연결되어 있으면 추가 AI 답변을 비동기로 붙입니다.\n\n"
        )

    def _rebuild_chat_input_v717(self) -> None:
        old = getattr(self, "chat_entry", None)
        if old is None:
            return
        parent = old.master
        for child in list(parent.winfo_children()):
            try:
                child.destroy()
            except Exception:
                pass

        self.chat_busy = False
        self.chat_var_v717 = tk.StringVar(value="")
        self.chat_entry = tk.Entry(
            parent,
            textvariable=self.chat_var_v717,
            bg="#171d27",
            fg=TEXT,
            insertbackground=TEXT,
            selectbackground="#3a4555",
            selectforeground=TEXT,
            relief="flat",
            bd=0,
            highlightthickness=1,
            highlightbackground=BORDER,
            highlightcolor=ACCENT,
            font=(FONT, 11),
            takefocus=True,
        )
        self.chat_entry.pack(side="left", fill="x", expand=True, ipady=10)
        self.chat_entry.bind("<Return>", self._chat_enter_v717)
        self.chat_entry.bind("<KP_Enter>", self._chat_enter_v717)

        self.chat_send = tk.Button(
            parent,
            text="전송",
            command=self.send_chat,
            width=7,
            bg=ACCENT,
            activebackground=ACCENT_HOVER,
            fg="white",
            activeforeground="white",
            relief="flat",
            bd=0,
            font=(FONT, 10, "bold"),
            cursor="hand2",
            padx=8,
            pady=10,
            takefocus=True,
        )
        self.chat_send.pack(side="left", padx=(8, 0))
        try:
            self.chat_entry.focus_set()
        except Exception:
            pass

    def _chat_enter_v717(self, _event=None):
        self.send_chat()
        return "break"

    def send_chat(self) -> None:
        try:
            question = self.chat_var_v717.get().strip()
        except Exception:
            try:
                question = self.chat_entry.get().strip()
            except Exception:
                question = ""
        if not question:
            try:
                self.chat_entry.focus_set()
            except Exception:
                pass
            return

        try:
            self.chat_var_v717.set("")
        except Exception:
            try:
                self.chat_entry.delete(0, "end")
            except Exception:
                pass

        previous = list(self.chat_history)
        self.chat_history.append({"role": "user", "content": question})
        self._append_chat(f"나 · {question}\n")

        report = self.last_report or {}
        try:
            english = str(self.config_data.get("language", "ko")) == "en"
            instant = str(self.chat_client.fallback(question, report, english)).strip()
        except Exception as exc:
            instant = f"내장 코치 응답 오류: {type(exc).__name__}: {exc}"
        if not instant:
            instant = "분석 결과에서 답변할 수 있는 근거가 부족합니다."
        self.chat_history.append({"role": "assistant", "content": instant})
        self._append_chat(f"AI · {instant}\n   [내장 코치 · 즉시]\n\n")
        try:
            self.chat_entry.focus_set()
        except Exception:
            pass

        if not bool(self.config_data.get("local_ai_enabled", True)):
            return

        report_snapshot = dict(report)

        def worker() -> None:
            try:
                reply = self.chat_client.ask(question, report_snapshot, previous)
                source = str(getattr(reply, "source", "builtin") or "builtin")
                text = str(getattr(reply, "text", "") or "").strip()
                if source == "ollama" and text and text != instant:
                    self._ollama_results_v717.put((text, source))
            except Exception:
                return

        threading.Thread(target=worker, daemon=True, name="VideoCoachOllamaV717").start()

    def _poll_ollama_v717(self) -> None:
        try:
            while True:
                text, _source = self._ollama_results_v717.get_nowait()
                self.chat_history.append({"role": "assistant", "content": text})
                self._append_chat(f"AI · {text}\n   [Ollama · 추가 답변]\n\n")
        except queue.Empty:
            pass
        except Exception:
            pass
        finally:
            try:
                if self.winfo_exists():
                    self.after(120, self._poll_ollama_v717)
            except Exception:
                pass

    def _deliver_report(self, report):
        super()._deliver_report(report)
        try:
            kills = int((report.get("combat") or {}).get("kills") or 0)
            self.kd_status_var.set(f"킬로그 킬 {kills}회 · 신규 행+강조 기준")
        except Exception:
            pass


if __name__ == "__main__":
    App().mainloop()
