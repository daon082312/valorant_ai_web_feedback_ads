from __future__ import annotations

import base64
import calendar
import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
from supabase import Client, create_client

SUPABASE_URL = os.getenv("SUPABASE_URL", "").strip()
SUPABASE_SECRET_KEY = (
    os.getenv("SUPABASE_SECRET_KEY", "").strip()
    or os.getenv("SUPABASE_SERVICE_ROLE_KEY", "").strip()
)
TOSS_CLIENT_KEY = os.getenv("TOSS_CLIENT_KEY", "").strip()
TOSS_SECRET_KEY = os.getenv("TOSS_SECRET_KEY", "").strip()
BILLING_CRON_TOKEN = os.getenv("BILLING_CRON_TOKEN", "").strip()
PREMIUM_MONTHLY_PRICE = int(os.getenv("PREMIUM_MONTHLY_PRICE", "990"))
PREMIUM_TERMS_VERSION = os.getenv("PREMIUM_TERMS_VERSION", "2026-09-12").strip()

TOSS_API_BASE = "https://api.tosspayments.com"
ORDER_NAME = "VALORANT AI Coach Premium 1개월"
MAX_RENEWAL_FAILURES = 3

_client: Client | None = None


def billing_is_configured() -> bool:
    return bool(
        SUPABASE_URL
        and SUPABASE_SECRET_KEY
        and TOSS_CLIENT_KEY
        and TOSS_SECRET_KEY
    )


def billing_scheduler_is_configured() -> bool:
    return bool(BILLING_CRON_TOKEN)


def _db() -> Client:
    global _client
    if not SUPABASE_URL or not SUPABASE_SECRET_KEY:
        raise RuntimeError("Supabase billing database is not configured.")
    if _client is None:
        _client = create_client(SUPABASE_URL, SUPABASE_SECRET_KEY)
    return _client


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _parse_dt(value: Any) -> datetime | None:
    if not value:
        return None
    dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _add_one_month(value: datetime) -> datetime:
    month = value.month + 1
    year = value.year
    if month == 13:
        month = 1
        year += 1
    day = min(value.day, calendar.monthrange(year, month)[1])
    return value.replace(year=year, month=month, day=day)


def _basic_auth_header() -> str:
    raw = f"{TOSS_SECRET_KEY}:".encode("utf-8")
    return "Basic " + base64.b64encode(raw).decode("ascii")


def _toss_headers() -> dict[str, str]:
    return {
        "Authorization": _basic_auth_header(),
        "Content-Type": "application/json",
    }


def _toss_error(response: httpx.Response) -> RuntimeError:
    try:
        data = response.json()
    except Exception:
        data = {}
    code = str(data.get("code") or f"HTTP_{response.status_code}")
    message = str(data.get("message") or "Toss Payments request failed.")
    return RuntimeError(f"{code}: {message}")


def issue_billing_key(auth_key: str, customer_key: str) -> dict:
    if not billing_is_configured():
        raise RuntimeError("Toss billing is not configured.")
    with httpx.Client(timeout=60.0) as client:
        response = client.post(
            f"{TOSS_API_BASE}/v1/billing/authorizations/issue",
            headers=_toss_headers(),
            json={"authKey": auth_key, "customerKey": customer_key},
        )
    if response.status_code != 200:
        raise _toss_error(response)
    return response.json()


def charge_billing_key(
    billing_key: str,
    customer_key: str,
    email: str,
    *,
    amount: int = PREMIUM_MONTHLY_PRICE,
    order_id: str | None = None,
) -> dict:
    if not billing_is_configured():
        raise RuntimeError("Toss billing is not configured.")
    order_id = order_id or f"prem_{uuid.uuid4().hex}"
    payload = {
        "amount": int(amount),
        "customerKey": customer_key,
        "orderId": order_id,
        "orderName": ORDER_NAME,
        "customerEmail": email,
    }
    with httpx.Client(timeout=70.0) as client:
        response = client.post(
            f"{TOSS_API_BASE}/v1/billing/{billing_key}",
            headers=_toss_headers(),
            json=payload,
        )
    if response.status_code != 200:
        raise _toss_error(response)
    data = response.json()
    if data.get("status") != "DONE":
        raise RuntimeError(f"UNEXPECTED_PAYMENT_STATUS: {data.get('status')}")
    return data


def delete_billing_key(billing_key: str) -> None:
    if not TOSS_SECRET_KEY or not billing_key:
        return
    with httpx.Client(timeout=60.0) as client:
        response = client.delete(
            f"{TOSS_API_BASE}/v1/billing/{billing_key}",
            headers=_toss_headers(),
        )
    if response.status_code != 200:
        raise _toss_error(response)


def get_subscription(user_id: str) -> dict | None:
    response = (
        _db().table("premium_subscriptions")
        .select("*")
        .eq("user_id", user_id)
        .limit(1)
        .execute()
    )
    rows = response.data or []
    return rows[0] if rows else None


def ensure_subscription(user_id: str, email: str) -> dict:
    existing = get_subscription(user_id)
    if existing:
        if email and existing.get("email") != email:
            _db().table("premium_subscriptions").update({
                "email": email,
                "updated_at": _iso(_utcnow()),
            }).eq("user_id", user_id).execute()
            existing["email"] = email
        return existing

    customer_key = f"cus_{uuid.uuid4().hex}"
    row = {
        "user_id": user_id,
        "email": email,
        "customer_key": customer_key,
        "status": "inactive",
        "amount": PREMIUM_MONTHLY_PRICE,
        "cancel_at_period_end": False,
        "failure_count": 0,
        "updated_at": _iso(_utcnow()),
    }
    response = _db().table("premium_subscriptions").insert(row).execute()
    rows = response.data or []
    return rows[0] if rows else row


def record_billing_consent(user_id: str, email: str) -> dict:
    subscription = ensure_subscription(user_id, email)
    now = _utcnow()
    update = {
        "consent_at": _iso(now),
        "terms_version": PREMIUM_TERMS_VERSION,
        "amount": PREMIUM_MONTHLY_PRICE,
        "cancel_at_period_end": False,
        "updated_at": _iso(now),
    }
    _db().table("premium_subscriptions").update(update).eq("user_id", user_id).execute()
    subscription.update(update)
    return subscription


def save_billing_key(user_id: str, billing_key: str) -> None:
    _db().table("premium_subscriptions").update({
        "billing_key": billing_key,
        "status": "incomplete",
        "last_error": None,
        "failure_count": 0,
        "updated_at": _iso(_utcnow()),
    }).eq("user_id", user_id).execute()


def _set_entitlement(user_id: str, premium_until: datetime | None, premium: bool) -> None:
    row = {
        "user_id": user_id,
        "plan": "premium" if premium else "free",
        "premium_until": _iso(premium_until) if premium else None,
        "updated_at": _iso(_utcnow()),
    }
    _db().table("user_entitlements").upsert(row, on_conflict="user_id").execute()


def record_payment(
    user_id: str,
    order_id: str,
    amount: int,
    status: str,
    *,
    payment_key: str | None = None,
    failure_code: str | None = None,
    failure_message: str | None = None,
) -> None:
    row = {
        "order_id": order_id,
        "user_id": user_id,
        "amount": int(amount),
        "status": status,
        "payment_key": payment_key,
        "failure_code": failure_code,
        "failure_message": failure_message,
        "created_at": _iso(_utcnow()),
    }
    _db().table("premium_payments").upsert(row, on_conflict="order_id").execute()


def activate_after_payment(user_id: str, payment: dict) -> dict:
    now = _utcnow()
    period_end = _add_one_month(now)
    update = {
        "status": "active",
        "amount": PREMIUM_MONTHLY_PRICE,
        "current_period_end": _iso(period_end),
        "next_billing_at": _iso(period_end),
        "cancel_at_period_end": False,
        "failure_count": 0,
        "last_error": None,
        "last_payment_key": payment.get("paymentKey"),
        "last_order_id": payment.get("orderId"),
        "last_attempt_at": _iso(now),
        "updated_at": _iso(now),
    }
    _db().table("premium_subscriptions").update(update).eq("user_id", user_id).execute()
    _set_entitlement(user_id, period_end, True)
    record_payment(
        user_id,
        str(payment.get("orderId")),
        int(payment.get("totalAmount") or payment.get("balanceAmount") or PREMIUM_MONTHLY_PRICE),
        "paid",
        payment_key=str(payment.get("paymentKey") or ""),
    )
    return update


def mark_initial_payment_failed(user_id: str, order_id: str, exc: Exception) -> None:
    message = str(exc)[:1000]
    code = message.split(":", 1)[0][:100]
    now = _utcnow()
    _db().table("premium_subscriptions").update({
        "status": "incomplete",
        "last_error": message,
        "last_attempt_at": _iso(now),
        "updated_at": _iso(now),
    }).eq("user_id", user_id).execute()
    _set_entitlement(user_id, None, False)
    record_payment(
        user_id,
        order_id,
        PREMIUM_MONTHLY_PRICE,
        "failed",
        failure_code=code,
        failure_message=message,
    )


def cancel_subscription(user_id: str) -> dict | None:
    subscription = get_subscription(user_id)
    if not subscription:
        return None
    now = _utcnow()
    period_end = _parse_dt(subscription.get("current_period_end"))
    update = {
        "status": "canceled",
        "cancel_at_period_end": True,
        "next_billing_at": None,
        "updated_at": _iso(now),
    }
    _db().table("premium_subscriptions").update(update).eq("user_id", user_id).execute()
    if not period_end or period_end <= now:
        _set_entitlement(user_id, None, False)
    return {**subscription, **update}


def clear_billing_key(user_id: str) -> None:
    _db().table("premium_subscriptions").update({
        "billing_key": None,
        "updated_at": _iso(_utcnow()),
    }).eq("user_id", user_id).execute()


def list_due_subscriptions(limit: int = 100) -> list[dict]:
    now = _iso(_utcnow())
    response = (
        _db().table("premium_subscriptions")
        .select("*")
        .in_("status", ["active", "past_due"])
        .eq("cancel_at_period_end", False)
        .not_.is_("billing_key", "null")
        .lte("next_billing_at", now)
        .lt("failure_count", MAX_RENEWAL_FAILURES)
        .limit(limit)
        .execute()
    )
    return response.data or []


def _mark_renewal_failure(subscription: dict, exc: Exception) -> None:
    user_id = str(subscription["user_id"])
    failure_count = int(subscription.get("failure_count") or 0) + 1
    now = _utcnow()
    retry_at = now + timedelta(days=1) if failure_count < MAX_RENEWAL_FAILURES else None
    message = str(exc)[:1000]
    _db().table("premium_subscriptions").update({
        "status": "past_due",
        "failure_count": failure_count,
        "next_billing_at": _iso(retry_at),
        "last_error": message,
        "last_attempt_at": _iso(now),
        "updated_at": _iso(now),
    }).eq("user_id", user_id).execute()

    period_end = _parse_dt(subscription.get("current_period_end"))
    if not period_end or period_end <= now:
        _set_entitlement(user_id, None, False)


def _expire_canceled_or_past_due() -> None:
    now = _utcnow()
    response = (
        _db().table("premium_subscriptions")
        .select("user_id,status,current_period_end,failure_count")
        .in_("status", ["canceled", "past_due"])
        .not_.is_("current_period_end", "null")
        .lte("current_period_end", _iso(now))
        .limit(500)
        .execute()
    )
    for row in response.data or []:
        if row.get("status") == "canceled" or int(row.get("failure_count") or 0) >= MAX_RENEWAL_FAILURES:
            _set_entitlement(str(row["user_id"]), None, False)


def run_due_renewals() -> dict:
    due = list_due_subscriptions()
    paid = 0
    failed = 0
    for subscription in due:
        user_id = str(subscription["user_id"])
        order_id = f"prem_{uuid.uuid4().hex}"
        try:
            payment = charge_billing_key(
                str(subscription["billing_key"]),
                str(subscription["customer_key"]),
                str(subscription.get("email") or ""),
                amount=int(subscription.get("amount") or PREMIUM_MONTHLY_PRICE),
                order_id=order_id,
            )
            activate_after_payment(user_id, payment)
            paid += 1
        except Exception as exc:
            mark_message = str(exc)[:1000]
            code = mark_message.split(":", 1)[0][:100]
            record_payment(
                user_id,
                order_id,
                int(subscription.get("amount") or PREMIUM_MONTHLY_PRICE),
                "failed",
                failure_code=code,
                failure_message=mark_message,
            )
            _mark_renewal_failure(subscription, exc)
            failed += 1

    _expire_canceled_or_past_due()
    return {"checked": len(due), "paid": paid, "failed": failed}
