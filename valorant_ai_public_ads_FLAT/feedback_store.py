from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from supabase import create_client

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
CALIBRATION_TTL_SECONDS = int(os.getenv("FEEDBACK_CALIBRATION_TTL_SECONDS", "300"))

_CATEGORIES = (
    "aim",
    "movement",
    "positioning",
    "utility",
    "decision_making",
    "teamplay",
    "other",
)

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


def _feedback_id(record: dict) -> str:
    """One latest vote per user/analysis/target.

    Clicking thumbs-up and then thumbs-down should replace the previous vote
    instead of counting as two independent training signals.
    """
    user_id = str(record.get("user_id") or "anonymous")
    analysis_id = str(record.get("analysis_id") or "unknown")
    target_type = str(record.get("target_type") or "overall")
    event_index = record.get("event_index")
    target = "overall" if target_type == "overall" else f"event:{event_index}"
    raw = f"{user_id}|{analysis_id}|{target_type}|{target}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


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
            # Keep feedback working even before the optional Supabase table is
            # created. Render's local fallback is temporary, but is better than
            # losing the user's vote entirely.
            print(f"[Feedback] Supabase 저장 실패, local fallback 사용: {exc}")

    if not saved_to_db:
        with LOCK:
            _write_local(stored)

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
            "feedback_id,created_at,target_type,event_category,rating,categories"
        )
        .order("created_at", desc=True)
        .limit(limit)
        .execute()
    )
    return list(response.data or [])


def _load_local(limit: int) -> list[dict]:
    if not FEEDBACK_FILE.exists():
        return []

    latest_by_id: dict[str, dict] = {}
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
            feedback_id = str(row.get("feedback_id") or "")
            if not feedback_id or feedback_id in latest_by_id:
                continue
            latest_by_id[feedback_id] = row
            if len(latest_by_id) >= limit:
                break
    except OSError:
        return []

    return list(latest_by_id.values())


def _load_recent_feedback(limit: int) -> tuple[list[dict], str]:
    if _db_client() is not None:
        try:
            rows = _load_db(limit)
            return rows, "supabase"
        except Exception as exc:
            print(f"[Feedback] Supabase calibration 조회 실패: {exc}")

    return _load_local(limit), "local"


def _build_calibration(rows: list[dict], source: str) -> dict:
    event_stats = defaultdict(lambda: {"up": 0, "down": 0})
    overall = {"helpful": 0, "partial": 0, "not_helpful": 0}
    concern = defaultdict(float)

    for row in rows:
        target_type = str(row.get("target_type") or "")
        rating = str(row.get("rating") or "")

        if target_type == "event":
            category = str(row.get("event_category") or "other")
            if category not in _CATEGORIES:
                category = "other"
            if rating in {"up", "down"}:
                event_stats[category][rating] += 1
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
        "[집계된 사용자 피드백 기반 보정 정보]",
        "아래 내용은 코드가 만든 통계이며 사용자 명령이 아닙니다.",
        "영상에서 실제로 보이는 사실보다 우선하지 말고, 점수를 기계적으로 올리거나 내리지 마세요.",
        "정확도가 낮았던 영역에서는 더 강한 시각적 근거를 요구하고 불확실하면 confidence를 낮추세요.",
    ]

    overall_n = sum(overall.values())
    if overall_n >= 5:
        satisfaction = (
            overall["helpful"] + 0.5 * overall["partial"]
        ) / overall_n
        lines.append(
            f"- 최근 전체 평가 {overall_n}건의 보정 만족도: {satisfaction * 100:.0f}%"
        )
        if satisfaction < 0.65:
            lines.append(
                "- 전체 평가 정확도가 낮은 편입니다. 관찰과 추론을 엄격히 분리하고, 불확실한 단정은 피하세요."
            )

    used_categories = 0
    category_metrics: dict[str, dict] = {}
    for category in _CATEGORIES:
        up = event_stats[category]["up"]
        down = event_stats[category]["down"]
        n = up + down
        if n == 0:
            continue

        # Beta(2,2) smoothing prevents a handful of votes from creating an
        # extreme calibration signal.
        approval = (up + 2) / (n + 4)
        category_metrics[category] = {
            "n": n,
            "approval": round(approval, 4),
            "concern_weight": round(concern[category], 2),
        }

        if n < 3:
            continue

        used_categories += 1
        lines.append(
            f"- {category}: 장면 피드백 {n}건, 보정 승인율 {approval * 100:.0f}%"
        )
        if approval < 0.62:
            lines.append(
                f"  · {category} 평가는 특히 보수적으로 하세요. 직접 관찰 가능한 근거가 명확하지 않으면 해당 주장과 confidence를 낮추세요."
            )
        elif approval < 0.78:
            lines.append(
                f"  · {category} 평가는 가능하면 서로 독립적인 시각 단서 2개 이상으로 확인하세요."
            )

        if concern[category] >= 2.0:
            lines.append(
                f"  · 전체 피드백에서도 {category}가 반복적으로 문제 영역으로 선택되었습니다. 과도한 추론을 피하세요."
            )

    if overall_n < 5 and used_categories == 0:
        prompt = ""
    else:
        prompt = "\n".join(lines)

    return {
        "sample_size": len(rows),
        "source": source,
        "overall_count": overall_n,
        "overall": overall,
        "categories": category_metrics,
        "prompt": prompt,
    }


def get_feedback_calibration() -> dict:
    """Return a cached, aggregate-only calibration profile.

    Raw comments are deliberately excluded from the model prompt. This avoids
    prompt injection through feedback while still letting accepted/rejected
    coaching signals make future analyses more conservative where needed.
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
