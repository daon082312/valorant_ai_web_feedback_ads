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


class CheatAssessment(BaseModel):
    rating: Literal[
        "no_clear_evidence",
        "insufficient_evidence",
        "suspicious",
        "strongly_suspicious",
    ] = Field(description="POV 플레이어의 비정상 플레이 의심도. 핵 사용 확정 판정이 아님")
    suspicion_score: int = Field(
        ge=0,
        le=100,
        description="영상에서 보이는 이상 패턴의 상대적 의심도 점수. 치트 사용 확률이 아님",
    )
    confidence: float = Field(ge=0, le=1)
    indicators: list[Literal[
        "aim_snap",
        "wall_tracking",
        "information_anomaly",
        "unnatural_target_switching",
        "trigger_like_timing",
        "none",
    ]] = Field(default_factory=list, max_length=6)
    reviewed_event_indices: list[int] = Field(default_factory=list, max_length=6)
    evidence: list[str] = Field(
        default_factory=list,
        max_length=4,
        description="실제 영상에서 관찰한 이상 신호. 각 문장은 장면 번호나 시점을 포함해 짧게 작성",
    )
    benign_explanations: list[str] = Field(
        default_factory=list,
        max_length=4,
        description="사운드, 팀 콜, 미니맵, 리콘, 높은 숙련도 등 정상적으로 설명 가능한 대안",
    )
    summary: str = Field(description="왜 이 등급인지 한국어 1~2문장으로 작성")


class CombatVerificationResponse(BaseModel):
    agent: str = Field(description="FULL HUD CONTEXT의 실제 능력 아이콘 세트로 판정한 POV 플레이어 요원 영문명. 불확실하면 Unknown")
    agent_confidence: float = Field(ge=0, le=1)
    agent_evidence: str = Field(description="요원 판정 근거를 짧은 한국어 한 문장으로 작성")
    events: list[CombatVerificationItem]
    cheat_assessment: CheatAssessment
    replace_summary: bool
    corrected_summary: str = Field(description="필요한 경우에만 잘못된 킬/데스/요원/공격수비 전제를 바로잡은 기존 요약")


PROMPT = """
당신은 VALORANT의 HUD와 POV 플레이를 정밀 판독하는 검증기입니다.
이번 요청의 목적은 5가지를 한 번에 다시 확인하는 것입니다.
1) POV 플레이어의 요원
2) 각 주요 장면에서 실제 사용된 스킬
3) POV 플레이어의 킬/사망/생존
4) 각 장면에서 POV 플레이어가 공격팀(Attacker)인지 수비팀(Defender)인지
5) POV 플레이에서 치트 사용을 의심할 만한 비정상 패턴이 반복되는지

가장 먼저 제공되는 UI REFERENCE SHEET들을 반드시 기준으로 사용하세요.
각 요원 칸에는 실제 게임의 killfeedPortrait와 공식 ability displayIcon 4개가 요원명/스킬명과 함께 있습니다.
당신의 기억보다 참조표와 영상 HUD가 우선입니다.

[공격팀 / 수비팀 판정 — 매우 중요]
- 각 이벤트의 FULL HUD CONTEXT에는 전체 장면, 하단 능력 HUD, 상단 중앙 ROUND ROLE HUD가 확대되어 있습니다.
- 공격/수비는 반드시 해당 장면의 ROUND ROLE HUD를 먼저 읽어서 판정하세요.
- 상단 중앙 game info UI의 Attack/Defend 역할 아이콘이 가장 중요한 직접 근거입니다.
- 빨강/파랑/초록 등 팀 색상만으로 공격팀/수비팀을 추측하지 마세요.
- 킬로그의 좌우 위치만으로 공격팀/수비팀을 추측하지 마세요.
- POV가 Spike를 소지하거나 직접 Plant를 수행하면 attacker의 강한 보조 근거입니다.
- POV가 직접 Defuse를 수행하면 defender의 강한 보조 근거입니다.
- 단순히 Spike가 설치되어 있다는 사실만으로 진영을 정하지 마세요.
- 영상이 여러 라운드 또는 진영 교대 구간을 포함할 수 있으므로 side는 이벤트마다 독립적으로 판정하세요.
- HUD가 잘렸거나 직접 근거가 없다면 side="unknown"으로 두세요.
- 기존 observation/feedback이 공격/수비를 반대로 전제한 경우 replace_text=true로 하고 바로잡으세요.

[요원 판정]
- 기존 분석의 요원명은 참고값일 뿐 정답이 아닙니다.
- 여러 이벤트에서 반복되는 하단 능력 아이콘 세트를 UI REFERENCE SHEET와 대조해 최종 요원을 정하세요.
- 한 가지 아이콘이나 손 모양만으로 확정하지 마세요. 가능하면 서로 다른 2개 이상의 능력 아이콘 일치를 확인하세요.
- 충분히 맞지 않으면 agent="Unknown"으로 두세요.

[스킬 판정]
- 최종 요원을 정한 뒤 그 요원의 공식 4개 스킬만 후보로 사용하세요.
- 직전→직후 HUD 변화, 능력 아이콘의 사용/쿨다운 상태, 실제 스킬 효과를 함께 봅니다.
- 총격, 재장전, 무기 교체, 일반 이동을 스킬로 오인하지 마세요.
- 실제 사용이 확실하지 않으면 ability_name=null로 두세요.

[킬로그 판정 — 최우선 안전 규칙]
- KILLFEED A는 우측 킬로그 전체 세로 스택, KILLFEED B는 아래로 밀린 오래된 행을 더 크게 보여줍니다.
- 각 킬로그 행을 독립적으로 읽고 다른 행의 공격자/피해자를 절대로 섞지 마세요.
- POV의 본인 킬(outcome="kill")은 아래 조건을 모두 만족할 때만 허용합니다.
  1) 최종 POV 요원이 Unknown이 아니다.
  2) 정확히 같은 킬로그 행에서 POV 요원의 killfeedPortrait가 공격자 위치에 있다.
  3) 그 같은 행에서 별도의 피해자 killfeedPortrait를 읽을 수 있다.
  4) 공격자와 피해자 위치 관계가 정상적인 한 행 구조와 일치한다.
  5) killfeed_row_confidence가 충분히 높다.
- POV가 피해자 위치면 victim, 작은 어시스트 아이콘에만 있으면 assist이며 절대로 본인 킬이 아닙니다.
- POV 초상화가 다른 행 어딘가에 보인다는 사실만으로 본인 킬로 판정하지 마세요.
- 화면 중앙의 적 사라짐, 명중 이펙트, 교전 우세, 크로스헤어 반응만으로 kill을 확정하지 마세요.
- attacker 진영이라는 뜻과 killfeed 공격자 위치라는 뜻을 혼동하지 마세요.

[사망 판정]
- POV death는 Combat Report, 관전자 전환, 리스폰/사망 화면 등 직접 증거가 있어야 합니다.
- 피격, 저체력, 붉은 화면, 흔들림만으로 death라고 하지 마세요.
- 직후에도 정상 HUD/무기/체력이 유지되며 플레이하면 survived입니다.

[치트 의심도 판정 — 확정 판정 금지]
- 이 평가는 업로드된 POV 플레이어의 행동만 대상으로 합니다. 상대 플레이어의 치트 사용 여부는 이 영상으로 판단하지 마세요.
- FULL HUD CONTEXT 아래에 AIM MOTION STRIP이 있으면, 그 스트립은 짧은 시간 간격의 연속 프레임이며 시간 순서는 왼쪽→오른쪽, 위→아래입니다. 에임 이동은 반드시 이 연속성을 보고 판단하세요.
- suspicion_score는 치트 사용 확률이 아니라 영상에서 관찰되는 이상 패턴의 상대적 점수입니다.
- 단 한 번의 빠른 플릭, 헤드샷, 좋은 프리파이어, 높은 헤드라인 유지, 빠른 반응만으로 suspicious 이상을 주지 마세요.
- 정상적인 사운드 정보, 팀원 콜, 미니맵, 리콘/드론/표식, 이전에 본 적 위치, 높은 게임센스가 설명할 수 있는 행동은 benign_explanations에 반드시 고려하세요.
- aim_snap: 여러 연속 프레임에서 표적 사이로 비정상적으로 즉각적인 에임 이동이 반복되고, 자연스러운 미세 조정이 거의 보이지 않을 때만 근거로 사용하세요.
- wall_tracking: 적이 보이지 않는 동안 벽/엄폐물 뒤 위치를 지속적으로 따라가는 패턴이 반복될 때만 고려하세요. 한 번의 프리에임은 근거가 아닙니다.
- information_anomaly: 화면상 확인 가능한 정보 없이 반복적으로 정확한 사전 대응을 하는 경우입니다. 단, 오디오는 이 검증 이미지에 없으므로 사운드 가능성을 배제할 수 없다는 점을 반드시 감안하세요.
- unnatural_target_switching: 여러 표적 사이의 전환이 반복적으로 기계적이고 지나치게 일정하게 나타날 때만 고려하세요.
- trigger_like_timing: 프레임만으로 정밀 발사 지연을 측정할 수 없으므로 매우 보수적으로 사용하세요.
- strongly_suspicious는 최소 2개의 서로 다른 주요 장면에서 반복되는 이상 패턴과 최소 2개의 독립적인 근거가 있을 때만 사용하세요.
- suspicious도 단일 장면의 우연한 플레이만으로 사용하지 마세요. 근거가 부족하면 insufficient_evidence를 선택하세요.
- 명확한 반복 패턴이 없으면 no_clear_evidence를 사용하세요.
- 결과는 신고, 제재, 비난의 근거가 될 수 있는 확정 판정이 아닙니다. 서버가 추가로 보수적인 후처리를 적용합니다.

[출력]
- 제공된 모든 이벤트를 event_index 그대로 정확히 한 번씩 반환하세요.
- side/side_confidence/side_evidence는 모든 이벤트에 작성하세요.
- 킬로그가 보이는 이벤트는 killfeed_attacker_agent, killfeed_victim_agent, pov_killfeed_position, killfeed_row_confidence를 채우세요.
- 기존 문장이 POV의 킬을 주장하지만 동일 행 공격자 조건을 만족하지 못하면 replace_text=true로 하고 본인 킬 주장을 제거하세요.
- 공격/수비 전제가 틀린 경우에도 replace_text=true로 수정하세요.
- 전체 summary에 공격/수비 또는 POV 킬을 잘못 전제한 표현이 있으면 replace_summary=true로 고치세요.
- cheat_assessment의 evidence에는 실제로 검토한 EVENT 번호를 포함하세요.
- 불확실하면 unknown/uncertain/null/insufficient_evidence를 사용하세요. 잘못된 단정보다 보수적 판정이 우선입니다.
"""


CHEAT_DISCLAIMER = (
    "이 평가는 영상에서 보이는 POV 플레이 패턴의 참고용 의심도이며 치트 사용 확정 판정이 아닙니다. "
    "사운드·팀 콜·미니맵 정보와 전체 경기 맥락이 빠져 있을 수 있으므로 신고나 제재의 단독 근거로 사용하면 안 됩니다."
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


def _apply_conservative_cheat_guard(parsed: dict, dense_motion_event_indices: list[int]) -> tuple[dict, bool]:
    """Prevent a short POV clip from being presented as a definitive cheat verdict."""
    assessment = dict(parsed.get("cheat_assessment") or {})
    rating = str(assessment.get("rating") or "insufficient_evidence")
    score = int(assessment.get("suspicion_score") or 0)
    confidence = float(assessment.get("confidence") or 0.0)
    indicators = [str(x) for x in assessment.get("indicators") or []]
    evidence = [str(x) for x in assessment.get("evidence") or [] if str(x).strip()]
    benign = [str(x) for x in assessment.get("benign_explanations") or [] if str(x).strip()]
    reviewed = []
    for value in assessment.get("reviewed_event_indices") or []:
        try:
            idx = int(value)
        except (TypeError, ValueError):
            continue
        if idx not in reviewed:
            reviewed.append(idx)

    changed = False
    dense_set = set(dense_motion_event_indices)
    reviewed_dense = [idx for idx in reviewed if idx in dense_set]
    repeated_evidence = len(set(reviewed)) >= 2 and len(evidence) >= 2
    aim_only_indicators = set(indicators).issubset({
        "aim_snap", "unnatural_target_switching", "trigger_like_timing", "none"
    })

    if rating == "strongly_suspicious":
        if confidence < 0.82 or not repeated_evidence or len(reviewed_dense) < 2:
            rating = "suspicious" if confidence >= 0.68 and repeated_evidence else "insufficient_evidence"
            score = min(score, 74 if rating == "suspicious" else 49)
            changed = True

    if rating == "suspicious":
        if confidence < 0.65 or len(set(reviewed)) < 2:
            rating = "insufficient_evidence"
            score = min(score, 49)
            changed = True
        elif aim_only_indicators and not reviewed_dense:
            rating = "insufficient_evidence"
            score = min(score, 45)
            changed = True

    if not dense_motion_event_indices and rating in {"suspicious", "strongly_suspicious"}:
        rating = "insufficient_evidence"
        score = min(score, 45)
        changed = True

    if rating == "no_clear_evidence":
        score = min(score, 34)
    elif rating == "insufficient_evidence":
        score = min(score, 49)
    elif rating == "suspicious":
        score = max(50, min(score, 79))
    else:
        score = max(80, min(score, 100))

    if not benign:
        benign = ["사운드·팀 콜·미니맵·스킬 정보가 이 정지 프레임 검증에 충분히 포함되지 않았을 수 있습니다."]

    if changed:
        summary = (
            "영상만으로 치트 사용을 확정할 근거가 충분하지 않아 서버가 판정을 보수적으로 낮췄습니다. "
            "반복되는 이상 패턴이 더 많은 독립 장면에서 확인되어야 합니다."
        )
    else:
        summary = str(assessment.get("summary") or "영상에서 확인 가능한 범위만 보수적으로 평가했습니다.")

    return {
        "rating": rating,
        "suspicion_score": score,
        "confidence": max(0.0, min(1.0, confidence)),
        "indicators": indicators[:6] or ["none"],
        "reviewed_event_indices": reviewed[:6],
        "dense_motion_event_indices": dense_motion_event_indices,
        "evidence": evidence[:4],
        "benign_explanations": benign[:4],
        "summary": summary,
        "disclaimer": CHEAT_DISCLAIMER,
        "server_guard_applied": changed,
    }, changed


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
            "cheat_assessment": {
                "rating": "insufficient_evidence",
                "suspicion_score": 0,
                "confidence": 0.0,
                "indicators": ["none"],
                "reviewed_event_indices": [],
                "dense_motion_event_indices": [],
                "evidence": [],
                "benign_explanations": [],
                "summary": "검증할 장면이 없습니다.",
                "disclaimer": CHEAT_DISCLAIMER,
                "server_guard_applied": False,
            },
        }

    try:
        reference_sheets = build_ui_reference_sheets()
    except Exception as exc:
        print(f"[HUDVerifier] reference sheet skipped: {type(exc).__name__}: {exc}")
        reference_sheets = []

    dense_motion_event_indices = [
        int(event.get("event_index", 0))
        for event in scanned_events
        if bool(event.get("dense_motion_strip"))
    ]

    client = genai.Client(api_key=api_key)
    contents: list = [
        PROMPT,
        f"기존 1차 분석 요원(정답 아님): {str(player_agent or 'Unknown')[:80]}",
        "기존 전체 요약:\n" + str(summary or "")[:1200],
        "AIM MOTION STRIP이 포함된 이벤트: " + (
            ", ".join(map(str, dense_motion_event_indices)) if dense_motion_event_indices else "없음"
        ),
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
        has_dense = bool(event.get("dense_motion_strip"))

        contents.append(
            f"EVENT {idx} · timestamp={timestamp} · AIM_MOTION_STRIP={has_dense}\n"
            f"기존 관찰: {observation}\n"
            f"기존 피드백: {feedback}\n"
            "이미지 순서: KILLFEED A(전체 스택), KILLFEED B(아래쪽 스택), "
            "FULL HUD CONTEXT(전체 장면+하단 능력 HUD+상단 공격/수비 HUD 확대"
            + ("+하단 AIM MOTION STRIP)." if has_dense else ").")
        )
        labels = [
            "KILLFEED A · 전체 세로 스택",
            "KILLFEED B · 아래로 밀린 행 확대",
            "FULL HUD CONTEXT · 전체 장면 + 능력 HUD + ROUND ROLE HUD + 선택적 AIM MOTION STRIP",
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
                    max_output_tokens=2600,
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
            cheat_assessment, cheat_guarded = _apply_conservative_cheat_guard(
                parsed,
                dense_motion_event_indices,
            )
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
            if cheat_guarded:
                print("[HUDVerifier] conservative cheat guard downgraded an overconfident cheat-suspicion result")

            return {
                "events": filtered,
                "model_used": model,
                "verified": bool(filtered),
                "replace_summary": bool(parsed.get("replace_summary")),
                "corrected_summary": str(parsed.get("corrected_summary") or summary),
                "agent": str(parsed.get("agent") or "Unknown"),
                "agent_confidence": float(parsed.get("agent_confidence") or 0.0),
                "agent_evidence": str(parsed.get("agent_evidence") or ""),
                "cheat_assessment": cheat_assessment,
                "portrait_reference_used": bool(reference_sheets),
                "ui_reference_sheets": len(reference_sheets),
                "local_scan_count": len(scanned_events),
                "gemini_event_count": len(scanned_events),
                "selected_event_indices": sorted(requested_indices),
                "dense_motion_event_indices": dense_motion_event_indices,
                "economy_mode": True,
                "token_usage": usage,
                "unified_hud_verification": True,
                "side_verification": True,
                "cheat_suspicion_verification": True,
                "strict_pov_kill_guard": True,
                "strict_pov_kill_guard_downgraded": guarded_count,
            }
        except (errors.APIError, ValueError, RuntimeError) as exc:
            text = f"{model}: {type(exc).__name__}: {exc}"
            errors_seen.append(text)
            print(f"[HUDVerifier] {text}")

    raise RuntimeError("HUD_VERIFICATION_FAILED: " + " | ".join(errors_seen[-2:]))
