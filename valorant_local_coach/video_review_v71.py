from __future__ import annotations

import json
import time
from pathlib import Path

import cv2

import video_review as base
from ammo_shot_detector import AmmoShotDetector
from tier_predictor import predict_tier
from video_aim_analyzer import VideoAimAnalyzer
from video_combat_detector import VideoCombatDetector


def analyze_video(video_path, config, root_dir, progress=None):
    """v7.1 two-pass review.

    Pass 1 keeps the existing utility/movement/minimap analysis. Pass 2 runs at a
    higher sampling rate and recalibrates ammo shots, self K/D and shot-synchronized
    aim. The second pass is offline-only and cannot affect in-game FPS.
    """
    def first_progress(frac, text):
        if progress:
            progress(min(0.62, float(frac) * 0.62), f"1/2 기본 분석 · {text}")

    report = base.analyze_video(video_path, config, root_dir, first_progress)

    path = Path(video_path)
    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        return report
    source_fps = float(cap.get(cv2.CAP_PROP_FPS) or 30.0)
    frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    refine_fps = max(8.0, min(12.0, float(config.get("video_review_fps", 8))))
    step = max(1, int(round(source_fps / refine_fps)))

    ammo_cfg = dict(config)
    ammo_cfg["ammo_hud_detection"] = True
    ammo_cfg["ammo_hud_detection_fps"] = 20
    ammo_cfg["ammo_hud_min_confidence"] = min(float(ammo_cfg.get("ammo_hud_min_confidence", 0.53)), 0.56)
    ammo = AmmoShotDetector(ammo_cfg)
    aim = VideoAimAnalyzer(config)
    combat = VideoCombatDetector(config)

    shot_times = []
    ammo_updates = []
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

        update = ammo.process(frame, base_ts + t, firing_hint=True)
        recent_fire = False
        if update is not None and 1 <= int(update.shots) <= 10:
            recent_fire = True
            for n in range(int(update.shots)):
                shot_times.append(t + n * 0.008)
            ammo_updates.append({
                "video_seconds": round(t, 3), "shots": int(update.shots),
                "ammo_before": int(update.ammo_before), "ammo_after": int(update.ammo_after),
                "confidence": float(update.confidence),
            })
        combat.process(frame, t, recent_fire=recent_fire)

        if progress and processed % 8 == 0:
            frac = (idx + 1) / frame_count if frame_count > 0 else 0.0
            progress(0.62 + min(0.37, frac * 0.37), f"2/2 K/D·에임 정밀 분석 · {t:.1f}s")
        idx += 1
    cap.release()

    aim_result = aim.score_shots(sorted(shot_times))
    combat_result = combat.summary()

    report["review_fps_refined"] = refine_fps
    report["confirmed_shots"] = len(shot_times)
    report["ammo_updates"] = ammo_updates
    report["shot_bursts"] = [[round(x, 3) for x in g] for g in base._segments_from_shots(sorted(shot_times))]
    report["shot_burst_count"] = len(report["shot_bursts"])
    report["aim_score"] = aim_result.get("aim_score")
    report["headline_score"] = aim_result.get("headline_score")
    report["aim_error_px"] = aim_result.get("aim_error_px")
    report["headline_error_px"] = aim_result.get("headline_error_px")
    report["aim_evaluated_shots"] = int(aim_result.get("evaluated_shots") or 0)
    report["auto_enemy_outline_color"] = aim_result.get("preferred_enemy_color")
    report["enemy_color_votes"] = aim_result.get("color_votes") or {}
    report["combat"] = combat_result
    report["kd_detection_version"] = "offline_video_calibrated_v2"
    report["aim_detection_version"] = "shot_synchronized_persistent_target_v2"

    report["tier_prediction"] = predict_tier(report)
    report["feedback"] = base._feedback(report, str(config.get("language", "ko")))
    report["limitations"] = [
        "K/D uses video-calibrated self-HUD transitions; unusual HUD scale, crop, overlays or edited videos can still cause misses.",
        "Aim uses only ammo-confirmed shots with persistent enemy-outline candidates nearby; uncertain shots are omitted rather than guessed.",
        "Enemy outline color is auto-selected among red/purple/yellow for aim scoring.",
        "Movement remains a video-motion estimate because keyboard inputs are not recorded in v7.",
    ]

    rp = report.get("report_path")
    if rp:
        try:
            Path(rp).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass
    if progress:
        progress(1.0, "정밀 분석 완료")
    return report
