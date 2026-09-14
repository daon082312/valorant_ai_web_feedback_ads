from __future__ import annotations

import json
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox

import cv2
import customtkinter as ctk
from PIL import Image, ImageTk

from personal_learning import PersonalCoachProfile
from video_chat import VideoCoachChat
from video_review import analyze_video

ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "config.json"
BG = "#080a0f"; PANEL = "#11151d"; PANEL2 = "#171d27"; BORDER = "#2a3340"
TEXT = "#f6f7fb"; MUTED = "#98a2b3"; ACCENT = "#ff4655"; ACCENT_HOVER = "#ff5d69"; GOOD = "#5dd39e"; FONT = "Malgun Gothic"

DEFAULT_CONFIG = {
    "language": "ko", "video_review_fps": 4, "video_skill_change_threshold": 11.0,
    "video_skill_cooldown_seconds": 1.4, "enemy_outline_color": "red", "ally_outline_color": "cyan",
    "minimap_enemy_color": "red", "minimap_ally_color": "cyan", "vision_min_component_area": 16,
    "minimap_roi": {"x": 0.0, "y": 0.0, "w": 0.30, "h": 0.36}, "ammo_hud_detection": True,
    "ammo_hud_detection_fps": 10, "ammo_hud_min_confidence": 0.53,
    "ammo_hud_roi": {"x": 0.875, "y": 0.865, "w": 0.115, "h": 0.12}, "combat_event_detection": True,
    "combat_event_fps": 4, "kill_hud_change_threshold": 10.0, "death_hud_change_threshold": 13.0,
    "kill_event_cooldown_seconds": 1.6, "death_event_cooldown_seconds": 4.0,
    "local_ai_enabled": True, "ollama_url": "http://127.0.0.1:11434", "ollama_model": "qwen3:4b",
    "ollama_timeout_seconds": 45, "personal_learning_enabled": True,
}


def load_config() -> dict:
    try: raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8")) if CONFIG_PATH.exists() else {}
    except Exception: raw = {}
    cfg = {**DEFAULT_CONFIG, **raw}; CONFIG_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8"); return cfg


def save_config(cfg: dict) -> None:
    CONFIG_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")


class App(ctk.CTk):
    def __init__(self):
        super().__init__(); ctk.set_appearance_mode("dark")
        self.title("VALORANT Video Coach"); self.geometry("1380x900"); self.minsize(1120, 760); self.configure(fg_color=BG)
        self.config_data = load_config(); self.chat_client = VideoCoachChat(self.config_data); self.personal_profile = PersonalCoachProfile(ROOT)
        self.selected_video = None; self.last_report = None; self.analysis_busy = False; self.chat_busy = False; self.chat_history = []; self._thumb_photo = None; self._settings_window = None
        self.metric_vars = {k: tk.StringVar(value="—") for k in ("tier", "aim", "headline", "movement", "skill", "kd")}
        self.file_var = tk.StringVar(value="분석할 VALORANT 영상을 선택하세요"); self.status_var = tk.StringVar(value="대기 중 · 실시간 캡처 없음"); self.progress_text = tk.StringVar(value="영상 분석을 시작하면 진행률이 표시됩니다.")
        self._build_ui()

    def _build_ui(self):
        root = ctk.CTkFrame(self, fg_color=BG, corner_radius=0); root.pack(fill="both", expand=True, padx=24, pady=20)
        header = ctk.CTkFrame(root, fg_color="transparent"); header.pack(fill="x", pady=(0,14))
        left = ctk.CTkFrame(header, fg_color="transparent"); left.pack(side="left", fill="x", expand=True)
        ctk.CTkLabel(left, text="OFFLINE · VIDEO FIRST COACHING", text_color=ACCENT, font=(FONT,12,"bold")).pack(anchor="w")
        ctk.CTkLabel(left, text="VALORANT Video Coach", text_color=TEXT, font=(FONT,32,"bold")).pack(anchor="w", pady=(2,0))
        ctk.CTkLabel(left, text="게임 중 실시간 분석은 하지 않습니다. 녹화 영상을 넣으면 에임·헤드라인·사격 안정성·스킬 타이밍·예상 티어를 분석합니다.", text_color=MUTED, font=(FONT,12)).pack(anchor="w", pady=(3,0))
        ctk.CTkButton(header, text="설정", command=self.open_settings, width=86, height=38, corner_radius=14, fg_color=PANEL2, hover_color="#252e3b", border_width=1, border_color=BORDER, font=(FONT,11,"bold")).pack(side="right", padx=(10,0))

        picker = ctk.CTkFrame(root, fg_color=PANEL, border_color=BORDER, border_width=1, corner_radius=22); picker.pack(fill="x", pady=(0,12))
        pleft = ctk.CTkFrame(picker, fg_color="transparent"); pleft.pack(side="left", fill="x", expand=True, padx=18, pady=15)
        ctk.CTkLabel(pleft, text="VIDEO REVIEW", text_color=ACCENT, font=(FONT,10,"bold")).pack(anchor="w")
        ctk.CTkLabel(pleft, textvariable=self.file_var, text_color=TEXT, font=(FONT,13,"bold"), anchor="w").pack(anchor="w", pady=(2,2))
        ctk.CTkLabel(pleft, textvariable=self.status_var, text_color=MUTED, font=(FONT,10), anchor="w").pack(anchor="w")
        btns = ctk.CTkFrame(picker, fg_color="transparent"); btns.pack(side="right", padx=16, pady=14)
        ctk.CTkButton(btns, text="영상 선택", command=self.select_video, width=110, height=42, corner_radius=14, fg_color=PANEL2, hover_color="#2a3442", border_width=1, border_color=BORDER, font=(FONT,11,"bold")).pack(side="left", padx=(0,8))
        self.analyze_btn = ctk.CTkButton(btns, text="분석 시작", command=self.start_analysis, width=120, height=42, corner_radius=14, fg_color=ACCENT, hover_color=ACCENT_HOVER, font=(FONT,11,"bold")); self.analyze_btn.pack(side="left")

        progress_wrap = ctk.CTkFrame(root, fg_color="transparent"); progress_wrap.pack(fill="x", pady=(0,12))
        self.progress = ctk.CTkProgressBar(progress_wrap, height=10, corner_radius=8, progress_color=ACCENT, fg_color=PANEL2); self.progress.set(0); self.progress.pack(fill="x")
        ctk.CTkLabel(progress_wrap, textvariable=self.progress_text, text_color=MUTED, font=(FONT,9)).pack(anchor="w", pady=(4,0))

        metrics = ctk.CTkFrame(root, fg_color="transparent"); metrics.pack(fill="x", pady=(0,12))
        defs = [("예상 티어","tier",1),("에임","aim",1),("헤드라인","headline",1),("무빙 안정성","movement",1),("스킬","skill",1),("내 K/D","kd",0)]
        for i,(label,key,red) in enumerate(defs):
            metrics.grid_columnconfigure(i, weight=1); card = ctk.CTkFrame(metrics, fg_color=PANEL, border_color=BORDER, border_width=1, corner_radius=18); card.grid(row=0,column=i,sticky="nsew",padx=(0 if i==0 else 5,0 if i==len(defs)-1 else 5))
            ctk.CTkLabel(card,text=label,text_color=MUTED,font=(FONT,10,"bold")).pack(anchor="w",padx=14,pady=(11,0)); ctk.CTkLabel(card,textvariable=self.metric_vars[key],text_color=ACCENT if red else TEXT,font=(FONT,21,"bold")).pack(anchor="w",padx=14,pady=(1,11))

        body = ctk.CTkFrame(root, fg_color="transparent"); body.pack(fill="both", expand=True); body.grid_columnconfigure(0,weight=3); body.grid_columnconfigure(1,weight=2); body.grid_rowconfigure(0,weight=1)
        review = ctk.CTkFrame(body, fg_color=PANEL, border_color=BORDER, border_width=1, corner_radius=22); review.grid(row=0,column=0,sticky="nsew",padx=(0,6))
        chat = ctk.CTkFrame(body, fg_color=PANEL, border_color=BORDER, border_width=1, corner_radius=22); chat.grid(row=0,column=1,sticky="nsew",padx=(6,0))
        review_head = ctk.CTkFrame(review, fg_color="transparent"); review_head.pack(fill="x",padx=14,pady=(12,8))
        self.thumb = tk.Label(review_head,text="NO VIDEO",width=26,height=7,bg="#07090d",fg=MUTED,font=(FONT,9,"bold"),bd=0); self.thumb.pack(side="left",padx=(0,12))
        rtext=ctk.CTkFrame(review_head,fg_color="transparent"); rtext.pack(side="left",fill="x",expand=True)
        ctk.CTkLabel(rtext,text="분석 피드백",text_color=TEXT,font=(FONT,17,"bold")).pack(anchor="w"); ctk.CTkLabel(rtext,text="점수는 빨간색으로 강조하고, 개인 학습 데이터가 쌓이면 평소 대비 변화도 표시합니다.",text_color=MUTED,font=(FONT,10),wraplength=650,justify="left").pack(anchor="w",pady=(2,0))
        shell=ctk.CTkFrame(review,fg_color="#0d1118",corner_radius=16); shell.pack(fill="both",expand=True,padx=12,pady=(0,12))
        self.feedback=tk.Text(shell,wrap="word",state="disabled",bg="#0d1118",fg=TEXT,insertbackground=TEXT,selectbackground="#384252",relief="flat",bd=0,highlightthickness=0,padx=14,pady=14,font=(FONT,11),spacing1=3,spacing3=7); self.feedback.pack(fill="both",expand=True,padx=4,pady=4)
        self.feedback.tag_configure("heading",foreground=ACCENT,font=(FONT,13,"bold")); self.feedback.tag_configure("score",foreground=ACCENT,font=(FONT,13,"bold")); self.feedback.tag_configure("muted",foreground=MUTED,font=(FONT,10)); self.feedback.tag_configure("good",foreground=GOOD,font=(FONT,11,"bold")); self._set_feedback("영상 분석 준비 완료\n\n실시간 분석 기능은 꺼져 있습니다. 영상을 선택한 뒤 ‘분석 시작’을 누르세요.")

        ctk.CTkLabel(chat,text="AI COACH",text_color=ACCENT,font=(FONT,10,"bold")).pack(anchor="w",padx=14,pady=(13,0)); ctk.CTkLabel(chat,text="영상 분석 결과 질문",text_color=TEXT,font=(FONT,17,"bold")).pack(anchor="w",padx=14,pady=(1,8))
        chat_shell=ctk.CTkFrame(chat,fg_color="#0d1118",corner_radius=16); chat_shell.pack(fill="both",expand=True,padx=12,pady=(0,8))
        self.chat_box=tk.Text(chat_shell,wrap="word",state="disabled",bg="#0d1118",fg=TEXT,insertbackground=TEXT,relief="flat",bd=0,highlightthickness=0,padx=12,pady=12,font=(FONT,10),spacing3=6); self.chat_box.pack(fill="both",expand=True,padx=4,pady=4); self._append_chat("AI · 영상을 분석한 뒤 ‘내 에임에서 제일 먼저 고칠 점은?’처럼 질문할 수 있습니다.\n\n")
        row=ctk.CTkFrame(chat,fg_color="transparent"); row.pack(fill="x",padx=12,pady=(0,12)); self.chat_entry=ctk.CTkEntry(row,placeholder_text="분석 결과에 대해 질문하세요",height=42,corner_radius=13,font=(FONT,11)); self.chat_entry.pack(side="left",fill="x",expand=True); self.chat_entry.bind("<Return>",lambda _e:self.send_chat()); self.chat_send=ctk.CTkButton(row,text="전송",command=self.send_chat,width=76,height=42,corner_radius=13,fg_color=ACCENT,hover_color=ACCENT_HOVER,font=(FONT,11,"bold")); self.chat_send.pack(side="left",padx=(8,0))

    def select_video(self):
        path=filedialog.askopenfilename(title="VALORANT 영상 선택",filetypes=[("Video","*.mp4 *.mov *.avi *.mkv *.webm"),("All files","*.*")]);
        if not path:return
        self.selected_video=Path(path); self.file_var.set(self.selected_video.name); self.status_var.set("영상 선택 완료 · 분석 대기"); self._load_thumbnail(self.selected_video)

    def _load_thumbnail(self,path):
        cap=cv2.VideoCapture(str(path)); frames=int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        if frames>10:cap.set(cv2.CAP_PROP_POS_FRAMES,max(0,frames//3))
        ok,frame=cap.read(); cap.release()
        if not ok:self.thumb.configure(image="",text="VIDEO"); return
        image=Image.fromarray(cv2.cvtColor(frame,cv2.COLOR_BGR2RGB)); image.thumbnail((220,120)); self._thumb_photo=ImageTk.PhotoImage(image); self.thumb.configure(image=self._thumb_photo,text="")

    def start_analysis(self):
        if self.analysis_busy:return
        if self.selected_video is None:messagebox.showinfo("영상 선택","먼저 분석할 영상을 선택해 주세요."); return
        self.analysis_busy=True; self.analyze_btn.configure(state="disabled",text="분석 중…"); self.progress.set(0); self.status_var.set("영상 분석 중 · 게임 실시간 캡처 없음"); self.progress_text.set("분석 준비 중…"); threading.Thread(target=self._analysis_worker,daemon=True).start()

    def _analysis_worker(self):
        try:
            report=analyze_video(self.selected_video,self.config_data,ROOT,self._progress_from_worker); self._apply_learning(report); self.after(0,lambda r=report:self._deliver_report(r))
        except Exception as exc:self.after(0,lambda e=exc:self._analysis_failed(e))

    def _progress_from_worker(self,fraction,text):self.after(0,lambda:(self.progress.set(max(0,min(1,fraction))),self.progress_text.set(text)))

    def _apply_learning(self,report):
        bursts=report.get("shot_bursts") or []; metrics={"movement_score":report.get("movement_score"),"aim_score":report.get("aim_score"),"skill_score":report.get("skill_score"),"avg_shots_per_fight":(sum(len(x) for x in bursts)/len(bursts)) if bursts else None,"fight_count":len(bursts),"shots":report.get("confirmed_shots",0)}; mode="brawl" if report.get("mode")=="brawl" else "normal"; report["personal_comparison"]=self.personal_profile.compare(metrics,mode)
        if self.config_data.get("personal_learning_enabled",True) and int(report.get("confirmed_shots") or 0)>=5 and float(report.get("duration_seconds") or 0)>=20:report["personal_baseline"]=self.personal_profile.learn(metrics,mode)
        else:report["personal_baseline"]=self.personal_profile.baseline(mode)
        try:Path(report["report_path"]).write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
        except Exception:pass

    @staticmethod
    def _score(value):return "—" if value is None else f"{float(value):.0f}/100"

    def _deliver_report(self,report):
        self.last_report=report; self.analysis_busy=False; self.analyze_btn.configure(state="normal",text="분석 시작"); self.status_var.set("분석 완료 · AI 코치 질문 가능"); self.progress.set(1); self.progress_text.set(f"완료 · {report.get('duration_seconds',0):.1f}초 영상 · {report.get('processed_frames',0)} 프레임 분석")
        tier=report.get("tier_prediction") or {}; self.metric_vars["tier"].set(tier.get("tier_ko") or tier.get("tier") or "—"); self.metric_vars["aim"].set(self._score(report.get("aim_score"))); self.metric_vars["headline"].set(self._score(report.get("headline_score"))); self.metric_vars["movement"].set(self._score(report.get("movement_score"))); self.metric_vars["skill"].set("—" if report.get("mode")=="brawl" else self._score(report.get("skill_score"))); combat=report.get("combat") or {}; self.metric_vars["kd"].set(f"{combat.get('kills',0)} / {combat.get('deaths',0)}"); self._render_feedback(report)

    def _render_feedback(self,report):
        tier=report.get("tier_prediction") or {}; combat=report.get("combat") or {}; lines=[("영상 분석 결과\n","heading"),(f"예상 티어  {tier.get('tier_ko','판정 불가')}  ·  티어 점수 {self._score(tier.get('score'))}  ·  신뢰도 {float(tier.get('confidence',0))*100:.0f}%\n",None),(f"에임 {self._score(report.get('aim_score'))}   ·   헤드라인 {self._score(report.get('headline_score'))}   ·   무빙 안정성 {self._score(report.get('movement_score'))}   ·   스킬 {('—' if report.get('mode')=='brawl' else self._score(report.get('skill_score')))}\n",None),(f"실제 발사 추정 {report.get('confirmed_shots',0)}발   ·   사격 구간 {report.get('shot_burst_count',0)}회   ·   내 킬 {combat.get('kills',0)}   ·   내 데스 {combat.get('deaths',0)}\n\n","muted"),("코칭 피드백\n","heading")]
        for item in report.get("feedback") or []:lines.append((f"• {item}\n",None))
        comparison=report.get("personal_comparison") or {}
        if comparison.get("messages"):
            lines.append(("\n개인 학습 비교\n","heading")); [lines.append((f"• {x}\n","muted")) for x in comparison["messages"][:5]]
        lines.append(("\n주의 · 티어 예측은 계정 랭크 조회가 아니라 영상에서 측정 가능한 기계적 지표 기반 추정입니다.\n","muted")); self.feedback.configure(state="normal"); self.feedback.delete("1.0","end")
        for text,tag in lines:
            start=self.feedback.index("end"); self.feedback.insert("end",text,tag or ())
            search_from=start
            while True:
                pos=self.feedback.search("/100",search_from,stopindex="end")
                if not pos:break
                line_start=self.feedback.index(f"{pos} linestart"); token_start=self.feedback.search(" ",pos,backwards=True,stopindex=line_start); token_start=line_start if not token_start else self.feedback.index(f"{token_start}+1c"); self.feedback.tag_add("score",token_start,f"{pos}+4c"); search_from=f"{pos}+4c"
        self.feedback.configure(state="disabled"); self.feedback.see("1.0")

    def _set_feedback(self,text):self.feedback.configure(state="normal"); self.feedback.delete("1.0","end"); self.feedback.insert("end",text); self.feedback.configure(state="disabled")
    def _analysis_failed(self,exc):self.analysis_busy=False; self.analyze_btn.configure(state="normal",text="분석 시작"); self.status_var.set("분석 실패"); self.progress_text.set(str(exc)); messagebox.showerror("영상 분석 실패",f"{type(exc).__name__}: {exc}")
    def _append_chat(self,text):self.chat_box.configure(state="normal"); self.chat_box.insert("end",text); self.chat_box.see("end"); self.chat_box.configure(state="disabled")

    def send_chat(self):
        if self.chat_busy:return
        q=self.chat_entry.get().strip();
        if not q:return
        self.chat_entry.delete(0,"end"); self._append_chat(f"나 · {q}\n"); previous=list(self.chat_history); self.chat_history.append({"role":"user","content":q}); self.chat_busy=True; self.chat_send.configure(state="disabled",text="…")
        def worker():
            try:text=self.chat_client.ask(q,self.last_report or {},previous).text
            except Exception as exc:text=f"AI 응답 오류: {type(exc).__name__}: {exc}"
            self.after(0,lambda:self._deliver_chat(text))
        threading.Thread(target=worker,daemon=True).start()

    def _deliver_chat(self,text):self.chat_history.append({"role":"assistant","content":text}); self._append_chat(f"AI · {text}\n\n"); self.chat_busy=False; self.chat_send.configure(state="normal",text="전송"); self.chat_entry.focus_set()

    def open_settings(self):
        if self._settings_window is not None and self._settings_window.winfo_exists():self._settings_window.lift(); return
        win=ctk.CTkToplevel(self); self._settings_window=win; win.title("Video Coach 설정"); win.geometry("650x570"); win.configure(fg_color=BG); win.transient(self)
        lang=tk.StringVar(value=str(self.config_data.get("language","ko"))); fps=tk.StringVar(value=str(self.config_data.get("video_review_fps",4))); enemy=tk.StringVar(value=str(self.config_data.get("enemy_outline_color","red"))); learn=tk.BooleanVar(value=bool(self.config_data.get("personal_learning_enabled",True))); ai=tk.BooleanVar(value=bool(self.config_data.get("local_ai_enabled",True))); model=tk.StringVar(value=str(self.config_data.get("ollama_model","qwen3:4b")))
        card=ctk.CTkFrame(win,fg_color=PANEL,corner_radius=20,border_width=1,border_color=BORDER); card.pack(fill="both",expand=True,padx=20,pady=20); ctk.CTkLabel(card,text="OFFLINE SETTINGS",text_color=ACCENT,font=(FONT,11,"bold")).pack(anchor="w",padx=18,pady=(18,0)); ctk.CTkLabel(card,text="영상 분석 설정",text_color=TEXT,font=(FONT,24,"bold")).pack(anchor="w",padx=18,pady=(1,14))
        self._setting_option(card,"언어",lang,["ko","en"]); self._setting_option(card,"영상 분석 FPS",fps,["2","3","4","6","8"]); self._setting_option(card,"적 윤곽색",enemy,["red","purple","yellow"]); self._setting_switch(card,"개인 플레이 자동 학습",learn); self._setting_switch(card,"로컬 Ollama AI 사용",ai); self._setting_entry(card,"Ollama 모델",model)
        ctk.CTkLabel(card,text="분석 FPS를 낮추면 CPU 사용량이 줄어듭니다. 실시간 게임 분석은 이 버전에서 실행되지 않습니다.",text_color=MUTED,font=(FONT,10),wraplength=560,justify="left").pack(anchor="w",padx=18,pady=(12,8))
        def save():self.config_data.update({"language":lang.get(),"video_review_fps":int(fps.get()),"enemy_outline_color":enemy.get(),"personal_learning_enabled":bool(learn.get()),"local_ai_enabled":bool(ai.get()),"ollama_model":model.get().strip() or "qwen3:4b"}); save_config(self.config_data); self.chat_client.update_config(self.config_data); self._settings_window=None; win.destroy()
        footer=ctk.CTkFrame(card,fg_color="transparent"); footer.pack(fill="x",padx=18,pady=(8,18)); ctk.CTkButton(footer,text="저장",command=save,width=100,height=40,corner_radius=13,fg_color=ACCENT,hover_color=ACCENT_HOVER,font=(FONT,11,"bold")).pack(side="right"); ctk.CTkButton(footer,text="취소",command=win.destroy,width=90,height=40,corner_radius=13,fg_color=PANEL2,hover_color="#28313d",font=(FONT,11,"bold")).pack(side="right",padx=(0,8)); win.protocol("WM_DELETE_WINDOW",lambda:(setattr(self,"_settings_window",None),win.destroy()))

    def _setting_option(self,parent,label,var,values):row=ctk.CTkFrame(parent,fg_color="transparent"); row.pack(fill="x",padx=18,pady=7); ctk.CTkLabel(row,text=label,text_color=MUTED,font=(FONT,11,"bold"),width=220,anchor="w").pack(side="left"); ctk.CTkOptionMenu(row,variable=var,values=values,width=180,height=36,corner_radius=11,fg_color=PANEL2,button_color="#303a49",button_hover_color="#3b4758").pack(side="right")
    def _setting_switch(self,parent,label,var):row=ctk.CTkFrame(parent,fg_color="transparent"); row.pack(fill="x",padx=18,pady=7); ctk.CTkLabel(row,text=label,text_color=MUTED,font=(FONT,11,"bold"),anchor="w").pack(side="left"); ctk.CTkSwitch(row,text="",variable=var,progress_color=ACCENT).pack(side="right")
    def _setting_entry(self,parent,label,var):row=ctk.CTkFrame(parent,fg_color="transparent"); row.pack(fill="x",padx=18,pady=7); ctk.CTkLabel(row,text=label,text_color=MUTED,font=(FONT,11,"bold"),width=220,anchor="w").pack(side="left"); ctk.CTkEntry(row,textvariable=var,width=220,height=36,corner_radius=11).pack(side="right")


if __name__ == "__main__":App().mainloop()
