from __future__ import annotations

import os
from typing import Literal

from dotenv import load_dotenv
from google import genai
from google.genai import errors, types
from pydantic import BaseModel, Field

load_dotenv()


DISCLAIMER = (
    "이 결과는 업로드된 POV 영상에서 보이는 비정상 패턴의 참고용 의심도이며 치트 사용 확정 판정이 아닙니다. "
    "사운드·팀 콜·미니맵·전체 경기 맥락이 빠져 있을 수 있으므로 신고나 제재의 단독 근거로 사용하면 안 됩니다."
)


Signal = Literal[
    "aim_snap",
    "wall_tracking",
    "information_anomaly",
    "unnatural_target_switching",
    "trigger_like_timing",
    "none",
]


class CheatEventAssessment(BaseModel):
    event_index: int = Field(ge=0, le=20)
    score: int = Field(ge=0, le=100)
    confidence: float = Field(ge=0, le=1)
    visibility_quality: float = Field(ge=0, le=1)
    signals: list[Signal] = Field(default_factory=list, max_length=5)
    evidence: str = Field(default="", max_length=500)
    benign_explanation: str = Field(default="", max_length=400)


class CheatOnlyResponse(BaseModel):
    events: list[CheatEventAssessment]


PROMPT = """
당신은 VALORANT POV 영상의 비정상 에임/정보 패턴만 검토하는 전용 검증기입니다.
요원, 스킬, 킬로그, 티어, 코칭은 분석하지 마세요. 오직 업로드된 POV 플레이어의 치트 의심 신호만 장면별로 평가합니다.

각 EVENT 이미지는 FULL HUD CONTEXT이며, 치트 검토 대상으로 선택된 장면에는 이미지 하단에
'AIM MOTION STRIP'이 있습니다. 이 스트립은 짧은 시간 간격의 연속 프레임이며 왼쪽→오른쪽,
위→아래 순서입니다. 반드시 프레임의 시간적 연속성을 보고 판정하세요.

장면별 score 기준:
- 0~29: 뚜렷한 비정상 패턴 없음.
- 30~49: 약하거나 정상 플레이로도 쉽게 설명되는 단서.
- 50~69: 반복성 또는 기계성이 보여 의심할 가치가 있음.
- 70~84: 한 장면 안에서 직접 보이는 강한 비정상 패턴.
- 85~100: 매우 명백한 기계적 스냅/추적/전환이 연속 프레임에서 직접 확인됨.
score는 치트 사용 확률이 아닙니다.

신호 판정 규칙:
- aim_snap: 에임이 매우 짧은 시간에 큰 각도로 이동해 적의 중심/머리 위치에 거의 즉시 정렬되고,
  자연스러운 미세 조정 없이 멈추는 움직임이 연속 프레임에서 직접 보일 때. 빠른 플릭 하나만으로는 부족합니다.
- wall_tracking: 적이 화면에서 가려진 동안에도 벽/엄폐물 뒤 예상 위치를 에임이 지속적으로 따라가고,
  적이 다시 나타날 때 정렬이 이어지는 패턴이 보일 때.
- information_anomaly: 화면상 정보가 없는데 여러 번 정확한 사전 조준/사전 대응을 하는 경우.
  단, 이 이미지에는 오디오가 없으므로 사운드 정보 가능성을 benign_explanation에 반드시 고려하세요.
- unnatural_target_switching: 둘 이상의 표적 사이를 인간적인 오버슈트/수정 없이 기계적으로 전환하는 패턴.
- trigger_like_timing: 적이 조준점과 겹치는 순간마다 지나치게 일정하게 발사되는 정황. 정지 프레임만으로
  확정할 수 없으므로 직접 근거가 약하면 사용하지 마세요.

중요:
- 좋은 에임, 높은 티어, 헤드샷, 프리파이어, 크로스헤어 배치만으로 점수를 높이지 마세요.
- 반대로 연속 프레임에 명확한 기계적 스냅이나 비가시 추적이 보이면 '프로라서 그럴 수 있음'이라는 이유만으로
  과도하게 낮추지 마세요. 직접 시각 증거는 그대로 점수에 반영하세요.
- 각 EVENT를 독립적으로 점수화하세요. 한 장면의 score를 낮추기 위해 '다른 장면에서도 반복되어야 한다'는 조건을
  붙이지 마세요. 반복성에 따른 최종 등급 조정은 서버가 합니다.
- visibility_quality가 낮으면 confidence를 낮추세요.
- signals가 없으면 ["none"]을 반환하세요.
- evidence에는 실제 보이는 움직임을 구체적으로 한 문장으로 적고 EVENT 번호를 포함하세요.
- benign_explanation에는 가장 가능성 높은 정상 설명을 한 문장으로 적으세요.
"""


def _model_candidates() -> list[str]:
    requested = os.getenv("CHEAT_VERIFIER_MODEL", "gemini-3.1-flash-lite").strip().removeprefix("models/")
    result = [requested or "gemini-3.1-flash-lite"]
    if os.getenv("CHEAT_ALLOW_QUALITY_FALLBACK", "").strip().lower() in {"1", "true", "yes", "on"}:
        result.append("gemini-3.5-flash-lite")
    return list(dict.fromkeys(x for x in result if x))


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


def _aggregate(items: list[dict]) -> dict:
    usable = [x for x in items if float(x.get("visibility_quality") or 0) >= 0.35]
    if not usable:
        return {
            "rating": "insufficient_evidence",
            "suspicion_score": 0,
            "confidence": 0.0,
            "indicators": ["none"],
            "reviewed_event_indices": [],
            "evidence": [],
            "benign_explanations": ["연속 에임 프레임의 가시성이 충분하지 않습니다."],
            "summary": "치트 의심도를 판단할 영상 근거가 충분하지 않습니다.",
            "disclaimer": DISCLAIMER,
            "dedicated_verifier": True,
            "per_event": items,
        }

    ordered = sorted(usable, key=lambda x: int(x.get("score") or 0), reverse=True)
    scores = [int(x.get("score") or 0) for x in usable]
    high = [x for x in usable if int(x.get("score") or 0) >= 70]
    moderate = [x for x in usable if int(x.get("score") or 0) >= 55]
    very_high = [x for x in usable if int(x.get("score") or 0) >= 85]
    signal_set = {
        str(sig)
        for item in usable
        for sig in (item.get("signals") or [])
        if str(sig) and str(sig) != "none"
    }

    # A single extremely abnormal event may be suspicious, but a strong label
    # still requires repetition across independent scenes.
    if len(high) >= 2 and len(signal_set) >= 1:
        rating = "strongly_suspicious"
    elif len(very_high) >= 1 or len(moderate) >= 2 or len(high) >= 1:
        rating = "suspicious"
    elif max(scores, default=0) >= 35:
        rating = "insufficient_evidence"
    else:
        rating = "no_clear_evidence"

    max_score = max(scores, default=0)
    repetition_bonus = 10 if len(high) >= 2 else (6 if len(moderate) >= 2 else 0)
    signal_bonus = min(8, max(0, len(signal_set) - 1) * 3)
    score = min(100, max_score + repetition_bonus + signal_bonus)
    if rating == "no_clear_evidence":
        score = min(score, 34)
    elif rating == "insufficient_evidence":
        score = min(score, 54)
    elif rating == "suspicious":
        score = max(55, min(score, 84))
    else:
        score = max(85, score)

    weighted_conf = sum(
        float(x.get("confidence") or 0) * float(x.get("visibility_quality") or 0)
        for x in usable
    ) / max(0.001, sum(float(x.get("visibility_quality") or 0) for x in usable))

    evidence = [str(x.get("evidence") or "").strip() for x in ordered if str(x.get("evidence") or "").strip()][:4]
    benign = [
        str(x.get("benign_explanation") or "").strip()
        for x in ordered
        if str(x.get("benign_explanation") or "").strip()
    ][:4]

    if rating == "strongly_suspicious":
        summary = "서로 다른 주요 장면에서 강한 비정상 에임/추적 패턴이 반복되어 강한 의심 신호로 분류했습니다. 확정 판정은 아닙니다."
    elif rating == "suspicious":
        summary = "연속 프레임에서 정상 플레이만으로 설명하기 어려운 비정상 패턴이 확인되어 추가 검토가 필요합니다. 확정 판정은 아닙니다."
    elif rating == "insufficient_evidence":
        summary = "일부 이상 단서는 있으나 치트 의심으로 단정할 만큼 반복성과 직접 근거가 충분하지 않습니다."
    else:
        summary = "검토한 연속 프레임에서는 뚜렷하게 반복되는 치트 의심 패턴이 확인되지 않았습니다."

    return {
        "rating": rating,
        "suspicion_score": score,
        "confidence": max(0.0, min(1.0, weighted_conf)),
        "indicators": sorted(signal_set)[:6] or ["none"],
        "reviewed_event_indices": [int(x.get("event_index", 0)) for x in usable][:6],
        "evidence": evidence,
        "benign_explanations": benign or ["사운드·팀 콜·미니맵 정보가 이미지 검증에 완전히 포함되지 않았을 수 있습니다."],
        "summary": summary,
        "disclaimer": DISCLAIMER,
        "dedicated_verifier": True,
        "per_event": items,
    }


def verify_cheat_suspicion(events: list[dict]) -> dict:
    selected = [event for event in list(events or []) if bool(event.get("dense_motion_strip"))][:3]
    if not selected:
        result = _aggregate([])
        result["model_used"] = ""
        result["token_usage"] = {}
        return result

    api_key = os.getenv("GEMINI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY_NOT_CONFIGURED")

    contents: list = [PROMPT]
    for event in selected:
        idx = int(event.get("event_index", 0))
        timestamp = str(event.get("timestamp") or "")[:20]
        frames = list(event.get("frames") or [])
        if len(frames) < 3:
            continue
        contents.append(
            f"EVENT {idx} · timestamp={timestamp}. "
            "다음 이미지는 FULL HUD CONTEXT이며 하단 AIM MOTION STRIP을 최우선으로 시간 순서대로 판독하세요."
        )
        contents.append(types.Part.from_bytes(data=frames[2], mime_type="image/jpeg"))

    requested = {int(event.get("event_index", 0)) for event in selected}
    client = genai.Client(api_key=api_key)
    errors_seen: list[str] = []

    for model in _model_candidates():
        try:
            response = client.models.generate_content(
                model=model,
                contents=contents,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=CheatOnlyResponse,
                    temperature=0.0,
                    max_output_tokens=1400,
                    media_resolution=types.MediaResolution.MEDIA_RESOLUTION_HIGH,
                    thinking_config=types.ThinkingConfig(thinking_level="minimal"),
                    automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
                ),
            )
            if not response.text:
                raise RuntimeError(f"{model} empty response")
            parsed = CheatOnlyResponse.model_validate_json(response.text).model_dump()
            items = [
                item for item in parsed.get("events", [])
                if int(item.get("event_index", -1)) in requested
            ]
            result = _aggregate(items)
            result["model_used"] = model
            result["token_usage"] = _usage_metadata(response)
            print(
                "[CheatVerifier] "
                f"model={model} rating={result.get('rating')} score={result.get('suspicion_score')} "
                f"events={[(x.get('event_index'), x.get('score')) for x in items]}"
            )
            return result
        except (errors.APIError, ValueError, RuntimeError) as exc:
            text = f"{model}: {type(exc).__name__}: {exc}"
            errors_seen.append(text)
            print(f"[CheatVerifier] {text}")

    raise RuntimeError("CHEAT_VERIFICATION_FAILED: " + " | ".join(errors_seen[-2:]))
