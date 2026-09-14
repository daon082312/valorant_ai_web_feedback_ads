from __future__ import annotations

import tkinter as tk

import customtkinter as ctk

import app_v7_6 as v76

ACCENT = v76.ACCENT
ACCENT_HOVER = v76.ACCENT_HOVER
PANEL2 = v76.PANEL2
MUTED = v76.MUTED
FONT = v76.FONT


class App(v76.App):
    """v7.7: non-overlapping controls and a fully controlled video-only fullscreen player."""

    def __init__(self):
        self._fullscreen_time_var = None
        self._fullscreen_slider = None
        self._fullscreen_play_btn = None
        self._fullscreen_controls = None
        self._fullscreen_slider_internal = False
        super().__init__()
        self.title("VALORANT Video Coach v7.7")

    def _install_fullscreen_controls(self) -> None:
        old_play = getattr(self, "play_btn", None)
        if old_play is None:
            return
        parent = old_play.master
        for child in list(parent.winfo_children()):
            try:
                child.destroy()
            except Exception:
                pass
        for col in range(5):
            parent.grid_columnconfigure(col, weight=0)
        parent.grid_columnconfigure(4, weight=1)
        self.back5_btn = ctk.CTkButton(parent, text="-5초", command=lambda: self._seek_relative_keep_state(-5.0), width=62, height=34, corner_radius=11, fg_color=PANEL2, hover_color="#354052", font=(FONT, 9, "bold"))
        self.back5_btn.grid(row=0, column=0, padx=(0, 5), pady=(1, 2), sticky="w")
        self.play_btn = ctk.CTkButton(parent, text="▶ 재생", command=self.toggle_playback, width=86, height=34, corner_radius=11, fg_color=ACCENT, hover_color=ACCENT_HOVER, font=(FONT, 9, "bold"))
        self.play_btn.grid(row=0, column=1, padx=5, pady=(1, 2), sticky="w")
        self.forward5_btn = ctk.CTkButton(parent, text="+5초", command=lambda: self._seek_relative_keep_state(5.0), width=62, height=34, corner_radius=11, fg_color=PANEL2, hover_color="#354052", font=(FONT, 9, "bold"))
        self.forward5_btn.grid(row=0, column=2, padx=5, pady=(1, 2), sticky="w")
        self.fullscreen_btn = ctk.CTkButton(parent, text="⛶ 영상 전체화면", command=self.toggle_video_fullscreen, width=118, height=34, corner_radius=11, fg_color=PANEL2, hover_color="#354052", font=(FONT, 9, "bold"))
        self.fullscreen_btn.grid(row=0, column=3, padx=(5, 8), pady=(1, 2), sticky="w")
        ctk.CTkLabel(parent, textvariable=self.time_var, text_color=MUTED, font=(FONT, 9, "bold"), anchor="e").grid(row=0, column=4, padx=(5, 2), pady=(1, 2), sticky="e")
        self.video_label.bind("<Double-Button-1>", lambda _e: self.toggle_video_fullscreen())
        self.video_label.configure(cursor="hand2")

    def _install_combat_correction_controls(self) -> None:
        if not hasattr(self, "event_list"):
            return
        parent = self.event_list.master
        try:
            self.event_list.pack_forget()
        except Exception:
            pass
        self.kd_status_var = tk.StringVar(value="자동 K/D · 분석 후 교정 가능")
        box = ctk.CTkFrame(parent, fg_color="#111720", corner_radius=12)
        box.pack(fill="x", padx=7, pady=(0, 7))
        ctk.CTkLabel(box, text="K/D 교정", text_color="#f6f7fb", font=(FONT, 9, "bold"), anchor="w").pack(fill="x", padx=9, pady=(8, 0))
        ctk.CTkLabel(box, text="현재 영상 위치에 이벤트 추가", text_color=MUTED, font=(FONT, 8), anchor="w").pack(fill="x", padx=9, pady=(0, 5))
        add_row = ctk.CTkFrame(box, fg_color="transparent")
        add_row.pack(fill="x", padx=7, pady=(0, 5))
        add_row.grid_columnconfigure(0, weight=1)
        add_row.grid_columnconfigure(1, weight=1)
        ctk.CTkButton(add_row, text="+ 킬", command=lambda: self._add_combat_event("kill"), height=31, corner_radius=9, fg_color=ACCENT, hover_color=ACCENT_HOVER, font=(FONT, 8, "bold")).grid(row=0, column=0, padx=(0, 3), sticky="ew")
        ctk.CTkButton(add_row, text="+ 데스", command=lambda: self._add_combat_event("death"), height=31, corner_radius=9, fg_color="#7d3540", hover_color="#98424f", font=(FONT, 8, "bold")).grid(row=0, column=1, padx=(3, 0), sticky="ew")
        ctk.CTkButton(box, text="자동 K/D로 복원", command=self._restore_auto_combat, height=29, corner_radius=9, fg_color=PANEL2, hover_color="#354052", font=(FONT, 8, "bold")).pack(fill="x", padx=7, pady=(0, 5))
        ctk.CTkLabel(box, textvariable=self.kd_status_var, text_color=MUTED, font=(FONT, 8), anchor="w", justify="left", wraplength=180).pack(fill="x", padx=9, pady=(0, 8))
        ctk.CTkLabel(parent, text="감지 이벤트 · 클릭하면 해당 초로 이동", text_color=MUTED, font=(FONT, 8, "bold"), anchor="w").pack(fill="x", padx=10, pady=(0, 3))
        self.event_list.pack(fill="both", expand=True, padx=7, pady=(0, 8))

    def _sync_play_buttons(self) -> None:
        text = "Ⅱ 일시정지" if self._player_playing else "▶ 재생"
        for btn in (getattr(self, "play_btn", None), self._fullscreen_play_btn):
            if btn is not None:
                try:
                    btn.configure(text=text)
                except Exception:
                    pass

    def toggle_playback(self) -> None:
        super().toggle_playback()
        self._sync_play_buttons()

    def _stop_playback(self) -> None:
        super()._stop_playback()
        self._sync_play_buttons()

    def _seek_relative_keep_state(self, delta: float) -> None:
        if self._player_path is None:
            return
        was_playing = bool(self._player_playing)
        target = self._player_current + float(delta)
        self.seek_to(target)
        if was_playing:
            self._player_playing = True
            try:
                self._decoder.play()
            except Exception:
                self._player_playing = False
            self._sync_play_buttons()

    def _update_time_ui(self) -> None:
        super()._update_time_ui()
        if self._fullscreen_time_var is not None:
            try:
                self._fullscreen_time_var.set(f"{self._fmt_time(self._player_current)} / {self._fmt_time(self._player_duration)}")
            except Exception:
                pass
        if self._fullscreen_slider is not None:
            try:
                self._fullscreen_slider_internal = True
                self._fullscreen_slider.configure(to=max(0.1, self._player_duration))
                self._fullscreen_slider.set(max(0.0, min(self._player_duration, self._player_current)))
            except Exception:
                pass
            finally:
                self._fullscreen_slider_internal = False

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
        win.title("VALORANT Video Coach · 영상 전체화면")
        win.attributes("-fullscreen", True)
        win.grid_rowconfigure(0, weight=1)
        win.grid_rowconfigure(1, weight=0)
        win.grid_columnconfigure(0, weight=1)
        win.protocol("WM_DELETE_WINDOW", self.exit_video_fullscreen)
        video_area = tk.Frame(win, bg="black", bd=0, highlightthickness=0)
        video_area.grid(row=0, column=0, sticky="nsew")
        video_area.grid_rowconfigure(0, weight=1)
        video_area.grid_columnconfigure(0, weight=1)
        label = tk.Label(video_area, bg="black", fg="#98a2b3", text="영상", bd=0)
        label.grid(row=0, column=0, sticky="nsew")
        self._fullscreen_label = label
        controls = tk.Frame(win, bg="#0d1118", height=74, bd=0, highlightthickness=0)
        controls.grid(row=1, column=0, sticky="ew")
        controls.grid_columnconfigure(5, weight=1)
        self._fullscreen_controls = controls

        def button(text, command, width=9, accent=False):
            return tk.Button(controls, text=text, command=command, width=width, bg="#ff4655" if accent else "#222a35", activebackground="#ff5d69" if accent else "#354052", fg="white", activeforeground="white", relief="flat", bd=0, font=(FONT, 10, "bold"), cursor="hand2", padx=8, pady=8)

        button("← 돌아가기", self.exit_video_fullscreen, width=11).grid(row=0, column=0, padx=(12, 5), pady=(9, 5))
        button("-5초", lambda: self._seek_relative_keep_state(-5.0), width=7).grid(row=0, column=1, padx=5, pady=(9, 5))
        self._fullscreen_play_btn = button("▶ 재생", self.toggle_playback, width=10, accent=True)
        self._fullscreen_play_btn.grid(row=0, column=2, padx=5, pady=(9, 5))
        button("+5초", lambda: self._seek_relative_keep_state(5.0), width=7).grid(row=0, column=3, padx=5, pady=(9, 5))
        self._fullscreen_time_var = tk.StringVar(value=f"{self._fmt_time(self._player_current)} / {self._fmt_time(self._player_duration)}")
        tk.Label(controls, textvariable=self._fullscreen_time_var, bg="#0d1118", fg="#d8dde6", font=(FONT, 10, "bold"), anchor="e").grid(row=0, column=6, padx=(8, 14), pady=(9, 5), sticky="e")
        slider = tk.Scale(controls, from_=0.0, to=max(0.1, self._player_duration), orient="horizontal", showvalue=False, resolution=0.1, bg="#0d1118", fg="white", troughcolor="#2a3340", activebackground="#ff4655", highlightthickness=0, bd=0, sliderlength=18)
        slider.grid(row=1, column=0, columnspan=7, padx=14, pady=(0, 8), sticky="ew")
        slider.set(self._player_current)
        slider.bind("<ButtonRelease-1>", self._fullscreen_seek_release)
        self._fullscreen_slider = slider
        win.bind("<Escape>", lambda _e: self.exit_video_fullscreen())
        win.bind("<F11>", lambda _e: self.exit_video_fullscreen())
        win.bind("<space>", self._fullscreen_space)
        win.bind("<Left>", lambda _e: self._seek_relative_keep_state(-5.0))
        win.bind("<Right>", lambda _e: self._seek_relative_keep_state(5.0))
        label.bind("<Double-Button-1>", lambda _e: self.exit_video_fullscreen())
        self._sync_play_buttons()
        self._update_time_ui()
        self._render_fullscreen_frame()
        try:
            win.lift()
            win.focus_force()
        except Exception:
            pass

    def _fullscreen_space(self, _event=None):
        self.toggle_playback()
        return "break"

    def _fullscreen_seek_release(self, _event=None):
        if self._fullscreen_slider is None or self._fullscreen_slider_internal:
            return
        was_playing = bool(self._player_playing)
        try:
            target = float(self._fullscreen_slider.get())
        except Exception:
            return
        self.seek_to(target)
        if was_playing:
            self._player_playing = True
            try:
                self._decoder.play()
            except Exception:
                self._player_playing = False
        self._sync_play_buttons()

    def _render_fullscreen_frame(self, frame=None) -> None:
        if self._fullscreen_window is None or self._fullscreen_label is None:
            return
        try:
            if not self._fullscreen_window.winfo_exists():
                return
            source = frame if frame is not None else self._latest_frame
            if source is None:
                return
            max_w = max(640, self._fullscreen_label.winfo_width())
            max_h = max(360, self._fullscreen_label.winfo_height())
            image = self._fit_image(source, max_w, max_h)
            if image is None:
                return
            from PIL import ImageTk
            self._fullscreen_photo = ImageTk.PhotoImage(image)
            self._fullscreen_label.configure(image=self._fullscreen_photo, text="")
        except Exception:
            pass

    def exit_video_fullscreen(self) -> None:
        win = self._fullscreen_window
        self._fullscreen_window = None
        self._fullscreen_label = None
        self._fullscreen_photo = None
        self._fullscreen_controls = None
        self._fullscreen_slider = None
        self._fullscreen_time_var = None
        self._fullscreen_play_btn = None
        if win is not None:
            try:
                if win.winfo_exists():
                    win.attributes("-fullscreen", False)
                    win.destroy()
            except Exception:
                pass
        try:
            self.lift()
            self.focus_force()
        except Exception:
            pass


if __name__ == "__main__":
    App().mainloop()
