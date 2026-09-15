from __future__ import annotations

import json
from pathlib import Path

import video_review as base
from tier_predictor_v714 import predict_tier
from video_review_v714 import analyze_video as analyze_video_v714
from video_skill_detector_v715 import RobustUtilityDetectorV715


def _dedupe_utility(events: list[dict]) -> list[dict]:
    ordered = sorted((dict(x) for x in events), key=lambda x: float(x.get("video_seconds") or 0.0))
    out: list[dict] = []
    for e in ordered:
        t = float(e.get("video_seconds") or 0.0)
        slot = str(e.get("slot_id") or "skill")
        duplicate = False
        for prev in reversed(out[-4:]):
            pt = float(prev.get("video_seconds") or 0.0)
            if t - pt > 0.32:
                break
            if str(prev.get("slot_id") or "skill") == slot and abs(t - pt) < 0.24:
                duplicate = True
                break
        if not duplicate:
            out.append(e)
    return out


def _ensure_skill_score(report: dict) -> None:
    events = _dedupe_utility(report.get("utility_events") or [])
    report["utility_events"] = events
    report["utility_use_candidates"] = len(events)

    if events and str(report.get("mode") or "") == "brawl":
        report["mode"] = "normal"

    bursts = report.get("shot_bursts") or []
    if events:
        score, detail = base._skill_score(events, bursts, "normal")
        report["skill_score"] = score
        detail = dict(detail or {})
        detail["method"] = "ability_hud_timing_v715"
        detail["detected_events"] = len(events)
        report["skill_detail"] = detail
        report["skill_confidence"] = round(min(0.94, 0.38 + len(events) * 0.11), 3)
        report["skill_method"] = "ability_hud_timing_v715"
    elif str(report.get("mode") or "") != "brawl":
        report["skill_score"] = 65.0
        report["skill_detail"] = {
            "method": "neutral_low_evidence_v715",
            "pre_fight": 0,
            "during": 0,
            "late": 0,
            "detected_events": 0,
        }
        report["skill_confidence"] = 0.18
        report["skill_method"] = "neutral_low_evidence_v715"

    timeline = [dict(e) for e in (report.get("timeline_events") or []) if str(e.get("kind")) != "skill"]
    names = {"skill1": "스킬 1", "skill2": "스킬 2", "skill3": "스킬 3", "ultimate": "궁극기"}
    for e in events:
        t = e.get("video_seconds")
        if t is None:
            continue
        slot = str(e.get("slot_id") or "skill")
        timeline.append({
            "video_seconds": float(t),
            "kind": "skill",
            "label": names.get(slot, "스킬"),
            "confidence": e.get("confidence"),
        })
    timeline.sort(key=lambda e: (float(e.get("video_seconds") or 0.0), str(e.get("kind") or "")))
    report["timeline_events"] = timeline[:300]


def analyze_video(video_path, config, root_dir, progress=None):
    old_detector = base.OfflineUtilityDetector
    base.OfflineUtilityDetector = RobustUtilityDetectorV715
    try:
        report = analyze_video_v714(video_path, config, root_dir, progress)
    finally:
        base.OfflineUtilityDetector = old_detector

    _ensure_skill_score(report)
    report["tier_prediction"] = predict_tier(report)
    report["tier_detection_version"] = "aim_movement_skill_single_9_tier_v715"
    report["skill_detection_version"] = "ability_hud_transition_v715"

    skill = report.get("skill_score")
    conf = float(report.get("skill_confidence") or 0.0) * 100.0
    feedback = [str(x) for x in (report.get("feedback") or [])]
    feedback = [x for x in feedback if "스킬 판정" not in x and "스킬 사용 근거" not in x]
    if skill is not None:
        if report.get("skill_method") == "neutral_low_evidence_v715":
            feedback.insert(3, f"스킬 점수 {float(skill):.0f}/100은 HUD 사용 근거가 부족해 중립값으로 표시하며 신뢰도는 {conf:.0f}%입니다.")
        else:
            feedback.insert(3, f"스킬 점수 {float(skill):.0f}/100 · 능력 HUD 변화 {len(report.get('utility_events') or [])}회를 교전 타이밍과 비교했습니다. 신뢰도 {conf:.0f}%입니다.")
    report["feedback"] = feedback

    rp = report.get("report_path")
    if rp:
        try:
            Path(rp).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass
    return report
