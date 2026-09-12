from __future__ import annotations

import os
from typing import Any

from dotenv import load_dotenv
from supabase import create_client

load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL", "").strip()
SUPABASE_SECRET_KEY = (
    os.getenv("SUPABASE_SECRET_KEY", "").strip()
    or os.getenv("SUPABASE_SERVICE_ROLE_KEY", "").strip()
)

TABLE_NAME = "analysis_history"
_client = None


def _db_client():
    global _client
    if not (SUPABASE_URL and SUPABASE_SECRET_KEY):
        return None
    if _client is None:
        _client = create_client(SUPABASE_URL, SUPABASE_SECRET_KEY)
    return _client


def history_is_configured() -> bool:
    return bool(SUPABASE_URL and SUPABASE_SECRET_KEY)


def save_analysis(user_id: str, file_name: str, result: dict[str, Any]) -> dict:
    client = _db_client()
    if client is None:
        raise RuntimeError("ANALYSIS_HISTORY_NOT_CONFIGURED")

    analysis_id = str(result.get("analysis_id") or "").strip()
    if not analysis_id:
        raise ValueError("analysis_id가 없습니다.")

    tier_prediction = result.get("tier_prediction") or {}
    if not isinstance(tier_prediction, dict):
        tier_prediction = {}

    row = {
        "analysis_id": analysis_id[:100],
        "user_id": user_id,
        "file_name": (file_name or "영상")[:255],
        "overall_score": int(result.get("overall_score") or 0),
        "tier": str(tier_prediction.get("tier") or "")[:40],
        "summary": str(result.get("summary") or "")[:4000],
        "model_used": str(result.get("model_used") or "")[:100],
        "result": result,
    }

    response = client.table(TABLE_NAME).upsert(
        row,
        on_conflict="analysis_id",
    ).execute()

    data = list(response.data or [])
    return data[0] if data else row


def list_analyses(user_id: str, limit: int = 50) -> list[dict]:
    client = _db_client()
    if client is None:
        raise RuntimeError("ANALYSIS_HISTORY_NOT_CONFIGURED")

    response = (
        client.table(TABLE_NAME)
        .select(
            "analysis_id,created_at,file_name,overall_score,tier,summary,model_used"
        )
        .eq("user_id", user_id)
        .order("created_at", desc=True)
        .limit(max(1, min(100, int(limit))))
        .execute()
    )
    return list(response.data or [])


def get_analysis(user_id: str, analysis_id: str) -> dict | None:
    client = _db_client()
    if client is None:
        raise RuntimeError("ANALYSIS_HISTORY_NOT_CONFIGURED")

    response = (
        client.table(TABLE_NAME)
        .select("analysis_id,created_at,file_name,result")
        .eq("user_id", user_id)
        .eq("analysis_id", analysis_id)
        .limit(1)
        .execute()
    )
    data = list(response.data or [])
    return data[0] if data else None


def delete_analysis(user_id: str, analysis_id: str) -> bool:
    client = _db_client()
    if client is None:
        raise RuntimeError("ANALYSIS_HISTORY_NOT_CONFIGURED")

    response = (
        client.table(TABLE_NAME)
        .delete()
        .eq("user_id", user_id)
        .eq("analysis_id", analysis_id)
        .execute()
    )
    return bool(response.data)
