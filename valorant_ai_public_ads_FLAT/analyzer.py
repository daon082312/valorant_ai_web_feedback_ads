from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Literal

from dotenv import load_dotenv
from google import genai
from google.genai import types, errors
from pydantic import BaseModel, Field

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
    reason: str


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
    severity: Literal[
        "positive",
        "low",
        "medium",
        "high",
    ]
    observation: str
    feedback: str
    confidence: float = Field(
        ge=0,
        le=1,
    )


class ValorantAnalysis(BaseModel):
    overall_score: int = Field(
        ge=0,
        le=100,
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

- 영상에서 실제로 확인 가능한 내용만 말합니다.
- 보이지 않는 적, 스킬, 팀원의 의도는 추측하지 않습니다.
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


def _generate(
    client: genai.Client,
    uploaded,
) -> dict:
    errors_seen = []

    for model in _models():
        print(f"[Gemini] {model} 시도")

        for attempt in range(1, 4):
            try:
                response = client.models.generate_content(
                    model=model,
                    contents=[
                        uploaded,
                        PROMPT,
                    ],
                    config=types.GenerateContentConfig(
                        response_mime_type=(
                            "application/json"
                        ),
                        response_schema=(
                            ValorantAnalysis
                        ),
                        temperature=0.2,
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
        )

    finally:
        if uploaded is not None:
            try:
                client.files.delete(
                    name=uploaded.name
                )
            except Exception:
                pass
