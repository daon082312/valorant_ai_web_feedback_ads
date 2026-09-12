from __future__ import annotations

import asyncio
import hmac
import json
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from analysis_history_store import (
    delete_analysis,
    get_analysis,
    history_is_configured,
    list_analyses,
    save_analysis,
)

BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


class SaveAnalysisRequest(BaseModel):
    file_name: str = Field(default="영상", max_length=255)
    analysis: dict


def _history_error_message(exc: Exception) -> str:
    text = str(exc)
    lower = text.lower()

    if (
        "pgrst205" in lower
        or "could not find the table" in lower
        or ("analysis_history" in lower and "schema cache" in lower)
        or ("relation" in lower and "analysis_history" in lower)
    ):
        return (
            "분석 저장 테이블이 아직 준비되지 않았습니다. Supabase SQL Editor에서 "
            "최신 supabase_analysis_history.sql 전체를 실행해 주세요."
        )

    if (
        "permission denied" in lower
        or "row-level security" in lower
        or "42501" in lower
        or "policy" in lower
    ):
        return (
            "분석 저장 권한 설정이 이전 버전입니다. Supabase SQL Editor에서 "
            "최신 supabase_analysis_history.sql 전체를 다시 실행해 주세요."
        )

    if "on conflict" in lower or "42p10" in lower or "unique" in lower:
        return (
            "분석 저장 테이블의 키 설정이 이전 버전입니다. Supabase SQL Editor에서 "
            "최신 supabase_analysis_history.sql 전체를 다시 실행해 주세요."
        )

    if "jwt" in lower or "session" in lower or "auth" in lower:
        return "로그인 세션을 확인하지 못했습니다. 다시 로그인한 뒤 시도해 주세요."

    return "저장된 분석을 불러오지 못했습니다."


def build_history_router(get_auth_context, attach_refreshed_session, public_base_url: str) -> APIRouter:
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

    @router.get("/history")
    async def history_page(request: Request):
        auth = await auth_for(request)
        if not auth:
            return RedirectResponse(url="/login", status_code=303)

        response = templates.TemplateResponse(
            request=request,
            name="history.html",
            context={
                "request": request,
                "adsense_enabled": False,
                "adblock_enabled": False,
                "auth_enabled": True,
                "is_premium": False,
                "current_user_email": auth.email,
                "contact_email": "",
            },
        )
        return attach_refreshed_session(response, auth)

    @router.get("/api/history")
    async def history_list(request: Request, limit: int = 50):
        auth = await auth_for(request)
        if not auth:
            return JSONResponse({"detail": "로그인이 필요합니다."}, status_code=401)
        if not history_is_configured():
            return JSONResponse(
                {"detail": "분석 저장 데이터베이스가 아직 설정되지 않았습니다."},
                status_code=503,
            )

        try:
            items = await asyncio.to_thread(
                list_analyses,
                auth.user_id,
                auth.access_token,
                auth.refresh_token,
                limit,
            )
            response = JSONResponse({"items": items})
            return attach_refreshed_session(response, auth)
        except Exception as exc:
            print(f"[History] 목록 조회 실패: {type(exc).__name__}: {exc}")
            return JSONResponse(
                {"detail": _history_error_message(exc)},
                status_code=503,
            )

    @router.get("/api/history/{analysis_id}")
    async def history_detail(request: Request, analysis_id: str):
        auth = await auth_for(request)
        if not auth:
            return JSONResponse({"detail": "로그인이 필요합니다."}, status_code=401)
        if not history_is_configured():
            return JSONResponse({"detail": "분석 저장 기능이 아직 설정되지 않았습니다."}, status_code=503)

        try:
            item = await asyncio.to_thread(
                get_analysis,
                auth.user_id,
                analysis_id[:100],
                auth.access_token,
                auth.refresh_token,
            )
            if not item:
                return JSONResponse({"detail": "저장된 분석을 찾을 수 없습니다."}, status_code=404)
            response = JSONResponse(item)
            return attach_refreshed_session(response, auth)
        except Exception as exc:
            print(f"[History] 상세 조회 실패: {type(exc).__name__}: {exc}")
            return JSONResponse({"detail": _history_error_message(exc)}, status_code=503)

    @router.post("/api/history")
    async def history_save(request: Request, payload: SaveAnalysisRequest):
        if not same_origin(request):
            return JSONResponse({"detail": "잘못된 요청 출처입니다."}, status_code=403)

        auth = await auth_for(request)
        if not auth:
            return JSONResponse({"detail": "로그인이 필요합니다."}, status_code=401)
        if not history_is_configured():
            return JSONResponse({"detail": "분석 저장 기능이 아직 설정되지 않았습니다."}, status_code=503)

        analysis_id = str(payload.analysis.get("analysis_id") or "").strip()
        if not analysis_id:
            return JSONResponse({"detail": "analysis_id가 없습니다."}, status_code=400)

        if len(json.dumps(payload.analysis, ensure_ascii=False)) > 300_000:
            return JSONResponse({"detail": "분석 결과가 너무 큽니다."}, status_code=413)

        try:
            await asyncio.to_thread(
                save_analysis,
                auth.user_id,
                payload.file_name,
                payload.analysis,
                auth.access_token,
                auth.refresh_token,
            )
            response = JSONResponse({"ok": True, "analysis_id": analysis_id})
            return attach_refreshed_session(response, auth)
        except Exception as exc:
            print(f"[History] 저장 실패: {type(exc).__name__}: {exc}")
            return JSONResponse({"detail": _history_error_message(exc)}, status_code=503)

    @router.delete("/api/history/{analysis_id}")
    async def history_delete(request: Request, analysis_id: str):
        if not same_origin(request):
            return JSONResponse({"detail": "잘못된 요청 출처입니다."}, status_code=403)

        auth = await auth_for(request)
        if not auth:
            return JSONResponse({"detail": "로그인이 필요합니다."}, status_code=401)
        if not history_is_configured():
            return JSONResponse({"detail": "분석 저장 기능이 아직 설정되지 않았습니다."}, status_code=503)

        try:
            deleted = await asyncio.to_thread(
                delete_analysis,
                auth.user_id,
                analysis_id[:100],
                auth.access_token,
                auth.refresh_token,
            )
            response = JSONResponse({"ok": True, "deleted": deleted})
            return attach_refreshed_session(response, auth)
        except Exception as exc:
            print(f"[History] 삭제 실패: {type(exc).__name__}: {exc}")
            return JSONResponse({"detail": _history_error_message(exc)}, status_code=503)

    return router
