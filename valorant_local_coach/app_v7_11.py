from __future__ import annotations

import customtkinter as ctk

import app_v7 as rootbase
import app_v7_2 as v72
import app_v7_10 as v710
from video_review_v711 import analyze_video as analyze_video_v711

rootbase.analyze_video = analyze_video_v711
v72.base.analyze_video = analyze_video_v711

MUTED = rootbase.MUTED


class App(v710.App):
    """v7.11: one exact 9-level tier with rank-colored tier text."""

    def __init__(self):
        super().__init__()
        self.title("VALORANT Video Coach v7.11")
        self._tier_value_label = None
        self.after_idle(self._locate_tier_value_label)
        self._append_chat(
            "AI · v7.11은 예상 티어를 범위가 아닌 1개 티어로 표시하며 레디언트까지 포함합니다.\n\n"
        )

    def _locate_tier_value_label(self) -> None:
        target_var = str(self.metric_vars.get("tier"))
        found = None

        def walk(widget):
            nonlocal found
            if found is not None:
                return
            try:
                children = widget.winfo_children()
            except Exception:
                return
            for child in children:
                try:
                    if isinstance(child, ctk.CTkLabel):
                        textvar = child.cget("textvariable")
                        if textvar is not None and str(textvar) == target_var:
                            found = child
                            return
                except Exception:
                    pass
                walk(child)
                if found is not None:
                    return

        walk(self)
        self._tier_value_label = found

    def _apply_tier_color(self, tier: dict) -> None:
        color = str(tier.get("tier_color") or MUTED)
        if self._tier_value_label is None:
            self._locate_tier_value_label()
        if self._tier_value_label is not None:
            try:
                self._tier_value_label.configure(text_color=color)
            except Exception:
                pass

    def _deliver_report(self, report):
        super()._deliver_report(report)
        tier = report.get("tier_prediction") or {}
        self.metric_vars["tier"].set(str(tier.get("tier_ko") or "판정 불가"))
        self._apply_tier_color(tier)


if __name__ == "__main__":
    App().mainloop()
