from __future__ import annotations

import tkinter as tk

import customtkinter as ctk

import app_v7 as rootbase
import app_v7_2 as v72
import app_v7_11 as v711
from video_review_v713 import analyze_video as analyze_video_v713, ensure_v713_calibration

rootbase.analyze_video = analyze_video_v713
v72.base.analyze_video = analyze_video_v713

ACCENT = rootbase.ACCENT
PANEL2 = rootbase.PANEL2
MUTED = rootbase.MUTED
TEXT = rootbase.TEXT
FONT = rootbase.FONT


class App(v711.App):
    """v7.13: clean event card + guaranteed aim calibration in report and UI."""

    def __init__(self):
        super().__init__()
        self.title("VALORANT Video Coach v7.13")
        self.after_idle(self._tune_event_layout_v713)
        self._append_chat(
            "AI · v7.13은 EVENT JUMP 중복 UI를 제거했고, 에임 점수 보정을 분석 결과와 화면 표시 양쪽에서 확인합니다.\n\n"
        )

    def _install_combat_correction_controls(self) -> None:
        """Rebuild the event card once so inherited titles cannot overlap."""
        if not hasattr(self, "event_list"):
            return
        parent = self.event_list.master

        try:
            self.event_list.pack_forget()
        except Exception:
            pass

        for child in list(parent.winfo_children()):
            if child is self.event_list:
                continue
            try:
                child.destroy()
            except Exception:
                pass

        self.kd_status_var = tk.StringVar(value="킬로그 킬만 분석 · 데스 계산 안 함")

        ctk.CTkLabel(
            parent, text="EVENT JUMP", text_color=ACCENT,
            font=(FONT, 10, "bold"), anchor="w",
        ).pack(fill="x", padx=12, pady=(12, 0))
        ctk.CTkLabel(
            parent, text="감지된 시점", text_color=TEXT,
            font=(FONT, 15, "bold"), anchor="w",
        ).pack(fill="x", padx=12, pady=(1, 7))

        status = ctk.CTkFrame(parent, fg_color="#111720", corner_radius=10)
        status.pack(fill="x", padx=10, pady=(0, 8))
        ctk.CTkLabel(
            status, textvariable=self.kd_status_var, text_color=MUTED,
            font=(FONT, 8, "bold"), anchor="w", justify="left", wraplength=250,
        ).pack(fill="x", padx=10, pady=8)

        self.event_list.pack(fill="both", expand=True, padx=6, pady=(0, 8))

    def _tune_event_layout_v713(self) -> None:
        try:
            event_card = self.event_list.master
            video_tab = event_card.master
            video_tab.grid_columnconfigure(0, weight=7, minsize=690)
            video_tab.grid_columnconfigure(1, weight=3, minsize=300)
            event_card.configure(width=300)
        except Exception:
            pass

    def _populate_events(self, report: dict) -> None:
        for child in self.event_list.winfo_children():
            try:
                child.destroy()
            except Exception:
                pass

        events = [e for e in (report.get("timeline_events") or []) if str(e.get("kind")) != "death"]
        if not events:
            ctk.CTkLabel(
                self.event_list, text="감지된 이벤트가 없습니다.", text_color=MUTED,
                font=(FONT, 9), anchor="w",
            ).pack(fill="x", padx=6, pady=8)
            return

        colors = {"kill": ACCENT, "skill": "#465dff", "shot": "#2d7f68", "aim": "#a44cff"}
        for item in events:
            t = float(item.get("video_seconds") or 0.0)
            kind = str(item.get("kind") or "event")
            label = str(item.get("label") or kind)
            if kind == "kill":
                label = "킬로그 · 내 킬"
            ctk.CTkButton(
                self.event_list,
                text=f"{self._fmt_time(t)}   {label}",
                command=lambda sec=t: self._jump_event(sec),
                anchor="w", height=32, corner_radius=10,
                fg_color=colors.get(kind, PANEL2), hover_color="#354052",
                text_color="white", font=(FONT, 8, "bold"),
            ).pack(fill="x", padx=(4, 14), pady=3)

    def _deliver_report(self, report):
        ensure_v713_calibration(report)
        super()._deliver_report(report)
        try:
            kills = int((report.get("combat") or {}).get("kills") or 0)
            self.kd_status_var.set(f"킬로그 기반 내 킬 {kills}회 · 데스 계산 안 함")
        except Exception:
            pass


if __name__ == "__main__":
    App().mainloop()
