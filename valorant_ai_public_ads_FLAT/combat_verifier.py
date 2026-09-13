from __future__ import annotations

import os
from typing import Literal

from dotenv import load_dotenv
from google import genai
from google.genai import errors, types
from pydantic import BaseModel, Field

from portrait_reference import build_ui_reference_sheets

load_dotenv()


class CombatVerificationItem(BaseModel):
    event_index: int = Field(ge=0, le=20)
    side: Literal["attacker", "defender", "unknown"]
    side_confidence: float = Field(ge=0, le=1)
    side_evidence: str
    outcome: Literal["kill", "death", "assist", "survived", "no_combat", "uncertain"]
    confidence: float = Field(ge=0, le=1)
    evidence: str
    killfeed_visible: bool
    killfeed_supports_pov_kill: bool
    killfeed_attacker_agent: str = ""
    killfeed_victim_agent: str = ""
    pov_killfeed_position: Literal["attacker", "victim", "assist", "not_found", "uncertain"] = "uncertain"
    killfeed_row_confidence: float = Field(default=0.0, ge=0, le=1)
    killfeed_note: str
    ability_name: str | None = None
    ability_confidence: float = Field(default=0.0, ge=0, le=1)
    ability_evidence: str = ""
    replace_text: bool
    corrected_observation: str
    corrected_feedback: str


class CombatVerificationResponse(BaseModel):
    agent: str
    agent_confidence: float = Field(ge=0, le=1)
    agent_evidence: str
    events: list[CombatVerificationItem]
    replace_summary: bool
    corrected_summary: str


PROMPT = """
VALORANT HUD 검증기입니다. 제공된 UI 참조표와 이벤트 이미지만 사용해 짧고 보수적으로 판정하세요.

검증 항목:
1. POV 플레이어 요원과 실제 사용 스킬
2. POV의 kill/death/assist/survived 여부
3. 공격팀/수비팀 여부

규칙:
- 불확실하면 Unknown/uncertain/null을 사용합니다.
- 공격/수비는 상단 ROUND ROLE HUD 또는 Plant/Defuse 같은 직접 근거로만 판단합니다.
- POV kill은 같은 킬로그 행에서 POV 요원이 공격자이고 별도 피해자가 함께 확인될 때만 허용합니다.
- POV 요원이 피해자 위치면 death, 작은 assist 위치면 assist입니다.
- 화면 중앙의 적 사라짐이나 명중 효과만으로 kill을 확정하지 않습니다.
- 스킬은 최종 요원이 실제로 가진 스킬만 반환합니다.
- 기존 문장이 화면과 충돌할 때만 replace_text=true로 수정합니다.
- 모든 근거 문장은 최대 한 문장으로 짧게 작성합니다.
- 제공된 event_index를 그대로 한 번씩 반환합니다.
"""


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
        value = value.strip().removeprefix("models/")
        if value and value not in result:
            result.append(value)
    return result


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


def _agent_key(value: str) -> str:
    return " ".join(str(value or "").strip().casefold().split())


def _apply_strict_kill_guard(parsed: dict) -> tuple[list[dict], int]:
    final_agent = str(parsed.get("agent") or "Unknown").strip()
    final_key = _agent_key(final_agent)
    agent_known = bool(final_key and final_key != "unknown")
    guarded = 0
    output: list[dict] = []

    for raw_item in parsed.get("events", []) or []:
        item = dict(raw_item)
        position = str(item.get("pov_killfeed_position") or "uncertain")
        attacker = str(item.get("killfeed_attacker_agent") or "").strip()
        victim = str(item.get("killfeed_victim_agent") or "").strip()
        row_confidence = float(item.get("killfeed_row_confidence") or 0.0)
        killfeed_visible = bool(item.get("killfeed_visible"))

        strict_support = (
            agent_known
            and killfeed_visible
            and position == "attacker"
            and _agent_key(attacker) == final_key
            and bool(victim)
            and _agent_key(victim) != final_key
            and row_confidence >= 0.75
        )
        item["killfeed_supports_pov_kill"] = bool(strict_support)
        item["strict_pov_kill_verified"] = bool(strict_support)

        if str(item.get("outcome") or "") == "kill" and not strict_support:
            guarded += 1
            item["outcome"] = "uncertain"
            item["confidence"] = min(float(item.get("confidence") or 0.0), 0.55)
            item["replace_text"] = True
            item["evidence"] = "같은 킬로그 행에서 POV 공격자 근거가 부족해 본인 처치로 확정하지 않음."
            item["corrected_observation"] = "킬로그에서 POV 플레이어의 본인 처치를 직접 확인하지 못했습니다."
            item["corrected_feedback"] = "처치 여부는 불확실로 두고 화면에서 직접 확인되는 플레이 요소만 평가합니다."
            note = str(item.get("killfeed_note") or "").strip()
            guard_note = "서버 동일행 가드 적용"
            item["killfeed_note"] = f"{note} · {guard_note}" if note else guard_note

        output.append(item)

    return output, guarded


def verify_combat_events(events: list[dict], summary: str = "", player_agent: str = "") -> dict:
    api_key = os.getenv("GEMINI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY_NOT_CONFIGURED")

    # Economy mode: only the first three important events are rechecked.
    scanned_events = list(events or [])[:3]
    if not scanned_events:
        return {
            "events": [],
            "model_used": "",
            "verified": False,
            "replace_summary": False,
            "corrected_summary": summary,
            "agent": "Unknown",
            "agent_confidence": 0.0,
            "agent_evidence": "검증할 장면이 없습니다.",
        }

    try:
        reference_sheets = build_ui_reference_sheets()
    except Exception as exc:
        print(f"[HUDVerifier] reference sheet skipped: {type(exc).__name__}: {exc}")
        reference_sheets = []

    client = genai.Client(api_key=api_key)
    contents: list = [
        PROMPT,
        f"기존 요원 추정: {str(player_agent or 'Unknown')[:60]}",
        "기존 요약: " + str(summary or "")[:700],
    ]

    # Keep reference accuracy, but use at most two sheets and medium resolution.
    for sheet_index, sheet in enumerate(reference_sheets[:2]):
        contents.append(f"UI REFERENCE SHEET {sheet_index + 1}")
        contents.append(types.Part.from_bytes(data=sheet, mime_type="image/jpeg"))

    for event in scanned_events:
        idx = int(event.get("event_index", 0))
        timestamp = str(event.get("timestamp") or "")[:20]
        observation = str(event.get("observation") or "")[:280]
        feedback = str(event.get("feedback") or "")[:300]
        frames = list(event.get("frames") or [])[:3]

        # Browser sends KILLFEED A, KILLFEED B, FULL HUD CONTEXT.
        # To save image tokens, use KILLFEED A + FULL HUD CONTEXT only.
        selected_frames = []
        if frames:
            selected_frames.append(("KILLFEED", frames[0]))
        if len(frames) >= 3:
            selected_frames.append(("FULL HUD CONTEXT", frames[2]))
        elif len(frames) >= 2:
            selected_frames.append(("FULL HUD CONTEXT", frames[-1]))

        contents.append(
            f"EVENT {idx} · {timestamp}\n"
            f"관찰: {observation}\n"
            f"피드백: {feedback}"
        )
        for label, image_bytes in selected_frames:
            contents.append(f"EVENT {idx} · {label}")
            contents.append(types.Part.from_bytes(data=image_bytes, mime_type="image/jpeg"))

    errors_seen: list[str] = []
    requested_indices = {int(event.get("event_index", 0)) for event in scanned_events}

    for model in _model_candidates():
        try:
            response = client.models.generate_content(
                model=model,
                contents=contents,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=CombatVerificationResponse,
                    temperature=0.0,
                    max_output_tokens=1200,
                    media_resolution=types.MediaResolution.MEDIA_RESOLUTION_MEDIUM,
                    thinking_config=types.ThinkingConfig(thinking_level="minimal"),
                    automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
                ),
            )
            if not response.text:
                raise RuntimeError(f"{model} empty response")

            parsed = CombatVerificationResponse.model_validate_json(response.text).model_dump()
            guarded_events, guarded_count = _apply_strict_kill_guard(parsed)
            filtered = [
                item for item in guarded_events
                if int(item.get("event_index", -1)) in requested_indices
            ]
            usage = _usage_metadata(response)
            if usage:
                print(
                    "[HUDVerifier] tokens · "
                    f"input={usage.get('prompt_tokens', 0)} · "
                    f"output={usage.get('output_tokens', 0)} · "
                    f"thinking={usage.get('thought_tokens', 0)} · "
                    f"total={usage.get('total_tokens', 0)}"
                )

            return {
                "events": filtered,
                "model_used": model,
                "verified": bool(filtered),
                "replace_summary": bool(parsed.get("replace_summary")),
                "corrected_summary": str(parsed.get("corrected_summary") or summary),
                "agent": str(parsed.get("agent") or "Unknown"),
                "agent_confidence": float(parsed.get("agent_confidence") or 0.0),
                "agent_evidence": str(parsed.get("agent_evidence") or ""),
                "portrait_reference_used": bool(reference_sheets),
                "ui_reference_sheets": min(2, len(reference_sheets)),
                "local_scan_count": len(scanned_events),
                "gemini_event_count": len(scanned_events),
                "selected_event_indices": sorted(requested_indices),
                "economy_mode": True,
                "token_usage": usage,
                "unified_hud_verification": True,
                "side_verification": True,
                "strict_pov_kill_guard": True,
                "strict_pov_kill_guard_downgraded": guarded_count,
                "media_resolution": "medium",
                "images_per_event": 2,
            }
        except (errors.APIError, ValueError, RuntimeError) as exc:
            text = f"{model}: {type(exc).__name__}: {exc}"
            errors_seen.append(text)
            print(f"[HUDVerifier] {text}")

    raise RuntimeError("HUD_VERIFICATION_FAILED: " + " | ".join(errors_seen[-2:]))
