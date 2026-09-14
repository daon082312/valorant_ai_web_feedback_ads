from __future__ import annotations

import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox

import cv2
import customtkinter as ctk
from PIL import Image, ImageTk

import app_v7 as base
from video_review_v72 import analyze_video as analyze_video_v72

base.analyze_video = analyze_video_v72

BG = base.BG; PANEL = base.PANEL; PANEL2 = base.PANEL2; BORDER = base.BORDER
TEXT = base.TEXT; MUTED = base.MUTED; ACCENT = base.ACCENT; ACCENT_HOVER = base.ACCENT_HOVER
GOOD = base.GOOD; FONT = base.FONT

class App(base.App):
    def __init__(self):
        self._player_cap = None; self._player_path = None; self._player_fps = 30.0
        self._player_duration = 0.0; self._player_current = 0.0; self._player_playing = False
        self._player_after = None; self._player_photo = None; self._player_step = 1; self._slider_internal = False
        super().__init__()
        self.title("VALORANT Video Coach v7.2")
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _build_ui(self):
        root = ctk.CTkFrame(self, fg_color=BG, corner_radius=0); root.pack(fill="both", expand=True, padx=24, pady=20)
        header = ctk.CTkFrame(root, fg_color="transparent"); header.pack(fill="x", pady=(0, 14))
        left = ctk.CTkFrame(header, fg_color="transparent"); left.pack(side="left", fill="x", expand=True)
        ctk.CTkLabel(left, text="OFFLINE · VIDEO REVIEW + EVENT NAVIGATION", text_color=ACCENT, font=(FONT, 12, "bold")).pack(anchor="w")
        ctk.CTkLabel(left, text="VALORANT Video Coach", text_color=TEXT, font=(FONT, 32, "bold")).pack(anchor="w", pady=(2, 0))
        ctk.CTkLabel(left, text="영상을 재생하며 킬·데스·스킬·사격·에임 평가 시점으로 즉시 이동할 수 있습니다.", text_color=MUTED, font=(FONT, 12)).pack(anchor="w", pady=(3, 0))
        ctk.CTkButton(header, text="설정", command=self.open_settings, width=86, height=38, corner_radius=14, fg_color=PANEL2, hover_color="#252e3b", border_width=1, border_color=BORDER, font=(FONT, 11, "bold")).pack(side="right", padx=(10, 0))

        picker = ctk.CTkFrame(root, fg_color=PANEL, border_color=BORDER, border_width=1, corner_radius=22); picker.pack(fill="x", pady=(0, 12))
        pleft = ctk.CTkFrame(picker, fg_color="transparent"); pleft.pack(side="left", fill="x", expand=True, padx=18, pady=15)
        ctk.CTkLabel(pleft, text="VIDEO REVIEW", text_color=ACCENT, font=(FONT, 10, "bold")).pack(anchor="w")
        ctk.CTkLabel(pleft, textvariable=self.file_var, text_color=TEXT, font=(FONT, 13, "bold"), anchor="w").pack(anchor="w", pady=(2, 2))
        ctk.CTkLabel(pleft, textvariable=self.status_var, text_color=MUTED, font=(FONT, 10), anchor="w").pack(anchor="w")
        btns = ctk.CTkFrame(picker, fg_color="transparent"); btns.pack(side="right", padx=16, pady=14)
        ctk.CTkButton(btns, text="영상 선택", command=self.select_video, width=110, height=42, corner_radius=14, fg_color=PANEL2, hover_color="#2a3442", border_width=1, border_color=BORDER, font=(FONT, 11, "bold")).pack(side="left", padx=(0, 8))
        self.analyze_btn = ctk.CTkButton(btns, text="분석 시작", command=self.start_analysis, width=120, height=42, corner_radius=14, fg_color=ACCENT, hover_color=ACCENT_HOVER, font=(FONT, 11, "bold")); self.analyze_btn.pack(side="left")

        progress_wrap = ctk.CTkFrame(root, fg_color="transparent"); progress_wrap.pack(fill="x", pady=(0, 12))
        self.progress = ctk.CTkProgressBar(progress_wrap, height=10, corner_radius=8, progress_color=ACCENT, fg_color=PANEL2); self.progress.set(0); self.progress.pack(fill="x")
        ctk.CTkLabel(progress_wrap, textvariable=self.progress_text, text_color=MUTED, font=(FONT, 9)).pack(anchor="w", pady=(4, 0))

        metrics = ctk.CTkFrame(root, fg_color="transparent"); metrics.pack(fill="x", pady=(0, 12))
        defs = [("예상 티어", "tier", 1), ("에임", "aim", 1), ("헤드라인", "headline", 1), ("무빙 안정성", "movement", 1), ("스킬", "skill", 1), ("내 K/D", "kd", 0)]
        for i, (label, key, red) in enumerate(defs):
            metrics.grid_columnconfigure(i, weight=1)
            card = ctk.CTkFrame(metrics, fg_color=PANEL, border_color=BORDER, border_width=1, corner_radius=18); card.grid(row=0, column=i, sticky="nsew", padx=(0 if i == 0 else 5, 0 if i == len(defs)-1 else 5))
            ctk.CTkLabel(card, text=label, text_color=MUTED, font=(FONT, 10, "bold")).pack(anchor="w", padx=14, pady=(11, 0))
            ctk.CTkLabel(card, textvariable=self.metric_vars[key], text_color=ACCENT if red else TEXT, font=(FONT, 21, "bold")).pack(anchor="w", padx=14, pady=(1, 11))

        body = ctk.CTkFrame(root, fg_color="transparent"); body.pack(fill="both", expand=True)
        body.grid_columnconfigure(0, weight=3); body.grid_columnconfigure(1, weight=2); body.grid_rowconfigure(0, weight=1)
        review = ctk.CTkFrame(body, fg_color=PANEL, border_color=BORDER, border_width=1, corner_radius=22); review.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
        chat = ctk.CTkFrame(body, fg_color=PANEL, border_color=BORDER, border_width=1, corner_radius=22); chat.grid(row=0, column=1, sticky="nsew", padx=(6, 0))

        self.review_tabs = ctk.CTkTabview(review, fg_color=PANEL, corner_radius=18, border_width=0, segmented_button_selected_color=ACCENT, segmented_button_selected_hover_color=ACCENT_HOVER)
        self.review_tabs.pack(fill="both", expand=True, padx=10, pady=10)
        video_tab = self.review_tabs.add("영상 · 이벤트"); feedback_tab = self.review_tabs.add("피드백"); self.review_tabs.set("영상 · 이벤트")
        video_tab.grid_columnconfigure(0, weight=4); video_tab.grid_columnconfigure(1, weight=1); video_tab.grid_rowconfigure(0, weight=1)
        player_card = ctk.CTkFrame(video_tab, fg_color="#090c11", corner_radius=16); player_card.grid(row=0, column=0, sticky="nsew", padx=(4, 6), pady=4)
        event_card = ctk.CTkFrame(video_tab, fg_color="#0d1118", corner_radius=16); event_card.grid(row=0, column=1, sticky="nsew", padx=(6, 4), pady=4)

        self.video_label = tk.Label(player_card, text="영상을 선택하세요", bg="#05070a", fg=MUTED, font=(FONT, 12, "bold"), bd=0); self.video_label.pack(fill="both", expand=True, padx=8, pady=(8, 4))
        self.timeline = ctk.CTkSlider(player_card, from_=0, to=1, number_of_steps=1000, command=self._on_slider, button_color=ACCENT, button_hover_color=ACCENT_HOVER, progress_color=ACCENT, fg_color="#2a3340", height=18); self.timeline.set(0); self.timeline.pack(fill="x", padx=12, pady=(5, 3))
        controls = ctk.CTkFrame(player_card, fg_color="transparent"); controls.pack(fill="x", padx=10, pady=(2, 10))
        ctk.CTkButton(controls, text="-5초", command=lambda: self.seek_relative(-5), width=62, height=34, corner_radius=11, fg_color=PANEL2, hover_color="#28313d").pack(side="left")
        self.play_btn = ctk.CTkButton(controls, text="▶ 재생", command=self.toggle_playback, width=82, height=34, corner_radius=11, fg_color=ACCENT, hover_color=ACCENT_HOVER, font=(FONT, 10, "bold")); self.play_btn.pack(side="left", padx=6)
        ctk.CTkButton(controls, text="+5초", command=lambda: self.seek_relative(5), width=62, height=34, corner_radius=11, fg_color=PANEL2, hover_color="#28313d").pack(side="left")
        self.time_var = tk.StringVar(value="00:00.0 / 00:00.0"); ctk.CTkLabel(controls, textvariable=self.time_var, text_color=MUTED, font=(FONT, 10, "bold")).pack(side="right", padx=4)

        ctk.CTkLabel(event_card, text="EVENT JUMP", text_color=ACCENT, font=(FONT, 10, "bold")).pack(anchor="w", padx=12, pady=(12, 0))
        ctk.CTkLabel(event_card, text="감지된 시점", text_color=TEXT, font=(FONT, 15, "bold")).pack(anchor="w", padx=12, pady=(1, 6))
        self.event_list = ctk.CTkScrollableFrame(event_card, fg_color="transparent", corner_radius=0); self.event_list.pack(fill="both", expand=True, padx=7, pady=(0, 8))
        ctk.CTkLabel(self.event_list, text="분석 후 킬·데스·스킬·사격·에임 시점이 표시됩니다.", text_color=MUTED, font=(FONT, 9), wraplength=170, justify="left").pack(anchor="w", padx=5, pady=6)

        ctk.CTkLabel(feedback_tab, text="분석 피드백", text_color=TEXT, font=(FONT, 17, "bold")).pack(anchor="w", padx=10, pady=(8, 6))
        shell = ctk.CTkFrame(feedback_tab, fg_color="#0d1118", corner_radius=16); shell.pack(fill="both", expand=True, padx=5, pady=(0, 5))
        self.feedback = tk.Text(shell, wrap="word", state="disabled", bg="#0d1118", fg=TEXT, insertbackground=TEXT, selectbackground="#384252", relief="flat", bd=0, highlightthickness=0, padx=14, pady=14, font=(FONT, 11), spacing1=3, spacing3=7); self.feedback.pack(fill="both", expand=True, padx=4, pady=4)
        self.feedback.tag_configure("heading", foreground=ACCENT, font=(FONT, 13, "bold")); self.feedback.tag_configure("score", foreground=ACCENT, font=(FONT, 13, "bold")); self.feedback.tag_configure("muted", foreground=MUTED, font=(FONT, 10)); self.feedback.tag_configure("good", foreground=GOOD, font=(FONT, 11, "bold"))
        self._set_feedback("영상 분석 준비 완료\n\n에임은 발사 시점을 우선 사용하고, 부족하면 지속 적 후보 장면으로 보완합니다.")

        ctk.CTkLabel(chat, text="AI COACH", text_color=ACCENT, font=(FONT, 10, "bold")).pack(anchor="w", padx=14, pady=(13, 0))
        ctk.CTkLabel(chat, text="영상 분석 결과 질문", text_color=TEXT, font=(FONT, 17, "bold")).pack(anchor="w", padx=14, pady=(1, 8))
        chat_shell = ctk.CTkFrame(chat, fg_color="#0d1118", corner_radius=16); chat_shell.pack(fill="both", expand=True, padx=12, pady=(0, 8))
        self.chat_box = tk.Text(chat_shell, wrap="word", state="disabled", bg="#0d1118", fg=TEXT, insertbackground=TEXT, relief="flat", bd=0, highlightthickness=0, padx=12, pady=12, font=(FONT, 10), spacing3=6); self.chat_box.pack(fill="both", expand=True, padx=4, pady=4); self._append_chat("AI · 분석 후 이벤트 시점과 에임 결과를 함께 복기할 수 있습니다.\n\n")
        row = ctk.CTkFrame(chat, fg_color="transparent"); row.pack(fill="x", padx=12, pady=(0, 12))
        self.chat_entry = ctk.CTkEntry(row, placeholder_text="분석 결과에 대해 질문하세요", height=42, corner_radius=13, font=(FONT, 11)); self.chat_entry.pack(side="left", fill="x", expand=True); self.chat_entry.bind("<Return>", lambda _e: self.send_chat())
        self.chat_send = ctk.CTkButton(row, text="전송", command=self.send_chat, width=76, height=42, corner_radius=13, fg_color=ACCENT, hover_color=ACCENT_HOVER, font=(FONT, 11, "bold")); self.chat_send.pack(side="left", padx=(8, 0))

    def select_video(self):
        path = filedialog.askopenfilename(title="VALORANT 영상 선택", filetypes=[("Video", "*.mp4 *.mov *.avi *.mkv *.webm"), ("All files", "*.*")])
        if not path: return
        self.selected_video = Path(path); self.file_var.set(self.selected_video.name); self.status_var.set("영상 선택 완료 · 재생/분석 가능"); self._open_player(self.selected_video)

    def _open_player(self, path):
        self._stop_playback()
        if self._player_cap is not None: self._player_cap.release()
        self._player_cap = cv2.VideoCapture(str(path))
        if not self._player_cap.isOpened(): self._player_cap = None; messagebox.showerror("영상 열기 실패", "선택한 영상을 열 수 없습니다."); return
        self._player_path = path; self._player_fps = max(1.0, float(self._player_cap.get(cv2.CAP_PROP_FPS) or 30.0)); frames = int(self._player_cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        self._player_duration = frames / self._player_fps if frames > 0 else 0.0; self._player_step = max(1, int(round(self._player_fps / 30.0)))
        self.timeline.configure(to=max(0.1, self._player_duration), number_of_steps=max(100, int(self._player_duration * 10))); self.seek_to(0.0)

    def _render_player_frame(self, frame):
        if frame is None or frame.size == 0: return
        image = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)); image.thumbnail((max(320, self.video_label.winfo_width()-12), max(220, self.video_label.winfo_height()-12)))
        self._player_photo = ImageTk.PhotoImage(image); self.video_label.configure(image=self._player_photo, text="")

    def _update_time_ui(self):
        self._slider_internal = True
        try: self.timeline.set(max(0.0, min(self._player_duration, self._player_current)))
        finally: self._slider_internal = False
        self.time_var.set(f"{self._fmt_time(self._player_current)} / {self._fmt_time(self._player_duration)}")

    @staticmethod
    def _fmt_time(seconds):
        seconds = max(0.0, float(seconds or 0.0)); minutes = int(seconds // 60); sec = seconds - minutes * 60
        return f"{minutes:02d}:{sec:04.1f}"

    def _on_slider(self, value):
        if not self._slider_internal and self._player_cap is not None: self.seek_to(float(value))

    def seek_to(self, seconds):
        if self._player_cap is None: return
        self._stop_playback(); t = max(0.0, min(float(seconds), max(0.0, self._player_duration))); self._player_cap.set(cv2.CAP_PROP_POS_MSEC, t*1000.0)
        ok, frame = self._player_cap.read()
        if ok:
            actual = float(self._player_cap.get(cv2.CAP_PROP_POS_MSEC) or t*1000.0)/1000.0; self._player_current = max(0.0, actual if actual > 0 else t); self._render_player_frame(frame)
        else: self._player_current = t
        self._update_time_ui()

    def seek_relative(self, delta): self.seek_to(self._player_current + float(delta))

    def toggle_playback(self):
        if self._player_cap is None:
            if self.selected_video is not None: self._open_player(self.selected_video)
            return
        if self._player_playing: self._stop_playback(); return
        if self._player_current >= max(0.0, self._player_duration - 0.05): self.seek_to(0.0)
        self._player_playing = True; self.play_btn.configure(text="Ⅱ 일시정지"); self._player_tick()

    def _stop_playback(self):
        self._player_playing = False
        if hasattr(self, "play_btn"): self.play_btn.configure(text="▶ 재생")
        if self._player_after is not None:
            try: self.after_cancel(self._player_after)
            except Exception: pass
            self._player_after = None

    def _player_tick(self):
        if not self._player_playing or self._player_cap is None: return
        ok, frame = self._player_cap.read()
        if not ok: self._stop_playback(); return
        for _ in range(max(0, self._player_step-1)): self._player_cap.grab()
        pos_ms = float(self._player_cap.get(cv2.CAP_PROP_POS_MSEC) or 0.0); self._player_current = pos_ms/1000.0 if pos_ms > 0 else self._player_current + self._player_step/self._player_fps
        self._render_player_frame(frame); self._update_time_ui()
        if self._player_current >= self._player_duration: self._stop_playback(); return
        display_fps = min(30.0, self._player_fps/max(1, self._player_step)); self._player_after = self.after(max(15, int(1000.0/max(1.0, display_fps))), self._player_tick)

    def _deliver_report(self, report):
        super()._deliver_report(report); self._populate_events(report); self.review_tabs.set("영상 · 이벤트")

    def _populate_events(self, report):
        for child in self.event_list.winfo_children(): child.destroy()
        events = report.get("timeline_events") or []
        if not events: ctk.CTkLabel(self.event_list, text="감지된 이벤트가 없습니다.", text_color=MUTED, font=(FONT, 9)).pack(anchor="w", padx=5, pady=6); return
        colors = {"kill": ACCENT, "death": "#8d3540", "skill": "#465dff", "shot": "#2d7f68", "aim": "#a44cff"}
        for item in events:
            t = float(item.get("video_seconds") or 0.0); kind = str(item.get("kind") or "event"); label = str(item.get("label") or kind)
            ctk.CTkButton(self.event_list, text=f"{self._fmt_time(t)}   {label}", command=lambda sec=t: self._jump_event(sec), anchor="w", height=34, corner_radius=10, fg_color=colors.get(kind, PANEL2), hover_color="#354052", text_color="white", font=(FONT, 9, "bold")).pack(fill="x", padx=3, pady=3)

    def _jump_event(self, seconds):
        self.review_tabs.set("영상 · 이벤트"); self.seek_to(max(0.0, float(seconds)-0.7))

    def _render_feedback(self, report):
        super()._render_feedback(report)
        if report.get("aim_score") is None: return
        method_map = {"shot_synchronized": "실제 발사 시점", "shot_plus_kill_anchor": "발사 + 킬 시점", "shot_plus_persistent_fallback": "발사 + 지속 적 후보", "persistent_target_fallback": "지속 적 후보 장면"}
        method = method_map.get(str(report.get("aim_method")), str(report.get("aim_method") or "unknown")); conf = float(report.get("aim_confidence") or 0.0)*100.0
        self.feedback.configure(state="normal"); self.feedback.insert("2.0", f"에임 판정 방식: {method} · 근거 {report.get('aim_evaluated_events', 0)}개 · 신뢰도 {conf:.0f}%\n", "muted"); self.feedback.configure(state="disabled")

    def _on_close(self):
        self._stop_playback()
        if self._player_cap is not None: self._player_cap.release(); self._player_cap = None
        self.destroy()

if __name__ == "__main__":
    App().mainloop()
