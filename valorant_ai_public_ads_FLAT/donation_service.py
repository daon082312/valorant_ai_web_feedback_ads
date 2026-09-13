from __future__ import annotations

import base64
import os
import uuid
from datetime import datetime, timezone

import httpx
from supabase import Client, create_client

SUPABASE_URL = os.getenv("SUPABASE_URL", "").strip()
SUPABASE_SECRET_KEY = (
    os.getenv("SUPABASE_SECRET_KEY", "").strip()
    or os.getenv("SUPABASE_SERVICE_ROLE_KEY", "").strip()
)
TOSS_CLIENT_KEY = os.getenv("TOSS_CLIENT_KEY", "").strip()
TOSS_SECRET_KEY = os.getenv("TOSS_SECRET_KEY", "").strip()
TOSS_API_BASE = "https://api.tosspayments.com"

DONATION_MIN_AMOUNT = int(os.getenv("DONATION_MIN_AMOUNT", "100"))
DONATION_ORDER_NAME = os.getenv("DONATION_ORDER_NAME", "VALORANT AI Coach 후원").strip() or "VALORANT AI Coach 후원"

_client: Client | None = None


def donation_is_configured() -> bool:
    return bool(
        SUPABASE_URL
        and SUPABASE_SECRET_KEY
        and TOSS_CLIENT_KEY
        and TOSS_SECRET_KEY
    )


def _db() -> Client:
    global _client
    if not SUPABASE_URL or not SUPABASE_SECRET_KEY:
        raise RuntimeError("Supabase donation database is not configured.")
    if _client is None:
        _client = create_client(SUPABASE_URL, SUPABASE_SECRET_KEY)
    return _client


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _basic_auth_header() -> str:
    raw = f"{TOSS_SECRET_KEY}:".encode("utf-8")
    return "Basic " + base64.b64encode(raw).decode("ascii")


def _toss_headers(order_id: str | None = None) -> dict[str, str]:
    headers = {
        "Authorization": _basic_auth_header(),
        "Content-Type": "application/json",
    }
    if order_id:
        headers["Idempotency-Key"] = order_id
    return headers


def _toss_error(response: httpx.Response) -> RuntimeError:
    try:
        data = response.json()
    except Exception:
        data = {}
    code = str(data.get("code") or f"HTTP_{response.status_code}")
    message = str(data.get("message") or "Toss Payments request failed.")
    return RuntimeError(f"{code}: {message}")


def validate_donation_amount(amount: int) -> int:
    amount = int(amount)
    if amount < DONATION_MIN_AMOUNT:
        raise ValueError(
            f"후원 금액은 {DONATION_MIN_AMOUNT:,}원 이상으로 입력해 주세요."
        )
    return amount


def create_donation_order(amount: int) -> dict:
    if not donation_is_configured():
        raise RuntimeError("Donation payment is not configured.")

    amount = validate_donation_amount(amount)
    now = _utcnow()
    row = {
        "order_id": f"don_{uuid.uuid4().hex}",
        "customer_key": f"donor_{uuid.uuid4().hex}",
        "amount": amount,
        "status": "pending",
        "created_at": _iso(now),
        "updated_at": _iso(now),
    }
    response = _db().table("donation_orders").insert(row).execute()
    rows = response.data or []
    return rows[0] if rows else row


def get_donation_order(order_id: str) -> dict | None:
    response = (
        _db().table("donation_orders")
        .select("*")
        .eq("order_id", order_id)
        .limit(1)
        .execute()
    )
    rows = response.data or []
    return rows[0] if rows else None


def confirm_donation(payment_key: str, order_id: str, callback_amount: int) -> dict:
    if not donation_is_configured():
        raise RuntimeError("Donation payment is not configured.")

    order = get_donation_order(order_id)
    if not order:
        raise RuntimeError("DONATION_ORDER_NOT_FOUND: 후원 주문을 찾을 수 없습니다.")

    intended_amount = int(order.get("amount") or 0)
    if int(callback_amount) != intended_amount:
        raise RuntimeError("DONATION_AMOUNT_MISMATCH: 후원 금액 검증에 실패했습니다.")

    if order.get("status") == "paid":
        return order
    if order.get("status") != "pending":
        raise RuntimeError("DONATION_ORDER_NOT_PENDING: 이미 종료된 후원 주문입니다.")

    with httpx.Client(timeout=70.0) as client:
        response = client.post(
            f"{TOSS_API_BASE}/v1/payments/confirm",
            headers=_toss_headers(order_id),
            json={
                "paymentKey": payment_key,
                "orderId": order_id,
                "amount": intended_amount,
            },
        )

    if response.status_code != 200:
        raise _toss_error(response)

    payment = response.json()
    if payment.get("status") != "DONE":
        raise RuntimeError(f"UNEXPECTED_PAYMENT_STATUS: {payment.get('status')}")
    if str(payment.get("orderId") or "") != order_id:
        raise RuntimeError("DONATION_ORDER_ID_MISMATCH: 승인 주문번호가 일치하지 않습니다.")
    if int(payment.get("totalAmount") or 0) != intended_amount:
        raise RuntimeError("DONATION_CONFIRMED_AMOUNT_MISMATCH: 승인 금액이 일치하지 않습니다.")

    now = _utcnow()
    update = {
        "status": "paid",
        "payment_key": str(payment.get("paymentKey") or payment_key),
        "method": str(payment.get("method") or ""),
        "paid_at": _iso(now),
        "updated_at": _iso(now),
        "failure_code": None,
        "failure_message": None,
    }
    _db().table("donation_orders").update(update).eq("order_id", order_id).execute()
    return {**order, **update}


def mark_donation_failed(order_id: str, code: str = "PAYMENT_FAILED", message: str = "") -> None:
    if not order_id or not donation_is_configured():
        return
    order = get_donation_order(order_id)
    if not order or order.get("status") != "pending":
        return
    _db().table("donation_orders").update({
        "status": "failed",
        "failure_code": str(code or "PAYMENT_FAILED")[:100],
        "failure_message": str(message or "")[:1000],
        "updated_at": _iso(_utcnow()),
    }).eq("order_id", order_id).execute()
