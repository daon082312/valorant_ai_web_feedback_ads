VALORANT AI Coach - Public + Ads build

포함 기능
- Gemini 클립 분석
- 성공한 분석만 개인 일일 횟수 차감
- 장면별/전체 사용자 피드백
- AdSense 광고 슬롯 2개
- About / Privacy / Terms / Guide 페이지
- Riot 비공식 서비스 필수 고지문
- /ads.txt, /robots.txt, /sitemap.xml

Render 설정
Build Command:
pip install -r requirements.txt

Start Command:
uvicorn app:app --host 0.0.0.0 --port $PORT

필수 Environment
GEMINI_API_KEY=...
GEMINI_MODELS=gemini-3.8-flash,gemini-3.7-flash,gemini-3.6-flash,gemini-3.5-flash-lite
DAILY_ANALYSIS_LIMIT=3
MAX_UPLOAD_MB=100
CONTACT_EMAIL=실제 문의 이메일
PUBLIC_BASE_URL=https://실제주소.onrender.com

AdSense 승인 후 Environment에 추가
ADSENSE_CLIENT=ca-pub-1234567890123456
ADSENSE_TOP_SLOT=1234567890
ADSENSE_RESULT_SLOT=0987654321

주의
1. AdSense 승인 전에 가짜 publisher ID나 slot ID를 넣지 마세요.
2. Google에서 발급한 값을 그대로 입력하세요.
3. ADSENSE_CLIENT가 비어 있으면 실제 광고 요청 없이 placeholder만 표시됩니다.
4. /ads.txt는 ADSENSE_CLIENT에서 pub- ID를 자동 생성합니다.
5. Privacy/Terms는 서비스용 초안입니다. 실제 운영자 정보, 보관기간,
   한국 개인정보 관련 의무, 쿠키 동의 방식, 유료상품 여부에 맞게 최종 수정하세요.
6. Render의 기본 파일시스템은 영구 저장소가 아닐 수 있습니다.
   정식 서비스에서는 usage/feedback 저장을 PostgreSQL, Redis 또는 Supabase로 옮기세요.
7. Riot 정책상 플레이어 대상 제품은 Developer Portal 등록이 필요하며,
   수익화 제품은 Approved 또는 Acknowledged 상태여야 합니다.
