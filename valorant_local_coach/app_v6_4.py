from __future__ import annotations

import threading
import tkinter as tk

import customtkinter as ctk

from app_v6 import App as V6App
from local_chat import ChatReply, LocalCoachChat
from main import ACCENT, BG, BORDER, MUTED, PANEL2, TEXT, save_config


class NoVideoRecorder:
    """Compatibility recorder that never buffers or writes fight videos."""

    def __init__(self):
        self._session_dir = None
        self.buffer_fps = 0
        self.record_width = 0

    def start_session(self) -> None:
        self._session_dir = None

    def on_frame(self, _sample) -> None:
        return

    def save_fight(self, *args, **kwargs):
        return None, None


class App(V6App):
    """v6.4 stability patch: no auto video recording + reliable AI chat input."""

    def __init__(self):
        super().__init__()
        self.config_data["record_fight_clips"] = False
        save_config(self.config_data)
        self.recorder = NoVideoRecorder()
        try:
            if hasattr(self, "round_clips_btn"):
                self.round_clips_btn.pack_forget()
        except Exception:
            pass

        self.chat_client = LocalCoachChat(self.config_data)
        self.chat_history: list[dict[str, str]] = []
        self.chat_busy = False
        self._v6_chat_window = None
        self._v6_chat_box = None
        self._v6_chat_entry = None
        self._v6_chat_send_btn = None
        self._v6_chat_status = None

    def open_chat_window(self) -> None:
        if self._v6_chat_window is not None and self._v6_chat_window.winfo_exists():
            self._v6_chat_window.deiconify()
            self._v6_chat_window.lift()
            self._focus_chat_input()
            return

        try:
            grabbed = self.grab_current()
            if grabbed is not None:
                grabbed.grab_release()
        except Exception:
            pass

        lang = "en" if self.config_data.get("language") == "en" else "ko"
        win = ctk.CTkToplevel(self)
        self._v6_chat_window = win
        win.title("VALORANT Local Coach · AI Chat")
        win.geometry("760x700")
        win.minsize(600, 520)
        win.configure(fg_color=BG)
        win.transient(self)

        root = ctk.CTkFrame(win, fg_color=BG, corner_radius=0)
        root.pack(fill="both", expand=True, padx=20, pady=18)
        ctk.CTkLabel(root, text="LOCAL AI CHAT", text_color=ACCENT, font=("Segoe UI", 12, "bold")).pack(anchor="w")
        ctk.CTkLabel(root, text="코치 AI와 대화" if lang == "ko" else "Talk to Coach AI", text_color=TEXT, font=("Segoe UI", 26, "bold")).pack(anchor="w", pady=(2, 8))

        self._v6_chat_status = tk.StringVar(value="입력 가능 · 로컬 코치" if lang == "ko" else "Ready · local coach")
        ctk.CTkLabel(root, textvariable=self._v6_chat_status, fg_color=PANEL2, text_color=MUTED, corner_radius=12, padx=12, pady=7).pack(fill="x", pady=(0, 10))

        wrap = ctk.CTkFrame(root, fg_color="#11151d", corner_radius=18)
        wrap.pack(fill="both", expand=True)
        self._v6_chat_box = tk.Text(wrap, wrap="word", state="disabled", bg="#11151d", fg=TEXT, insertbackground=TEXT, selectbackground="#394151", relief="flat", padx=14, pady=14, font=("Segoe UI", 10), spacing1=2, spacing3=5, bd=0, highlightthickness=0)
        self._v6_chat_box.pack(fill="both", expand=True, padx=5, pady=5)
        self._v6_chat_box.tag_configure("user", foreground="#dce6ff", font=("Segoe UI", 10, "bold"))
        self._v6_chat_box.tag_configure("ai", foreground=TEXT)
        self._v6_chat_box.tag_configure("system", foreground=MUTED, font=("Segoe UI", 9))
        self._v6_append_chat("AI · 질문을 입력하세요. Ollama가 없으면 내장 코치가 바로 답합니다.\n\n" if lang == "ko" else "AI · Type a question. If Ollama is unavailable, the built-in coach answers instead.\n\n", "system")

        row = ctk.CTkFrame(root, fg_color="transparent")
        row.pack(fill="x", pady=(10, 0))
        entry_shell = ctk.CTkFrame(row, fg_color=PANEL2, border_color=BORDER, border_width=1, corner_radius=13)
        entry_shell.pack(side="left", fill="x", expand=True)
        self._v6_chat_entry = tk.Entry(entry_shell, bg=PANEL2, fg=TEXT, insertbackground=TEXT, relief="flat", bd=0, highlightthickness=0, font=("Segoe UI", 11), takefocus=True)
        self._v6_chat_entry.pack(fill="x", padx=12, pady=10)
        self._v6_chat_entry.configure(state="normal")
        self._v6_chat_entry.bind("<Return>", lambda _e: self._v6_send_chat())
        self._v6_chat_entry.bind("<Button-1>", lambda _e: self.after(1, self._focus_chat_input))

        self._v6_chat_send_btn = ctk.CTkButton(row, text="전송" if lang == "ko" else "Send", command=self._v6_send_chat, width=88, height=40, corner_radius=13, fg_color=ACCENT, hover_color="#ff5b68")
        self._v6_chat_send_btn.pack(side="left", padx=(8, 0))
        ctk.CTkButton(row, text="초기화" if lang == "ko" else "Clear", command=self._v6_clear_chat, width=82, height=40, corner_radius=13, fg_color=PANEL2, hover_color="#2a3341").pack(side="left", padx=(7, 0))

        def on_close() -> None:
            try:
                win.grab_release()
            except Exception:
                pass
            self._v6_chat_window = None
            self._v6_chat_box = None
            self._v6_chat_entry = None
            self._v6_chat_send_btn = None
            self._v6_chat_status = None
            win.destroy()

        win.protocol("WM_DELETE_WINDOW", on_close)
        self.after(100, self._focus_chat_input)
        self.after(300, self._focus_chat_input)

    def _focus_chat_input(self) -> None:
        try:
            if self._v6_chat_window is None or not self._v6_chat_window.winfo_exists():
                return
            self._v6_chat_window.deiconify()
            self._v6_chat_window.lift()
            self._v6_chat_window.focus_force()
            if self._v6_chat_entry is not None and self._v6_chat_entry.winfo_exists():
                self._v6_chat_entry.configure(state="normal")
                self._v6_chat_entry.focus_force()
                self._v6_chat_entry.icursor("end")
        except Exception:
            pass

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
        if self._v6_chat_status is not None:
            self._v6_chat_status.set("AI 연결 확인 중…" if self.config_data.get("language") != "en" else "Checking local AI…")

        def worker() -> None:
            try:
                status = self.chat_client.status()
                if not status.get("online") or not status.get("model_installed"):
                    fallback = LocalCoachChat._fallback_reply(question, context)
                    note = "Ollama/모델을 사용할 수 없어 내장 코치로 답변했습니다." if self.config_data.get("language") != "en" else "Ollama/model unavailable; answered with the built-in coach."
                    reply = ChatReply(f"{fallback}\n\n({note})", "builtin", self.chat_client.model, False)
                else:
                    reply = self.chat_client.ask(question, context, history_before)
            except Exception as exc:
                fallback = LocalCoachChat._fallback_reply(question, context)
                reply = ChatReply(f"{fallback}\n\n(내장 코치로 전환됨: {type(exc).__name__})", "builtin", self.chat_client.model, False)
            self.after(0, lambda r=reply: self._v6_deliver_chat(r))

        threading.Thread(target=worker, daemon=True, name="LocalCoachChatV64").start()

    def _v6_deliver_chat(self, reply) -> None:
        self.chat_history.append({"role": "assistant", "content": reply.text})
        self._v6_append_chat(f"AI · {reply.text}\n\n", "ai")
        self.chat_busy = False
        if self._v6_chat_status is not None:
            self._v6_chat_status.set(f"{reply.model} · {'Ollama' if reply.source == 'ollama' else '내장 코치'}")
        if self._v6_chat_send_btn is not None and self._v6_chat_send_btn.winfo_exists():
            self._v6_chat_send_btn.configure(state="normal")
        self._focus_chat_input()

    def _v6_clear_chat(self) -> None:
        if self.chat_busy:
            return
        self.chat_history.clear()
        box = self._v6_chat_box
        if box is not None and box.winfo_exists():
            box.configure(state="normal")
            box.delete("1.0", "end")
            box.configure(state="disabled")
            self._v6_append_chat("AI · 대화 기록을 초기화했습니다.\n\n" if self.config_data.get("language") != "en" else "AI · Chat history cleared.\n\n", "system")
        self._focus_chat_input()


if __name__ == "__main__":
    App().mainloop()
