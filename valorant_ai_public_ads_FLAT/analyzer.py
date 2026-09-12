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
        description="반드시 자연스러운 한국어로만 작성하는 짧은 티어 예측 근거. 게임 고유명사만 영문 허용"
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
        description="영상에서 직접 관찰한 사실을 자연스러운 한국어 한 문장으로 작성"
    )
    feedback: str = Field(
        description="해당 장면에 대한 코칭을 자연스러운 한국어 한 문장으로 작성"
    )
    confidence: float = Field(ge=0, le=1)


class ValorantAnalysis(BaseModel):
    overall_score: int = Field(ge=0, le=100)
    summary: str = Field(
        description="전체 분석 요약을 자연스러운 한국어 최대 두 문장으로 작성"
    )
    tier_prediction: TierPrediction
    scores: ScoreSet
    events: list[Event]
    top_priorities: list[str] = Field(
        description="가장 먼저 개선할 점 최대 2개를 각각 짧은 한국어 문장으로 작성",
    )
    limitations: list[str] = Field(
        description="분석 한계 최대 2개를 각각 짧은 한국어 문장으로 작성",
    )


PROMPT = """
당신은 한국어로 답변하는 VALORANT 경기 후 코칭 전문가입니다. 첨부된 클립 전체를 시간 순서대로 분석하세요.

출력 언어 규칙:
- 사용자가 읽는 모든 자연어 문장은 반드시 한국어로 작성합니다.
- summary, tier_prediction.reason, events[].observation, events[].feedback,
  top_priorities, limitations를 모두 한국어로 작성합니다.
- JSON 키와 enum 값만 스키마에 정의된 영문 값을 사용합니다.

분석 규칙:
- 영상에서 직접 확인 가능한 내용만 말하고 보이지 않는 정보는 추측하지 않습니다.
- 불확실하면 confidence를 낮춥니다.
- 가능한 경우 MM:SS timestamp를 사용합니다.
- Aim, Movement, Positioning, Utility, Decision Making, Teamplay를 평가합니다.
- 영상으로 정확히 측정할 수 없는 reaction time(ms), DPI, frame-perfect timing을 주장하지 않습니다.
- overall_score와 영역별 점수는 0~100입니다.
- 중요한 장면만 최대 3개 반환하고 비슷한 장면은 합칩니다.
- top_priorities는 최대 2개, limitations는 최대 2개입니다.
- summary는 최대 2문장, observation과 feedback은 각각 한 문장으로 짧고 구체적으로 작성합니다.
- 한 번의 킬이나 실수만으로 습관을 단정하지 않습니다.
- 킬 성공 여부보다 크로스헤어 위치, 노출 각도, 커버, 이동, 유틸리티, 교전 선택을 우선 평가합니다.

티어 예측:
- 실제 랭크/MMR이 아니라 이 클립에서 관찰되는 플레이 수준의 추정치입니다.
- 가능한 티어: Iron, Bronze, Silver, Gold, Platinum, Diamond, Ascendant, Immortal, Radiant.
- Aim만 보지 말고 Movement, Positioning, Utility, Decision Making과 일관성을 함께 봅니다.
- 짧거나 근거가 부족한 클립은 confidence를 낮게 주며, 근거가 매우 적으면 0.45 이하로 둡니다.
- reason은 한국어 한 문장으로 핵심 근거만 작성합니다.
- 실제 랭크와 다를 수 있음을 limitations에 포함합니다.
"""


DEFAULT_PRIMARY_MODEL = "gemini-2.5-flash-lite"


def _truthy_env(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _models() -> list[str]:
    primary = os.getenv("GEMINI_PRIMARY_MODEL", DEFAULT_PRIMARY_MODEL).strip()
    if not primary:
        primary = DEFAULT_PRIMARY_MODEL

    candidates = [primary]

    if _truthy_env("ALLOW_EXPENSIVE_MODEL_FALLBACK", False):
        env_models = os.getenv("GEMINI_MODELS", "").strip()
        if env_models:
            candidates.extend(
                x.strip()
                for x in env_models.split(",")
                if x.strip()
            )

    seen = set()
    ordered = []
    for model in candidates:
        if model and model not in seen:
            seen.add(model)
            ordered.append(model)
    return ordered


def _empty_calibration() -> dict:
    return {
        "sample_size": 0,
        "source": "disabled_after_error",
        "overall_count": 0,
        "overall": {},
        "categories": {},
        "prompt": "",
    }


def _safe_feedback_calibration() -> dict:
    """Feedback improves later analyses, but must never break an analysis."""
    try:
        value = get_feedback_calibration()
        return value if isinstance(value, dict) else _empty_calibration()
    except Exception as exc:
        print(f"[Feedback] calibration skipped because of error: {type(exc).__name__}: {exc}")
        return _empty_calibration()


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

    return f"{PROMPT}\n\n{calibration_prompt[:2200]}"


def _generate(client: genai.Client, uploaded, calibration: dict) -> dict:
    errors_seen: list[str] = []
    prompt = _build_prompt(calibration)

    for model in _models():
        print(
            f"[Gemini] {model} 시도 · economy · File API default video processing · "
            f"feedback n={calibration.get('sample_size', 0)}"
        )

        for attempt in range(1, 3):
            try:
                response = client.models.generate_content(
                    model=model,
                    contents=[uploaded, prompt],
                    config=types.GenerateContentConfig(
                        response_mime_type="application/json",
                        response_schema=ValorantAnalysis,
                        temperature=0.15,
                        max_output_tokens=1800,
                    ),
                )

                if not response.text:
                    finish_reason = ""
                    try:
                        finish_reason = str(response.candidates[0].finish_reason)
                    except Exception:
                        pass
                    raise RuntimeError(
                        f"{model} 빈 응답"
                        + (f" (finish_reason={finish_reason})" if finish_reason else "")
                    )

                result = (
                    ValorantAnalysis
                    .model_validate_json(response.text)
                    .model_dump()
                )

                result["events"] = list(result.get("events") or [])[:3]
                result["top_priorities"] = list(result.get("top_priorities") or [])[:2]
                result["limitations"] = list(result.get("limitations") or [])[:2]

                result["model_used"] = model
                result["analysis_fps"] = 1
                result["media_resolution"] = "default"
                result["economy_mode"] = True
                result["feedback_calibration_used"] = bool(calibration.get("prompt"))
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
                print(f"[Gemini] API 오류: {text}")

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

                break

            except (ValueError, RuntimeError) as exc:
                errors_seen.append(f"{model}: {type(exc).__name__}: {exc}")
                print(f"[Gemini] 응답 검증 오류: {type(exc).__name__}: {exc}")
                break

    details = "\n\n".join(errors_seen[-2:])
    raise RuntimeError(
        "저비용 Gemini 분석 모델을 현재 사용할 수 없습니다. 잠시 후 다시 시도해 주세요.\n\n"
        f"{details}"
    )


def analyze_video(video_path: Path) -> dict:
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY가 없습니다.")

    client = genai.Client(api_key=api_key)
    uploaded = None

    try:
        calibration = _safe_feedback_calibration()
        uploaded = client.files.upload(file=video_path)
        uploaded = _wait_for_file(client, uploaded)
        return _generate(client, uploaded, calibration)
    finally:
        if uploaded is not None:
            try:
                client.files.delete(name=uploaded.name)
            except Exception:
                pass
