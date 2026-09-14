# VALORANT Local Coach v1

API 비용과 서버 비용 없이 Windows PC에서 실행되는 로컬 코칭 프로토타입입니다.

## 지금 되는 것

- 모니터 화면을 로컬에서 실시간 캡처
- 전역 WASD 입력 추적
- 왼쪽 마우스 클릭을 사격 이벤트로 기록
- 교전(연속 사격 묶음) 자동 분리
- 이동 중 사격 비율 분석
- 긴 스프레이 감지
- 사격 구간 화면 움직임 분석
- 교전 종료 직후 앱 창에 피드백 표시
- 세션 JSON 기록 저장
- 향후 적/킬피드/요원/스킬 AI 학습용 프레임 자동 수집

## 일부러 넣지 않은 것

- 게임 메모리 읽기
- DLL 주입
- Vanguard 우회
- 적 위치를 게임 화면 위에 표시하는 오버레이
- 교전 중 즉시 행동 지시

이 버전은 게임 외부의 독립 창에서 **교전 종료 후** 코칭을 보여줍니다.

## 설치

Windows 10/11 + Python 3.11 또는 3.12 권장.

```bat
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python main.py
```

또는 `run_windows.bat`을 실행하세요.

## EXE 만들기

`build_windows.bat`을 실행하면 `dist\ValorantLocalCoach.exe`가 만들어집니다.

> PyInstaller EXE는 Windows에서 빌드해야 합니다. Linux/Mac에서 Windows EXE를 직접 만들 수는 없습니다.

## 설정

`config.json`에서 조정할 수 있습니다.

- `capture_fps`: 화면 분석 FPS. 기본 12
- `fight_gap_seconds`: 연속 사격을 하나의 교전으로 묶는 최대 공백
- `moving_shot_warn_ratio`: 이동사격 경고 기준
- `long_burst_shot_count`: 긴 스프레이 기준 탄 수
- `camera_motion_warn_threshold`: 사격 중 화면 흔들림 경고 기준
- `monitor_index`: 분석할 모니터 번호. 보통 1

## 다음 버전: 실제 Vision AI

현재 v1은 입력 + 화면 움직임을 이용한 코칭 엔진입니다. `data/training_frames`에 사격 주변 프레임을 자동 저장하도록 되어 있어 다음 단계에서 직접 학습시킬 수 있습니다.

권장 라벨:

- enemy_visible
- enemy_head
- crosshair
- killfeed_self_kill
- death
- spike_planted
- round_end
- agent_icon
- ability_used

이 라벨로 YOLO/RT-DETR/ONNX 모델을 학습하면 다음 기능을 붙일 수 있습니다.

1. 크로스헤어-적 머리 거리
2. 첫 적 노출 → 첫 발 반응시간
3. 적이 보이는 상태의 재장전
4. 잘못된 피킹/와이드 스윙 패턴
5. 킬/데스 자동 인식
6. 요원/스킬 사용 평가
7. 라운드 종료 자동 요약

## 데이터 위치

- `data/training_frames/<세션>/`: 학습 후보 프레임
- `data/sessions/session_*.json`: 세션 요약

모든 데이터는 기본적으로 PC 로컬에만 저장됩니다.
