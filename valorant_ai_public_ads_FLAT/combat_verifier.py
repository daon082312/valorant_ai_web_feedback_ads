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
    side: Literal["attacker", "defender", "unknown"] = Field(
        description="이 장면에서 POV 플레이어의 실제 라운드 진영. 직접 HUD 근거가 없으면 unknown"
    )
    side_confidence: float = Field(ge=0, le=1)
    side_evidence: str = Field(description="공격/수비 판정의 직접 HUD 근거를 짧은 한국어 한 문장으로 작성")
    outcome: Literal["kill", "death", "assist", "survived", "no_combat", "uncertain"]
    confidence: float = Field(ge=0, le=1)
    evidence: str = Field(description="전투 결과 근거를 짧은 한국어 한 문장으로 작성")
    killfeed_visible: bool
    killfeed_supports_pov_kill: bool
    killfeed_attacker_agent: str = Field(
        default="",
        description="POV kill 판단에 사용한 정확히 같은 킬로그 행의 공격자 요원 영문명. 읽지 못하면 빈 문자열",
    )
    killfeed_victim_agent: str = Field(
        default="",
        description="POV kill 판단에 사용한 정확히 같은 킬로그 행의 피해자 요원 영문명. 읽지 못하면 빈 문자열",
    )
    pov_killfeed_position: Literal["attacker", "victim", "assist", "not_found", "uncertain"] = Field(
        default="uncertain",
        description="POV 요원이 해당 킬로그 행에서 실제로 위치한 역할",
    )
    killfeed_row_confidence: float = Field(
        default=0.0,
        ge=0,
        le=1,
        description="공격자와 피해자를 같은 행에서 정확히 읽었다는 신뢰도",
    )
    killfeed_note: str = Field(description="킬로그에서 실제로 읽은 내용을 짧게 작성")
    ability_name: str | None = Field(default=None, description="이 장면에서 실제 사용이 확인된 최종 요원의 스킬 영문명. 불확실하면 null")
    ability_confidence: float = Field(default=0.0, ge=0, le=1)
    ability_evidence: str = Field(default="", description="스킬 판정의 HUD/화면 근거를 짧게 작성")
    replace_text: bool
    corrected_observation: str = Field(description="검증 결과를 반영한 관찰 한 문장")
    corrected_feedback: str = Field(description="검증 결과를 반영한 코칭 한 문장")


class CombatVerificationResponse(BaseModel):
    agent: str = Field(description="FULL HUD CONTEXT의 실제 능력 아이콘 세트로 판정한 POV 플레이어 요원 영문명. 불확실하면 Unknown")
    agent_confidence: float = Field(ge=0, le=1)
    agent_evidence: str = Field(description="요원 판정 근거를 짧은 한국어 한 문장으로 작성")
    events: list[CombatVerificationItem]
    replace_summary: bool
    corrected_summary: str = Field(description="필요한 경우에만 잘못된 킬/데스/요원/공격수비 전제를 바로잡은 기존 요약")


PROMPT = """
당신은 VALORANT의 HUD를 정밀 판독하는 검증기입니다.
이번 요청의 목적은 4가지를 한 번에 다시 확인하는 것입니다.
1) POV 플레이어의 요원
2) 각 주요 장면에서 실제 사용된 스킬
3) POV 플레이어의 킬/사망/생존
4) 각 장면에서 POV 플레이어가 공격팀(Attacker)인지 수비팀(Defender)인지

가장 먼저 제공되는 UI REFERENCE SHEET들을 반드시 기준으로 사용하세요.
각 요원 칸에는 실제 게임의 killfeedPortrait와 공식 ability displayIcon 4개가 요원명/스킬명과 함께 있습니다.
당신의 기억보다 참조표와 영상 HUD가 우선입니다.

[공격팀 / 수비팀 판정 — 매우 중요]
- 각 이벤트의 FULL HUD CONTEXT에는 전체 장면, 하단 능력 HUD, 그리고 상단 중앙 ROUND ROLE HUD가 확대되어 있습니다.
- 공격/수비는 반드시 해당 장면의 ROUND ROLE HUD를 먼저 읽어서 판정하세요.
- 상단 중앙 game info UI의 Attack/Defend 역할 아이콘이 가장 중요한 직접 근거입니다.
- 빨강/파랑/초록 등 팀 색상만으로 공격팀/수비팀을 추측하지 마세요. 색은 역할 자체의 확정 근거가 아닙니다.
- 킬로그의 좌우 위치만으로 공격팀/수비팀을 추측하지 마세요.
- POV가 Spike를 소지하고 있거나 직접 Plant를 수행하는 화면은 attacker의 강한 보조 근거입니다.
- POV가 직접 Defuse를 수행하는 화면은 defender의 강한 보조 근거입니다.
- 단순히 Spike가 설치되어 있다는 사실만으로 POV가 공격팀인지 수비팀인지 정하지 마세요.
- 영상이 여러 라운드 또는 진영 교대 구간을 포함할 수 있으므로 side는 이벤트마다 독립적으로 판정하세요.
- HUD가 잘렸거나 역할 아이콘을 읽을 수 없고 Plant/Defuse 같은 직접 근거도 없다면 side="unknown"으로 두세요.
- 기존 observation/feedback이 공격/수비를 반대로 전제한 경우 replace_text=true로 하고 그 전제를 바로잡으세요.

[요원 판정]
- 기존 분석의 요원명은 참고값일 뿐 정답이 아닙니다.
- 각 이벤트의 FULL HUD CONTEXT 아래쪽에는 같은 시점의 하단 능력 HUD가 크게 확대되어 있습니다.
- 여러 이벤트에서 반복해서 보이는 4개 능력 아이콘 세트를 UI REFERENCE SHEET와 대조해 최종 요원을 정하세요.
- 한 아이콘이나 손 모양 하나만 보고 확정하지 마세요. 가능하면 서로 다른 2개 이상의 능력 아이콘 일치를 확인하세요.
- 참조표와 충분히 맞지 않으면 agent="Unknown"으로 두세요.

[스킬 판정]
- 먼저 최종 요원을 정한 뒤 그 요원의 공식 4개 스킬만 후보로 사용하세요.
- 이벤트의 FULL HUD CONTEXT에서 직전→직후 HUD 변화, 능력 아이콘의 사용/쿨다운 상태, 화면의 실제 스킬 효과를 함께 봅니다.
- 총격, 재장전, 무기 교체, 일반 이동을 스킬로 오인하지 마세요.
- 실제 사용이 확실하지 않으면 ability_name=null, ability_confidence를 낮게 두세요.
- 다른 요원의 스킬 이름은 절대로 반환하지 마세요.

[킬로그 판정 — 최우선 안전 규칙]
- KILLFEED A는 우측 킬로그 전체 세로 스택을 넓게 보여줍니다.
- KILLFEED B는 다른 킬 때문에 아래로 밀린 오래된 행을 놓치지 않도록 아래쪽 스택을 더 크게 보여줍니다.
- 동시에 여러 킬이 나면 최신 킬이 위에 추가되고 기존 행이 아래로 밀릴 수 있으므로 모든 행을 위에서 아래까지 각각 읽으세요.
- 각 킬로그 행을 독립적으로 읽고, 한 행의 공격자 초상화와 피해자 초상화를 서로 다른 행과 절대로 섞지 마세요.
- POV의 본인 킬(outcome="kill")은 아래 조건을 모두 만족할 때만 허용합니다.
  1) 최종 POV 요원이 Unknown이 아니다.
  2) 정확히 같은 킬로그 행에서 POV 요원의 killfeedPortrait가 공격자 위치에 있다.
  3) 그 같은 행에서 별도의 피해자 killfeedPortrait를 실제로 읽을 수 있다.
  4) 공격자와 피해자 위치 관계가 한 행의 정상적인 킬로그 구조와 일치한다.
  5) killfeed_row_confidence가 충분히 높다.
- 이 조건을 만족할 때만 pov_killfeed_position="attacker", killfeed_supports_pov_kill=true로 하세요.
- POV 요원이 피해자 위치에 있으면 pov_killfeed_position="victim"이며 절대로 본인 킬이 아닙니다.
- POV 요원이 작은 어시스트 아이콘에만 있으면 pov_killfeed_position="assist"이며 절대로 본인 킬이 아닙니다.
- POV 요원 초상화가 킬로그의 다른 행 어딘가에 있다는 사실만으로 본인 킬로 판정하지 마세요.
- 공격자 요원을 못 읽었거나 피해자 요원을 못 읽었거나 같은 행인지 불확실하면 killfeed_supports_pov_kill=false, outcome="uncertain"을 우선하세요.
- 킬로그에 팀원의 킬이 보이고 화면 중앙에서 POV가 적을 쏘고 있어도, POV가 공격자인 같은 행을 확인하지 못하면 본인 킬이 아닙니다.
- 화면 중앙의 적 사라짐, 명중 이펙트, 교전 우세, 크로스헤어 반응만으로 kill을 확정하지 마세요.
- 공격팀/수비팀(side) 정보는 킬로그의 공격자/피해자 식별과 별개입니다. attacker 진영이라고 킬로그 attacker 위치라는 뜻이 아닙니다.
- killfeed_attacker_agent와 killfeed_victim_agent에는 판정에 사용한 바로 그 한 행의 요원명을 기록하세요. 읽지 못하면 빈 문자열로 두세요.

[사망 판정]
- POV death는 Combat Report, 관전자 전환, 리스폰/사망 화면 등 직접 증거가 있어야 합니다.
- 피격, 저체력, 붉은 화면, 흔들림만으로 death라고 하지 마세요.
- 직후에도 정상 HUD/무기/체력이 유지되며 플레이하면 survived입니다.

[출력]
- 제공된 모든 이벤트를 event_index 그대로 정확히 한 번씩 반환하세요.
- side/side_confidence/side_evidence는 모든 이벤트에 반드시 작성하세요.
- 킬로그가 보이는 이벤트는 killfeed_attacker_agent, killfeed_victim_agent, pov_killfeed_position, killfeed_row_confidence를 반드시 채우세요.
- 기존 observation/feedback이 실제 화면과 충돌할 때만 replace_text=true로 수정하세요.
- 기존 문장이 POV의 킬을 주장하지만 위의 동일 행 공격자 조건을 만족하지 못하면 replace_text=true로 하고 본인 킬 주장을 제거하세요.
- 공격/수비 전제가 틀린 경우에는 전투 결과가 맞더라도 replace_text=true로 수정하세요.
- 전체 summary에 공격/수비 또는 POV 킬을 잘못 전제한 표현이 있으면 replace_summary=true로 고치세요.
- evidence 문장은 짧게 유지하세요.
- 확실하지 않은 내용은 unknown/uncertain/null로 두는 것이 잘못된 단정보다 낫습니다.
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
    """Do not trust a model-level 'kill' unless same-row attacker evidence exists."""
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
            item["evidence"] = "같은 킬로그 행에서 POV가 공격자인 직접 근거가 부족해 본인 킬로 인정하지 않음."
            item["corrected_observation"] = "킬로그에서 POV 플레이어가 공격자인 동일 행을 확인하지 못해 본인 처치로 확정하지 않습니다."
            item["corrected_feedback"] = "처치 여부는 불확실로 두고 포지셔닝·에임·스킬 사용처럼 화면에서 직접 확인되는 요소만 평가합니다."
            note = str(item.get("killfeed_note") or "").strip()
            guard_note = "서버 동일행 가드: POV 공격자+피해자 동시 확인 실패"
            item["killfeed_note"] = f"{note} · {guard_note}" if note else guard_note

        output.append(item)

    return output, guarded


def verify_combat_events(events: list[dict], summary: str = "", player_agent: str = "") -> dict:
    api_key = os.getenv("GEMINI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY_NOT_CONFIGURED")

    scanned_events = list(events or [])[:6]
    if not scanned_events:
        return {
            "events": [], "model_used": "", "verified": False,
            "replace_summary": False, "corrected_summary": summary,
            "agent": "Unknown", "agent_confidence": 0.0,
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
        f"기존 1차 분석 요원(정답 아님): {str(player_agent or 'Unknown')[:80]}",
        "기존 전체 요약:\n" + str(summary or "")[:1200],
    ]

    for sheet_index, sheet in enumerate(reference_sheets[:2]):
        contents.append(f"UI REFERENCE SHEET {sheet_index + 1}: 실제 killfeedPortrait + 공식 스킬 아이콘")
        contents.append(types.Part.from_bytes(data=sheet, mime_type="image/jpeg"))

    for event in scanned_events:
        idx = int(event.get("event_index", 0))
        timestamp = str(event.get("timestamp") or "")[:20]
        observation = str(event.get("observation") or "")[:450]
        feedback = str(event.get("feedback") or "")[:500]
        frames = list(event.get("frames") or [])[:3]

        contents.append(
            f"EVENT {idx} · timestamp={timestamp}\n"
            f"기존 관찰: {observation}\n"
            f"기존 피드백: {feedback}\n"
            "이미지 순서: KILLFEED A(전체 스택), KILLFEED B(아래쪽 스택), FULL HUD CONTEXT(전체 장면+하단 능력 HUD+상단 공격/수비 HUD 확대)."
        )
        labels = [
            "KILLFEED A · 전체 세로 스택",
            "KILLFEED B · 아래로 밀린 행 확대",
            "FULL HUD CONTEXT · 전체 장면 + 능력 HUD + ROUND ROLE HUD 확대",
        ]
        for frame_no, image_bytes in enumerate(frames):
            contents.append(f"EVENT {idx} · {labels[frame_no] if frame_no < len(labels) else 'FRAME'}")
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
                    max_output_tokens=2100,
                    media_resolution=types.MediaResolution.MEDIA_RESOLUTION_HIGH,
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
            if guarded_count:
                print(f"[HUDVerifier] strict POV kill guard downgraded {guarded_count} false/weak kill claim(s)")

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
                "ui_reference_sheets": len(reference_sheets),
                "local_scan_count": len(scanned_events),
                "gemini_event_count": len(scanned_events),
                "selected_event_indices": sorted(requested_indices),
                "economy_mode": True,
                "token_usage": usage,
                "unified_hud_verification": True,
                "side_verification": True,
                "strict_pov_kill_guard": True,
                "strict_pov_kill_guard_downgraded": guarded_count,
            }
        except (errors.APIError, ValueError, RuntimeError) as exc:
            text = f"{model}: {type(exc).__name__}: {exc}"
            errors_seen.append(text)
            print(f"[HUDVerifier] {text}")

    raise RuntimeError("HUD_VERIFICATION_FAILED: " + " | ".join(errors_seen[-2:]))
