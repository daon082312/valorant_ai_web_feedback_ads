from __future__ import annotations

import copy
import queue
import threading
import tkinter as tk

import app_v7 as rootbase
import app_v7_2 as v72
import app_v7_15 as v715
from video_review_v716 import analyze_video as analyze_video_v716

rootbase.analyze_video = analyze_video_v716
v72.base.analyze_video = analyze_video_v716

TEXT = rootbase.TEXT
BORDER = rootbase.BORDER
ACCENT = rootbase.ACCENT
ACCENT_HOVER = rootbase.ACCENT_HOVER
FONT = rootbase.FONT


class App(v715.App):
    """v7.16: independent native chat entry and conservative killfeed v2."""

    def __init__(self):
        self._chat_results_v716: queue.Queue[tuple[str, str]] = queue.Queue()
        super().__init__()
        self.title("VALORANT Video Coach v7.16")
        self.after_idle(self._rebuild_chat_input_v716)
        self.after(100, self._poll_chat_results_v716)
        self._append_chat(
            "AI · v7.16은 질문 입력 경로를 단순한 Windows Tk Entry로 다시 만들고, 킬로그는 2프레임 확인+중복 제거 후 확정합니다.\n\n"
        )

    def _rebuild_chat_input_v716(self) -> None:
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
        self.chat_var_v716 = tk.StringVar(value="")
        self.chat_entry = tk.Entry(
            parent,
            textvariable=self.chat_var_v716,
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
        )
        self.chat_entry.pack(side="left", fill="x", expand=True, ipady=10)
        self.chat_entry.bind("<Return>", self._chat_enter_v716)
        self.chat_entry.bind("<Button-1>", lambda _e: self.after_idle(self.chat_entry.focus_force))

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
        )
        self.chat_send.pack(side="left", padx=(8, 0))
        try:
            self.chat_entry.focus_force()
        except Exception:
            pass

    def _chat_enter_v716(self, _event=None):
        self.send_chat()
        return "break"

    def send_chat(self) -> None:
        try:
            q = self.chat_var_v716.get().strip()
        except Exception:
            try:
                q = self.chat_entry.get().strip()
            except Exception:
                q = ""
        if not q:
            try:
                self.chat_entry.focus_force()
            except Exception:
                pass
            return
        if self.chat_busy:
            self._append_chat("AI · 이전 답변을 생성 중입니다. 답변이 끝난 뒤 다시 전송해 주세요.\n\n")
            return

        try:
            self.chat_var_v716.set("")
        except Exception:
            pass
        self._append_chat(f"나 · {q}\n")
        previous = list(self.chat_history)
        self.chat_history.append({"role": "user", "content": q})
        self.chat_busy = True
        try:
            self.chat_send.configure(state="disabled", text="…")
        except Exception:
            pass
        report_snapshot = copy.deepcopy(self.last_report or {})

        def worker() -> None:
            try:
                reply = self.chat_client.ask(q, report_snapshot, previous)
                text = str(getattr(reply, "text", "") or "").strip()
                if not text:
                    text = "응답 내용이 비어 있습니다."
                source = str(getattr(reply, "source", "builtin") or "builtin")
            except Exception as exc:
                try:
                    text = str(self.chat_client.fallback(q, report_snapshot, False))
                    source = "builtin"
                except Exception:
                    text = f"AI 응답 오류: {type(exc).__name__}: {exc}"
                    source = "builtin"
            self._chat_results_v716.put((text, source))

        threading.Thread(target=worker, daemon=True, name="VideoCoachChatV716").start()

    def _poll_chat_results_v716(self) -> None:
        try:
            while True:
                text, source = self._chat_results_v716.get_nowait()
                self.chat_history.append({"role": "assistant", "content": text})
                label = "Ollama" if source == "ollama" else "내장 코치"
                self._append_chat(f"AI · {text}\n   [{label}]\n\n")
                self.chat_busy = False
                try:
                    self.chat_send.configure(state="normal", text="전송")
                    self.chat_entry.focus_force()
                except Exception:
                    pass
        except queue.Empty:
            pass
        except Exception as exc:
            self.chat_busy = False
            try:
                self.chat_send.configure(state="normal", text="전송")
                self._append_chat(f"AI · 응답 표시 오류: {type(exc).__name__}: {exc}\n\n")
            except Exception:
                pass
        finally:
            try:
                if self.winfo_exists():
                    self.after(100, self._poll_chat_results_v716)
            except Exception:
                pass

    def _deliver_report(self, report):
        super()._deliver_report(report)
        try:
            kills = int((report.get("combat") or {}).get("kills") or 0)
            self.kd_status_var.set(f"확정 킬로그 킬 {kills}회 · 2프레임 확인/중복 제거")
        except Exception:
            pass


if __name__ == "__main__":
    App().mainloop()
