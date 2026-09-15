from __future__ import annotations

import app_v7 as rootbase
import app_v7_2 as v72
import app_v7_13 as v713
from video_review_v714 import analyze_video as analyze_video_v714, ensure_v714_tier

rootbase.analyze_video = analyze_video_v714
v72.base.analyze_video = analyze_video_v714


class App(v713.App):
    """v7.14: tier prediction directly balances aim, movement and skill."""

    def __init__(self):
        super().__init__()
        self.title("VALORANT Video Coach v7.14")
        self._append_chat(
            "AI · v7.14는 티어 평가에서 무빙과 스킬 비중을 높였고, 상위 티어는 에임뿐 아니라 무빙도 기준을 통과해야 합니다.\n\n"
        )

    def _deliver_report(self, report):
        # v7.13's inherited UI safety net recalculates its own tier once. Let it
        # finish the common UI work, then restore v7.14's movement/skill-aware
        # tier and repaint only the affected outputs.
        super()._deliver_report(report)
        ensure_v714_tier(report)
        tier = report.get("tier_prediction") or {}
        self.metric_vars["tier"].set(str(tier.get("tier_ko") or "판정 불가"))
        self._apply_tier_color(tier)
        self._render_feedback(report)


if __name__ == "__main__":
    App().mainloop()
