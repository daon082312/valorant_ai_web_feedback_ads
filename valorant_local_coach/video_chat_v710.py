from __future__ import annotations

from video_chat import VideoCoachChat


class VideoCoachChatV710(VideoCoachChat):
    """Video coach context without death/KD inference."""

    @staticmethod
    def _context(report: dict) -> str:
        tier = report.get("tier_prediction") or {}
        combat = report.get("combat") or {}
        feedback = report.get("feedback") or []
        personal = report.get("personal_comparison") or {}
        return "\n".join([
            "[영상 분석 측정값]",
            f"예상 티어: {tier.get('tier_ko') or tier.get('tier')} / 티어점수: {tier.get('score')} / 신뢰도: {tier.get('confidence')}",
            f"에임: {report.get('aim_score')} / 헤드라인: {report.get('headline_score')} / 무빙 안정성: {report.get('movement_score')} / 스킬: {report.get('skill_score')}",
            f"발사 추정: {report.get('confirmed_shots')} / 사격구간: {report.get('shot_burst_count')} / 모드: {report.get('mode')}",
            f"오른쪽 위 킬로그 기반 내 킬 추정: {combat.get('kills', 0)} / 데스는 분석하지 않음",
            "피드백: " + " | ".join(str(x) for x in feedback[:6]),
            "개인학습 비교: " + " | ".join(str(x) for x in personal.get('messages', [])[:5]),
        ])[:6500]

    @staticmethod
    def fallback(question: str, report: dict, english: bool = False) -> str:
        if not report:
            return "Analyze a video first." if english else "먼저 영상을 분석해 주세요."
        q = question.casefold()
        if any(x in q for x in ("kill", "death", "kd", "k/d", "킬", "데스", "킬뎃", "킬댓")):
            kills = int((report.get("combat") or {}).get("kills") or 0)
            if english:
                return f"The top-right killfeed detector found {kills} of your highlighted kills. Deaths are not analyzed in this version."
            return f"오른쪽 위 킬로그의 내 킬 강조 테두리 기준으로 {kills}킬을 감지했습니다. 이 버전에서는 데스는 분석하지 않습니다."
        return VideoCoachChat.fallback(question, report, english)
