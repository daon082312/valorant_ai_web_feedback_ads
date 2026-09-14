from __future__ import annotations

import json
from pathlib import Path

import video_review_v74 as v74
from video_killfeed_detector import VideoKillfeedDetector


def analyze_video(video_path, config, root_dir, progress=None):
    """v7.10: replace K/D HUD inference with top-right self-kill killfeed detection."""
    old_detector = v74.VideoCombatDetector
    v74.VideoCombatDetector = VideoKillfeedDetector
    try:
        report = v74.analyze_video(video_path, config, root_dir, progress)
    finally:
        v74.VideoCombatDetector = old_detector

    combat = report.get("combat") or {}
    kills = int(combat.get("kills") or 0)
    combat.pop("deaths", None)
    combat.pop("kd_ratio", None)
    combat["deaths_analyzed"] = False
    combat["detector"] = "top_right_killfeed_self_highlight_v1"
    report["combat"] = combat
    report["kd_detection_version"] = None
    report["kill_detection_version"] = "top_right_killfeed_self_highlight_v1"
    report["killfeed_kills"] = kills
    report.pop("combat_auto", None)
    report.pop("combat_corrections", None)

    cleaned = []
    for line in report.get("feedback") or []:
        text = str(line)
        lower = text.casefold()
        if any(token in lower for token in ("내 데스", "k/d", "death hud", "deaths hud", "데스 hud")):
            continue
        cleaned.append(text)
    cleaned.insert(0, f"오른쪽 위 킬로그의 '내 킬 강조 테두리'를 기준으로 내 킬 {kills}회를 감지했습니다. 데스는 이 버전에서 분석하지 않습니다.")
    report["feedback"] = cleaned

    limitations = [x for x in (report.get("limitations") or []) if "K/D" not in str(x) and "death" not in str(x).casefold()]
    limitations.append("Kills are estimated only from the top-right VALORANT killfeed highlight for the local player's own kills. Deaths are intentionally not analyzed.")
    limitations.append("Killfeed detection can miss or misclassify heavily cropped videos, unusual HUD scale, observer/replay overlays, or edited footage.")
    report["limitations"] = limitations

    rp = report.get("report_path")
    if rp:
        try:
            Path(rp).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass
    return report
