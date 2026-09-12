from __future__ import annotations

import os
from typing import Any, Callable

from dotenv import load_dotenv
from supabase import create_client

load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL", "").strip()
SUPABASE_PUBLISHABLE_KEY = (
    os.getenv("SUPABASE_PUBLISHABLE_KEY", "").strip()
    or os.getenv("SUPABASE_ANON_KEY", "").strip()
)
SUPABASE_SECRET_KEY = (
    os.getenv("SUPABASE_SECRET_KEY", "").strip()
    or os.getenv("SUPABASE_SERVICE_ROLE_KEY", "").strip()
)

TABLE_NAME = "analysis_history"
_service = None


def history_is_configured() -> bool:
    return bool(SUPABASE_URL and (SUPABASE_PUBLISHABLE_KEY or SUPABASE_SECRET_KEY))


def _user_client(access_token: str, refresh_token: str = ""):
    if not SUPABASE_URL or not SUPABASE_PUBLISHABLE_KEY:
        raise RuntimeError("ANALYSIS_HISTORY_PUBLISHABLE_NOT_CONFIGURED")
    if not access_token:
        raise RuntimeError("ANALYSIS_HISTORY_AUTH_REQUIRED")

    client = create_client(SUPABASE_URL, SUPABASE_PUBLISHABLE_KEY)
    if refresh_token:
        client.auth.set_session(access_token, refresh_token)
    else:
        client.postgrest.auth(access_token)
    return client


def _service_client():
    global _service
    if not SUPABASE_URL or not SUPABASE_SECRET_KEY:
        return None
    if _service is None:
        _service = create_client(SUPABASE_URL, SUPABASE_SECRET_KEY)
    return _service


def _with_fallback(
    user_operation: Callable[[Any], Any],
    service_operation: Callable[[Any], Any],
    access_token: str,
    refresh_token: str,
):
    first_error: Exception | None = None

    if SUPABASE_PUBLISHABLE_KEY:
        try:
            return user_operation(_user_client(access_token, refresh_token))
        except Exception as exc:
            first_error = exc
            print(f"[History] user-session path failed, trying server fallback: {type(exc).__name__}: {exc}")

    service = _service_client()
    if service is not None:
        return service_operation(service)

    if first_error is not None:
        raise first_error
    raise RuntimeError("ANALYSIS_HISTORY_NOT_CONFIGURED")


def save_analysis(
    user_id: str,
    file_name: str,
    result: dict[str, Any],
    access_token: str,
    refresh_token: str = "",
) -> dict:
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

    def do_save(client):
        response = client.table(TABLE_NAME).upsert(
            row,
            on_conflict="user_id,analysis_id",
        ).execute()
        data = list(response.data or [])
        return data[0] if data else row

    return _with_fallback(do_save, do_save, access_token, refresh_token)


def list_analyses(
    user_id: str,
    access_token: str,
    refresh_token: str = "",
    limit: int = 50,
) -> list[dict]:
    safe_limit = max(1, min(100, int(limit)))

    def do_list(client):
        response = (
            client.table(TABLE_NAME)
            .select(
                "analysis_id,created_at,file_name,overall_score,tier,summary,model_used"
            )
            .eq("user_id", user_id)
            .order("created_at", desc=True)
            .limit(safe_limit)
            .execute()
        )
        return list(response.data or [])

    return _with_fallback(do_list, do_list, access_token, refresh_token)


def get_analysis(
    user_id: str,
    analysis_id: str,
    access_token: str,
    refresh_token: str = "",
) -> dict | None:
    safe_id = str(analysis_id or "")[:100]

    def do_get(client):
        response = (
            client.table(TABLE_NAME)
            .select("analysis_id,created_at,file_name,result")
            .eq("user_id", user_id)
            .eq("analysis_id", safe_id)
            .limit(1)
            .execute()
        )
        data = list(response.data or [])
        return data[0] if data else None

    return _with_fallback(do_get, do_get, access_token, refresh_token)


def delete_analysis(
    user_id: str,
    analysis_id: str,
    access_token: str,
    refresh_token: str = "",
) -> bool:
    safe_id = str(analysis_id or "")[:100]

    def do_delete(client):
        response = (
            client.table(TABLE_NAME)
            .delete()
            .eq("user_id", user_id)
            .eq("analysis_id", safe_id)
            .execute()
        )
        return bool(response.data)

    return bool(_with_fallback(do_delete, do_delete, access_token, refresh_token))
