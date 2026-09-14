from __future__ import annotations

import copy
import json
import queue
import threading
import tkinter as tk
from pathlib import Path

import customtkinter as ctk

import app_v7 as rootbase
import app_v7_2 as v72
import app_v7_4 as v74
from video_review_v75 import analyze_video as analyze_video_v75

rootbase.analyze_video = analyze_video_v75
v72.base.analyze_video = analyze_video_v75

TEXT = v72.TEXT
MUTED = v72.MUTED
PANEL2 = v72.PANEL2
BORDER = v72.BORDER
ACCENT = v72.ACCENT
FONT = v72.FONT


class App(v74.App):
    """v7.5: thread-safe AI chat and user-correctable conservative K/D."""

    def __init__(self):
        self._chat_results: queue.Queue[tuple[str, str]] = queue.Queue()
        super().__init__()
        self.title("VALORANT Video Coach v7.5")
        self._install_chat_box_v75()
        self._install_combat_correction_controls()
        self.after(80, self._poll_chat_results)

    def _install_chat_box_v75(self) -> None:
        old = getattr(self, "chat_entry", None)
        if old is None:
            return
        parent = old.master
        try:
            old.destroy()
        except Exception:
            pass
        try:
            self.chat_send.pack_forget()
        except Exception:
            pass
        self.chat_entry = tk.Text(
            parent,
            height=2,
            wrap="word",
            bg="#171d27",
            fg=TEXT,
            insertbackground=TEXT,
            selectbackground="#3a4555",
            selectforeground=TEXT,
            relief="flat",
            bd=0,
            highlightthickness=1,
            highlightbackground=BORDER,
            highlightcolor=ACCENT,
            font=(FONT, 11),
            undo=True,
            takefocus=True,
        )
        self.chat_entry.pack(side="left", fill="x", expand=True, ipady=5)
        self.chat_send.pack(side="left", padx=(8, 0))
        self.chat_entry.bind("<Return>", self._chat_enter_v75)
        self.chat_entry.bind("<Shift-Return>", lambda _e: None)
        self.chat_entry.bind("<Button-1>", lambda _e: self.after_idle(self.chat_entry.focus_force))
        self._append_chat(
            "v7.5 · AI 입력/응답 경로를 Tk 메인 스레드 방식으로 변경했습니다. "
            "Ollama가 없어도 내장 코치가 답변합니다.\n\n"
        )
        self.after_idle(self.chat_entry.focus_set)

    def _chat_enter_v75(self, event=None):
        if event is not None and (event.state & 0x0001):
            return None
        self.send_chat()
        return "break"

    def _chat_text(self) -> str:
        try:
            return self.chat_entry.get("1.0", "end-1c").strip()
        except Exception:
            try:
                return self.chat_entry.get().strip()
            except Exception:
                return ""

    def _clear_chat_input(self) -> None:
        try:
            self.chat_entry.delete("1.0", "end")
        except Exception:
            try:
                self.chat_entry.delete(0, "end")
            except Exception:
                pass

    def send_chat(self) -> None:
        q = self._chat_text()
        if not q:
            try:
                self.chat_entry.focus_force()
            except Exception:
                pass
            return
        if self.chat_busy:
            self._append_chat("AI · 이전 답변을 생성 중입니다. 입력 내용은 유지했습니다.\n\n")
            return
        self._clear_chat_input()
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
                source = getattr(reply, "source", "builtin")
            except Exception as exc:
                text = f"AI 응답 오류: {type(exc).__name__}: {exc}"
            self._chat_results.put((text, source))

        threading.Thread(target=worker, daemon=True, name="VideoCoachChatV75").start()

    def _poll_chat_results(self) -> None:
        try:
            while True:
                text, source = self._chat_results.get_nowait()
                self._deliver_chat_v75(text, source)
        except queue.Empty:
            pass
        try:
            if self.winfo_exists():
                self.after(80, self._poll_chat_results)
        except Exception:
            pass

    def _deliver_chat_v75(self, text: str, source: str) -> None:
        self.chat_history.append({"role": "assistant", "content": text})
        label = "Ollama" if source == "ollama" else "내장 코치"
        self._append_chat(f"AI · {text}\n   [{label}]\n\n")
        self.chat_busy = False
        try:
            self.chat_send.configure(state="normal", text="전송")
            self.chat_entry.focus_force()
        except Exception:
            pass

    def _install_combat_correction_controls(self) -> None:
        if not hasattr(self, "event_list"):
            return
        parent = self.event_list.master
        try:
            self.event_list.pack_forget()
        except Exception:
            pass
        self.kd_status_var = tk.StringVar(value="자동 K/D · 분석 후 교정 가능")
        ctl = ctk.CTkFrame(parent, fg_color="transparent")
        ctl.pack(fill="x", padx=7, pady=(0, 5))
        ctk.CTkLabel(ctl, textvariable=self.kd_status_var, text_color=MUTED, font=(FONT, 8, "bold"), anchor="w").pack(fill="x", padx=3, pady=(0, 4))
        row = ctk.CTkFrame(ctl, fg_color="transparent")
        row.pack(fill="x")
        ctk.CTkButton(row, text="현재 +킬", command=lambda: self._add_combat_event("kill"),
                      width=68, height=30, corner_radius=9, fg_color=ACCENT, hover_color="#ff5d69",
                      font=(FONT, 8, "bold")).pack(side="left", padx=(0, 3))
        ctk.CTkButton(row, text="현재 +데스", command=lambda: self._add_combat_event("death"),
                      width=72, height=30, corner_radius=9, fg_color="#7d3540", hover_color="#98424f",
                      font=(FONT, 8, "bold")).pack(side="left", padx=3)
        ctk.CTkButton(row, text="자동 복원", command=self._restore_auto_combat,
                      width=72, height=30, corner_radius=9, fg_color=PANEL2, hover_color="#354052",
                      font=(FONT, 8, "bold")).pack(side="right", padx=(3, 0))
        self.event_list.pack(fill="both", expand=True, padx=7, pady=(0, 8))

    def _deliver_report(self, report):
        super()._deliver_report(report)
        combat = report.get("combat") or {}
        self.kd_status_var.set(f"자동 감지 K {combat.get('kills', 0)} / D {combat.get('deaths', 0)} · 잘못된 이벤트는 삭제 가능")

    def _populate_events(self, report: dict) -> None:
        for child in self.event_list.winfo_children():
            child.destroy()
        events = report.get("timeline_events") or []
        if not events:
            ctk.CTkLabel(self.event_list, text="감지된 이벤트가 없습니다.", text_color=MUTED,
                         font=(FONT, 9)).pack(anchor="w", padx=5, pady=6)
            return
        colors = {"kill": ACCENT, "death": "#8d3540", "skill": "#465dff", "shot": "#2d7f68", "aim": "#a44cff"}
        for item in events:
            t = float(item.get("video_seconds") or 0.0)
            kind = str(item.get("kind") or "event")
            label = str(item.get("label") or kind)
            if kind in {"kill", "death"}:
                shell = ctk.CTkFrame(self.event_list, fg_color="transparent")
                shell.pack(fill="x", padx=3, pady=3)
                btn = ctk.CTkButton(shell, text=f"{self._fmt_time(t)}   {label}",
                                    command=lambda sec=t: self._jump_event(sec), anchor="w", height=34,
                                    corner_radius=10, fg_color=colors[kind], hover_color="#354052",
                                    text_color="white", font=(FONT, 9, "bold"))
                btn.pack(side="left", fill="x", expand=True)
                ctk.CTkButton(shell, text="×", command=lambda k=kind, sec=t: self._remove_combat_event(k, sec),
                              width=34, height=34, corner_radius=10, fg_color="#262d37", hover_color="#424b58",
                              text_color="#f6f7fb", font=(FONT, 12, "bold")).pack(side="left", padx=(4, 0))
            else:
                ctk.CTkButton(self.event_list, text=f"{self._fmt_time(t)}   {label}",
                              command=lambda sec=t: self._jump_event(sec), anchor="w", height=34, corner_radius=10,
                              fg_color=colors.get(kind, PANEL2), hover_color="#354052", text_color="white",
                              font=(FONT, 9, "bold")).pack(fill="x", padx=3, pady=3)

    def _add_combat_event(self, kind: str) -> None:
        if not self.last_report:
            return
        t = max(0.0, float(getattr(self, "_player_current", 0.0)))
        combat = self.last_report.setdefault("combat", {"events": []})
        events = combat.setdefault("events", [])
        if any(str(e.get("kind")) == kind and abs(float(e.get("video_seconds") or 0.0) - t) < 0.45 for e in events):
            self.kd_status_var.set("같은 시점에 동일한 이벤트가 이미 있습니다.")
            return
        events.append({
            "video_seconds": round(t, 2), "kind": kind, "confidence": 1.0,
            "score": 1.0, "source": "manual_user_correction", "manual": True,
        })
        self.last_report.setdefault("combat_corrections", []).append({"action": "add", "kind": kind, "video_seconds": round(t, 2)})
        self._sync_combat_after_edit()

    def _remove_combat_event(self, kind: str, seconds: float) -> None:
        if not self.last_report:
            return
        events = (self.last_report.get("combat") or {}).get("events") or []
        best_i = None
        best_d = 999.0
        for i, e in enumerate(events):
            if str(e.get("kind")) != kind:
                continue
            d = abs(float(e.get("video_seconds") or 0.0) - float(seconds))
            if d < best_d:
                best_i, best_d = i, d
        if best_i is not None and best_d < 0.55:
            removed = events.pop(best_i)
            self.last_report.setdefault("combat_corrections", []).append({
                "action": "remove", "kind": kind, "video_seconds": removed.get("video_seconds")
            })
            self._sync_combat_after_edit()

    def _restore_auto_combat(self) -> None:
        if not self.last_report:
            return
        auto = self.last_report.get("combat_auto")
        if not isinstance(auto, dict):
            return
        self.last_report["combat"] = copy.deepcopy(auto)
        self.last_report["combat_corrections"] = []
        self._sync_combat_after_edit(restored=True)

    def _sync_combat_after_edit(self, restored: bool = False) -> None:
        combat = self.last_report.setdefault("combat", {})
        events = combat.setdefault("events", [])
        events.sort(key=lambda e: float(e.get("video_seconds") or 0.0))
        kills = sum(1 for e in events if str(e.get("kind")) == "kill")
        deaths = sum(1 for e in events if str(e.get("kind")) == "death")
        combat["kills"] = kills
        combat["deaths"] = deaths
        combat["kd_ratio"] = round(kills / max(1, deaths), 2)
        combat["corrected"] = bool(self.last_report.get("combat_corrections"))
        timeline = [e for e in (self.last_report.get("timeline_events") or []) if str(e.get("kind")) not in {"kill", "death"}]
        for e in events:
            kind = str(e.get("kind") or "")
            if kind not in {"kill", "death"}:
                continue
            timeline.append({
                "video_seconds": float(e.get("video_seconds") or 0.0),
                "kind": kind,
                "label": "킬" if kind == "kill" else "데스",
                "manual": bool(e.get("manual")),
            })
        timeline.sort(key=lambda e: (float(e.get("video_seconds") or 0.0), str(e.get("kind") or "")))
        self.last_report["timeline_events"] = timeline
        self.metric_vars["kd"].set(f"{kills} / {deaths}")
        self.kd_status_var.set(
            f"{'자동 복원' if restored else '교정됨'} · K {kills} / D {deaths} · AI도 이 값을 사용"
        )
        self._populate_events(self.last_report)
        self._persist_corrected_report()
        self._append_chat(f"AI · K/D가 K {kills} / D {deaths}로 {'복원' if restored else '교정'}되었습니다. 다음 질문부터 이 값을 사용합니다.\n\n")

    def _persist_corrected_report(self) -> None:
        rp = (self.last_report or {}).get("report_path")
        if not rp:
            return
        try:
            Path(rp).write_text(json.dumps(self.last_report, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass


if __name__ == "__main__":
    App().mainloop()
