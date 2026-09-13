from __future__ import annotations

import asyncio
import hmac
import os
import time
import uuid
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from direct_gemini import analyze_direct_gemini_file, start_direct_video_upload
from membership_store import is_premium, membership_is_configured
from usage_store import (
    DailyLimitExceeded,
    get_usage,
    record_success,
    usage_is_configured,
)


ACCOUNT_DAILY_LIMIT = int(os.getenv("DAILY_ANALYSIS_LIMIT", "3"))
IP_DAILY_LIMIT = int(os.getenv("IP_DAILY_ANALYSIS_LIMIT", "6"))
MAX_UPLOAD_MB = int(os.getenv("MAX_UPLOAD_MB", "100"))
DIRECT_UPLOAD_TICKET_TTL = int(os.getenv("DIRECT_UPLOAD_TICKET_TTL", "1200"))

_ALLOWED_MIME_BY_SUFFIX = {
    ".mp4": "video/mp4",
    ".mov": "video/quicktime",
    ".webm": "video/webm",
    ".avi": "video/x-msvideo",
    ".mkv": "video/x-matroska",
}

_ticket_guard = asyncio.Lock()
_tickets: dict[str, dict] = {}
_user_ticket: dict[str, str] = {}
_user_locks: dict[str, asyncio.Lock] = {}
_user_locks_guard = asyncio.Lock()


class DirectUploadStartRequest(BaseModel):
    filename: str = Field(min_length=1, max_length=220)
    size_bytes: int = Field(ge=1)
    mime_type: str = Field(default="", max_length=120)


class DirectAnalyzeRequest(BaseModel):
    ticket_id: str = Field(min_length=12, max_length=100)


def _client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _resolved_mime(filename: str, browser_mime: str) -> str:
    suffix = Path(filename).suffix.lower()
    if suffix not in _ALLOWED_MIME_BY_SUFFIX:
        raise ValueError("지원하지 않는 영상 형식입니다.")
    value = str(browser_mime or "").strip().lower()
    if value.startswith("video/"):
        return value
    return _ALLOWED_MIME_BY_SUFFIX[suffix]


async def _premium_for(user_id: str) -> bool:
    if not membership_is_configured():
        return False
    try:
        return await asyncio.to_thread(is_premium, user_id)
    except Exception:
        return False


async def _analysis_lock(user_id: str) -> asyncio.Lock:
    async with _user_locks_guard:
        if user_id not in _user_locks:
            _user_locks[user_id] = asyncio.Lock()
        return _user_locks[user_id]


def _usage_db_error() -> JSONResponse:
    return JSONResponse(
        status_code=503,
        content={
            "detail": {
                "code": "USAGE_DATABASE_UNAVAILABLE",
                "message": "사용 횟수 데이터베이스에 연결할 수 없습니다. 잠시 후 다시 시도해 주세요.",
                "daily_limit": ACCOUNT_DAILY_LIMIT,
            }
        },
    )


def _limit_response() -> JSONResponse:
    return JSONResponse(
        status_code=429,
        content={
            "detail": {
                "code": "USER_DAILY_LIMIT",
                "message": "오늘의 무료 분석 한도를 모두 사용했습니다. 계정 또는 동일 네트워크의 일일 한도에 도달했습니다.",
                "remaining": 0,
                "daily_limit": ACCOUNT_DAILY_LIMIT,
            }
        },
    )


def _classify_analysis_error(exc: Exception) -> tuple[int, str, str]:
    text = str(exc or "")
    lower = text.lower()
    if "429" in text or "resource_exhausted" in lower or "quota" in lower or "prepayment credits" in lower:
        return 503, "AI_QUOTA_EXHAUSTED", "현재 AI API 사용 한도 또는 결제 잔액 문제로 분석할 수 없습니다. 개인 무료 분석 횟수는 차감되지 않았습니다."
    if "503" in text or "unavailable" in lower or "high demand" in lower:
        return 503, "AI_TEMPORARILY_UNAVAILABLE", "현재 AI 서버가 혼잡합니다. 개인 무료 분석 횟수는 차감되지 않았습니다."
    return 500, "ANALYSIS_FAILED", "분석 중 오류가 발생했습니다. 개인 무료 분석 횟수는 차감되지 않았습니다."


async def _register_ticket(
    user_id: str,
    filename: str,
    size_bytes: int,
    mime_type: str,
    file_name: str,
) -> str:
    now = time.time()
    ticket_id = f"du_{uuid.uuid4().hex}"
    async with _ticket_guard:
        expired = [key for key, value in _tickets.items() if float(value.get("expires_at", 0)) <= now]
        for key in expired:
            old = _tickets.pop(key, None)
            if old and _user_ticket.get(str(old.get("user_id"))) == key:
                _user_ticket.pop(str(old.get("user_id")), None)

        old_ticket = _user_ticket.get(user_id)
        if old_ticket:
            _tickets.pop(old_ticket, None)

        _tickets[ticket_id] = {
            "user_id": user_id,
            "filename": filename,
            "size_bytes": int(size_bytes),
            "mime_type": mime_type,
            "file_name": file_name,
            "expires_at": now + max(300, DIRECT_UPLOAD_TICKET_TTL),
        }
        _user_ticket[user_id] = ticket_id
    return ticket_id


async def _consume_ticket(ticket_id: str, user_id: str) -> dict | None:
    now = time.time()
    async with _ticket_guard:
        ticket = _tickets.get(ticket_id)
        if not ticket:
            return None
        if str(ticket.get("user_id")) != user_id or float(ticket.get("expires_at", 0)) <= now:
            _tickets.pop(ticket_id, None)
            if _user_ticket.get(user_id) == ticket_id:
                _user_ticket.pop(user_id, None)
            return None
        _tickets.pop(ticket_id, None)
        if _user_ticket.get(user_id) == ticket_id:
            _user_ticket.pop(user_id, None)
        return ticket


def build_direct_upload_router(get_auth_context, attach_refreshed_session, public_base_url: str) -> APIRouter:
    router = APIRouter()

    async def auth_for(request: Request):
        return await asyncio.to_thread(get_auth_context, request)

    def base_url(request: Request) -> str:
        return (public_base_url or str(request.base_url).rstrip("/")).rstrip("/")

    def same_origin(request: Request) -> bool:
        origin = request.headers.get("origin", "").rstrip("/")
        if not origin:
            return True
        return hmac.compare_digest(origin, base_url(request))

    @router.post("/analysis/direct-upload/start")
    async def direct_upload_start(request: Request, payload: DirectUploadStartRequest):
        if not same_origin(request):
            return JSONResponse({"detail": "잘못된 요청 출처입니다."}, status_code=403)

        auth = await auth_for(request)
        if not auth:
            return JSONResponse(
                {"detail": {"code": "LOGIN_REQUIRED", "message": "분석하려면 로그인해 주세요."}},
                status_code=401,
            )

        premium = await _premium_for(auth.user_id)
        if not premium and not usage_is_configured():
            return _usage_db_error()

        if payload.size_bytes > MAX_UPLOAD_MB * 1024 * 1024:
            return JSONResponse(
                {"detail": f"업로드 파일은 최대 {MAX_UPLOAD_MB}MB까지 가능합니다."},
                status_code=413,
            )

        try:
            mime_type = _resolved_mime(payload.filename, payload.mime_type)
        except ValueError as exc:
            return JSONResponse({"detail": str(exc)}, status_code=400)

        ip = _client_ip(request)
        if not premium:
            try:
                usage_before = await asyncio.to_thread(
                    get_usage,
                    auth.user_id,
                    ip,
                    ACCOUNT_DAILY_LIMIT,
                    IP_DAILY_LIMIT,
                )
            except Exception:
                return _usage_db_error()
            if usage_before["remaining"] <= 0:
                return _limit_response()

        try:
            session = await asyncio.to_thread(
                start_direct_video_upload,
                payload.filename,
                payload.size_bytes,
                mime_type,
            )
            ticket_id = await _register_ticket(
                auth.user_id,
                payload.filename,
                payload.size_bytes,
                mime_type,
                str(session["file_name"]),
            )
            response = JSONResponse({
                "ok": True,
                "ticket_id": ticket_id,
                "upload_url": session["upload_url"],
                "mime_type": mime_type,
                "size_bytes": payload.size_bytes,
                "transport": "browser_to_gemini_direct",
            })
            return attach_refreshed_session(response, auth)
        except HTTPException as exc:
            response = JSONResponse({"detail": exc.detail}, status_code=exc.status_code)
            return attach_refreshed_session(response, auth)
        except Exception as exc:
            print(f"[DirectUpload] session error: {type(exc).__name__}: {exc}")
            response = JSONResponse(
                {"detail": {"code": "DIRECT_UPLOAD_UNAVAILABLE", "message": "직접 업로드 세션을 만들지 못했습니다."}},
                status_code=503,
            )
            return attach_refreshed_session(response, auth)

    @router.post("/analysis/direct")
    async def direct_analyze(request: Request, payload: DirectAnalyzeRequest):
        if not same_origin(request):
            return JSONResponse({"detail": "잘못된 요청 출처입니다."}, status_code=403)

        auth = await auth_for(request)
        if not auth:
            return JSONResponse(
                {"detail": {"code": "LOGIN_REQUIRED", "message": "분석하려면 로그인해 주세요."}},
                status_code=401,
            )

        premium = await _premium_for(auth.user_id)
        if not premium and not usage_is_configured():
            return _usage_db_error()

        ip = _client_ip(request)
        lock = await _analysis_lock(auth.user_id)
        async with lock:
            if premium:
                usage_before = {"remaining": None}
            else:
                try:
                    usage_before = await asyncio.to_thread(
                        get_usage,
                        auth.user_id,
                        ip,
                        ACCOUNT_DAILY_LIMIT,
                        IP_DAILY_LIMIT,
                    )
                except Exception:
                    return _usage_db_error()
                if usage_before["remaining"] <= 0:
                    return _limit_response()

            ticket = await _consume_ticket(payload.ticket_id, auth.user_id)
            if not ticket:
                return JSONResponse(
                    {"detail": {"code": "DIRECT_UPLOAD_TICKET_INVALID", "message": "직접 업로드 세션이 만료되었거나 유효하지 않습니다."}},
                    status_code=400,
                )

            try:
                result = await asyncio.to_thread(
                    analyze_direct_gemini_file,
                    str(ticket["file_name"]),
                    expected_size_bytes=int(ticket["size_bytes"]),
                    expected_mime_type=str(ticket["mime_type"]),
                )

                if premium:
                    usage_after = {
                        "remaining": None,
                        "account_remaining": None,
                        "ip_remaining": None,
                    }
                else:
                    try:
                        usage_after = await asyncio.to_thread(
                            record_success,
                            auth.user_id,
                            ip,
                            ACCOUNT_DAILY_LIMIT,
                            IP_DAILY_LIMIT,
                        )
                    except DailyLimitExceeded:
                        return _limit_response()
                    except Exception:
                        return _usage_db_error()

                result["analysis_id"] = uuid.uuid4().hex
                result["usage"] = {
                    "premium": premium,
                    "unlimited": premium,
                    "remaining": usage_after["remaining"],
                    "daily_limit": None if premium else ACCOUNT_DAILY_LIMIT,
                    "account_remaining": usage_after["account_remaining"],
                    "ip_remaining": usage_after["ip_remaining"],
                }
                result["render_video_proxy_used"] = False
                result["transport_mode"] = "browser_to_gemini_direct"

                response = JSONResponse(result)
                return attach_refreshed_session(response, auth)

            except HTTPException as exc:
                response = JSONResponse({"detail": exc.detail}, status_code=exc.status_code)
                return attach_refreshed_session(response, auth)
            except Exception as exc:
                print(f"[DirectUpload] analysis error: {type(exc).__name__}: {exc}")
                status, code, message = _classify_analysis_error(exc)
                response = JSONResponse(
                    status_code=status,
                    content={
                        "detail": {
                            "code": code,
                            "message": message,
                            "premium": premium,
                            "remaining": usage_before["remaining"],
                            "daily_limit": None if premium else ACCOUNT_DAILY_LIMIT,
                        }
                    },
                )
                return attach_refreshed_session(response, auth)

    return router
