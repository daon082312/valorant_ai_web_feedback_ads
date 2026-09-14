from __future__ import annotations

import queue
import threading
import time
import tkinter as tk
from tkinter import messagebox

import customtkinter as ctk

from app_v5_fixed import App as V5App
from fight_recorder import FightRecorder
from input_tracker import KeyEvent, ShotEvent, SkillEvent, normalize_binding
from main import ACCENT, BG, BORDER, MUTED, PANEL, PANEL2, TEXT, WORK_DIR, save_config
from screen_capture import FrameSample
from skill_usage_detector import SkillUsageDetector
from shot_detector import AmmoShotDetector


ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("dark-blue")

V6_DEFAULTS = {
    "preview_fps": 5,
    "record_buffer_fps": 6,
    "record_width": 720,
    "fight_record_jpeg_quality": 58,
    "vision_analysis_fps": 3,
    "save_training_frames": False,
    "skill_hud_use_detection": True,
    "skill_hud_detection_fps": 8,
    "skill_hud_change_threshold": 8.0,
    "skill_hud_confirm_window_seconds": 1.15,
    "skill_hud_event_cooldown_seconds": 1.0,
    "shot_hud_detection": True,
    "shot_hud_detection_fps": 12,
    "shot_hud_change_threshold": 0.55,
    "shot_hud_max_change": 18.0,
    "shot_hud_event_cooldown_seconds": 0.055,
    "shot_hud_candidate_window_seconds": 0.45,
    "ammo_hud_roi": {"x": 0.865, "y": 0.835, "w": 0.115, "h": 0.125},
}

TEXTS = {
    "ko": {
        "eyebrow": "LOCAL · POST-FIGHT AI COACHING",
        "subtitle": "로컬에서 화면·무빙·교전·탄약 HUD 사격·HUD 스킬 사용을 분석합니다. 무거운 작업은 백그라운드에서 처리합니다.",
        "start": "세션 시작", "stop": "세션 종료", "chat": "AI 채팅", "minimap": "미니맵",
        "loss": "패배 원인", "clips": "교전 클립", "reference": "기준 학습", "settings": "설정",
        "screen": "화면 미리보기", "review": "교전 후 피드백", "shots": "실제 발사",
        "moving": "이동사격", "shift": "SHIFT 사격", "ctrl": "CTRL 사격", "skill": "스킬 사용",
        "idle": "대기 중 · 로컬 전용",
    },
    "en": {
        "eyebrow": "LOCAL · POST-FIGHT AI COACHING",
        "subtitle": "Local screen, movement, fight, ammo-HUD shot and HUD-confirmed utility analysis. Heavy work runs in the background.",
        "start": "Start Session", "stop": "Stop Session", "chat": "AI Chat", "minimap": "Minimap",
        "loss": "Loss Analysis", "clips": "Fight Clips", "reference": "Reference Learn", "settings": "Settings",
        "screen": "Screen Preview", "review": "Post-fight Feedback", "shots": "Confirmed Shots",
        "moving": "Moving Shots", "shift": "SHIFT Shots", "ctrl": "CTRL Shots", "skill": "Utility Uses",
        "idle": "Idle · local only",
    },
}


class App(V5App):
    """Rounded, unified-settings, performance-safe v6 application."""

    def __init__(self):
        super().__init__()
        changed = False
        for key, value in V6_DEFAULTS.items():
            if key not in self.config_data:
                self.config_data[key] = value
                changed = True
        if changed:
            save_config(self.config_data)

        self.event_queue = queue.Queue(maxsize=128)
        self.skill_detector = SkillUsageDetector(self.config_data)
        self.shot_detector = AmmoShotDetector(self.config_data)
        self.recorder = FightRecorder(
            WORK_DIR,
            fps=int(self.config_data.get("capture_fps", 12)),
            buffer_seconds=float(self.config_data.get("fight_record_buffer_seconds", 30.0)),
            jpeg_quality=int(self.config_data.get("fight_record_jpeg_quality", 58)),
            buffer_fps=int(self.config_data.get("record_buffer_fps", 6)),
            record_width=int(self.config_data.get("record_width", 720)),
        )
        self._preview_lock = threading.Lock()
        self._preview_frame = None
        self._preview_timestamp = 0.0
        self._preview_rendered_timestamp = 0.0
        self._last_preview_submit = 0.0
        self._vision_queue: queue.Queue[tuple[float, object]] = queue.Queue(maxsize=1)
        self._vision_last_submit = 0.0
        self._vision_start_after = 0.0
        threading.Thread(target=self._vision_worker, daemon=True, name="VisionLatestFrame").start()
        self._settings_window = None
        self.after(90, self._preview_tick)
        self.after(250, self._apply_language)

    def _setup_styles(self) -> None:
        super()._setup_styles()

    def _apply_font_refresh(self) -> None:
        return

    def _build_ui(self) -> None:
        self.title("VALORANT Local Coach")
        self.geometry("1320x880")
        self.minsize(1080, 720)
        self.configure(bg=BG)

        self._compat = tk.Frame(self, bg=BG)
        self.start_btn = tk.Button(self._compat)
        self.stop_btn = tk.Button(self._compat)
        self.shots_var = tk.StringVar(value="0")
        self.move_var = tk.StringVar(value="0")
        self.walk_var = tk.StringVar(value="0")
        self.crouch_var = tk.StringVar(value="0")
        self.skill_var = tk.StringVar(value="0")
        self.keys_var = tk.StringVar(value="INPUT  -")
        self.status_var = tk.StringVar(value="대기 중 · 로컬 전용")
        self.bindings_var = tk.StringVar(value=self._bindings_text())
        self.profile_var = tk.StringVar(value=self._profile_label())

        root = ctk.CTkFrame(self, fg_color=BG, corner_radius=0)
        root.pack(fill="both", expand=True, padx=22, pady=18)
        header = ctk.CTkFrame(root, fg_color="transparent")
        header.pack(fill="x", pady=(0, 12))
        self.v6_eyebrow = ctk.CTkLabel(header, text="LOCAL · POST-FIGHT AI COACHING", text_color=ACCENT, font=("Segoe UI", 12, "bold"))
        self.v6_eyebrow.pack(anchor="w")
        self.v6_title = ctk.CTkLabel(header, text="VALORANT Local Coach", text_color=TEXT, font=("Segoe UI", 31, "bold"))
        self.v6_title.pack(anchor="w", pady=(1, 0))
        self.v6_subtitle = ctk.CTkLabel(header, text="로컬에서 화면·무빙·교전·탄약 HUD 사격·HUD 스킬 사용을 분석합니다.", text_color=MUTED, font=("Segoe UI", 13), anchor="w")
        self.v6_subtitle.pack(anchor="w", pady=(0, 4))

        toolbar = ctk.CTkFrame(root, fg_color=PANEL, border_color=BORDER, border_width=1, corner_radius=20)
        toolbar.pack(fill="x", pady=(0, 12), ipady=4)
        left = ctk.CTkFrame(toolbar, fg_color="transparent"); left.pack(side="left", padx=12, pady=10)
        right = ctk.CTkFrame(toolbar, fg_color="transparent"); right.pack(side="right", padx=12, pady=10)
        self.round_start_btn = self._round_button(left, "세션 시작", self.start_session, primary=True, width=112); self.round_start_btn.pack(side="left", padx=(0, 6))
        self.round_stop_btn = self._round_button(left, "세션 종료", self.stop_session, width=112); self.round_stop_btn.configure(state="disabled"); self.round_stop_btn.pack(side="left", padx=6)
        self.round_chat_btn = self._round_button(left, "AI 채팅", self.open_chat_window, width=94); self.round_chat_btn.pack(side="left", padx=(12, 5))
        self.round_minimap_btn = self._round_button(left, "미니맵", self.open_minimap_window, width=90); self.round_minimap_btn.pack(side="left", padx=5)
        self.round_loss_btn = self._round_button(left, "패배 원인", self.open_loss_analysis, width=96); self.round_loss_btn.pack(side="left", padx=5)
        self.round_clips_btn = self._round_button(left, "교전 클립", self.open_clip_folder, width=96); self.round_clips_btn.pack(side="left", padx=5)
        self.round_reference_btn = self._round_button(left, "기준 학습", self.learn_reference_profile, width=92); self.round_reference_btn.pack(side="left", padx=5)
        self.round_settings_btn = self._round_button(right, "설정", self.open_settings, width=88); self.round_settings_btn.pack(side="right")
        self.status_pill = ctk.CTkLabel(right, textvariable=self.status_var, fg_color=PANEL2, text_color=TEXT, corner_radius=12, padx=12, pady=7, font=("Segoe UI", 11, "bold")); self.status_pill.pack(side="right", padx=(0, 8))

        info = ctk.CTkFrame(root, fg_color="transparent"); info.pack(fill="x", pady=(0, 10))
        ctk.CTkLabel(info, textvariable=self.bindings_var, text_color=MUTED, font=("Segoe UI", 11), anchor="w").pack(side="left")
        ctk.CTkLabel(info, textvariable=self.profile_var, text_color=MUTED, font=("Segoe UI", 11), anchor="e").pack(side="right")

        metrics = ctk.CTkFrame(root, fg_color="transparent"); metrics.pack(fill="x", pady=(0, 12))
        metric_defs = [("shots", self.shots_var), ("moving", self.move_var), ("shift", self.walk_var), ("ctrl", self.crouch_var), ("skill", self.skill_var)]
        self.metric_labels = {}
        for idx, (key, variable) in enumerate(metric_defs):
            metrics.grid_columnconfigure(idx, weight=1)
            card = ctk.CTkFrame(metrics, fg_color=PANEL, border_color=BORDER, border_width=1, corner_radius=18)
            card.grid(row=0, column=idx, sticky="nsew", padx=(0 if idx == 0 else 5, 0 if idx == 4 else 5))
            label = ctk.CTkLabel(card, text=key, text_color=MUTED, font=("Segoe UI", 11, "bold")); label.pack(anchor="w", padx=14, pady=(10, 0)); self.metric_labels[key] = label
            ctk.CTkLabel(card, textvariable=variable, text_color=TEXT, font=("Segoe UI", 22, "bold")).pack(anchor="w", padx=14, pady=(0, 10))

        body = ctk.CTkFrame(root, fg_color="transparent"); body.pack(fill="both", expand=True); body.grid_columnconfigure(0, weight=3); body.grid_columnconfigure(1, weight=2); body.grid_rowconfigure(0, weight=1)
        preview_card = ctk.CTkFrame(body, fg_color=PANEL, border_color=BORDER, border_width=1, corner_radius=22); preview_card.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
        feedback_card = ctk.CTkFrame(body, fg_color=PANEL, border_color=BORDER, border_width=1, corner_radius=22); feedback_card.grid(row=0, column=1, sticky="nsew", padx=(6, 0))
        preview_head = ctk.CTkFrame(preview_card, fg_color="transparent"); preview_head.pack(fill="x", padx=14, pady=(12, 8))
        self.screen_title = ctk.CTkLabel(preview_head, text="화면 미리보기", text_color=TEXT, font=("Segoe UI", 16, "bold")); self.screen_title.pack(side="left")
        ctk.CTkLabel(preview_head, textvariable=self.keys_var, text_color=MUTED, font=("Segoe UI", 11, "bold")).pack(side="right")
        preview_wrap = ctk.CTkFrame(preview_card, fg_color="#080a0e", corner_radius=16); preview_wrap.pack(fill="both", expand=True, padx=12, pady=(0, 12))
        self.preview = tk.Label(preview_wrap, text="세션을 시작하면 화면이 표시됩니다.", bg="#080a0e", fg=MUTED, bd=0, font=("Segoe UI", 10)); self.preview.pack(fill="both", expand=True, padx=8, pady=8)
        self.review_title = ctk.CTkLabel(feedback_card, text="교전 후 피드백", text_color=TEXT, font=("Segoe UI", 16, "bold")); self.review_title.pack(anchor="w", padx=14, pady=(12, 8))
        feedback_wrap = ctk.CTkFrame(feedback_card, fg_color="#11151d", corner_radius=16); feedback_wrap.pack(fill="both", expand=True, padx=12, pady=(0, 12))
        self.feedback = tk.Text(feedback_wrap, wrap="word", state="disabled", bg="#11151d", fg=TEXT, insertbackground=TEXT, selectbackground="#394151", relief="flat", padx=12, pady=12, font=("Segoe UI", 10), spacing1=2, spacing3=5, bd=0, highlightthickness=0)
        self.feedback.pack(fill="both", expand=True, padx=4, pady=4); self.feedback.tag_configure("heading", foreground=ACCENT, font=("Segoe UI", 11, "bold")); self.feedback.tag_configure("good", foreground="#59d08a"); self.feedback.tag_configure("muted", foreground=MUTED)
        self._append_feedback("준비 완료 · 탄약 HUD 사격 + HUD 스킬 사용 감지\n", "heading")

    def _round_button(self, parent, text, command, *, primary=False, width=100):
        return ctk.CTkButton(parent, text=text, command=command, width=width, height=36, corner_radius=14, border_width=1, fg_color=ACCENT if primary else PANEL2, hover_color="#ff5b68" if primary else "#2a3341", border_color="#ff6b76" if primary else BORDER, text_color="white", font=("Segoe UI", 12, "bold"))

    def _install_ai_button(self) -> None: return
    def _install_v5_controls(self) -> None: return

    def _apply_language(self) -> None:
        lang = "en" if str(self.config_data.get("language", "ko")) == "en" else "ko"; t = TEXTS[lang]
        try:
            self.v6_eyebrow.configure(text=t["eyebrow"]); self.v6_subtitle.configure(text=t["subtitle"])
            self.round_start_btn.configure(text=t["start"]); self.round_stop_btn.configure(text=t["stop"]); self.round_chat_btn.configure(text=t["chat"]); self.round_minimap_btn.configure(text=t["minimap"]); self.round_loss_btn.configure(text=t["loss"]); self.round_clips_btn.configure(text=t["clips"]); self.round_reference_btn.configure(text=t["reference"]); self.round_settings_btn.configure(text=t["settings"])
            self.screen_title.configure(text=t["screen"]); self.review_title.configure(text=t["review"])
            for key in ("shots", "moving", "shift", "ctrl", "skill"): self.metric_labels[key].configure(text=t[key])
            if not self.session_running: self.status_var.set(t["idle"])
        except Exception: pass

    def _safe_event(self, item) -> None:
        try: self.event_queue.put_nowait(item)
        except queue.Full:
            try: self.event_queue.get_nowait()
            except queue.Empty: pass
            try: self.event_queue.put_nowait(item)
            except queue.Full: pass

    def _thread_shot(self, event: ShotEvent) -> None:
        if self.config_data.get("shot_hud_detection", True):
            self.shot_detector.register_candidate(event.timestamp); return
        self.engine.on_shot(event); self._safe_event(("shot", event))

    def _thread_key(self, event: KeyEvent) -> None:
        self.engine.on_key_event(event); self._safe_event(("key", event.keys))

    def _thread_skill(self, event: SkillEvent) -> None:
        if hasattr(self, "skill_detector"): self.skill_detector.register_candidate(event)

    def _thread_frame(self, sample: FrameSample) -> None:
        self.engine.on_frame(sample)
        if self.config_data.get("shot_hud_detection", True):
            try:
                for confirmed in self.shot_detector.process(sample.frame_bgr, sample.timestamp, firing_held=self.input_tracker.is_firing()):
                    event = self.input_tracker.snapshot_shot_event(confirmed.timestamp, source="ammo_hud_confirmed", confidence=confirmed.confidence)
                    self.engine.on_shot(event); self._safe_event(("shot", event))
            except Exception as exc: print(f"[ShotHUD] detect failed: {type(exc).__name__}: {exc}")
        if self.config_data.get("record_fight_clips", True): self.recorder.on_frame(sample)
        if hasattr(self, "skill_detector"):
            for event in self.skill_detector.process(sample.frame_bgr, sample.timestamp): self.engine.on_skill(event); self._safe_event(("skill", event))
        preview_fps = max(2.0, min(12.0, float(self.config_data.get("preview_fps", 5))))
        if sample.timestamp - self._last_preview_submit >= 1.0 / preview_fps:
            self._last_preview_submit = sample.timestamp
            with self._preview_lock: self._preview_frame = sample.frame_bgr.copy(); self._preview_timestamp = sample.timestamp
        vision_fps = max(1.0, min(8.0, float(self.config_data.get("vision_analysis_fps", 3))))
        if sample.timestamp >= self._vision_start_after and sample.timestamp - self._vision_last_submit >= 1.0 / vision_fps:
            self._vision_last_submit = sample.timestamp; item = (sample.timestamp, sample.frame_bgr.copy())
            try: self._vision_queue.put_nowait(item)
            except queue.Full:
                try: self._vision_queue.get_nowait()
                except queue.Empty: pass
                try: self._vision_queue.put_nowait(item)
                except queue.Full: pass

    def _vision_worker(self) -> None:
        while True:
            ts, frame = self._vision_queue.get()
            try:
                result = self.vision.analyze(frame, ts)
                if result is not None: self._latest_vision = result
            except Exception as exc: print(f"[Vision] worker failed: {type(exc).__name__}: {exc}")

    def _preview_tick(self) -> None:
        try:
            frame = None; ts = 0.0
            with self._preview_lock:
                if self._preview_timestamp > self._preview_rendered_timestamp and self._preview_frame is not None: frame = self._preview_frame.copy(); ts = self._preview_timestamp
            if frame is not None:
                if self.config_data.get("show_vision_boxes_in_preview", True):
                    annotated = self.vision.latest_annotated()
                    if annotated is not None: frame = annotated
                self._show_frame(frame); self._preview_rendered_timestamp = ts
        finally: self.after(120, self._preview_tick)

    def _drain_events(self) -> None:
        processed = 0
        while processed < 24:
            try: kind, payload = self.event_queue.get_nowait()
            except queue.Empty: break
            processed += 1
            if kind == "shot":
                self.shot_count += 1
                if payload.moving: self.moving_shot_count += 1
                if payload.walking: self.walk_shot_count += 1
                if payload.crouching: self.crouch_shot_count += 1
                self.shots_var.set(str(self.shot_count)); self.move_var.set(str(self.moving_shot_count)); self.walk_var.set(str(self.walk_shot_count)); self.crouch_var.set(str(self.crouch_shot_count))
            elif kind == "skill": self.skill_count += 1; self.skill_var.set(str(self.skill_count))
            elif kind == "key":
                visible = [str(x).upper() for x in payload if x in {"w", "a", "s", "d", "shift", "ctrl", "mouse4", "mouse5"}]; self.keys_var.set("INPUT  " + (" + ".join(visible) if visible else "-"))
            elif kind == "clip_saved":
                video_path, metadata_path = payload
                if video_path: self._append_feedback(f"교전 클립 저장 · {video_path.name}\n", "muted")
                elif metadata_path: self._append_feedback(f"교전 메타데이터 저장 · {metadata_path.name}\n", "muted")
        self.after(45, self._drain_events)

    def start_session(self) -> None:
        self.skill_detector.clear(); self.shot_detector.clear(); self._vision_start_after = time.time() + 0.8; self._vision_last_submit = 0.0
        super().start_session(); self.round_start_btn.configure(state="disabled", fg_color="#632c34"); self.round_stop_btn.configure(state="normal")
        self.status_var.set("Analyzing · optimized local mode" if self.config_data.get("language") == "en" else "분석 중 · 최적화 로컬 모드")

    def stop_session(self) -> None:
        super().stop_session(); self.round_start_btn.configure(state="normal", fg_color=ACCENT); self.round_stop_btn.configure(state="disabled")
        self.status_var.set("Session saved" if self.config_data.get("language") == "en" else "세션 종료 · 저장 완료")

    def open_skill_settings(self) -> None: self.open_settings("skills")
    def open_vision_settings(self) -> None: self.open_settings("vision")
    def open_language_settings(self) -> None: self.open_settings("general")
    def open_ai_settings(self) -> None: self.open_settings("ai")

    def open_settings(self, tab: str = "general") -> None:
        if self._settings_window is not None and self._settings_window.winfo_exists(): self._settings_window.lift(); return
        lang = "en" if self.config_data.get("language") == "en" else "ko"; names = {"general": "General" if lang == "en" else "일반", "skills": "Skills" if lang == "en" else "스킬", "vision": "Vision" if lang == "en" else "비전", "ai": "AI"}
        win = ctk.CTkToplevel(self); self._settings_window = win; win.title("VALORANT Local Coach · Settings"); win.geometry("760x680"); win.minsize(700, 620); win.configure(fg_color=BG); win.transient(self); win.grab_set()
        head = ctk.CTkFrame(win, fg_color="transparent"); head.pack(fill="x", padx=22, pady=(20, 8)); ctk.CTkLabel(head, text="SETTINGS", text_color=ACCENT, font=("Segoe UI", 12, "bold")).pack(anchor="w"); ctk.CTkLabel(head, text="설정" if lang == "ko" else "Settings", text_color=TEXT, font=("Segoe UI", 26, "bold")).pack(anchor="w")
        tabs = ctk.CTkTabview(win, fg_color=PANEL, segmented_button_fg_color=PANEL2, segmented_button_selected_color=ACCENT, segmented_button_selected_hover_color="#ff5b68", corner_radius=20, border_width=1, border_color=BORDER); tabs.pack(fill="both", expand=True, padx=20, pady=(4, 12))
        frames = {key: tabs.add(names[key]) for key in ("general", "skills", "vision", "ai")}; tabs.set(names.get(tab, names["general"]))
        general = frames["general"]
        language_var = tk.StringVar(value=str(self.config_data.get("language", "ko"))); retention_var = tk.StringVar(value=str(self.config_data.get("clip_retention_hours", 24))); auto_delete_var = tk.BooleanVar(value=bool(self.config_data.get("auto_delete_old_videos", True))); preview_var = tk.StringVar(value=str(self.config_data.get("preview_fps", 5))); record_fps_var = tk.StringVar(value=str(self.config_data.get("record_buffer_fps", 6))); record_width_var = tk.StringVar(value=str(self.config_data.get("record_width", 720))); vision_fps_var = tk.StringVar(value=str(self.config_data.get("vision_analysis_fps", 3))); training_var = tk.BooleanVar(value=bool(self.config_data.get("save_training_frames", False))); shot_hud_var = tk.BooleanVar(value=bool(self.config_data.get("shot_hud_detection", True))); shot_threshold_var = tk.StringVar(value=str(self.config_data.get("shot_hud_change_threshold", 0.55)))
        self._settings_row_option(general, "Language / 언어", language_var, ["ko", "en"]); self._settings_row_entry(general, "Video retention hours / 영상 보관시간", retention_var); self._settings_switch(general, "Delete old videos automatically / 오래된 영상 자동 삭제", auto_delete_var); self._settings_row_option(general, "Preview FPS / 미리보기 FPS", preview_var, ["3", "4", "5", "6", "8"]); self._settings_row_option(general, "Recording FPS / 녹화 FPS", record_fps_var, ["4", "5", "6", "8"]); self._settings_row_option(general, "Recording width / 녹화 폭", record_width_var, ["640", "720", "800", "960"]); self._settings_row_option(general, "Vision FPS", vision_fps_var, ["2", "3", "4", "5"]); self._settings_switch(general, "Save training frames / 학습 프레임 저장 (CPU 사용 증가)", training_var); self._settings_switch(general, "Ammo-HUD confirmed shots / 탄약 HUD 실제 발사 감지", shot_hud_var); self._settings_row_entry(general, "Ammo change threshold / 탄약 HUD 변화 임계값", shot_threshold_var)
        skills = frames["skills"]; skill_entries = {}; ctk.CTkLabel(skills, text=("키는 후보 슬롯을 알려줄 뿐입니다. HUD가 실제로 변해야 사용으로 기록됩니다." if lang == "ko" else "Keys only identify a candidate slot. A use is counted only after a HUD state change."), text_color=MUTED, wraplength=620, justify="left", font=("Segoe UI", 12)).pack(anchor="w", padx=16, pady=(16, 10))
        for slot in ("skill1", "skill2", "skill3", "ultimate"):
            item = self.config_data.get("skill_bindings", {}).get(slot, {}); row = ctk.CTkFrame(skills, fg_color=PANEL2, corner_radius=14); row.pack(fill="x", padx=16, pady=5); label_entry = ctk.CTkEntry(row, width=300, corner_radius=10); label_entry.insert(0, str(item.get("label", slot))); label_entry.pack(side="left", padx=10, pady=10, fill="x", expand=True); key_entry = ctk.CTkEntry(row, width=150, corner_radius=10); key_entry.insert(0, str(item.get("key", ""))); key_entry.pack(side="right", padx=10, pady=10); skill_entries[slot] = (label_entry, key_entry)
        hud_detect_var = tk.BooleanVar(value=bool(self.config_data.get("skill_hud_use_detection", True))); hud_threshold_var = tk.StringVar(value=str(self.config_data.get("skill_hud_change_threshold", 8.0))); self._settings_switch(skills, "HUD-confirmed use detection / HUD 실제 사용 확인", hud_detect_var); self._settings_row_entry(skills, "HUD change threshold / HUD 변화 임계값", hud_threshold_var)
        vision = frames["vision"]; enemy_var = tk.StringVar(value=str(self.config_data.get("enemy_outline_color", "red"))); ally_var = tk.StringVar(value=str(self.config_data.get("ally_outline_color", "cyan"))); mm_enemy_var = tk.StringVar(value=str(self.config_data.get("minimap_enemy_color", "red"))); mm_ally_var = tk.StringVar(value=str(self.config_data.get("minimap_ally_color", "cyan"))); boxes_var = tk.BooleanVar(value=bool(self.config_data.get("show_vision_boxes_in_preview", True))); self._settings_row_option(vision, "Enemy outline / 적 윤곽", enemy_var, ["red", "purple", "yellow"]); self._settings_row_option(vision, "Ally color / 아군 색", ally_var, ["cyan", "green"]); self._settings_row_option(vision, "Minimap enemy / 미니맵 적", mm_enemy_var, ["red", "purple", "yellow"]); self._settings_row_option(vision, "Minimap ally / 미니맵 아군", mm_ally_var, ["cyan", "green"]); self._settings_switch(vision, "Show detection boxes in preview / 판독 박스 표시", boxes_var)
        ai = frames["ai"]; ai_enabled_var = tk.BooleanVar(value=bool(self.config_data.get("local_ai_enabled", True))); ai_url_var = tk.StringVar(value=str(self.config_data.get("ollama_url", "http://127.0.0.1:11434"))); ai_model_var = tk.StringVar(value=str(self.config_data.get("ollama_model", "qwen3:4b"))); self._settings_switch(ai, "Use local Ollama AI / 로컬 Ollama AI 사용", ai_enabled_var); self._settings_row_entry(ai, "Ollama URL", ai_url_var); self._settings_row_entry(ai, "Model / 모델", ai_model_var)
        footer = ctk.CTkFrame(win, fg_color="transparent"); footer.pack(fill="x", padx=20, pady=(0, 18))
        def save_all() -> None:
            try: retention=max(1.0,float(retention_var.get())); hud_threshold=max(2.0,float(hud_threshold_var.get())); shot_threshold=max(0.10,float(shot_threshold_var.get())); preview_fps=max(2,int(preview_var.get())); record_fps=max(2,int(record_fps_var.get())); record_width=max(480,int(record_width_var.get())); vision_fps=max(1,int(vision_fps_var.get()))
            except ValueError: messagebox.showerror("Settings", "숫자 설정값을 확인해 주세요. / Check numeric settings.", parent=win); return
            new_bindings={}; used=set()
            for slot,(label_entry,key_entry) in skill_entries.items():
                label=label_entry.get().strip() or slot; key=normalize_binding(key_entry.get())
                if not key: messagebox.showerror("Settings", f"{label}: key is empty", parent=win); return
                if key in used: messagebox.showerror("Settings", f"Duplicate key: {key.upper()}", parent=win); return
                used.add(key); new_bindings[slot]={"label":label,"key":key}
            self.config_data.update({"language":language_var.get(),"clip_retention_hours":retention,"auto_delete_old_videos":bool(auto_delete_var.get()),"preview_fps":preview_fps,"record_buffer_fps":record_fps,"record_width":record_width,"vision_analysis_fps":vision_fps,"save_training_frames":bool(training_var.get()),"shot_hud_detection":bool(shot_hud_var.get()),"shot_hud_change_threshold":shot_threshold,"skill_bindings":new_bindings,"skill_hud_use_detection":bool(hud_detect_var.get()),"skill_hud_change_threshold":hud_threshold,"enemy_outline_color":enemy_var.get(),"ally_outline_color":ally_var.get(),"minimap_enemy_color":mm_enemy_var.get(),"minimap_ally_color":mm_ally_var.get(),"show_vision_boxes_in_preview":bool(boxes_var.get()),"local_ai_enabled":bool(ai_enabled_var.get()),"ollama_url":ai_url_var.get().strip().rstrip("/"),"ollama_model":ai_model_var.get().strip() or "qwen3:4b"})
            save_config(self.config_data); self.input_tracker.set_skill_bindings(new_bindings); self.skill_detector.update_config(self.config_data); self.shot_detector.update_config(self.config_data); self.vision.update_config(self.config_data)
            if hasattr(self,"chat_client"): self.chat_client.update_config(self.config_data)
            self.recorder.buffer_fps=min(self.recorder.fps,record_fps); self.recorder.record_width=record_width; self.bindings_var.set(self._bindings_text()); self._apply_language(); win.destroy(); self._settings_window=None
        ctk.CTkButton(footer,text="Save / 저장",command=save_all,width=120,height=40,corner_radius=14,fg_color=ACCENT,hover_color="#ff5b68",font=("Segoe UI",12,"bold")).pack(side="right"); ctk.CTkButton(footer,text="Cancel / 취소",command=win.destroy,width=110,height=40,corner_radius=14,fg_color=PANEL2,hover_color="#2a3341",font=("Segoe UI",12,"bold")).pack(side="right",padx=(0,8))
        def on_close(): self._settings_window=None; win.destroy()
        win.protocol("WM_DELETE_WINDOW",on_close)

    def _settings_row_entry(self,parent,label,variable):
        row=ctk.CTkFrame(parent,fg_color="transparent"); row.pack(fill="x",padx=16,pady=7); ctk.CTkLabel(row,text=label,text_color=MUTED,width=310,anchor="w",font=("Segoe UI",12,"bold")).pack(side="left"); ctk.CTkEntry(row,textvariable=variable,corner_radius=10,width=260).pack(side="right")
    def _settings_row_option(self,parent,label,variable,values):
        row=ctk.CTkFrame(parent,fg_color="transparent"); row.pack(fill="x",padx=16,pady=7); ctk.CTkLabel(row,text=label,text_color=MUTED,width=310,anchor="w",font=("Segoe UI",12,"bold")).pack(side="left"); ctk.CTkOptionMenu(row,variable=variable,values=values,corner_radius=10,fg_color=PANEL2,button_color="#323b49",button_hover_color="#414c5d",width=180).pack(side="right")
    def _settings_switch(self,parent,label,variable):
        row=ctk.CTkFrame(parent,fg_color="transparent"); row.pack(fill="x",padx=16,pady=7); ctk.CTkLabel(row,text=label,text_color=MUTED,anchor="w",font=("Segoe UI",12,"bold")).pack(side="left"); ctk.CTkSwitch(row,text="",variable=variable,progress_color=ACCENT).pack(side="right")


if __name__ == "__main__":
    App().mainloop()
