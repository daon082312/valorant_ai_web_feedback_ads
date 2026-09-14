from __future__ import annotations

import json
import time
from pathlib import Path
import cv2

import video_review as base
from ammo_shot_detector import AmmoShotDetector
from tier_predictor import predict_tier
from video_aim_analyzer_v72 import VideoAimAnalyzerV72
from video_combat_detector import VideoCombatDetector


def _timeline(report: dict, aim_result: dict) -> list[dict]:
    events = []
    for item in (report.get("combat") or {}).get("events") or []:
        t = item.get("video_seconds")
        if t is None: continue
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
            progress(min(0.58, float(frac) * 0.58), f"1/2 기본 분석 · {text}")
    report = base.analyze_video(video_path, config, root_dir, first_progress)

    path = Path(video_path)
    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        return report
    source_fps = float(cap.get(cv2.CAP_PROP_FPS) or 30.0)
    frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    refine_fps = max(8.0, min(15.0, float(config.get("video_review_fps", 8))))
    step = max(1, int(round(source_fps / refine_fps)))

    ammo_cfg = dict(config)
    ammo_cfg["ammo_hud_detection"] = True
    ammo_cfg["ammo_hud_detection_fps"] = 24
    ammo_cfg["ammo_hud_min_confidence"] = min(float(ammo_cfg.get("ammo_hud_min_confidence", 0.53)), 0.55)
    ammo = AmmoShotDetector(ammo_cfg)
    aim = VideoAimAnalyzerV72(config)
    combat = VideoCombatDetector(config)

    shot_times = []; ammo_updates = []; base_ts = time.time(); idx = processed = 0; frame_size_set = False
    while True:
        ok, frame = cap.read()
        if not ok: break
        if idx % step != 0:
            idx += 1; continue
        t = idx / source_fps if source_fps > 0 else processed / refine_fps
        processed += 1
        if not frame_size_set:
            aim.set_frame_size(frame.shape[1], frame.shape[0]); frame_size_set = True
        aim.process(frame, t)
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
            progress(0.58 + min(0.41, frac * 0.41), f"2/2 K/D·에임 정밀 분석 · {t:.1f}s")
        idx += 1
    cap.release()

    combat_result = combat.summary()
    kill_times = [float(e.get("video_seconds")) for e in combat_result.get("events") or [] if e.get("kind") == "kill" and e.get("video_seconds") is not None]
    aim_result = aim.score(sorted(shot_times), kill_times)

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
    report["aim_evaluated_events"] = int(aim_result.get("evaluated_events") or 0)
    report["aim_method"] = aim_result.get("method")
    report["aim_confidence"] = aim_result.get("confidence")
    report["aim_events"] = aim_result.get("aim_events") or []
    report["auto_enemy_outline_color"] = aim_result.get("preferred_enemy_color")
    report["enemy_color_votes"] = aim_result.get("color_votes") or {}
    report["combat"] = combat_result
    report["kd_detection_version"] = "offline_video_calibrated_v2"
    report["aim_detection_version"] = "shot_or_persistent_target_v3"
    report["tier_prediction"] = predict_tier(report)
    report["feedback"] = base._feedback(report, str(config.get("language", "ko")))
    if report.get("aim_score") is not None:
        method_text = {"shot_synchronized": "실제 발사 시점", "shot_plus_kill_anchor": "발사+킬 시점", "shot_plus_persistent_fallback": "발사+지속 적 후보", "persistent_target_fallback": "지속 적 후보"}.get(str(report.get("aim_method")), str(report.get("aim_method")))
        report["feedback"].insert(1, f"에임 판정 근거는 {method_text}이며 신뢰도는 {float(report.get('aim_confidence') or 0)*100:.0f}%입니다.")
    report["timeline_events"] = _timeline(report, aim_result)
    report["limitations"] = [
        "Aim primarily uses ammo-confirmed shots; when those cannot be paired with an enemy, persistent enemy-visible engagement frames are used and confidence is capped.",
        "Enemy outline color is auto-selected among red/purple/yellow. Unusual colorblind settings, video filters or very low saturation can still reduce detection.",
        "K/D uses video-calibrated self-HUD transitions and can still miss unusual HUD scale, crop, overlays or edited videos.",
        "Movement remains a video-motion estimate because keyboard inputs are not recorded in v7.",
    ]
    rp = report.get("report_path")
    if rp:
        try: Path(rp).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception: pass
    if progress: progress(1.0, "정밀 분석 완료")
    return report
