from __future__ import annotations

import app_v7 as rootbase
import app_v7_2 as v72
import app_v7_3 as v73
from video_review_v74 import analyze_video as analyze_video_v74

rootbase.analyze_video = analyze_video_v74
v72.base.analyze_video = analyze_video_v74


class App(v73.App):
    """v7.4: recalibrated aim/movement scores and broad tier ranges."""

    def __init__(self):
        super().__init__()
        self.title("VALORANT Video Coach v7.4")

    def _deliver_report(self, report):
        super()._deliver_report(report)
        tier = report.get("tier_prediction") or {}
        if tier.get("tier_ko"):
            self.metric_vars["tier"].set(str(tier.get("tier_ko")))
        self._append_chat(
            f"AI · v7.4 재보정 완료. 티어는 {tier.get('tier_ko', '판정 불가')} 범위, "
            f"에임 {self._score(report.get('aim_score'))}, 무빙 {self._score(report.get('movement_score'))}입니다.\n\n"
        )


if __name__ == "__main__":
    App().mainloop()
