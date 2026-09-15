from __future__ import annotations

import copy
import queue
import threading
import tkinter as tk

import customtkinter as ctk

import app_v7 as rootbase
import app_v7_2 as v72
import app_v7_14 as v714
from video_review_v715 import analyze_video as analyze_video_v715

rootbase.analyze_video = analyze_video_v715
v72.base.analyze_video = analyze_video_v715

PANEL2 = rootbase.PANEL2
BORDER = rootbase.BORDER
TEXT = rootbase.TEXT
MUTED = rootbase.MUTED
ACCENT = rootbase.ACCENT
ACCENT_HOVER = rootbase.ACCENT_HOVER
FONT = rootbase.FONT


class App(v714.App):
    """v7.15: fresh event panel, reliable skill scoring and chat input."""

    def __init__(self):
        self._chat_results_v715: queue.Queue[tuple[str, str]] = queue.Queue()
        super().__init__()
        self.title("VALORANT Video Coach v7.15")
        self.after_idle(self._rebuild_event_panel_v715)
        self.after_idle(self._rebuild_chat_input_v715)
        self._append_chat(
            "AI · v7.15는 EVENT JUMP 패널을 새로 만들고, 스킬 HUD 검출과 채팅 입력/응답 경로를 다시 고쳤습니다.\n\n"
        )

    def _install_combat_correction_controls(self) -> None:
        return

    def _rebuild_event_panel_v715(self) -> None:
        try:
            player_card = self.video_label.master
            video_tab = player_card.master
        except Exception:
            return

        try:
            for child in list(video_tab.winfo_children()):
                if child is player_card:
                    continue
                try:
                    child.destroy()
                except Exception:
                    pass
        except Exception:
            return

        try:
            video_tab.grid_columnconfigure(0, weight=7, minsize=690)
            video_tab.grid_columnconfigure(1, weight=3, minsize=300)
            video_tab.grid_rowconfigure(0, weight=1)
        except Exception:
            pass

        event_card = ctk.CTkFrame(video_tab, fg_color="#0d1118", corner_radius=16, width=300)
        event_card.grid(row=0, column=1, sticky="nsew", padx=(6, 4), pady=4)
        event_card.grid_propagate(False)
        self._event_card_v715 = event_card

        ctk.CTkLabel(
            event_card, text="EVENT JUMP", text_color=ACCENT,
            font=(FONT, 10, "bold"), anchor="w",
        ).pack(fill="x", padx=12, pady=(12, 0))
        ctk.CTkLabel(
            event_card, text="감지된 시점", text_color=TEXT,
            font=(FONT, 15, "bold"), anchor="w",
        ).pack(fill="x", padx=12, pady=(1, 7))

        self.kd_status_var = tk.StringVar(value="킬로그 킬 · 스킬 · 사격 · 에임 이벤트")
        status = ctk.CTkFrame(event_card, fg_color="#141b25", corner_radius=10)
        status.pack(fill="x", padx=10, pady=(0, 8))
        ctk.CTkLabel(
            status, textvariable=self.kd_status_var, text_color=MUTED,
            font=(FONT, 8, "bold"), anchor="w", justify="left",
        ).pack(fill="x", padx=10, pady=8)

        self.event_list = ctk.CTkScrollableFrame(event_card, fg_color="transparent", corner_radius=0)
        self.event_list.pack(fill="both", expand=True, padx=6, pady=(0, 8))

        if self.last_report:
            self._populate_events(self.last_report)
            kills = int((self.last_report.get("combat") or {}).get("kills") or 0)
            skills = len(self.last_report.get("utility_events") or [])
            self.kd_status_var.set(f"킬로그 킬 {kills}회 · 스킬 후보 {skills}회")
        else:
            ctk.CTkLabel(
                self.event_list, text="분석 후 감지된 이벤트가 표시됩니다.",
                text_color=MUTED, font=(FONT, 9), anchor="w", justify="left",
            ).pack(fill="x", padx=6, pady=8)

    def _populate_events(self, report: dict) -> None:
        if not hasattr(self, "event_list"):
            return
        for child in self.event_list.winfo_children():
            try:
                child.destroy()
            except Exception:
                pass
        events = [e for e in (report.get("timeline_events") or []) if str(e.get("kind")) != "death"]
        if not events:
            ctk.CTkLabel(self.event_list, text="감지된 이벤트가 없습니다.", text_color=MUTED,
                         font=(FONT, 9), anchor="w").pack(fill="x", padx=6, pady=8)
            return
        colors = {"kill": ACCENT, "skill": "#5b55e7", "shot": "#2d7f68", "aim": "#8d48df"}
        for item in events:
            t = float(item.get("video_seconds") or 0.0)
            kind = str(item.get("kind") or "event")
            label = str(item.get("label") or kind)
            if kind == "kill":
                label = "킬로그 · 내 킬"
            ctk.CTkButton(
                self.event_list, text=f"{self._fmt_time(t)}   {label}",
                command=lambda sec=t: self._jump_event(sec), anchor="w",
                height=32, corner_radius=10, fg_color=colors.get(kind, PANEL2),
                hover_color="#354052", text_color="white", font=(FONT, 8, "bold"),
            ).pack(fill="x", padx=(4, 12), pady=3)

    def _deliver_report(self, report):
        super()._deliver_report(report)
        try:
            kills = int((report.get("combat") or {}).get("kills") or 0)
            skills = len(report.get("utility_events") or [])
            self.kd_status_var.set(f"킬로그 킬 {kills}회 · 스킬 후보 {skills}회")
        except Exception:
            pass
        if report.get("skill_score") is not None and str(report.get("mode") or "") != "brawl":
            self.metric_vars["skill"].set(self._score(report.get("skill_score")))
        self._populate_events(report)

    def _rebuild_chat_input_v715(self) -> None:
        old = getattr(self, "chat_entry", None)
        if old is None:
            return
        parent = old.master
        for child in list(parent.winfo_children()):
            try:
                child.destroy()
            except Exception:
                pass

        self.chat_entry = tk.Text(
            parent, height=2, wrap="word", bg="#171d27", fg=TEXT,
            insertbackground=TEXT, selectbackground="#3a4555", selectforeground=TEXT,
            relief="flat", bd=0, highlightthickness=1,
            highlightbackground=BORDER, highlightcolor=ACCENT,
            font=(FONT, 11), undo=True, takefocus=True,
        )
        self.chat_entry.pack(side="left", fill="x", expand=True, ipady=4)
        self.chat_entry.bind("<Return>", self._chat_enter_v715)
        self.chat_entry.bind("<Shift-Return>", lambda _e: None)

        self.chat_send = tk.Button(
            parent, text="전송", command=self.send_chat, width=7,
            bg=ACCENT, activebackground=ACCENT_HOVER,
            fg="white", activeforeground="white", relief="flat", bd=0,
            font=(FONT, 10, "bold"), cursor="hand2", padx=8, pady=10,
        )
        self.chat_send.pack(side="left", padx=(8, 0))
        try:
            self.chat_entry.focus_set()
        except Exception:
            pass

    def _chat_enter_v715(self, event=None):
        if event is not None and (event.state & 0x0001):
            return None
        self.send_chat()
        return "break"

    def send_chat(self) -> None:
        try:
            q = self.chat_entry.get("1.0", "end-1c").strip()
        except Exception:
            q = ""
        if not q:
            return
        if self.chat_busy:
            self._append_chat("AI · 이전 답변을 생성 중입니다. 잠시 후 다시 전송해 주세요.\n\n")
            return

        try:
            self.chat_entry.delete("1.0", "end")
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
            source = "builtin"
            try:
                reply = self.chat_client.ask(q, report_snapshot, previous)
                text = str(reply.text or "").strip() or "응답 내용이 비어 있습니다."
                source = str(getattr(reply, "source", "builtin"))
            except Exception as exc:
                text = f"AI 응답 오류: {type(exc).__name__}: {exc}"
            self._chat_results_v715.put((text, source))

        threading.Thread(target=worker, daemon=True, name="VideoCoachChatV715").start()

    def _poll_chat_results(self) -> None:
        try:
            while True:
                text, source = self._chat_results_v715.get_nowait()
                self.chat_history.append({"role": "assistant", "content": text})
                label = "Ollama" if source == "ollama" else "내장 코치"
                self._append_chat(f"AI · {text}\n   [{label}]\n\n")
                self.chat_busy = False
                try:
                    self.chat_send.configure(state="normal", text="전송")
                    self.chat_entry.focus_set()
                except Exception:
                    pass
        except queue.Empty:
            pass
        except Exception as exc:
            self.chat_busy = False
            try:
                self._append_chat(f"AI · 응답 표시 오류: {type(exc).__name__}: {exc}\n\n")
                self.chat_send.configure(state="normal", text="전송")
            except Exception:
                pass
        finally:
            try:
                if self.winfo_exists():
                    self.after(90, self._poll_chat_results)
            except Exception:
                pass


if __name__ == "__main__":
    App().mainloop()
