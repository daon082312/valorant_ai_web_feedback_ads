from __future__ import annotations

import os
from typing import Literal

from dotenv import load_dotenv
from google import genai
from google.genai import errors, types
from pydantic import BaseModel, Field

from portrait_reference import build_agent_portrait_reference_sheet

load_dotenv()


class CombatVerificationItem(BaseModel):
    event_index: int = Field(ge=0, le=20)
    outcome: Literal["kill", "death", "assist", "survived", "no_combat", "uncertain"]
    confidence: float = Field(ge=0, le=1)
    evidence: str = Field(description="판정 근거가 된 화면 UI/상황을 한국어 한 문장으로 작성")
    killfeed_visible: bool = Field(description="확대된 우측 상단 킬로그에서 읽을 수 있는 엔트리가 보이는지")
    killfeed_supports_pov_kill: bool = Field(description="킬로그가 POV 플레이어 본인의 적 처치를 직접 지지하는지")
    killfeed_note: str = Field(description="킬로그에서 실제로 확인한 내용을 한국어 한 문장으로 작성")
    replace_text: bool = Field(description="기존 관찰/피드백의 전투 결과가 틀렸거나 명백한 결과를 놓쳐 문장 교체가 필요한지")
    corrected_observation: str = Field(description="검증 결과를 반영한 한국어 관찰 한 문장")
    corrected_feedback: str = Field(description="검증 결과를 반영한 한국어 코칭 1~2문장")


class CombatVerificationResponse(BaseModel):
    events: list[CombatVerificationItem]
    replace_summary: bool = Field(description="전체 요약에 잘못된 킬/데스 전제가 있어 수정이 필요한지")
    corrected_summary: str = Field(description="전투 결과 검증을 반영한 전체 한국어 요약. 수정이 필요 없으면 기존 요약을 그대로 반환")


PROMPT = """
당신은 VALORANT의 킬로그와 사망 UI를 전문적으로 판독하는 전투 결과 검증기입니다.
요원 초상화 참조 시트와 각 이벤트의 확대 킬로그 프레임이 제공됩니다.

이미지 구성:
- 가장 먼저 현재 VALORANT 요원 초상화 참조 시트가 1장 제공됩니다. 각 초상화 아래 영문 요원명이 적혀 있습니다.
- 각 이벤트마다 이미지가 최대 3장 제공됩니다.
  1) KILLFEED A: 우측 상단 킬로그만 아주 크게 확대한 단독 프레임
  2) KILLFEED B: 약간 뒤 시점의 우측 상단 킬로그만 아주 크게 확대한 단독 프레임
  3) FULL CONTEXT: 같은 장면의 전체 화면 연락표

반드시 요원 초상화 참조 시트를 먼저 익힌 뒤 KILLFEED A/B에서 공격자와 피해자 요원 아이콘을 대조하세요.
킬 여부는 KILLFEED A/B를 가장 중요한 직접 증거로 사용하고, FULL CONTEXT는 본인 사망/생존과 맥락 확인에 사용하세요.

매우 중요한 판정 규칙:
1. KILLFEED A 또는 B에 실제 킬로그 엔트리가 보이면 killfeed_visible=true로 하세요.
2. POV 플레이어가 적을 죽였다고 판정하려면 다음 중 직접 증거가 있어야 합니다.
   - 킬로그 공격자 쪽의 요원 초상화가 POV 플레이어 요원과 일치하고 방향/팀 맥락도 맞음
   - 명확한 킬 확인 UI가 나타남
   - 적 사망이 화면에서 직접 확인됨
   단순 명중, 적이 화면에서 사라짐, 교전 우세만으로 kill을 추측하지 마세요.
3. 킬로그에 여러 엔트리가 겹쳐 보여도 각 행을 따로 읽으세요. 가장 최신 행만 보고 나머지를 무시하지 마세요.
4. 2026년 VALORANT 킬피드에는 어시스트 요원 아이콘도 추가로 보일 수 있습니다. 보조/어시스트 아이콘을 공격자 본인 아이콘으로 착각하지 마세요.
5. 현재 POV 플레이어 요원명이 별도로 제공됩니다. 같은 팀에서는 같은 요원을 중복 선택할 수 없으므로,
   킬로그 공격자 쪽 초상화가 POV 요원과 명확히 일치하면 본인 처치의 강한 증거입니다.
6. 킬로그에 다른 팀원의 처치만 보이면 POV 플레이어의 kill로 계산하지 마세요.
7. POV 플레이어가 죽었다고 판정하려면 사망 화면, Combat Report, 관전자 전환, 리스폰 전환 등 직접 증거가 필요합니다.
   피격, 저체력, 붉은 화면, 화면 흔들림, 엄폐, 후퇴, 암전만으로 death를 판정하지 마세요.
8. 이후 FULL CONTEXT에서도 정상 HUD/무기/체력이 유지되고 플레이가 계속되면 death가 아니라 survived 쪽을 우선합니다.
9. 킬로그가 읽히지 않거나 증거가 충돌하면 uncertain을 사용하세요. 잘못된 확정보다 불확실 판정이 낫습니다.
10. 기존 observation/feedback이 실제 킬로그 또는 사망 UI와 충돌하면 replace_text=true로 하고 수정하세요.
11. 기존 문장이 본인 사망을 잘못 전제로 하면 그 전제를 제거하세요. 실제 적 처치를 놓쳤다면 킬로그 근거를 반영하세요.
12. corrected_observation은 화면에서 직접 확인한 사실 위주 한 문장, corrected_feedback은 해당 사실에 맞는 1~2문장 코칭으로 작성하세요.
13. 제공된 이벤트 인덱스를 그대로 반환하고 모든 이벤트를 정확히 한 번씩 반환하세요.
14. 전체 요약에 이번 검증과 충돌하는 킬/데스 주장이 있으면 replace_summary=true로 하고 그 부분만 수정하세요.
    Aim, Movement, Positioning, Utility 등 킬/데스와 무관한 코칭은 유지하세요.
15. killfeed_note에는 실제로 읽은 킬로그 근거를 적으세요. 읽을 수 없으면 "확대 킬로그에서 명확한 엔트리를 확인하지 못함"이라고 적으세요.
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


def verify_combat_events(
    events: list[dict],
    summary: str = "",
    player_agent: str = "",
) -> dict:
    api_key = os.getenv("GEMINI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY_NOT_CONFIGURED")

    if not events:
        return {
            "events": [],
            "model_used": "",
            "verified": False,
            "replace_summary": False,
            "corrected_summary": summary,
        }

    client = genai.Client(api_key=api_key)
    contents: list = [
        PROMPT,
        f"POV 플레이어의 현재 요원: {str(player_agent or 'Unknown')[:80]}",
        "전체 기존 요약:\n" + str(summary or "")[:1800],
    ]

    # Give Gemini an explicit visual dictionary of current agent portraits so
    # tiny killfeed portraits can be matched instead of guessed from memory.
    try:
        portrait_sheet = build_agent_portrait_reference_sheet()
    except Exception as exc:
        print(f"[CombatVerifier] portrait reference skipped: {type(exc).__name__}: {exc}")
        portrait_sheet = b""
    if portrait_sheet:
        contents.append("VALORANT 요원 초상화 참조 시트. 이후 킬로그 아이콘을 이 시트와 대조하세요.")
        contents.append(types.Part.from_bytes(data=portrait_sheet, mime_type="image/jpeg"))

    for event in events[:6]:
        idx = int(event.get("event_index", 0))
        timestamp = str(event.get("timestamp") or "")[:20]
        observation = str(event.get("observation") or "")[:700]
        feedback = str(event.get("feedback") or "")[:900]
        contents.append(
            f"이벤트 {idx} · timestamp={timestamp}\n"
            f"기존 관찰: {observation}\n"
            f"기존 피드백: {feedback}\n"
            "다음 이미지를 순서대로 판독하세요. 앞쪽은 큰 단독 킬로그 프레임이고 마지막은 전체 화면 맥락입니다."
        )
        frames = list(event.get("frames") or [])[:3]
        for frame_no, image_bytes in enumerate(frames):
            if frame_no == 0:
                label = "KILLFEED A · 큰 단독 확대 프레임"
            elif frame_no == 1:
                label = "KILLFEED B · 뒤 시점 큰 단독 확대 프레임"
            else:
                label = "FULL CONTEXT · 전체 화면 시간순 시퀀스"
            contents.append(f"이벤트 {idx} · {label}")
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
                    max_output_tokens=2000,
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
                "portrait_reference_used": bool(portrait_sheet),
            }
        except (errors.APIError, ValueError, RuntimeError) as exc:
            text = f"{model}: {type(exc).__name__}: {exc}"
            errors_seen.append(text)
            print(f"[CombatVerifier] {text}")
            continue

    raise RuntimeError("COMBAT_VERIFICATION_FAILED: " + " | ".join(errors_seen[-2:]))
