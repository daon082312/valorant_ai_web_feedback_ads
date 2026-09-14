# VALORANT Local Coach v5

API/Render 없이 Windows PC에서 실행되는 로컬 VALORANT 복기/코칭 프로그램입니다. 화면 캡처, 입력 기록, 교전 자동 녹화, 로컬 AI 대화, 미니맵/적·아군 후보 판독을 한 프로그램에 통합했습니다.

## v5 기능

- WASD, Shift, Ctrl, 사격 입력 분석
- 사용자가 직접 스킬 키 지정
  - 1번/2번/3번 스킬, 궁극기
  - 문자/숫자, Mouse4/Mouse5 지원
- 교전 자동 분리 및 전후 자동 녹화
- 이동사격, Shift 워크 사격, Ctrl 앉은 사격, 정지→첫 탄, 반대방향 탭 분석
- 스킬 사용 타이밍 분석
- 로컬 Ollama 코치 AI와 대화
- **교전 영상 24시간 보관 후 자동 삭제**
  - 프로그램 시작 시 정리
  - 실행 중에는 1시간마다 정리
  - 영상만 삭제하고 세션/교전 JSON은 유지
  - 프로그램이 꺼져 있던 동안 24시간이 지난 파일은 다음 실행 때 삭제
- **미니맵 복기 창**
  - 상단 왼쪽 미니맵 영역 실시간 표시
  - 교전마다 전/중/후 미니맵 JPG 저장
- **적/아군 후보 판독**
  - 화면과 미니맵에서 색상/형태 기반 로컬 CV 사용
  - 적 윤곽 색상: red / purple / yellow 설정 가능
  - 아군 색상: cyan / green 설정 가능
  - 낮은 신뢰도 장면은 미분류
- **헤드라인 평가**
  - 적 후보가 잡힌 프레임에서 화면 중앙 크로스헤어 높이와 적 후보 상단 머리 추정 영역의 세로 오차를 계산
  - 충분한 적 판독이 없으면 점수를 만들지 않음
- **패배 원인 분석**
  - 이동사격, Ctrl 의존, 교전 점수, 스킬 사용량/타이밍, 헤드라인 등 실제 측정값을 근거로 가능성이 높은 요인을 정렬
  - 실제 패배의 인과를 확정한다고 표현하지 않음
- **언어 설정**
  - 한국어(`ko`) / English(`en`)
  - UI와 AI 답변 언어 변경
- **폰트 개선**
  - Pretendard → Inter → Noto Sans KR → Segoe UI 순서로 설치된 폰트를 자동 선택
  - 별도 폰트 파일 배포 없음

## 중요한 판독 한계

현재 적/아군 판독은 학습된 YOLO 모델이 아니라 **색상/형태 휴리스틱**입니다. VALORANT의 적 윤곽 색상 설정과 `비전 설정`의 색상을 맞춰야 합니다. 화면 효과, HUD 색상, 맵 배경 등에 따라 오탐/미탐이 생길 수 있으며 확신이 낮은 장면은 판독하지 않습니다.

헤드라인 점수도 이 적 후보를 이용한 추정치입니다. 실제 머리 bounding box를 학습한 모델이 추가되면 더 정확해질 수 있습니다.

미니맵에서는 현재 색상 후보 수와 변화, 교전 전/중/후 스냅샷을 제공합니다. 이 숫자만으로 로테이션이나 포지셔닝의 좋고 나쁨을 단정하지 않습니다.

## 설치/실행

Windows 10/11 + Python 3.11 또는 3.12 권장.

가장 간단한 방법:

1. ZIP 압축 해제
2. `run_windows.bat` 더블클릭

수동 실행:

```bat
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python app_v5.py
```

## 로컬 AI 대화

Ollama가 있으면 외부 API 없이 PC 안에서 대화합니다.

기본 모델:

```text
qwen3:4b
```

예:

```bat
ollama pull qwen3:4b
```

프로그램의 `AI 채팅` → `AI 설정`에서 Ollama 주소와 모델명을 변경할 수 있습니다. Ollama가 없어도 측정 수치 기반 기본 코치 답변은 동작합니다.

AI가 참고하는 항목:

- 최근/전체 교전 무빙 지표
- Shift/Ctrl, 정지→첫 탄, 반대방향 탭
- 스킬 입력 타임라인
- 헤드라인 점수
- 화면/미니맵 적·아군 후보 수
- 사용자가 `패배 원인`을 선택했을 때 측정 기반 패배 요인 후보

AI는 현재 측정하지 못한 킬/데스, 실제 스킬 적중, 실제 적 위치를 지어내지 않도록 프롬프트가 제한되어 있습니다.

## 스킬 키 설정

프로그램 상단 `스킬 키 설정`에서 실제 키 바인딩을 입력합니다.

예:

- 1번 스킬 = Q
- 2번 스킬 = E
- 3번 스킬 = Mouse4
- 궁극기 = X

설정은 `config.json`에 저장됩니다.

## 비전 설정

`비전 설정`에서 실제 VALORANT 설정과 맞춥니다.

- Enemy outline: `red`, `purple`, `yellow`
- Ally color: `cyan`, `green`
- Minimap enemy / ally 색상
- 프로그램 내부 화면 미리보기 판독 박스 표시 여부

게임 화면 자체에는 오버레이를 주입하지 않습니다. 판독 박스는 Local Coach 프로그램 내부 미리보기에만 표시됩니다.

## 데이터 위치

- `data/fight_clips/<세션>/fight_*.mp4` 또는 `.avi`: 교전 영상 — 24시간 후 자동 삭제
- `data/fight_clips/<세션>/fight_*.json`: 교전 무빙/스킬 메타데이터 — 유지
- `data/fight_clips/<세션>/vision_*_minimap_*.jpg`: 미니맵 전/중/후 스냅샷
- `data/fight_clips/<세션>/vision_*_vision.json`: 적/아군/헤드라인 판독 요약
- `data/sessions/session_*.json`: 세션 기록
- `data/training_frames/`: 향후 Vision 모델 학습 후보 프레임

## EXE 만들기

`build_windows.bat`을 실행하면 PyInstaller로 Windows 실행 파일을 생성합니다.

## 다음 단계

정확도를 더 높이려면 실제 학습 모델을 붙이는 단계가 필요합니다. 권장 라벨은 다음과 같습니다.

- enemy_body
- enemy_head
- ally_body
- crosshair
- minimap_enemy
- minimap_ally
- smoke / flash / molly effect
- killfeed_self_kill / death

현재 v5는 이 학습 모델이 없어도 동작하는 로컬 휴리스틱 버전이며, 모델 추가 시 `vision_analyzer.py`를 교체하기 쉽게 분리되어 있습니다.
