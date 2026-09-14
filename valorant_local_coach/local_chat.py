from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from dataclasses import dataclass


@dataclass(slots=True)
class ChatReply:
    text: str
    source: str
    model: str
    online: bool


class LocalCoachChat:
    """Local VALORANT coaching chat backed by Ollama with an offline fallback."""

    def __init__(self, config: dict):
        self.update_config(config)

    def update_config(self, config: dict) -> None:
        self.config = config
        self.base_url = str(config.get("ollama_url", "http://127.0.0.1:11434")).rstrip("/")
        self.model = str(config.get("ollama_model", "qwen3:4b")).strip() or "qwen3:4b"
        self.timeout = max(10.0, float(config.get("ollama_timeout_seconds", 90)))
        self.history_messages = max(2, min(20, int(config.get("chat_history_messages", 10))))

    def _request_json(self, path: str, payload: dict | None = None, timeout: float | None = None) -> dict:
        url = f"{self.base_url}{path}"
        data = None
        headers = {"Content-Type": "application/json"}
        if payload is not None:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(url, data=data, headers=headers, method="POST" if payload is not None else "GET")
        with urllib.request.urlopen(request, timeout=timeout or self.timeout) as response:
            raw = response.read().decode("utf-8", errors="replace")
        return json.loads(raw) if raw.strip() else {}

    def status(self) -> dict:
        try:
            data = self._request_json("/api/tags", timeout=2.5)
            models = []
            for item in data.get("models") or []:
                name = str(item.get("name") or item.get("model") or "").strip()
                if name:
                    models.append(name)
            return {
                "online": True,
                "model_installed": self.model in models or any(x.split(":")[0] == self.model.split(":")[0] for x in models),
                "models": models,
                "model": self.model,
            }
        except Exception as exc:
            return {"online": False, "model_installed": False, "models": [], "model": self.model, "error": str(exc)}

    @staticmethod
    def _compact_context(context: dict) -> str:
        summary = context.get("session_summary") or {}
        movement = summary.get("movement_metrics") or {}
        latest = context.get("latest_fight") or {}
        bindings = context.get("skill_bindings") or {}
        vision = context.get("vision") or {}
        loss = context.get("loss_analysis") or {}

        lines = [
            "[현재 로컬 측정 데이터 / Current local measurements]",
            f"세션 진행 중: {bool(context.get('session_running'))}",
            f"경기 결과 지정: {context.get('match_result')}",
            f"교전 수: {summary.get('fight_count', 0)} / 총 사격: {summary.get('shots', 0)} / 스킬 입력: {(summary.get('skill_usage') or {}).get('total', 0)}",
            f"WASD 중 사격 비율: {float(movement.get('moving_shot_ratio', 0)) * 100:.1f}%",
            f"Shift 사격 비율: {float(movement.get('walk_shot_ratio', 0)) * 100:.1f}%",
            f"Ctrl 사격 비율: {float(movement.get('crouch_shot_ratio', 0)) * 100:.1f}%",
            f"반대방향 탭 비율: {float(movement.get('opposite_tap_ratio', 0)) * 100:.1f}%",
            f"정지→첫 탄 중앙값(ms): {movement.get('median_stop_to_shot_ms')}",
            f"평균 교전 무빙 점수: {summary.get('average_fight_score')}",
            "우선 개선: " + "; ".join(str(x) for x in (summary.get("priorities") or [])[:4]),
        ]

        if vision:
            lines.extend([
                "[로컬 비전 / Local vision]",
                f"화면 적 후보 프레임: {vision.get('enemy_detection_frames', 0)} / 아군 후보 프레임: {vision.get('ally_detection_frames', 0)}",
                f"최대 적 후보: {vision.get('max_enemies', 0)} / 최대 아군 후보: {vision.get('max_allies', 0)}",
                f"미니맵 최대 적 후보: {vision.get('minimap_max_enemies', 0)} / 아군 후보: {vision.get('minimap_max_allies', 0)}",
                f"헤드라인 점수: {vision.get('headline_score')} / 평균 높이 오차(px): {vision.get('headline_error_px')}",
                f"판독 방식: {vision.get('detector')} / 적 윤곽색: {vision.get('enemy_outline_color')}",
            ])

        if latest:
            lines.extend([
                "[최근 교전]",
                f"제목: {latest.get('title', '')}",
                f"사격 {latest.get('shots', 0)}발 / 무빙 점수 {latest.get('movement_score', latest.get('score'))} / 스킬 타이밍 점수 {latest.get('skill_timing_score')}",
                f"이동사격 {float(latest.get('moving_shot_ratio', 0)) * 100:.1f}% / Shift {float(latest.get('walk_shot_ratio', 0)) * 100:.1f}% / Ctrl {float(latest.get('crouch_shot_ratio', 0)) * 100:.1f}%",
                f"정지→첫 탄: {latest.get('median_stop_to_shot_ms')}ms",
                "무빙 피드백: " + " | ".join(str(x) for x in (latest.get("movement_messages") or [])[:4]),
                "스킬 피드백: " + " | ".join(str(x) for x in (latest.get("skill_messages") or [])[:4]),
            ])
            uses = latest.get("skill_uses") or []
            if uses:
                lines.append(
                    "최근 스킬 타임라인: "
                    + " / ".join(
                        f"{u.get('label')}({u.get('binding')}, {u.get('relative_seconds')}s, {u.get('phase')})"
                        for u in uses[:8]
                    )
                )

        if loss and loss.get('causes'):
            lines.append('[패배 요인 후보 / Possible loss factors]')
            for item in (loss.get('causes') or [])[:4]:
                lines.append(f"{item.get('key')}: severity={item.get('severity')} evidence={item.get('evidence')}")

        if bindings:
            lines.append(
                "스킬 키 설정: "
                + " / ".join(
                    f"{item.get('label', slot)}={str(item.get('key', '-')).upper()}"
                    for slot, item in bindings.items()
                    if isinstance(item, dict)
                )
            )
        return "\n".join(lines)[:8500]

    @staticmethod
    def _system_prompt(context_text: str, language: str = 'ko') -> str:
        answer_language = 'English' if language == 'en' else '한국어'
        return (
            f"너는 VALORANT Local Coach의 코칭 AI다. 답변은 반드시 {answer_language}로 작성한다. 사용자의 실제 로컬 측정값을 근거로 간결하고 구체적으로 답한다.\n"
            "규칙:\n"
            "- 제공된 측정 데이터에 없는 킬/데스, 적 위치, 랭크, 에임 정확도, 스킬 적중 여부는 지어내지 않는다.\n"
            "- 적/아군 판독과 헤드라인은 색상/형태 기반 로컬 CV 휴리스틱이므로 확정 판정처럼 표현하지 않는다.\n"
            "- 이동사격, Shift/Ctrl, 정지→첫 탄, 반대방향 탭, 스킬 입력 타이밍, 헤드라인 점수를 우선 활용한다.\n"
            "- 패배 원인 질문에는 측정된 요인을 가능성 순서로 설명하고 인과를 확정하지 않는다.\n"
            "- 미니맵 후보 수만으로 전술적 위치나 로테이션이 좋았다고 단정하지 않는다.\n"
            "- 측정할 수 없는 부분은 현재 버전에서 측정하지 못했다고 명확히 말한다.\n"
            "- 답변은 보통 2~6문장으로 하고, 필요하면 연습 방법을 1~3개 제안한다.\n"
            "- 실시간 경기 중 즉시 행동 지시보다 교전/라운드 종료 후 복기와 훈련 조언 중심으로 답한다.\n\n"
            + context_text
        )

    def ask(self, user_text: str, context: dict, history: list[dict[str, str]]) -> ChatReply:
        text = str(user_text or "").strip()
        if not text:
            return ChatReply("질문을 입력해 주세요.", "builtin", self.model, False)

        if bool(self.config.get("local_ai_enabled", True)):
            try:
                context_text = self._compact_context(context)
                messages: list[dict[str, str]] = [{"role": "system", "content": self._system_prompt(context_text, str(context.get("language") or self.config.get("language", "ko")))}]
                for item in history[-self.history_messages:]:
                    role = str(item.get("role") or "")
                    content = str(item.get("content") or "").strip()
                    if role in {"user", "assistant"} and content:
                        messages.append({"role": role, "content": content[:4000]})
                messages.append({"role": "user", "content": text[:3000]})
                payload = {
                    "model": self.model,
                    "messages": messages,
                    "stream": False,
                    "options": {"temperature": 0.3, "num_ctx": 8192},
                }
                data = self._request_json("/api/chat", payload)
                answer = str(((data.get("message") or {}).get("content")) or "").strip()
                answer = re.sub(r"<think>.*?</think>", "", answer, flags=re.DOTALL | re.IGNORECASE).strip()
                if answer:
                    return ChatReply(answer, "ollama", self.model, True)
                raise RuntimeError("로컬 모델이 빈 답변을 반환했습니다.")
            except (urllib.error.URLError, TimeoutError, OSError, ValueError, RuntimeError, json.JSONDecodeError):
                fallback = self._fallback_reply(text, context)
                return ChatReply(
                    fallback + "\n\n(로컬 AI 연결 실패: Ollama가 실행 중인지와 모델 설치 여부를 확인하세요.)",
                    "builtin",
                    self.model,
                    False,
                )

        return ChatReply(self._fallback_reply(text, context), "builtin", self.model, False)

    @staticmethod
    def _fallback_reply(user_text: str, context: dict) -> str:
        q = user_text.casefold()
        summary = context.get("session_summary") or {}
        m = summary.get("movement_metrics") or {}
        latest = context.get("latest_fight") or {}
        vision = context.get("vision") or {}
        priorities = summary.get("priorities") or []
        english = str(context.get("language") or "ko") == "en"

        if any(word in q for word in ("loss", "lose", "lost", "패배", "졌", "원인")):
            loss = context.get("loss_analysis") or {}
            causes = loss.get("causes") or []
            if causes:
                joined = "; ".join(f"{x.get('key')}: {x.get('evidence')}" for x in causes[:3])
                return (f"The strongest measured loss-factor candidates are: {joined}. These are correlations from the recorded metrics, not proof of causation." if english else f"측정값 기준 패배 요인 후보는 {joined} 순입니다. 이는 기록된 지표의 가능성 분석이며 실제 패배 원인의 인과를 확정하지 않습니다.")

        if any(word in q for word in ("headline", "head line", "crosshair", "헤드라인", "머리높", "크로스헤어")):
            hs = vision.get("headline_score")
            err = vision.get("headline_error_px")
            frames = vision.get("enemy_detection_frames", 0)
            if hs is None:
                return ("I do not have enough confident enemy-outline detections to score head-line placement yet. Check the enemy outline color in Vision Settings and record more fights." if english else "아직 신뢰할 만한 적 윤곽 판독이 부족해 헤드라인 점수를 계산하지 못했습니다. 비전 설정의 적 윤곽 색상을 확인하고 교전을 더 기록해 주세요.")
            return (f"Estimated head-line score is {hs}/100 with about {err}px vertical error across {frames} enemy-detection frames. This is a color/shape heuristic, not exact head detection." if english else f"추정 헤드라인 점수는 {hs}/100이고 평균 세로 오차는 약 {err}px입니다. 적 판독 프레임은 {frames}개이며, 현재 값은 색상/형태 휴리스틱이라 실제 머리 검출과는 차이가 있을 수 있습니다.")

        if any(word in q for word in ("recent", "last fight", "최근", "방금", "교전")) and latest:
            score = latest.get("movement_score", latest.get("score", "--"))
            moving = float(latest.get("moving_shot_ratio", 0)) * 100
            stop = latest.get("median_stop_to_shot_ms")
            notes = (latest.get("movement_messages") or []) + (latest.get("skill_messages") or [])
            tail = " ".join(str(x) for x in notes[:2])
            if english:
                return f"The latest fight movement score is {score}/100, with {moving:.0f}% moving shots and a {stop if stop is not None else '--'} ms stop-to-first-shot median. The detailed rule feedback is currently recorded in the session data; use these numbers as the primary evidence."
            return f"최근 교전 무빙 점수는 {score}/100이고, 이동사격 비율은 {moving:.0f}%입니다. 정지→첫 탄 중앙값은 {stop if stop is not None else '--'}ms입니다. {tail}".strip()

        if any(word in q for word in ("movement", "strafe", "counter", "무빙", "이동", "스트레이프", "카운터")):
            moving = float(m.get("moving_shot_ratio", 0)) * 100
            walk = float(m.get("walk_shot_ratio", 0)) * 100
            crouch = float(m.get("crouch_shot_ratio", 0)) * 100
            stop = m.get("median_stop_to_shot_ms")
            advice = priorities[0] if priorities else ("Separate movement release from the first shot." if english else "WASD 정지와 첫 탄 타이밍을 반복 연습하세요.")
            if english:
                return f"This session has {moving:.0f}% WASD-moving shots, {walk:.0f}% Shift shots and {crouch:.0f}% Ctrl shots. Stop-to-first-shot median is {stop if stop is not None else '--'} ms. First priority: {advice}"
            return f"현재 세션은 WASD 중 사격 {moving:.0f}%, Shift 사격 {walk:.0f}%, Ctrl 사격 {crouch:.0f}%입니다. 정지→첫 탄 중앙값은 {stop if stop is not None else '--'}ms입니다. 우선순위는 '{advice}'입니다."

        if any(word in q for word in ("utility", "ability", "skill", "스킬", "유틸", "궁", "연막", "플래시")):
            usage = summary.get("skill_usage") or {}
            if latest:
                score = latest.get("skill_timing_score")
                if english:
                    return f"The program currently scores utility key timing. Latest fight utility-timing score: {score if score is not None else '--'}/100. It still cannot prove whether a flash hit or a smoke placement was good without a trained vision model."
                notes = latest.get("skill_messages") or []
                extra = " ".join(str(x) for x in notes[:2])
                return f"현재 프로그램은 스킬 키 입력 타이밍을 평가합니다. 최근 교전 스킬 타이밍 점수는 {score if score is not None else '--'}/100입니다. {extra} 실제 적중 여부나 연막 위치 평가는 아직 Vision 모델이 필요합니다."
            return (f"{usage.get('total', 0)} utility inputs were recorded this session. Actual hit/effect quality is not measured yet." if english else f"이번 세션에서 스킬 입력은 {usage.get('total', 0)}회 기록됐습니다. 실제 적중 여부나 연막 위치는 현재 버전에서 측정하지 못하고, 입력 타이밍만 평가합니다.")

        if any(word in q for word in ("improve", "practice", "fix", "뭐부터", "고쳐", "개선", "연습")):
            if priorities:
                return (("Current priority: " + " → ".join(str(x) for x in priorities[:3]) + ". Focus on only the first item for one session, then compare the metrics again.") if english else "현재 데이터 기준 우선 개선 순서는 " + " → ".join(str(x) for x in priorities[:3]) + " 입니다. 한 세션에서는 첫 번째 항목 하나만 집중해서 수치를 다시 비교해 보세요.")
            return ("There are not enough analyzed fights yet. Record several fights first." if english else "아직 분석된 교전이 충분하지 않습니다. 몇 번의 교전을 기록한 뒤 다시 물어보면 측정값 기준으로 우선순위를 정할 수 있습니다.")

        return (
            "You can ask about movement, the latest fight, utility timing, head-line placement, minimap candidate counts and measured loss factors. Ollama enables open-ended local chat; without it, this built-in coach answers from recorded metrics."
            if english else
            "로컬 코치에게 무빙, 최근 교전, 스킬 타이밍, 헤드라인, 미니맵 후보 수, 패배 요인과 개선 우선순위를 물어볼 수 있습니다. Ollama가 연결되면 자유 대화가 가능하고, 연결되지 않았을 때는 현재 측정 데이터 기반의 기본 답변을 제공합니다."
        )
