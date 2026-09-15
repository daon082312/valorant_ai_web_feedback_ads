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

AIM_GATES = {4: 62.0, 5: 72.0, 6: 80.0, 7: 88.0, 8: 95.0}
MOVEMENT_GATES = {4: 58.0, 5: 66.0, 6: 74.0, 7: 82.0, 8: 90.0}
SKILL_GATES = {4: 52.0, 5: 60.0, 6: 68.0, 7: 76.0, 8: 84.0}

WEIGHTS = {
    "aim": 0.31,
    "headline": 0.10,
    "movement": 0.27,
    "skill": 0.20,
    "spray": 0.12,
}


def _tier_index(score: float) -> int:
    idx = 0
    for i, threshold in enumerate(THRESHOLDS):
        if score >= threshold:
            idx = i
    return min(len(TIERS) - 1, idx)


def _gate_down(idx: int, value: float | None, gates: dict[int, float], *, missing_cap: int | None = None) -> int:
    if value is None:
        return min(idx, missing_cap) if missing_cap is not None else idx
    while idx >= 4:
        required = gates.get(idx)
        if required is None or float(value) >= required:
            break
        idx -= 1
    return max(0, idx)


def predict_tier(report: dict) -> dict:
    aim_conf = max(0.0, min(1.0, float(report.get("aim_confidence") or 0.0)))
    bursts = int(report.get("shot_burst_count") or 0)
    duration = float(report.get("duration_seconds") or 0.0)
    utility_count = len(report.get("utility_events") or [])

    aim_value = _num(report.get("aim_score"))
    movement_value = _num(report.get("movement_score"))
    skill_value = _num(report.get("skill_score"))
    spray_value = _num(report.get("spray_control_score"))
    headline_value = _num(report.get("headline_score"))

    movement_rel = min(1.0, 0.42 + bursts / 10.0)
    skill_rel = min(1.0, 0.28 + utility_count / 6.0)
    spray_rel = min(1.0, 0.35 + bursts / 10.0)

    raw = {
        "aim": (aim_value, WEIGHTS["aim"], max(0.28, aim_conf)),
        "headline": (headline_value, WEIGHTS["headline"], max(0.25, aim_conf)),
        "movement": (movement_value, WEIGHTS["movement"], movement_rel),
        "skill": (skill_value, WEIGHTS["skill"], skill_rel),
        "spray": (spray_value, WEIGHTS["spray"], spray_rel),
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
            "weights": dict(WEIGHTS),
            "note": "single_video_mechanics_tier_v714_not_account_rank",
        }

    total_weight = sum(x[2] for x in available)
    score = sum(x[1] * x[2] for x in available) / max(1e-6, total_weight)

    metric_evidence = min(1.0, len(available) / 5.0)
    burst_evidence = min(1.0, bursts / 10.0)
    duration_evidence = min(1.0, duration / 180.0)
    reliability_mean = sum(x[4] * x[2] for x in available) / max(1e-6, total_weight)
    confidence = (
        0.30 * metric_evidence
        + 0.25 * burst_evidence
        + 0.20 * duration_evidence
        + 0.25 * reliability_mean
    )
    confidence = max(0.08, min(0.95, confidence))

    raw_idx = _tier_index(score)
    idx_after_aim = _gate_down(raw_idx, aim_value, AIM_GATES, missing_cap=5)
    idx_after_movement = _gate_down(idx_after_aim, movement_value, MOVEMENT_GATES, missing_cap=5)

    skill_gate_active = skill_value is not None and utility_count >= 2 and str(report.get("mode") or "") != "brawl"
    idx = _gate_down(idx_after_movement, skill_value, SKILL_GATES) if skill_gate_active else idx_after_movement

    tier, tier_ko, tier_color = TIERS[idx]
    label = "high" if confidence >= 0.72 else "medium" if confidence >= 0.45 else "low"

    return {
        "tier": tier,
        "tier_ko": tier_ko,
        "tier_color": tier_color,
        "tier_index": idx,
        "raw_tier_index": raw_idx,
        "score": round(score, 1),
        "confidence": round(confidence, 3),
        "confidence_label": label,
        "basis": [x[0] for x in available],
        "raw_metrics": {x[0]: round(x[3], 1) for x in available},
        "weights": dict(WEIGHTS),
        "thresholds": list(THRESHOLDS),
        "aim_gates": dict(AIM_GATES),
        "movement_gates": dict(MOVEMENT_GATES),
        "skill_gates": dict(SKILL_GATES),
        "skill_gate_active": bool(skill_gate_active),
        "utility_evidence_count": utility_count,
        "note": "single_video_mechanics_tier_v714_not_account_rank",
    }
