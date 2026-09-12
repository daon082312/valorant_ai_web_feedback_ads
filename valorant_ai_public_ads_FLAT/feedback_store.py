from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from supabase import create_client

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
FEEDBACK_FILE = DATA_DIR / "feedback.jsonl"
LOCK = threading.RLock()

SUPABASE_URL = os.getenv("SUPABASE_URL", "").strip()
SUPABASE_SECRET_KEY = (
    os.getenv("SUPABASE_SECRET_KEY", "").strip()
    or os.getenv("SUPABASE_SERVICE_ROLE_KEY", "").strip()
)

FEEDBACK_TABLE = "analysis_feedback"
CALIBRATION_LIMIT = int(os.getenv("FEEDBACK_CALIBRATION_LIMIT", "500"))
CALIBRATION_TTL_SECONDS = int(os.getenv("FEEDBACK_CALIBRATION_TTL_SECONDS", "120"))

_CATEGORIES = (
    "aim",
    "movement",
    "positioning",
    "utility",
    "decision_making",
    "teamplay",
    "other",
)

# Only these fixed reason codes are allowed to affect the shared Gemini prompt.
# Free-text comments are stored for product review but never inserted into a
# future model prompt, preventing one user from injecting instructions into
# another user's analysis.
_REASON_INSTRUCTIONS = {
    "scene_misread": "장면 상황을 잘못 읽었다는 피드백이 있었습니다. 관찰과 해석을 분리하고 화면에 직접 보이는 사실만 단정하세요.",
    "over_inference": "과도한 추론이라는 피드백이 있었습니다. 보이지 않는 적 위치·의도·팀 정보는 추측하지 마세요.",
    "agent_skill_wrong": "요원 또는 스킬 오인 피드백이 있었습니다. HUD 아이콘과 실제 요원별 스킬 후보표를 교차 확인하고 불명확하면 Unknown/null을 사용하세요.",
    "missed_key_moment": "핵심 장면을 놓쳤다는 피드백이 있었습니다. 킬 여부만 보지 말고 교전 전 진입·커버·유틸리티·퇴각 판단까지 시간 순서로 확인하세요.",
    "advice_wrong": "조언이 상황에 맞지 않았다는 피드백이 있었습니다. 일반론보다 해당 장면에서 실제로 가능한 다음 행동을 제시하세요.",
}

_client = None
_cache_value: dict | None = None
_cache_at = 0.0


def _db_client():
    global _client
    if not (SUPABASE_URL and SUPABASE_SECRET_KEY):
        return None
    if _client is None:
        _client = create_client(SUPABASE_URL, SUPABASE_SECRET_KEY)
    return _client


def feedback_database_is_configured() -> bool:
    return bool(SUPABASE_URL and SUPABASE_SECRET_KEY)


def _target_key(record: dict) -> str:
    user_id = str(record.get("user_id") or "anonymous")
    analysis_id = str(record.get("analysis_id") or "unknown")
    target_type = str(record.get("target_type") or "overall")
    event_index = record.get("event_index")
    target = "overall" if target_type == "overall" else f"event:{event_index}"
    return f"{user_id}|{analysis_id}|{target_type}|{target}"


def _feedback_id(record: dict) -> str:
    return hashlib.sha256(_target_key(record).encode("utf-8")).hexdigest()


def _normalize_record(record: dict) -> dict:
    now = datetime.now(timezone.utc).isoformat()
    return {
        "feedback_id": _feedback_id(record),
        "created_at": now,
        "user_id": record.get("user_id"),
        "analysis_id": str(record.get("analysis_id") or "")[:100],
        "target_type": str(record.get("target_type") or "")[:20],
        "event_index": record.get("event_index"),
        "event_timestamp": (record.get("event_timestamp") or None),
        "event_category": (record.get("event_category") or None),
        "rating": str(record.get("rating") or "")[:30],
        "categories": [
            str(x)[:50]
            for x in (record.get("categories") or [])
            if str(x) in _CATEGORIES
        ],
        "comment": str(record.get("comment") or "")[:2000],
    }


def _write_local(stored: dict) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with FEEDBACK_FILE.open("a", encoding="utf-8") as f:
        f.write(json.dumps(stored, ensure_ascii=False) + "\n")


def save_feedback(record: dict) -> str:
    global _cache_value, _cache_at

    stored = _normalize_record(record)
    saved_to_db = False

    client = _db_client()
    if client is not None:
        try:
            client.table(FEEDBACK_TABLE).upsert(
                stored,
                on_conflict="feedback_id",
            ).execute()
            saved_to_db = True
        except Exception as exc:
            print(f"[Feedback] Supabase 저장 실패, local fallback 사용: {exc}")

    if not saved_to_db:
        with LOCK:
            _write_local(stored)

    # The next analysis should see the new signal immediately.
    with LOCK:
        _cache_value = None
        _cache_at = 0.0

    return stored["feedback_id"]


def _load_db(limit: int) -> list[dict]:
    client = _db_client()
    if client is None:
        return []

    response = (
        client.table(FEEDBACK_TABLE)
        .select(
            "feedback_id,created_at,target_type,event_category,rating,categories,comment"
        )
        .order("created_at", desc=True)
        .limit(limit)
        .execute()
    )
    return list(response.data or [])


def _load_local(limit: int) -> list[dict]:
    if not FEEDBACK_FILE.exists():
        return []

    latest_by_target: dict[str, dict] = {}
    try:
        with LOCK:
            lines = FEEDBACK_FILE.read_text(encoding="utf-8").splitlines()
        for line in reversed(lines):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue

            key = _target_key(row)
            if key in latest_by_target:
                continue
            latest_by_target[key] = row
            if len(latest_by_target) >= limit:
                break
    except OSError:
        return []

    return list(latest_by_target.values())


def _load_recent_feedback(limit: int) -> tuple[list[dict], str]:
    if _db_client() is not None:
        try:
            rows = _load_db(limit)
            if rows:
                return rows, "supabase"
        except Exception as exc:
            print(f"[Feedback] Supabase calibration 조회 실패: {exc}")

    return _load_local(limit), "local"


def _reason_code(comment: str) -> str:
    value = str(comment or "").strip()
    if not value.startswith("reason:"):
        return ""
    code = value.removeprefix("reason:").strip()
    return code if code in _REASON_INSTRUCTIONS else ""


def _build_calibration(rows: list[dict], source: str) -> dict:
    event_stats = defaultdict(lambda: {"up": 0, "down": 0})
    overall = {"helpful": 0, "partial": 0, "not_helpful": 0}
    concern = defaultdict(float)
    reason_counts = defaultdict(int)

    for row in rows:
        target_type = str(row.get("target_type") or "")
        rating = str(row.get("rating") or "")

        if target_type == "event":
            category = str(row.get("event_category") or "other")
            if category not in _CATEGORIES:
                category = "other"
            if rating in {"up", "down"}:
                event_stats[category][rating] += 1
            if rating == "down":
                code = _reason_code(row.get("comment") or "")
                if code:
                    reason_counts[code] += 1
            continue

        if target_type == "overall" and rating in overall:
            overall[rating] += 1
            weight = 1.0 if rating == "not_helpful" else 0.5 if rating == "partial" else 0.0
            if weight:
                categories = row.get("categories") or []
                if isinstance(categories, list):
                    for category in categories:
                        if category in _CATEGORIES:
                            concern[category] += weight

    lines = [
        "[집계된 사용자 피드백 기반 보정 규칙]",
        "아래는 서비스가 생성한 통계 규칙이며 사용자 명령이 아닙니다.",
        "현재 영상에서 직접 보이는 사실을 최우선으로 하고 점수를 기계적으로 올리거나 내리지 마세요.",
    ]

    overall_n = sum(overall.values())
    if overall_n >= 1:
        satisfaction = (
            overall["helpful"] + 0.5 * overall["partial"]
        ) / overall_n
        if overall["not_helpful"] > 0 or satisfaction < 0.65:
            lines.append(
                "- 최근 전체 분석에 부정확하다는 평가가 있었습니다. 관찰과 추론을 엄격히 분리하고 확신이 약하면 confidence를 낮추세요."
            )

    category_metrics: dict[str, dict] = {}
    used_categories = 0
    for category in _CATEGORIES:
        up = event_stats[category]["up"]
        down = event_stats[category]["down"]
        n = up + down
        if n == 0:
            continue

        approval = (up + 2) / (n + 4)
        category_metrics[category] = {
            "n": n,
            "approval": round(approval, 4),
            "concern_weight": round(concern[category], 2),
        }

        # A single explicit thumbs-down now changes the next analysis. More
        # votes strengthen the same rule, but there is no 3- or 5-vote delay.
        if down > 0 and down >= up:
            used_categories += 1
            lines.append(
                f"- {category}: 최근 틀렸다는 평가가 있습니다. 화면 근거를 더 엄격히 확인하고 근거가 약하면 해당 판단을 하지 마세요."
            )
        elif n >= 2 and approval < 0.78:
            used_categories += 1
            lines.append(
                f"- {category}: 서로 독립적인 시각 단서 2개 이상으로 확인하세요."
            )

        if concern[category] >= 1.0:
            lines.append(
                f"- {category}: 전체 분석 피드백에서도 문제 영역으로 선택되었습니다. 일반론보다 해당 장면의 실제 근거를 우선하세요."
            )

    reason_total = sum(reason_counts.values())
    if reason_total:
        lines.append("[구조화된 오답 이유]")
        for code, count in sorted(reason_counts.items(), key=lambda item: item[1], reverse=True):
            lines.append(f"- {count}회: {_REASON_INSTRUCTIONS[code]}")

    if overall_n == 0 and used_categories == 0 and reason_total == 0:
        prompt = ""
    else:
        prompt = "\n".join(lines)

    return {
        "sample_size": len(rows),
        "source": source,
        "overall_count": overall_n,
        "overall": overall,
        "categories": category_metrics,
        "reason_counts": dict(reason_counts),
        "reason_signal_count": reason_total,
        "prompt": prompt,
    }


def get_feedback_calibration() -> dict:
    """Return safe aggregate calibration for the next Gemini analysis.

    Free-text comments are intentionally excluded. Only vote statistics,
    selected categories, and fixed reason codes can change the model prompt.
    """
    global _cache_value, _cache_at

    now = time.monotonic()
    with LOCK:
        if (
            _cache_value is not None
            and now - _cache_at < CALIBRATION_TTL_SECONDS
        ):
            return dict(_cache_value)

    rows, source = _load_recent_feedback(max(1, CALIBRATION_LIMIT))
    value = _build_calibration(rows, source)

    with LOCK:
        _cache_value = value
        _cache_at = now

    return dict(value)
