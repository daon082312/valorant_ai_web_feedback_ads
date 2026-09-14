from __future__ import annotations

import json
import queue
import sys
import threading
import time
import tkinter as tk
from dataclasses import asdict
from pathlib import Path
from tkinter import messagebox, ttk

from PIL import Image, ImageTk

from coach_engine import CoachEngine, FightFeedback
from fight_recorder import FightRecorder
from input_tracker import InputTracker, KeyEvent, ShotEvent, SkillEvent, normalize_binding
from pro_profile import learn_profile_from_sessions
from screen_capture import FrameSample, ScreenCapture


ROOT_DIR = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
WORK_DIR = Path.cwd() if getattr(sys, "frozen", False) else Path(__file__).resolve().parent
CONFIG_PATH = WORK_DIR / "config.json"
PROFILE_PATH = WORK_DIR / "pro_baseline.json"
REFERENCE_DIR = WORK_DIR / "data" / "reference_sessions"
CLIP_DIR = WORK_DIR / "data" / "fight_clips"

BG = "#0b0e13"
PANEL = "#151922"
PANEL2 = "#1c222d"
BORDER = "#2b323f"
MUTED = "#9ba5b4"
TEXT = "#f3f5f7"
ACCENT = "#ff4655"
ACCENT_HOVER = "#ff5b68"
GOOD = "#59d08a"

DEFAULT_SKILL_BINDINGS = {
    "skill1": {"label": "1번 스킬", "key": "q"},
    "skill2": {"label": "2번 스킬", "key": "e"},
    "skill3": {"label": "3번 스킬", "key": "c"},
    "ultimate": {"label": "궁극기", "key": "x"},
}

DEFAULT_CONFIG = {
    "capture_fps": 12,
    "analysis_width": 960,
    "fight_gap_seconds": 1.8,
    "min_shots_per_fight": 2,
    "moving_shot_warn_ratio": 0.25,
    "walk_shot_warn_ratio": 0.18,
    "crouch_shot_warn_ratio": 0.55,
    "stop_to_shot_too_fast_ms": 35,
    "stop_to_shot_slow_ms": 320,
    "long_burst_shot_count": 6,
    "camera_motion_warn_threshold": 18.0,
    "save_training_frames": True,
    "training_frame_interval_ms": 120,
    "monitor_index": 1,
    "record_fight_clips": True,
    "fight_clip_pre_seconds": 4.0,
    "fight_clip_post_seconds": 1.5,
    "fight_record_buffer_seconds": 30.0,
    "fight_record_jpeg_quality": 70,
    "skill_pre_fight_seconds": 4.0,
    "skill_post_fight_seconds": 1.5,
    "skill_bindings": DEFAULT_SKILL_BINDINGS,
}


def _merged_skill_bindings(value) -> dict:
    merged = {slot: dict(item) for slot, item in DEFAULT_SKILL_BINDINGS.items()}
    if isinstance(value, dict):
        for slot, item in value.items():
            if slot in merged and isinstance(item, dict):
                merged[slot].update(item)
    return merged


def load_config() -> dict:
    if not CONFIG_PATH.exists():
        data = dict(DEFAULT_CONFIG)
        data["skill_bindings"] = _merged_skill_bindings(None)
        CONFIG_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return data
    try:
        raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        data = {**DEFAULT_CONFIG, **raw}
        data["skill_bindings"] = _merged_skill_bindings(raw.get("skill_bindings"))
        return data
    except Exception:
        data = dict(DEFAULT_CONFIG)
        data["skill_bindings"] = _merged_skill_bindings(None)
        return data


def save_config(config: dict) -> None:
    CONFIG_PATH.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("VALORANT Local Coach · Movement & Utility")
        self.geometry("1240x860")
        self.minsize(1000, 720)
        self.configure(bg=BG)

        REFERENCE_DIR.mkdir(parents=True, exist_ok=True)
        CLIP_DIR.mkdir(parents=True, exist_ok=True)
        self.config_data = load_config()
        self.engine = CoachEngine(self.config_data, WORK_DIR)
        self.recorder = FightRecorder(
            WORK_DIR,
            fps=int(self.config_data["capture_fps"]),
            buffer_seconds=float(self.config_data["fight_record_buffer_seconds"]),
            jpeg_quality=int(self.config_data["fight_record_jpeg_quality"]),
        )
        self.event_queue: queue.Queue = queue.Queue()
        self.input_tracker = InputTracker(
            on_shot=self._thread_shot,
            on_key_event=self._thread_key,
            on_skill=self._thread_skill,
            skill_bindings=self.config_data["skill_bindings"],
        )
        self.capture = ScreenCapture(
            on_frame=self._thread_frame,
            fps=self.config_data["capture_fps"],
            width=self.config_data["analysis_width"],
            monitor_index=self.config_data["monitor_index"],
        )
        self.session_running = False
        self.latest_photo = None
        self.shot_count = 0
        self.moving_shot_count = 0
        self.walk_shot_count = 0
        self.crouch_shot_count = 0
        self.skill_count = 0

        self._setup_styles()
        self._build_ui()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(50, self._drain_events)
        self.after(300, self._poll_fights)

    def _setup_styles(self) -> None:
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure("Root.TFrame", background=BG)
        style.configure("Card.TFrame", background=PANEL, bordercolor=BORDER, relief="solid", borderwidth=1)
        style.configure("Panel2.TFrame", background=PANEL2)
        style.configure("TLabel", background=BG, foreground=TEXT, font=("Segoe UI", 10))
        style.configure("Card.TLabel", background=PANEL, foreground=TEXT)
        style.configure("Muted.Card.TLabel", background=PANEL, foreground=MUTED)
        style.configure("Eyebrow.TLabel", background=BG, foreground=ACCENT, font=("Segoe UI", 9, "bold"))
        style.configure("Eyebrow.Card.TLabel", background=PANEL, foreground=ACCENT, font=("Segoe UI", 9, "bold"))
        style.configure("Title.TLabel", background=BG, foreground=TEXT, font=("Segoe UI", 28, "bold"))
        style.configure("Subtitle.TLabel", background=BG, foreground=MUTED, font=("Segoe UI", 10))
        style.configure("Metric.Card.TLabel", background=PANEL, foreground=TEXT, font=("Segoe UI", 14, "bold"))
        style.configure("MetricName.Card.TLabel", background=PANEL, foreground=MUTED, font=("Segoe UI", 9, "bold"))
        style.configure("Section.Card.TLabel", background=PANEL, foreground=TEXT, font=("Segoe UI", 12, "bold"))
        style.configure("Status.Card.TLabel", background=PANEL2, foreground=TEXT, font=("Segoe UI", 9, "bold"), padding=(10, 6))
        style.configure("TEntry", fieldbackground=PANEL2, foreground=TEXT, insertcolor=TEXT, bordercolor=BORDER)
        style.map("TEntry", fieldbackground=[("focus", PANEL2)], foreground=[("focus", TEXT)])

    def _button(self, parent, text: str, command, primary: bool = False, width: int | None = None):
        button = tk.Button(
            parent,
            text=text,
            command=command,
            bg=ACCENT if primary else PANEL2,
            fg="white",
            activebackground=ACCENT_HOVER if primary else "#252d39",
            activeforeground="white",
            relief="flat",
            bd=0,
            padx=14,
            pady=9,
            font=("Segoe UI", 10, "bold"),
            cursor="hand2",
            disabledforeground="#737b88",
        )
        if width:
            button.configure(width=width)
        return button

    def _card(self, parent, padding: int = 16):
        frame = tk.Frame(parent, bg=PANEL, highlightthickness=1, highlightbackground=BORDER, bd=0)
        inner = tk.Frame(frame, bg=PANEL, padx=padding, pady=padding)
        inner.pack(fill="both", expand=True)
        return frame, inner

    def _build_ui(self) -> None:
        root = ttk.Frame(self, style="Root.TFrame", padding=(24, 20))
        root.pack(fill="both", expand=True)

        ttk.Label(root, text="LOCAL · POST-FIGHT AI COACHING", style="Eyebrow.TLabel").pack(anchor="w")
        ttk.Label(root, text="VALORANT Local Coach", style="Title.TLabel").pack(anchor="w", pady=(4, 2))
        ttk.Label(
            root,
            text="API/Render 없이 화면·WASD·Shift·Ctrl·사격·스킬 키를 로컬 분석하고 교전을 자동 녹화합니다.",
            style="Subtitle.TLabel",
        ).pack(anchor="w", pady=(0, 14))

        control_card, control = self._card(root, 14)
        control_card.pack(fill="x", pady=(0, 12))

        top_row = tk.Frame(control, bg=PANEL)
        top_row.pack(fill="x")
        self.start_btn = self._button(top_row, "세션 시작", self.start_session, primary=True, width=12)
        self.start_btn.pack(side="left")
        self.stop_btn = self._button(top_row, "세션 종료", self.stop_session, width=12)
        self.stop_btn.configure(state="disabled")
        self.stop_btn.pack(side="left", padx=(8, 0))
        self._button(top_row, "스킬 키 설정", self.open_skill_settings).pack(side="left", padx=(16, 0))
        self._button(top_row, "교전 클립", self.open_clip_folder).pack(side="left", padx=(8, 0))
        self._button(top_row, "기준 세션 학습", self.learn_reference_profile).pack(side="left", padx=(8, 0))

        self.status_var = tk.StringVar(value="대기 중 · 로컬 전용")
        ttk.Label(top_row, textvariable=self.status_var, style="Status.Card.TLabel").pack(side="right")

        binding_row = tk.Frame(control, bg=PANEL)
        binding_row.pack(fill="x", pady=(12, 0))
        tk.Label(binding_row, text="스킬 바인딩", bg=PANEL, fg=MUTED, font=("Segoe UI", 9, "bold")).pack(side="left")
        self.bindings_var = tk.StringVar(value=self._bindings_text())
        tk.Label(binding_row, textvariable=self.bindings_var, bg=PANEL, fg=TEXT, font=("Segoe UI", 9)).pack(side="left", padx=(10, 0))
        self.profile_var = tk.StringVar(value=self._profile_label())
        tk.Label(binding_row, textvariable=self.profile_var, bg=PANEL, fg=MUTED, font=("Segoe UI", 9)).pack(side="right")

        metrics = tk.Frame(root, bg=BG)
        metrics.pack(fill="x", pady=(0, 12))
        self.shots_var = tk.StringVar(value="0")
        self.move_var = tk.StringVar(value="0")
        self.walk_var = tk.StringVar(value="0")
        self.crouch_var = tk.StringVar(value="0")
        self.skill_var = tk.StringVar(value="0")
        metric_defs = [
            ("사격", self.shots_var),
            ("이동사격", self.move_var),
            ("SHIFT 사격", self.walk_var),
            ("CTRL 사격", self.crouch_var),
            ("스킬 입력", self.skill_var),
        ]
        for idx, (name, variable) in enumerate(metric_defs):
            card, inner = self._card(metrics, 12)
            card.grid(row=0, column=idx, sticky="nsew", padx=(0 if idx == 0 else 5, 0 if idx == len(metric_defs) - 1 else 5))
            metrics.grid_columnconfigure(idx, weight=1)
            ttk.Label(inner, text=name, style="MetricName.Card.TLabel").pack(anchor="w")
            ttk.Label(inner, textvariable=variable, style="Metric.Card.TLabel").pack(anchor="w", pady=(3, 0))

        body = tk.PanedWindow(root, orient="horizontal", bg=BG, bd=0, sashwidth=8, sashrelief="flat")
        body.pack(fill="both", expand=True)

        preview_card, preview_inner = self._card(body, 14)
        feedback_card, feedback_inner = self._card(body, 14)
        body.add(preview_card, stretch="always", minsize=560)
        body.add(feedback_card, stretch="always", minsize=360)

        preview_head = tk.Frame(preview_inner, bg=PANEL)
        preview_head.pack(fill="x")
        ttk.Label(preview_head, text="LIVE CAPTURE", style="Eyebrow.Card.TLabel").pack(side="left")
        self.keys_var = tk.StringVar(value="INPUT  -")
        tk.Label(preview_head, textvariable=self.keys_var, bg=PANEL, fg=MUTED, font=("Segoe UI", 9, "bold")).pack(side="right")
        tk.Label(preview_inner, text="화면 미리보기", bg=PANEL, fg=TEXT, font=("Segoe UI", 14, "bold")).pack(anchor="w", pady=(5, 10))

        self.preview = tk.Label(
            preview_inner,
            text="세션을 시작하면 선택한 모니터 화면이 표시됩니다.",
            bg="#080a0e",
            fg=MUTED,
            font=("Segoe UI", 10),
            bd=0,
        )
        self.preview.pack(fill="both", expand=True)

        ttk.Label(feedback_inner, text="POST-FIGHT REVIEW", style="Eyebrow.Card.TLabel").pack(anchor="w")
        tk.Label(feedback_inner, text="교전 후 피드백", bg=PANEL, fg=TEXT, font=("Segoe UI", 14, "bold")).pack(anchor="w", pady=(5, 10))
        self.feedback = tk.Text(
            feedback_inner,
            wrap="word",
            state="disabled",
            font=("Segoe UI", 10),
            bg="#11151d",
            fg=TEXT,
            insertbackground=TEXT,
            selectbackground="#394151",
            relief="flat",
            padx=12,
            pady=12,
            spacing1=2,
            spacing3=5,
        )
        self.feedback.pack(fill="both", expand=True)
        self.feedback.tag_configure("heading", foreground=ACCENT, font=("Segoe UI", 11, "bold"))
        self.feedback.tag_configure("good", foreground=GOOD)
        self.feedback.tag_configure("muted", foreground=MUTED)
        self._append_feedback("준비 완료\n", "heading")
        self._append_feedback(
            "세션을 시작하면 교전 전후 영상이 자동 저장되고, 무빙과 등록된 스킬 키 타이밍을 교전 종료 후 평가합니다.\n"
            "스킬의 실제 적중/공간 확보 가치는 아직 키 입력 기반이므로 저장된 클립과 향후 Vision 모델로 보강합니다.\n",
            "muted",
        )

    def _bindings_text(self) -> str:
        parts = []
        for slot in ("skill1", "skill2", "skill3", "ultimate"):
            item = self.config_data.get("skill_bindings", {}).get(slot, {})
            parts.append(f"{item.get('label', slot)}: {str(item.get('key', '-')).upper()}")
        return "   ·   ".join(parts)

    def _profile_label(self) -> str:
        profile = self.engine.pro_profile.data
        source = profile.get("source", "unknown")
        suffix = "학습 기준" if source == "reference_session_learning" else "초기 참고값"
        return f"무빙 비교: {suffix}"

    def _thread_shot(self, event: ShotEvent) -> None:
        self.engine.on_shot(event)
        self.event_queue.put(("shot", event))

    def _thread_key(self, event: KeyEvent) -> None:
        self.engine.on_key_event(event)
        self.event_queue.put(("key", event.keys))

    def _thread_skill(self, event: SkillEvent) -> None:
        self.engine.on_skill(event)
        self.event_queue.put(("skill", event))

    def _thread_frame(self, sample: FrameSample) -> None:
        self.engine.on_frame(sample)
        if self.config_data.get("record_fight_clips", True):
            self.recorder.on_frame(sample)
        if int(sample.timestamp * 4) != int((sample.timestamp - 0.05) * 4):
            self.event_queue.put(("frame", sample.frame_bgr.copy()))

    def _append_feedback(self, text: str, tag: str | None = None) -> None:
        self.feedback.configure(state="normal")
        if tag:
            self.feedback.insert("end", text, tag)
        else:
            self.feedback.insert("end", text)
        self.feedback.see("end")
        self.feedback.configure(state="disabled")

    def _show_frame(self, frame_bgr) -> None:
        import cv2
        frame_rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
        image = Image.fromarray(frame_rgb)
        image.thumbnail((720, 520))
        self.latest_photo = ImageTk.PhotoImage(image=image)
        self.preview.configure(image=self.latest_photo, text="")

    def _drain_events(self) -> None:
        try:
            while True:
                kind, payload = self.event_queue.get_nowait()
                if kind == "shot":
                    self.shot_count += 1
                    if payload.moving:
                        self.moving_shot_count += 1
                    if payload.walking:
                        self.walk_shot_count += 1
                    if payload.crouching:
                        self.crouch_shot_count += 1
                    self.shots_var.set(str(self.shot_count))
                    self.move_var.set(str(self.moving_shot_count))
                    self.walk_var.set(str(self.walk_shot_count))
                    self.crouch_var.set(str(self.crouch_shot_count))
                elif kind == "skill":
                    self.skill_count += 1
                    self.skill_var.set(str(self.skill_count))
                elif kind == "key":
                    visible = [str(x).upper() for x in payload if x in {"w", "a", "s", "d", "shift", "ctrl", "mouse4", "mouse5"}]
                    self.keys_var.set("INPUT  " + (" + ".join(visible) if visible else "-"))
                elif kind == "frame":
                    self._show_frame(payload)
                elif kind == "clip_saved":
                    video_path, metadata_path = payload
                    if video_path:
                        self._append_feedback(f"교전 클립 저장 · {video_path.name}\n", "muted")
                    elif metadata_path:
                        self._append_feedback(f"교전 메타데이터 저장 · {metadata_path.name}\n", "muted")
        except queue.Empty:
            pass
        self.after(50, self._drain_events)

    def _poll_fights(self) -> None:
        if self.session_running:
            fight = self.engine.poll_finalized_fight()
            if fight:
                self._render_fight(fight)
                if self.config_data.get("record_fight_clips", True):
                    threading.Thread(target=self._save_fight_clip, args=(fight,), daemon=True).start()
        self.after(300, self._poll_fights)

    def _save_fight_clip(self, fight: FightFeedback) -> None:
        try:
            skills = self.engine.skill_events_for_fight(fight)
            video_path, metadata_path = self.recorder.save_fight(
                fight.started_at,
                fight.ended_at,
                pre_seconds=float(self.config_data.get("fight_clip_pre_seconds", 4.0)),
                post_seconds=float(self.config_data.get("fight_clip_post_seconds", 1.5)),
                skill_events=skills,
                feedback_payload=asdict(fight),
            )
            self.event_queue.put(("clip_saved", (video_path, metadata_path)))
        except Exception as exc:
            self.event_queue.put(("clip_saved", (None, None)))
            print(f"[Recorder] clip save failed: {type(exc).__name__}: {exc}")

    def _render_fight(self, fight: FightFeedback) -> None:
        duration = max(0.0, fight.ended_at - fight.started_at)
        stop_text = "--" if fight.median_stop_to_shot_ms is None else f"{fight.median_stop_to_shot_ms:.0f}ms"
        skill_score = "--" if fight.skill_timing_score is None else str(fight.skill_timing_score)

        self._append_feedback(f"\n{time.strftime('%H:%M:%S')} · {fight.title}\n", "heading")
        self._append_feedback(
            f"무빙 {fight.movement_score}/100   ·   스킬 타이밍 {skill_score}/100   ·   사격 {fight.shots}발   ·   교전 {duration:.1f}s\n"
            f"WASD중 {fight.moving_shot_ratio * 100:.0f}%   ·   Shift {fight.walk_shot_ratio * 100:.0f}%   ·   Ctrl {fight.crouch_shot_ratio * 100:.0f}%   ·   정지→첫 탄 {stop_text}\n"
        )
        for message in fight.movement_messages:
            self._append_feedback(f"• 무빙 · {message}\n")
        for message in fight.skill_messages:
            self._append_feedback(f"• 스킬 · {message}\n")
        if fight.skill_uses:
            uses = " / ".join(
                f"{use['label']}({use['binding'].upper()}, {use['relative_seconds']:+.1f}s, {use['phase']})"
                for use in fight.skill_uses
            )
            self._append_feedback(f"스킬 타임라인 · {uses}\n", "muted")

    def start_session(self) -> None:
        if self.session_running:
            return
        self.shot_count = self.moving_shot_count = self.walk_shot_count = self.crouch_shot_count = self.skill_count = 0
        for variable in (self.shots_var, self.move_var, self.walk_var, self.crouch_var, self.skill_var):
            variable.set("0")
        self.keys_var.set("INPUT  -")
        self.engine.start_session()
        self.recorder.start_session()
        self.input_tracker.set_skill_bindings(self.config_data["skill_bindings"])
        try:
            self.input_tracker.start()
            self.capture.start()
        except Exception as exc:
            self.input_tracker.stop()
            self.capture.stop()
            messagebox.showerror("시작 실패", str(exc))
            return
        self.session_running = True
        self.start_btn.configure(state="disabled", bg="#5e2730")
        self.stop_btn.configure(state="normal")
        self.status_var.set("분석 중 · 교전 자동 녹화")
        self._append_feedback("\n새 세션 시작\n", "heading")

    def stop_session(self) -> None:
        if not self.session_running:
            return
        self.session_running = False
        self.input_tracker.stop()
        self.capture.stop()
        path = self.engine.save_session()
        summary = self.engine.session_summary()
        self.start_btn.configure(state="normal", bg=ACCENT)
        self.stop_btn.configure(state="disabled")
        self.status_var.set("세션 종료 · 저장 완료")
        self.keys_var.set("INPUT  -")

        m = summary["movement_metrics"]
        score = summary["average_fight_score"]
        score_text = "--" if score is None else f"{score:.1f}"
        stop_text = "--" if m["median_stop_to_shot_ms"] is None else f"{m['median_stop_to_shot_ms']:.0f}ms"
        priorities = "\n".join(f"• {item}" for item in summary["priorities"])
        skill_usage = summary.get("skill_usage", {})
        self._append_feedback("\n세션 요약\n", "heading")
        self._append_feedback(
            f"교전 {summary['fight_count']}회 · 사격 {summary['shots']}발 · 스킬 입력 {skill_usage.get('total', 0)}회\n"
            f"WASD중 사격 {m['moving_shot_ratio'] * 100:.0f}% · Shift {m['walk_shot_ratio'] * 100:.0f}% · Ctrl {m['crouch_shot_ratio'] * 100:.0f}%\n"
            f"정지→첫 탄 {stop_text} · 반대방향 탭 {m['opposite_tap_ratio'] * 100:.0f}% · 평균 무빙 점수 {score_text}\n"
            f"우선 개선:\n{priorities}\n"
            f"세션 JSON · {path}\n"
        )

    def open_skill_settings(self) -> None:
        dialog = tk.Toplevel(self)
        dialog.title("스킬 키 설정")
        dialog.geometry("560x420")
        dialog.resizable(False, False)
        dialog.configure(bg=BG)
        dialog.transient(self)
        dialog.grab_set()

        container = tk.Frame(dialog, bg=BG, padx=22, pady=20)
        container.pack(fill="both", expand=True)
        tk.Label(container, text="SKILL BINDINGS", bg=BG, fg=ACCENT, font=("Segoe UI", 9, "bold")).pack(anchor="w")
        tk.Label(container, text="스킬 키 설정", bg=BG, fg=TEXT, font=("Segoe UI", 22, "bold")).pack(anchor="w", pady=(3, 4))
        tk.Label(
            container,
            text="실제 VALORANT 키 설정과 동일하게 입력하세요. 예: q, e, c, x, 1, mouse4, mouse5",
            bg=BG,
            fg=MUTED,
            font=("Segoe UI", 9),
        ).pack(anchor="w", pady=(0, 14))

        card, inner = self._card(container, 14)
        card.pack(fill="x")
        entries: dict[str, tuple[tk.Entry, tk.Entry]] = {}
        headers = tk.Frame(inner, bg=PANEL)
        headers.pack(fill="x", pady=(0, 6))
        tk.Label(headers, text="슬롯/이름", bg=PANEL, fg=MUTED, width=26, anchor="w", font=("Segoe UI", 9, "bold")).pack(side="left")
        tk.Label(headers, text="키", bg=PANEL, fg=MUTED, width=16, anchor="w", font=("Segoe UI", 9, "bold")).pack(side="left", padx=(12, 0))

        for slot in ("skill1", "skill2", "skill3", "ultimate"):
            item = self.config_data["skill_bindings"].get(slot, DEFAULT_SKILL_BINDINGS[slot])
            row = tk.Frame(inner, bg=PANEL)
            row.pack(fill="x", pady=5)
            label_entry = tk.Entry(row, bg=PANEL2, fg=TEXT, insertbackground=TEXT, relief="flat", font=("Segoe UI", 10))
            label_entry.insert(0, str(item.get("label", slot)))
            label_entry.pack(side="left", fill="x", expand=True, ipady=7)
            key_entry = tk.Entry(row, bg=PANEL2, fg=TEXT, insertbackground=TEXT, relief="flat", width=15, font=("Consolas", 10, "bold"))
            key_entry.insert(0, str(item.get("key", "")))
            key_entry.pack(side="left", padx=(12, 0), ipady=7)
            entries[slot] = (label_entry, key_entry)

        note = tk.Label(
            container,
            text="같은 키를 두 스킬에 중복 지정할 수 없습니다. Mouse4/Mouse5도 지원합니다.",
            bg=BG,
            fg=MUTED,
            font=("Segoe UI", 9),
        )
        note.pack(anchor="w", pady=(12, 0))

        buttons = tk.Frame(container, bg=BG)
        buttons.pack(fill="x", side="bottom", pady=(16, 0))

        def save_bindings() -> None:
            new_bindings = {}
            used = set()
            for slot, (label_entry, key_entry) in entries.items():
                label = label_entry.get().strip() or DEFAULT_SKILL_BINDINGS[slot]["label"]
                key = normalize_binding(key_entry.get())
                if not key:
                    messagebox.showerror("키 설정", f"{label}의 키를 입력해 주세요.", parent=dialog)
                    return
                if key in used:
                    messagebox.showerror("키 설정", f"{key.upper()} 키가 중복 지정되어 있습니다.", parent=dialog)
                    return
                used.add(key)
                new_bindings[slot] = {"label": label, "key": key}

            self.config_data["skill_bindings"] = new_bindings
            save_config(self.config_data)
            self.input_tracker.set_skill_bindings(new_bindings)
            self.bindings_var.set(self._bindings_text())
            self._append_feedback("스킬 키 설정 저장 · " + self._bindings_text() + "\n", "muted")
            dialog.destroy()

        self._button(buttons, "저장", save_bindings, primary=True, width=12).pack(side="right")
        self._button(buttons, "취소", dialog.destroy, width=12).pack(side="right", padx=(0, 8))

    def learn_reference_profile(self) -> None:
        if self.session_running:
            messagebox.showinfo("세션 진행 중", "세션을 종료한 뒤 기준 프로필을 학습해 주세요.")
            return
        try:
            profile = learn_profile_from_sessions(REFERENCE_DIR, PROFILE_PATH)
            self.engine.reload_pro_profile()
            self.profile_var.set(self._profile_label())
            messagebox.showinfo("기준 학습 완료", f"{profile.get('sample_sessions', 0)}개 기준 세션에서 무빙 프로필을 학습했습니다.")
        except Exception as exc:
            messagebox.showerror("기준 학습 실패", f"{exc}\n\n세션 JSON을 {REFERENCE_DIR} 폴더에 최소 3개 넣어 주세요.")

    def open_clip_folder(self) -> None:
        CLIP_DIR.mkdir(parents=True, exist_ok=True)
        self._open_path(CLIP_DIR)

    @staticmethod
    def _open_path(path: Path) -> None:
        import os
        try:
            os.startfile(path)  # type: ignore[attr-defined]
        except Exception:
            messagebox.showinfo("경로", str(path))

    def _on_close(self) -> None:
        if self.session_running:
            self.stop_session()
        self.destroy()


if __name__ == "__main__":
    App().mainloop()
