from __future__ import annotations

import json
from pathlib import Path

from tier_predictor_v711 import predict_tier
from video_review_v710 import analyze_video as analyze_video_v710


def analyze_video(video_path, config, root_dir, progress=None):
    """v7.11: keep v7.10 analysis, replace tier range with one 9-level tier."""
    report = analyze_video_v710(video_path, config, root_dir, progress)
    tier = predict_tier(report)
    report["tier_prediction"] = tier
    report["tier_detection_version"] = "single_9_tier_with_radiant_v711"

    cleaned = []
    for item in report.get("feedback") or []:
        text = str(item)
        if "예상 티어" in text and ("범위" in text or "~" in text or "–" in text):
            continue
        cleaned.append(text)
    confidence = float(tier.get("confidence") or 0.0) * 100.0
    cleaned.insert(
        0,
        f"예상 티어는 {tier.get('tier_ko', '판정 불가')} 1개로 표시합니다. 영상 근거 신뢰도는 {confidence:.0f}%입니다.",
    )
    report["feedback"] = cleaned

    rp = report.get("report_path")
    if rp:
        try:
            Path(rp).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass
    return report
