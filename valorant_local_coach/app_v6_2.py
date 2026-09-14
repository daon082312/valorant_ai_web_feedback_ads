from __future__ import annotations

import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog

import customtkinter as ctk

from app_v6 import App as V6App
from main import ACCENT, BG, BORDER, MUTED, PANEL, PANEL2, TEXT, WORK_DIR
from video_analyzer import VideoFileAnalyzer


class App(V6App):
    """v6.2 safety wrapper: guaranteed chat window + imported-video review."""

    def __init__(self):
        self._last_video_analysis = None
        self._video_analysis_window = None
        super().__init__()

    def _build_ui(self) -> None:
        super()._build_ui()
        if not hasattr(self, "round_video_btn"):
            parent = self.round_settings_btn.master
            self.round_video_btn = self._round_button(parent, "영상 분석", self.open_video_analysis, width=96)
            self.round_video_btn.pack(side="right", padx=(0, 6), before=self.round_settings_btn)

    def _apply_language(self) -> None:
        super()._apply_language()
        if hasattr(self, "round_video_btn"):
            self.round_video_btn.configure(text="Analyze Video" if self.config_data.get("language") == "en" else "영상 분석")

    def _chat_context(self) -> dict:
        context = super()._chat_context()
        context["video_analysis"] = self._last_video_analysis
        return context

    def open_chat_window(self) -> None:
        win = getattr(self, "chat_window", None)
        if win is not None and win.winfo_exists():
            win.deiconify()
            win.lift()
            if getattr(self, "chat_entry", None):
                self.chat_entry.focus_set()
            return

        self.chat_window = ctk.CTkToplevel(self)
        self.chat_window.title("VALORANT Local Coach · AI Chat")
        self.chat_window.geometry("760x700")
        self.chat_window.minsize(600, 520)
        self.chat_window.configure(fg_color=BG)
        self.chat_window.transient(self)

        root = ctk.CTkFrame(self.chat_window, fg_color=BG, corner_radius=0)
        root.pack(fill="both", expand=True, padx=20, pady=18)
        ctk.CTkLabel(root, text="LOCAL AI CHAT", text_color=ACCENT, font=("Segoe UI", 12, "bold")).pack(anchor="w")
        title = "코치 AI와 대화" if self.config_data.get("language") != "en" else "Chat with Coach AI"
        ctk.CTkLabel(root, text=title, text_color=TEXT, font=("Segoe UI", 26, "bold")).pack(anchor="w", pady=(2, 8))

        bar = ctk.CTkFrame(root, fg_color=PANEL, border_color=BORDER, border_width=1, corner_radius=16)
        bar.pack(fill="x", pady=(0, 10))
        self.ai_status_var = tk.StringVar(value=f"{self.config_data.get('ollama_model', 'qwen3:4b')} · local")
        ctk.CTkLabel(bar, textvariable=self.ai_status_var, text_color=MUTED, font=("Segoe UI", 11, "bold")).pack(side="left", padx=12, pady=10)
        ctk.CTkButton(bar, text="설정", command=lambda: self.open_settings("ai"), width=74, height=32, corner_radius=12, fg_color=PANEL2, hover_color="#2a3341").pack(side="right", padx=(4, 10), pady=8)
        ctk.CTkButton(bar, text="초기화", command=self.clear_chat, width=76, height=32, corner_radius=12, fg_color=PANEL2, hover_color="#2a3341").pack(side="right", padx=4, pady=8)

        self.chat_box = ctk.CTkTextbox(root, fg_color="#11151d", text_color=TEXT, corner_radius=16, font=("Segoe UI", 12), wrap="word")
        self.chat_box.pack(fill="both", expand=True)
        self.chat_box.insert("end", "AI · 최근 교전, 무빙, 스킬 사용 또는 불러온 영상 분석에 대해 질문하세요.\n\n")
        self.chat_box.configure(state="disabled")

        row = ctk.CTkFrame(root, fg_color="transparent")
        row.pack(fill="x", pady=(10, 0))
        self.chat_entry = ctk.CTkEntry(row, placeholder_text="질문 입력…", height=40, corner_radius=14)
        self.chat_entry.pack(side="left", fill="x", expand=True)
        self.chat_entry.bind("<Return>", lambda _e: self.send_chat())
        self.chat_send_btn = ctk.CTkButton(row, text="전송", command=self.send_chat, width=86, height=40, corner_radius=14, fg_color=ACCENT, hover_color="#ff5b68")
        self.chat_send_btn.pack(side="left", padx=(8, 0))
        self.chat_entry.focus_set()

    def _append_chat_v62(self, text: str) -> None:
        box = getattr(self, "chat_box", None)
        if box is None or not box.winfo_exists():
            return
        box.configure(state="normal")
        box.insert("end", text)
        box.see("end")
        box.configure(state="disabled")

    def send_chat(self) -> None:
        if getattr(self, "chat_busy", False) or not getattr(self, "chat_entry", None):
            return
        question = self.chat_entry.get().strip()
        if not question:
            return
        self.chat_entry.delete(0, "end")
        self._append_chat_v62(f"나 · {question}\n")
        history_before = list(getattr(self, "chat_history", []))
        self.chat_history.append({"role": "user", "content": question})
        context = self._chat_context()
        self.chat_busy = True
        self.chat_entry.configure(state="disabled")
        self.chat_send_btn.configure(state="disabled")
        if getattr(self, "ai_status_var", None):
            self.ai_status_var.set(f"{self.config_data.get('ollama_model', 'qwen3:4b')} · 답변 생성 중…")

        def worker() -> None:
            reply = self.chat_client.ask(question, context, history_before)
            self.after(0, lambda: self._deliver_chat_reply_v62(reply))

        threading.Thread(target=worker, daemon=True, name="LocalCoachChat").start()

    def _deliver_chat_reply_v62(self, reply) -> None:
        self.chat_history.append({"role": "assistant", "content": reply.text})
        self._append_chat_v62(f"AI · {reply.text}\n\n")
        if getattr(self, "ai_status_var", None):
            self.ai_status_var.set(f"{reply.model} · {'Ollama' if reply.source == 'ollama' else '기본 코치'}")
        self.chat_busy = False
        if getattr(self, "chat_entry", None) and self.chat_entry.winfo_exists():
            self.chat_entry.configure(state="normal")
            self.chat_entry.focus_set()
        if getattr(self, "chat_send_btn", None) and self.chat_send_btn.winfo_exists():
            self.chat_send_btn.configure(state="normal")

    def clear_chat(self) -> None:
        if getattr(self, "chat_busy", False):
            return
        self.chat_history.clear()
        box = getattr(self, "chat_box", None)
        if box is not None and box.winfo_exists():
            box.configure(state="normal")
            box.delete("1.0", "end")
            box.insert("end", "AI · 대화를 초기화했습니다. 현재 세션/영상 분석 데이터는 계속 참고합니다.\n")
            box.configure(state="disabled")

    def open_video_analysis(self) -> None:
        path = filedialog.askopenfilename(parent=self, title="분석할 VALORANT 영상 선택", filetypes=[("Video files", "*.mp4 *.avi *.mov *.mkv *.webm"), ("All files", "*.*")])
        if not path:
            return

        old = self._video_analysis_window
        if old is not None and old.winfo_exists():
            old.destroy()

        lang = "en" if self.config_data.get("language") == "en" else "ko"
        win = ctk.CTkToplevel(self)
        self._video_analysis_window = win
        win.title("VALORANT Local Coach · Video Analysis")
        win.geometry("760x600")
        win.minsize(620, 500)
        win.configure(fg_color=BG)
        win.transient(self)

        root = ctk.CTkFrame(win, fg_color=BG, corner_radius=0)
        root.pack(fill="both", expand=True, padx=20, pady=18)
        ctk.CTkLabel(root, text="LOCAL VIDEO REVIEW", text_color=ACCENT, font=("Segoe UI", 12, "bold")).pack(anchor="w")
        ctk.CTkLabel(root, text=("영상 피드백" if lang == "ko" else "Video Feedback"), text_color=TEXT, font=("Segoe UI", 26, "bold")).pack(anchor="w", pady=(2, 2))
        ctk.CTkLabel(root, text=Path(path).name, text_color=MUTED, font=("Segoe UI", 12)).pack(anchor="w", pady=(0, 12))

        card = ctk.CTkFrame(root, fg_color=PANEL, border_color=BORDER, border_width=1, corner_radius=18)
        card.pack(fill="x", pady=(0, 12))
        status_var = tk.StringVar(value=("분석 준비 중…" if lang == "ko" else "Preparing analysis…"))
        ctk.CTkLabel(card, textvariable=status_var, text_color=TEXT, font=("Segoe UI", 12, "bold")).pack(anchor="w", padx=14, pady=(12, 6))
        progressbar = ctk.CTkProgressBar(card, height=12, corner_radius=6, progress_color=ACCENT)
        progressbar.pack(fill="x", padx=14, pady=(0, 14))
        progressbar.set(0)

        result_box = ctk.CTkTextbox(root, fg_color="#11151d", text_color=TEXT, corner_radius=16, font=("Segoe UI", 12), wrap="word")
        result_box.pack(fill="both", expand=True)
        result_box.configure(state="disabled")

        def append(text: str) -> None:
            result_box.configure(state="normal")
            result_box.insert("end", text)
            result_box.see("end")
            result_box.configure(state="disabled")

        def progress(value: float, label: str) -> None:
            def apply() -> None:
                if win.winfo_exists():
                    progressbar.set(max(0, min(1, value)))
                    status_var.set(label)
            self.after(0, apply)

        def finish(result: dict | None, error: str | None = None) -> None:
            if not win.winfo_exists():
                return
            if error:
                status_var.set("분석 실패" if lang == "ko" else "Analysis failed")
                append(f"오류 · {error}\n")
                return
            self._last_video_analysis = result
            progressbar.set(1)
            status_var.set("분석 완료" if lang == "ko" else "Analysis complete")
            append(f"영상 {result.get('duration_seconds', 0):.1f}s · 샘플 {result.get('sampled_frames', 0)}프레임\n" f"탄약 HUD 추정 사격 {result.get('estimated_shots', 0)}발 · 사격 구간 {result.get('shot_bursts', 0)}개 · 평균 버스트 {result.get('average_burst_size', 0)}발\n" f"헤드라인 {result.get('headline_score') if result.get('headline_score') is not None else '--'} · 적 후보 프레임 {result.get('enemy_detection_frames', 0)} · 스킬 HUD 변화 {result.get('skill_hud_changes', 0)}회\n\n")
            for item in result.get("feedback") or []:
                append(f"• {item}\n")
            append(f"\n분석 JSON · {result.get('result_json')}\n")
            self._append_feedback(f"\n영상 분석 완료 · {result.get('filename')}\n", "heading")
            for item in (result.get("feedback") or [])[:4]:
                self._append_feedback(f"• 영상 · {item}\n")

        def worker() -> None:
            try:
                result = VideoFileAnalyzer(WORK_DIR, self.config_data).analyze(path, progress)
                self.after(0, lambda: finish(result))
            except Exception as exc:
                self.after(0, lambda: finish(None, f"{type(exc).__name__}: {exc}"))

        threading.Thread(target=worker, daemon=True, name="ImportedVideoAnalysis").start()


if __name__ == "__main__":
    App().mainloop()
