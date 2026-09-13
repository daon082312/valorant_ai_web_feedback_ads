from __future__ import annotations

import asyncio
import hmac
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field, ValidationError

from donation_service import (
    DONATION_MAX_AMOUNT,
    DONATION_MIN_AMOUNT,
    DONATION_ORDER_NAME,
    TOSS_CLIENT_KEY,
    confirm_donation,
    create_donation_order,
    donation_is_configured,
    mark_donation_failed,
)

BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


class DonationIntentRequest(BaseModel):
    amount: int = Field(ge=DONATION_MIN_AMOUNT, le=DONATION_MAX_AMOUNT)


def build_donation_router(public_base_url: str) -> APIRouter:
    router = APIRouter()

    def base_url(request: Request) -> str:
        return (public_base_url or str(request.base_url).rstrip("/")).rstrip("/")

    def same_origin(request: Request) -> bool:
        origin = request.headers.get("origin", "").rstrip("/")
        if not origin:
            return True
        return hmac.compare_digest(origin, base_url(request))

    @router.get("/donate", response_class=HTMLResponse)
    async def donate_page(request: Request):
        return templates.TemplateResponse(
            request=request,
            name="donate.html",
            context={
                "request": request,
                "adsense_enabled": False,
                "adblock_enabled": False,
                "is_premium": False,
                "current_user_email": "",
                "donation_configured": donation_is_configured(),
                "donation_min_amount": DONATION_MIN_AMOUNT,
                "donation_max_amount": DONATION_MAX_AMOUNT,
            },
        )

    @router.post("/donation/intent")
    async def donation_intent(request: Request):
        if not same_origin(request):
            return JSONResponse({"detail": "잘못된 요청 출처입니다."}, status_code=403)
        if not donation_is_configured():
            return JSONResponse({"detail": "후원 결제 시스템이 아직 설정되지 않았습니다."}, status_code=503)

        try:
            payload = DonationIntentRequest.model_validate(await request.json())
        except (ValidationError, ValueError, TypeError):
            return JSONResponse(
                {
                    "detail": (
                        f"후원 금액은 {DONATION_MIN_AMOUNT:,}원 이상 "
                        f"{DONATION_MAX_AMOUNT:,}원 이하로 입력해 주세요."
                    )
                },
                status_code=400,
            )

        try:
            order = await asyncio.to_thread(create_donation_order, payload.amount)
        except Exception as exc:
            print(f"[Donation] intent failed: {type(exc).__name__}: {exc}")
            return JSONResponse(
                {"detail": "후원 주문을 준비하지 못했습니다. 결제 설정과 Supabase 테이블을 확인해 주세요."},
                status_code=503,
            )

        root = base_url(request)
        return JSONResponse({
            "clientKey": TOSS_CLIENT_KEY,
            "customerKey": order["customer_key"],
            "orderId": order["order_id"],
            "orderName": DONATION_ORDER_NAME,
            "amount": int(order["amount"]),
            "successUrl": f"{root}/donation/success",
            "failUrl": f"{root}/donation/fail",
        })

    @router.get("/donation/success")
    async def donation_success(
        request: Request,
        paymentKey: str = "",
        orderId: str = "",
        amount: int = 0,
    ):
        if not donation_is_configured() or not paymentKey or not orderId or amount <= 0:
            return RedirectResponse(url="/donate?donation=setup_error", status_code=303)

        try:
            order = await asyncio.to_thread(confirm_donation, paymentKey, orderId, amount)
            confirmed_amount = int(order.get("amount") or amount)
            return RedirectResponse(
                url=f"/donate?donation=success&amount={confirmed_amount}",
                status_code=303,
            )
        except Exception as exc:
            print(f"[Donation] confirm failed: {type(exc).__name__}: {exc}")
            try:
                await asyncio.to_thread(mark_donation_failed, orderId, "CONFIRM_FAILED", str(exc))
            except Exception:
                pass
            return RedirectResponse(url="/donate?donation=failed", status_code=303)

    @router.get("/donation/fail")
    async def donation_fail(
        request: Request,
        code: str = "PAYMENT_FAILED",
        message: str = "",
        orderId: str = "",
    ):
        try:
            await asyncio.to_thread(mark_donation_failed, orderId, code, message)
        except Exception:
            pass
        safe_code = quote((code or "PAYMENT_FAILED")[:80], safe="")
        return RedirectResponse(url=f"/donate?donation=failed&code={safe_code}", status_code=303)

    return router
