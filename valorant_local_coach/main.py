from __future__ import annotations

import json
import queue
import sys
import time
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk

from PIL import Image, ImageTk

from coach_engine import CoachEngine, FightFeedback
from input_tracker import InputTracker, ShotEvent
from screen_capture import FrameSample, ScreenCapture


ROOT_DIR = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
WORK_DIR = Path.cwd() if getattr(sys, "frozen", False) else Path(__file__).resolve().parent
CONFIG_PATH = WORK_DIR / "config.json"

DEFAULT_CONFIG = {
    "capture_fps": 12,
    "analysis_width": 960,
    "fight_gap_seconds": 1.8,
    "min_shots_per_fight": 2,
    "moving_shot_warn_ratio": 0.25,
    "long_burst_shot_count": 6,
    "camera_motion_warn_threshold": 18.0,
    "save_training_frames": True,
    "training_frame_interval_ms": 120,
    "monitor_index": 1,
}


def load_config() -> dict:
    if not CONFIG_PATH.exists():
        CONFIG_PATH.write_text(json.dumps(DEFAULT_CONFIG, indent=2), encoding="utf-8")
        return dict(DEFAULT_CONFIG)
    try:
        data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        return {**DEFAULT_CONFIG, **data}
    except Exception:
        return dict(DEFAULT_CONFIG)


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("VALORANT Local Coach v1")
        self.geometry("1050x760")
        self.minsize(900, 650)

        self.config_data = load_config()
        self.engine = CoachEngine(self.config_data, WORK_DIR)
        self.event_queue: queue.Queue = queue.Queue()
        self.input_tracker = InputTracker(on_shot=self._thread_shot)
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

        self._build_ui()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(50, self._drain_events)
        self.after(300, self._poll_fights)

    def _build_ui(self) -> None:
        root = ttk.Frame(self, padding=14)
        root.pack(fill="both", expand=True)

        title = ttk.Label(root, text="VALORANT Local Coach", font=("Segoe UI", 22, "bold"))
        title.pack(anchor="w")
        ttk.Label(
            root,
            text="API/서버 없이 PC에서 화면과 입력을 분석합니다. 교전 중 지시 대신 교전 종료 후 피드백을 표시합니다.",
        ).pack(anchor="w", pady=(2, 12))

        controls = ttk.Frame(root)
        controls.pack(fill="x")
        self.start_btn = ttk.Button(controls, text="세션 시작", command=self.start_session)
        self.start_btn.pack(side="left")
        self.stop_btn = ttk.Button(controls, text="세션 종료", command=self.stop_session, state="disabled")
        self.stop_btn.pack(side="left", padx=8)
        ttk.Button(controls, text="설정 파일 열기", command=self.open_config).pack(side="left")

        self.status_var = tk.StringVar(value="대기 중")
        ttk.Label(controls, textvariable=self.status_var).pack(side="right")

        stats = ttk.Frame(root)
        stats.pack(fill="x", pady=12)
        self.shots_var = tk.StringVar(value="사격 0")
        self.move_var = tk.StringVar(value="이동사격 0")
        self.fights_var = tk.StringVar(value="교전 분석 0")
        for variable in (self.shots_var, self.move_var, self.fights_var):
            ttk.Label(stats, textvariable=variable, font=("Segoe UI", 11, "bold")).pack(side="left", padx=(0, 24))

        body = ttk.Panedwindow(root, orient="horizontal")
        body.pack(fill="both", expand=True)

        left = ttk.Frame(body, padding=6)
        right = ttk.Frame(body, padding=6)
        body.add(left, weight=3)
        body.add(right, weight=2)

        ttk.Label(left, text="화면 미리보기", font=("Segoe UI", 12, "bold")).pack(anchor="w")
        self.preview = ttk.Label(left, text="세션을 시작하면 화면 캡처가 표시됩니다.", anchor="center")
        self.preview.pack(fill="both", expand=True, pady=(8, 0))

        ttk.Label(right, text="교전 후 피드백", font=("Segoe UI", 12, "bold")).pack(anchor="w")
        self.feedback = tk.Text(right, wrap="word", height=22, state="disabled", font=("Segoe UI", 10))
        self.feedback.pack(fill="both", expand=True, pady=(8, 0))
        self._append_feedback("세션을 시작한 뒤 VALORANT를 플레이하세요.\n왼쪽 클릭 사격과 WASD 입력, 화면 움직임을 로컬에서 분석합니다.\n")

    def _thread_shot(self, event: ShotEvent) -> None:
        self.engine.on_shot(event)
        self.event_queue.put(("shot", event))

    def _thread_frame(self, sample: FrameSample) -> None:
        self.engine.on_frame(sample)
        if int(sample.timestamp * 4) != int((sample.timestamp - 0.05) * 4):
            self.event_queue.put(("frame", sample.frame_bgr.copy()))

    def _append_feedback(self, text: str) -> None:
        self.feedback.configure(state="normal")
        self.feedback.insert("end", text)
        self.feedback.see("end")
        self.feedback.configure(state="disabled")

    def _show_frame(self, frame_bgr) -> None:
        import cv2
        frame_rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
        image = Image.fromarray(frame_rgb)
        image.thumbnail((620, 480))
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
                    self.shots_var.set(f"사격 {self.shot_count}")
                    self.move_var.set(f"이동사격 {self.moving_shot_count}")
                elif kind == "frame":
                    self._show_frame(payload)
        except queue.Empty:
            pass
        self.after(50, self._drain_events)

    def _poll_fights(self) -> None:
        if self.session_running:
            fight = self.engine.poll_finalized_fight()
            if fight:
                self._render_fight(fight)
                self.fights_var.set(f"교전 분석 {len(self.engine.fights)}")
        self.after(300, self._poll_fights)

    def _render_fight(self, fight: FightFeedback) -> None:
        duration = max(0.0, fight.ended_at - fight.started_at)
        lines = [
            f"\n[{time.strftime('%H:%M:%S')}] {fight.title} · 점수 {fight.score}/100",
            f"사격 {fight.shots}발 · 이동사격 {fight.moving_shots}발 ({fight.moving_shot_ratio * 100:.0f}%) · 교전 {duration:.1f}초",
        ]
        lines.extend(f"• {message}" for message in fight.messages)
        self._append_feedback("\n".join(lines) + "\n")

    def start_session(self) -> None:
        if self.session_running:
            return
        self.shot_count = 0
        self.moving_shot_count = 0
        self.shots_var.set("사격 0")
        self.move_var.set("이동사격 0")
        self.fights_var.set("교전 분석 0")
        self.engine.start_session()
        try:
            self.input_tracker.start()
            self.capture.start()
        except Exception as exc:
            self.input_tracker.stop()
            self.capture.stop()
            messagebox.showerror("시작 실패", str(exc))
            return
        self.session_running = True
        self.start_btn.configure(state="disabled")
        self.stop_btn.configure(state="normal")
        self.status_var.set("분석 중 · 로컬 전용")
        self._append_feedback("\n=== 새 세션 시작 ===\n")

    def stop_session(self) -> None:
        if not self.session_running:
            return
        self.session_running = False
        self.input_tracker.stop()
        self.capture.stop()
        path = self.engine.save_session()
        summary = self.engine.session_summary()
        self.start_btn.configure(state="normal")
        self.stop_btn.configure(state="disabled")
        self.status_var.set("세션 종료")

        ratio = float(summary["moving_shot_ratio"]) * 100
        score = summary["average_fight_score"]
        score_text = "--" if score is None else f"{score:.1f}"
        priorities = "\n".join(f"• {item}" for item in summary["priorities"])
        self._append_feedback(
            "\n=== 세션 요약 ===\n"
            f"교전 {summary['fight_count']}회 · 사격 {summary['shots']}발 · 이동사격 {ratio:.0f}%\n"
            f"평균 교전 점수: {score_text}\n"
            f"우선 개선:\n{priorities}\n"
            f"저장: {path}\n"
        )

    def open_config(self) -> None:
        import os
        try:
            os.startfile(CONFIG_PATH)  # type: ignore[attr-defined]
        except Exception:
            messagebox.showinfo("설정 파일", str(CONFIG_PATH))

    def _on_close(self) -> None:
        if self.session_running:
            self.stop_session()
        self.destroy()


if __name__ == "__main__":
    App().mainloop()
