from __future__ import annotations


def _num(value):
    try:
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return None


TIERS = [
    ("Iron", "아이언", "#8A9199"),
    ("Bronze", "브론즈", "#C57A44"),
    ("Silver", "실버", "#C7D1DB"),
    ("Gold", "골드", "#FFD166"),
    ("Platinum", "플래티넘", "#4FD1C5"),
    ("Diamond", "다이아몬드", "#6EA8FE"),
    ("Ascendant", "초월자", "#59D185"),
    ("Immortal", "불멸", "#FF5C9A"),
    ("Radiant", "레디언트", "#FFF0B3"),
]

THRESHOLDS = [0.0, 42.0, 51.0, 59.0, 68.0, 77.0, 85.0, 92.0, 97.0]


def _tier_index(score: float) -> int:
    idx = 0
    for i, threshold in enumerate(THRESHOLDS):
        if score >= threshold:
            idx = i
    return min(len(TIERS) - 1, idx)


def predict_tier(report: dict) -> dict:
    """Return exactly one mechanics tier, including Radiant."""
    aim_conf = max(0.0, min(1.0, float(report.get("aim_confidence") or 0.0)))
    bursts = int(report.get("shot_burst_count") or 0)
    duration = float(report.get("duration_seconds") or 0.0)
    utility_count = len(report.get("utility_events") or [])

    raw = {
        "aim": (_num(report.get("aim_score")), 0.34, max(0.25, aim_conf)),
        "headline": (_num(report.get("headline_score")), 0.14, max(0.25, aim_conf)),
        "movement": (_num(report.get("movement_score")), 0.27, min(1.0, 0.35 + bursts / 10.0)),
        "skill": (_num(report.get("skill_score")), 0.10, min(1.0, 0.25 + utility_count / 8.0)),
        "spray": (_num(report.get("spray_control_score")), 0.15, min(1.0, 0.35 + bursts / 10.0)),
    }

    available = []
    for name, (value, weight, reliability) in raw.items():
        if value is None:
            continue
        adjusted = 70.0 + reliability * (value - 70.0)
        effective_weight = weight * (0.45 + 0.55 * reliability)
        available.append((name, adjusted, effective_weight, value, reliability))

    if not available:
        return {
            "tier": "Unrated",
            "tier_ko": "판정 불가",
            "tier_color": "#98A2B3",
            "score": None,
            "confidence": 0.0,
            "confidence_label": "low",
            "basis": [],
            "note": "single_video_mechanics_tier_v711_not_account_rank",
        }

    total_weight = sum(x[2] for x in available)
    score = sum(x[1] * x[2] for x in available) / max(1e-6, total_weight)

    metric_evidence = min(1.0, len(available) / 5.0)
    burst_evidence = min(1.0, bursts / 10.0)
    duration_evidence = min(1.0, duration / 180.0)
    reliability_mean = sum(x[4] * x[2] for x in available) / max(1e-6, total_weight)
    confidence = 0.30 * metric_evidence + 0.25 * burst_evidence + 0.20 * duration_evidence + 0.25 * reliability_mean
    confidence = max(0.08, min(0.95, confidence))

    idx = _tier_index(score)
    tier, tier_ko, tier_color = TIERS[idx]
    label = "high" if confidence >= 0.72 else "medium" if confidence >= 0.45 else "low"

    return {
        "tier": tier,
        "tier_ko": tier_ko,
        "tier_color": tier_color,
        "tier_index": idx,
        "score": round(score, 1),
        "confidence": round(confidence, 3),
        "confidence_label": label,
        "basis": [x[0] for x in available],
        "raw_metrics": {x[0]: round(x[3], 1) for x in available},
        "thresholds": list(THRESHOLDS),
        "note": "single_video_mechanics_tier_v711_not_account_rank",
    }
