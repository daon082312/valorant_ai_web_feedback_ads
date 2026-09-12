from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Literal

from dotenv import load_dotenv
from google import genai
from google.genai import errors, types
from pydantic import BaseModel, Field

from feedback_store import get_feedback_calibration

load_dotenv()


class ScoreSet(BaseModel):
    aim: int = Field(ge=0, le=100)
    movement: int = Field(ge=0, le=100)
    positioning: int = Field(ge=0, le=100)
    utility: int = Field(ge=0, le=100)
    decision_making: int = Field(ge=0, le=100)


class TierPrediction(BaseModel):
    tier: Literal[
        "Iron",
        "Bronze",
        "Silver",
        "Gold",
        "Platinum",
        "Diamond",
        "Ascendant",
        "Immortal",
        "Radiant",
    ]
    confidence: float = Field(ge=0, le=1)
    reason: str = Field(
        description="반드시 자연스러운 한국어로만 작성하는 티어 예측 근거. 게임 고유명사만 영문 허용"
    )


class Event(BaseModel):
    timestamp: str
    category: Literal[
        "aim",
        "movement",
        "positioning",
        "utility",
        "decision_making",
        "teamplay",
        "other",
    ]
    severity: Literal["positive", "low", "medium", "high"]
    observation: str = Field(
        description="영상에서 직접 관찰한 사실을 반드시 자연스러운 한국어 문장으로 작성"
    )
    feedback: str = Field(
        description="해당 장면에 대한 코칭을 반드시 자연스러운 한국어 문장으로 작성"
    )
    confidence: float = Field(ge=0, le=1)


class ValorantAnalysis(BaseModel):
    overall_score: int = Field(ge=0, le=100)
    summary: str = Field(
        description="전체 분석 요약을 반드시 자연스러운 한국어로 작성"
    )
    tier_prediction: TierPrediction
    scores: ScoreSet
    events: list[Event]
    top_priorities: list[str] = Field(
        description="가장 먼저 개선할 점을 각각 자연스러운 한국어 문장으로 작성"
    )
    limitations: list[str] = Field(
        description="분석 한계를 각각 자연스러운 한국어 문장으로 작성"
    )


PROMPT = """
당신은 한국어로 답변하는 VALORANT 경기 후 코칭 전문가입니다. 첨부된 클립 전체를 시간 순서대로 분석하세요.

출력 언어 규칙 — 반드시 지키세요:
- 사용자가 보는 모든 자연어 문장은 반드시 한국어로 작성합니다.
- summary, tier_prediction.reason, events[].observation, events[].feedback,
  top_priorities의 모든 항목, limitations의 모든 항목을 반드시 한국어로 작성합니다.
- 영어 문장이나 영어 단락을 작성하지 마세요.
- VALORANT, Aim, Movement, Positioning, Utility, Agent 이름, 무기 이름처럼
  게임에서 통용되는 고유명사/용어만 필요한 경우 영문 표기를 허용합니다.
- 영어 용어를 사용하더라도 설명 문장은 한국어 문장이어야 합니다.
- JSON 키, enum 값(category, severity, tier)은 스키마에 정의된 영문 값을 그대로 사용하되,
  사용자가 읽는 설명 텍스트는 모두 한국어여야 합니다.

분석 규칙:
- 영상에서 직접 확인 가능한 내용만 말합니다. 보이지 않는 적, 스킬, 팀원의 의도를 추측하지 않습니다.
- 관찰과 코칭을 분리하고, 불확실하면 confidence를 낮춥니다.
- 가능한 경우 MM:SS timestamp를 사용합니다.
- Aim, Movement, Positioning, Utility, Decision Making, Teamplay를 평가합니다.
- reaction time(ms), DPI, frame-perfect counter-strafe timing처럼 영상으로 정확히 측정할 수 없는 수치를 주장하지 않습니다.
- overall_score와 영역별 점수는 0~100입니다.
- top_priorities는 최대 3개입니다.
- events는 중요한 장면만 최대 5개 반환합니다. 비슷한 장면은 합칩니다.
- limitations는 핵심 항목 최대 3개입니다.
- summary는 최대 3문장, event observation/feedback은 각각 1~2문장으로 간결하게 작성합니다.
- 한 번의 킬이나 실수만으로 습관을 단정하지 않습니다.
- 단순 킬 성공 여부보다 크로스헤어 위치, 노출 각도, 커버, 이동, 유틸리티, 교전 선택을 우선 평가합니다.

티어 예측:
- 실제 랭크/MMR이 아니라 이 클립에서 관찰되는 플레이 수준의 추정치입니다.
- 가능한 티어: Iron, Bronze, Silver, Gold, Platinum, Diamond, Ascendant, Immortal, Radiant.
- Aim만 보지 말고 Movement, Positioning, Utility, Decision Making과 일관성을 함께 봅니다.
- 짧거나 근거가 부족한 클립은 tier_prediction.confidence를 낮게 주며, 근거가 매우 적으면 0.45 이하로 둡니다.
- reason은 핵심 근거만 한국어 1~2문장으로 작성합니다.
- 실제 랭크와 다를 수 있음을 limitations에 한국어로 포함합니다.

최종 확인:
JSON을 반환하기 직전에 사용자가 읽는 모든 텍스트 필드를 확인하세요.
영어 문장이 있으면 의미를 유지한 채 자연스러운 한국어로 바꾼 뒤 반환하세요.
"""


# Cheapest supported video model first. More capable models are fallback only.
DEFAULT_MODELS = [
    "gemini-2.5-flash-lite",
    "gemini-3.1-flash-lite",
    "gemini-3.5-flash-lite",
    "gemini-3.6-flash",
]


def _models() -> list[str]:
    primary = os.getenv("GEMINI_PRIMARY_MODEL", "gemini-2.5-flash-lite").strip()
    env_models = os.getenv("GEMINI_MODELS", "").strip()

    candidates: list[str] = []
    if primary:
        candidates.append(primary)

    if env_models:
        candidates.extend(
            x.strip()
            for x in env_models.split(",")
            if x.strip()
        )
    else:
        candidates.extend(DEFAULT_MODELS)

    seen = set()
    ordered = []
    for model in candidates:
        if model and model not in seen:
            seen.add(model)
            ordered.append(model)
    return ordered


def _video_fps() -> float:
    # 1.5 FPS costs much less than 4 FPS while retaining more temporal detail
    # than Gemini's 1 FPS default. Override with VIDEO_ANALYSIS_FPS if needed.
    raw = os.getenv("VIDEO_ANALYSIS_FPS", "1.5").strip()
    try:
        value = float(raw)
    except ValueError:
        value = 1.5
    return min(24.0, max(0.1, value))


def _wait_for_file(client: genai.Client, uploaded):
    for _ in range(120):
        current = client.files.get(name=uploaded.name)
        state = getattr(getattr(current, "state", None), "name", None)

        if state == "ACTIVE":
            return current
        if state == "FAILED":
            raise RuntimeError("Gemini 영상 처리에 실패했습니다.")
        time.sleep(2)

    raise RuntimeError("Gemini 영상 처리 시간이 너무 오래 걸렸습니다.")


def _daily_quota(text: str) -> bool:
    lower = text.lower()
    return (
        "generaterequestsperdayperprojectpermodel" in lower
        or "requestsperday" in lower
        or "requests per day" in lower
    )


def _build_prompt(calibration: dict) -> str:
    calibration_prompt = str(calibration.get("prompt") or "").strip()
    if not calibration_prompt:
        return PROMPT

    # Only aggregate statistics are appended. Raw user comments never become
    # model instructions, which prevents feedback prompt injection.
    return f"{PROMPT}\n\n{calibration_prompt[:3500]}"


def _video_part(uploaded, fps: float) -> types.Part:
    uri = getattr(uploaded, "uri", None)
    mime_type = getattr(uploaded, "mime_type", None) or "video/mp4"
    if not uri:
        raise RuntimeError("Gemini 업로드 영상 URI를 찾을 수 없습니다.")

    return types.Part(
        file_data=types.FileData(file_uri=uri, mime_type=mime_type),
        video_metadata=types.VideoMetadata(fps=fps),
    )


def _generate(client: genai.Client, uploaded, calibration: dict) -> dict:
    errors_seen: list[str] = []
    fps = _video_fps()
    prompt = _build_prompt(calibration)
    video_part = _video_part(uploaded, fps)

    for model in _models():
        print(
            f"[Gemini] {model} 시도 · video={fps:g} FPS · low-res · "
            f"feedback n={calibration.get('sample_size', 0)}"
        )

        for attempt in range(1, 3):
            try:
                response = client.models.generate_content(
                    model=model,
                    contents=types.Content(
                        parts=[video_part, types.Part(text=prompt)]
                    ),
                    config=types.GenerateContentConfig(
                        response_mime_type="application/json",
                        response_schema=ValorantAnalysis,
                        temperature=0.15,
                        media_resolution=(
                            types.MediaResolution.MEDIA_RESOLUTION_LOW
                        ),
                        max_output_tokens=1400,
                    ),
                )

                if not response.text:
                    raise RuntimeError(f"{model} 빈 응답")

                result = (
                    ValorantAnalysis
                    .model_validate_json(response.text)
                    .model_dump()
                )

                result["model_used"] = model
                result["analysis_fps"] = fps
                result["media_resolution"] = "low"
                result["feedback_calibration_used"] = bool(
                    calibration.get("prompt")
                )
                result["feedback_calibration_samples"] = int(
                    calibration.get("sample_size") or 0
                )
                result["feedback_calibration_source"] = str(
                    calibration.get("source") or "none"
                )
                return result

            except errors.APIError as exc:
                text = str(exc)
                errors_seen.append(f"{model}: {text}")

                if (
                    ("429" in text or "RESOURCE_EXHAUSTED" in text)
                    and _daily_quota(text)
                ):
                    break

                if "429" in text or "RESOURCE_EXHAUSTED" in text:
                    if attempt < 2:
                        time.sleep(8)
                        continue
                    break

                if "503" in text or "UNAVAILABLE" in text:
                    if attempt < 2:
                        time.sleep(4)
                        continue
                    break

                if (
                    "404" in text
                    or "NOT_FOUND" in text
                    or "not found" in text.lower()
                ):
                    break

                raise

            except (ValueError, RuntimeError) as exc:
                # Invalid/empty structured output can fall back to the next model.
                errors_seen.append(f"{model}: {exc}")
                break

    details = "\n\n".join(errors_seen[-4:])
    raise RuntimeError(
        "Gemini 분석 모델을 사용할 수 없거나 현재 서버가 혼잡합니다.\n\n"
        f"{details}"
    )


def analyze_video(video_path: Path) -> dict:
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY가 없습니다.")

    client = genai.Client(api_key=api_key)
    uploaded = None

    try:
        calibration = get_feedback_calibration()
        uploaded = client.files.upload(file=video_path)
        uploaded = _wait_for_file(client, uploaded)
        return _generate(client, uploaded, calibration)
    finally:
        if uploaded is not None:
            try:
                client.files.delete(name=uploaded.name)
            except Exception:
                pass
