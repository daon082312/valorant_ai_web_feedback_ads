from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Literal

from dotenv import load_dotenv
from fastapi import HTTPException
from google import genai
from google.genai import errors, types
from pydantic import BaseModel, Field

from feedback_store import get_feedback_calibration
from valorant_reference import (
    abilities_for_agent,
    canonical_ability,
    canonical_agent,
    compact_kit_prompt,
)

load_dotenv()


class ScoreSet(BaseModel):
    aim: int = Field(ge=0, le=100)
    movement: int = Field(ge=0, le=100)
    positioning: int = Field(ge=0, le=100)
    utility: int = Field(ge=0, le=100)
    decision_making: int = Field(ge=0, le=100)


class TierPrediction(BaseModel):
    tier: Literal[
        "Iron", "Bronze", "Silver", "Gold", "Platinum",
        "Diamond", "Ascendant", "Immortal", "Radiant",
    ]
    confidence: float = Field(ge=0, le=1)
    reason: str = Field(description="티어 예측 근거를 한국어 1문장으로 간결하게 작성")


class AgentPrediction(BaseModel):
    agent: str = Field(
        description="HUD와 스킬 단서를 종합해 판단한 플레이어 에이전트 영문 이름. 확신할 수 없으면 Unknown"
    )
    confidence: float = Field(ge=0, le=1)
    reason: str = Field(description="에이전트 판별의 핵심 시각 근거를 한국어 한 문장으로 작성")


class Event(BaseModel):
    timestamp: str
    category: Literal[
        "aim", "movement", "positioning", "utility",
        "decision_making", "teamplay", "other",
    ]
    severity: Literal["positive", "low", "medium", "high"]
    observation: str = Field(description="화면에서 직접 관찰한 사실을 한국어 한 문장으로 작성")
    feedback: str = Field(description="개선 방법 또는 강점을 한국어 한 문장으로 작성")
    ability_name: str | None = Field(
        default=None,
        description="이 장면에서 플레이어가 명확히 사용한 스킬의 공식 영문 이름. 없거나 불확실하면 null",
    )
    ability_confidence: float = Field(default=0.0, ge=0, le=1)
    confidence: float = Field(ge=0, le=1)


class ValorantAnalysis(BaseModel):
    overall_score: int = Field(ge=0, le=100)
    summary: str = Field(description="강점, 약점, 핵심 개선 방향을 포함한 자연스러운 한국어 2~3문장 요약")
    agent_prediction: AgentPrediction
    tier_prediction: TierPrediction
    scores: ScoreSet
    events: list[Event]
    top_priorities: list[str] = Field(description="가장 먼저 개선할 점 최대 2개")
    limitations: list[str] = Field(description="분석 한계 최대 1개")


PROMPT = """
당신은 한국어로 답변하는 VALORANT 경기 후 코칭 전문가입니다. 첨부 클립을 시간 순서대로 분석하세요.

규칙:
- 화면에서 직접 확인 가능한 내용만 말하고 불확실하면 confidence를 낮춥니다.
- 가능한 경우 MM:SS timestamp를 사용합니다.
- Aim, Movement, Positioning, Utility, Decision Making, Teamplay를 평가합니다.
- reaction time(ms), DPI처럼 영상만으로 알 수 없는 수치는 추측하지 않습니다.
- 중요한 장면은 최대 4개만 고릅니다. 중복보다 서로 다른 강점·실수를 우선합니다.
- summary는 2~3문장, observation과 feedback은 각각 1문장으로 짧게 작성합니다.
- top_priorities는 최대 2개, limitations는 최대 1개입니다.
- 근거가 있으면 좋은 플레이도 최소 1개 포함합니다.

에이전트/스킬:
- 하단 HUD 스킬 아이콘, 손/장비, 화면 효과를 함께 보고 판단합니다.
- 불명확하면 agent=Unknown으로 둡니다.
- 에이전트를 정한 뒤 그 요원의 실제 스킬만 ability_name 후보로 사용합니다.
- 실제 스킬 사용이 명확할 때만 ability_name을 쓰고, 아니면 null로 둡니다.

티어 예측:
- 실제 랭크/MMR이 아니라 이 클립에서 관찰되는 플레이 수준의 추정치입니다.
- Iron, Bronze, Silver, Gold, Platinum, Diamond, Ascendant, Immortal, Radiant 중 하나를 선택합니다.
- Aim뿐 아니라 Movement, Positioning, Utility, Decision Making을 함께 봅니다.
- 짧거나 근거가 부족하면 confidence를 낮춥니다.
"""


DEFAULT_PRIMARY_MODEL = "gemini-3.1-flash-lite"
QUALITY_FALLBACK_MODEL = "gemini-3.5-flash-lite"


def _truthy_env(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _float_env(name: str, default: float, minimum: float, maximum: float) -> float:
    try:
        value = float(os.getenv(name, str(default)).strip())
    except Exception:
        value = default
    return max(minimum, min(maximum, value))


ANALYSIS_VIDEO_FPS = _float_env("ANALYSIS_VIDEO_FPS", 0.5, 0.25, 1.0)
ANALYSIS_MAX_OUTPUT_TOKENS = int(os.getenv("ANALYSIS_MAX_OUTPUT_TOKENS", "1500"))


def _normalize_model(model: str) -> str:
    value = str(model or "").strip().removeprefix("models/")
    if value == "gemini-2.5-flash-lite":
        return DEFAULT_PRIMARY_MODEL
    return value


def _models() -> list[str]:
    prefer_low_cost = _truthy_env("PREFER_LOW_COST_MODEL", True)
    requested = _normalize_model(os.getenv("GEMINI_PRIMARY_MODEL", ""))

    if prefer_low_cost:
        candidates = [DEFAULT_PRIMARY_MODEL]
    else:
        candidates = [requested or DEFAULT_PRIMARY_MODEL, DEFAULT_PRIMARY_MODEL]

    if _truthy_env("ALLOW_QUALITY_FALLBACK", False):
        candidates.append(QUALITY_FALLBACK_MODEL)

    if _truthy_env("ALLOW_EXPENSIVE_MODEL_FALLBACK", False):
        for raw in os.getenv("GEMINI_MODELS", "").split(","):
            model = _normalize_model(raw)
            if model:
                candidates.append(model)

    result: list[str] = []
    for model in candidates:
        model = _normalize_model(model)
        if model and model not in result:
            result.append(model)
    return result


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
    try:
        value = get_feedback_calibration()
        return value if isinstance(value, dict) else _empty_calibration()
    except Exception as exc:
        print(f"[Feedback] calibration skipped: {type(exc).__name__}: {exc}")
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


def _build_prompt(calibration: dict, vision_hint: dict | None = None) -> str:
    parts = [PROMPT]

    kit_prompt = compact_kit_prompt(max_chars=2200)
    if kit_prompt:
        parts.append(kit_prompt)

    calibration_prompt = str(calibration.get("prompt") or "").strip()
    if calibration_prompt:
        parts.append(calibration_prompt[:500])

    if vision_hint and vision_hint.get("label"):
        confidence = float(vision_hint.get("confidence") or 0)
        sample_count = int(vision_hint.get("sample_count") or 0)
        if confidence >= 0.50 and sample_count >= 2:
            parts.append(
                f"보조 분류기 힌트: 에이전트 {vision_hint['label']} "
                f"(신뢰도 {confidence:.2f}). 영상과 일치할 때만 사용하세요."
            )

    return "\n\n".join(parts)


def _safe_failure_detail(errors_seen: list[str]) -> tuple[int, str]:
    text = "\n".join(errors_seen)
    lower = text.lower()

    if "429" in text or "resource_exhausted" in lower or "quota" in lower:
        return 503, "Gemini API 사용 한도 또는 결제 잔액 문제입니다. 무료 분석 횟수는 차감되지 않았습니다."
    if "403" in text or "permission_denied" in lower:
        return 503, "Gemini API 키 또는 프로젝트 권한 문제입니다. Render의 GEMINI_API_KEY 설정을 확인해 주세요."
    if "404" in text or "not_found" in lower or "not found" in lower:
        return 503, "현재 설정된 Gemini 모델을 사용할 수 없습니다."
    if "400" in text or "invalid_argument" in lower:
        return 503, "Gemini가 영상 분석 요청 형식을 거부했습니다. Render 로그의 '[Gemini]' 줄을 확인해 주세요."
    if "validation" in lower or "json" in lower or "빈 응답" in text:
        return 503, "Gemini 응답 형식 검증에 실패했습니다."
    if "503" in text or "unavailable" in lower:
        return 503, "Gemini 서버가 일시적으로 사용할 수 없습니다. 무료 분석 횟수는 차감되지 않았습니다."
    return 503, "Gemini 영상 분석 단계에서 오류가 발생했습니다."


def _usage_metadata(response) -> dict:
    usage = getattr(response, "usage_metadata", None)
    if usage is None:
        return {}
    return {
        "prompt_tokens": int(getattr(usage, "prompt_token_count", 0) or 0),
        "output_tokens": int(getattr(usage, "candidates_token_count", 0) or 0),
        "thought_tokens": int(getattr(usage, "thoughts_token_count", 0) or 0),
        "total_tokens": int(getattr(usage, "total_token_count", 0) or 0),
    }


def _sanitize_agent_and_abilities(result: dict) -> None:
    agent_prediction = result.get("agent_prediction") or {}
    if not isinstance(agent_prediction, dict):
        return

    raw_agent = str(agent_prediction.get("agent") or "")
    agent = canonical_agent(raw_agent)
    if not agent:
        return

    agent_prediction["agent"] = agent
    allowed = abilities_for_agent(agent)
    if not allowed:
        return

    removed = 0
    for event in result.get("events") or []:
        if not isinstance(event, dict):
            continue
        raw_ability = str(event.get("ability_name") or "").strip()
        if not raw_ability:
            continue
        ability = canonical_ability(agent, raw_ability)
        if ability:
            event["ability_name"] = ability
        else:
            event["ability_name"] = None
            event["ability_confidence"] = 0.0
            event["ability_validation_removed"] = raw_ability
            removed += 1

    result["ability_kit_validation"] = {
        "agent": agent,
        "allowed_abilities": allowed,
        "removed_invalid_count": removed,
    }


def _video_part(uploaded) -> types.Part:
    return types.Part(
        file_data=types.FileData(
            file_uri=str(uploaded.uri),
            mime_type=str(getattr(uploaded, "mime_type", "") or "video/mp4"),
        ),
        video_metadata=types.VideoMetadata(fps=ANALYSIS_VIDEO_FPS),
    )


def _generate(client: genai.Client, uploaded, calibration: dict, vision_hint: dict | None = None) -> dict:
    errors_seen: list[str] = []
    prompt = _build_prompt(calibration, vision_hint)
    video_part = _video_part(uploaded)

    for model in _models():
        print(
            f"[Gemini] {model} · economy mode · video_fps={ANALYSIS_VIDEO_FPS:g} · "
            f"low media resolution · minimal thinking"
        )
        try:
            response = client.models.generate_content(
                model=model,
                contents=[video_part, prompt],
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=ValorantAnalysis,
                    temperature=0.10,
                    max_output_tokens=max(900, min(1800, ANALYSIS_MAX_OUTPUT_TOKENS)),
                    media_resolution=types.MediaResolution.MEDIA_RESOLUTION_LOW,
                    thinking_config=types.ThinkingConfig(thinking_level="minimal"),
                    automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
                ),
            )

            if not response.text:
                raise RuntimeError(f"{model} 빈 응답")

            result = ValorantAnalysis.model_validate_json(response.text).model_dump()
            result["events"] = list(result.get("events") or [])[:4]
            result["top_priorities"] = list(result.get("top_priorities") or [])[:2]
            result["limitations"] = list(result.get("limitations") or [])[:1]
            _sanitize_agent_and_abilities(result)

            result["model_used"] = model
            result["analysis_fps"] = ANALYSIS_VIDEO_FPS
            result["media_resolution"] = "low"
            result["thinking_level"] = "minimal"
            result["economy_mode"] = True
            result["vision_learning_hint_used"] = bool(
                vision_hint
                and vision_hint.get("label")
                and float(vision_hint.get("confidence") or 0) >= 0.50
                and int(vision_hint.get("sample_count") or 0) >= 2
            )
            result["vision_learning_hint"] = vision_hint or {}
            result["feedback_calibration_used"] = bool(calibration.get("prompt"))
            result["feedback_calibration_samples"] = int(calibration.get("sample_size") or 0)
            result["feedback_calibration_source"] = str(calibration.get("source") or "none")
            result["token_usage"] = _usage_metadata(response)

            usage = result["token_usage"]
            if usage:
                print(
                    "[Gemini] tokens · "
                    f"input={usage.get('prompt_tokens', 0)} · "
                    f"output={usage.get('output_tokens', 0)} · "
                    f"thinking={usage.get('thought_tokens', 0)} · "
                    f"total={usage.get('total_tokens', 0)}"
                )
            return result

        except errors.APIError as exc:
            text = str(exc)
            errors_seen.append(f"{model}: {text}")
            print(f"[Gemini] API 오류: {text}")
            if ("429" in text or "RESOURCE_EXHAUSTED" in text) and _daily_quota(text):
                continue
        except (ValueError, RuntimeError) as exc:
            errors_seen.append(f"{model}: {type(exc).__name__}: {exc}")
            print(f"[Gemini] 응답 검증 오류: {type(exc).__name__}: {exc}")

    status_code, detail = _safe_failure_detail(errors_seen)
    raise HTTPException(status_code=status_code, detail=detail)


def analyze_video(video_path: Path, vision_hint: dict | None = None) -> dict:
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise HTTPException(status_code=503, detail="Render에 GEMINI_API_KEY가 설정되어 있지 않습니다.")

    client = genai.Client(api_key=api_key)
    uploaded = None

    try:
        calibration = _safe_feedback_calibration()
        try:
            uploaded = client.files.upload(file=video_path)
            uploaded = _wait_for_file(client, uploaded)
        except HTTPException:
            raise
        except errors.APIError as exc:
            text = str(exc)
            print(f"[Gemini] 파일 업로드 API 오류: {text}")
            status_code, detail = _safe_failure_detail([text])
            raise HTTPException(status_code=status_code, detail=detail) from exc
        except Exception as exc:
            print(f"[Gemini] 파일 처리 오류: {type(exc).__name__}: {exc}")
            raise HTTPException(
                status_code=503,
                detail="Gemini에 영상을 업로드하거나 처리하는 단계에서 오류가 발생했습니다.",
            ) from exc

        return _generate(client, uploaded, calibration, vision_hint)

    finally:
        if uploaded is not None:
            try:
                client.files.delete(name=uploaded.name)
            except Exception:
                pass
