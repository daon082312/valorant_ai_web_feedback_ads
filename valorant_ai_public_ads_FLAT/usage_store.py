from __future__ import annotations

import hashlib
import hmac
import os
from datetime import datetime
from zoneinfo import ZoneInfo

from supabase import Client, create_client


LOCAL_TZ = ZoneInfo("Asia/Seoul")

SUPABASE_URL = os.getenv("SUPABASE_URL", "").strip()

SUPABASE_SECRET_KEY = (
    os.getenv("SUPABASE_SECRET_KEY", "").strip()
    or os.getenv("SUPABASE_SERVICE_ROLE_KEY", "").strip()
)

USAGE_HASH_SECRET = (
    os.getenv("USAGE_HASH_SECRET", "").strip()
    or os.getenv("GEMINI_API_KEY", "").strip()
)

_client: Client | None = None


class DailyLimitExceeded(Exception):
    pass


def usage_is_configured() -> bool:
    return bool(
        SUPABASE_URL
        and SUPABASE_SECRET_KEY
        and USAGE_HASH_SECRET
    )


def _get_client() -> Client:
    global _client

    if not usage_is_configured():
        raise RuntimeError(
            "Supabase usage database is not configured. "
            "Set SUPABASE_URL, SUPABASE_SECRET_KEY, "
            "and USAGE_HASH_SECRET."
        )

    if _client is None:
        _client = create_client(
            SUPABASE_URL,
            SUPABASE_SECRET_KEY,
        )

    return _client


def _today() -> str:
    return datetime.now(LOCAL_TZ).date().isoformat()


def _user_key(ip: str) -> str:
    normalized_ip = (ip or "unknown").strip()

    return hmac.new(
        USAGE_HASH_SECRET.encode("utf-8"),
        normalized_ip.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def get_used(ip: str) -> int:
    client = _get_client()

    response = (
        client.table("daily_usage")
        .select("successful_analyses")
        .eq("user_key", _user_key(ip))
        .eq("usage_date", _today())
        .limit(1)
        .execute()
    )

    rows = response.data or []

    if not rows:
        return 0

    return int(
        rows[0].get(
            "successful_analyses",
            0,
        )
    )


def get_remaining(
    ip: str,
    daily_limit: int,
) -> int:
    return max(
        int(daily_limit) - get_used(ip),
        0,
    )


def record_success(
    ip: str,
    daily_limit: int,
) -> int:
    client = _get_client()

    response = client.rpc(
        "consume_daily_usage",
        {
            "p_user_key": _user_key(ip),
            "p_usage_date": _today(),
            "p_daily_limit": int(daily_limit),
        },
    ).execute()

    value = response.data

    if value is None:
        raise RuntimeError(
            "Supabase RPC returned no usage count."
        )

    used = int(value)

    if used < 0:
        raise DailyLimitExceeded()

    return used
