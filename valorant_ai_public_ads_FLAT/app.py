from __future__ import annotations

import asyncio
import os
import shutil
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path

from fastapi import FastAPI, File, UploadFile, HTTPException, Request, Form
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field
from supabase import create_client

from analyzer import analyze_video
from feedback_store import save_feedback
from membership_store import is_premium, membership_is_configured
from usage_store import (
    DailyLimitExceeded,
    get_usage,
    record_success,
    usage_is_configured,
)

BASE_DIR = Path(__file__).resolve().parent
ACCOUNT_DAILY_LIMIT = int(os.getenv("DAILY_ANALYSIS_LIMIT", "3"))
IP_DAILY_LIMIT = int(os.getenv("IP_DAILY_ANALYSIS_LIMIT", "6"))
MAX_UPLOAD_MB = int(os.getenv("MAX_UPLOAD_MB", "100"))
CONTACT_EMAIL = os.getenv("CONTACT_EMAIL", "contact@example.com")
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "").rstrip("/")

ADSENSE_CLIENT = os.getenv("ADSENSE_CLIENT", "").strip()
ADSENSE_TOP_SLOT = os.getenv("ADSENSE_TOP_SLOT", "").strip()
ADSENSE_RESULT_SLOT = os.getenv("ADSENSE_RESULT_SLOT", "").strip()

SUPABASE_URL = os.getenv("SUPABASE_URL", "").strip()
SUPABASE_PUBLISHABLE_KEY = (
    os.getenv("SUPABASE_PUBLISHABLE_KEY", "").strip()
    or os.getenv("SUPABASE_ANON_KEY", "").strip()
)

AUTH_ACCESS_COOKIE = "valorant_ai_access"
AUTH_REFRESH_COOKIE = "valorant_ai_refresh"
AUTH_COOKIE_MAX_AGE = 60 * 60 * 24 * 30

app = FastAPI(title="VALORANT AI Coach")
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

_analysis_locks: dict[str, asyncio.Lock] = {}
_analysis_locks_guard = asyncio.Lock()


class FeedbackRequest(BaseModel):
    analysis_id: str = Field(min_length=1, max_length=100)
    target_type: str = Field(pattern="^(overall|event)$")
    event_index: int | None = Field(default=None, ge=0)
    event_timestamp: str | None = Field(default=None, max_length=20)
    event_category: str | None = Field(default=None, max_length=50)
    rating: str = Field(min_length=1, max_length=30)
    categories: list[str] = Field(default_factory=list, max_length=10)
    comment: str = Field(default="", max_length=2000)


@dataclass
class AuthContext:
    user_id: str
    email: str
    access_token: str
    refresh_token: str
    refreshed: bool = False


def common_context(
    request: Request,
    *,
    is_premium_user: bool = False,
    adblock_enabled: bool = True,
    current_user_email: str = "",
) -> dict:
    return {
        "request": request,
        "contact_email": CONTACT_EMAIL,
        "adsense_client": ADSENSE_CLIENT,
        "adsense_top_slot": ADSENSE_TOP_SLOT,
        "adsense_result_slot": ADSENSE_RESULT_SLOT,
        "adsense_enabled": bool(ADSENSE_CLIENT) and not is_premium_user,
        "auth_enabled": auth_is_configured(),
        "is_premium": is_premium_user,
        "adblock_enabled": bool(adblock_enabled and not is_premium_user),
        "current_user_email": current_user_email,
    }


def auth_is_configured() -> bool:
    return bool(SUPABASE_URL and SUPABASE_PUBLISHABLE_KEY)


def _auth_client():
    if not auth_is_configured():
        raise RuntimeError("Supabase Auth is not configured.")
    return create_client(SUPABASE_URL, SUPABASE_PUBLISHABLE_KEY)


def _set_auth_cookies(response, access_token: str, refresh_token: str) -> None:
    response.set_cookie(
        key=AUTH_ACCESS_COOKIE,
        value=access_token,
        max_age=AUTH_COOKIE_MAX_AGE,
        httponly=True,
        secure=True,
        samesite="lax",
        path="/",
    )
    response.set_cookie(
        key=AUTH_REFRESH_COOKIE,
        value=refresh_token,
        max_age=AUTH_COOKIE_MAX_AGE,
        httponly=True,
        secure=True,
        samesite="lax",
        path="/",
    )


def _clear_auth_cookies(response) -> None:
    response.delete_cookie(AUTH_ACCESS_COOKIE, path="/")
    response.delete_cookie(AUTH_REFRESH_COOKIE, path="/")


def _context_from_auth_response(auth_response, refreshed: bool) -> AuthContext | None:
    user = getattr(auth_response, "user", None)
    session = getattr(auth_response, "session", None)
    if not user or not session:
        return None
    return AuthContext(
        user_id=str(user.id),
        email=str(user.email or ""),
        access_token=str(session.access_token),
        refresh_token=str(session.refresh_token),
        refreshed=refreshed,
    )


def get_auth_context(request: Request) -> AuthContext | None:
    if not auth_is_configured():
        return None

    access_token = request.cookies.get(AUTH_ACCESS_COOKIE, "")
    refresh_token = request.cookies.get(AUTH_REFRESH_COOKIE, "")

    if access_token:
        try:
            response = _auth_client().auth.get_user(access_token)
            user = getattr(response, "user", None)
            if user:
                return AuthContext(
                    user_id=str(user.id),
                    email=str(user.email or ""),
                    access_token=access_token,
                    refresh_token=refresh_token,
                    refreshed=False,
                )
        except Exception:
            pass

    if refresh_token:
        try:
            response = _auth_client().auth.refresh_session(refresh_token)
            return _context_from_auth_response(response, refreshed=True)
        except Exception:
            pass

    return None


def _attach_refreshed_session(response, auth: AuthContext | None):
    if auth and auth.refreshed:
        _set_auth_cookies(response, auth.access_token, auth.refresh_token)
    return response


async def _premium_for_user(user_id: str) -> bool:
    if not membership_is_configured():
        return False
    try:
        return await asyncio.to_thread(is_premium, user_id)
    except Exception:
        return False


async def _page_context(
    request: Request,
    *,
    adblock_enabled: bool = True,
) -> tuple[dict, AuthContext | None]:
    auth = await asyncio.to_thread(get_auth_context, request)
    premium = await _premium_for_user(auth.user_id) if auth else False
    context = common_context(
        request,
        is_premium_user=premium,
        adblock_enabled=adblock_enabled,
        current_user_email=auth.email if auth else "",
    )
    return context, auth


async def _render_page(
    request: Request,
    template_name: str,
    *,
    adblock_enabled: bool = True,
    extra: dict | None = None,
):
    context, auth = await _page_context(request, adblock_enabled=adblock_enabled)
    if extra:
        context.update(extra)
    response = templates.TemplateResponse(
        request=request,
        name=template_name,
        context=context,
    )
    return _attach_refreshed_session(response, auth)


def get_client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


async def get_analysis_lock(user_id: str) -> asyncio.Lock:
    async with _analysis_locks_guard:
        if user_id not in _analysis_locks:
            _analysis_locks[user_id] = asyncio.Lock()
        return _analysis_locks[user_id]


def classify_analysis_error(exc: Exception) -> tuple[int, str, str]:
    text = str(exc)
    if (
        "429" in text
        or "RESOURCE_EXHAUSTED" in text
        or "quota" in text.lower()
        or "prepayment credits" in text.lower()
    ):
        return 503, "AI_QUOTA_EXHAUSTED", (
            "현재 AI API 사용 한도 또는 결제 잔액 문제로 분석할 수 없습니다. "
            "개인 무료 분석 횟수는 차감되지 않았습니다."
        )
    if "503" in text or "UNAVAILABLE" in text or "high demand" in text.lower():
        return 503, "AI_TEMPORARILY_UNAVAILABLE", (
            "현재 AI 서버가 혼잡합니다. 개인 무료 분석 횟수는 차감되지 않았습니다."
        )
    return 500, "ANALYSIS_FAILED", (
        "분석 중 오류가 발생했습니다. 개인 무료 분석 횟수는 차감되지 않았습니다."
    )


def usage_database_error_response() -> JSONResponse:
    return JSONResponse(
        status_code=503,
        content={"detail": {"code": "USAGE_DATABASE_UNAVAILABLE", "message": (
            "사용 횟수 데이터베이스에 연결할 수 없습니다. 잠시 후 다시 시도해 주세요."
        ), "daily_limit": ACCOUNT_DAILY_LIMIT}},
    )


def login_required_response() -> JSONResponse:
    return JSONResponse(
        status_code=401,
        content={"detail": {"code": "LOGIN_REQUIRED", "message": "분석하려면 로그인해 주세요.", "login_url": "/login"}},
    )


def _auth_redirect_url(request: Request) -> str:
    base = PUBLIC_BASE_URL or str(request.base_url).rstrip("/")
    return f"{base}/login?verified=1"


def _render_auth(request: Request, mode: str, *, error: str = "", message: str = "", status_code: int = 200):
    context = common_context(request, adblock_enabled=False)
    context.update({"mode": mode, "error": error, "message": message})
    return templates.TemplateResponse(
        request=request,
        name="auth.html",
        context=context,
        status_code=status_code,
    )


@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    return await _render_page(request, "index.html")


@app.get("/about", response_class=HTMLResponse)
async def about(request: Request):
    return await _render_page(request, "about.html")


@app.get("/privacy", response_class=HTMLResponse)
async def privacy(request: Request):
    return await _render_page(request, "privacy.html")


@app.get("/terms", response_class=HTMLResponse)
async def terms(request: Request):
    return await _render_page(request, "terms.html")


@app.get("/guide", response_class=HTMLResponse)
async def guide(request: Request):
    return await _render_page(request, "guide.html")


@app.get("/premium", response_class=HTMLResponse)
async def premium_page(request: Request):
    return await _render_page(request, "premium.html", adblock_enabled=False)


@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, registered: int = 0, verified: int = 0):
    message = ""
    if registered:
        message = "가입 확인 이메일을 보냈습니다. 이메일의 인증 링크를 누른 뒤 로그인해 주세요."
    elif verified:
        message = "이메일 인증이 완료되었습니다. 로그인해 주세요."
    return _render_auth(request, "login", message=message)


@app.get("/signup", response_class=HTMLResponse)
async def signup_page(request: Request):
    return _render_auth(request, "signup")


@app.post("/auth/signup")
async def signup(request: Request, email: str = Form(...), password: str = Form(...)):
    if not auth_is_configured():
        return _render_auth(request, "signup", error="로그인 시스템이 아직 설정되지 않았습니다.", status_code=503)

    email = email.strip().lower()
    if len(password) < 8:
        return _render_auth(request, "signup", error="비밀번호는 8자 이상이어야 합니다.", status_code=400)

    try:
        response = _auth_client().auth.sign_up({
            "email": email,
            "password": password,
            "options": {"email_redirect_to": _auth_redirect_url(request)},
        })
        auth = _context_from_auth_response(response, refreshed=False)
        if auth:
            redirect = RedirectResponse(url="/", status_code=303)
            _set_auth_cookies(redirect, auth.access_token, auth.refresh_token)
            return redirect
        return RedirectResponse(url="/login?registered=1", status_code=303)
    except Exception as exc:
        text = str(exc)
        lowered = text.lower()
        if "already registered" in lowered:
            message = "이미 가입된 이메일입니다. 로그인해 주세요."
        elif "rate limit" in lowered:
            message = "인증 이메일 발송 한도에 도달했습니다. 잠시 후 다시 시도해 주세요."
        else:
            message = "회원가입에 실패했습니다. 이메일 주소와 비밀번호를 확인해 주세요."
        return _render_auth(request, "signup", error=message, status_code=400)


@app.post("/auth/login")
async def login(request: Request, email: str = Form(...), password: str = Form(...)):
    if not auth_is_configured():
        return _render_auth(request, "login", error="로그인 시스템이 아직 설정되지 않았습니다.", status_code=503)

    try:
        response = _auth_client().auth.sign_in_with_password({
            "email": email.strip().lower(),
            "password": password,
        })
        auth = _context_from_auth_response(response, refreshed=False)
        if not auth:
            raise RuntimeError("No Supabase session returned.")
        redirect = RedirectResponse(url="/", status_code=303)
        _set_auth_cookies(redirect, auth.access_token, auth.refresh_token)
        return redirect
    except Exception:
        return _render_auth(
            request,
            "login",
            error="로그인에 실패했습니다. 이메일 인증 여부와 비밀번호를 확인해 주세요.",
            status_code=401,
        )


@app.post("/auth/logout")
async def logout(request: Request):
    access_token = request.cookies.get(AUTH_ACCESS_COOKIE, "")
    refresh_token = request.cookies.get(AUTH_REFRESH_COOKIE, "")
    if access_token and refresh_token and auth_is_configured():
        try:
            client = _auth_client()
            client.auth.set_session(access_token, refresh_token)
            client.auth.sign_out()
        except Exception:
            pass
    redirect = RedirectResponse(url="/", status_code=303)
    _clear_auth_cookies(redirect)
    return redirect


@app.get("/auth/me")
async def auth_me(request: Request):
    auth = await asyncio.to_thread(get_auth_context, request)
    if not auth:
        response = JSONResponse({"authenticated": False, "email": None, "premium": False})
        _clear_auth_cookies(response)
        return response

    premium = await _premium_for_user(auth.user_id)
    response = JSONResponse({
        "authenticated": True,
        "email": auth.email,
        "premium": premium,
    })
    return _attach_refreshed_session(response, auth)


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "auth_configured": auth_is_configured(),
        "usage_database_configured": usage_is_configured(),
        "premium_database_configured": membership_is_configured(),
    }


@app.get("/usage")
async def usage(request: Request):
    auth = await asyncio.to_thread(get_auth_context, request)
    if not auth:
        return login_required_response()

    premium = await _premium_for_user(auth.user_id)
    if premium:
        response = JSONResponse({
            "premium": True,
            "unlimited": True,
            "remaining": None,
            "daily_limit": None,
            "account_remaining": None,
            "account_limit": None,
            "ip_remaining": None,
            "ip_limit": None,
        })
        return _attach_refreshed_session(response, auth)

    if not usage_is_configured():
        return usage_database_error_response()

    ip = get_client_ip(request)
    try:
        usage_data = await asyncio.to_thread(
            get_usage,
            auth.user_id,
            ip,
            ACCOUNT_DAILY_LIMIT,
            IP_DAILY_LIMIT,
        )
    except Exception:
        return usage_database_error_response()

    response = JSONResponse({
        "premium": False,
        "unlimited": False,
        "remaining": usage_data["remaining"],
        "daily_limit": ACCOUNT_DAILY_LIMIT,
        "account_remaining": usage_data["account_remaining"],
        "account_limit": ACCOUNT_DAILY_LIMIT,
        "ip_remaining": usage_data["ip_remaining"],
        "ip_limit": IP_DAILY_LIMIT,
    })
    return _attach_refreshed_session(response, auth)


@app.get("/ads.txt", response_class=PlainTextResponse)
async def ads_txt():
    if not ADSENSE_CLIENT.startswith("ca-pub-"):
        return "# AdSense publisher ID is not configured yet.\n"
    publisher_id = ADSENSE_CLIENT.removeprefix("ca-")
    return f"google.com, {publisher_id}, DIRECT, f08c47fec0942fa0\n"


@app.get("/robots.txt", response_class=PlainTextResponse)
async def robots_txt():
    sitemap = f"\nSitemap: {PUBLIC_BASE_URL}/sitemap.xml" if PUBLIC_BASE_URL else ""
    return f"User-agent: *\nAllow: /\n{sitemap}\n"


@app.get("/sitemap.xml", response_class=PlainTextResponse)
async def sitemap_xml():
    if not PUBLIC_BASE_URL:
        return '<?xml version="1.0" encoding="UTF-8"?><urlset></urlset>'
    paths = ["", "/about", "/privacy", "/terms", "/guide", "/premium", "/login", "/signup"]
    urls = "".join(f"<url><loc>{PUBLIC_BASE_URL}{p}</loc></url>" for p in paths)
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        f"{urls}</urlset>"
    )


@app.post("/analyze")
async def analyze(request: Request, file: UploadFile = File(...)):
    auth = await asyncio.to_thread(get_auth_context, request)
    if not auth:
        return login_required_response()

    premium = await _premium_for_user(auth.user_id)
    if not premium and not usage_is_configured():
        return usage_database_error_response()

    ip = get_client_ip(request)
    lock = await get_analysis_lock(auth.user_id)

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
                return usage_database_error_response()

            if usage_before["remaining"] <= 0:
                response = JSONResponse(
                    status_code=429,
                    content={"detail": {"code": "USER_DAILY_LIMIT", "message": (
                        "오늘의 무료 분석 한도를 모두 사용했습니다. 계정 또는 동일 네트워크의 일일 한도에 도달했습니다."
                    ), "remaining": 0, "daily_limit": ACCOUNT_DAILY_LIMIT}},
                )
                return _attach_refreshed_session(response, auth)

        if not file.filename:
            raise HTTPException(status_code=400, detail="파일 이름이 없습니다.")

        suffix = Path(file.filename).suffix.lower()
        allowed = {".mp4", ".mov", ".webm", ".avi", ".mkv"}
        if suffix not in allowed:
            raise HTTPException(status_code=400, detail="지원하지 않는 영상 형식입니다.")

        temp_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
                temp_path = Path(tmp.name)
                shutil.copyfileobj(file.file, tmp)

            size_mb = temp_path.stat().st_size / 1024 / 1024
            if size_mb > MAX_UPLOAD_MB:
                raise HTTPException(status_code=413, detail=f"업로드 파일은 최대 {MAX_UPLOAD_MB}MB까지 가능합니다.")

            result = await asyncio.to_thread(analyze_video, temp_path)

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
                    response = JSONResponse(
                        status_code=429,
                        content={"detail": {"code": "USER_DAILY_LIMIT", "message": (
                            "오늘의 무료 분석 한도를 모두 사용했습니다. 계정 또는 동일 네트워크의 일일 한도에 도달했습니다."
                        ), "remaining": 0, "daily_limit": ACCOUNT_DAILY_LIMIT}},
                    )
                    return _attach_refreshed_session(response, auth)
                except Exception:
                    return usage_database_error_response()

            result["analysis_id"] = uuid.uuid4().hex
            result["usage"] = {
                "premium": premium,
                "unlimited": premium,
                "remaining": usage_after["remaining"],
                "daily_limit": None if premium else ACCOUNT_DAILY_LIMIT,
                "account_remaining": usage_after["account_remaining"],
                "ip_remaining": usage_after["ip_remaining"],
            }

            response = JSONResponse(status_code=200, content=result)
            return _attach_refreshed_session(response, auth)

        except HTTPException:
            raise
        except Exception as exc:
            status, code, message = classify_analysis_error(exc)
            response = JSONResponse(
                status_code=status,
                content={"detail": {
                    "code": code,
                    "message": message,
                    "premium": premium,
                    "remaining": usage_before["remaining"],
                    "daily_limit": None if premium else ACCOUNT_DAILY_LIMIT,
                }},
            )
            return _attach_refreshed_session(response, auth)
        finally:
            try:
                await file.close()
            except Exception:
                pass
            if temp_path and temp_path.exists():
                try:
                    temp_path.unlink()
                except Exception:
                    pass


@app.post("/feedback")
async def feedback(request: Request, payload: FeedbackRequest):
    auth = await asyncio.to_thread(get_auth_context, request)
    if not auth:
        return login_required_response()

    if payload.target_type == "overall":
        if payload.rating not in {"helpful", "partial", "not_helpful"}:
            raise HTTPException(status_code=400, detail="잘못된 전체 평가입니다.")
    else:
        if payload.rating not in {"up", "down"}:
            raise HTTPException(status_code=400, detail="잘못된 장면 평가입니다.")
        if payload.event_index is None:
            raise HTTPException(status_code=400, detail="event_index가 필요합니다.")

    data = payload.model_dump()
    data["user_id"] = auth.user_id
    feedback_id = save_feedback(data)
    response = JSONResponse({"ok": True, "feedback_id": feedback_id})
    return _attach_refreshed_session(response, auth)
