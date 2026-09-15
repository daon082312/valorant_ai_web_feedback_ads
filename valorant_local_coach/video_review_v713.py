from __future__ import annotations

import json
from pathlib import Path

from tier_predictor_v713 import predict_tier
from video_review_v710 import analyze_video as analyze_video_v710

AIM_CALIBRATION_VERSION = "slight_up_v713"


def calibrate_aim_score(score):
    """Small but visible upward calibration for the coaching score."""
    try:
        if score is None:
            return None
        score = float(score)
    except (TypeError, ValueError):
        return score

    if score >= 97.0:
        bonus = 0.8
    elif score >= 90.0:
        bonus = 1.8
    elif score >= 80.0:
        bonus = 3.2
    elif score >= 65.0:
        bonus = 4.5
    else:
        bonus = 5.0
    return round(min(100.0, score + bonus), 1)


def ensure_v713_calibration(report: dict) -> dict:
    if report.get("aim_calibration_version") != AIM_CALIBRATION_VERSION:
        original = report.get("aim_score")
        calibrated = calibrate_aim_score(original)
        report["aim_score_raw"] = original
        report["aim_score"] = calibrated
        report["aim_calibration_version"] = AIM_CALIBRATION_VERSION

    report["tier_prediction"] = predict_tier(report)
    report["tier_detection_version"] = "single_9_tier_stricter_aim_gate_v713"
    return report


def analyze_video(video_path, config, root_dir, progress=None):
    report = analyze_video_v710(video_path, config, root_dir, progress)
    ensure_v713_calibration(report)

    tier = report.get("tier_prediction") or {}
    confidence = float(tier.get("confidence") or 0.0) * 100.0
    cleaned = []
    for item in report.get("feedback") or []:
        text = str(item)
        if "에임 점수는 v7.12" in text:
            continue
        if "예상 티어" in text and ("범위" in text or "~" in text or "–" in text):
            continue
        cleaned.append(text)
    cleaned.insert(0, f"예상 티어는 {tier.get('tier_ko', '판정 불가')} 1개로 표시합니다. 영상 근거 신뢰도는 {confidence:.0f}%입니다.")
    cleaned.insert(1, "에임 점수는 원시 측정값보다 소폭 상향 보정하며, 플래티넘 이상부터는 별도의 더 높은 에임 기준을 통과해야 합니다.")
    report["feedback"] = cleaned

    rp = report.get("report_path")
    if rp:
        try:
            Path(rp).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass
    return report
