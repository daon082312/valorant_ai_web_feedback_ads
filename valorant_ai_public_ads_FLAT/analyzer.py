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
    reason: str = Field(description="티어 예측 근거를 자연스러운 한국어 1~2문장으로 간결하게 작성")


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
    feedback: str = Field(description="문제 또는 강점과 개선 방법을 한국어 1~2문장으로 작성")
    ability_name: str | None = Field(
        default=None,
        description="이 장면에서 플레이어가 명확히 사용한 스킬의 공식 영문 이름. 없거나 불확실하면 null",
    )
    ability_confidence: float = Field(default=0.0, ge=0, le=1)
    confidence: float = Field(ge=0, le=1)


class ValorantAnalysis(BaseModel):
    overall_score: int = Field(ge=0, le=100)
    summary: str = Field(description="강점, 약점, 핵심 개선 방향을 포함한 자연스러운 한국어 3~4문장 요약")
    agent_prediction: AgentPrediction
    tier_prediction: TierPrediction
    scores: ScoreSet
    events: list[Event]
    top_priorities: list[str] = Field(description="가장 먼저 개선할 점 최대 3개를 간결한 한국어 문장으로 작성")
    limitations: list[str] = Field(description="분석 한계 최대 2개를 한국어로 작성")


PROMPT = """
당신은 한국어로 답변하는 VALORANT 경기 후 코칭 전문가입니다. 첨부된 클립 전체를 시간 순서대로 분석하세요.

핵심 규칙:
- 사용자가 읽는 모든 자연어는 한국어로 작성합니다. JSON 키와 enum 값은 스키마를 따릅니다.
- 영상에서 직접 확인 가능한 내용만 말하고, 불확실하면 confidence를 낮춥니다.
- 가능한 경우 MM:SS timestamp를 사용합니다.
- Aim, Movement, Positioning, Utility, Decision Making, Teamplay를 평가합니다.
- reaction time(ms), DPI 등 영상만으로 정확히 알 수 없는 수치를 추측하지 않습니다.
- 중요한 장면은 최대 6개까지 고릅니다. 중복 장면보다 서로 다른 강점·실수·판단을 우선합니다.
- summary는 3~4문장, event observation은 1문장, feedback은 1~2문장으로 간결하게 작성합니다.
- top_priorities는 최대 3개, limitations는 최대 2개입니다.
- 좋은 플레이도 근거가 있으면 최소 1개 포함합니다.

에이전트/스킬 판별:
- HUD 하단 스킬 아이콘, 손/장비, 스킬 효과를 함께 보고 플레이어 에이전트를 판단합니다.
- 한 가지 단서만으로 확정하지 않습니다. 불명확하면 agent를 Unknown으로 둡니다.
- 에이전트를 판단한 뒤에는 그 에이전트가 실제로 보유한 스킬만 후보로 사용합니다.
- 각 주요 장면에서 스킬 사용이 명확할 때만 ability_name을 작성합니다. 총격·이동·무기 교체를 스킬로 오인하지 않습니다.
- 스킬이 불명확하면 ability_name은 null, ability_confidence는 낮게 둡니다.

티어 예측:
- 실제 랭크/MMR이 아니라 이 클립에서 관찰되는 플레이 수준의 추정치입니다.
- 가능한 티어: Iron, Bronze, Silver, Gold, Platinum, Diamond, Ascendant, Immortal, Radiant.
- Aim뿐 아니라 Movement, Positioning, Utility, Decision Making을 함께 봅니다.
- 짧거나 근거가 부족한 클립은 confidence를 낮게 둡니다.
"""


# Economy defaults. 3.1 Flash-Lite is cheaper than 3.5 Flash-Lite.
DEFAULT_PRIMARY_MODEL = "gemini-3.1-flash-lite"
QUALITY_FALLBACK_MODEL = "gemini-3.5-flash-lite"
DEPRECATED_MODELS = {
    "gemini-2.5-flash-lite",
    "models/gemini-2.5-flash-lite",
}


def _truthy_env(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _normalize_model(model: str) -> str:
    value = str(model or "").strip()
    if value.startswith("models/"):
        value = value.removeprefix("models/")
    if value in {"gemini-2.5-flash-lite"}:
        print(
            f"[Gemini] deprecated model '{value}' ignored; "
            f"using {DEFAULT_PRIMARY_MODEL} instead"
        )
        return DEFAULT_PRIMARY_MODEL
    return value


def _models() -> list[str]:
    # Cost-first is ON by default. This prevents an old Render environment
    # variable such as GEMINI_PRIMARY_MODEL=gemini-3.5-flash-lite from silently
    # making every analysis more expensive.
    prefer_low_cost = _truthy_env("PREFER_LOW_COST_MODEL", True)
    requested = _normalize_model(os.getenv("GEMINI_PRIMARY_MODEL", ""))

    if prefer_low_cost:
        candidates = [DEFAULT_PRIMARY_MODEL, QUALITY_FALLBACK_MODEL]
    else:
        candidates = [requested or DEFAULT_PRIMARY_MODEL, DEFAULT_PRIMARY_MODEL, QUALITY_FALLBACK_MODEL]

    if _truthy_env("ALLOW_EXPENSIVE_MODEL_FALLBACK", False):
        env_models = os.getenv("GEMINI_MODELS", "").strip()
        if env_models:
            candidates.extend(
                _normalize_model(x)
                for x in env_models.split(",")
                if x.strip()
            )

    seen = set()
    ordered = []
    for model in candidates:
        model = _normalize_model(model)
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


def _build_prompt(calibration: dict, vision_hint: dict | None = None) -> str:
    parts = [PROMPT]

    calibration_prompt = str(calibration.get("prompt") or "").strip()
    if calibration_prompt:
        # Aggregate feedback is useful, but cap it tightly so historical
        # calibration cannot keep inflating every paid request.
        parts.append(calibration_prompt[:900])

    if vision_hint and vision_hint.get("label"):
        confidence = float(vision_hint.get("confidence") or 0)
        sample_count = int(vision_hint.get("sample_count") or 0)
        if confidence >= 0.45 and sample_count >= 2:
            parts.append(
                f"보조 이미지 분류기 힌트: 에이전트 '{vision_hint['label']}' "
                f"(신뢰도 {confidence:.2f}, 샘플 {sample_count}). "
                "영상 근거와 일치할 때만 사용하세요."
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
        return 503, "현재 설정된 Gemini 모델을 사용할 수 없습니다. 지원되는 Flash-Lite 대체 모델까지 시도했지만 실패했습니다."
    if "400" in text or "invalid_argument" in lower:
        return 503, "Gemini가 영상 분석 요청 형식을 거부했습니다. Render 로그의 '[Gemini] API 오류' 한 줄을 확인해 주세요."
    if "validation" in lower or "json" in lower or "빈 응답" in text:
        return 503, "Gemini 응답 형식 검증에 실패했습니다. 대체 모델까지 시도했습니다."
    if "503" in text or "unavailable" in lower:
        return 503, "Gemini 서버가 일시적으로 사용할 수 없습니다. 무료 분석 횟수는 차감되지 않았습니다."

    return 503, "Gemini 영상 분석 단계에서 오류가 발생했습니다. Render 로그에서 '[Gemini]'로 시작하는 줄을 확인해 주세요."


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


def _generate(client: genai.Client, uploaded, calibration: dict, vision_hint: dict | None = None) -> dict:
    errors_seen: list[str] = []
    prompt = _build_prompt(calibration, vision_hint)

    for model in _models():
        print(
            f"[Gemini] {model} 시도 · cost-first · low media resolution · minimal thinking · "
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
                        temperature=0.10,
                        max_output_tokens=2200,
                        media_resolution=types.MediaResolution.MEDIA_RESOLUTION_LOW,
                        thinking_config=types.ThinkingConfig(thinking_level="minimal"),
                        automatic_function_calling=types.AutomaticFunctionCallingConfig(
                            disable=True
                        ),
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

                result = ValorantAnalysis.model_validate_json(response.text).model_dump()
                result["events"] = list(result.get("events") or [])[:6]
                result["top_priorities"] = list(result.get("top_priorities") or [])[:3]
                result["limitations"] = list(result.get("limitations") or [])[:2]
                result["model_used"] = model
                result["analysis_fps"] = 1
                result["media_resolution"] = "low"
                result["thinking_level"] = "minimal"
                result["economy_mode"] = True
                result["vision_learning_hint_used"] = bool(
                    vision_hint
                    and vision_hint.get("label")
                    and float(vision_hint.get("confidence") or 0) >= 0.45
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

    status_code, detail = _safe_failure_detail(errors_seen)
    raise HTTPException(status_code=status_code, detail=detail)


def analyze_video(video_path: Path, vision_hint: dict | None = None) -> dict:
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise HTTPException(
            status_code=503,
            detail="Render에 GEMINI_API_KEY가 설정되어 있지 않습니다.",
        )

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
