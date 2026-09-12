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
    aim: int = Field(
        ge=0,
        le=100,
        description="관찰 가능한 크로스헤어 배치, 조준 안정성, 교전 중 에임 수행을 종합한 점수",
    )
    movement: int = Field(
        ge=0,
        le=100,
        description="관찰 가능한 이동, 피킹, 정지 후 사격, 이동 경로의 품질을 종합한 점수",
    )
    positioning: int = Field(
        ge=0,
        le=100,
        description="노출 각도, 커버 활용, 교전 위치 선택 등 관찰 가능한 포지셔닝 점수",
    )
    utility: int = Field(
        ge=0,
        le=100,
        description="클립에서 실제로 확인되는 스킬/유틸리티 사용의 타이밍과 효율 점수",
    )
    decision_making: int = Field(
        ge=0,
        le=100,
        description="클립에서 확인되는 교전 선택, 후퇴/진입 판단, 정보 활용 등 의사결정 점수",
    )


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
    confidence: float = Field(
        ge=0,
        le=1,
        description="이 클립만으로 플레이 수준을 추정한 신뢰도. 근거가 적으면 낮춰야 함",
    )
    reason: str = Field(
        description="티어 추정에 사용한 직접 관찰 가능한 핵심 근거를 한국어 1~3문장으로 설명"
    )


class Event(BaseModel):
    timestamp: str = Field(description="가능하면 MM:SS 형식의 장면 시작 시점")
    category: Literal[
        "aim",
        "movement",
        "positioning",
        "utility",
        "decision_making",
        "teamplay",
        "other",
    ]
    severity: Literal[
        "positive",
        "low",
        "medium",
        "high",
    ]
    observation: str = Field(
        description="영상에서 직접 확인한 사실. 보이지 않는 정보나 의도를 추정하지 않음"
    )
    feedback: str = Field(
        description="해당 관찰에 직접 연결되는 구체적인 한국어 코칭"
    )
    confidence: float = Field(
        ge=0,
        le=1,
        description="관찰과 피드백이 영상 근거로 뒷받침되는 정도",
    )


class ValorantAnalysis(BaseModel):
    overall_score: int = Field(
        ge=0,
        le=100,
        description="클립에서 관찰된 플레이만 기준으로 한 종합 점수",
    )
    summary: str
    tier_prediction: TierPrediction
    scores: ScoreSet
    events: list[Event]
    top_priorities: list[str]
    limitations: list[str]


PROMPT = """
당신은 VALORANT 경기 후(post-game) 코칭 전문가입니다.
첨부된 게임 클립 전체를 시간 순서대로 분석하세요.

핵심 원칙:
- 영상에서 실제로 확인 가능한 내용만 말합니다.
- 보이지 않는 적, 스킬, 팀원의 의도는 추측하지 않습니다.
- 관찰(observation)과 해석/코칭(feedback)을 분리합니다.
- 한 프레임 또는 한 번의 킬만으로 일반적인 습관이라고 단정하지 않습니다.
- 불확실하면 confidence를 낮게 기록합니다.
- 가능한 경우 MM:SS timestamp를 기록합니다.
- 피드백은 한국어로 작성합니다.
- Aim, Movement, Positioning, Utility,
  Decision Making, Teamplay를 평가합니다.
- 영상만으로 정확히 측정할 수 없는 reaction time(ms),
  DPI, frame-perfect counter-strafe timing을
  측정했다고 주장하지 마세요.
- overall_score와 각 점수는 0~100입니다.
- top_priorities는 최대 3개입니다.
- events는 중복되거나 중요도가 낮은 장면을 제외하고 최대 8개만 반환합니다.
- limitations는 핵심적인 항목 최대 4개만 반환합니다.
- summary와 각 feedback은 짧고 구체적으로 작성하여 불필요하게 긴 설명을 피합니다.

정확도 규칙:
- 같은 결론을 뒷받침하는 장면이 여러 개 있으면 일관성을 근거로 사용할 수 있습니다.
- 장면이 하나뿐이면 '이 장면에서는'처럼 범위를 제한해 설명하세요.
- 미니맵/HUD가 작거나 가려져 있으면 해당 정보에 대한 confidence를 낮추세요.
- 사격 직전과 직후의 이동을 함께 보고 Movement를 판단하세요.
- 단순 킬 성공 여부보다 크로스헤어 위치, 노출 각도, 커버, 유틸리티,
  교전 선택과 반복되는 패턴을 우선 평가하세요.

티어 예측 규칙:
- tier_prediction은 계정의 실제 경쟁전 랭크를 읽거나 맞히는 기능이 아니라,
  이 클립에서 관찰되는 플레이 수준을 바탕으로 한 추정치입니다.
- 가능한 티어는 Iron, Bronze, Silver, Gold, Platinum, Diamond,
  Ascendant, Immortal, Radiant 중 하나입니다.
- Aim만 보지 말고 Movement, Positioning, Utility 사용,
  Decision Making, 교전 선택, 크로스헤어 배치와 일관성을 종합합니다.
- 한두 번의 좋은 킬 또는 나쁜 실수만으로 티어를 과도하게 올리거나 내리지 마세요.
- 클립이 짧거나 판단할 장면이 적으면 tier_prediction.confidence를 낮게 주세요.
  특히 근거가 부족하면 confidence를 0.45 이하로 설정하세요.
- tier_prediction.reason에는 왜 그 티어로 추정했는지 핵심 근거를
  1~3문장으로 한국어로 설명하세요.
- 실제 랭크/MMR과 다를 수 있다는 점을 limitations에도 명시하세요.
"""


DEFAULT_MODELS = [
    "gemini-3.8-flash",
    "gemini-3.7-flash",
    "gemini-3.6-flash",
    "gemini-3.5-flash-lite",
]


def _models() -> list[str]:
    env_models = os.getenv(
        "GEMINI_MODELS",
        "",
    ).strip()

    if env_models:
        return [
            x.strip()
            for x in env_models.split(",")
            if x.strip()
        ]

    return DEFAULT_MODELS.copy()


def _video_fps() -> float:
    # 2 FPS keeps twice the temporal detail of Gemini's 1 FPS default while
    # using roughly half the visual-frame input of the previous 4 FPS setting.
    raw = os.getenv("VIDEO_ANALYSIS_FPS", "2").strip()
    try:
        value = float(raw)
    except ValueError:
        value = 2.0

    return min(24.0, max(0.1, value))


def _wait_for_file(
    client: genai.Client,
    uploaded,
):
    for _ in range(120):
        current = client.files.get(
            name=uploaded.name
        )
        state = getattr(
            getattr(
                current,
                "state",
                None,
            ),
            "name",
            None,
        )

        if state == "ACTIVE":
            return current

        if state == "FAILED":
            raise RuntimeError(
                "Gemini 영상 처리에 실패했습니다."
            )

        time.sleep(2)

    raise RuntimeError(
        "Gemini 영상 처리 시간이 너무 오래 걸렸습니다."
    )


def _daily_quota(text: str) -> bool:
    lower = text.lower()
    return (
        "generaterequestsperdayperprojectpermodel"
        in lower
        or "requestsperday" in lower
        or "requests per day" in lower
    )


def _build_prompt(calibration: dict) -> str:
    calibration_prompt = str(calibration.get("prompt") or "").strip()
    if not calibration_prompt:
        return PROMPT

    # The calibration text is generated by server code from aggregate counters.
    # Raw user comments are never concatenated into the model prompt.
    return f"{PROMPT}\n\n{calibration_prompt[:5000]}"


def _video_part(uploaded, fps: float) -> types.Part:
    uri = getattr(uploaded, "uri", None)
    mime_type = getattr(uploaded, "mime_type", None) or "video/mp4"
    if not uri:
        raise RuntimeError("Gemini 업로드 영상 URI를 찾을 수 없습니다.")

    return types.Part(
        file_data=types.FileData(
            file_uri=uri,
            mime_type=mime_type,
        ),
        video_metadata=types.VideoMetadata(
            fps=fps,
        ),
    )


def _generate(
    client: genai.Client,
    uploaded,
    calibration: dict,
) -> dict:
    errors_seen = []
    fps = _video_fps()
    prompt = _build_prompt(calibration)
    video_part = _video_part(uploaded, fps)

    for model in _models():
        print(
            f"[Gemini] {model} 시도 · video={fps:g} FPS · low-res · "
            f"feedback n={calibration.get('sample_size', 0)}"
        )

        for attempt in range(1, 4):
            try:
                response = client.models.generate_content(
                    model=model,
                    contents=types.Content(
                        parts=[
                            video_part,
                            types.Part(text=prompt),
                        ]
                    ),
                    config=types.GenerateContentConfig(
                        response_mime_type=(
                            "application/json"
                        ),
                        response_schema=(
                            ValorantAnalysis
                        ),
                        temperature=0.2,
                        media_resolution=(
                            types.MediaResolution.MEDIA_RESOLUTION_LOW
                        ),
                    ),
                )

                if not response.text:
                    raise RuntimeError(
                        f"{model} 빈 응답"
                    )

                result = (
                    ValorantAnalysis
                    .model_validate_json(
                        response.text
                    )
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
                errors_seen.append(
                    f"{model}: {text}"
                )

                if (
                    (
                        "429" in text
                        or "RESOURCE_EXHAUSTED"
                        in text
                    )
                    and _daily_quota(text)
                ):
                    print(
                        f"[Gemini] {model} "
                        "일일 quota 소진"
                    )
                    break

                if (
                    "429" in text
                    or "RESOURCE_EXHAUSTED"
                    in text
                ):
                    if attempt < 3:
                        time.sleep(
                            10 * attempt
                        )
                        continue
                    break

                if (
                    "503" in text
                    or "UNAVAILABLE" in text
                ):
                    if attempt < 3:
                        time.sleep(
                            5 * attempt
                        )
                        continue
                    break

                if (
                    "404" in text
                    or "NOT_FOUND" in text
                    or "not found"
                    in text.lower()
                ):
                    break

                raise

    details = "\n\n".join(
        errors_seen[-4:]
    )

    raise RuntimeError(
        "Gemini quota가 모두 소진되었거나 "
        "현재 서버가 혼잡합니다.\n\n"
        f"{details}"
    )


def analyze_video(
    video_path: Path,
) -> dict:
    api_key = os.getenv(
        "GEMINI_API_KEY"
    )

    if not api_key:
        raise RuntimeError(
            "GEMINI_API_KEY가 없습니다."
        )

    client = genai.Client(
        api_key=api_key
    )
    uploaded = None

    try:
        calibration = get_feedback_calibration()

        uploaded = client.files.upload(
            file=video_path
        )

        uploaded = _wait_for_file(
            client,
            uploaded,
        )

        return _generate(
            client,
            uploaded,
            calibration,
        )

    finally:
        if uploaded is not None:
            try:
                client.files.delete(
                    name=uploaded.name
                )
            except Exception:
                pass
