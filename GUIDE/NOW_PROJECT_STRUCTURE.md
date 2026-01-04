# 프로젝트 구조 (NOW_PROJECT_STRUCTURE.md)

**최종 업데이트:** 2025-12-12 14:30:00 (13차 - PLAN.md 전면 UI 개편 완료)

---

## 📂 프로젝트 디렉토리 구조

```
플레이보드 크롤링/
├── app.py                      # Flask 웹 서버 메인 (Track A - Port 5000, 크롤러)
├── dashboard_app.py            # Flask DB 대시보드 (Track B - Port 5001, API 연동)
├── run.py                      # 프로젝트 실행 진입점 (서버 시작)
├── config.py                   # 전역 설정 (API 키, 경로, 크롤링 옵션)
├── config_mappings.py          # Playboard URL 동적 생성 매핑 (국가, 카테고리, 기간)
├── logger_config.py            # 로깅 시스템 설정 (파일 핸들러, 포맷터, 로그 접두사 지원)
├── requirements.txt            # Python 의존성 패키지 목록
├── requirements.bat            # 전체 환경 설치 스크립트 (가상환경 생성, 패키지 설치)
├── start.bat                   # Windows 서버 시작 스크립트 (Track A - 크롤러)
├── START_DASHBOARD.bat         # Windows DB 대시보드 시작 스크립트 (Track B)
├── token.pickle                # Google OAuth 2.0 인증 토큰 (읽기 전용)
├── token_playlist.pickle       # OAuth 2.0 재생목록 관리 토큰 (쓰기 권한)
│
├── modules/                    # 핵심 기능 모듈
│   ├── __init__.py
│   ├── crawler_selenium.py     # Selenium 기반 Playboard 크롤러 (시스템 PATH 우선 ChromeDriver)
│   ├── youtube_handler.py      # YouTube API 및 자막 추출 핸들러
│   ├── youtube_manager.py      # YouTube API 채널/영상 동기화 + 재생목록 내보내기
│   ├── youtube_utils.py        # Zero-Cost ID Extraction 유틸리티
│   ├── quota_tracker.py        # YouTube API Quota 추적
│   ├── auth_manager.py         # OAuth 2.0 인증 관리자 (재생목록 쓰기 권한) [신규]
│   ├── database.py             # SQLite DB 핸들러 (9개 테이블, API 테이블 추가)
│   └── utils.py                # 유틸리티 함수 (파일명 정제, 숫자 변환, 알림음)
│
├── templates/                  # Flask HTML 템플릿
│   ├── index.html              # 메인 대시보드 UI (Track A 크롤러)
│   ├── dashboard.html          # 시각화 대시보드 (Chart.js 통합, 실시간 통계)
│   ├── db_dashboard.html       # DB 대시보드 UI (Track B API 연동) - 재생목록 내보내기 모달 포함
│   └── results.html            # 크롤링 결과 표시 페이지 (레거시)
│
├── static/                     # 정적 파일 (CSS, JS, 이미지)
│   ├── css/
│   └── js/
│
├── output/                     # 크롤링 결과 저장소
│   ├── *.csv                   # CSV 파일 저장소
│   ├── db/                     # SQLite 데이터베이스
│   │   └── youtube_data.db     # 영상/채널/API 데이터 DB (9개 테이블)
│   └── transcripts/            # YouTube 자막 텍스트 파일 저장소
│
├── logs/                       # 애플리케이션 로그 파일 및 문서
│   ├── log_YYYYMMDD_HHMMSS.log # 크롤러 로그 파일 (start.bat 실행 시)
│   ├── log_START_DASHBOARD_YYYYMMDD_HHMMSS.log # DB 대시보드 로그 (START_DASHBOARD.bat 실행 시)
│   ├── NOW_PROJECT_STRUCTURE.md # 이 파일
│   ├── NOW_LOGIC.md            # 핵심 로직 문서
│   ├── NOW_DB_SCHEMA.md        # 데이터베이스 스키마 문서
│   └── NOW_ISSUE.md            # 현재 이슈 및 에러 로그
│
├── google_service_key/         # Google API 인증 파일
│   ├── service-account-key.json
│   └── client_secret_*.json    # OAuth 2.0 클라이언트 시크릿
│
└── venv/                       # Python 가상 환경

```

---

## 🔗 모듈 의존성 및 데이터 흐름도

### Two-Track System 아키텍처

```
┌─────────────────────────────────────────────────────────────────┐
│                    Two-Track System                             │
├─────────────────────────────────┬───────────────────────────────┤
│   Track A (Port 5000)           │   Track B (Port 5001)         │
│   데이터 크롤러                  │   DB 대시보드                  │
├─────────────────────────────────┼───────────────────────────────┤
│   start.bat                     │   START_DASHBOARD.bat         │
│   app.py                        │   dashboard_app.py            │
│   └─> crawler_selenium.py       │   └─> youtube_manager.py      │
│   └─> database.py               │   └─> youtube_utils.py        │
│                                 │   └─> quota_tracker.py        │
│                                 │   └─> auth_manager.py [신규]  │
│                                 │   └─> database.py             │
├─────────────────────────────────┼───────────────────────────────┤
│   로그 파일:                     │   로그 파일:                   │
│   log_YYYYMMDD_HHMMSS.log       │   log_START_DASHBOARD_        │
│                                 │   YYYYMMDD_HHMMSS.log         │
├─────────────────────────────────┼───────────────────────────────┤
│   기능:                          │   기능:                        │
│   - Playboard 랭킹 크롤링         │   - 고급 데이터 필터링/정렬     │
│   - CSV/DB 저장                  │   - Zero-Cost ID Extraction   │
│   - channel_url 수집             │   - YouTube API 동기화         │
│   - 로그인 모드 지원              │   - Quota 관리                 │
│                                 │   - 쇼츠/영상 분류              │
│                                 │   - 조회수 합산 순위 분석       │
│                                 │   - CSV 내보내기               │
│                                 │   - 재생목록 내보내기           │
│                                 │   - Channel Viewer [신규]      │
└─────────────────────────────────┴───────────────────────────────┘
```

### Dependency Graph

```
run.py (진입점 - Track A)
  │
  ├─> logger_config.py (로깅 설정)
  │
  └─> app.py (Flask 서버 - Port 5000)
        │
        ├─> config.py (전역 설정)
        ├─> config_mappings.py (URL 생성)
        ├─> logger_config.py (로깅)
        │
        ├─> modules/crawler_selenium.py (Playboard 크롤링)
        │     └─> config.py
        │     └─> logger_config.py
        │
        └─> modules/database.py (DB 핸들러)

dashboard_app.py (Track B - Port 5001)
  │
  ├─> config.py (전역 설정)
  ├─> logger_config.py (로그 접두사: log_START_DASHBOARD_)
  │
  ├─> modules/youtube_manager.py (채널/영상 동기화 + 재생목록 내보내기)
  │     └─> modules/youtube_utils.py (ID 추출)
  │     └─> modules/quota_tracker.py (할당량 추적)
  │     └─> config.py
  │
  ├─> modules/auth_manager.py (OAuth 2.0 재생목록 쓰기 인증) [신규]
  │     └─> config.py
  │
  └─> modules/database.py (DB 핸들러)
```

---

## 🔧 핵심 모듈 설명

### 1. **auth_manager.py** (OAuth 2.0 인증 관리자) [신규]
- **목적**: YouTube 재생목록 쓰기 권한을 위한 OAuth 2.0 인증 처리
- **주요 기능**:
  - `get_authenticated_service()`: 쓰기 권한이 있는 YouTube API 서비스 객체 반환
  - `run_oauth_flow()`: 브라우저 기반 OAuth 로그인 실행
  - `get_auth_status()`: 현재 인증 상태 확인
  - `revoke_token()`: 토큰 삭제 (로그아웃)
- **토큰 파일**: `token_playlist.pickle` (읽기 전용 token.pickle과 분리)
- **Scope**: `https://www.googleapis.com/auth/youtube` (재생목록 관리 권한)

### 2. **youtube_manager.py** (채널/영상/재생목록 관리)
- **기존 기능**: 채널 동기화, 영상 수집, Smart Recovery
- **신규 기능 (Hybrid Playlist Export)**:
  - `create_playlist()`: 새 재생목록 생성 (50 Quota)
  - `add_video_to_playlist()`: 재생목록에 영상 추가 (50 Quota/개)
  - `add_videos_to_playlist_batch()`: 일괄 영상 추가
  - `generate_playlist_url()`: Zero-Cost URL 생성 (50개 단위 분할)

### 3. **dashboard_app.py** (DB 대시보드 - Track B)
- **주요 API 엔드포인트**:
  - `/api/crawl_data` : 고급 데이터 조회 (필터, 정렬, 페이징)
  - `/api/crawl_data/category_rank` : 카테고리별 전체 순위
  - `/api/crawl_data/aggregated` : 조회수 합산 순위
  - `/api/sync/channel` : YouTube API 채널 동기화
  - `/api/sync/videos` : YouTube API 영상 동기화
  - `/api/quota` : API Quota 현황
- **재생목록 내보내기 API** [신규]:
  - `/api/export/auth/status` : OAuth 인증 상태 확인
  - `/api/export/auth/login` : OAuth 로그인 실행
  - `/api/export/auth/logout` : 로그아웃
  - `/api/export/playlist` : 재생목록 내보내기 (URL/API 방식)
  - `/api/export/quota_estimate` : Quota 예상치 계산

### 4. **db_dashboard.html** (프론트엔드 UI)
- **탭 구조** (4개 탭):
  - Tab 0: 대시보드 - 통계 개요
  - Tab 1: API 데이터 - 채널 카드 캐러셀 + 영상 테이블
  - Tab 2: 채널 관리 - 채널 동기화 및 영상 수집
  - Tab 3: Channel Viewer - 채널별 영상 전용 탭 [신규]
- **재생목록 내보내기 모달**:
  - 간편 링크 방식 (비용 0): URL 생성 후 YouTube에서 수동 저장
  - API 자동화 방식: OAuth 로그인 후 자동으로 재생목록 생성 및 영상 추가
  - 인증 상태 표시, Quota 실시간 계산
  - 필터 정보 표시, 기본 제목 자동 생성
- **Channel Viewer 탭** [신규]:
  - 채널 헤더 (프로필, 채널명, 구독자, 영상 수, YouTube 링크)
  - 필터/정렬 바 (타입 필터, 정렬 옵션, 뷰 모드 토글)
  - 그리드/테이블 뷰 전환
  - 페이지네이션
- **채널 관리 개선** [신규]:
  - 채널 목록 새로고침 버튼 추가
  - 채널 추출 후 자동 목록 갱신

---

## 🆕 최신 기능 (2025-12-12)

### 1. Channel Viewer 시스템 [신규]
**채널별 영상 전용 탭 - API 데이터 탭에서 채널 카드 클릭 시 이동**

- **주요 JavaScript 함수**:
  - `openChannelViewer(channelId, channelName)`: Channel Viewer 탭 열기
  - `closeChannelViewer()`: API 데이터 탭으로 복귀
  - `loadChannelViewerHeader(channelId)`: 채널 헤더 정보 로드
  - `loadChannelViewerVideos()`: 채널 영상 목록 로드
  - `renderCvGridView(videos)`: 그리드 뷰 렌더링
  - `renderCvTableView(videos)`: 테이블 뷰 렌더링
  - `setCvViewMode(mode)`: 뷰 모드 전환 (grid/table)

- **상태 변수**:
  - `cvChannelId`, `cvChannelName`: 현재 표시 중인 채널
  - `cvPage`, `cvTotalPages`: 페이지네이션 상태
  - `cvViewMode`: 현재 뷰 모드 ('grid' | 'table')
  - `CV_PAGE_SIZE`: 페이지당 영상 수 (24개)

### 2. 채널 관리 개선 [신규]
- **새로고침 버튼**: 채널 목록 수동 갱신
- **자동 업데이트**: 채널 추출 후 500ms 딜레이로 목록 자동 갱신

### 3. API 데이터 탭 UX 개선
- **채널 카드 버튼**: 더블클릭 대신 명시적 "📺 채널 영상 보기" 버튼
- **채널 테이블 뷰 지원** [신규 2025-12-12]:
  - `renderApiChannelsTable()` 함수 추가
  - 채널 전용 테이블 헤더 (번호, 채널 정보, 구독자 수, 영상 수, 총 조회수, 동기화, 액션)
  - DocumentFragment 최적화 렌더링
  - "📺 보기" 버튼으로 Channel Viewer 연결

### 4. 시스템 안정성 개선
- **Safety Timeout**: 8초 후 로딩 스피너 강제 제거
- **Request Timing Middleware**: 1초 이상 느린 요청 로깅
- **Global Try-Catch**: 초기화 오류 사용자 알림
- **sync_status 통일**: 채널 동기화 상태 값 'synced'로 통일 (기존 'success' → 'synced')
- **Cache-Control 헤더** [신규 2025-12-12]: 채널 관리 API 캐시 무효화로 데이터 즉시 갱신
- **동기화 후 1초 딜레이** [신규 2025-12-12]: DB 커밋 완료 대기 후 목록 갱신

### 5. 디버그 로깅 시스템 개선
- **VERBOSE 모드**: `toggleDebugVerbose()` 함수로 상세 로그 ON/OFF
- **메모리 최적화**: 최대 로그 수 1000 → 500개로 축소
- **새 카테고리**: API_START, API_END, WARN, CHANNEL_VIEWER, VIEW_MODE, BUTTON
- **버튼 클릭 로깅**: 새로고침 버튼 등에 클릭 시간, 소요 시간 로깅

### 6. Hybrid Playlist Export System
**API Quota 비용과 사용자 편의성 간의 균형을 맞춘 두 가지 방식 제공**

| 방식 | 특징 | 장점 | 단점 | 비용 (50개 기준) |
| :--- | :--- | :--- | :--- | :--- |
| **URL 생성** | 임시 링크 생성 | **비용 0**, 로그인 불필요 | 링크 열어서 수동 저장 필요, 50개 제한 | **0 Quota** |
| **API 자동화** | 백그라운드 자동 처리 | 클릭 한 번으로 완료 | **높은 API 비용**, OAuth 로그인 필요 | **2,550 Quota** |

### 7. OAuth 2.0 인증 모듈 분리
- `auth_manager.py`: 재생목록 쓰기 권한 전용 인증 모듈
- `token_playlist.pickle`: 읽기 전용 토큰과 분리하여 관리

---

## 🚀 실행 방법

### Track A: 데이터 크롤러 시작
```bash
start.bat
# → http://localhost:5000
```

### Track B: DB 대시보드 시작
```bash
START_DASHBOARD.bat
# → http://localhost:5001
```

---

**문서 관리자**: AI 자동 생성
**프로젝트명**: YouTube Crawler Pro (Playboard Edition)
**Python 버전**: 3.12.6
**최종 업데이트**: 2025-12-12
