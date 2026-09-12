from __future__ import annotations

import os
from datetime import datetime, timezone

from supabase import Client, create_client

SUPABASE_URL = os.getenv("SUPABASE_URL", "").strip()
SUPABASE_SECRET_KEY = (
    os.getenv("SUPABASE_SECRET_KEY", "").strip()
    or os.getenv("SUPABASE_SERVICE_ROLE_KEY", "").strip()
)

_client: Client | None = None


def membership_is_configured() -> bool:
    return bool(SUPABASE_URL and SUPABASE_SECRET_KEY)


def _get_client() -> Client:
    global _client
    if not membership_is_configured():
        raise RuntimeError("Supabase premium membership store is not configured.")
    if _client is None:
        _client = create_client(SUPABASE_URL, SUPABASE_SECRET_KEY)
    return _client


def is_premium(user_id: str) -> bool:
    response = (
        _get_client()
        .table("user_entitlements")
        .select("plan,premium_until")
        .eq("user_id", user_id)
        .limit(1)
        .execute()
    )
    rows = response.data or []
    if not rows:
        return False

    row = rows[0]
    if row.get("plan") != "premium":
        return False

    premium_until = row.get("premium_until")
    if not premium_until:
        return True

    try:
        expires_at = datetime.fromisoformat(str(premium_until).replace("Z", "+00:00"))
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        return expires_at > datetime.now(timezone.utc)
    except (TypeError, ValueError):
        return False
