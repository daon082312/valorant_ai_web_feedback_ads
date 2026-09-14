from __future__ import annotations


def _num(value):
    try:
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return None


def predict_tier(report: dict) -> dict:
    """Estimate a broad VALORANT tier from video-observable mechanics only.

    This is intentionally not an account-rank lookup and should be treated as a
    coaching estimate. Missing metrics are omitted and the remaining weights are
    renormalized.
    """
    metrics = {
        "aim": (_num(report.get("aim_score")), 0.40),
        "headline": (_num(report.get("headline_score")), 0.20),
        "movement": (_num(report.get("movement_score")), 0.25),
        "skill": (_num(report.get("skill_score")), 0.10),
        "spray": (_num(report.get("spray_control_score")), 0.05),
    }
    available = [(name, value, weight) for name, (value, weight) in metrics.items() if value is not None]
    if not available:
        return {"tier": "Unrated", "tier_ko": "판정 불가", "score": None, "confidence": 0.0, "confidence_label": "low", "basis": []}

    total_weight = sum(weight for _name, _value, weight in available)
    score = sum(value * weight for _name, value, weight in available) / max(1e-6, total_weight)
    bands = [
        (95, "Immortal", "불멸"), (90, "Ascendant", "초월자"), (83, "Diamond", "다이아몬드"),
        (75, "Platinum", "플래티넘"), (65, "Gold", "골드"), (55, "Silver", "실버"),
        (45, "Bronze", "브론즈"), (0, "Iron", "아이언"),
    ]
    tier, tier_ko = "Iron", "아이언"
    for minimum, en, ko in bands:
        if score >= minimum:
            tier, tier_ko = en, ko
            break

    processed = max(1, int(report.get("processed_frames") or 0))
    enemy_frames = int(report.get("enemy_detection_frames") or 0)
    shots = int(report.get("confirmed_shots") or 0)
    duration = float(report.get("duration_seconds") or 0.0)
    evidence = min(1.0, enemy_frames / max(12.0, processed * 0.08))
    shot_evidence = min(1.0, shots / 30.0)
    duration_evidence = min(1.0, duration / 180.0)
    metric_evidence = min(1.0, len(available) / 5.0)
    confidence = 0.34 * evidence + 0.24 * shot_evidence + 0.18 * duration_evidence + 0.24 * metric_evidence
    confidence = max(0.05, min(0.96, confidence))
    label = "high" if confidence >= 0.72 else "medium" if confidence >= 0.45 else "low"
    return {
        "tier": tier, "tier_ko": tier_ko, "score": round(score, 1), "confidence": round(confidence, 3),
        "confidence_label": label, "basis": [name for name, _value, _weight in available],
        "note": "video_mechanics_estimate_not_account_rank",
    }
