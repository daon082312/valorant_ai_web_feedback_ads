from __future__ import annotations

import os
from typing import Any

from dotenv import load_dotenv
from supabase import create_client

load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL", "").strip()
SUPABASE_PUBLISHABLE_KEY = (
    os.getenv("SUPABASE_PUBLISHABLE_KEY", "").strip()
    or os.getenv("SUPABASE_ANON_KEY", "").strip()
)

TABLE_NAME = "analysis_history"


def history_is_configured() -> bool:
    return bool(SUPABASE_URL and SUPABASE_PUBLISHABLE_KEY)


def _user_client(access_token: str, refresh_token: str = ""):
    if not history_is_configured():
        raise RuntimeError("ANALYSIS_HISTORY_NOT_CONFIGURED")
    if not access_token:
        raise RuntimeError("ANALYSIS_HISTORY_AUTH_REQUIRED")

    client = create_client(SUPABASE_URL, SUPABASE_PUBLISHABLE_KEY)

    # The normal auth flow already validated this session. Reuse it here so
    # Postgres RLS can enforce auth.uid() = user_id for every history action.
    if refresh_token:
        client.auth.set_session(access_token, refresh_token)
    else:
        client.postgrest.auth(access_token)
    return client


def save_analysis(
    user_id: str,
    file_name: str,
    result: dict[str, Any],
    access_token: str,
    refresh_token: str = "",
) -> dict:
    client = _user_client(access_token, refresh_token)

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
        on_conflict="user_id,analysis_id",
    ).execute()

    data = list(response.data or [])
    return data[0] if data else row


def list_analyses(
    user_id: str,
    access_token: str,
    refresh_token: str = "",
    limit: int = 50,
) -> list[dict]:
    client = _user_client(access_token, refresh_token)

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


def get_analysis(
    user_id: str,
    analysis_id: str,
    access_token: str,
    refresh_token: str = "",
) -> dict | None:
    client = _user_client(access_token, refresh_token)

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


def delete_analysis(
    user_id: str,
    analysis_id: str,
    access_token: str,
    refresh_token: str = "",
) -> bool:
    client = _user_client(access_token, refresh_token)

    response = (
        client.table(TABLE_NAME)
        .delete()
        .eq("user_id", user_id)
        .eq("analysis_id", analysis_id)
        .execute()
    )
    return bool(response.data)
