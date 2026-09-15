# VALORANT Video Coach v7.16

## v7.16 변경
- AI 질문 입력창을 native Tk Entry로 재구성: Enter/전송 버튼 모두 직접 `send_chat()`에 연결
- v7.16 전용 응답 큐/폴러 사용, Ollama 실패 시 내장 코치 fallback
- 킬로그 detector v2: 새 강조 행을 2개 샘플 프레임에서 확인한 뒤 킬 확정
- perceptual row hash를 실제 중복 제거에 사용
- 기존 킬로그가 아래로 밀리거나 애니메이션되는 경우 재카운트 방지
- 킬피드 강조 기준을 v1보다 보수적으로 조정
- v7.15의 스킬 평가, v7.14 티어, v7.13 에임 보정 유지

Windows에서 `run_windows.bat`을 실행하세요.
