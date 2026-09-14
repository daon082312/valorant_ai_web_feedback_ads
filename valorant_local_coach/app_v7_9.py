from __future__ import annotations

import time

import cv2
from PIL import Image, ImageTk

import app_v7_8 as v78


class App(v78.App):
    """v7.9: stable fixed video viewport and bounded frame rendering."""

    def __init__(self):
        self._last_main_render_at = 0.0
        self._main_render_interval = 1.0 / 15.0
        super().__init__()
        self.title("VALORANT Video Coach v7.9")
        try:
            self._decoder.max_display_fps = 15.0
        except Exception:
            pass
        self._stabilize_review_layout()
        try:
            self.review_tabs.configure(command=self._review_tab_changed_v79)
        except Exception:
            pass

    def _stabilize_review_layout(self) -> None:
        try:
            player_card = self.video_label.master
            player_card.configure(height=455)
            player_card.pack_propagate(False)
            video_tab = player_card.master
            video_tab.grid_propagate(False)
        except Exception:
            pass
        try:
            event_card = self.event_list.master
            event_card.configure(height=455)
            event_card.pack_propagate(False)
        except Exception:
            pass
        try:
            self.after_idle(self._redraw_latest_main_frame)
        except Exception:
            pass

    def _review_tab_changed_v79(self) -> None:
        try:
            tab = self.review_tabs.get()
        except Exception:
            return
        if tab == "피드백":
            try:
                if self.video_label.winfo_manager():
                    self.video_label.pack_forget()
            except Exception:
                pass
            return
        try:
            if not self.video_label.winfo_manager():
                self.video_label.pack(
                    fill="both", expand=True, padx=8, pady=(8, 4), before=self.timeline
                )
        except Exception:
            pass
        self.after_idle(self._redraw_latest_main_frame)

    @staticmethod
    def _fit_image(frame, max_w: int, max_h: int):
        if frame is None or getattr(frame, "size", 0) == 0:
            return None
        h, w = frame.shape[:2]
        if w <= 0 or h <= 0:
            return None
        scale = min(max_w / float(w), max_h / float(h))
        scale = max(0.05, min(1.5, scale))
        nw = max(1, int(w * scale))
        nh = max(1, int(h * scale))
        if nw != w or nh != h:
            interpolation = cv2.INTER_AREA if scale < 1.0 else cv2.INTER_LINEAR
            frame = cv2.resize(frame, (nw, nh), interpolation=interpolation)
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        return Image.fromarray(rgb)

    def _redraw_latest_main_frame(self) -> None:
        frame = getattr(self, "_latest_frame", None)
        if frame is not None:
            self._render_player_frame(frame, force=True)

    def _render_player_frame(self, frame, force: bool = False) -> None:
        if frame is None or getattr(frame, "size", 0) == 0:
            return

        fullscreen_alive = False
        try:
            fullscreen_alive = self._fullscreen_window is not None and self._fullscreen_window.winfo_exists()
        except Exception:
            fullscreen_alive = False
        if fullscreen_alive:
            self._render_fullscreen_frame(frame)
            return

        try:
            if self.review_tabs.get() != "영상 · 이벤트":
                return
        except Exception:
            pass

        now = time.monotonic()
        if not force and now - self._last_main_render_at < self._main_render_interval:
            return
        self._last_main_render_at = now

        try:
            if not self.video_label.winfo_manager():
                return
            vw = max(320, int(self.video_label.winfo_width()) - 12)
            vh = max(210, int(self.video_label.winfo_height()) - 12)
        except Exception:
            return
        max_w = min(920, vw)
        max_h = min(500, vh)
        image = self._fit_image(frame, max_w, max_h)
        if image is None:
            return
        self._player_photo = ImageTk.PhotoImage(image)
        try:
            self.video_label.configure(image=self._player_photo, text="")
        except Exception:
            pass

    def exit_video_fullscreen(self) -> None:
        super().exit_video_fullscreen()
        try:
            self.after_idle(self._redraw_latest_main_frame)
        except Exception:
            pass


if __name__ == "__main__":
    App().mainloop()
