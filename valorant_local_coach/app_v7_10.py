from __future__ import annotations

import tkinter as tk

import customtkinter as ctk

import app_v7 as rootbase
import app_v7_2 as v72
import app_v7_9 as v79
from video_chat_v710 import VideoCoachChatV710
from video_review_v710 import analyze_video as analyze_video_v710

rootbase.analyze_video = analyze_video_v710
v72.base.analyze_video = analyze_video_v710

ACCENT = rootbase.ACCENT
PANEL2 = rootbase.PANEL2
MUTED = rootbase.MUTED
FONT = rootbase.FONT


class App(v79.App):
    """v7.10: no death/KD inference; kills come from the top-right killfeed."""

    def __init__(self):
        super().__init__()
        self.title("VALORANT Video Coach v7.10")
        self.chat_client = VideoCoachChatV710(self.config_data)
        self.after_idle(self._rename_kd_card)
        self._append_chat("AI · v7.10은 데스를 자동 분석하지 않습니다. 내 킬은 오른쪽 위 킬로그의 강조 테두리만 사용합니다.\n\n")

    def _install_combat_correction_controls(self) -> None:
        if not hasattr(self, "event_list"):
            return
        parent = self.event_list.master
        try:
            self.event_list.pack_forget()
        except Exception:
            pass
        self.kd_status_var = tk.StringVar(value="오른쪽 위 킬로그 · 내 킬 강조 테두리 기준")
        bar = ctk.CTkFrame(parent, fg_color="#111720", corner_radius=10)
        bar.pack(fill="x", padx=7, pady=(0, 7))
        ctk.CTkLabel(bar, textvariable=self.kd_status_var, text_color=MUTED, font=(FONT, 8, "bold"), anchor="w", justify="left", wraplength=185).pack(fill="x", padx=9, pady=8)
        ctk.CTkLabel(parent, text="이벤트 이동 · 킬로그 킬/스킬/사격/에임", text_color=MUTED, font=(FONT, 8, "bold"), anchor="w").pack(fill="x", padx=10, pady=(0, 3))
        self.event_list.pack(fill="both", expand=True, padx=7, pady=(0, 8))

    def _open_kd_dialog(self) -> None:
        return

    def _rename_kd_card(self) -> None:
        def walk(widget):
            try:
                children = widget.winfo_children()
            except Exception:
                return
            for child in children:
                try:
                    text = child.cget("text") if hasattr(child, "cget") else None
                    if text == "내 K/D":
                        child.configure(text="킬로그 킬")
                    elif text == "K/D 교정":
                        child.configure(text="킬로그")
                except Exception:
                    pass
                walk(child)
        walk(self)

    def _deliver_report(self, report):
        super()._deliver_report(report)
        combat = report.get("combat") or {}
        kills = int(combat.get("kills") or 0)
        self.metric_vars["kd"].set(str(kills))
        try:
            self.kd_status_var.set(f"킬로그 기반 내 킬 {kills}회 · 데스 분석 안 함")
        except Exception:
            pass

    def _populate_events(self, report: dict) -> None:
        for child in self.event_list.winfo_children():
            child.destroy()
        events = [e for e in (report.get("timeline_events") or []) if str(e.get("kind")) != "death"]
        if not events:
            ctk.CTkLabel(self.event_list, text="감지된 이벤트가 없습니다.", text_color=MUTED, font=(FONT, 9)).pack(anchor="w", padx=5, pady=6)
            return
        colors = {"kill": ACCENT, "skill": "#465dff", "shot": "#2d7f68", "aim": "#a44cff"}
        for item in events:
            t = float(item.get("video_seconds") or 0.0)
            kind = str(item.get("kind") or "event")
            label = str(item.get("label") or kind)
            if kind == "kill":
                label = "킬로그 · 내 킬"
            ctk.CTkButton(self.event_list, text=f"{self._fmt_time(t)}   {label}", command=lambda sec=t: self._jump_event(sec), anchor="w", height=34, corner_radius=10, fg_color=colors.get(kind, PANEL2), hover_color="#354052", text_color="white", font=(FONT, 9, "bold")).pack(fill="x", padx=3, pady=3)

    def _render_feedback(self, report: dict) -> None:
        tier = report.get("tier_prediction") or {}
        combat = report.get("combat") or {}
        kills = int(combat.get("kills") or 0)
        lines = [
            ("영상 분석 결과\n", "heading"),
            (f"예상 티어  {tier.get('tier_ko', '판정 불가')}  ·  티어 점수 {self._score(tier.get('score'))}  ·  신뢰도 {float(tier.get('confidence', 0))*100:.0f}%\n", None),
            (f"에임 {self._score(report.get('aim_score'))}   ·   헤드라인 {self._score(report.get('headline_score'))}   ·   무빙 안정성 {self._score(report.get('movement_score'))}   ·   스킬 {('—' if report.get('mode') == 'brawl' else self._score(report.get('skill_score')))}\n", None),
            (f"실제 발사 추정 {report.get('confirmed_shots', 0)}발   ·   사격 구간 {report.get('shot_burst_count', 0)}회   ·   킬로그 기반 내 킬 {kills}회   ·   데스 분석 안 함\n\n", "muted"),
            ("코칭 피드백\n", "heading"),
        ]
        for item in report.get("feedback") or []:
            lines.append((f"• {item}\n", None))
        comparison = report.get("personal_comparison") or {}
        if comparison.get("messages"):
            lines.append(("\n개인 학습 비교\n", "heading"))
            for item in comparison["messages"][:5]:
                lines.append((f"• {item}\n", "muted"))
        lines.append(("\n주의 · 킬은 오른쪽 위 킬로그에서 내 킬에 붙는 강조 테두리를 영상으로 판독한 추정치입니다. 데스는 계산하지 않습니다.\n", "muted"))

        self.feedback.configure(state="normal")
        self.feedback.delete("1.0", "end")
        for text, tag in lines:
            start = self.feedback.index("end")
            self.feedback.insert("end", text, tag or ())
            search_from = start
            while True:
                pos = self.feedback.search("/100", search_from, stopindex="end")
                if not pos:
                    break
                line_start = self.feedback.index(f"{pos} linestart")
                token_start = self.feedback.search(" ", pos, backwards=True, stopindex=line_start)
                token_start = line_start if not token_start else self.feedback.index(f"{token_start}+1c")
                self.feedback.tag_add("score", token_start, f"{pos}+4c")
                search_from = f"{pos}+4c"
        self.feedback.configure(state="disabled")
        self.feedback.see("1.0")


if __name__ == "__main__":
    App().mainloop()
