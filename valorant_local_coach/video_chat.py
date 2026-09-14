from __future__ import annotations

import json
import re
import urllib.request
from dataclasses import dataclass


@dataclass(slots=True)
class ChatReply:
    text: str
    source: str
    model: str


class VideoCoachChat:
    def __init__(self, config: dict):
        self.update_config(config)

    def update_config(self, config: dict) -> None:
        self.config = config
        self.base_url = str(config.get("ollama_url", "http://127.0.0.1:11434")).rstrip("/")
        self.model = str(config.get("ollama_model", "qwen3:4b")).strip() or "qwen3:4b"
        self.timeout = max(5.0, min(60.0, float(config.get("ollama_timeout_seconds", 45))))

    def _request(self, path: str, payload: dict | None = None, timeout: float | None = None) -> dict:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
        req = urllib.request.Request(self.base_url + path, data=data, headers={"Content-Type": "application/json"}, method="POST" if data else "GET")
        with urllib.request.urlopen(req, timeout=timeout or self.timeout) as res:
            return json.loads(res.read().decode("utf-8", errors="replace"))

    def status(self) -> dict:
        try:
            data = self._request("/api/tags", timeout=1.0)
            names = [str(x.get("name") or x.get("model") or "") for x in data.get("models", [])]
            installed = self.model in names or any(x.split(":")[0] == self.model.split(":")[0] for x in names)
            return {"online": True, "installed": installed}
        except Exception:
            return {"online": False, "installed": False}

    @staticmethod
    def _context(report: dict) -> str:
        tier = report.get("tier_prediction") or {}
        combat = report.get("combat") or {}
        feedback = report.get("feedback") or []
        personal = report.get("personal_comparison") or {}
        return "\n".join([
            "[영상 분석 측정값]",
            f"예상 티어: {tier.get('tier_ko') or tier.get('tier')} / 티어점수: {tier.get('score')} / 신뢰도: {tier.get('confidence')}",
            f"에임: {report.get('aim_score')} / 에임신뢰도: {report.get('aim_confidence')} / 에임방식: {report.get('aim_method')} / 헤드라인: {report.get('headline_score')} / 무빙 안정성: {report.get('movement_score')} / 스킬: {report.get('skill_score')}",
            f"발사 추정: {report.get('confirmed_shots')} / 사격구간: {report.get('shot_burst_count')} / 모드: {report.get('mode')}",
            f"내 킬 HUD 추정: {combat.get('kills', 0)} / 내 데스 HUD 추정: {combat.get('deaths', 0)}",
            "피드백: " + " | ".join(str(x) for x in feedback[:6]),
            "개인학습 비교: " + " | ".join(str(x) for x in personal.get('messages', [])[:5]),
        ])[:6500]

    @staticmethod
    def fallback(question: str, report: dict, english: bool = False) -> str:
        if not report:
            return "Analyze a video first." if english else "먼저 영상을 분석해 주세요."
        q = question.casefold()
        tier = report.get("tier_prediction") or {}
        if any(x in q for x in ("tier", "rank", "티어", "랭크")):
            return (f"Video-mechanics estimate: {tier.get('tier', 'Unrated')} ({float(tier.get('confidence',0))*100:.0f}% confidence). It is not your account rank." if english else f"영상 기계적 지표 기준 예상 티어는 {tier.get('tier_ko') or tier.get('tier') or '판정 불가'}이고 신뢰도는 {float(tier.get('confidence',0))*100:.0f}%입니다. 실제 계정 랭크를 조회한 값은 아닙니다.")
        if any(x in q for x in ("aim", "에임", "crosshair", "크로스")):
            aim = report.get('aim_score')
            head = report.get('headline_score')
            conf = float(report.get('aim_confidence') or 0.0)
            method = report.get('aim_method') or 'unknown'
            if aim is None:
                return ("There is not enough reliable shot-time enemy evidence to score aim. Check the enemy outline color and review the aim event markers." if english else "신뢰할 수 있는 발사 시점+적 윤곽 근거가 부족해 에임 점수를 확정하지 않았습니다. 적 윤곽색 설정과 에임 이벤트 장면을 확인해 주세요.")
            return (f"Aim {aim}/100, head-line {head}/100, confidence {conf*100:.0f}%, method {method}. Use the event-jump aim markers to verify the scored moments." if english else f"에임 {aim}/100, 헤드라인 {head}/100, 판정 신뢰도 {conf*100:.0f}%입니다. 판정 방식은 {method}이며, 이벤트 목록의 에임 시점으로 이동해 실제 장면과 점수가 맞는지 확인하세요.")
        if any(x in q for x in ("movement", "무빙", "spray", "스프레이")):
            return (f"Video-estimated movement/shot stability is {report.get('movement_score')}/100. This uses shot-time screen motion and burst length, not keyboard input." if english else f"영상 기반 무빙/사격 안정성은 {report.get('movement_score')}/100입니다. 실제 WASD를 읽은 값이 아니라 사격 순간 화면 움직임과 버스트 길이를 이용한 추정입니다.")
        fb = report.get("feedback") or []
        return " ".join(str(x) for x in fb[:3]) if fb else ("Not enough measurable evidence." if english else "측정 가능한 근거가 아직 부족합니다.")

    def ask(self, question: str, report: dict, history: list[dict[str, str]]) -> ChatReply:
        english = str(self.config.get("language", "ko")) == "en"
        if not bool(self.config.get("local_ai_enabled", True)):
            return ChatReply(self.fallback(question, report, english), "builtin", self.model)
        status = self.status()
        if not status.get("online") or not status.get("installed"):
            return ChatReply(self.fallback(question, report, english), "builtin", self.model)
        prompt = (
            "You are an offline VALORANT video review coach. Use only the measured video metrics below. "
            "Never claim the predicted tier is the user's real account rank. Kill/death and skill are HUD-based estimates. "
            "Movement score is video-estimated and does not reveal actual keyboard inputs. Aim has a method and confidence; do not overstate low-confidence aim scores. Answer in Korean unless language is en.\n\n"
            + self._context(report)
        )
        messages = [{"role": "system", "content": prompt}]
        messages += [x for x in history[-8:] if x.get("role") in {"user", "assistant"}]
        messages.append({"role": "user", "content": question[:2500]})
        try:
            data = self._request("/api/chat", {"model": self.model, "messages": messages, "stream": False, "options": {"temperature": 0.3}})
            text = str((data.get("message") or {}).get("content") or "").strip()
            text = re.sub(r"<think>.*?</think>", "", text, flags=re.S | re.I).strip()
            if text:
                return ChatReply(text, "ollama", self.model)
        except Exception:
            pass
        return ChatReply(self.fallback(question, report, english), "builtin", self.model)
