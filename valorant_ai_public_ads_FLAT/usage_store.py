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
    return bool(SUPABASE_URL and SUPABASE_SECRET_KEY and USAGE_HASH_SECRET)


def _get_client() -> Client:
    global _client
    if not usage_is_configured():
        raise RuntimeError(
            "Supabase usage database is not configured. "
            "Set SUPABASE_URL, SUPABASE_SECRET_KEY, and USAGE_HASH_SECRET."
        )
    if _client is None:
        _client = create_client(SUPABASE_URL, SUPABASE_SECRET_KEY)
    return _client


def _today() -> str:
    return datetime.now(LOCAL_TZ).date().isoformat()


def _hash_subject(kind: str, value: str) -> str:
    normalized = (value or "unknown").strip()
    return hmac.new(
        USAGE_HASH_SECRET.encode("utf-8"),
        f"{kind}:{normalized}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def _account_key(user_id: str) -> str:
    return _hash_subject("account", user_id)


def _ip_key(ip: str) -> str:
    return _hash_subject("ip", ip)


def _count_for(subject_type: str, subject_key: str) -> int:
    response = (
        _get_client().table("daily_usage_v2")
        .select("successful_analyses")
        .eq("subject_type", subject_type)
        .eq("subject_key", subject_key)
        .eq("usage_date", _today())
        .limit(1)
        .execute()
    )
    rows = response.data or []
    if not rows:
        return 0
    return int(rows[0].get("successful_analyses", 0))


def get_usage(user_id: str, ip: str, account_limit: int, ip_limit: int) -> dict:
    account_used = _count_for("account", _account_key(user_id))
    ip_used = _count_for("ip", _ip_key(ip))

    account_remaining = max(int(account_limit) - account_used, 0)
    ip_remaining = max(int(ip_limit) - ip_used, 0)

    return {
        "account_used": account_used,
        "account_remaining": account_remaining,
        "ip_used": ip_used,
        "ip_remaining": ip_remaining,
        "remaining": min(account_remaining, ip_remaining),
    }


def record_success(user_id: str, ip: str, account_limit: int, ip_limit: int) -> dict:
    response = _get_client().rpc(
        "consume_dual_daily_usage",
        {
            "p_account_key": _account_key(user_id),
            "p_ip_key": _ip_key(ip),
            "p_usage_date": _today(),
            "p_account_limit": int(account_limit),
            "p_ip_limit": int(ip_limit),
        },
    ).execute()

    data = response.data
    if isinstance(data, list) and data:
        data = data[0]
    if not isinstance(data, dict):
        raise RuntimeError("Supabase RPC returned an invalid usage result.")
    if not bool(data.get("allowed")):
        raise DailyLimitExceeded(str(data.get("reason") or "daily limit exceeded"))

    account_used = int(data.get("account_used", 0))
    ip_used = int(data.get("ip_used", 0))
    account_remaining = max(int(account_limit) - account_used, 0)
    ip_remaining = max(int(ip_limit) - ip_used, 0)

    return {
        "account_used": account_used,
        "account_remaining": account_remaining,
        "ip_used": ip_used,
        "ip_remaining": ip_remaining,
        "remaining": min(account_remaining, ip_remaining),
    }
