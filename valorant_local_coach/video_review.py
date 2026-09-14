from __future__ import annotations

import json
import statistics
import time
from collections import Counter
from pathlib import Path
from typing import Callable

import cv2
import numpy as np

from ammo_shot_detector import AmmoShotDetector
from combat_event_detector import CombatEventDetector
from tier_predictor import predict_tier
from vision_analyzer import VisionAnalyzer

DEFAULT_SLOT_ROIS = {
    "skill1": {"x": 0.365, "y": 0.875, "w": 0.060, "h": 0.105},
    "skill2": {"x": 0.430, "y": 0.875, "w": 0.060, "h": 0.105},
    "skill3": {"x": 0.495, "y": 0.875, "w": 0.060, "h": 0.105},
    "ultimate": {"x": 0.560, "y": 0.875, "w": 0.065, "h": 0.105},
}


class OfflineUtilityDetector:
    def __init__(self, config: dict):
        self.rois = config.get("skill_hud_rois") or DEFAULT_SLOT_ROIS
        self.threshold = max(5.0, float(config.get("video_skill_change_threshold", 11.0)))
        self.cooldown = max(0.8, float(config.get("video_skill_cooldown_seconds", 1.4)))
        self._baseline: dict[str, np.ndarray] = {}
        self._evidence: dict[str, int] = Counter()
        self._last_event: dict[str, float] = {}
        self._noise: dict[str, float] = {}

    @staticmethod
    def _crop(frame: np.ndarray, roi: dict) -> np.ndarray | None:
        h, w = frame.shape[:2]
        x1 = max(0, min(w - 1, int(w * float(roi.get("x", 0.0)))))
        y1 = max(0, min(h - 1, int(h * float(roi.get("y", 0.0)))))
        x2 = max(x1 + 1, min(w, int(w * (float(roi.get("x", 0.0)) + float(roi.get("w", 0.0))))))
        y2 = max(y1 + 1, min(h, int(h * (float(roi.get("y", 0.0)) + float(roi.get("h", 0.0))))))
        crop = frame[y1:y2, x1:x2]
        if crop.size == 0:
            return None
        gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
        return cv2.GaussianBlur(cv2.resize(gray, (48, 48), interpolation=cv2.INTER_AREA), (3, 3), 0)

    def process(self, frame: np.ndarray, video_seconds: float) -> list[dict]:
        events: list[dict] = []
        for slot, roi in self.rois.items():
            crop = self._crop(frame, roi)
            if crop is None:
                continue
            base = self._baseline.get(slot)
            if base is None:
                self._baseline[slot] = crop.astype(np.float32)
                self._noise[slot] = 1.5
                continue
            diff = float(np.mean(cv2.absdiff(base.astype(np.uint8), crop)))
            noise = float(self._noise.get(slot, 1.5))
            threshold = max(self.threshold, noise * 3.0 + 1.5)
            last = float(self._last_event.get(slot, -999.0))
            if diff >= threshold and video_seconds - last >= self.cooldown:
                self._evidence[slot] = int(self._evidence.get(slot, 0)) + 1
                if self._evidence[slot] >= 2:
                    confidence = max(0.50, min(0.95, 0.55 + (diff - threshold) / max(15.0, threshold * 2.5)))
                    events.append({
                        "slot_id": slot,
                        "video_seconds": round(video_seconds, 2),
                        "confidence": round(confidence, 3),
                        "change_score": round(diff, 2),
                    })
                    self._last_event[slot] = video_seconds
                    self._baseline[slot] = crop.astype(np.float32)
                    self._evidence[slot] = 0
                    self._noise[slot] = max(1.0, noise * 0.8)
                    continue
            self._evidence[slot] = 0
            self._noise[slot] = noise * 0.96 + min(diff, 8.0) * 0.04
            cv2.accumulateWeighted(crop.astype(np.float32), self._baseline[slot], 0.06)
        return events


def _segments_from_shots(shot_times: list[float], gap: float = 1.8) -> list[list[float]]:
    if not shot_times:
        return []
    groups = [[shot_times[0]]]
    for ts in shot_times[1:]:
        if ts - groups[-1][-1] > gap:
            groups.append([ts])
        else:
            groups[-1].append(ts)
    return groups


def _motion_frame(frame: np.ndarray) -> np.ndarray:
    h, w = frame.shape[:2]
    crop = frame[int(h * 0.12):int(h * 0.84), int(w * 0.08):int(w * 0.94)]
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    return cv2.GaussianBlur(cv2.resize(gray, (160, 90), interpolation=cv2.INTER_AREA), (3, 3), 0)


def _nearest_motion(motion_samples: list[tuple[float, float]], t: float, window: float = 0.45) -> list[float]:
    return [value for ts, value in motion_samples if abs(ts - t) <= window]


def _movement_score(bursts: list[list[float]], motion_samples: list[tuple[float, float]]) -> tuple[float | None, dict]:
    if not bursts:
        return None, {"shot_motion": None, "long_burst_ratio": None}
    shot_motion_values: list[float] = []
    for burst in bursts:
        center = (burst[0] + burst[-1]) / 2.0
        nearby = _nearest_motion(motion_samples, center)
        if nearby:
            shot_motion_values.append(statistics.fmean(nearby))
    avg_burst = statistics.fmean(len(x) for x in bursts)
    long_ratio = sum(1 for x in bursts if len(x) >= 7) / max(1, len(bursts))
    shot_motion = statistics.fmean(shot_motion_values) if shot_motion_values else None
    score = 94.0
    if shot_motion is not None:
        score -= max(0.0, shot_motion - 5.5) * 2.8
    score -= max(0.0, avg_burst - 3.2) * 3.4
    score -= long_ratio * 18.0
    return round(max(20.0, min(100.0, score)), 1), {
        "shot_motion": round(shot_motion, 2) if shot_motion is not None else None,
        "avg_burst": round(avg_burst, 2),
        "long_burst_ratio": round(long_ratio, 3),
    }


def _skill_score(events: list[dict], bursts: list[list[float]], mode: str) -> tuple[float | None, dict]:
    if mode == "brawl":
        return None, {"pre_fight": 0, "during": 0, "late": 0}
    if not events:
        return None, {"pre_fight": 0, "during": 0, "late": 0}
    starts = [g[0] for g in bursts]
    ends = [g[-1] for g in bursts]
    pre = during = late = 0
    for event in events:
        t = float(event.get("video_seconds", 0.0))
        if any(s - 4.0 <= t <= s - 0.15 for s in starts):
            pre += 1
        elif any(s - 0.15 <= t <= e + 0.45 for s, e in zip(starts, ends)):
            during += 1
        else:
            late += 1
    score = 78 + min(14, pre * 5) + min(6, during * 2) - min(20, late * 5)
    return float(max(35, min(100, score))), {"pre_fight": pre, "during": during, "late": late}


def _spray_score(bursts: list[list[float]]) -> float | None:
    if not bursts:
        return None
    avg = statistics.fmean(len(x) for x in bursts)
    long_ratio = sum(1 for x in bursts if len(x) >= 7) / max(1, len(bursts))
    score = 96.0 - max(0.0, avg - 3.0) * 4.0 - long_ratio * 22.0
    return round(max(25.0, min(100.0, score)), 1)


def _feedback(report: dict, language: str = "ko") -> list[str]:
    ko = language != "en"
    messages: list[str] = []
    aim = report.get("aim_score")
    headline = report.get("headline_score")
    move = report.get("movement_score")
    skill = report.get("skill_score")
    tier = report.get("tier_prediction") or {}
    kd = report.get("combat") or {}
    bursts = report.get("shot_bursts") or []
    if ko:
        if tier.get("tier_ko"):
            messages.append(f"영상 기반 예상 티어는 {tier['tier_ko']}이며, 신뢰도는 {tier.get('confidence', 0) * 100:.0f}%입니다. 실제 계정 랭크가 아니라 플레이 영상의 기계적 지표 추정치입니다.")
        if aim is not None:
            messages.append(f"에임 점수 {aim:.0f}/100 · 크로스헤어와 적 머리 추정점 사이의 2D 오차를 기준으로 계산했습니다.")
        if headline is not None:
            messages.append(f"헤드라인 {headline:.0f}/100 · 피킹 전 머리 높이를 더 일정하게 유지할수록 점수가 올라갑니다.")
        if move is not None:
            messages.append(f"영상 기반 무빙/사격 안정성 {move:.0f}/100 · 사격 순간 화면 흔들림과 긴 스프레이 비중을 반영했습니다. 실제 WASD 입력 자체를 읽은 점수는 아닙니다.")
        if skill is not None:
            messages.append(f"스킬 타이밍 후보 점수 {skill:.0f}/100 · 교전 직전/교전 중 HUD 변화 시점을 기준으로 추정했습니다.")
        if bursts:
            avg = statistics.fmean(len(x) for x in bursts)
            messages.append(f"사격 구간당 평균 {avg:.1f}발 · 7발 이상 긴 스프레이가 많으면 짧은 버스트 후 재조준을 우선 연습하세요.")
        messages.append(f"내 킬 HUD 추정 {kd.get('kills', 0)} · 내 데스 HUD 추정 {kd.get('deaths', 0)}. 화면 HUD 기반이라 불확실한 이벤트는 일부러 누락합니다.")
    else:
        messages.append(f"Estimated tier: {tier.get('tier', 'Unrated')} ({tier.get('confidence', 0) * 100:.0f}% confidence). This is a mechanics estimate, not account rank.")
        if aim is not None:
            messages.append(f"Aim score: {aim:.0f}/100.")
        if headline is not None:
            messages.append(f"Head-line score: {headline:.0f}/100.")
        if move is not None:
            messages.append(f"Video-estimated movement/shot stability: {move:.0f}/100.")
        if skill is not None:
            messages.append(f"Utility-timing candidate score: {skill:.0f}/100.")
    return messages[:7]


def analyze_video(video_path: str | Path, config: dict, root_dir: str | Path, progress: Callable[[float, str], None] | None = None) -> dict:
    path = Path(video_path)
    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        raise RuntimeError("영상을 열 수 없습니다. / Could not open video.")
    source_fps = float(cap.get(cv2.CAP_PROP_FPS) or 30.0)
    frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    duration = frame_count / source_fps if source_fps > 0 and frame_count > 0 else 0.0
    review_fps = max(2.0, min(10.0, float(config.get("video_review_fps", 4))))
    step = max(1, int(round(source_fps / review_fps)))

    vision_cfg = dict(config)
    vision_cfg["vision_analysis_fps"] = review_fps
    vision_cfg["show_vision_boxes_in_preview"] = False
    vision_cfg["store_minimap_history"] = False
    vision = VisionAnalyzer(Path(root_dir), vision_cfg)
    ammo_cfg = dict(config)
    ammo_cfg["ammo_hud_detection"] = True
    ammo_cfg["ammo_hud_detection_fps"] = min(20, max(8, int(review_fps * 2)))
    ammo = AmmoShotDetector(ammo_cfg)
    utility = OfflineUtilityDetector(config)
    combat = CombatEventDetector(config)

    shots = 0
    shot_times: list[float] = []
    ammo_updates: list[dict] = []
    utility_events: list[dict] = []
    headline_scores: list[float] = []
    headline_errors: list[float] = []
    aim_scores: list[float] = []
    aim_errors: list[float] = []
    enemy_frames = ally_frames = 0
    max_enemies = max_allies = 0
    minimap_max_enemies = minimap_max_allies = 0
    processed = 0
    base_ts = time.time()
    idx = 0
    previous_motion = None
    motion_samples: list[tuple[float, float]] = []
    gameplay_hud_frames = 0
    skill_hud_visible_frames = 0

    while True:
        ok, frame = cap.read()
        if not ok:
            break
        if idx % step != 0:
            idx += 1
            continue
        video_seconds = idx / source_fps if source_fps > 0 else processed / review_fps
        synthetic_ts = base_ts + video_seconds
        processed += 1

        motion = _motion_frame(frame)
        if previous_motion is not None:
            motion_samples.append((video_seconds, float(np.mean(cv2.absdiff(previous_motion, motion)))))
        previous_motion = motion

        sample = vision.analyze(frame, synthetic_ts)
        if sample is not None:
            enemy_frames += int(sample.enemies > 0)
            ally_frames += int(sample.allies > 0)
            max_enemies = max(max_enemies, sample.enemies)
            max_allies = max(max_allies, sample.allies)
            minimap_max_enemies = max(minimap_max_enemies, sample.minimap_enemies)
            minimap_max_allies = max(minimap_max_allies, sample.minimap_allies)
            if sample.headline_score is not None:
                headline_scores.append(float(sample.headline_score))
            if sample.headline_error_px is not None:
                headline_errors.append(float(sample.headline_error_px))
            if sample.aim_score is not None:
                aim_scores.append(float(sample.aim_score))
            if sample.aim_error_px is not None:
                aim_errors.append(float(sample.aim_error_px))

        update = ammo.process(frame, synthetic_ts, firing_hint=True)
        recent_fire = False
        if update is not None and 1 <= update.shots <= 10:
            recent_fire = True
            shots += int(update.shots)
            for n in range(int(update.shots)):
                shot_times.append(video_seconds + n * 0.01)
            ammo_updates.append({"video_seconds": round(video_seconds, 2), "shots": int(update.shots), "ammo_before": int(update.ammo_before), "ammo_after": int(update.ammo_after), "confidence": float(update.confidence)})
        utility_events.extend(utility.process(frame, video_seconds))
        combat.process(frame, synthetic_ts, recent_fire=recent_fire)

        ammo_crop = frame[int(frame.shape[0]*0.84):int(frame.shape[0]*0.99), int(frame.shape[1]*0.85):int(frame.shape[1]*0.995)]
        if ammo_crop.size:
            gray = cv2.cvtColor(ammo_crop, cv2.COLOR_BGR2GRAY)
            if float(np.std(gray)) >= 10.0:
                gameplay_hud_frames += 1
                active = 0
                for roi in DEFAULT_SLOT_ROIS.values():
                    h, w = frame.shape[:2]
                    x1, y1 = int(w*roi["x"]), int(h*roi["y"])
                    x2, y2 = int(w*(roi["x"]+roi["w"])), int(h*(roi["y"]+roi["h"]))
                    slot = frame[max(0,y1):min(h,y2), max(0,x1):min(w,x2)]
                    if slot.size:
                        sg = cv2.cvtColor(slot, cv2.COLOR_BGR2GRAY)
                        edges = cv2.Canny(sg,55,130)
                        score = float(np.std(sg))*0.7 + (float(np.count_nonzero(edges))/max(1,edges.size))*115.0
                        if score >= 13.0:
                            active += 1
                if active >= 1:
                    skill_hud_visible_frames += 1

        if progress and processed % 6 == 0:
            frac = min(0.99, (idx + 1) / frame_count) if frame_count > 0 else 0.0
            progress(frac, f"{video_seconds:.1f}s / {duration:.1f}s" if duration else f"{video_seconds:.1f}s")
        idx += 1

    cap.release()
    if progress:
        progress(1.0, "완료 / Done")

    bursts = _segments_from_shots(sorted(shot_times))
    headline_score = round(statistics.fmean(headline_scores), 1) if headline_scores else None
    headline_error = round(statistics.fmean(headline_errors), 1) if headline_errors else None
    aim_score = round(statistics.fmean(aim_scores), 1) if aim_scores else None
    aim_error = round(statistics.fmean(aim_errors), 1) if aim_errors else None
    movement_score, movement_detail = _movement_score(bursts, motion_samples)
    spray_score = _spray_score(bursts)
    mode = "brawl" if duration >= 30.0 and gameplay_hud_frames >= 20 and skill_hud_visible_frames <= max(1, int(gameplay_hud_frames * 0.01)) else "normal"
    skill_score, skill_detail = _skill_score(utility_events, bursts, mode)
    slot_counts = Counter(event["slot_id"] for event in utility_events)
    combat_summary = combat.summary()

    report = {
        "source_video": str(path), "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "duration_seconds": round(duration, 2), "source_fps": round(source_fps, 3), "review_fps": review_fps,
        "processed_frames": processed, "mode": mode, "confirmed_shots": shots, "ammo_updates": ammo_updates,
        "shot_bursts": [[round(x, 2) for x in group] for group in bursts], "shot_burst_count": len(bursts),
        "utility_use_candidates": len(utility_events), "utility_by_slot": dict(slot_counts), "utility_events": utility_events,
        "skill_score": skill_score, "skill_detail": skill_detail,
        "enemy_detection_frames": enemy_frames, "ally_detection_frames": ally_frames,
        "max_enemies": max_enemies, "max_allies": max_allies,
        "minimap_max_enemies": minimap_max_enemies, "minimap_max_allies": minimap_max_allies,
        "headline_score": headline_score, "headline_error_px": headline_error,
        "aim_score": aim_score, "aim_error_px": aim_error,
        "movement_score": movement_score, "movement_detail": movement_detail,
        "spray_control_score": spray_score, "combat": combat_summary,
        "limitations": [
            "Movement score is estimated from video motion and firing behavior; keyboard inputs are not known.",
            "Utility detection is HUD-change based without key hints and is a candidate-use estimate.",
            "Enemy/ally/head detection is color/shape heuristic, not a trained game-specific detector.",
            "Kill/death values are conservative self-HUD visual estimates.",
        ],
    }
    report["tier_prediction"] = predict_tier(report)
    report["feedback"] = _feedback(report, str(config.get("language", "ko")))
    out_dir = Path(root_dir) / "data" / "video_reviews"
    out_dir.mkdir(parents=True, exist_ok=True)
    safe_stem = "".join(c if c.isalnum() or c in "-_" else "_" for c in path.stem)[:60] or "video"
    out_path = out_dir / f"{time.strftime('%Y%m%d_%H%M%S')}_{safe_stem}.json"
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    report["report_path"] = str(out_path)
    return report
