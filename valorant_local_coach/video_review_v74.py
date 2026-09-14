from __future__ import annotations

import json
import statistics
import time
from pathlib import Path

import cv2
import numpy as np

import video_review as base
from ammo_shot_detector import AmmoShotDetector
from tier_predictor_v74 import predict_tier
from video_aim_analyzer_v74 import VideoAimAnalyzerV74
from video_combat_detector import VideoCombatDetector


def _motion_probe(frame: np.ndarray) -> np.ndarray:
    h, w = frame.shape[:2]
    crop = frame[int(h * 0.12):int(h * 0.78), int(w * 0.10):int(w * 0.92)]
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    return cv2.GaussianBlur(cv2.resize(gray, (144, 82), interpolation=cv2.INTER_AREA), (5, 5), 0)


def _robust_movement_score(bursts: list[list[float]], motion_samples: list[tuple[float, float]]) -> tuple[float | None, dict]:
    if not bursts:
        return None, {"method": "no_shot_bursts_v74"}
    values = [float(v) for _t, v in motion_samples]
    if not values:
        avg_burst = statistics.fmean(len(x) for x in bursts)
        score = 96.0 - max(0.0, avg_burst - 5.5) * 1.8
        return round(max(55.0, min(100.0, score)), 1), {"method": "burst_only_v74", "avg_burst": round(avg_burst, 2), "baseline_motion": None}
    baseline = statistics.median(values)
    mad = statistics.median(abs(v - baseline) for v in values)
    scale = max(1.25, 1.4826 * mad)
    burst_z: list[float] = []
    burst_motion: list[float] = []
    for burst in bursts:
        center = (burst[0] + burst[-1]) / 2.0
        nearby = [v for t, v in motion_samples if abs(t - center) <= 0.22]
        if not nearby:
            continue
        m = statistics.median(nearby)
        burst_motion.append(m)
        burst_z.append(max(0.0, (m - baseline) / scale))
    avg_burst = statistics.fmean(len(x) for x in bursts)
    long_ratio = sum(1 for x in bursts if len(x) >= 9) / max(1, len(bursts))
    severe_ratio = sum(1 for z in burst_z if z >= 2.5) / max(1, len(burst_z)) if burst_z else 0.0
    excess = statistics.median(burst_z) if burst_z else 0.0
    score = 96.0
    score -= min(15.0, excess * 4.0)
    score -= severe_ratio * 12.0
    score -= max(0.0, avg_burst - 5.5) * 2.0
    score -= long_ratio * 10.0
    score = max(50.0, min(100.0, score))
    return round(score, 1), {"method": "video_relative_camera_motion_v74", "baseline_motion": round(baseline, 2), "motion_mad": round(mad, 2), "shot_motion": round(statistics.median(burst_motion), 2) if burst_motion else None, "relative_excess_z": round(excess, 2), "high_motion_burst_ratio": round(severe_ratio, 3), "avg_burst": round(avg_burst, 2), "long_burst_ratio": round(long_ratio, 3)}


def _timeline(report: dict, aim_result: dict) -> list[dict]:
    events: list[dict] = []
    for item in (report.get("combat") or {}).get("events") or []:
        t = item.get("video_seconds")
        if t is None:
            continue
        kind = str(item.get("kind") or "event")
        events.append({"video_seconds": float(t), "kind": kind, "label": "킬" if kind == "kill" else "데스" if kind == "death" else kind})
    skill_names = {"skill1": "스킬 1", "skill2": "스킬 2", "skill3": "스킬 3", "ultimate": "궁극기"}
    for item in report.get("utility_events") or []:
        t = item.get("video_seconds")
        if t is not None:
            slot = str(item.get("slot_id") or "skill")
            events.append({"video_seconds": float(t), "kind": "skill", "label": skill_names.get(slot, "스킬")})
    for burst in report.get("shot_bursts") or []:
        if burst:
            events.append({"video_seconds": float(burst[0]), "kind": "shot", "label": f"사격 {len(burst)}발"})
    for item in aim_result.get("aim_events") or []:
        t = item.get("video_seconds")
        if t is not None:
            events.append({"video_seconds": float(t), "kind": "aim", "label": f"에임 {float(item.get('aim_score', 0)):.0f}점", "score": item.get("aim_score"), "source": item.get("source")})
    events.sort(key=lambda e: (float(e["video_seconds"]), e["kind"]))
    compact = []
    for e in events:
        if compact and e["kind"] == compact[-1]["kind"] and abs(float(e["video_seconds"]) - float(compact[-1]["video_seconds"])) < 0.20:
            continue
        compact.append(e)
    return compact[:300]


def analyze_video(video_path, config, root_dir, progress=None):
    def first_progress(frac, text):
        if progress:
            progress(min(0.50, float(frac) * 0.50), f"1/2 기본 분석 · {text}")
    report = base.analyze_video(video_path, config, root_dir, first_progress)
    path = Path(video_path)
    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        return report
    source_fps = float(cap.get(cv2.CAP_PROP_FPS) or 30.0)
    frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    refine_fps = max(10.0, min(15.0, float(config.get("video_refine_fps", 12))))
    step = max(1, int(round(source_fps / refine_fps)))
    ammo_cfg = dict(config)
    ammo_cfg["ammo_hud_detection"] = True
    ammo_cfg["ammo_hud_detection_fps"] = 24
    ammo_cfg["ammo_hud_min_confidence"] = min(float(ammo_cfg.get("ammo_hud_min_confidence", 0.53)), 0.55)
    ammo = AmmoShotDetector(ammo_cfg)
    aim = VideoAimAnalyzerV74(config)
    combat = VideoCombatDetector(config)
    shot_times: list[float] = []
    ammo_updates: list[dict] = []
    motion_samples: list[tuple[float, float]] = []
    previous_motion = None
    base_ts = time.time()
    idx = processed = 0
    frame_size_set = False
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        if idx % step != 0:
            idx += 1
            continue
        t = idx / source_fps if source_fps > 0 else processed / refine_fps
        processed += 1
        if not frame_size_set:
            aim.set_frame_size(frame.shape[1], frame.shape[0])
            frame_size_set = True
        aim.process(frame, t)
        motion = _motion_probe(frame)
        if previous_motion is not None:
            motion_samples.append((t, float(np.mean(cv2.absdiff(previous_motion, motion)))))
        previous_motion = motion
        update = ammo.process(frame, base_ts + t, firing_hint=True)
        recent_fire = False
        if update is not None and 1 <= int(update.shots) <= 10:
            recent_fire = True
            for n in range(int(update.shots)):
                shot_times.append(t + n * 0.008)
            ammo_updates.append({"video_seconds": round(t, 3), "shots": int(update.shots), "ammo_before": int(update.ammo_before), "ammo_after": int(update.ammo_after), "confidence": float(update.confidence)})
        combat.process(frame, t, recent_fire=recent_fire)
        if progress and processed % 8 == 0:
            frac = (idx + 1) / frame_count if frame_count > 0 else 0.0
            progress(0.50 + min(0.49, frac * 0.49), f"2/2 점수 재보정 · {t:.1f}s")
        idx += 1
    cap.release()
    combat_result = combat.summary()
    kill_times = [float(e.get("video_seconds")) for e in combat_result.get("events") or [] if e.get("kind") == "kill" and e.get("video_seconds") is not None]
    aim_result = aim.score(sorted(shot_times), kill_times)
    bursts = base._segments_from_shots(sorted(shot_times))
    movement_score, movement_detail = _robust_movement_score(bursts, motion_samples)
    report["review_fps_refined"] = refine_fps
    report["confirmed_shots"] = len(shot_times)
    report["ammo_updates"] = ammo_updates
    report["shot_bursts"] = [[round(x, 3) for x in g] for g in bursts]
    report["shot_burst_count"] = len(bursts)
    report["movement_score"] = movement_score
    report["movement_detail"] = movement_detail
    for key in ("aim_score", "headline_score", "aim_error_px", "headline_error_px", "aim_events", "color_votes"):
        report[key] = aim_result.get(key)
    report["aim_evaluated_shots"] = int(aim_result.get("evaluated_shots") or 0)
    report["aim_shot_anchor_count"] = int(aim_result.get("shot_anchor_count") or 0)
    report["aim_evaluated_events"] = int(aim_result.get("evaluated_events") or 0)
    report["aim_method"] = aim_result.get("method")
    report["aim_confidence"] = aim_result.get("confidence")
    report["auto_enemy_outline_color"] = aim_result.get("preferred_enemy_color")
    report["configured_enemy_outline_color"] = aim_result.get("configured_enemy_color")
    report["combat"] = combat_result
    report["kd_detection_version"] = "offline_video_calibrated_v2"
    report["aim_detection_version"] = "head_zone_robust_primary_only_v74"
    report["movement_detection_version"] = "video_relative_motion_v74"
    report["tier_prediction"] = predict_tier(report)
    report["feedback"] = base._feedback(report, str(config.get("language", "ko")))
    tier = report["tier_prediction"]
    report["feedback"].insert(0, f"예상 티어는 {tier.get('tier_ko', '판정 불가')} 범위로 표시합니다. 단일 영상 기반이라 신뢰도가 낮을수록 범위를 넓게 잡습니다.")
    if report.get("aim_score") is not None:
        report["feedback"].insert(1, f"에임 {float(report['aim_score']):.0f}/100 · 정확한 한 점이 아니라 적 머리 영역(head zone)까지의 보정 거리를 사용해 과도한 감점을 줄였습니다. 신뢰도 {float(report.get('aim_confidence') or 0)*100:.0f}%.")
    if report.get("movement_score") is not None:
        report["feedback"].insert(2, f"무빙/사격 안정성 {float(report['movement_score']):.0f}/100 · 플릭 자체를 벌점으로 보지 않고, 영상 평소 카메라 움직임보다 사격 순간 움직임이 비정상적으로 클 때만 감점합니다.")
    report["timeline_events"] = _timeline(report, aim_result)
    report["limitations"] = ["Aim v7.4 scores distance to a tolerant head zone; it is a coaching metric, not hit probability.", "Movement v7.4 is relative video camera-motion/shot-discipline scoring; actual WASD inputs are unavailable.", "Tier v7.4 is a broad mechanics range with reliability-aware shrinkage, not Riot account rank or a trained rank classifier.", "K/D remains a video-HUD estimate and can miss unusual HUD scale, crop, overlays or edited videos."]
    rp = report.get("report_path")
    if rp:
        try:
            Path(rp).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass
    if progress:
        progress(1.0, "재보정 분석 완료")
    return report
