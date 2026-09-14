from __future__ import annotations

import tkinter as tk

import customtkinter as ctk

from app_v6_8 import App as V68App
from main import ACCENT, BG, BORDER, MUTED, PANEL2, TEXT


class App(V68App):
    """v6.9: in-window AI coach drawer to avoid Windows/Tk Toplevel focus failures."""

    def __init__(self):
        self._chat_drawer = None
        self._chat_drawer_visible = False
        self._chat_typing_active = False
        super().__init__()
        self._v6_chat_window = None

    def open_chat_window(self) -> None:
        try:
            grabbed = self.grab_current()
            if grabbed is not None:
                grabbed.grab_release()
        except Exception:
            pass

        if self._chat_drawer is None or not self._chat_drawer.winfo_exists():
            self._build_chat_drawer()

        self._chat_drawer.place(relx=1.0, rely=0.0, anchor="ne", relheight=1.0, width=460)
        self._chat_drawer.lift()
        self._chat_drawer_visible = True
        self.after_idle(self._focus_chat_input)
        self.after(80, self._focus_chat_input)

    def _build_chat_drawer(self) -> None:
        lang = "en" if self.config_data.get("language") == "en" else "ko"
        drawer = ctk.CTkFrame(self, fg_color=BG, border_color=BORDER, border_width=1, corner_radius=0)
        self._chat_drawer = drawer

        header = ctk.CTkFrame(drawer, fg_color="transparent")
        header.pack(fill="x", padx=18, pady=(18, 8))
        title_wrap = ctk.CTkFrame(header, fg_color="transparent")
        title_wrap.pack(side="left", fill="x", expand=True)
        ctk.CTkLabel(title_wrap, text="LOCAL AI COACH", text_color=ACCENT, font=("Segoe UI", 11, "bold")).pack(anchor="w")
        ctk.CTkLabel(title_wrap, text="AI 코치" if lang == "ko" else "AI Coach", text_color=TEXT, font=("Segoe UI", 25, "bold")).pack(anchor="w")
        ctk.CTkButton(header, text="×", command=self._hide_chat_drawer, width=38, height=38, corner_radius=12, fg_color=PANEL2, hover_color="#2a3341", text_color=TEXT, font=("Segoe UI", 18, "bold")).pack(side="right", padx=(8, 0))

        self._v6_chat_status = tk.StringVar(value="입력 가능 · 로컬 코치" if lang == "ko" else "Ready · local coach")
        ctk.CTkLabel(drawer, textvariable=self._v6_chat_status, fg_color=PANEL2, text_color=MUTED, corner_radius=12, padx=12, pady=7, anchor="w").pack(fill="x", padx=18, pady=(0, 10))

        chat_shell = ctk.CTkFrame(drawer, fg_color="#11151d", corner_radius=16)
        chat_shell.pack(fill="both", expand=True, padx=18, pady=(0, 10))
        self._v6_chat_box = tk.Text(chat_shell, wrap="word", state="disabled", bg="#11151d", fg=TEXT, insertbackground=TEXT, selectbackground="#394151", relief="flat", padx=12, pady=12, font=("Segoe UI", 10), spacing1=2, spacing3=5, bd=0, highlightthickness=0, takefocus=False)
        self._v6_chat_box.pack(fill="both", expand=True, padx=4, pady=4)
        self._v6_chat_box.tag_configure("user", foreground="#dce6ff", font=("Segoe UI", 10, "bold"))
        self._v6_chat_box.tag_configure("ai", foreground=TEXT)
        self._v6_chat_box.tag_configure("system", foreground=MUTED, font=("Segoe UI", 9))
        self._v6_append_chat("AI · 아래 입력칸을 클릭하고 바로 입력하세요. Ollama가 없으면 내장 코치가 답합니다.\n\n" if lang == "ko" else "AI · Click the field below and type. The built-in coach is used if Ollama is unavailable.\n\n", "system")

        input_row = ctk.CTkFrame(drawer, fg_color="transparent")
        input_row.pack(fill="x", padx=18, pady=(0, 18))
        entry_shell = tk.Frame(input_row, bg=PANEL2, highlightthickness=1, highlightbackground=BORDER, highlightcolor=ACCENT, bd=0)
        entry_shell.pack(side="left", fill="x", expand=True)
        self._v6_chat_entry = tk.Entry(entry_shell, bg=PANEL2, fg=TEXT, insertbackground=TEXT, disabledbackground=PANEL2, disabledforeground=MUTED, relief="flat", bd=0, highlightthickness=0, font=("Segoe UI", 11), takefocus=True, exportselection=False)
        self._v6_chat_entry.pack(fill="x", padx=12, pady=11)
        self._v6_chat_entry.configure(state="normal")
        self._v6_chat_entry.bind("<Return>", self._chat_return)
        self._v6_chat_entry.bind("<Button-1>", lambda _e: self.after_idle(self._focus_chat_input))
        self._v6_chat_entry.bind("<FocusIn>", lambda _e: self._on_chat_focus(True))
        self._v6_chat_entry.bind("<FocusOut>", lambda _e: self._on_chat_focus(False))

        self._v6_chat_send_btn = ctk.CTkButton(input_row, text="전송" if lang == "ko" else "Send", command=self._v6_send_chat, width=78, height=42, corner_radius=12, fg_color=ACCENT, hover_color="#ff5b68")
        self._v6_chat_send_btn.pack(side="left", padx=(8, 0))
        ctk.CTkButton(input_row, text="초기화" if lang == "ko" else "Clear", command=self._v6_clear_chat, width=72, height=42, corner_radius=12, fg_color=PANEL2, hover_color="#2a3341").pack(side="left", padx=(7, 0))

    def _chat_return(self, _event=None):
        self._v6_send_chat()
        return "break"

    def _on_chat_focus(self, active: bool) -> None:
        self._chat_typing_active = bool(active)

    def _focus_chat_input(self) -> None:
        try:
            if self._chat_drawer is None or not self._chat_drawer.winfo_exists() or not self._chat_drawer_visible:
                return
            self._chat_drawer.lift()
            if self._v6_chat_entry is not None and self._v6_chat_entry.winfo_exists():
                self._v6_chat_entry.configure(state="normal")
                self._v6_chat_entry.focus_set()
                self._v6_chat_entry.icursor("end")
        except Exception:
            pass

    def _hide_chat_drawer(self) -> None:
        try:
            if self._chat_drawer is not None and self._chat_drawer.winfo_exists():
                self._chat_drawer.place_forget()
        finally:
            self._chat_drawer_visible = False
            self._chat_typing_active = False
            try:
                self.focus_set()
            except Exception:
                pass

    def _thread_key(self, event) -> None:
        if self._chat_typing_active:
            return
        super()._thread_key(event)

    def _thread_skill(self, event) -> None:
        if self._chat_typing_active:
            return
        super()._thread_skill(event)

    def _thread_shot(self, event) -> None:
        if self._chat_drawer_visible:
            return
        super()._thread_shot(event)

    def _v6_deliver_chat(self, reply) -> None:
        self.chat_history.append({"role": "assistant", "content": reply.text})
        self._v6_append_chat(f"AI · {reply.text}\n\n", "ai")
        self.chat_busy = False
        if self._v6_chat_status is not None:
            source = "Ollama" if reply.source == "ollama" else ("내장 코치" if self.config_data.get("language") != "en" else "built-in")
            self._v6_chat_status.set(f"{reply.model} · {source}")
        if self._v6_chat_send_btn is not None and self._v6_chat_send_btn.winfo_exists():
            self._v6_chat_send_btn.configure(state="normal")
        self.after_idle(self._focus_chat_input)


if __name__ == "__main__":
    App().mainloop()
