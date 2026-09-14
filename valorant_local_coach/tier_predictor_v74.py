from __future__ import annotations


def _num(value):
    try:
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return None


TIERS = [
    ("Iron", "아이언"), ("Bronze", "브론즈"), ("Silver", "실버"), ("Gold", "골드"),
    ("Platinum", "플래티넘"), ("Diamond", "다이아몬드"), ("Ascendant", "초월자"), ("Immortal", "불멸"),
]


def _tier_index(score: float) -> int:
    thresholds = [0, 42, 51, 59, 68, 77, 85, 92]
    idx = 0
    for i, threshold in enumerate(thresholds):
        if score >= threshold:
            idx = i
    return min(len(TIERS) - 1, idx)


def predict_tier(report: dict) -> dict:
    """Return a broad mechanics tier/range with reliability-aware shrinkage."""
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
        return {"tier": "Unrated", "tier_ko": "판정 불가", "score": None, "confidence": 0.0, "confidence_label": "low", "basis": []}

    total_weight = sum(x[2] for x in available)
    score = sum(x[1] * x[2] for x in available) / max(1e-6, total_weight)

    metric_evidence = min(1.0, len(available) / 5.0)
    burst_evidence = min(1.0, bursts / 10.0)
    duration_evidence = min(1.0, duration / 180.0)
    reliability_mean = sum(x[4] * x[2] for x in available) / max(1e-6, total_weight)
    confidence = 0.30 * metric_evidence + 0.25 * burst_evidence + 0.20 * duration_evidence + 0.25 * reliability_mean
    confidence = max(0.08, min(0.95, confidence))

    center_idx = _tier_index(score)
    center_en, center_ko = TIERS[center_idx]
    if confidence < 0.72:
        if confidence >= 0.38:
            thresholds = [0, 42, 51, 59, 68, 77, 85, 92]
            lower = thresholds[center_idx]
            upper = thresholds[center_idx + 1] if center_idx + 1 < len(thresholds) else 100.0
            midpoint = (lower + upper) / 2.0
            if score >= midpoint and center_idx < len(TIERS) - 1:
                low_idx, high_idx = center_idx, center_idx + 1
            elif center_idx > 0:
                low_idx, high_idx = center_idx - 1, center_idx
            else:
                low_idx, high_idx = 0, min(1, len(TIERS) - 1)
        else:
            low_idx = max(0, center_idx - 1)
            high_idx = min(len(TIERS) - 1, center_idx + 1)
        low_en, low_ko = TIERS[low_idx]
        high_en, high_ko = TIERS[high_idx]
        tier = center_en if low_idx == high_idx else f"{low_en}–{high_en}"
        tier_ko = center_ko if low_idx == high_idx else f"{low_ko}~{high_ko}"
    else:
        tier, tier_ko = center_en, center_ko

    label = "high" if confidence >= 0.72 else "medium" if confidence >= 0.45 else "low"
    return {
        "tier": tier,
        "tier_ko": tier_ko,
        "center_tier": center_en,
        "center_tier_ko": center_ko,
        "score": round(score, 1),
        "confidence": round(confidence, 3),
        "confidence_label": label,
        "basis": [x[0] for x in available],
        "raw_metrics": {x[0]: round(x[3], 1) for x in available},
        "note": "broad_video_mechanics_range_v74_not_account_rank",
    }
