from __future__ import annotations

import app_v7 as base
from video_review_v71 import analyze_video as analyze_video_v71

base.analyze_video = analyze_video_v71


class App(base.App):
    """v7.1: high-accuracy offline K/D and shot-synchronized aim refinement."""

    def __init__(self):
        super().__init__()
        self.title("VALORANT Video Coach v7.1")


if __name__ == "__main__":
    App().mainloop()
