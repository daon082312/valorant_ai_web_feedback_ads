from __future__ import annotations

import os
from typing import Literal

from dotenv import load_dotenv
from google import genai
from google.genai import errors, types
from pydantic import BaseModel, Field

load_dotenv()


class CombatVerificationItem(BaseModel):
    event_index: int = Field(ge=0, le=20)
    outcome: Literal["kill", "death", "assist", "survived", "no_combat", "uncertain"]
    confidence: float = Field(ge=0, le=1)
    evidence: str = Field(description="판정 근거가 된 화면 UI/상황을 한국어 한 문장으로 작성")
    replace_text: bool = Field(description="기존 관찰/피드백의 전투 결과가 틀렸거나 명백한 결과를 놓쳐 문장 교체가 필요한지")
    corrected_observation: str = Field(description="검증 결과를 반영한 한국어 관찰 한 문장")
    corrected_feedback: str = Field(description="검증 결과를 반영한 한국어 코칭 1~2문장")


class CombatVerificationResponse(BaseModel):
    events: list[CombatVerificationItem]
    replace_summary: bool = Field(description="전체 요약에 잘못된 킬/데스 전제가 있어 수정이 필요한지")
    corrected_summary: str = Field(description="전투 결과 검증을 반영한 전체 한국어 요약. 수정이 필요 없으면 기존 요약을 그대로 반환")


PROMPT = """
당신은 VALORANT 전투 결과만 엄격하게 재검증하는 판독기입니다.
각 이벤트마다 직전/시점/직후 프레임이 순서대로 제공됩니다. 기존 코칭 문장은 참고만 하고,
킬/데스 여부는 반드시 화면의 직접적인 증거로 다시 판단하세요.

매우 중요한 판정 규칙:
1. POV 플레이어가 적을 죽였다고 판정하려면 킬피드, 명확한 킬 확인 UI, 적 사망 애니메이션 등 직접 증거가 있어야 합니다.
   총을 맞혔다, 적이 화면에서 사라졌다, 교전에서 우세했다는 이유만으로 kill을 추측하지 마세요.
2. POV 플레이어가 죽었다고 판정하려면 사망/Combat Report/관전자/리스폰 전환처럼 POV 플레이어의 사망을 직접 확인할 수 있어야 합니다.
   피격, 낮은 체력, 붉은 화면 효과, 화면 흔들림, 엄폐, 후퇴, 암전만으로 death를 판정하지 마세요.
3. 직후 프레임에서도 일반 HUD와 무기/체력이 계속 보이며 POV 플레이가 이어지면 death가 아니라 survived 쪽을 우선합니다.
4. 킬피드가 보이더라도 다른 팀원의 킬일 수 있습니다. POV 플레이어의 처치라고 확실하지 않으면 kill로 단정하지 마세요.
5. 확실하지 않으면 uncertain을 사용하세요. 정확한 불확실성이 잘못된 단정보다 낫습니다.
6. 기존 observation/feedback이 실제 화면과 충돌하면 replace_text=true로 하고 수정 문장을 작성하세요.
7. 기존 문장이 사망을 잘못 전제로 하면 사망 전제를 제거하세요. 실제 적 처치를 놓쳤다면 명확히 적 처치를 반영하세요.
8. corrected_observation은 화면에서 직접 확인한 사실 위주 1문장, corrected_feedback은 그 사실에 맞는 1~2문장 코칭으로 작성하세요.
9. 제공된 이벤트 인덱스를 그대로 반환하고, 모든 이벤트를 정확히 한 번씩 반환하세요.
10. 전체 요약에 이번 프레임 검증과 충돌하는 킬/데스 주장이 있으면 replace_summary=true로 하고 그 부분만 수정하세요.
    킬/데스와 무관한 Aim, Movement, Positioning, Utility 코칭은 가능한 한 유지하세요.
"""


def _model_candidates() -> list[str]:
    requested = os.getenv("COMBAT_VERIFIER_MODEL", "").strip()
    candidates = [requested or "gemini-3.1-flash-lite", "gemini-3.5-flash-lite"]
    result: list[str] = []
    for value in candidates:
        value = value.removeprefix("models/").strip()
        if value and value not in result:
            result.append(value)
    return result


def verify_combat_events(events: list[dict], summary: str = "") -> dict:
    api_key = os.getenv("GEMINI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY_NOT_CONFIGURED")

    if not events:
        return {"events": [], "model_used": "", "verified": False, "replace_summary": False, "corrected_summary": summary}

    client = genai.Client(api_key=api_key)
    contents: list = [
        PROMPT,
        "전체 기존 요약:\n" + str(summary or "")[:1800],
    ]

    for event in events[:6]:
        idx = int(event.get("event_index", 0))
        timestamp = str(event.get("timestamp") or "")[:20]
        observation = str(event.get("observation") or "")[:700]
        feedback = str(event.get("feedback") or "")[:900]
        contents.append(
            f"이벤트 {idx} · timestamp={timestamp}\n"
            f"기존 관찰: {observation}\n"
            f"기존 피드백: {feedback}\n"
            "다음 이미지는 이 이벤트의 시간 순서 프레임입니다."
        )
        for frame_no, image_bytes in enumerate(event.get("frames") or []):
            contents.append(f"이벤트 {idx} 프레임 {frame_no + 1}")
            contents.append(types.Part.from_bytes(data=image_bytes, mime_type="image/jpeg"))

    errors_seen: list[str] = []
    for model in _model_candidates():
        try:
            response = client.models.generate_content(
                model=model,
                contents=contents,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=CombatVerificationResponse,
                    temperature=0.0,
                    max_output_tokens=1800,
                    media_resolution=types.MediaResolution.MEDIA_RESOLUTION_HIGH,
                    thinking_config=types.ThinkingConfig(thinking_level="minimal"),
                    automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
                ),
            )
            if not response.text:
                raise RuntimeError(f"{model} empty response")

            parsed = CombatVerificationResponse.model_validate_json(response.text).model_dump()
            requested_indices = {
                int(event.get("event_index", 0)) for event in events[:6]
            }
            filtered = [
                item for item in parsed.get("events", [])
                if int(item.get("event_index", -1)) in requested_indices
            ]
            return {
                "events": filtered,
                "model_used": model,
                "verified": bool(filtered),
                "replace_summary": bool(parsed.get("replace_summary")),
                "corrected_summary": str(parsed.get("corrected_summary") or summary),
            }
        except (errors.APIError, ValueError, RuntimeError) as exc:
            text = f"{model}: {type(exc).__name__}: {exc}"
            errors_seen.append(text)
            print(f"[CombatVerifier] {text}")
            continue

    raise RuntimeError("COMBAT_VERIFICATION_FAILED: " + " | ".join(errors_seen[-2:]))
