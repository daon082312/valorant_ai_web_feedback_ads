from __future__ import annotations

import os
from typing import Literal

from dotenv import load_dotenv
from google import genai
from google.genai import errors, types
from pydantic import BaseModel, Field

from portrait_reference import match_agent_portrait_in_killfeed

load_dotenv()

MAX_GEMINI_EVENTS = max(1, min(3, int(os.getenv("COMBAT_GEMINI_MAX_EVENTS", "2"))))


class CombatVerificationItem(BaseModel):
    event_index: int = Field(ge=0, le=20)
    outcome: Literal["kill", "death", "assist", "survived", "no_combat", "uncertain"]
    confidence: float = Field(ge=0, le=1)
    evidence: str = Field(description="판정 근거를 매우 짧은 한국어 한 문장으로 작성")
    killfeed_visible: bool
    killfeed_supports_pov_kill: bool
    killfeed_note: str = Field(description="킬로그 근거를 짧은 한국어 한 문장으로 작성")
    replace_text: bool
    corrected_observation: str = Field(description="필요할 때만 수정 관찰 한 문장")
    corrected_feedback: str = Field(description="필요할 때만 수정 코칭 한 문장")


class CombatVerificationResponse(BaseModel):
    events: list[CombatVerificationItem]
    replace_summary: bool
    corrected_summary: str = Field(description="필요할 때만 킬/데스 오류를 수정한 기존 요약")


PROMPT = """
당신은 VALORANT 킬로그/사망 UI 전용 검증기입니다.
서버가 먼저 모든 주요 장면을 로컬 초상화 매칭으로 훑고, 의심도가 높은 최대 2개 장면만 이 요청에 넣었습니다.
각 이벤트에는 KILLFEED A/B가 제공되며, 사망 가능성이 있는 장면만 FULL CONTEXT가 추가됩니다.

규칙:
- 킬 판정은 확대 KILLFEED A/B를 최우선 증거로 사용합니다.
- POV 플레이어 요원 초상화가 공격자 쪽에 명확히 있고 방향/팀 맥락이 맞을 때만 본인 kill의 강한 증거로 봅니다.
- 어시스트 요원 아이콘을 공격자 아이콘으로 착각하지 마세요.
- 단순 명중, 적이 화면에서 사라짐, 교전 우세만으로 kill을 추측하지 마세요.
- death는 Combat Report, 관전자/리스폰 전환 등 직접 증거가 있어야 합니다. 피격/저체력/붉은 화면만으로 death라 하지 마세요.
- FULL CONTEXT가 있는 경우 정상 HUD와 무기/체력이 유지되면 survived를 우선합니다.
- 로컬 초상화 similarity 힌트는 '해당 초상화가 이미지 어딘가에 있을 가능성'일 뿐 공격자 위치를 뜻하지 않습니다.
- 불확실하면 uncertain을 사용합니다.
- 기존 문장이 실제 킬/데스와 충돌할 때만 replace_text=true로 하고 짧게 수정합니다.
- 전체 summary도 킬/데스 전제가 틀렸을 때만 수정하며 나머지 코칭은 유지합니다.
- evidence, killfeed_note, corrected_*는 최대한 짧게 쓰세요.
"""

_KILL_WORDS = (
    "킬", "처치", "죽였", "제거", "헤드샷", "킬로그", "kill", "killed", "eliminat", "headshot",
)
_DEATH_WORDS = (
    "사망", "죽었", "죽음", "데스", "combat report", "관전자", "death", "died", "dead",
)
_COMBAT_WORDS = (
    "교전", "적", "총격", "피격", "에임", "aim", "fight", "duel", "enemy", "damage",
)


def _truthy_env(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _model_candidates() -> list[str]:
    requested = os.getenv("COMBAT_VERIFIER_MODEL", "").strip().removeprefix("models/")
    candidates = [requested or "gemini-3.1-flash-lite"]
    if _truthy_env("COMBAT_ALLOW_QUALITY_FALLBACK", False):
        candidates.append("gemini-3.5-flash-lite")

    result: list[str] = []
    for value in candidates:
        value = value.removeprefix("models/").strip()
        if value and value not in result:
            result.append(value)
    return result


def _event_text(event: dict) -> str:
    return (
        str(event.get("observation") or "") + " " +
        str(event.get("feedback") or "")
    ).casefold()


def _text_score(event: dict) -> float:
    text = _event_text(event)
    score = 0.0
    if any(word in text for word in _KILL_WORDS):
        score += 3.0
    if any(word in text for word in _DEATH_WORDS):
        score += 3.0
    if any(word in text for word in _COMBAT_WORDS):
        score += 1.0
    return score


def _death_suspected(event: dict) -> bool:
    text = _event_text(event)
    return any(word in text for word in _DEATH_WORDS)


def _scan_portraits(events: list[dict], player_agent: str) -> dict[int, list[dict]]:
    hints_by_event: dict[int, list[dict]] = {}
    if not player_agent or player_agent.casefold() == "unknown":
        return hints_by_event

    for event in events[:6]:
        idx = int(event.get("event_index", 0))
        hints: list[dict] = []
        for image_bytes in list(event.get("frames") or [])[:2]:
            try:
                hints.append(match_agent_portrait_in_killfeed(image_bytes, player_agent))
            except Exception as exc:
                print(f"[CombatVerifier] local portrait match skipped: {type(exc).__name__}: {exc}")
        hints_by_event[idx] = hints
    return hints_by_event


def _portrait_score(hints: list[dict]) -> float:
    if not hints:
        return 0.0
    similarities = [float(item.get("similarity") or 0.0) for item in hints]
    best = max(similarities, default=0.0)
    ready = any(bool(item.get("ready")) for item in hints)
    return max(0.0, best) * 3.0 + (2.5 if ready else 0.0)


def _select_events(events: list[dict], portrait_hints: dict[int, list[dict]]) -> list[dict]:
    ranked = []
    for order, event in enumerate(events[:6]):
        idx = int(event.get("event_index", order))
        score = _text_score(event) + _portrait_score(portrait_hints.get(idx, []))
        ranked.append((score, -order, event))

    ranked.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return [item[2] for item in ranked[:MAX_GEMINI_EVENTS]]


def verify_combat_events(events: list[dict], summary: str = "", player_agent: str = "") -> dict:
    api_key = os.getenv("GEMINI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY_NOT_CONFIGURED")

    scanned_events = list(events or [])[:6]
    if not scanned_events:
        return {
            "events": [], "model_used": "", "verified": False,
            "replace_summary": False, "corrected_summary": summary,
            "local_scan_count": 0, "gemini_event_count": 0,
        }

    # Free/local stage: all six scenes are scanned with cached official agent
    # portraits. Only the two most suspicious scenes reach the paid API call.
    portrait_hints = _scan_portraits(scanned_events, player_agent)
    selected_events = _select_events(scanned_events, portrait_hints)

    print(
        f"[CombatVerifier] economy · local scan={len(scanned_events)} · "
        f"Gemini selected={len(selected_events)} · max={MAX_GEMINI_EVENTS}"
    )

    client = genai.Client(api_key=api_key)
    contents: list = [
        PROMPT,
        f"POV 플레이어 요원: {str(player_agent or 'Unknown')[:80]}",
        "기존 전체 요약:\n" + str(summary or "")[:1000],
    ]

    for event in selected_events:
        idx = int(event.get("event_index", 0))
        timestamp = str(event.get("timestamp") or "")[:20]
        observation = str(event.get("observation") or "")[:360]
        feedback = str(event.get("feedback") or "")[:420]
        all_frames = list(event.get("frames") or [])[:3]
        # Kill checks need only the two large killfeed crops. FULL CONTEXT is
        # expensive and is attached only when the first pass actually suspects
        # that the POV player died.
        frames = all_frames[:3] if _death_suspected(event) else all_frames[:2]
        hints = portrait_hints.get(idx, [])

        formatted_hints = ", ".join(
            f"{chr(65 + i)} sim={float(h.get('similarity') or 0):.3f} ready={bool(h.get('ready'))}"
            for i, h in enumerate(hints[:2])
        ) or "없음"

        contents.append(
            f"이벤트 {idx} · {timestamp}\n"
            f"기존 관찰: {observation}\n"
            f"기존 피드백: {feedback}\n"
            f"로컬 POV 요원 초상화 매칭: {formatted_hints}"
        )
        for frame_no, image_bytes in enumerate(frames):
            label = "KILLFEED A" if frame_no == 0 else "KILLFEED B" if frame_no == 1 else "FULL CONTEXT"
            contents.append(f"이벤트 {idx} · {label}")
            contents.append(types.Part.from_bytes(data=image_bytes, mime_type="image/jpeg"))

    errors_seen: list[str] = []
    selected_indices = {int(event.get("event_index", 0)) for event in selected_events}

    for model in _model_candidates():
        try:
            response = client.models.generate_content(
                model=model,
                contents=contents,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=CombatVerificationResponse,
                    temperature=0.0,
                    max_output_tokens=900,
                    media_resolution=types.MediaResolution.MEDIA_RESOLUTION_HIGH,
                    thinking_config=types.ThinkingConfig(thinking_level="minimal"),
                    automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
                ),
            )
            if not response.text:
                raise RuntimeError(f"{model} empty response")

            parsed = CombatVerificationResponse.model_validate_json(response.text).model_dump()
            filtered = [
                item for item in parsed.get("events", [])
                if int(item.get("event_index", -1)) in selected_indices
            ]
            return {
                "events": filtered,
                "model_used": model,
                "verified": bool(filtered),
                "replace_summary": bool(parsed.get("replace_summary")),
                "corrected_summary": str(parsed.get("corrected_summary") or summary),
                "portrait_reference_used": bool(portrait_hints),
                "portrait_match_hints": portrait_hints,
                "local_scan_count": len(scanned_events),
                "gemini_event_count": len(selected_events),
                "selected_event_indices": sorted(selected_indices),
                "economy_mode": True,
            }
        except (errors.APIError, ValueError, RuntimeError) as exc:
            text = f"{model}: {type(exc).__name__}: {exc}"
            errors_seen.append(text)
            print(f"[CombatVerifier] {text}")

    raise RuntimeError("COMBAT_VERIFICATION_FAILED: " + " | ".join(errors_seen[-2:]))
