from __future__ import annotations

import json
import statistics
from pathlib import Path


DEFAULT_PROFILE = {
    "profile_name": "Pro-style starter reference",
    "source": "heuristic_seed_not_measured_pro_data",
    "note": "실제 프로 키입력 데이터가 아니라 초기 비교용 기준입니다. reference_sessions에서 학습하면 자동 교체됩니다.",
    "moving_shot_ratio_max": 0.18,
    "walk_shot_ratio_max": 0.12,
    "crouch_shot_ratio_max": 0.40,
    "crouch_spray_ratio_max": 0.28,
    "stop_to_shot_median_ms_min": 45.0,
    "stop_to_shot_median_ms_max": 240.0,
    "opposite_tap_ratio_min": 0.10,
}


class ProMovementProfile:
    def __init__(self, path: Path):
        self.path = path
        self.data = self._load()

    def _load(self) -> dict:
        if not self.path.exists():
            self.path.write_text(json.dumps(DEFAULT_PROFILE, ensure_ascii=False, indent=2), encoding="utf-8")
            return dict(DEFAULT_PROFILE)
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return {**DEFAULT_PROFILE, **data}
        except Exception:
            return dict(DEFAULT_PROFILE)

    def reload(self) -> None:
        self.data = self._load()

    def compare(self, metrics: dict) -> list[str]:
        p = self.data
        messages: list[str] = []
        moving = float(metrics.get("moving_shot_ratio") or 0)
        walk = float(metrics.get("walk_shot_ratio") or 0)
        crouch = float(metrics.get("crouch_shot_ratio") or 0)
        crouch_spray = float(metrics.get("crouch_spray_ratio") or 0)
        stop_ms = metrics.get("median_stop_to_shot_ms")
        opposite = float(metrics.get("opposite_tap_ratio") or 0)

        if moving > float(p["moving_shot_ratio_max"]):
            messages.append("참고 기준보다 이동 입력 중 사격 비율이 높습니다.")
        if walk > float(p["walk_shot_ratio_max"]):
            messages.append("Shift 워크 상태에서의 사격 비율이 참고 기준보다 높습니다.")
        if crouch > float(p["crouch_shot_ratio_max"]):
            messages.append("Ctrl 앉은 사격 의존도가 참고 기준보다 높습니다.")
        if crouch_spray > float(p["crouch_spray_ratio_max"]):
            messages.append("앉은 상태의 긴 스프레이 비중이 참고 기준보다 높습니다.")
        if stop_ms is not None:
            low = float(p["stop_to_shot_median_ms_min"])
            high = float(p["stop_to_shot_median_ms_max"])
            if stop_ms < low:
                messages.append("이동키를 놓은 직후 너무 빠르게 첫 탄이 나가는 패턴이 있습니다.")
            elif stop_ms > high:
                messages.append("정지 후 첫 탄까지 지연이 길어 피킹-사격 연결이 느린 편입니다.")
        if opposite < float(p["opposite_tap_ratio_min"]):
            messages.append("A↔D/W↔S 반대방향 탭을 이용한 빠른 정지/방향전환 패턴이 적습니다.")
        return messages


def learn_profile_from_sessions(reference_dir: Path, output_path: Path) -> dict:
    files = sorted(reference_dir.glob("*.json"))
    rows: list[dict] = []
    for path in files:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        movement = data.get("movement_metrics") or {}
        if movement:
            rows.append(movement)

    if len(rows) < 3:
        raise ValueError("기준 학습에는 movement_metrics가 들어 있는 세션 JSON이 최소 3개 필요합니다.")

    def vals(key: str) -> list[float]:
        result = []
        for row in rows:
            value = row.get(key)
            if isinstance(value, (int, float)):
                result.append(float(value))
        return result

    stop_values = vals("median_stop_to_shot_ms")
    profile = {
        "profile_name": f"Learned reference ({len(rows)} sessions)",
        "source": "reference_session_learning",
        "note": "reference_sessions 폴더의 로컬 세션 지표 중앙값/상위 범위에서 학습했습니다.",
        "sample_sessions": len(rows),
        "moving_shot_ratio_max": min(0.5, statistics.median(vals("moving_shot_ratio")) * 1.25),
        "walk_shot_ratio_max": min(0.5, statistics.median(vals("walk_shot_ratio")) * 1.35),
        "crouch_shot_ratio_max": min(0.8, statistics.median(vals("crouch_shot_ratio")) * 1.30),
        "crouch_spray_ratio_max": min(0.8, statistics.median(vals("crouch_spray_ratio")) * 1.30),
        "stop_to_shot_median_ms_min": max(0.0, statistics.median(stop_values) * 0.55) if stop_values else 45.0,
        "stop_to_shot_median_ms_max": statistics.median(stop_values) * 1.55 if stop_values else 240.0,
        "opposite_tap_ratio_min": max(0.0, statistics.median(vals("opposite_tap_ratio")) * 0.65),
    }
    output_path.write_text(json.dumps(profile, ensure_ascii=False, indent=2), encoding="utf-8")
    return profile
