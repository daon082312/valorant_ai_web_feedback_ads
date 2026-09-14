from __future__ import annotations

import json
from pathlib import Path

import video_review_v74 as v74
from video_combat_detector_v3 import VideoCombatDetectorV3


def analyze_video(video_path, config, root_dir, progress=None):
    """v7.5 review: v7.4 scoring + stricter self K/D state-machine detector."""
    old_detector = v74.VideoCombatDetector
    v74.VideoCombatDetector = VideoCombatDetectorV3
    try:
        report = v74.analyze_video(video_path, config, root_dir, progress)
    finally:
        v74.VideoCombatDetector = old_detector

    report["kd_detection_version"] = "offline_self_hud_state_machine_v3"
    report["combat_auto"] = json.loads(json.dumps(report.get("combat") or {}))
    report["combat_corrections"] = []
    report["limitations"] = list(report.get("limitations") or [])
    report["limitations"].append(
        "K/D v7.5 is deliberately conservative. Use the event correction controls if a HUD scale/crop causes a miss or false positive; AI coaching uses the corrected result."
    )

    rp = report.get("report_path")
    if rp:
        try:
            Path(rp).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass
    return report
