from __future__ import annotations

import json
import statistics
import time
from pathlib import Path


class PersonalCoachProfile:
    """Small on-device personal baseline learner.

    This is not a neural model. It learns robust rolling baselines from the user's
    own completed sessions and keeps normal/brawl modes separate.
    """

    HIGHER_BETTER = ("movement_score", "aim_score", "skill_score")
    LOWER_BETTER = ("moving_shot_ratio", "avg_shots_per_fight")

    def __init__(self, root_dir: Path, max_sessions: int = 30):
        self.root_dir = Path(root_dir)
        self.path = self.root_dir / "data" / "personal_profile.json"
        self.max_sessions = max(8, int(max_sessions))
        self.data = self._load()

    def _blank(self) -> dict:
        return {"version": 1, "modes": {"normal": {"sessions": []}, "brawl": {"sessions": []}}}

    def _load(self) -> dict:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                raise ValueError
            raw.setdefault("version", 1)
            raw.setdefault("modes", {})
            raw["modes"].setdefault("normal", {"sessions": []})
            raw["modes"].setdefault("brawl", {"sessions": []})
            return raw
        except Exception:
            return self._blank()

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8")

    @staticmethod
    def _num(value):
        try:
            if value is None:
                return None
            return float(value)
        except (TypeError, ValueError):
            return None

    def sessions(self, mode: str) -> list[dict]:
        mode = "brawl" if mode == "brawl" else "normal"
        return list(self.data.get("modes", {}).get(mode, {}).get("sessions", []))

    def baseline(self, mode: str) -> dict:
        sessions = self.sessions(mode)
        keys = (
            "movement_score",
            "aim_score",
            "skill_score",
            "moving_shot_ratio",
            "stop_to_shot_ms",
            "avg_shots_per_fight",
        )
        out = {"session_count": len(sessions)}
        for key in keys:
            vals = [self._num(item.get(key)) for item in sessions]
            vals = [v for v in vals if v is not None]
            out[key] = round(float(statistics.median(vals)), 3) if vals else None
        return out

    def compare(self, metrics: dict, mode: str) -> dict:
        base = self.baseline(mode)
        result = {"session_count": base["session_count"], "baseline": base, "deltas": {}, "messages": []}
        if base["session_count"] < 2:
            result["messages"].append("개인 기준 학습 중 · 2개 이상의 유효 세션이 쌓이면 평소 대비 비교를 시작합니다.")
            return result

        labels = {
            "movement_score": "무빙 점수",
            "aim_score": "에임 점수",
            "skill_score": "스킬 점수",
            "moving_shot_ratio": "이동사격 비율",
            "stop_to_shot_ms": "정지→첫 탄",
            "avg_shots_per_fight": "교전당 발수",
        }
        for key in self.HIGHER_BETTER + self.LOWER_BETTER + ("stop_to_shot_ms",):
            cur = self._num(metrics.get(key))
            ref = self._num(base.get(key))
            if cur is None or ref is None:
                continue
            delta = cur - ref
            result["deltas"][key] = round(delta, 3)
            if key in self.HIGHER_BETTER and abs(delta) >= 3.0:
                word = "높음" if delta > 0 else "낮음"
                result["messages"].append(f"{labels[key]} · 개인 기준보다 {abs(delta):.1f}점 {word}")
            elif key == "moving_shot_ratio" and abs(delta) >= 0.05:
                word = "낮아짐" if delta < 0 else "높아짐"
                result["messages"].append(f"{labels[key]} · 평소 대비 {abs(delta) * 100:.0f}%p {word}")
            elif key == "avg_shots_per_fight" and abs(delta) >= 1.0:
                word = "짧아짐" if delta < 0 else "길어짐"
                result["messages"].append(f"{labels[key]} · 평소 대비 {abs(delta):.1f}발 {word}")
            elif key == "stop_to_shot_ms" and abs(delta) >= 45.0:
                word = "빨라짐" if delta < 0 else "느려짐"
                result["messages"].append(f"{labels[key]} · 평소 대비 {abs(delta):.0f}ms {word}")
        return result

    def learn(self, metrics: dict, mode: str) -> dict:
        mode = "brawl" if mode == "brawl" else "normal"
        cleaned = {"timestamp": time.time()}
        for key in (
            "movement_score",
            "aim_score",
            "skill_score",
            "moving_shot_ratio",
            "stop_to_shot_ms",
            "avg_shots_per_fight",
            "fight_count",
            "shots",
        ):
            value = self._num(metrics.get(key))
            if value is not None:
                cleaned[key] = round(value, 4)
        sessions = self.data.setdefault("modes", {}).setdefault(mode, {"sessions": []}).setdefault("sessions", [])
        sessions.append(cleaned)
        del sessions[:-self.max_sessions]
        self._save()
        return self.baseline(mode)
