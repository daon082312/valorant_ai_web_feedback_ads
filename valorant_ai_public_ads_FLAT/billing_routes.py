from __future__ import annotations

import asyncio
import hmac
import os
from urllib.parse import quote

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, RedirectResponse

from billing_service import (
    BILLING_CRON_TOKEN,
    PREMIUM_MONTHLY_PRICE,
    TOSS_CLIENT_KEY,
    activate_after_payment,
    billing_is_configured,
    billing_scheduler_is_configured,
    cancel_subscription,
    charge_billing_key,
    clear_billing_key,
    delete_billing_key,
    ensure_subscription,
    get_subscription,
    issue_billing_key,
    mark_initial_payment_failed,
    record_billing_consent,
    run_due_renewals,
    save_billing_key,
)
from donation_routes import build_donation_router
from history_routes import build_history_router
from vision_routes import build_vision_router


def build_billing_router(get_auth_context, attach_refreshed_session, public_base_url: str) -> APIRouter:
    router = APIRouter()

    def base_url(request: Request) -> str:
        return (public_base_url or str(request.base_url).rstrip("/")).rstrip("/")

    def same_origin(request: Request) -> bool:
        origin = request.headers.get("origin", "").rstrip("/")
        if not origin:
            return True
        return hmac.compare_digest(origin, base_url(request))

    async def auth_for(request: Request):
        return await asyncio.to_thread(get_auth_context, request)

    @router.get("/billing/status")
    async def billing_status(request: Request):
        auth = await auth_for(request)
        if not auth:
            return JSONResponse({"authenticated": False}, status_code=401)

        subscription = await asyncio.to_thread(get_subscription, auth.user_id)
        payload = {
            "authenticated": True,
            "configured": billing_is_configured(),
            "scheduler_configured": billing_scheduler_is_configured(),
            "price": PREMIUM_MONTHLY_PRICE,
            "status": subscription.get("status") if subscription else "inactive",
            "current_period_end": subscription.get("current_period_end") if subscription else None,
            "cancel_at_period_end": bool(subscription.get("cancel_at_period_end")) if subscription else False,
            "failure_count": int(subscription.get("failure_count") or 0) if subscription else 0,
        }
        response = JSONResponse(payload)
        return attach_refreshed_session(response, auth)

    @router.post("/billing/intent")
    async def billing_intent(request: Request):
        if not same_origin(request):
            return JSONResponse({"detail": "잘못된 요청 출처입니다."}, status_code=403)
        auth = await auth_for(request)
        if not auth:
            return JSONResponse({"detail": "로그인이 필요합니다."}, status_code=401)
        if not billing_is_configured():
            return JSONResponse({"detail": "결제 시스템이 아직 설정되지 않았습니다."}, status_code=503)

        subscription = await asyncio.to_thread(record_billing_consent, auth.user_id, auth.email)
        root = base_url(request)
        response = JSONResponse({
            "clientKey": TOSS_CLIENT_KEY,
            "customerKey": subscription["customer_key"],
            "price": PREMIUM_MONTHLY_PRICE,
            "successUrl": f"{root}/billing/success",
            "failUrl": f"{root}/billing/fail",
            "customerEmail": auth.email,
        })
        return attach_refreshed_session(response, auth)

    @router.get("/billing/success")
    async def billing_success(request: Request, authKey: str = "", customerKey: str = ""):
        auth = await auth_for(request)
        if not auth:
            return RedirectResponse(url="/login", status_code=303)
        if not billing_is_configured() or not authKey or not customerKey:
            return RedirectResponse(url="/premium?payment=setup_error", status_code=303)

        subscription = await asyncio.to_thread(ensure_subscription, auth.user_id, auth.email)
        if customerKey != subscription.get("customer_key") or not subscription.get("consent_at"):
            return RedirectResponse(url="/premium?payment=invalid", status_code=303)

        order_id = ""
        try:
            billing = await asyncio.to_thread(issue_billing_key, authKey, customerKey)
            billing_key = str(billing.get("billingKey") or "")
            if not billing_key:
                raise RuntimeError("NO_BILLING_KEY: 빌링키가 반환되지 않았습니다.")
            await asyncio.to_thread(save_billing_key, auth.user_id, billing_key)

            import uuid
            order_id = f"prem_{uuid.uuid4().hex}"
            payment = await asyncio.to_thread(
                charge_billing_key,
                billing_key,
                customerKey,
                auth.email,
                amount=PREMIUM_MONTHLY_PRICE,
                order_id=order_id,
            )
            await asyncio.to_thread(activate_after_payment, auth.user_id, payment)
            response = RedirectResponse(url="/premium?payment=success", status_code=303)
            return attach_refreshed_session(response, auth)
        except Exception as exc:
            if order_id:
                await asyncio.to_thread(mark_initial_payment_failed, auth.user_id, order_id, exc)
            return RedirectResponse(url="/premium?payment=failed", status_code=303)

    @router.get("/billing/fail")
    async def billing_fail(request: Request, code: str = ""):
        safe_code = quote((code or "PAYMENT_FAILED")[:80], safe="")
        return RedirectResponse(url=f"/premium?payment=failed&code={safe_code}", status_code=303)

    @router.post("/billing/cancel")
    async def billing_cancel(request: Request):
        if not same_origin(request):
            return JSONResponse({"detail": "잘못된 요청 출처입니다."}, status_code=403)
        auth = await auth_for(request)
        if not auth:
            return RedirectResponse(url="/login", status_code=303)

        subscription = await asyncio.to_thread(get_subscription, auth.user_id)
        if not subscription:
            return RedirectResponse(url="/premium?cancel=none", status_code=303)

        await asyncio.to_thread(cancel_subscription, auth.user_id)
        billing_key = str(subscription.get("billing_key") or "")
        if billing_key:
            try:
                await asyncio.to_thread(delete_billing_key, billing_key)
                await asyncio.to_thread(clear_billing_key, auth.user_id)
            except Exception:
                # cancel_at_period_end already prevents any further server-side charge.
                pass

        response = RedirectResponse(url="/premium?cancel=success", status_code=303)
        return attach_refreshed_session(response, auth)

    @router.post("/internal/billing/run")
    async def billing_cron(request: Request):
        if not BILLING_CRON_TOKEN:
            return JSONResponse({"detail": "Billing cron token is not configured."}, status_code=503)
        supplied = request.headers.get("authorization", "")
        expected = f"Bearer {BILLING_CRON_TOKEN}"
        if not hmac.compare_digest(supplied, expected):
            return JSONResponse({"detail": "Unauthorized"}, status_code=401)
        if not billing_is_configured():
            return JSONResponse({"detail": "Billing is not configured."}, status_code=503)

        result = await asyncio.to_thread(run_due_renewals)
        return JSONResponse({"ok": True, **result})

    router.include_router(build_donation_router(public_base_url))
    router.include_router(
        build_history_router(
            get_auth_context,
            attach_refreshed_session,
            public_base_url,
        )
    )
    router.include_router(
        build_vision_router(
            get_auth_context,
            attach_refreshed_session,
            public_base_url,
        )
    )

    return router
