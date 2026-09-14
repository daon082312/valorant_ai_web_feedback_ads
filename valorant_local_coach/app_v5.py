from __future__ import annotations

import time
import tkinter as tk
import tkinter.font as tkfont
from pathlib import Path
from tkinter import messagebox, ttk

import cv2
from PIL import Image, ImageTk

from app_v4 import App as ChatApp
from main import ACCENT, BG, BORDER, MUTED, PANEL, PANEL2, TEXT, WORK_DIR, save_config
from retention import cleanup_old_videos
from vision_analyzer import VisionAnalyzer


V5_DEFAULTS = {
    'clip_retention_hours': 24,
    'auto_delete_old_videos': True,
    'language': 'ko',
    'ui_font_family': 'Pretendard',
    'vision_analysis_fps': 4,
    'enemy_outline_color': 'red',
    'ally_outline_color': 'cyan',
    'minimap_enemy_color': 'red',
    'minimap_ally_color': 'cyan',
    'vision_min_component_area': 16,
    'show_vision_boxes_in_preview': True,
    'minimap_roi': {'x': 0.0, 'y': 0.0, 'w': 0.30, 'h': 0.36},
}

KO_EN = {
    '세션 시작': 'Start Session',
    '세션 종료': 'Stop Session',
    '스킬 키 설정': 'Skill Keys',
    '교전 클립': 'Fight Clips',
    '기준 세션 학습': 'Reference Learning',
    'AI 채팅': 'AI Chat',
    '미니맵': 'Minimap',
    '비전 설정': 'Vision Settings',
    '언어': 'Language',
    '패배 원인': 'Loss Analysis',
    '스킬 바인딩': 'Skill Bindings',
    '사격': 'Shots',
    '이동사격': 'Moving Shots',
    'SHIFT 사격': 'Shift Shots',
    'CTRL 사격': 'Ctrl Shots',
    '스킬 입력': 'Utility Inputs',
    '화면 미리보기': 'Screen Preview',
    '교전 후 피드백': 'Post-fight Feedback',
    '준비 완료': 'Ready',
}
EN_KO = {v: k for k, v in KO_EN.items()}


def _ensure_v5_config(app) -> None:
    changed = False
    for key, value in V5_DEFAULTS.items():
        if key not in app.config_data:
            app.config_data[key] = value
            changed = True
    if changed:
        save_config(app.config_data)


def _select_font(root: tk.Misc, preferred: str = 'Pretendard') -> str:
    try:
        families = {name.casefold(): name for name in tkfont.families(root)}
    except Exception:
        return 'Segoe UI'
    for wanted in (preferred, 'Pretendard', 'Inter', 'Noto Sans KR', 'Segoe UI'):
        hit = families.get(str(wanted).casefold())
        if hit:
            return hit
    return 'Segoe UI'


class App(ChatApp):
    """v5: retention, minimap review, local ally/enemy CV, headline and loss analysis."""

    def __init__(self):
        super().__init__()
        _ensure_v5_config(self)
        self.ui_font = _select_font(self, str(self.config_data.get('ui_font_family', 'Pretendard')))
        self.vision = VisionAnalyzer(WORK_DIR, self.config_data)
        self.minimap_window = None
        self.minimap_label = None
        self.minimap_photo = None
        self.minimap_status_var = None
        self._last_cleanup_report = {}
        self._latest_vision = None
        self._last_match_result = None
        self._apply_font_refresh()
        self.after(180, self._install_v5_controls)
        self.after(350, self._apply_language)
        self.after(450, self._cleanup_videos)
        self.after(600, self._poll_minimap_window)
        self.after(60 * 60 * 1000, self._periodic_cleanup)

    def _setup_styles(self) -> None:
        super()._setup_styles()
        family = _select_font(self, 'Pretendard')
        style = ttk.Style(self)
        style.configure('TLabel', font=(family, 10))
        style.configure('Eyebrow.TLabel', font=(family, 9, 'bold'))
        style.configure('Eyebrow.Card.TLabel', font=(family, 9, 'bold'))
        style.configure('Title.TLabel', font=(family, 28, 'bold'))
        style.configure('Subtitle.TLabel', font=(family, 10))
        style.configure('Metric.Card.TLabel', font=(family, 14, 'bold'))
        style.configure('MetricName.Card.TLabel', font=(family, 9, 'bold'))
        style.configure('Section.Card.TLabel', font=(family, 12, 'bold'))
        style.configure('Status.Card.TLabel', font=(family, 9, 'bold'))

    def _button(self, parent, text: str, command, primary: bool = False, width: int | None = None):
        button = super()._button(parent, text, command, primary=primary, width=width)
        try:
            button.configure(font=(_select_font(self, getattr(self, 'ui_font', 'Pretendard')), 10, 'bold'))
        except Exception:
            pass
        return button

    def _apply_font_refresh(self) -> None:
        for widget in self._walk_widgets(self):
            try:
                current = widget.cget('font')
            except Exception:
                continue
            try:
                f = tkfont.Font(font=current)
                size = int(f.cget('size') or 10)
                weight = str(f.cget('weight') or 'normal')
                widget.configure(font=(self.ui_font, size, weight))
            except Exception:
                pass

    def _install_v5_controls(self) -> None:
        target_master = None
        for widget in self._walk_widgets(self):
            if isinstance(widget, tk.Button) and str(widget.cget('text')) in {'기준 세션 학습', 'Reference Learning'}:
                target_master = widget.master
                break
        if target_master is None:
            return
        existing = {str(w.cget('text')) for w in target_master.winfo_children() if isinstance(w, tk.Button)}
        for text, command in (
            ('미니맵', self.open_minimap_window),
            ('비전 설정', self.open_vision_settings),
            ('언어', self.open_language_settings),
            ('패배 원인', self.open_loss_analysis),
        ):
            if text not in existing and KO_EN.get(text) not in existing:
                self._button(target_master, text, command).pack(side='left', padx=(8, 0))
        self._apply_language()

    def _thread_frame(self, sample) -> None:
        super()._thread_frame(sample)
        try:
            result = self.vision.analyze(sample.frame_bgr, sample.timestamp)
            if result is not None:
                self._latest_vision = result
                if self.config_data.get('show_vision_boxes_in_preview', True):
                    annotated = self.vision.latest_annotated()
                    if annotated is not None:
                        self.event_queue.put(('frame', annotated))
        except Exception as exc:
            print(f'[Vision] analyze failed: {type(exc).__name__}: {exc}')

    def start_session(self) -> None:
        self.vision.clear()
        super().start_session()

    def _save_fight_clip(self, fight) -> None:
        super()._save_fight_clip(fight)
        try:
            session_dir = getattr(self.recorder, '_session_dir', None)
            if session_dir:
                stamp = time.strftime('%H%M%S', time.localtime(fight.started_at))
                self.vision.save_fight_assets(fight.started_at, fight.ended_at, Path(session_dir), f'vision_{stamp}')
        except Exception as exc:
            print(f'[Vision] fight assets save failed: {type(exc).__name__}: {exc}')

    def _chat_context(self) -> dict:
        context = super()._chat_context()
        context['vision'] = self.vision.summary(120.0)
        context['language'] = self.config_data.get('language', 'ko')
        context['match_result'] = self._last_match_result
        context['loss_analysis'] = self._loss_analysis_data() if self._last_match_result == 'loss' else None
        return context

    def _cleanup_videos(self) -> None:
        if not self.config_data.get('auto_delete_old_videos', True):
            return
        try:
            self._last_cleanup_report = cleanup_old_videos(
                WORK_DIR,
                float(self.config_data.get('clip_retention_hours', 24)),
            )
        except Exception as exc:
            print(f'[Retention] cleanup failed: {type(exc).__name__}: {exc}')

    def _periodic_cleanup(self) -> None:
        self._cleanup_videos()
        self.after(60 * 60 * 1000, self._periodic_cleanup)

    def open_minimap_window(self) -> None:
        if self.minimap_window and self.minimap_window.winfo_exists():
            self.minimap_window.deiconify(); self.minimap_window.lift(); return
        win = tk.Toplevel(self)
        self.minimap_window = win
        win.title('VALORANT Local Coach · Minimap')
        win.geometry('520x620')
        win.minsize(420, 500)
        win.configure(bg=BG)
        root = tk.Frame(win, bg=BG, padx=18, pady=16); root.pack(fill='both', expand=True)
        tk.Label(root, text='MINIMAP REVIEW', bg=BG, fg=ACCENT, font=(self.ui_font, 9, 'bold')).pack(anchor='w')
        title = '미니맵 분석' if self.config_data.get('language') != 'en' else 'Minimap Analysis'
        tk.Label(root, text=title, bg=BG, fg=TEXT, font=(self.ui_font, 22, 'bold')).pack(anchor='w', pady=(3, 8))
        self.minimap_status_var = tk.StringVar(value='-')
        tk.Label(root, textvariable=self.minimap_status_var, bg=PANEL, fg=MUTED, font=(self.ui_font, 9), padx=10, pady=8, anchor='w', justify='left').pack(fill='x', pady=(0, 10))
        self.minimap_label = tk.Label(root, text='미니맵 캡처 대기 중', bg='#080a0e', fg=MUTED, font=(self.ui_font, 10))
        self.minimap_label.pack(fill='both', expand=True)
        note = ('아군/적 숫자는 색상 기반 로컬 CV 후보입니다. 낮은 신뢰도의 장면은 판독하지 않습니다.'
                if self.config_data.get('language') != 'en'
                else 'Ally/enemy counts are local color-CV candidates. Low-confidence scenes are left unclassified.')
        tk.Label(root, text=note, bg=BG, fg=MUTED, wraplength=470, justify='left', font=(self.ui_font, 9)).pack(anchor='w', pady=(10, 0))

    def _poll_minimap_window(self) -> None:
        try:
            if self.minimap_window and self.minimap_window.winfo_exists() and self.minimap_label:
                frame = self.vision.latest_minimap()
                summary = self.vision.summary(8.0)
                if frame is not None:
                    rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                    image = Image.fromarray(rgb); image.thumbnail((470, 440))
                    self.minimap_photo = ImageTk.PhotoImage(image=image)
                    self.minimap_label.configure(image=self.minimap_photo, text='')
                if self.minimap_status_var:
                    headline = summary.get('headline_score')
                    if self.config_data.get('language') == 'en':
                        text = (f"Screen enemy candidates: {summary.get('max_enemies', 0)} · allies: {summary.get('max_allies', 0)}\n"
                                f"Minimap enemy candidates: {summary.get('minimap_max_enemies', 0)} · allies: {summary.get('minimap_max_allies', 0)} · Headline: {headline if headline is not None else '--'}")
                    else:
                        text = (f"화면 적 후보 {summary.get('max_enemies', 0)} · 아군 후보 {summary.get('max_allies', 0)}\n"
                                f"미니맵 적 후보 {summary.get('minimap_max_enemies', 0)} · 아군 후보 {summary.get('minimap_max_allies', 0)} · 헤드라인 {headline if headline is not None else '--'}")
                    self.minimap_status_var.set(text)
        finally:
            self.after(600, self._poll_minimap_window)

    def open_vision_settings(self) -> None:
        win = tk.Toplevel(self); win.title('Vision Settings'); win.geometry('600x520'); win.resizable(False, False); win.configure(bg=BG); win.transient(self); win.grab_set()
        root = tk.Frame(win, bg=BG, padx=22, pady=20); root.pack(fill='both', expand=True)
        lang = self.config_data.get('language', 'ko')
        tk.Label(root, text='LOCAL VISION', bg=BG, fg=ACCENT, font=(self.ui_font, 9, 'bold')).pack(anchor='w')
        tk.Label(root, text='비전 설정' if lang != 'en' else 'Vision Settings', bg=BG, fg=TEXT, font=(self.ui_font, 22, 'bold')).pack(anchor='w', pady=(3, 12))
        card = tk.Frame(root, bg=PANEL, highlightthickness=1, highlightbackground=BORDER, padx=14, pady=14); card.pack(fill='x')

        enemy_var = tk.StringVar(value=str(self.config_data.get('enemy_outline_color', 'red')))
        ally_var = tk.StringVar(value=str(self.config_data.get('ally_outline_color', 'cyan')))
        mm_enemy_var = tk.StringVar(value=str(self.config_data.get('minimap_enemy_color', 'red')))
        mm_ally_var = tk.StringVar(value=str(self.config_data.get('minimap_ally_color', 'cyan')))
        boxes_var = tk.BooleanVar(value=bool(self.config_data.get('show_vision_boxes_in_preview', True)))
        for label, variable, values in (
            ('Enemy outline / 적 윤곽', enemy_var, ('red', 'purple', 'yellow')),
            ('Ally color / 아군 색', ally_var, ('cyan', 'green')),
            ('Minimap enemy / 미니맵 적', mm_enemy_var, ('red', 'purple', 'yellow')),
            ('Minimap ally / 미니맵 아군', mm_ally_var, ('cyan', 'green')),
        ):
            row = tk.Frame(card, bg=PANEL); row.pack(fill='x', pady=7)
            tk.Label(row, text=label, bg=PANEL, fg=MUTED, width=24, anchor='w', font=(self.ui_font, 9, 'bold')).pack(side='left')
            ttk.Combobox(row, textvariable=variable, values=values, state='readonly', width=18).pack(side='left')
        tk.Checkbutton(card, text='프로그램 미리보기에 판독 박스 표시 / Show boxes in app preview', variable=boxes_var, bg=PANEL, fg=TEXT, activebackground=PANEL, activeforeground=TEXT, selectcolor=PANEL2, font=(self.ui_font, 9)).pack(anchor='w', pady=(10, 0))
        tk.Label(root, text=('적 윤곽 색은 VALORANT 설정과 맞춰야 합니다. 이 판독은 색상/형태 휴리스틱이며 확실하지 않은 장면은 미분류합니다.' if lang != 'en' else 'Match the enemy outline color to VALORANT settings. This is a color/shape heuristic and leaves uncertain scenes unclassified.'), bg=BG, fg=MUTED, wraplength=550, justify='left', font=(self.ui_font, 9)).pack(anchor='w', pady=(12, 0))
        buttons = tk.Frame(root, bg=BG); buttons.pack(fill='x', side='bottom', pady=(14, 0))

        def save():
            self.config_data['enemy_outline_color'] = enemy_var.get()
            self.config_data['ally_outline_color'] = ally_var.get()
            self.config_data['minimap_enemy_color'] = mm_enemy_var.get()
            self.config_data['minimap_ally_color'] = mm_ally_var.get()
            self.config_data['show_vision_boxes_in_preview'] = bool(boxes_var.get())
            save_config(self.config_data); self.vision.update_config(self.config_data); win.destroy()

        self._button(buttons, '저장' if lang != 'en' else 'Save', save, primary=True, width=10).pack(side='right')
        self._button(buttons, '취소' if lang != 'en' else 'Cancel', win.destroy, width=10).pack(side='right', padx=(0, 8))

    def open_language_settings(self) -> None:
        win = tk.Toplevel(self); win.title('Language'); win.geometry('430x250'); win.resizable(False, False); win.configure(bg=BG); win.transient(self); win.grab_set()
        root = tk.Frame(win, bg=BG, padx=22, pady=20); root.pack(fill='both', expand=True)
        tk.Label(root, text='LANGUAGE', bg=BG, fg=ACCENT, font=(self.ui_font, 9, 'bold')).pack(anchor='w')
        tk.Label(root, text='언어 / Language', bg=BG, fg=TEXT, font=(self.ui_font, 22, 'bold')).pack(anchor='w', pady=(3, 14))
        var = tk.StringVar(value=str(self.config_data.get('language', 'ko')))
        ttk.Combobox(root, textvariable=var, values=('ko', 'en'), state='readonly', width=20).pack(anchor='w')
        tk.Label(root, text='ko = 한국어 · en = English', bg=BG, fg=MUTED, font=(self.ui_font, 9)).pack(anchor='w', pady=(8, 0))
        buttons = tk.Frame(root, bg=BG); buttons.pack(fill='x', side='bottom')

        def save():
            self.config_data['language'] = var.get(); save_config(self.config_data)
            if hasattr(self, 'chat_client'): self.chat_client.update_config(self.config_data)
            self._apply_language(); win.destroy()

        self._button(buttons, 'Save', save, primary=True, width=10).pack(side='right')

    def _apply_language(self) -> None:
        lang = str(self.config_data.get('language', 'ko'))
        mapping = KO_EN if lang == 'en' else EN_KO
        try:
            for widget in self._walk_widgets(self):
                try:
                    text = str(widget.cget('text'))
                except Exception:
                    continue
                if text in mapping:
                    widget.configure(text=mapping[text])
            self.title('VALORANT Local Coach · Movement & Utility' if lang == 'en' else 'VALORANT Local Coach · 무빙 & 스킬')
        except Exception:
            pass

    def _loss_analysis_data(self) -> dict:
        summary = self.engine.session_summary()
        movement = summary.get('movement_metrics') or {}
        vision = self.vision.summary(600.0)
        fight_count = int(summary.get('fight_count') or 0)
        skill_total = int((summary.get('skill_usage') or {}).get('total') or 0)
        causes = []

        def add(key: str, severity: float, evidence: str):
            causes.append({'key': key, 'severity': round(severity, 2), 'evidence': evidence})

        moving = float(movement.get('moving_shot_ratio') or 0)
        if moving >= 0.20:
            add('moving_shots', min(1.0, moving / 0.5), f'WASD중 사격 {moving * 100:.0f}%')
        crouch = float(movement.get('crouch_shot_ratio') or 0)
        if crouch >= 0.50:
            add('crouch_dependency', min(1.0, crouch), f'Ctrl 사격 {crouch * 100:.0f}%')
        avg = summary.get('average_fight_score')
        if avg is not None and float(avg) < 72:
            add('fight_execution', min(1.0, (80 - float(avg)) / 35), f'평균 무빙 점수 {float(avg):.1f}/100')
        hs = vision.get('headline_score')
        if hs is not None and vision.get('enemy_detection_frames', 0) >= 2 and float(hs) < 72:
            add('headline', min(1.0, (80 - float(hs)) / 45), f'헤드라인 {float(hs):.1f}/100, 오차 {vision.get("headline_error_px")}px')
        if fight_count >= 2 and skill_total / max(1, fight_count) < 0.5:
            add('utility_timing', 0.42, f'교전 {fight_count}회 대비 스킬 입력 {skill_total}회')
        causes.sort(key=lambda item: item['severity'], reverse=True)
        return {'causes': causes[:4], 'summary': summary, 'vision': vision, 'note': '측정된 지표로 가능한 요인을 정렬한 것이며 실제 라운드 패배의 인과를 확정하지 않습니다.'}

    def open_loss_analysis(self) -> None:
        self._last_match_result = 'loss'
        data = self._loss_analysis_data()
        lang = self.config_data.get('language', 'ko')
        labels_ko = {
            'moving_shots': '이동사격', 'crouch_dependency': '앉은 사격 의존',
            'fight_execution': '교전 실행', 'headline': '헤드라인 유지', 'utility_timing': '스킬 사용량/타이밍',
        }
        labels_en = {
            'moving_shots': 'Moving shots', 'crouch_dependency': 'Crouch dependence',
            'fight_execution': 'Fight execution', 'headline': 'Head-line placement', 'utility_timing': 'Utility usage/timing',
        }
        labels = labels_en if lang == 'en' else labels_ko
        causes = data['causes']
        if not causes:
            text = ('현재 측정 지표만으로 뚜렷한 패배 요인을 특정하기 어렵습니다. 더 많은 교전을 기록하거나 Vision 판독을 확보하세요.' if lang != 'en' else 'The current measured metrics do not identify a clear loss factor. Record more fights or obtain more vision detections.')
        else:
            lines = [f"{idx}. {labels.get(item['key'], item['key'])} · {item['evidence']}" for idx, item in enumerate(causes, 1)]
            tail = ('\n\n※ 측정값 기반의 가능성 분석이며 실제 패배 원인을 확정하지 않습니다.' if lang != 'en' else '\n\nMeasured-factor analysis only; it does not prove the causal reason for the loss.')
            text = '\n'.join(lines) + tail
        messagebox.showinfo('패배 원인 분석' if lang != 'en' else 'Loss Analysis', text)


if __name__ == '__main__':
    App().mainloop()
