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
        description="반드시 자연스러운 한국어로 작성하는 티어 예측 근거 2~3문장. 관찰된 강점과 약점을 함께 설명"
    )


class AgentPrediction(BaseModel):
    agent: str = Field(
        description="영상의 HUD, 스킬 아이콘, 손/장비, 스킬 효과를 종합해 판단한 플레이어 에이전트 영문 이름. 확신할 수 없으면 Unknown"
    )
    confidence: float = Field(ge=0, le=1)
    reason: str = Field(
        description="에이전트를 그렇게 판단한 시각적 근거를 한국어 1~2문장으로 작성"
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
        description="영상에서 직접 관찰한 사실을 자연스러운 한국어 1~2문장으로 구체적으로 작성"
    )
    feedback: str = Field(
        description="해당 장면의 문제 또는 강점, 이유, 개선 방법을 자연스러운 한국어 2~3문장으로 작성"
    )
    ability_name: str | None = Field(
        default=None,
        description="이 장면에서 플레이어가 사용한 스킬의 공식 영문 이름. 사용하지 않았거나 확신할 수 없으면 null",
    )
    ability_confidence: float = Field(
        default=0.0,
        ge=0,
        le=1,
        description="ability_name 판별 신뢰도. 스킬을 판별하지 못하면 0",
    )
    confidence: float = Field(ge=0, le=1)


class ValorantAnalysis(BaseModel):
    overall_score: int = Field(ge=0, le=100)
    summary: str = Field(
        description="전체 플레이 경향, 강점, 약점, 가장 중요한 개선 방향을 포함한 자연스러운 한국어 4~6문장 요약"
    )
    agent_prediction: AgentPrediction
    tier_prediction: TierPrediction
    scores: ScoreSet
    events: list[Event]
    top_priorities: list[str] = Field(
        description="가장 먼저 개선할 점 최대 4개. 각각 무엇을, 왜, 어떻게 개선할지 포함한 한국어 문장으로 작성",
    )
    limitations: list[str] = Field(
        description="분석 한계 최대 3개를 한국어로 작성",
    )


PROMPT = """
당신은 한국어로 답변하는 VALORANT 경기 후 코칭 전문가입니다. 첨부된 클립 전체를 시간 순서대로 분석하세요.

출력 언어 규칙:
- 사용자가 읽는 모든 자연어 문장은 반드시 한국어로 작성합니다.
- summary, agent_prediction.reason, tier_prediction.reason, events[].observation, events[].feedback,
  top_priorities, limitations를 모두 한국어로 작성합니다.
- 에이전트 이름과 스킬 공식 이름은 게임에서 사용하는 영문 표기를 사용해도 됩니다.
- JSON 키와 enum 값은 스키마에 정의된 값을 사용합니다.

에이전트/스킬 인식 규칙:
- 분석을 시작할 때 HUD 하단의 스킬 아이콘, 손/장비, 스킬 사용 애니메이션과 화면 효과를 종합하여 플레이어의 에이전트를 먼저 판단합니다.
- 한 가지 시각 단서만으로 에이전트를 확정하지 말고 서로 독립적인 단서를 가능하면 2개 이상 확인합니다.
- 에이전트가 확정되면 해당 에이전트가 실제로 보유한 스킬만 스킬 후보로 사용합니다. 다른 에이전트의 스킬 이름을 섞지 마세요.
- 각 주요 장면에서 실제로 스킬 사용이 관찰되면 ability_name에 그 스킬의 공식 영문 이름을 기록하고 ability_confidence를 제공합니다.
- 단순 총격, 이동, 무기 교체를 스킬 사용으로 오인하지 마세요.
- 스킬 아이콘이나 효과가 불명확하면 ability_name은 null, ability_confidence는 낮게 둡니다.
- agent_prediction도 확신할 수 없으면 agent를 Unknown으로 하고 confidence를 낮게 둡니다.

분석 규칙:
- 영상에서 직접 확인 가능한 내용만 말하고 보이지 않는 정보는 추측하지 않습니다.
- 불확실하면 confidence를 낮춥니다.
- 가능한 경우 MM:SS timestamp를 사용합니다.
- Aim, Movement, Positioning, Utility, Decision Making, Teamplay를 평가합니다.
- 영상으로 정확히 측정할 수 없는 reaction time(ms), DPI, frame-perfect timing을 주장하지 않습니다.
- overall_score와 영역별 점수는 0~100입니다.
- 중요한 장면을 최대 6개 반환합니다. 서로 다른 실수·강점·교전 선택을 우선하고, 완전히 비슷한 장면만 합칩니다.
- top_priorities는 최대 4개, limitations는 최대 3개입니다.
- summary는 4~6문장으로 작성하며, 전체 플레이 스타일, 잘한 점, 반복되는 문제, 가장 중요한 개선 방향을 모두 포함합니다.
- 각 event의 observation은 1~2문장으로 실제 화면에서 본 사실을 구체적으로 설명합니다.
- 각 event의 feedback은 2~3문장으로 작성하며, 단순히 '잘했다/아쉽다'로 끝내지 말고 왜 그런지와 다음에 무엇을 해야 하는지를 설명합니다.
- 한 번의 킬이나 실수만으로 습관을 단정하지 않습니다.
- 킬 성공 여부보다 크로스헤어 위치, 노출 각도, 커버, 이동, 유틸리티, 교전 선택을 우선 평가합니다.
- 좋은 플레이도 최소 1개 이상 찾을 수 있으면 포함해서 사용자가 유지해야 할 습관을 알려줍니다.
- 영상에서 반복되는 패턴이 보이면 서로 다른 시점의 근거를 연결해서 설명합니다.
- 내용 없는 반복, 뻔한 문구, 불필요한 장황함은 피하고 실제 코칭에 도움이 되는 정보 밀도를 높입니다.

티어 예측:
- 실제 랭크/MMR이 아니라 이 클립에서 관찰되는 플레이 수준의 추정치입니다.
- 가능한 티어: Iron, Bronze, Silver, Gold, Platinum, Diamond, Ascendant, Immortal, Radiant.
- Aim만 보지 말고 Movement, Positioning, Utility, Decision Making과 일관성을 함께 봅니다.
- 짧거나 근거가 부족한 클립은 confidence를 낮게 주며, 근거가 매우 적으면 0.45 이하로 둡니다.
- reason은 한국어 2~3문장으로 작성하며, 티어를 높게 본 근거와 낮게 본 근거를 모두 포함합니다.
- 실제 랭크와 다를 수 있음을 limitations에 포함합니다.
"""


DEFAULT_PRIMARY_MODEL = "gemini-2.5-flash-lite"
LOW_COST_FALLBACK_MODEL = "gemini-3.1-flash-lite"


def _truthy_env(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _models() -> list[str]:
    primary = os.getenv("GEMINI_PRIMARY_MODEL", DEFAULT_PRIMARY_MODEL).strip()
    if not primary:
        primary = DEFAULT_PRIMARY_MODEL

    candidates = [primary, DEFAULT_PRIMARY_MODEL, LOW_COST_FALLBACK_MODEL]

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
        parts.append(calibration_prompt[:2200])

    if vision_hint and vision_hint.get("label"):
        confidence = float(vision_hint.get("confidence") or 0)
        sample_count = int(vision_hint.get("sample_count") or 0)
        if confidence >= 0.45 and sample_count >= 2:
            parts.append(
                "사이트의 별도 학습형 이미지 분류기가 HUD 프레임을 기반으로 "
                f"플레이어 에이전트를 '{vision_hint['label']}'로 예측했습니다 "
                f"(학습 샘플 {sample_count}개, 내부 신뢰도 {confidence:.2f}). "
                "이 값은 사용자의 정정 데이터로 실제 학습된 보조 모델의 힌트일 뿐 정답으로 강제하지 마세요. "
                "영상의 HUD/스킬 아이콘/효과와 일치할 때만 채택하고, 불일치하면 영상 근거를 우선하세요."
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
        return 503, "현재 설정된 Gemini 모델을 사용할 수 없습니다. 저가형 대체 모델까지 시도했지만 실패했습니다."
    if "400" in text or "invalid_argument" in lower:
        return 503, "Gemini가 영상 분석 요청 형식을 거부했습니다. Render 로그의 '[Gemini] API 오류' 한 줄을 확인해 주세요."
    if "validation" in lower or "json" in lower or "빈 응답" in text:
        return 503, "Gemini 응답 형식 검증에 실패했습니다. 저가형 대체 모델까지 시도했습니다."
    if "503" in text or "unavailable" in lower:
        return 503, "Gemini 서버가 일시적으로 사용할 수 없습니다. 무료 분석 횟수는 차감되지 않았습니다."

    return 503, "Gemini 영상 분석 단계에서 오류가 발생했습니다. Render 로그에서 '[Gemini]'로 시작하는 줄을 확인해 주세요."


def _generate(client: genai.Client, uploaded, calibration: dict, vision_hint: dict | None = None) -> dict:
    errors_seen: list[str] = []
    prompt = _build_prompt(calibration, vision_hint)

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

                result["events"] = list(result.get("events") or [])[:6]
                result["top_priorities"] = list(result.get("top_priorities") or [])[:4]
                result["limitations"] = list(result.get("limitations") or [])[:3]
                result["model_used"] = model
                result["analysis_fps"] = 1
                result["media_resolution"] = "default"
                result["economy_mode"] = True
                result["vision_learning_hint_used"] = bool(
                    vision_hint
                    and vision_hint.get("label")
                    and float(vision_hint.get("confidence") or 0) >= 0.45
                    and int(vision_hint.get("sample_count") or 0) >= 2
                )
                result["vision_learning_hint"] = vision_hint or {}
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
