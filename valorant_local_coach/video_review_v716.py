from __future__ import annotations

import json
from pathlib import Path

import video_review_v710 as v710
from video_killfeed_detector_v2 import VideoKillfeedDetectorV2
from video_review_v715 import analyze_video as analyze_video_v715


def analyze_video(video_path, config, root_dir, progress=None):
    old = v710.VideoKillfeedDetector
    v710.VideoKillfeedDetector = VideoKillfeedDetectorV2
    try:
        report = analyze_video_v715(video_path, config, root_dir, progress)
    finally:
        v710.VideoKillfeedDetector = old

    combat = report.get("combat") or {}
    kills = int(combat.get("kills") or 0)
    combat["detector"] = "top_right_killfeed_self_highlight_v2_confirmed_dedup"
    report["combat"] = combat
    report["killfeed_kills"] = kills
    report["kill_detection_version"] = "top_right_killfeed_self_highlight_v2_confirmed_dedup"

    feedback = []
    for item in report.get("feedback") or []:
        text = str(item)
        if "오른쪽 위 킬로그" in text and "킬" in text and "감지" in text:
            continue
        feedback.append(text)
    feedback.insert(0, f"오른쪽 위 킬로그를 2프레임 확인하고 중복 행을 제거한 뒤 내 킬 {kills}회를 감지했습니다.")
    report["feedback"] = feedback

    rp = report.get("report_path")
    if rp:
        try:
            Path(rp).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass
    return report
