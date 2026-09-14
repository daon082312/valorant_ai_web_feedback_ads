from __future__ import annotations

import threading
import tkinter as tk
from dataclasses import asdict
from tkinter import messagebox

from local_chat import LocalCoachChat
from main import ACCENT, ACCENT_HOVER, BG, BORDER, MUTED, PANEL, PANEL2, TEXT, App as BaseApp, save_config


AI_DEFAULTS = {
    "local_ai_enabled": True,
    "ollama_url": "http://127.0.0.1:11434",
    "ollama_model": "qwen3:4b",
    "ollama_timeout_seconds": 90,
    "chat_history_messages": 10,
}


def _ensure_ai_config(app) -> None:
    changed = False
    for key, value in AI_DEFAULTS.items():
        if key not in app.config_data:
            app.config_data[key] = value
            changed = True
    if changed:
        save_config(app.config_data)


if hasattr(BaseApp, "send_chat"):
    App = BaseApp
else:
    class App(BaseApp):
        """Compatibility wrapper that adds a same-style local AI chat window."""

        def __init__(self):
            super().__init__()
            _ensure_ai_config(self)
            self.chat_client = LocalCoachChat(self.config_data)
            self.chat_history: list[dict[str, str]] = []
            self.chat_busy = False
            self.chat_window = None
            self.chat_box = None
            self.chat_entry = None
            self.chat_send_btn = None
            self.ai_status_var = None
            self.after(150, self._install_ai_button)

        def _walk_widgets(self, parent):
            for child in parent.winfo_children():
                yield child
                yield from self._walk_widgets(child)

        def _install_ai_button(self) -> None:
            for widget in self._walk_widgets(self):
                if isinstance(widget, tk.Button) and str(widget.cget("text")) == "기준 세션 학습":
                    self._button(widget.master, "AI 채팅", self.open_chat_window).pack(side="left", padx=(8, 0))
                    break

        def _chat_context(self) -> dict:
            fights = self.engine.fights
            latest = asdict(fights[-1]) if fights else {}
            return {
                "session_running": self.session_running,
                "session_summary": self.engine.session_summary(),
                "latest_fight": latest,
                "skill_bindings": self.config_data.get("skill_bindings", {}),
            }

        def _append_chat(self, text: str, tag: str | None = None) -> None:
            if not self.chat_box or not self.chat_box.winfo_exists():
                return
            self.chat_box.configure(state="normal")
            self.chat_box.insert("end", text, tag or ())
            self.chat_box.see("end")
            self.chat_box.configure(state="disabled")

        def open_chat_window(self) -> None:
            if self.chat_window and self.chat_window.winfo_exists():
                self.chat_window.deiconify()
                self.chat_window.lift()
                if self.chat_entry:
                    self.chat_entry.focus_set()
                return

            win = tk.Toplevel(self)
            self.chat_window = win
            win.title("VALORANT Local Coach · AI Chat")
            win.geometry("720x680")
            win.minsize(560, 520)
            win.configure(bg=BG)

            root = tk.Frame(win, bg=BG, padx=20, pady=18)
            root.pack(fill="both", expand=True)
            tk.Label(root, text="LOCAL AI CHAT", bg=BG, fg=ACCENT, font=("Segoe UI", 9, "bold")).pack(anchor="w")
            tk.Label(root, text="코치 AI와 대화", bg=BG, fg=TEXT, font=("Segoe UI", 24, "bold")).pack(anchor="w", pady=(3, 2))
            tk.Label(
                root,
                text="현재 세션의 교전·무빙·Shift/Ctrl·정지→첫 탄·스킬 타이밍을 자동으로 참고합니다.",
                bg=BG, fg=MUTED, font=("Segoe UI", 9),
            ).pack(anchor="w", pady=(0, 12))

            bar = tk.Frame(root, bg=PANEL, highlightthickness=1, highlightbackground=BORDER, padx=12, pady=10)
            bar.pack(fill="x", pady=(0, 10))
            self.ai_status_var = tk.StringVar(value=f"{self.config_data.get('ollama_model', 'qwen3:4b')} · 로컬")
            tk.Label(bar, textvariable=self.ai_status_var, bg=PANEL, fg=MUTED, font=("Segoe UI", 9, "bold")).pack(side="left")
            self._button(bar, "AI 설정", self.open_ai_settings).pack(side="right")
            self._button(bar, "대화 초기화", self.clear_chat).pack(side="right", padx=(0, 8))

            self.chat_box = tk.Text(
                root, wrap="word", state="disabled", bg="#11151d", fg=TEXT,
                insertbackground=TEXT, selectbackground="#394151", relief="flat",
                padx=12, pady=12, font=("Segoe UI", 10), spacing1=2, spacing3=5,
            )
            self.chat_box.pack(fill="both", expand=True)
            self.chat_box.tag_configure("user", foreground="#dce6ff", font=("Segoe UI", 10, "bold"))
            self.chat_box.tag_configure("ai", foreground=TEXT)
            self.chat_box.tag_configure("system", foreground=MUTED, font=("Segoe UI", 9))
            self._append_chat("AI · 최근 교전이나 무빙, 스킬 타이밍에 대해 물어보세요.\n\n", "system")

            input_row = tk.Frame(root, bg=BG)
            input_row.pack(fill="x", pady=(10, 0))
            self.chat_entry = tk.Entry(
                input_row, bg=PANEL2, fg=TEXT, insertbackground=TEXT,
                relief="flat", font=("Segoe UI", 10),
            )
            self.chat_entry.pack(side="left", fill="x", expand=True, ipady=9)
            self.chat_entry.bind("<Return>", lambda _e: self.send_chat())
            self.chat_send_btn = self._button(input_row, "전송", self.send_chat, primary=True, width=9)
            self.chat_send_btn.pack(side="left", padx=(8, 0))
            self.chat_entry.focus_set()

        def send_chat(self) -> None:
            if self.chat_busy or not self.chat_entry:
                return
            question = self.chat_entry.get().strip()
            if not question:
                return
            self.chat_entry.delete(0, "end")
            self._append_chat(f"나 · {question}\n", "user")
            history_before = list(self.chat_history)
            self.chat_history.append({"role": "user", "content": question})
            context = self._chat_context()
            self.chat_busy = True
            self.chat_entry.configure(state="disabled")
            if self.chat_send_btn:
                self.chat_send_btn.configure(state="disabled", bg="#5e2730")
            if self.ai_status_var:
                self.ai_status_var.set(f"{self.config_data.get('ollama_model', 'qwen3:4b')} · 답변 생성 중…")

            def worker() -> None:
                reply = self.chat_client.ask(question, context, history_before)
                self.after(0, lambda: self._deliver_chat_reply(reply))

            threading.Thread(target=worker, daemon=True).start()

        def _deliver_chat_reply(self, reply) -> None:
            self.chat_history.append({"role": "assistant", "content": reply.text})
            self._append_chat(f"AI · {reply.text}\n\n", "ai")
            if self.ai_status_var:
                self.ai_status_var.set(f"{reply.model} · {'Ollama' if reply.source == 'ollama' else '기본 코치'}")
            self.chat_busy = False
            if self.chat_entry and self.chat_entry.winfo_exists():
                self.chat_entry.configure(state="normal")
                self.chat_entry.focus_set()
            if self.chat_send_btn and self.chat_send_btn.winfo_exists():
                self.chat_send_btn.configure(state="normal", bg=ACCENT)

        def clear_chat(self) -> None:
            if self.chat_busy:
                return
            self.chat_history.clear()
            if self.chat_box and self.chat_box.winfo_exists():
                self.chat_box.configure(state="normal")
                self.chat_box.delete("1.0", "end")
                self.chat_box.configure(state="disabled")
                self._append_chat("AI · 대화 기록을 초기화했습니다. 현재 세션 측정값은 계속 참고합니다.\n", "system")

        def open_ai_settings(self) -> None:
            dialog = tk.Toplevel(self)
            dialog.title("로컬 AI 설정")
            dialog.geometry("620x390")
            dialog.resizable(False, False)
            dialog.configure(bg=BG)
            dialog.transient(self)
            dialog.grab_set()

            root = tk.Frame(dialog, bg=BG, padx=22, pady=20)
            root.pack(fill="both", expand=True)
            tk.Label(root, text="LOCAL AI", bg=BG, fg=ACCENT, font=("Segoe UI", 9, "bold")).pack(anchor="w")
            tk.Label(root, text="코치 AI 설정", bg=BG, fg=TEXT, font=("Segoe UI", 22, "bold")).pack(anchor="w", pady=(3, 4))
            tk.Label(root, text="Ollama를 실행하면 외부 API 비용 없이 로컬 모델과 대화합니다.", bg=BG, fg=MUTED, font=("Segoe UI", 9)).pack(anchor="w", pady=(0, 14))

            card = tk.Frame(root, bg=PANEL, highlightthickness=1, highlightbackground=BORDER, padx=14, pady=14)
            card.pack(fill="x")

            def field(label: str, value: str) -> tk.Entry:
                row = tk.Frame(card, bg=PANEL)
                row.pack(fill="x", pady=6)
                tk.Label(row, text=label, bg=PANEL, fg=MUTED, width=16, anchor="w", font=("Segoe UI", 9, "bold")).pack(side="left")
                entry = tk.Entry(row, bg=PANEL2, fg=TEXT, insertbackground=TEXT, relief="flat", font=("Consolas", 10))
                entry.insert(0, value)
                entry.pack(side="left", fill="x", expand=True, ipady=7)
                return entry

            url_entry = field("Ollama 주소", str(self.config_data.get("ollama_url", AI_DEFAULTS["ollama_url"])))
            model_entry = field("모델", str(self.config_data.get("ollama_model", AI_DEFAULTS["ollama_model"])))
            enabled_var = tk.BooleanVar(value=bool(self.config_data.get("local_ai_enabled", True)))
            tk.Checkbutton(
                card, text="Ollama 로컬 AI 사용", variable=enabled_var, bg=PANEL, fg=TEXT,
                activebackground=PANEL, activeforeground=TEXT, selectcolor=PANEL2, font=("Segoe UI", 9),
            ).pack(anchor="w", pady=(8, 0))

            status_var = tk.StringVar(value="연결 테스트로 Ollama와 모델 설치 상태를 확인할 수 있습니다.")
            tk.Label(root, textvariable=status_var, bg=BG, fg=MUTED, font=("Segoe UI", 9), wraplength=560, justify="left").pack(anchor="w", pady=(12, 0))

            buttons = tk.Frame(root, bg=BG)
            buttons.pack(fill="x", side="bottom", pady=(15, 0))

            def test() -> None:
                status_var.set("연결 확인 중…")
                cfg = dict(self.config_data)
                cfg["ollama_url"] = url_entry.get().strip().rstrip("/")
                cfg["ollama_model"] = model_entry.get().strip() or "qwen3:4b"

                def worker() -> None:
                    result = LocalCoachChat(cfg).status()
                    def done() -> None:
                        if result.get("online") and result.get("model_installed"):
                            status_var.set(f"연결 성공 · {cfg['ollama_model']} 사용 가능")
                        elif result.get("online"):
                            status_var.set(f"Ollama 연결 성공 · 모델 없음. 터미널에서: ollama pull {cfg['ollama_model']}")
                        else:
                            status_var.set("연결 실패 · Ollama가 실행 중인지 확인하세요. 기본 코치 모드는 계속 동작합니다.")
                    self.after(0, done)
                threading.Thread(target=worker, daemon=True).start()

            def save() -> None:
                url = url_entry.get().strip().rstrip("/")
                model = model_entry.get().strip()
                if not url.startswith(("http://", "https://")):
                    messagebox.showerror("AI 설정", "Ollama 주소는 http:// 또는 https://로 시작해야 합니다.", parent=dialog)
                    return
                if not model:
                    messagebox.showerror("AI 설정", "모델명을 입력해 주세요.", parent=dialog)
                    return
                self.config_data["ollama_url"] = url
                self.config_data["ollama_model"] = model
                self.config_data["local_ai_enabled"] = bool(enabled_var.get())
                save_config(self.config_data)
                self.chat_client.update_config(self.config_data)
                if self.ai_status_var:
                    self.ai_status_var.set(f"{model} · 로컬")
                dialog.destroy()

            self._button(buttons, "저장", save, primary=True, width=10).pack(side="right")
            self._button(buttons, "취소", dialog.destroy, width=10).pack(side="right", padx=(0, 8))
            self._button(buttons, "연결 테스트", test, width=12).pack(side="left")


if __name__ == "__main__":
    App().mainloop()
