# YouTube Crawler Pro - 마케터 전용 도구

Playboard 데이터 수집 및 YouTube 자막 추출을 위한 Flask 기반 웹 애플리케이션입니다.

## 주요 기능

### 1. 동적 URL 생성 시스템
- **국가 선택**: 한국, 미국, 일본 등 8개국 지원
- **카테고리 선택**: 전체, 게임, 음악, 뉴스, 교육 등 13개 카테고리
- **기간 선택**: 일간, 주간, 월간
- **특정 날짜 조회**: Unix timestamp를 사용한 과거 날짜 랭킹 조회
- **실시간 URL 미리보기**: 선택 즉시 생성될 URL 확인 가능

### 2. 고급 크롤링 기능
- **쇼츠/영상/채널** 3가지 타입 지원
- **로그인 모드**: 비로그인(100개) / 로그인(200개)
- **향상된 데이터 추출**:
  - 순위 및 순위 변화
  - Video ID / Channel ID 자동 추출
  - 썸네일 이미지
  - 조회수, 좋아요, 구독자 등 상세 정보

### 3. YouTube 자막 추출
- **하이브리드 방식**:
  - 1차: `youtube-transcript-api` (높은 성공률)
  - 2차: Google Official API (백업)
- **배치 처리**: 여러 영상 동시 처리
- **자동 저장**: 텍스트 파일로 저장

### 4. 엑셀 친화적 기능
- **클립보드 복사**: 테이블 데이터를 탭 구분 형식으로 복사
- **엑셀 즉시 붙여넣기**: 복사 후 엑셀에 바로 Ctrl+V 가능
- **CSV 다운로드**: UTF-8 BOM 포함하여 엑셀에서 한글 깨짐 방지

## 프로젝트 구조

```
플레이보드 크롤링/
├── app.py                      # Flask 메인 애플리케이션
├── run.py                      # 서버 실행 진입점
├── start.bat                   # Windows 서버 시작 스크립트
├── config.py                   # 전역 설정
├── config_mappings.py          # URL 동적 생성 매핑 데이터
├── logger_config.py            # 로깅 시스템 설정
├── requirements.txt            # Python 의존성
├── .gitignore                  # Git 제외 파일 목록
├── README.md                   # 프로젝트 문서
├── 사용가이드.md              # 한국어 사용 가이드
│
├── google_service_key/         # GCP 인증 키
│   ├── service-account-key.json
│   └── client_secret_*.json
│
├── modules/                    # 핵심 기능 모듈
│   ├── __init__.py
│   ├── crawler_selenium.py    # Selenium 기반 Playboard 크롤러
│   └── youtube_handler.py     # YouTube API 및 자막 추출
│
├── templates/                  # Flask HTML 템플릿
│   ├── index.html             # 메인 대시보드
│   └── results.html           # 결과 페이지
│
├── static/                     # 정적 파일
│   ├── css/
│   └── js/
│       └── scripts.js         # 클립보드 유틸리티
│
├── output/                     # 크롤링 결과 저장
│   └── transcripts/           # YouTube 자막 텍스트 파일
│
├── logs/                       # 로그 파일 및 프로젝트 문서
│   ├── log_YYYYMMDD_HHMMSS.log # 통합 로그 파일 (서버 시작마다 생성)
│   ├── NOW_PROJECT_STRUCTURE.md  # 프로젝트 구조 문서
│   ├── NOW_LOGIC.md           # 핵심 로직 문서
│   ├── NOW_DB_SCHEMA.md       # 데이터베이스 스키마 문서
│   └── NOW_ISSUE.md           # 현재 이슈 및 기술 부채
│
└── venv/                       # Python 가상 환경
```

## 설치 방법

### 1. Python 환경 설정

```bash
# 가상환경 생성
python -m venv venv

# 가상환경 활성화 (Windows)
venv\Scripts\activate

# 가상환경 활성화 (Mac/Linux)
source venv/bin/activate

# 의존성 설치
pip install -r requirements.txt
```

### 2. ChromeDriver 설치

Selenium 사용을 위해 Chrome 브라우저와 일치하는 ChromeDriver가 필요합니다.

- [ChromeDriver 다운로드](https://chromedriver.chromium.org/)
- Chrome 버전 확인: `chrome://version`
- ChromeDriver를 시스템 PATH에 추가

### 3. Google Cloud 인증 설정

`google_service_key/` 폴더에 다음 파일을 배치하세요:

- `service-account-key.json`: 서비스 계정 키
- `client_secret_*.json`: OAuth 2.0 클라이언트 ID

## 사용 방법

### 1. 서버 실행

```bash
# 방법 1: start.bat 실행 (Windows)
start.bat

# 방법 2: Python 직접 실행
python run.py

# 방법 3: Flask 앱 직접 실행
python app.py
```

브라우저에서 접속: `http://localhost:5000` (자동으로 열림)

### 2. 크롤링 수행

#### 기본 워크플로우
1. **타겟 선택**: 쇼츠 / 영상 / 채널
2. **조건 설정**:
   - 카테고리 (예: 게임)
   - 국가 (예: 한국)
   - 기간 (예: 일간)
   - 특정 날짜 (선택사항)
3. **URL 미리보기 확인**
4. **로그인 모드 선택** (200개 수집 시)
5. **크롤링 시작** 버튼 클릭

#### 로그인 모드
- 체크하면 브라우저가 열리고 30초 동안 대기
- Playboard에 수동으로 로그인
- 로그인 완료 후 자동으로 크롤링 시작

### 3. 결과 활용

#### 클립보드로 엑셀에 복사
1. 결과 페이지에서 **"📋 엑셀용 복사"** 버튼 클릭
2. Excel/Google Sheets 열기
3. `Ctrl+V` (붙여넣기)
4. 데이터가 자동으로 열과 행에 정렬됨

#### CSV 다운로드
- **"📥 CSV 다운로드"** 버튼으로 파일 저장
- UTF-8 BOM 포함으로 한글 깨짐 없음

#### 자막 추출
1. 크롤링 결과에서 **"📝 전체 자막 추출"** 클릭
2. 자동으로 모든 Video ID의 자막 추출 시작
3. `output/transcripts/` 폴더에 저장

## 주요 개선사항 (NOW_PLAN 반영)

### 1. 동적 URL 생성 시스템
- **이전**: 하드코딩된 2개 URL만 지원
- **개선**: 국가/카테고리/기간 조합으로 수백 가지 URL 자동 생성
- **추가**: 특정 날짜 조회 (Unix timestamp 지원)

### 2. 데이터 추출 강화
- **Video ID / Channel ID**: URL에서 정확하게 추출
- **순위 변화**: 상승/하락 정보 수집
- **썸네일**: 이미지 URL 추출

### 3. UI/UX 개선
- **URL 미리보기**: 선택 즉시 생성될 URL 확인
- **드롭다운 메뉴**: 직관적인 옵션 선택
- **라디오 버튼**: 명확한 단일 선택

### 4. 마케터 친화 기능
- **클립보드 복사**: 엑셀 즉시 붙여넣기
- **검색 기능**: 테이블 내 실시간 검색
- **통계 표시**: 항목 수, 영상 수 등

## URL 생성 예시

### 예시 1: 한국 게임 쇼츠 일간
```
타겟: 쇼츠
카테고리: 게임
국가: 한국
기간: 일간

생성 URL:
https://playboard.co/chart/short/most-viewed-gaming-videos-in-south-korea-daily
```

### 예시 2: 미국 음악 영상 주간
```
타겟: 영상
카테고리: 음악
국가: 미국
기간: 주간

생성 URL:
https://playboard.co/chart/video/most-viewed-music-videos-in-united-states-weekly
```

### 예시 3: 특정 날짜 조회
```
타겟: 쇼츠
카테고리: 전체
국가: 한국
기간: 일간
날짜: 2025-12-01

생성 URL:
https://playboard.co/chart/short/most-viewed-all-videos-in-south-korea-daily?period=1733011200
```

## 설정 커스터마이징

### config_mappings.py
국가, 카테고리 추가/수정:

```python
COUNTRIES = {
    "한국": "south-korea",
    "새로운국가": "new-country"  # 추가
}

CATEGORIES = {
    "새카테고리": "new-category"  # 추가
}
```

### config.py
크롤링 설정 조정:

```python
CHROME_HEADLESS = True  # 브라우저 숨김 모드
LOGIN_WAIT_TIME = 60    # 로그인 대기 시간 증가
MAX_ITEMS_NO_LOGIN = 100
MAX_ITEMS_WITH_LOGIN = 200
```

## 트러블슈팅

### ChromeDriver 오류
```
WebDriverException: 'chromedriver' executable needs to be in PATH
```
**해결**: ChromeDriver 다운로드 후 PATH 추가

### 클립보드 복사 실패
**원인**: HTTPS가 아닌 HTTP 환경
**해결**: Fallback 방식 자동 적용 (동작함)

### 자막 추출 실패
```
No transcript found for video {video_id}
```
**원인**: 해당 영상에 자막 없음 또는 비활성화
**해결**: 다른 영상 시도

### 로그인 후에도 100개만 수집
**원인**: 로그인이 제대로 되지 않음
**해결**:
- `LOGIN_WAIT_TIME` 증가
- 수동 로그인 후 충분히 대기

## API 엔드포인트

### 주요 API
- `GET /`: 메인 대시보드
- `POST /api/build_url`: URL 생성 (미리보기용)
- `POST /crawl`: 크롤링 실행
- `POST /extract_transcripts`: 자막 추출
- `POST /upload_csv`: CSV 파일 업로드
- `GET /status`: 실시간 로그 조회
- `GET /results`: 결과 페이지
- `GET /download/<filename>`: 파일 다운로드
- `GET /view_data/<filename>`: 데이터 조회

## 라이센스

개인 사용 및 마케팅 연구 목적으로 제작되었습니다.

## 프로젝트 문서

자세한 정보는 `logs/` 폴더의 문서를 참조하세요:

- **[NOW_PROJECT_STRUCTURE.md](logs/NOW_PROJECT_STRUCTURE.md)**: 프로젝트 구조, 모듈 의존성, 환경 변수
- **[NOW_LOGIC.md](logs/NOW_LOGIC.md)**: 핵심 로직 플로우, 예외 처리, 데이터 생명 주기
- **[NOW_DB_SCHEMA.md](logs/NOW_DB_SCHEMA.md)**: CSV 스키마, 데이터베이스 마이그레이션 계획
- **[NOW_ISSUE.md](logs/NOW_ISSUE.md)**: 현재 이슈, 알려진 버그, 기술 부채

## 변경 이력

### v2.1 (2025-12-04)
- ✅ 로깅 시스템 통합 (단일 log_YYYYMMDD_HHMMSS.log 파일)
- ✅ 로깅 함수 확장 (API 요청, 상태 변화, 사용자 액션 추적)
- ✅ 로그 파일 크기 50MB, 백업 10개로 증가
- ✅ 프로젝트 문서화 (NOW_*.md 파일 생성)
- ✅ 불필요한 파일 제거 (crawler.py, PLAN.md, 참고파일/)
- ✅ .gitignore 업데이트
- ✅ start.bat 스크립트 추가 (인코딩 문제 해결)

### v2.0 (2025 업데이트)
- ✅ 동적 URL 생성 시스템 추가
- ✅ 국가/카테고리/기간 드롭다운 UI
- ✅ 특정 날짜 조회 (Unix timestamp)
- ✅ 클립보드 복사 기능 (엑셀용)
- ✅ Video ID / Channel ID 자동 추출
- ✅ 순위 변화 데이터 수집
- ✅ crawler.py → crawler_selenium.py 리팩토링
- ✅ youtube_api.py → youtube_handler.py 리팩토링

### v1.0 (초기 버전)
- 기본 크롤링 기능
- 자막 추출 기능
- Flask 웹 인터페이스
