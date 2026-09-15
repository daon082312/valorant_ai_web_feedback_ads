from __future__ import annotations

import json
from pathlib import Path

from tier_predictor_v714 import predict_tier
from video_review_v713 import analyze_video as analyze_video_v713, ensure_v713_calibration


def ensure_v714_tier(report: dict) -> dict:
    ensure_v713_calibration(report)
    report["tier_prediction"] = predict_tier(report)
    report["tier_detection_version"] = "aim_movement_skill_single_9_tier_v714"
    return report


def analyze_video(video_path, config, root_dir, progress=None):
    report = analyze_video_v713(video_path, config, root_dir, progress)
    ensure_v714_tier(report)

    tier = report.get("tier_prediction") or {}
    cleaned = []
    for item in report.get("feedback") or []:
        text = str(item)
        if "티어 평가" in text and ("무빙" in text or "스킬" in text):
            continue
        cleaned.append(text)

    cleaned.insert(
        2,
        "티어 평가는 에임 31% · 헤드라인 10% · 무빙 27% · 스킬 20% · 스프레이 12%를 사용합니다. 상위 티어는 에임뿐 아니라 무빙 기준도 통과해야 합니다.",
    )
    if bool(tier.get("skill_gate_active")):
        cleaned.insert(3, "이 영상은 스킬 사용 근거가 충분해 스킬 점수도 상위 티어 통과 기준에 직접 반영했습니다.")
    else:
        cleaned.insert(3, "스킬 사용 근거가 적은 영상에서는 스킬 점수는 가중치에는 반영하지만, 상위 티어를 강제로 제한하는 기준으로는 쓰지 않습니다.")
    report["feedback"] = cleaned

    rp = report.get("report_path")
    if rp:
        try:
            Path(rp).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass
    return report
