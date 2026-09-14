from __future__ import annotations

import json
import time
from pathlib import Path

import cv2

import video_review as base
from ammo_shot_detector import AmmoShotDetector
from tier_predictor import predict_tier
from video_aim_analyzer_v73 import VideoAimAnalyzerV73
from video_combat_detector import VideoCombatDetector


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
        if t is None:
            continue
        slot = str(item.get("slot_id") or "skill")
        events.append({"video_seconds": float(t), "kind": "skill", "label": skill_names.get(slot, "스킬")})
    for burst in report.get("shot_bursts") or []:
        if burst:
            events.append({"video_seconds": float(burst[0]), "kind": "shot", "label": f"사격 {len(burst)}발"})
    for item in aim_result.get("aim_events") or []:
        t = item.get("video_seconds")
        if t is None:
            continue
        events.append({
            "video_seconds": float(t), "kind": "aim",
            "label": f"에임 {float(item.get('aim_score', 0)):.0f}점",
            "score": item.get("aim_score"), "source": item.get("source"),
        })
    events.sort(key=lambda e: (float(e["video_seconds"]), e["kind"]))
    compact: list[dict] = []
    for e in events:
        if compact and e["kind"] == compact[-1]["kind"] and abs(float(e["video_seconds"]) - float(compact[-1]["video_seconds"])) < 0.20:
            continue
        compact.append(e)
    return compact[:300]


def analyze_video(video_path, config, root_dir, progress=None):
    """v7.3: offline base review plus tighter shot-time aim scoring."""
    def first_progress(frac, text):
        if progress:
            progress(min(0.55, float(frac) * 0.55), f"1/2 기본 분석 · {text}")

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
    aim = VideoAimAnalyzerV73(config)
    combat = VideoCombatDetector(config)

    shot_times: list[float] = []
    ammo_updates: list[dict] = []
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
            progress(0.55 + min(0.44, frac * 0.44), f"2/2 에임·K/D 재검증 · {t:.1f}s")
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
    report["aim_detection_version"] = "tight_shot_nearest_target_screen_norm_v73"
    report["tier_prediction"] = predict_tier(report)
    report["feedback"] = base._feedback(report, str(config.get("language", "ko")))

    if report.get("aim_score") is not None:
        method_map = {
            "shot_synchronized_v73": "실제 발사 직전/직후",
            "shot_plus_kill_anchor_v73": "실제 발사+킬 시점",
            "shot_plus_sparse_fallback_v73": "발사 시점+일부 지속 적 후보",
            "persistent_target_fallback_v73": "지속 적 후보(낮은 신뢰도)",
        }
        method_text = method_map.get(str(report.get("aim_method")), str(report.get("aim_method")))
        report["feedback"].insert(1, f"에임은 {method_text} 기준이며 신뢰도 {float(report.get('aim_confidence') or 0)*100:.0f}%입니다. 발사 시점과 먼 적 후보는 점수에서 제외합니다.")
    else:
        report["feedback"].insert(1, "신뢰할 수 있는 발사 시점+적 윤곽 조합이 부족해 에임 점수를 억지로 만들지 않았습니다. 적 윤곽색 설정과 영상 해상도를 확인하세요.")

    report["timeline_events"] = _timeline(report, aim_result)
    report["limitations"] = [
        "Aim v7.3 uses a tight shot-time window and selects the persistent enemy candidate closest to the crosshair; uncertain targets are omitted.",
        "Aim score is normalized by screen height to reduce target-distance bias. Persistent-target fallback is capped at low confidence.",
        "K/D remains a video-HUD estimate and can miss unusual HUD scale, crop, overlays or edited videos.",
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
