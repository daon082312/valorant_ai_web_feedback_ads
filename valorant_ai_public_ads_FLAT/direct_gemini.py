from __future__ import annotations

import os
import uuid
from pathlib import Path

import httpx
from fastapi import HTTPException
from google import genai
from google.genai import errors

from analyzer import _generate, _safe_failure_detail, _safe_feedback_calibration, _wait_for_file


GEMINI_UPLOAD_START_URL = "https://generativelanguage.googleapis.com/upload/v1beta/files"


def _api_key() -> str:
    value = os.getenv("GEMINI_API_KEY", "").strip()
    if not value:
        raise HTTPException(
            status_code=503,
            detail="Render에 GEMINI_API_KEY가 설정되어 있지 않습니다.",
        )
    return value


def start_direct_video_upload(filename: str, size_bytes: int, mime_type: str) -> dict:
    """Create a Gemini resumable upload session without proxying video bytes."""
    api_key = _api_key()
    clean_name = Path(str(filename or "valorant-clip")).name[:160] or "valorant-clip"
    size_bytes = int(size_bytes)
    mime_type = str(mime_type or "video/mp4").strip().lower()

    if size_bytes <= 0:
        raise ValueError("영상 크기가 올바르지 않습니다.")
    if not mime_type.startswith("video/"):
        raise ValueError("영상 MIME 형식이 올바르지 않습니다.")

    # Gemini allows callers to provide the immutable File resource ID on create.
    # Keeping it server-generated binds the upload ticket to exactly one file and
    # means the browser does not need to trust/forward a file ID from Google.
    file_name = f"files/vai-{uuid.uuid4().hex}"

    headers = {
        "x-goog-api-key": api_key,
        "X-Goog-Upload-Protocol": "resumable",
        "X-Goog-Upload-Command": "start",
        "X-Goog-Upload-Header-Content-Length": str(size_bytes),
        "X-Goog-Upload-Header-Content-Type": mime_type,
        "Content-Type": "application/json",
    }

    with httpx.Client(timeout=25.0, follow_redirects=True) as client:
        response = client.post(
            GEMINI_UPLOAD_START_URL,
            headers=headers,
            json={
                "file": {
                    "name": file_name,
                    "display_name": clean_name,
                }
            },
        )

    if response.status_code >= 400:
        detail = response.text.strip().replace("\n", " ")[:700]
        raise RuntimeError(
            f"GEMINI_UPLOAD_SESSION_FAILED HTTP {response.status_code}: {detail}"
        )

    upload_url = str(response.headers.get("x-goog-upload-url") or "").strip()
    if not upload_url.startswith("https://"):
        raise RuntimeError("GEMINI_UPLOAD_URL_MISSING")

    return {
        "upload_url": upload_url,
        "file_name": file_name,
        "mime_type": mime_type,
        "size_bytes": size_bytes,
    }


def analyze_direct_gemini_file(
    file_name: str,
    *,
    expected_size_bytes: int = 0,
    expected_mime_type: str = "",
    vision_hint: dict | None = None,
) -> dict:
    """Analyze a file already uploaded to Gemini directly by the user's browser."""
    api_key = _api_key()
    clean_name = str(file_name or "").strip()
    if (
        not clean_name.startswith("files/vai-")
        or len(clean_name) > 220
        or "?" in clean_name
        or "#" in clean_name
        or ".." in clean_name
    ):
        raise ValueError("Gemini 파일 ID가 올바르지 않습니다.")

    client = genai.Client(api_key=api_key)
    uploaded = None

    try:
        try:
            uploaded = client.files.get(name=clean_name)
        except errors.APIError as exc:
            text = str(exc)
            status_code, detail = _safe_failure_detail([text])
            raise HTTPException(status_code=status_code, detail=detail) from exc

        actual_name = str(getattr(uploaded, "name", "") or "").strip()
        if actual_name and actual_name != clean_name:
            raise ValueError("업로드된 Gemini 파일 ID 검증에 실패했습니다.")

        actual_mime = str(getattr(uploaded, "mime_type", "") or "").strip().lower()
        if actual_mime and not actual_mime.startswith("video/"):
            raise ValueError("업로드된 파일이 영상 형식이 아닙니다.")

        expected_mime = str(expected_mime_type or "").strip().lower()
        if expected_mime and not expected_mime.startswith("video/"):
            raise ValueError("요청된 영상 MIME 형식이 올바르지 않습니다.")

        try:
            actual_size = int(getattr(uploaded, "size_bytes", 0) or 0)
        except (TypeError, ValueError):
            actual_size = 0
        if expected_size_bytes and actual_size and actual_size != int(expected_size_bytes):
            raise ValueError("업로드된 영상 크기 검증에 실패했습니다.")

        uploaded = _wait_for_file(client, uploaded)
        calibration = _safe_feedback_calibration()
        result = _generate(client, uploaded, calibration, vision_hint)
        result["transport_mode"] = "browser_to_gemini_direct"
        return result

    finally:
        if uploaded is not None:
            try:
                client.files.delete(name=clean_name)
            except Exception:
                pass
