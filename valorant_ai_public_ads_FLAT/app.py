from __future__ import annotations

import asyncio
import os
import shutil
import tempfile
import uuid
from pathlib import Path

from fastapi import FastAPI, File, UploadFile, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from analyzer import analyze_video
from feedback_store import save_feedback

BASE_DIR = Path(__file__).resolve().parent
DAILY_LIMIT = int(os.getenv("DAILY_ANALYSIS_LIMIT", "3"))
MAX_UPLOAD_MB = int(os.getenv("MAX_UPLOAD_MB", "100"))
CONTACT_EMAIL = os.getenv("CONTACT_EMAIL", "contact@example.com")
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "").rstrip("/")

ADSENSE_CLIENT = os.getenv("ADSENSE_CLIENT", "").strip()
ADSENSE_TOP_SLOT = os.getenv("ADSENSE_TOP_SLOT", "").strip()
ADSENSE_RESULT_SLOT = os.getenv("ADSENSE_RESULT_SLOT", "").strip()

app = FastAPI(title="VALORANT AI Coach")
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

_ip_locks: dict[str, asyncio.Lock] = {}
_ip_locks_guard = asyncio.Lock()


class FeedbackRequest(BaseModel):
    analysis_id: str = Field(min_length=1, max_length=100)
    target_type: str = Field(pattern="^(overall|event)$")
    event_index: int | None = Field(default=None, ge=0)
    event_timestamp: str | None = Field(default=None, max_length=20)
    event_category: str | None = Field(default=None, max_length=50)
    rating: str = Field(min_length=1, max_length=30)
    categories: list[str] = Field(default_factory=list, max_length=10)
    comment: str = Field(default="", max_length=2000)


def common_context(request: Request) -> dict:
    return {
        "request": request,
        "contact_email": CONTACT_EMAIL,
        "adsense_client": ADSENSE_CLIENT,
        "adsense_top_slot": ADSENSE_TOP_SLOT,
        "adsense_result_slot": ADSENSE_RESULT_SLOT,
        "adsense_enabled": bool(ADSENSE_CLIENT),
    }


def get_client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


async def get_ip_lock(ip: str) -> asyncio.Lock:
    async with _ip_locks_guard:
        if ip not in _ip_locks:
            _ip_locks[ip] = asyncio.Lock()
        return _ip_locks[ip]


def classify_analysis_error(exc: Exception) -> tuple[int, str, str]:
    text = str(exc)

    if "429" in text or "RESOURCE_EXHAUSTED" in text or "quota" in text.lower():
        return (
            503,
            "AI_QUOTA_EXHAUSTED",
            "현재 AI API 사용 한도 또는 결제 잔액 문제로 분석할 수 없습니다. "
            "개인 무료 분석 횟수는 차감되지 않았습니다.",
        )

    if "503" in text or "UNAVAILABLE" in text or "high demand" in text.lower():
        return (
            503,
            "AI_TEMPORARILY_UNAVAILABLE",
            "현재 AI 서버가 혼잡합니다. 개인 무료 분석 횟수는 차감되지 않았습니다.",
        )

    return (
        500,
        "ANALYSIS_FAILED",
        "분석 중 오류가 발생했습니다. 개인 무료 분석 횟수는 차감되지 않았습니다.",
    )


@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context=common_context(request),
    )


@app.get("/about", response_class=HTMLResponse)
async def about(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="about.html",
        context=common_context(request),
    )


@app.get("/privacy", response_class=HTMLResponse)
async def privacy(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="privacy.html",
        context=common_context(request),
    )


@app.get("/terms", response_class=HTMLResponse)
async def terms(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="terms.html",
        context=common_context(request),
    )


@app.get("/guide", response_class=HTMLResponse)
async def guide(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="guide.html",
        context=common_context(request),
    )


@app.get("/usage")
async def usage(request: Request):
    used = _get_used_count(request)

    return {
        "remaining": max(
            DAILY_LIMIT - used,
            0,
        ),
        "daily_limit": DAILY_LIMIT,
    }


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

    paths = ["", "/about", "/privacy", "/terms", "/guide"]
    urls = "".join(f"<url><loc>{PUBLIC_BASE_URL}{p}</loc></url>" for p in paths)
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        f"{urls}</urlset>"
    )


@app.post("/analyze")
async def analyze(request: Request, file: UploadFile = File(...)):
    ip = get_client_ip(request)
    lock = await get_ip_lock(ip)

    async with lock:
        remaining_before = get_remaining(ip, DAILY_LIMIT)
        if remaining_before <= 0:
            raise HTTPException(
                status_code=429,
                detail={
                    "code": "USER_DAILY_LIMIT",
                    "message": f"오늘 무료 분석 횟수 {DAILY_LIMIT}회를 모두 사용했습니다.",
                    "remaining": 0,
                    "daily_limit": DAILY_LIMIT,
                },
            )

        if not file.filename:
            raise HTTPException(status_code=400, detail="파일 이름이 없습니다.")

        suffix = Path(file.filename).suffix.lower()
        allowed = {".mp4", ".mov", ".webm", ".avi", ".mkv"}
        if suffix not in allowed:
            raise HTTPException(status_code=400, detail="지원하지 않는 영상 형식입니다.")

        temp_path = None

        try:
            with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
                temp_path = Path(tmp.name)
                shutil.copyfileobj(file.file, tmp)

            size_mb = temp_path.stat().st_size / 1024 / 1024
            if size_mb > MAX_UPLOAD_MB:
                raise HTTPException(
                    status_code=413,
                    detail=f"업로드 파일은 최대 {MAX_UPLOAD_MB}MB까지 가능합니다.",
                )

            result = analyze_video(temp_path)

            used_today = record_success(ip)
            remaining_after = max(DAILY_LIMIT - used_today, 0)

            result["analysis_id"] = uuid.uuid4().hex
            result["usage"] = {
                "remaining": remaining_after,
                "daily_limit": DAILY_LIMIT,
            }
            return result

        except HTTPException:
            raise

        except Exception as exc:
            status, code, message = classify_analysis_error(exc)
            return JSONResponse(
                status_code=status,
                content={
                    "detail": {
                        "code": code,
                        "message": message,
                        "remaining": get_remaining(ip, DAILY_LIMIT),
                        "daily_limit": DAILY_LIMIT,USAGE_COOKIE_NAME = "valorant_ai_daily_usage"
LOCAL_TZ = ZoneInfo("Asia/Seoul")

USAGE_SIGNING_SECRET = (
    os.getenv("USAGE_SIGNING_SECRET")
    or os.getenv("GEMINI_API_KEY")
).encode("utf-8")


def _today():
    return datetime.now(LOCAL_TZ).date().isoformat()


def _sign(value):
    return hmac.new(
        USAGE_SIGNING_SECRET,
        value.encode(),
        hashlib.sha256,
    ).hexdigest()


def _make_usage_cookie(count):
    payload = json.dumps({
        "date": _today(),
        "count": count,
    })

    encoded = base64.urlsafe_b64encode(
        payload.encode()
    ).decode()

    return f"{encoded}.{_sign(encoded)}"


def _get_used_count(request: Request):
    token = request.cookies.get(
        USAGE_COOKIE_NAME
    )

    if not token:
        return 0

    try:
        encoded, signature = token.rsplit(".", 1)

        if not hmac.compare_digest(
            signature,
            _sign(encoded),
        ):
            return 0

        payload = json.loads(
            base64.urlsafe_b64decode(
                encoded.encode()
            ).decode()
        )

        if payload.get("date") != _today():
            return 0

        return int(payload.get("count", 0))

    except Exception:
        return 0
                    }
                },
            )

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
async def feedback(payload: FeedbackRequest):
    if payload.target_type == "overall":
        if payload.rating not in {"helpful", "partial", "not_helpful"}:
            raise HTTPException(status_code=400, detail="잘못된 전체 평가입니다.")
    else:
        if payload.rating not in {"up", "down"}:
            raise HTTPException(status_code=400, detail="잘못된 장면 평가입니다.")
        if payload.event_index is None:
            raise HTTPException(status_code=400, detail="event_index가 필요합니다.")

    feedback_id = save_feedback(payload.model_dump())
    return {"ok": True, "feedback_id": feedback_id}

    
    import base64
import hashlib
import hmac
import json
from datetime import datetime
from zoneinfo import ZoneInfo
