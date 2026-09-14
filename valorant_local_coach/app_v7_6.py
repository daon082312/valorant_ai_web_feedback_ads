from __future__ import annotations

import queue
import tkinter as tk
from pathlib import Path

import cv2
import customtkinter as ctk
from PIL import Image, ImageTk

import app_v7_5 as v75
from async_video_player import AsyncVideoDecoder

ACCENT = v75.ACCENT
ACCENT_HOVER = "#ff5d69"
PANEL2 = v75.PANEL2
MUTED = v75.MUTED
FONT = v75.FONT


class App(v75.App):
    """v7.6: non-blocking video playback + dedicated video-only fullscreen."""

    def __init__(self):
        self._decoder = AsyncVideoDecoder(max_display_fps=24.0)
        self._decoder_poll_after = None
        self._latest_frame = None
        self._fullscreen_window = None
        self._fullscreen_label = None
        self._fullscreen_photo = None
        super().__init__()
        self.title("VALORANT Video Coach v7.6")
        self._install_fullscreen_controls()
        self.bind("<F11>", lambda _e: self.toggle_video_fullscreen())
        self.bind("<Escape>", lambda _e: self.exit_video_fullscreen())
        self._decoder_poll_after = self.after(25, self._poll_decoder)

    def _install_fullscreen_controls(self) -> None:
        controls = getattr(self, "play_btn", None)
        if controls is None:
            return
        parent = self.play_btn.master
        self.fullscreen_btn = ctk.CTkButton(
            parent, text="⛶ 전체화면", command=self.toggle_video_fullscreen,
            width=92, height=34, corner_radius=11, fg_color=PANEL2,
            hover_color="#354052", font=(FONT, 9, "bold"),
        )
        self.fullscreen_btn.pack(side="left", padx=(0, 6), after=self.play_btn)
        self.video_label.bind("<Double-Button-1>", lambda _e: self.toggle_video_fullscreen())
        self.video_label.configure(cursor="hand2")

    def _open_player(self, path: Path) -> None:
        self._stop_playback()
        self._player_path = Path(path)
        self._player_current = 0.0
        self._player_duration = 0.0
        self._player_fps = 30.0
        self._latest_frame = None
        self.timeline.configure(to=1.0, number_of_steps=1000)
        self.timeline.set(0.0)
        self.time_var.set("00:00.0 / 00:00.0")
        self.video_label.configure(image="", text="영상 불러오는 중…")
        self._decoder.open(path)

    def toggle_playback(self) -> None:
        if self._player_path is None:
            if self.selected_video is not None:
                self._open_player(self.selected_video)
            return
        if self._player_playing:
            self._stop_playback()
            return
        if self._player_duration > 0 and self._player_current >= self._player_duration - 0.05:
            self.seek_to(0.0)
        self._player_playing = True
        self.play_btn.configure(text="Ⅱ 일시정지")
        self._decoder.play()

    def _stop_playback(self) -> None:
        self._player_playing = False
        try:
            self._decoder.pause()
        except Exception:
            pass
        if hasattr(self, "play_btn"):
            try:
                self.play_btn.configure(text="▶ 재생")
            except Exception:
                pass

    def seek_to(self, seconds) -> None:
        if self._player_path is None:
            return
        self._stop_playback()
        target = max(0.0, min(float(seconds), self._player_duration if self._player_duration > 0 else float(seconds)))
        self._player_current = target
        self._update_time_ui()
        self._decoder.seek(target)

    def seek_relative(self, delta) -> None:
        self.seek_to(self._player_current + float(delta))

    def _player_tick(self) -> None:
        return

    def _poll_decoder(self) -> None:
        latest_frame = None
        latest_time = None
        try:
            while True:
                msg = self._decoder.messages.get_nowait()
                kind = msg[0]
                if kind == "meta":
                    self._player_fps = float(msg[1])
                    self._player_duration = float(msg[2])
                    self._player_step = int(msg[4])
                    self.timeline.configure(
                        to=max(0.1, self._player_duration),
                        number_of_steps=max(100, int(max(1.0, self._player_duration) * 10)),
                    )
                    self._update_time_ui()
                elif kind == "frame":
                    latest_time = float(msg[1])
                    latest_frame = msg[2]
                elif kind == "seeked":
                    self._player_current = float(msg[1])
                    self._update_time_ui()
                elif kind == "eof":
                    self._player_current = float(msg[1])
                    self._stop_playback()
                    self._update_time_ui()
                elif kind == "error":
                    self._stop_playback()
                    self.video_label.configure(image="", text=str(msg[1]))
        except queue.Empty:
            pass

        if latest_frame is not None:
            self._latest_frame = latest_frame
            if latest_time is not None:
                self._player_current = latest_time
            self._render_player_frame(latest_frame)
            self._update_time_ui()

        try:
            if self.winfo_exists():
                self._decoder_poll_after = self.after(25, self._poll_decoder)
        except Exception:
            pass

    @staticmethod
    def _fit_image(frame, max_w: int, max_h: int):
        if frame is None or frame.size == 0:
            return None
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        image = Image.fromarray(rgb)
        iw, ih = image.size
        if iw <= 0 or ih <= 0:
            return image
        scale = min(max_w / iw, max_h / ih)
        scale = max(0.05, scale)
        size = (max(1, int(iw * scale)), max(1, int(ih * scale)))
        if size != image.size:
            image = image.resize(size, Image.Resampling.LANCZOS)
        return image

    def _render_player_frame(self, frame) -> None:
        if frame is None or frame.size == 0:
            return
        max_w = min(1100, max(320, self.video_label.winfo_width() - 12))
        max_h = min(650, max(220, self.video_label.winfo_height() - 12))
        image = self._fit_image(frame, max_w, max_h)
        if image is not None:
            self._player_photo = ImageTk.PhotoImage(image)
            self.video_label.configure(image=self._player_photo, text="")
        self._render_fullscreen_frame(frame)

    def _render_fullscreen_frame(self, frame=None) -> None:
        if self._fullscreen_window is None or self._fullscreen_label is None:
            return
        try:
            if not self._fullscreen_window.winfo_exists():
                return
            source = frame if frame is not None else self._latest_frame
            if source is None:
                return
            sw = max(640, self._fullscreen_window.winfo_screenwidth())
            sh = max(360, self._fullscreen_window.winfo_screenheight())
            image = self._fit_image(source, sw, sh)
            if image is None:
                return
            self._fullscreen_photo = ImageTk.PhotoImage(image)
            self._fullscreen_label.configure(image=self._fullscreen_photo, text="")
        except Exception:
            pass

    def toggle_video_fullscreen(self) -> None:
        if self._fullscreen_window is not None:
            try:
                if self._fullscreen_window.winfo_exists():
                    self.exit_video_fullscreen()
                    return
            except Exception:
                pass
        if self._player_path is None:
            return

        win = tk.Toplevel(self)
        self._fullscreen_window = win
        win.configure(bg="black")
        win.title("VALORANT Video Coach · Video Fullscreen")
        win.attributes("-fullscreen", True)
        win.bind("<Escape>", lambda _e: self.exit_video_fullscreen())
        win.bind("<F11>", lambda _e: self.exit_video_fullscreen())
        win.bind("<Double-Button-1>", lambda _e: self.exit_video_fullscreen())
        win.protocol("WM_DELETE_WINDOW", self.exit_video_fullscreen)

        label = tk.Label(win, bg="black", fg="#98a2b3", text="영상", bd=0)
        label.pack(fill="both", expand=True)
        label.bind("<Double-Button-1>", lambda _e: self.exit_video_fullscreen())
        self._fullscreen_label = label
        self._render_fullscreen_frame()
        try:
            win.focus_force()
        except Exception:
            pass

    def exit_video_fullscreen(self) -> None:
        win = self._fullscreen_window
        self._fullscreen_window = None
        self._fullscreen_label = None
        self._fullscreen_photo = None
        if win is not None:
            try:
                if win.winfo_exists():
                    win.attributes("-fullscreen", False)
                    win.destroy()
            except Exception:
                pass

    def start_analysis(self) -> None:
        self._stop_playback()
        super().start_analysis()

    def _on_close(self) -> None:
        self.exit_video_fullscreen()
        self._stop_playback()
        if self._decoder_poll_after is not None:
            try:
                self.after_cancel(self._decoder_poll_after)
            except Exception:
                pass
            self._decoder_poll_after = None
        try:
            self._decoder.close()
        except Exception:
            pass
        self.destroy()


if __name__ == "__main__":
    App().mainloop()
