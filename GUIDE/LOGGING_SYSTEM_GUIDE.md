# 로깅 시스템 가이드 (2025-12-11 업데이트)

## 개요

이 문서는 프로젝트의 통합 로깅 시스템에 대한 가이드입니다.
2025-12-11 디버그 로그 강화 작업 후 최신 상태를 반영합니다.

---

## 로그 파일 구조

### 파일 위치 및 명명 규칙
```
logs/
├── log_YYYYMMDD_HHMMSS.log                    # Crawler 로그 (start.bat)
├── log_START_DASHBOARD_YYYYMMDD_HHMMSS.log    # Dashboard 로그 (START_DASHBOARD.bat)
├── NOW_ISSUE.md                                # 현재 이슈 문서
├── NOW_LOGIC.md                                # 시스템 로직 문서
├── NOW_DB_SCHEMA.md                            # DB 스키마 문서
├── NOW_PROJECT_STRUCTURE.md                    # 프로젝트 구조
├── SERVER_RESTART_GUIDE.md                     # 서버 재시작 가이드
└── LOGGING_SYSTEM_GUIDE.md                     # 본 문서
```

### 로그 파일 설정
- **인코딩**: UTF-8
- **최대 크기**: 50MB
- **로테이션**: 자동 (50MB 초과 시)
- **백업 개수**: 최대 10개
- **포맷**:
  ```
  [YYYY-MM-DD HH:MM:SS] LEVEL     [logger_name:module:function:line] - message
  ```

---

## 로그 레벨 정책

| 레벨 | 용도 | 예시 | 출력 위치 |
|------|------|------|----------|
| **DEBUG** | 상세한 진단 정보 | 변수값, API 파라미터, 진행 상황 | 파일만 |
| **INFO** | 일반 정보 | API 요청 시작/완료, 채널 동기화 성공 | 콘솔 + 파일 |
| **WARNING** | 경고 메시지 | Quota 부족, 복구 실패 (계속 진행 가능) | 콘솔 + 파일 |
| **ERROR** | 에러 메시지 | Exception 발생, API 실패 | 콘솔 + 파일 |

---

## 로그 접두사 설정

### logger_config.py 사용법

**방법 1: 로거 초기화 전 접두사 설정**
```python
from logger_config import set_log_prefix, setup_logger

# 1. 접두사 설정 (가장 먼저!)
set_log_prefix('log_START_DASHBOARD_')

# 2. 로거 초기화
logger = setup_logger('dashboard')

# 결과: logs/log_START_DASHBOARD_20251211_143025.log
```

**방법 2: 기본 접두사 사용**
```python
from logger_config import setup_logger

# 접두사 설정 없이 초기화
logger = setup_logger('crawler')

# 결과: logs/log_20251211_143025.log (기본 접두사 'log_')
```

### 현재 사용 중인 접두사
- **크롤러**: `log_` (기본값)
- **대시보드**: `log_START_DASHBOARD_`

---

## 주요 파일별 로그 구현

### 1. START_DASHBOARD.bat

**파일**: [START_DASHBOARD.bat](../START_DASHBOARD.bat)

**로그 레벨**:
- `[DEBUG]`: 각 단계별 진행 상황
- `[INFO]`: 중요 정보 (서버 시작, URL, 로그 파일 위치)
- `[ERROR]`: 치명적 오류 (가상환경 실패, 패키지 설치 실패)

**주요 로그 위치**:
```batch
# 가상환경 활성화
echo [DEBUG] Activating virtual environment...
call venv\Scripts\activate.bat
if errorlevel 1 (
    echo [ERROR] Failed to activate virtual environment
    exit /b 1
)
echo [DEBUG] Virtual environment activated successfully

# 패키지 체크
echo [DEBUG] Checking Flask...
python -c "import flask" 2>nul
if errorlevel 1 (
    echo [INFO] Installing Flask...
    pip install flask
) else (
    echo [DEBUG] Flask is already installed
)

# 서버 시작
echo [INFO] Port: 5001
echo [INFO] URL: http://localhost:5001
echo [INFO] Log file: logs\log_START_DASHBOARD_*.log
```

---

### 2. dashboard_app.py

**파일**: [dashboard_app.py](../dashboard_app.py)

#### 초기화 로그 ([L22-72](../dashboard_app.py#L22-L72))
```python
logger.info("=" * 60)
logger.info("DASHBOARD APP INITIALIZATION START")
logger.info("=" * 60)

try:
    logger.debug("Importing Flask modules...")
    from flask import Flask, render_template, request, jsonify, Response
    logger.debug("Flask modules imported successfully")

    logger.debug("Importing custom modules...")
    from modules.youtube_manager import YouTubeManager
    logger.debug("Custom modules imported successfully")
except ImportError as e:
    logger.error(f"Failed to import required modules: {e}")
    logger.exception("Import error details:")
    sys.exit(1)

logger.debug("Initializing YouTubeManager...")
youtube_manager = YouTubeManager(DB_PATH)
logger.debug("YouTubeManager initialized successfully")

logger.info("Dashboard app initialization completed successfully")
logger.info("=" * 60)
```

#### API 엔드포인트 로그

**GET /api/stats** ([L96-153](../dashboard_app.py#L96-L153)):
```python
@app.route('/api/stats')
def api_stats():
    logger.debug("GET /api/stats - Request received")
    try:
        logger.debug("Connecting to database...")
        # ... DB 작업 ...
        logger.debug(f"Shorts count: {shorts_count}")
        logger.info(f"GET /api/stats - Success (crawl: {total}, api: {api_total})")
        return jsonify(response_data)
    except Exception as e:
        logger.error(f"GET /api/stats - Error: {e}")
        logger.exception("Stats API exception details:")
        return jsonify({'status': 'error', 'message': str(e)}), 500
```

**POST /api/sync/channel** ([L298-330](../dashboard_app.py#L298-L330)):
```python
@app.route('/api/sync/channel', methods=['POST'])
def api_sync_channel():
    logger.info(f"POST /api/sync/channel - URL: {channel_url}")

    if not channel_url:
        logger.warning("POST /api/sync/channel - Missing channel_url parameter")
        return jsonify({'status': 'error', 'message': 'channel_url required'}), 400

    logger.debug(f"Starting channel sync for: {channel_url}")
    result = youtube_manager.sync_channel(channel_url)

    if result['success']:
        logger.info(f"POST /api/sync/channel - Success (channel_id: {result['channel_id']}, quota: {result['quota_used']})")
    else:
        logger.warning(f"POST /api/sync/channel - Failed: {result['error']}")
```

---

### 3. modules/youtube_manager.py

**파일**: [modules/youtube_manager.py](../modules/youtube_manager.py)

#### API 초기화 ([L44-58](../modules/youtube_manager.py#L44-L58))
```python
def _init_youtube_api(self):
    logger.debug("Initializing YouTube API client...")
    try:
        api_key = getattr(Config, 'YOUTUBE_API_KEY', None)
        if api_key:
            logger.debug(f"API key found (length: {len(api_key)})")
            self.youtube = build('youtube', 'v3', developerKey=api_key)
            logger.info("✓ YouTube API initialized successfully with API key")
        else:
            logger.warning("✗ YouTube API key not found in Config")
    except Exception as e:
        logger.error(f"✗ YouTube API initialization failed: {e}")
        logger.exception("API initialization exception details:")
```

#### 채널 동기화 ([L290-439](../modules/youtube_manager.py#L290-L439))
```python
def sync_channel(self, channel_url: str, ...):
    logger.info("=" * 80)
    logger.info(f"CHANNEL SYNC START - URL: {channel_url}")
    logger.info(f"Parameters: channel_name={channel_name}, subs={subscriber_count}")
    logger.info("=" * 80)

    # Step 1: Zero-Cost
    logger.debug(f"[Step 1: Zero-Cost] Attempting URL parsing...")
    channel_id = get_channel_id_from_url(channel_url)
    if channel_id:
        logger.info(f"[Step 1: Zero-Cost] ✓ Channel ID extracted from URL: {channel_id}")

    # API 호출
    logger.debug(f"Calling channels.list API for channel_id: {channel_id}")
    response = self.youtube.channels().list(...).execute()
    logger.debug(f"API call successful (quota used: {result['quota_used']})")

    # 데이터 추출
    logger.debug(f"Extracted data - title: {data['title']}, subs: {data['subscriber_count']:,}")

    # 성공 로그
    logger.info("=" * 80)
    logger.info(f"✓ CHANNEL SYNC SUCCESS: {data['title']} ({channel_id})")
    logger.info(f"Stats - Subscribers: {data['subscriber_count']:,}, Quota: {result['quota_used']}")
    logger.info("=" * 80)
```

**로그 특징**:
- ✓/✗ 기호로 성공/실패 시각적 구분
- 구분선 (=) 80자로 섹션 구분
- DEBUG: 각 단계별 상세 정보
- INFO: 중요한 결과 및 통계
- ERROR: Exception traceback 포함

---

## 로그 확인 방법

### Windows PowerShell 명령어

**최신 로그 확인**:
```powershell
# Dashboard 로그 최신 50줄
Get-Content "logs\log_START_DASHBOARD_*.log" -Tail 50

# Crawler 로그 최신 50줄
Get-Content "logs\log_202512*.log" -Tail 50
```

**에러 로그 검색**:
```powershell
# ERROR 레벨 로그만 검색
Select-String -Path "logs\log_START_DASHBOARD_*.log" -Pattern "ERROR"

# Exception traceback 검색
Select-String -Path "logs\log_START_DASHBOARD_*.log" -Pattern "Traceback" -Context 5
```

**특정 API 요청 로그 검색**:
```powershell
# 채널 동기화 로그
Select-String -Path "logs\log_START_DASHBOARD_*.log" -Pattern "CHANNEL SYNC"

# API 엔드포인트 로그
Select-String -Path "logs\log_START_DASHBOARD_*.log" -Pattern "POST /api/sync"
```

**실시간 모니터링**:
```powershell
# 실시간으로 로그 확인 (tail -f와 유사)
Get-Content "logs\log_START_DASHBOARD_*.log" -Wait -Tail 20
```

---

## 로그 분석 팁

### 1. 에러 발생 시 체크리스트
1. **에러 메시지 확인**
   ```powershell
   Select-String -Path "logs\log_*.log" -Pattern "ERROR"
   ```

2. **Exception traceback 확인**
   - 에러 발생 파일 및 라인 번호 확인
   - Exception 타입 확인 (ModuleNotFoundError, HttpError 등)

3. **직전 로그 확인**
   - 에러 발생 직전의 DEBUG 로그 확인
   - 어떤 단계에서 실패했는지 파악

4. **관련 파라미터 확인**
   - API 요청 파라미터
   - DB 연결 상태
   - 변수 값

### 2. 성능 분석
- INFO 레벨 로그에서 처리 시간 확인
- Quota 사용량 추적
- API 호출 빈도 분석

### 3. 디버깅
- DEBUG 레벨 로그에서 변수 값 확인
- 각 단계별 진행 상황 추적
- 조건문 분기 확인

---

## 로그 설정 커스터마이징

### logger_config.py 수정

**파일**: [logger_config.py](../logger_config.py)

**로그 레벨 변경**:
```python
# 콘솔 출력 레벨 변경 (기본: INFO)
console_handler.setLevel(logging.DEBUG)  # 모든 로그 콘솔 출력

# 파일 출력 레벨 변경 (기본: DEBUG)
file_handler.setLevel(logging.INFO)  # INFO 이상만 파일 저장
```

**로그 포맷 변경**:
```python
detailed_formatter = logging.Formatter(
    fmt='[%(asctime)s] %(levelname)-8s [%(name)s:%(funcName)s:%(lineno)d] - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
```

**로그 파일 크기 및 백업 개수**:
```python
file_handler = RotatingFileHandler(
    log_filepath,
    maxBytes=100*1024*1024,  # 100MB로 변경
    backupCount=20,           # 백업 20개로 변경
    encoding='utf-8'
)
```

---

## 유틸리티 함수

### logger_config.py 제공 함수

**1. log_exception** - Exception 상세 로깅
```python
from logger_config import log_exception

try:
    # ... 코드 ...
except Exception as e:
    log_exception(logger, e, context="채널 동기화 중")
```

**2. log_function_call** - 함수 호출 로깅
```python
from logger_config import log_function_call

def my_function(param1, param2):
    log_function_call(logger, 'my_function', param1=param1, param2=param2)
    # ... 함수 본문 ...
```

**3. log_state_change** - 상태 변화 로깅
```python
from logger_config import log_state_change

log_state_change(logger, 'QuotaTracker', 'idle', 'processing', reason="API 요청 시작")
```

**4. log_user_action** - 사용자 액션 로깅
```python
from logger_config import log_user_action

log_user_action(logger, '채널 동기화', details=f"URL: {channel_url}")
```

---

## 문제 해결

### 로그 파일이 생성되지 않음
1. `logs/` 폴더 존재 확인
2. 쓰기 권한 확인
3. logger_config.py의 `_get_or_create_log_filepath()` 함수 확인

### 한글 인코딩 깨짐
- UTF-8 인코딩 확인: `encoding='utf-8'` 설정 확인
- 파일 읽을 때도 UTF-8로 지정:
  ```powershell
  Get-Content "logs\log_*.log" -Encoding UTF8
  ```

### 로그가 너무 많이 쌓임
- 로테이션 설정 확인 (maxBytes, backupCount)
- DEBUG 레벨 로그 줄이기 (중요 부분만 로그)
- 오래된 로그 수동 삭제

---

## 참고 문서
- [NOW_ISSUE.md](NOW_ISSUE.md) - 현재 이슈 및 해결 상태
- [NOW_LOGIC.md](NOW_LOGIC.md) - 시스템 로직
- [SERVER_RESTART_GUIDE.md](SERVER_RESTART_GUIDE.md) - 서버 재시작 가이드

---

**마지막 업데이트**: 2025-12-11
**작성자**: AI Assistant
**버전**: 1.0
