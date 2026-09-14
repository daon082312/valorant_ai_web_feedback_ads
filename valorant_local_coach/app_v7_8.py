from __future__ import annotations

import tkinter as tk

import customtkinter as ctk

import app_v7_7 as v77

ACCENT = v77.ACCENT
ACCENT_HOVER = v77.ACCENT_HOVER
PANEL2 = v77.PANEL2
MUTED = v77.MUTED
FONT = v77.FONT


class App(v77.App):
    """v7.8: isolated K/D correction and reliable borderless video fullscreen."""

    def __init__(self):
        self._kd_dialog = None
        super().__init__()
        self.title("VALORANT Video Coach v7.8")

    def _install_combat_correction_controls(self) -> None:
        if not hasattr(self, "event_list"):
            return
        parent = self.event_list.master
        try:
            self.event_list.pack_forget()
        except Exception:
            pass
        self.kd_status_var = tk.StringVar(value="자동 K/D · 분석 후 필요할 때 교정")
        bar = ctk.CTkFrame(parent, fg_color="#111720", corner_radius=11)
        bar.pack(fill="x", padx=7, pady=(0, 7))
        bar.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(bar, textvariable=self.kd_status_var, text_color=MUTED, font=(FONT, 8), anchor="w").grid(row=0, column=0, padx=(9, 5), pady=7, sticky="ew")
        ctk.CTkButton(bar, text="K/D 교정", command=self._open_kd_dialog, width=78, height=29, corner_radius=9, fg_color=PANEL2, hover_color="#354052", font=(FONT, 8, "bold")).grid(row=0, column=1, padx=(5, 7), pady=5, sticky="e")
        ctk.CTkLabel(parent, text="이벤트 이동 · 클릭하면 해당 초로 이동", text_color=MUTED, font=(FONT, 8, "bold"), anchor="w").pack(fill="x", padx=10, pady=(0, 3))
        self.event_list.pack(fill="both", expand=True, padx=7, pady=(0, 8))

    def _open_kd_dialog(self) -> None:
        if self._kd_dialog is not None:
            try:
                if self._kd_dialog.winfo_exists():
                    self._kd_dialog.lift(); self._kd_dialog.focus_force(); return
            except Exception:
                pass
        win = tk.Toplevel(self)
        self._kd_dialog = win
        win.title("K/D 교정")
        win.geometry("420x245")
        win.resizable(False, False)
        win.configure(bg="#0d1118")
        win.transient(self)
        win.protocol("WM_DELETE_WINDOW", self._close_kd_dialog)
        current = tk.StringVar(value=f"현재 영상 위치 · {self._fmt_time(self._player_current)}")
        status = tk.StringVar(value=self.kd_status_var.get())
        tk.Label(win, text="K/D 교정", bg="#0d1118", fg="white", font=(FONT, 18, "bold"), anchor="w").pack(fill="x", padx=18, pady=(18, 2))
        tk.Label(win, textvariable=current, bg="#0d1118", fg="#aeb6c2", font=(FONT, 10), anchor="w").pack(fill="x", padx=18)
        tk.Label(win, textvariable=status, bg="#0d1118", fg="#aeb6c2", font=(FONT, 9), anchor="w").pack(fill="x", padx=18, pady=(3, 12))
        row = tk.Frame(win, bg="#0d1118"); row.pack(fill="x", padx=18)
        tk.Button(row, text="+ 킬", command=lambda: self._kd_action("kill", current, status), bg="#ff4655", activebackground="#ff5d69", fg="white", activeforeground="white", relief="flat", bd=0, font=(FONT, 11, "bold"), pady=10).pack(side="left", fill="x", expand=True, padx=(0, 5))
        tk.Button(row, text="+ 데스", command=lambda: self._kd_action("death", current, status), bg="#7d3540", activebackground="#98424f", fg="white", activeforeground="white", relief="flat", bd=0, font=(FONT, 11, "bold"), pady=10).pack(side="left", fill="x", expand=True, padx=(5, 0))
        tk.Button(win, text="자동 K/D로 복원", command=lambda: self._kd_restore(status), bg="#222a35", activebackground="#354052", fg="white", activeforeground="white", relief="flat", bd=0, font=(FONT, 10, "bold"), pady=9).pack(fill="x", padx=18, pady=(10, 5))
        tk.Button(win, text="닫기", command=self._close_kd_dialog, bg="#151b24", activebackground="#2a3340", fg="#d8dde6", activeforeground="white", relief="flat", bd=0, font=(FONT, 10), pady=7).pack(fill="x", padx=18, pady=(0, 14))
        try:
            win.lift(); win.focus_force()
        except Exception:
            pass

    def _kd_action(self, kind: str, current_var, status_var) -> None:
        self._add_combat_event(kind)
        current_var.set(f"현재 영상 위치 · {self._fmt_time(self._player_current)}")
        status_var.set(self.kd_status_var.get())

    def _kd_restore(self, status_var) -> None:
        self._restore_auto_combat(); status_var.set(self.kd_status_var.get())

    def _close_kd_dialog(self) -> None:
        win = self._kd_dialog; self._kd_dialog = None
        if win is not None:
            try:
                if win.winfo_exists(): win.destroy()
            except Exception:
                pass

    def _set_playing(self, playing: bool) -> None:
        if self._player_path is None: return
        playing = bool(playing)
        if playing and self._player_duration > 0 and self._player_current >= self._player_duration - 0.05:
            self._player_current = 0.0; self._decoder.seek(0.0, resume=True)
        elif playing:
            self._decoder.play()
        else:
            self._decoder.pause()
        self._player_playing = playing; self._sync_play_buttons()

    def toggle_playback(self) -> None:
        self._set_playing(not bool(self._player_playing))

    def _stop_playback(self) -> None:
        self._set_playing(False)

    def seek_to(self, seconds) -> None:
        if self._player_path is None: return
        target = max(0.0, min(float(seconds), self._player_duration if self._player_duration > 0 else float(seconds)))
        resume = bool(self._player_playing)
        self._player_current = target; self._update_time_ui()
        self._decoder.seek(target, resume=resume)
        self._player_playing = resume; self._sync_play_buttons()

    def _seek_relative_keep_state(self, delta: float) -> None:
        self.seek_to(self._player_current + float(delta))

    def _fullscreen_seek_release(self, _event=None):
        if self._fullscreen_slider is None or self._fullscreen_slider_internal: return
        try: self.seek_to(float(self._fullscreen_slider.get()))
        except Exception: return

    def toggle_video_fullscreen(self) -> None:
        if self._fullscreen_window is not None:
            try:
                if self._fullscreen_window.winfo_exists(): self.exit_video_fullscreen(); return
            except Exception:
                pass
        if self._player_path is None: return
        win = tk.Toplevel(self); self._fullscreen_window = win
        win.configure(bg="black"); win.title("VALORANT Video Coach · 영상 전체화면")
        sw = max(800, win.winfo_screenwidth()); sh = max(600, win.winfo_screenheight())
        win.overrideredirect(True); win.geometry(f"{sw}x{sh}+0+0")
        try: win.attributes("-topmost", True)
        except Exception: pass
        win.grid_rowconfigure(0, weight=1); win.grid_rowconfigure(1, weight=0); win.grid_columnconfigure(0, weight=1)
        video_area = tk.Frame(win, bg="black", bd=0, highlightthickness=0); video_area.grid(row=0, column=0, sticky="nsew"); video_area.grid_rowconfigure(0, weight=1); video_area.grid_columnconfigure(0, weight=1)
        label = tk.Label(video_area, bg="black", fg="#98a2b3", text="영상", bd=0); label.grid(row=0, column=0, sticky="nsew"); self._fullscreen_label = label
        controls = tk.Frame(win, bg="#0d1118", height=94, bd=0, highlightthickness=0); controls.grid(row=1, column=0, sticky="ew"); controls.grid_columnconfigure(4, weight=1); self._fullscreen_controls = controls
        def mk(text, command, width=9, accent=False):
            return tk.Button(controls, text=text, command=command, width=width, bg="#ff4655" if accent else "#222a35", activebackground="#ff5d69" if accent else "#354052", fg="white", activeforeground="white", relief="flat", bd=0, font=(FONT, 10, "bold"), cursor="hand2", padx=8, pady=8, takefocus=True)
        mk("← 돌아가기", self.exit_video_fullscreen, width=11).grid(row=0, column=0, padx=(12, 5), pady=(9, 5), sticky="w")
        mk("-5초", lambda: self.seek_to(self._player_current - 5.0), width=7).grid(row=0, column=1, padx=5, pady=(9, 5), sticky="w")
        self._fullscreen_play_btn = mk("▶ 재생", self.toggle_playback, width=10, accent=True); self._fullscreen_play_btn.grid(row=0, column=2, padx=5, pady=(9, 5), sticky="w")
        mk("+5초", lambda: self.seek_to(self._player_current + 5.0), width=7).grid(row=0, column=3, padx=5, pady=(9, 5), sticky="w")
        self._fullscreen_time_var = tk.StringVar(value=f"{self._fmt_time(self._player_current)} / {self._fmt_time(self._player_duration)}")
        tk.Label(controls, textvariable=self._fullscreen_time_var, bg="#0d1118", fg="#d8dde6", font=(FONT, 10, "bold"), anchor="e").grid(row=0, column=5, padx=(8, 14), pady=(9, 5), sticky="e")
        slider = tk.Scale(controls, from_=0.0, to=max(0.1, self._player_duration), orient="horizontal", showvalue=False, resolution=0.1, bg="#0d1118", fg="white", troughcolor="#2a3340", activebackground="#ff4655", highlightthickness=0, bd=0, sliderlength=20)
        slider.grid(row=1, column=0, columnspan=6, padx=14, pady=(0, 9), sticky="ew"); slider.set(self._player_current); slider.bind("<ButtonRelease-1>", self._fullscreen_seek_release); self._fullscreen_slider = slider
        for widget in (win, video_area, label, controls):
            widget.bind("<Escape>", lambda _e: self.exit_video_fullscreen()); widget.bind("<F11>", lambda _e: self.exit_video_fullscreen()); widget.bind("<space>", self._fullscreen_space); widget.bind("<Left>", lambda _e: self.seek_to(self._player_current - 5.0)); widget.bind("<Right>", lambda _e: self.seek_to(self._player_current + 5.0))
        label.bind("<Double-Button-1>", lambda _e: self.exit_video_fullscreen())
        self._sync_play_buttons(); self._update_time_ui(); self._render_fullscreen_frame()
        try: win.lift(); win.focus_force()
        except Exception: pass

    def exit_video_fullscreen(self) -> None:
        win = self._fullscreen_window
        self._fullscreen_window = None; self._fullscreen_label = None; self._fullscreen_photo = None; self._fullscreen_controls = None; self._fullscreen_slider = None; self._fullscreen_time_var = None; self._fullscreen_play_btn = None
        if win is not None:
            try:
                if win.winfo_exists(): win.destroy()
            except Exception: pass
        try:
            self.review_tabs.set("영상 · 이벤트"); self.deiconify(); self.lift(); self.focus_force()
        except Exception: pass

    def _on_close(self) -> None:
        self._close_kd_dialog(); super()._on_close()


if __name__ == "__main__":
    App().mainloop()
