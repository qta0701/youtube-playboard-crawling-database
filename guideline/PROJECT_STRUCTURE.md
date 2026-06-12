# 프로젝트 구조 (NOW_PROJECT_STRUCTURE.md)

이 문서는 **Streamlit 기반 단일 통합 앱**으로 개편된 프로젝트 구성과 모듈 간 관계를 정의합니다.

---

## 📂 프로젝트 디렉토리 구조

```
플레이보드 크롤링/
├── app.py                      # Streamlit 웹 대시보드 & 서버 메인 (통합 진입점, Port 8501~8502)
├── START_DASHBOARD.bat         # 프로젝트 시작 단일 배치 스크립트 (가상환경 자동 감지 및 기동)
├── config.py                   # 전역 설정 (경로 설정, 크롤링 기본 매개변수 등)
├── config_mappings.py          # Playboard URL 동적 매핑 (국가, 카테고리, 기간)
├── logger_config.py            # 로깅 시스템 설정 (파일 로깅, 콘솔 출력 서식)
├── requirements.txt            # Python 의존성 패키지 목록
├── requirements.bat            # 개발환경 셋업 배치 파일 (가상환경 생성, 패키지 동기화)
├── token.pickle                # Google API OAuth 2.0 읽기 전용 인증 토큰
├── token_playlist.pickle       # OAuth 2.0 재생목록 쓰기 권한 전용 토큰
│
├── modules/                    # 핵심 기능 비즈니스 로직 모듈
│   ├── __init__.py
│   ├── crawler_selenium.py     # Selenium 기반 Playboard 크롤러 (최적화 스크롤 및 로컬 C드라이브 프로필 적용)
│   ├── external_loader.py      # [NEW] 외부 프로그램 독립 격리 로더 (sys.modules 및 sys.path 샌드박싱)
│   ├── youtube_handler.py      # YouTube API 자막 추출 및 정보 처리
│   ├── youtube_manager.py      # YouTube API 지원 및 재생목록 일괄 내보내기
│   ├── youtube_utils.py        # Zero-Cost ID Extraction 유틸리티
│   ├── quota_tracker.py        # YouTube API Quota 할당량 추적
│   ├── auth_manager.py         # OAuth 2.0 재생목록 제어 쓰기 권한 관리자
│   ├── database.py             # SQLite DB 핸들러 (API 관련 스키마 제거, 구글시트 연동 최적화)
│   └── utils.py                # 시스템 알림음, OS 알림 및 문자열 가공 유틸리티
│
├── 외부프로그램/                # [NEW] 외부 프로젝트 저장소 (독립 실행 파일 및 모듈 존재)
│   ├── 롱폼-대본추출기/          # 롱폼 영상 유튜브 자막 추출 및 구글 시트 연동
│   ├── 롱폼-유튜브검색기/        # 롱폼 영상 유튜브 검색 및 구글 시트 벌크 저장
│   ├── 숏폼-대본추출기/          # 숏폼 영상 유튜브 자막 추출 및 구글 시트 연동
│   └── 숏폼-유튜브검색기/        # 숏폼 영상 유튜브 검색 및 구글 시트 벌크 저장
│
├── output/                     # 데이터 및 원본 아티팩트 보관소
│   ├── *.csv                   # 크롤링 백업 원본 CSV 파일
│   ├── db/                     # 데이터베이스 폴더
│   │   └── youtube_data.db     # 통합 SQLite3 데이터베이스 (영상, 채널, 설정 메타)
│   └── transcripts/            # YouTube 동영상에서 수집된 자막 텍스트 파일 저장소
│
├── logs/                       # 앱 실행 시 발생하는 로깅 파일 보관소
│
├── google_service_key/         # Google Cloud API 연동용 보안 인증 키
│   ├── service-account-key.json
│   └── client_secret_*.json    # OAuth 2.0 클라이언트 시크릿
│
├── guideline/                  # 시스템 개발 및 설정 지침 폴더
│   ├── PROJECT_STRUCTURE.md    # 이 파일
│   ├── PLAYBOARD_CRAWLER_GUIDE.md # 플레이보드 크롤러 가이드라인
│   ├── DB_STATISTICS_SEARCH_GUIDE.md # DB 통계 및 검색 가이드라인
│   ├── DATABASE_SHEET_INTEGRATION_GUIDE.md # 구글시트 연동 및 통합 DB 가이드라인
│   └── EXTERNAL_PROGRAMS_INTEGRATION_GUIDE.md # 외부 프로그램 통합 및 탭 연동 가이드라인
│
└── venv/ / .venv/              # Python 가상 환경 폴더
```


---

## 🔗 모듈 의존성 및 데이터 흐름도

### Streamlit 통합 아키텍처

```
                  ┌───────────────────────────────────────────────┐
                  │          사용자 브라우저 (Streamlit UI)          │
                  └───────────────────────┬───────────────────────┘
                                          │
                                          ▼
                                    [app.py 메인]
                                          │
                ┌─────────────────────────┼─────────────────────────┐
                ▼                         ▼                         ▼
      [Playboard 크롤링 엔진]     [대본 추출 및 유튜브 검색]      [OAuth/인증 레이어]
        crawler_selenium.py         youtube_manager.py          auth_manager.py
                │                         │                         │
                └─────────────────────────┼─────────────────────────┘
                                          │
                                          ▼
                                   [database.py]
                                          │
                                          ▼
                                  [youtube_data.db]
```

### Dependency Graph

```
START_DASHBOARD.bat (실행 쉘)
  │
  └─> app.py (Streamlit 구동 - Port 8501~8502)
        │
        ├─> config.py & config_mappings.py (환경 설정 및 매핑)
        ├─> logger_config.py (통합 로깅 시스템)
        │
        ├─> modules/crawler_selenium.py (플레이보드 Selenium 크롤러)
        │     └─> modules/database.py (DB 처리)
        │     └─> modules/utils.py (알림 사운드 및 OS 알림 연동)
        │
        ├─> modules/youtube_manager.py (YouTube API 및 재생목록 처리 지원)
        │     ├─> modules/youtube_utils.py (Zero-Cost ID 추출)
        │     ├─> modules/quota_tracker.py (할당량 계측)
        │     └─> modules/database.py
        │
        ├─> modules/auth_manager.py (OAuth 재생목록 쓰기 인증 관리)
        ├─> modules/database.py (SQLite DB 조작)
        │
        └─> modules/external_loader.py (격리 모듈 동적 로더)
              ├─> 외부프로그램/롱폼-대본추출기 & 숏폼-대본추출기 (GUI_Extract.py, Main_Extract.py)
              └─> 외부프로그램/롱폼-유튜브검색기 & 숏폼-유튜브검색기 (GUI_Interface.py, Main_Search.py)
```

---

## 🔧 핵심 모듈 설명

### 1. **app.py** (Streamlit 메인 진입점)
- **역할**: 단일 Streamlit 프로세스로 모든 화면과 크롤러, 외부 독립 프로그램 및 수집 모듈 연동 로직을 메인페이지의 6개 탭(플레이보드 크롤러, 크롤링 데이터, 채널 및 영상 수집, 대본 추출기, 유튜브 검색기, 구글 시트 연동 DB)으로 제공하고, 사이드바를 통해 API Quota 소모 현황을 상시 모니터링합니다.
- **기능**:
  - 메인페이지 탭 기반 대화형 인터페이스 구성 (6개 탭으로 단순화)
  - 크롤링 데이터의 날짜별 카테고리 썸네일 그리드 대시보드 뷰 및 상세 검색 제공
  - 크롤러 수동 구동 요청 처리 및 실시간 처리 로그 시각화
  - 사이드바 고정형 일일 API Quota 추적 및 최근 7일 그래프 위젯 탑재
  - **📥 채널 및 영상 수집 (Tab 3)**: 유튜브 API를 이용하여 채널과 영상/재생목록 데이터를 수집해 로컬 DB(`sheet_channels`, `sheet_videos`)에 중복 없이 Upsert 적재하고 동일 채널 영상을 실시간 집계해 통계를 즉각 최신화.
  - **📝 대본 자동 추출기 (Tab 4)**: 외부 대본 추출기 프로젝트를 동적 격리 로드하여 구글 시트 기반 실시간 자막 추출 및 구글 드라이브/Docs 문서화 실행.
  - **🔎 유튜브 키워드 검색기 (Tab 5)**: 외부 유튜브 검색기 프로젝트를 동적 격리 로드하여 키워드 검색을 실행하고 구글 시트 '키워드 검색결과' 탭 및 로컬 DB `sheet_videos` 테이블에 중복 체크 후 누적 저장.
  - **📊 구글 시트 연동 DB (Tab 6)**: 로컬 DB 테이블(`sheet_videos`, `sheet_channels`, `sheet_playlist_ids`)과 구글 스프레드시트 7개 탭 간의 대용량 벌크 양방향 동기화 및 9행 수식 보호 관리.

### 2. **modules/crawler_selenium.py** (Playboard 크롤러)
- **역할**: 셀레늄 자동화 웹 브라우저를 띄워 Playboard 랭킹 사이트를 크롤링합니다.
- **특화 로직**:
  - `_init_driver`: 브라우저 옵션을 통해 백그라운드 Throttling을 원천 방지하고 시스템 환경에 맞는 ChromeDriver 자동 로드
  - `get_chrome_profile_path`: Windows 로컬 C 드라이브 사용자 홈 디렉터리에 크롬 사용자 프로필 폴더를 안전하게 생성/리다이렉션하여 드라이브 권한 오류를 해소하고 로그인 세션 유도
  - `_scroll_to_load_items`: Element-Based Stepped Scrolling 및 멈춤 현상 해소를 위한 JavaScript Wiggle 모션 적용

### 3. **modules/youtube_manager.py** (YouTube 자원 관리자)
- **역할**: 유튜브 API를 통해 동영상 상세 메타데이터를 파싱하고 재생목록 생성/영상 삽입 기능을 수행합니다.
- **기능**:
  - API 할당량 소모를 방지하는 Zero-Cost ID Extraction 파싱 기법 적용
  - API 자동 방식의 재생목록 생성 및 영상 삽입 기능 탑재

### 4. **modules/auth_manager.py** (OAuth 인증 제어)
- **역할**: YouTube 재생목록 쓰기 등 사용자 개인 계정에 대한 조작 권한이 필요한 경우, 로컬 호스트 상에 임시 OAuth 인라인 브라우저를 열어 인증 프로세스를 수행하고 갱신 토큰을 `token_playlist.pickle`에 분리 저장합니다.

### 5. **modules/external_loader.py** (외부 모듈 격리 동적 로더)
- **역할**: 동일한 라이브러리 파일 명세(예: `sheet_config.py`, `sheet_utils.py`)를 가지는 다수의 외부 프로그램을 네임스페이스 충돌 없이 독립적으로 가동하기 위해 `sys.modules` 및 `sys.path` 샌드박싱 처리를 수행하는 헬퍼 모듈입니다.

---

## 🚀 기동 가이드

1. **개발환경 구축 (초기 1회)**:
   ```bash
   requirements.bat
   ```
   이 스크립트는 로컬 가상환경(.venv)을 만들고 필요한 패키지(streamlit, selenium, plotly 등)를 자동으로 셋업합니다.

2. **시스템 실행**:
   ```bash
   START_DASHBOARD.bat
   ```
   실행 시 자동으로 브라우저 환경이 감지되며, Streamlit 로컬 호스트(http://localhost:8501) 대시보드가 브라우저 새 탭으로 즉각 기동됩니다.
