from __future__ import annotations

import tkinter as tk
from tkinter import messagebox

import customtkinter as ctk

from app_v6_6 import App as V66App
from input_tracker import normalize_binding
from main import ACCENT, BG, BORDER, MUTED, PANEL, PANEL2, TEXT, save_config


RECORDING_CONFIG_KEYS = (
    "record_fight_clips",
    "fight_clip_pre_seconds",
    "fight_clip_post_seconds",
    "fight_record_buffer_seconds",
    "fight_record_jpeg_quality",
    "record_buffer_fps",
    "record_width",
    "clip_retention_hours",
    "auto_delete_old_videos",
)


class App(V66App):
    """v6.7: recording settings/config removed; manual video review remains available."""

    def __init__(self):
        super().__init__()
        self._strip_recording_config()
        try:
            if hasattr(self, "round_clips_btn"):
                self.round_clips_btn.pack_forget()
        except Exception:
            pass

    def _strip_recording_config(self) -> None:
        changed = False
        for key in RECORDING_CONFIG_KEYS:
            if key in self.config_data:
                self.config_data.pop(key, None)
                changed = True
        if changed:
            save_config(self.config_data)

    def open_settings(self, tab: str = "general") -> None:
        if self._settings_window is not None and self._settings_window.winfo_exists():
            self._settings_window.lift()
            return

        lang = "en" if self.config_data.get("language") == "en" else "ko"
        names = {"general": "General" if lang == "en" else "일반", "skills": "Skills" if lang == "en" else "스킬", "vision": "Vision" if lang == "en" else "비전", "ai": "AI"}
        win = ctk.CTkToplevel(self)
        self._settings_window = win
        win.title("VALORANT Local Coach · Settings")
        win.geometry("760x650")
        win.minsize(700, 590)
        win.configure(fg_color=BG)
        win.transient(self)
        win.grab_set()

        head = ctk.CTkFrame(win, fg_color="transparent")
        head.pack(fill="x", padx=22, pady=(20, 8))
        ctk.CTkLabel(head, text="SETTINGS", text_color=ACCENT, font=("Segoe UI", 12, "bold")).pack(anchor="w")
        ctk.CTkLabel(head, text="설정" if lang == "ko" else "Settings", text_color=TEXT, font=("Segoe UI", 26, "bold")).pack(anchor="w")

        tabs = ctk.CTkTabview(win, fg_color=PANEL, segmented_button_fg_color=PANEL2, segmented_button_selected_color=ACCENT, segmented_button_selected_hover_color="#ff5b68", corner_radius=20, border_width=1, border_color=BORDER)
        tabs.pack(fill="both", expand=True, padx=20, pady=(4, 12))
        frames = {key: tabs.add(names[key]) for key in ("general", "skills", "vision", "ai")}
        tabs.set(names.get(tab, names["general"]))

        general = frames["general"]
        language_var = tk.StringVar(value=str(self.config_data.get("language", "ko")))
        mode_override_var = tk.StringVar(value=str(self.config_data.get("game_mode_override", "auto")))
        preview_var = tk.StringVar(value=str(self.config_data.get("preview_fps", 5)))
        vision_fps_var = tk.StringVar(value=str(self.config_data.get("vision_analysis_fps", 3)))
        training_var = tk.BooleanVar(value=bool(self.config_data.get("save_training_frames", False)))
        ammo_detect_var = tk.BooleanVar(value=bool(self.config_data.get("ammo_hud_detection", True)))
        ammo_fps_var = tk.StringVar(value=str(self.config_data.get("ammo_hud_detection_fps", 12)))
        ammo_conf_var = tk.StringVar(value=str(self.config_data.get("ammo_hud_min_confidence", 0.53)))

        self._settings_row_option(general, "Language / 언어", language_var, ["ko", "en"])
        self._settings_row_option(general, "Game mode / 게임 모드", mode_override_var, ["auto", "normal", "brawl"])
        self._settings_row_option(general, "Preview FPS / 미리보기 FPS", preview_var, ["1", "2", "3", "4", "5", "6", "8"])
        self._settings_row_option(general, "Vision FPS", vision_fps_var, ["2", "3", "4", "5"])
        self._settings_switch(general, "Save training frames / 학습 프레임 저장 (CPU 사용 증가)", training_var)
        self._settings_switch(general, "Ammo HUD shot count / 탄약 HUD 실제 사격 감지", ammo_detect_var)
        self._settings_row_option(general, "Ammo HUD FPS", ammo_fps_var, ["8", "10", "12", "15"])
        self._settings_row_entry(general, "Ammo OCR confidence / 탄약 판독 신뢰도", ammo_conf_var)

        skills = frames["skills"]
        skill_entries = {}
        ctk.CTkLabel(skills, text=("키는 후보 슬롯을 알려줄 뿐입니다. HUD가 실제로 변해야 사용으로 기록됩니다." if lang == "ko" else "Keys only identify a candidate slot. A use is counted only after a HUD state change."), text_color=MUTED, wraplength=620, justify="left", font=("Segoe UI", 12)).pack(anchor="w", padx=16, pady=(16, 10))
        for slot in ("skill1", "skill2", "skill3", "ultimate"):
            item = self.config_data.get("skill_bindings", {}).get(slot, {})
            row = ctk.CTkFrame(skills, fg_color=PANEL2, corner_radius=14)
            row.pack(fill="x", padx=16, pady=5)
            label_entry = ctk.CTkEntry(row, width=300, corner_radius=10)
            label_entry.insert(0, str(item.get("label", slot)))
            label_entry.pack(side="left", padx=10, pady=10, fill="x", expand=True)
            key_entry = ctk.CTkEntry(row, width=150, corner_radius=10)
            key_entry.insert(0, str(item.get("key", "")))
            key_entry.pack(side="right", padx=10, pady=10)
            skill_entries[slot] = (label_entry, key_entry)

        hud_detect_var = tk.BooleanVar(value=bool(self.config_data.get("skill_hud_use_detection", True)))
        hud_threshold_var = tk.StringVar(value=str(self.config_data.get("skill_hud_change_threshold", 8.0)))
        self._settings_switch(skills, "HUD-confirmed use detection / HUD 실제 사용 확인", hud_detect_var)
        self._settings_row_entry(skills, "HUD change threshold / HUD 변화 임계값", hud_threshold_var)

        vision = frames["vision"]
        enemy_var = tk.StringVar(value=str(self.config_data.get("enemy_outline_color", "red")))
        ally_var = tk.StringVar(value=str(self.config_data.get("ally_outline_color", "cyan")))
        mm_enemy_var = tk.StringVar(value=str(self.config_data.get("minimap_enemy_color", "red")))
        mm_ally_var = tk.StringVar(value=str(self.config_data.get("minimap_ally_color", "cyan")))
        boxes_var = tk.BooleanVar(value=bool(self.config_data.get("show_vision_boxes_in_preview", True)))
        self._settings_row_option(vision, "Enemy outline / 적 윤곽", enemy_var, ["red", "purple", "yellow"])
        self._settings_row_option(vision, "Ally color / 아군 색", ally_var, ["cyan", "green"])
        self._settings_row_option(vision, "Minimap enemy / 미니맵 적", mm_enemy_var, ["red", "purple", "yellow"])
        self._settings_row_option(vision, "Minimap ally / 미니맵 아군", mm_ally_var, ["cyan", "green"])
        self._settings_switch(vision, "Show detection boxes in preview / 판독 박스 표시", boxes_var)

        ai = frames["ai"]
        ai_enabled_var = tk.BooleanVar(value=bool(self.config_data.get("local_ai_enabled", True)))
        ai_url_var = tk.StringVar(value=str(self.config_data.get("ollama_url", "http://127.0.0.1:11434")))
        ai_model_var = tk.StringVar(value=str(self.config_data.get("ollama_model", "qwen3:4b")))
        self._settings_switch(ai, "Use local Ollama AI / 로컬 Ollama AI 사용", ai_enabled_var)
        self._settings_row_entry(ai, "Ollama URL", ai_url_var)
        self._settings_row_entry(ai, "Model / 모델", ai_model_var)

        footer = ctk.CTkFrame(win, fg_color="transparent")
        footer.pack(fill="x", padx=20, pady=(0, 18))

        def save_all() -> None:
            try:
                hud_threshold = max(2.0, float(hud_threshold_var.get()))
                preview_fps = max(1, int(preview_var.get()))
                vision_fps = max(1, int(vision_fps_var.get()))
                ammo_fps = max(6, int(float(ammo_fps_var.get())))
                ammo_conf = max(0.35, min(0.95, float(ammo_conf_var.get())))
            except ValueError:
                messagebox.showerror("Settings", "숫자 설정값을 확인해 주세요. / Check numeric settings.", parent=win)
                return

            new_bindings = {}
            used = set()
            for slot, (label_entry, key_entry) in skill_entries.items():
                label = label_entry.get().strip() or slot
                key = normalize_binding(key_entry.get())
                if not key:
                    messagebox.showerror("Settings", f"{label}: key is empty", parent=win)
                    return
                if key in used:
                    messagebox.showerror("Settings", f"Duplicate key: {key.upper()}", parent=win)
                    return
                used.add(key)
                new_bindings[slot] = {"label": label, "key": key}

            self.config_data.update({"language": language_var.get(), "game_mode_override": mode_override_var.get(), "preview_fps": preview_fps, "vision_analysis_fps": vision_fps, "save_training_frames": bool(training_var.get()), "ammo_hud_detection": bool(ammo_detect_var.get()), "ammo_hud_detection_fps": ammo_fps, "ammo_hud_min_confidence": ammo_conf, "skill_bindings": new_bindings, "skill_hud_use_detection": bool(hud_detect_var.get()), "skill_hud_change_threshold": hud_threshold, "enemy_outline_color": enemy_var.get(), "ally_outline_color": ally_var.get(), "minimap_enemy_color": mm_enemy_var.get(), "minimap_ally_color": mm_ally_var.get(), "show_vision_boxes_in_preview": bool(boxes_var.get()), "local_ai_enabled": bool(ai_enabled_var.get()), "ollama_url": ai_url_var.get().strip().rstrip("/"), "ollama_model": ai_model_var.get().strip() or "qwen3:4b"})
            self._strip_recording_config()
            save_config(self.config_data)
            self.input_tracker.set_skill_bindings(new_bindings)
            self.skill_detector.update_config(self.config_data)
            if hasattr(self, "ammo_detector"):
                self.ammo_detector.update_config(self.config_data)
            self.vision.update_config(self.config_data)
            if hasattr(self, "mode_detector"):
                self.mode_detector.update_config(self.config_data)
            if hasattr(self, "chat_client"):
                self.chat_client.update_config(self.config_data)
            self.bindings_var.set(self._bindings_text())
            self._apply_language()
            self._settings_window = None
            win.destroy()

        ctk.CTkButton(footer, text="Save / 저장", command=save_all, width=120, height=40, corner_radius=14, fg_color=ACCENT, hover_color="#ff5b68", font=("Segoe UI", 12, "bold")).pack(side="right")
        ctk.CTkButton(footer, text="Cancel / 취소", command=win.destroy, width=110, height=40, corner_radius=14, fg_color=PANEL2, hover_color="#2a3341", font=("Segoe UI", 12, "bold")).pack(side="right", padx=(0, 8))

        def on_close() -> None:
            self._settings_window = None
            win.destroy()

        win.protocol("WM_DELETE_WINDOW", on_close)


if __name__ == "__main__":
    App().mainloop()
