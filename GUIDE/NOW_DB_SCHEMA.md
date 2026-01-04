# 데이터베이스 스키마 (NOW_DB_SCHEMA.md)

**최종 업데이트:** 2025-12-10 13:00:00

---

## 📌 데이터 저장 방식

프로젝트는 **Two-Track System**과 **이중 저장 시스템**을 사용합니다:

### Two-Track System 아키텍처
- **Track A (Port 5000)**: 데이터 크롤러 - Playboard 데이터 수집
- **Track B (Port 5001)**: DB 대시보드 - YouTube API 연동 및 데이터 관리

### 저장 시스템
1. **CSV 파일** - 호환성 및 데이터 교환용 (사용자 화면에 보이는 컬럼만 저장)
2. **SQLite 데이터베이스** - 쿼리, 통계, 히스토리 관리용 (Batch Commit 구현)
   - **shorts_rank**: 쇼츠 랭킹 전용 테이블 (10개마다 자동 commit)
   - **videos_rank**: 일반 영상 랭킹 전용 테이블 (10개마다 자동 commit)
   - **channels_rank**: 채널 랭킹 전용 테이블 (10개마다 자동 commit) - **channel_url 컬럼 추가**
   - **api_channels**: YouTube API 채널 데이터 (Zero-Cost ID Extraction 결과)
   - **api_videos**: YouTube API 영상 데이터 (쇼츠/영상 분류)
   - **quota_logs**: API 할당량 추적 로그
   - **videos** (레거시): 하위 호환성 유지

### 현재 저장 구조
```
output/
├── *.csv                       # CSV 파일
├── db/
│   └── youtube_data.db         # SQLite 데이터베이스 (9개 테이블)
└── transcripts/
    ├── dQw4w9WgXcQ_transcript.txt
    └── jNQXAC9IVRw_transcript.txt
```

---

## 🗄️ SQLite 데이터베이스 스키마 (3-Table Structure)

### 1. shorts_rank 테이블 (쇼츠 랭킹)

```sql
CREATE TABLE IF NOT EXISTS shorts_rank (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    video_id TEXT,
    title TEXT,
    thumbnail_url TEXT,
    channel_name TEXT,
    channel_id TEXT,
    views INTEGER DEFAULT 0,
    likes INTEGER DEFAULT 0,
    rank INTEGER,
    rank_change TEXT,
    upload_date TEXT,
    subscriber_count TEXT,
    tags TEXT,
    category TEXT,
    country TEXT,
    period TEXT,
    crawled_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(video_id, category, country, period, crawled_at)
);
```

**설명**:
- 쇼츠 전용 랭킹 데이터 저장
- 동일 영상이 다른 카테고리/국가/기간에서 랭킹 될 수 있음
- **tags**: 해시태그 수집 (콤마로 구분, 예: "#kpop,#music")
- UNIQUE 제약으로 중복 방지 (동일 조건 + 크롤링 시간 조합)
- **Batch Commit**: 10개 항목마다 자동 commit으로 중단 시 데이터 보존 (PLAN.md 3.2)

### 2. videos_rank 테이블 (일반 영상 랭킹)

```sql
CREATE TABLE IF NOT EXISTS videos_rank (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    video_id TEXT,
    title TEXT,
    thumbnail_url TEXT,
    channel_name TEXT,
    channel_id TEXT,
    views INTEGER DEFAULT 0,
    likes INTEGER DEFAULT 0,
    rank INTEGER,
    rank_change TEXT,
    upload_date TEXT,
    subscriber_count TEXT,
    tags TEXT,
    category TEXT,
    country TEXT,
    period TEXT,
    crawled_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(video_id, category, country, period, crawled_at)
);
```

**설명**:
- 일반 영상 전용 랭킹 데이터 저장
- **tags**: 해시태그 수집 (콤마로 구분)
- 구조는 shorts_rank와 동일하지만 별도 테이블 관리
- **Batch Commit**: 10개 항목마다 자동 commit으로 중단 시 데이터 보존 (PLAN.md 3.2)

### 3. channels_rank 테이블 (채널 랭킹)

```sql
CREATE TABLE IF NOT EXISTS channels_rank (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    channel_id TEXT,
    channel_name TEXT,
    profile_url TEXT,
    channel_url TEXT,           -- Zero-Cost ID Extraction용 Playboard URL (2025-12-05 추가)
    ranking_type TEXT,          -- 랭킹 타입 (popular/growth) (2025-12-05 추가)
    rank INTEGER,
    rank_change TEXT,
    subscriber_count TEXT,      -- 구독자 수 (2025-12-08 수정: score_1 → subscriber_count)
    total_views TEXT,           -- 총 조회수 (2025-12-08 수정: score_2 → total_views)
    video_count INTEGER DEFAULT 0,
    tags TEXT,
    category TEXT,
    country TEXT,
    period TEXT,
    crawled_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(channel_id, category, country, period, crawled_at)
);
```

**설명**:
- 채널 전용 랭킹 데이터 저장
- **channel_url**: Playboard 채널 URL (Zero-Cost ID Extraction용) - 2025-12-05 추가
- **ranking_type**: 랭킹 타입 구분 (popular: 인기순위, growth: 구독자급상승) - 2025-12-05 추가
- **subscriber_count**: 구독자 수 (2025-12-08 수정: score_1에서 명확한 이름으로 변경)
- **total_views**: 총 조회수 (2025-12-08 수정: score_2에서 명확한 이름으로 변경)
- **video_count**: 영상 개수 (일부 차트 제공)
- **tags**: 채널 태그 (콤마로 구분, 예: "#뉴스,#MBC")
- **Batch Commit**: 10개 항목마다 자동 commit으로 중단 시 데이터 보존

**컬럼명 변경 내역 (2025-12-08)**:
- `score_1` → `subscriber_count`: 구독자 수를 명확히 표현
- `score_2` → `total_views`: 총 조회수를 명확히 표현
- 기존 score_1, score_2는 카테고리별로 다른 지표를 담아 혼란을 초래했음

### 4. videos 테이블 (레거시 - 하위 호환성)

```sql
CREATE TABLE IF NOT EXISTS videos (
    video_id TEXT PRIMARY KEY,
    title TEXT,
    channel_name TEXT,
    channel_id TEXT,
    views INTEGER DEFAULT 0,
    likes INTEGER DEFAULT 0,
    upload_date TEXT,
    subscriber_count TEXT,
    rank INTEGER,
    rank_change TEXT,
    category TEXT,
    country TEXT,
    period TEXT,
    target_type TEXT,
    video_url TEXT,
    crawled_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_videos_category ON videos(category);
CREATE INDEX idx_videos_crawled_at ON videos(crawled_at);
```

### 2. crawl_history 테이블 (크롤링 히스토리)

```sql
CREATE TABLE IF NOT EXISTS crawl_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    target_type TEXT,
    category TEXT,
    country TEXT,
    period TEXT,
    item_count INTEGER,
    success BOOLEAN,
    error_message TEXT,
    crawled_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_crawl_history_crawled_at ON crawl_history(crawled_at);
```

### 3. transcripts 테이블 (자막 정보)

```sql
CREATE TABLE IF NOT EXISTS transcripts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    video_id TEXT,
    language TEXT,
    transcript_text TEXT,
    extracted_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (video_id) REFERENCES videos(video_id)
);
```

---

## 🆕 YouTube API 연동 테이블 (2025-12-05 추가)

### 1. api_channels 테이블 (API 채널 데이터)

```sql
CREATE TABLE IF NOT EXISTS api_channels (
    channel_id TEXT PRIMARY KEY,          -- YouTube Channel ID (UC로 시작)
    title TEXT,                           -- 채널명
    thumbnail_url TEXT,                   -- 채널 썸네일 URL
    subscriber_count INTEGER,             -- 구독자 수
    view_count INTEGER,                   -- 총 조회수
    video_count INTEGER,                  -- 총 영상 수
    uploads_playlist_id TEXT,             -- uploads 플레이리스트 ID (영상 수집용)
    crawled_url TEXT,                     -- 크롤링 원본 URL (Playboard)
    last_updated DATETIME DEFAULT CURRENT_TIMESTAMP
);
```

**설명**:
- Zero-Cost ID Extraction 결과 저장
- Playboard URL에서 Channel ID 추출 → YouTube API로 상세 정보 조회
- **uploads_playlist_id**: 채널 영상 목록 조회에 필요 (API 비용 1 unit)
- API 비용: channels.list = 1 unit (Search API 100 unit 대비 99% 절감)

### 2. api_videos 테이블 (API 영상 데이터)

```sql
CREATE TABLE IF NOT EXISTS api_videos (
    video_id TEXT PRIMARY KEY,            -- YouTube Video ID (11자)
    channel_id TEXT,                      -- 채널 ID (FK)
    title TEXT,                           -- 영상 제목
    published_at DATETIME,                -- 게시일
    duration_iso TEXT,                    -- ISO 8601 Duration (PT1M30S)
    duration_sec INTEGER,                 -- 초 단위 Duration
    video_type TEXT,                      -- 'shorts' (≤60초) 또는 'video'
    view_count INTEGER,                   -- 조회수
    like_count INTEGER,                   -- 좋아요 수
    tags TEXT,                            -- 태그 (콤마 구분)
    last_updated DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(channel_id) REFERENCES api_channels(channel_id)
);
```

**설명**:
- YouTube API videos.list 응답 저장
- **video_type**: Duration 기반 자동 분류
  - 60초 이하: 'shorts'
  - 60초 초과: 'video'
- **duration_iso**: ISO 8601 형식 (PT1H2M3S = 1시간 2분 3초)
- **duration_sec**: 초 단위 변환값 (검색/필터링용)

### 3. quota_logs 테이블 (API 할당량 추적)

```sql
CREATE TABLE IF NOT EXISTS quota_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    request_date DATE,                    -- 요청 날짜 (YYYY-MM-DD)
    endpoint TEXT,                        -- API 엔드포인트 (channels.list, videos.list 등)
    units_used INTEGER,                   -- 사용 단위
    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
);
```

**설명**:
- YouTube API 할당량 추적 (일일 10,000 units 제한)
- **endpoint별 비용**:
  - channels.list: 1 unit
  - videos.list: 1 unit
  - playlistItems.list: 1 unit
  - search.list: 100 units (사용 안함 - Zero-Cost 전략)

### API 데이터 흐름

```
1. Playboard 크롤링 (Track A)
   └── channel_url 수집 (channels_rank 테이블)

2. DB 대시보드 동기화 (Track B)
   └── channel_url → Zero-Cost ID Extraction
       └── HTML 파싱으로 Channel ID 추출 (비용 0)
       └── YouTube API channels.list 호출 (비용 1)
       └── api_channels 테이블에 저장

3. 영상 수집 (Track B)
   └── uploads_playlist_id로 playlistItems.list 호출 (비용 1/page)
   └── 영상 ID 목록 획득
   └── videos.list로 상세 정보 조회 (비용 1/50개)
   └── Duration 기반 쇼츠/영상 자동 분류
   └── api_videos 테이블에 저장
```

---

## 📊 CSV 파일 스키마

### 1. 쇼츠/영상 데이터 (`shorts`, `video`)

#### 파일명 규칙
```
{target_type}_{category}_{country}_{period}_{timestamp}.csv
```

#### 스키마 (컬럼 정의) - PLAN.md 3.5 최종 확정

**CSV에 저장되는 컬럼** (Video ID, Likes는 수집하지 않음)

| 컬럼명 | 데이터 타입 | Nullable | 설명 | 예시 |
|--------|-------------|----------|------|------|
| `Rank` | String | No | 순위 | `"1"`, `"2"`, `"3"` |
| `Rank Change` | String | Yes | 순위 변화 | `"▲5"`, `"▼3"`, `"NEW"`, `"N/A"` |
| `Video Title` | String | Yes | 영상 제목 | `"Amazing Magic Trick"` |
| `Thumbnail` | String | Yes | 썸네일 이미지 URL | `"https://i.ytimg.com/vi/..."`, `"N/A"` |
| `Channel Name` | String | Yes | 채널명 | `"MrBeast"`, `"N/A"` |
| `Subscribers` | String | Yes | 구독자 수 (포맷된 문자열) | `"1.5M"`, `"250K"`, `""` |
| `Views` | String | Yes | 조회수 (포맷된 문자열) | `"1.2M"`, `"350K"`, `"1,234,567"`, `"N/A"` |
| `Upload Date` | String | Yes | 게시 날짜 | `"2024.11.15"`, `"2024-12-01"`, `"N/A"` |
| `Tags` | String | Yes | 해시태그 | `"#kpop,#music"`, `""` |
| `Country` | String | No | 국가 정보 | `"한국"`, `"미국"`, `"일본"` |
| `Type` | String | No | 타겟 타입 | `"shorts"`, `"video"` |

**제외된 컬럼** (수집하지 않음):
- `Video ID`: 사용자에게 불필요
- `Likes`: 데이터 수집이 불안정하여 제외

#### 샘플 데이터 (CSV) - PLAN.md 3.5
```csv
Rank,Rank Change,Video Title,Thumbnail,Channel Name,Subscribers,Views,Upload Date,Tags,Country,Type
1,NEW,Incredible Dance Move,https://i.ytimg.com/vi/dQw4w9WgXcQ/default.jpg,Dance Channel,500K,1.2M,2024.12.01,#dance #shorts,한국,shorts
2,▲3,Funny Cat Compilation,https://i.ytimg.com/vi/jNQXAC9IVRw/default.jpg,Cat Lovers,1.2M,850K,2024.11.28,#cats #funny,한국,shorts
3,▼1,Amazing Magic Trick,N/A,Magic Pro,,500K,2024.12.03,#magic,한국,shorts
```

#### 데이터 타입 변환 (사용 시)
```python
import pandas as pd

df = pd.read_csv('shorts_전체_한국_일간_20251204_153045.csv')

# Views를 숫자로 변환 (K, M 처리)
def parse_views(view_str):
    if view_str == 'N/A' or pd.isna(view_str):
        return 0
    view_str = str(view_str).replace(',', '')
    if 'K' in view_str:
        return float(view_str.replace('K', '')) * 1000
    elif 'M' in view_str:
        return float(view_str.replace('M', '')) * 1000000
    else:
        return float(view_str)

df['Views_Numeric'] = df['Views'].apply(parse_views)
```

---

### 2. 채널 데이터 (`channel`)

#### 파일명 규칙
```
channel_{category}_{country}_{period}_{timestamp}.csv
```

#### 스키마 (컬럼 정의)

| 컬럼명 | 데이터 타입 | Nullable | 설명 | 예시 |
|--------|-------------|----------|------|------|
| `Rank` | String | No | 순위 | `"1"`, `"2"`, `"3"` |
| `Rank Change` | String | Yes | 순위 변화 | `"▲5"`, `"▼3"`, `"NEW"`, `"N/A"` |
| `Channel Name` | String | Yes | 채널명 | `"MrBeast"`, `"N/A"` |
| `Channel ID` | String | Yes | YouTube Channel ID (24자리) | `"UCX6OQ3DkcsbYNE6H8uQQuVA"`, `"N/A"` |
| `Profile Image` | String | Yes | 프로필 이미지 URL | `"https://yt3.ggpht.com/..."`, `"N/A"` |
| `Subscribers` | String | Yes | 구독자 수 (포맷된 문자열) | `"100M"`, `"5.2M"`, `"N/A"` |
| `Total Views` | String | Yes | 총 조회수 (포맷된 문자열) | `"10B"`, `"500M"`, `"N/A"` |

#### 샘플 데이터 (CSV)
```csv
Rank,Rank Change,Channel Name,Channel ID,Profile Image,Subscribers,Total Views
1,NEW,MrBeast,UCX6OQ3DkcsbYNE6H8uQQuVA,https://yt3.ggpht.com/...,200M,30B
2,▲1,PewDiePie,UC-lHJZR3Gqxm24_Vd_AJ5Yw,https://yt3.ggpht.com/...,111M,28B
3,▼2,T-Series,UCq-Fj5jknLsUf-MWSy4_brA,https://yt3.ggpht.com/...,250M,240B
```

---

### 3. 자막 텍스트 파일

#### 파일명 규칙
```
{video_id}_transcript.txt
```

#### 내용 형식
```
전체 자막이 하나의 문자열로 병합되어 저장됨 (시간 정보 제거)
```

#### 샘플 데이터
```
안녕하세요 여러분 오늘은 마법 트릭을 보여드리겠습니다 먼저 카드를 준비하고 이렇게 섞어주세요 그리고 한 장을 선택해주세요...
```

#### 인코딩
- **UTF-8** (한글, 영어, 특수문자 모두 지원)

#### 구조화된 자막 데이터 (원본)
```python
# youtube-transcript-api에서 반환하는 원본 데이터 구조
[
    {'text': '안녕하세요', 'start': 0.0, 'duration': 2.5},
    {'text': '여러분', 'start': 2.5, 'duration': 1.0},
    {'text': '오늘은 마법 트릭을', 'start': 3.5, 'duration': 3.2},
    ...
]

# 현재는 'text' 필드만 추출하여 저장
# 향후 timestamp 정보가 필요하면 JSON 형식으로 저장 가능
```

---

## 🔄 데이터베이스 마이그레이션 계획 (향후)

### SQLite 스키마 제안

현재 CSV 파일 기반 구조를 SQLite로 전환 시 다음과 같은 스키마 사용 가능:

#### 테이블 1: `videos`
```sql
CREATE TABLE videos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    video_id VARCHAR(11) NOT NULL,           -- YouTube Video ID
    title TEXT,
    thumbnail_url TEXT,
    channel_name VARCHAR(255),
    channel_id VARCHAR(24),
    views INTEGER,                            -- 숫자로 변환 저장
    likes INTEGER,
    rank INTEGER,
    rank_change VARCHAR(10),                  -- "▲5", "▼3", "NEW"
    video_type VARCHAR(20),                   -- "shorts", "video"
    category VARCHAR(50),                     -- "음악", "게임" 등
    country VARCHAR(50),                      -- "한국", "미국" 등
    period VARCHAR(20),                       -- "일간", "주간", "월간"
    crawled_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(video_id, category, country, period, crawled_at)  -- 중복 방지
);

-- 인덱스 생성 (검색 성능 향상)
CREATE INDEX idx_video_id ON videos(video_id);
CREATE INDEX idx_crawled_at ON videos(crawled_at);
CREATE INDEX idx_category_country ON videos(category, country);
```

#### 테이블 2: `channels`
```sql
CREATE TABLE channels (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    channel_id VARCHAR(24) NOT NULL UNIQUE,  -- YouTube Channel ID
    channel_name VARCHAR(255),
    profile_image_url TEXT,
    subscribers INTEGER,
    total_views BIGINT,
    rank INTEGER,
    rank_change VARCHAR(10),
    category VARCHAR(50),
    country VARCHAR(50),
    period VARCHAR(20),
    crawled_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_channel_id ON channels(channel_id);
CREATE INDEX idx_crawled_at_ch ON channels(crawled_at);
```

#### 테이블 3: `transcripts`
```sql
CREATE TABLE transcripts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    video_id VARCHAR(11) NOT NULL UNIQUE,    -- videos.video_id 참조
    full_text TEXT,                          -- 전체 자막 텍스트
    language VARCHAR(10),                    -- "ko", "en"
    is_generated BOOLEAN DEFAULT 0,          -- 자동 생성 자막 여부
    extracted_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (video_id) REFERENCES videos(video_id)
);

CREATE INDEX idx_transcript_video_id ON transcripts(video_id);
```

#### 테이블 4: `crawl_logs`
```sql
CREATE TABLE crawl_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    target_type VARCHAR(20),                 -- "shorts", "video", "channel"
    category VARCHAR(50),
    country VARCHAR(50),
    period VARCHAR(20),
    items_collected INTEGER,
    status VARCHAR(20),                      -- "success", "failed", "partial"
    error_message TEXT,
    started_at DATETIME,
    completed_at DATETIME,
    duration_seconds INTEGER
);

CREATE INDEX idx_crawl_logs_started ON crawl_logs(started_at);
```

---

### PostgreSQL 스키마 제안 (대규모 데이터)

```sql
-- 파티셔닝 적용 (월별)
CREATE TABLE videos (
    id SERIAL PRIMARY KEY,
    video_id VARCHAR(11) NOT NULL,
    title TEXT,
    thumbnail_url TEXT,
    channel_name VARCHAR(255),
    channel_id VARCHAR(24),
    views BIGINT,
    likes INTEGER,
    rank INTEGER,
    rank_change VARCHAR(10),
    video_type VARCHAR(20),
    category VARCHAR(50),
    country VARCHAR(50),
    period VARCHAR(20),
    crawled_at TIMESTAMP DEFAULT NOW()
) PARTITION BY RANGE (crawled_at);

-- 월별 파티션 생성 (예시: 2025년 12월)
CREATE TABLE videos_202512 PARTITION OF videos
    FOR VALUES FROM ('2025-12-01') TO ('2026-01-01');

-- 인덱스
CREATE INDEX idx_videos_video_id ON videos(video_id);
CREATE INDEX idx_videos_crawled_at ON videos(crawled_at);
CREATE INDEX idx_videos_category_country ON videos(category, country);

-- Full-Text Search 인덱스 (제목 검색)
CREATE INDEX idx_videos_title_fts ON videos USING GIN(to_tsvector('korean', title));
```

---

## 🔗 테이블 간 관계 (ERD)

```
┌─────────────────┐
│    videos       │
├─────────────────┤
│ id (PK)         │
│ video_id (UK)   │◄──────┐
│ title           │       │
│ channel_id      │───┐   │
│ views           │   │   │
│ rank            │   │   │
│ crawled_at      │   │   │
└─────────────────┘   │   │
                      │   │
                      │   │ (1:1)
┌─────────────────┐   │   │
│  transcripts    │   │   │
├─────────────────┤   │   │
│ id (PK)         │   │   │
│ video_id (FK)   │───┘   │
│ full_text       │       │
│ language        │       │
│ extracted_at    │       │
└─────────────────┘       │
                          │
                          │ (N:1)
                          ▼
                ┌─────────────────┐
                │   channels      │
                ├─────────────────┤
                │ id (PK)         │
                │ channel_id (UK) │
                │ channel_name    │
                │ subscribers     │
                │ total_views     │
                └─────────────────┘
```

---

## 📝 주요 쿼리 예시 (SQLite 기준)

### 1. 특정 카테고리 최신 순위 조회
```sql
SELECT
    rank,
    video_id,
    title,
    channel_name,
    views
FROM videos
WHERE category = '음악'
    AND country = '한국'
    AND period = '일간'
ORDER BY crawled_at DESC, rank ASC
LIMIT 100;
```

### 2. 순위 상승 영상 TOP 10
```sql
SELECT
    rank,
    rank_change,
    title,
    views
FROM videos
WHERE rank_change LIKE '▲%'
ORDER BY CAST(REPLACE(rank_change, '▲', '') AS INTEGER) DESC
LIMIT 10;
```

### 3. 채널별 총 영상 수 및 평균 조회수
```sql
SELECT
    channel_name,
    COUNT(*) as total_videos,
    AVG(views) as avg_views,
    MAX(views) as max_views
FROM videos
WHERE channel_name != 'N/A'
GROUP BY channel_name
ORDER BY total_videos DESC
LIMIT 20;
```

### 4. 자막이 있는 영상만 조회 (JOIN)
```sql
SELECT
    v.video_id,
    v.title,
    v.views,
    t.language,
    LENGTH(t.full_text) as transcript_length
FROM videos v
INNER JOIN transcripts t ON v.video_id = t.video_id
WHERE t.full_text IS NOT NULL
ORDER BY v.views DESC;
```

### 5. 일별 크롤링 통계
```sql
SELECT
    DATE(crawled_at) as crawl_date,
    category,
    COUNT(*) as items_collected,
    AVG(views) as avg_views
FROM videos
GROUP BY DATE(crawled_at), category
ORDER BY crawl_date DESC;
```

---

## 🔍 데이터 검증 규칙

### 1. Video ID 검증
```python
import re

def is_valid_video_id(video_id):
    """
    YouTube Video ID 형식 검증
    - 길이: 11자
    - 허용 문자: A-Z, a-z, 0-9, _, -
    """
    if video_id == 'N/A':
        return True  # N/A는 허용
    pattern = r'^[A-Za-z0-9_-]{11}$'
    return bool(re.match(pattern, video_id))
```

### 2. Channel ID 검증
```python
def is_valid_channel_id(channel_id):
    """
    YouTube Channel ID 형식 검증
    - 길이: 24자
    - 시작: UC
    - 허용 문자: A-Z, a-z, 0-9, _, -
    """
    if channel_id == 'N/A':
        return True
    pattern = r'^UC[A-Za-z0-9_-]{22}$'
    return bool(re.match(pattern, channel_id))
```

### 3. Views 숫자 변환 검증
```python
def parse_views(view_str):
    """
    Views 문자열 → 정수 변환
    입력: "1.2M", "350K", "1,234,567"
    출력: 1200000, 350000, 1234567
    """
    if view_str == 'N/A' or pd.isna(view_str):
        return 0

    view_str = str(view_str).replace(',', '').strip()

    if 'B' in view_str:
        return int(float(view_str.replace('B', '')) * 1_000_000_000)
    elif 'M' in view_str:
        return int(float(view_str.replace('M', '')) * 1_000_000)
    elif 'K' in view_str:
        return int(float(view_str.replace('K', '')) * 1_000)
    else:
        return int(float(view_str))
```

---

## 🚀 데이터 마이그레이션 스크립트 (CSV → SQLite)

```python
import sqlite3
import pandas as pd
import glob
from datetime import datetime

def migrate_csv_to_sqlite(csv_dir='output', db_path='youtube_crawler.db'):
    """
    모든 CSV 파일을 SQLite 데이터베이스로 마이그레이션
    """
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    # 테이블 생성 (위 스키마 참조)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS videos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            video_id VARCHAR(11),
            title TEXT,
            thumbnail_url TEXT,
            channel_name VARCHAR(255),
            views INTEGER,
            likes INTEGER,
            rank INTEGER,
            rank_change VARCHAR(10),
            video_type VARCHAR(20),
            category VARCHAR(50),
            country VARCHAR(50),
            period VARCHAR(20),
            crawled_at DATETIME
        )
    ''')

    # CSV 파일 목록 가져오기
    csv_files = glob.glob(f'{csv_dir}/*.csv')

    for csv_file in csv_files:
        print(f"Migrating {csv_file}...")

        # CSV 파일명에서 메타데이터 추출
        filename = csv_file.split('/')[-1].replace('.csv', '')
        parts = filename.split('_')

        target_type = parts[0]  # shorts, video, channel
        category = parts[1]
        country = parts[2]
        period = parts[3]
        timestamp_str = '_'.join(parts[4:])

        # CSV 읽기
        df = pd.read_csv(csv_file)

        # Views를 숫자로 변환
        df['Views_Numeric'] = df['Views'].apply(parse_views)
        df['Likes_Numeric'] = df['Likes'].apply(parse_views)

        # crawled_at 추가
        df['category'] = category
        df['country'] = country
        df['period'] = period
        df['video_type'] = target_type
        df['crawled_at'] = datetime.strptime(timestamp_str, '%Y%m%d_%H%M%S')

        # DB에 삽입
        for _, row in df.iterrows():
            cursor.execute('''
                INSERT INTO videos (
                    video_id, title, thumbnail_url, channel_name,
                    views, likes, rank, rank_change, video_type,
                    category, country, period, crawled_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                row['Video ID'], row['Video Title'], row['Thumbnail'],
                row['Channel Name'], row['Views_Numeric'], row['Likes_Numeric'],
                row['Rank'], row['Rank Change'], row['video_type'],
                row['category'], row['country'], row['period'], row['crawled_at']
            ))

    conn.commit()
    conn.close()
    print(f"Migration complete! Database saved to {db_path}")
```

---

## 📊 데이터 사이징 (예상)

### CSV 파일 크기
- **쇼츠/영상 100개**: 약 20KB
- **채널 100개**: 약 15KB
- **자막 1개**: 약 5KB ~ 50KB (영상 길이에 따라)

### SQLite 데이터베이스 크기 (예상)
- **1만 개 영상**: 약 5MB
- **10만 개 영상**: 약 50MB
- **100만 개 영상**: 약 500MB

### 인덱스 크기
- **인덱스**: 테이블 크기의 약 20% 추가

---

---

## 📊 수집 이력 조회 API (2025-12-05 추가)

### 1. 기간별 수집 이력 조회

#### `get_crawl_history_by_period(period_type, start_date, end_date)`

**목적**: crawl_history 테이블에서 기간별 크롤링 이력 조회

**SQL 쿼리**:
```sql
-- 일간 (오늘)
SELECT * FROM crawl_history
WHERE date(crawled_at) = date('now', 'localtime')
ORDER BY crawled_at DESC

-- 주간 (최근 7일)
SELECT * FROM crawl_history
WHERE date(crawled_at) >= date('now', '-7 days', 'localtime')
ORDER BY crawled_at DESC

-- 월간 (최근 30일)
SELECT * FROM crawl_history
WHERE date(crawled_at) >= date('now', '-30 days', 'localtime')
ORDER BY crawled_at DESC

-- 커스텀 (사용자 지정 범위)
SELECT * FROM crawl_history
WHERE date(crawled_at) BETWEEN ? AND ?
ORDER BY crawled_at DESC
```

**반환 데이터**:
```python
[
    {
        'id': 1,
        'type': 'shorts',
        'category': 'music',
        'country': 'south-korea',
        'period': 'daily',
        'item_count': 50,
        'success': 1,
        'crawled_at': '2025-12-05 10:30:00'
    },
    ...
]
```

### 2. 카테고리별 수집 현황 조회

#### `get_collection_status(period_type, start_date, end_date)`

**목적**: 특정 기간 내 카테고리별 수집 여부 확인 (체크박스 표시용)

**로직**:
```python
# 1. 수집 이력 조회
history = get_crawl_history_by_period(period_type, start_date, end_date)

# 2. 카테고리별 수집 여부 체크
status = {
    'shorts': {},
    'video': {},
    'channel_popular': {},
    'channel_growth': {}
}

for record in history:
    if record['type'] == 'shorts':
        status['shorts'][record['category']] = True
    elif record['type'] == 'video':
        status['video'][record['category']] = True
    elif record['type'] == 'channel' and 'popular' in record['category']:
        status['channel_popular'][record['category']] = True
    elif record['type'] == 'channel' and 'growth' in record['category']:
        status['channel_growth'][record['category']] = True
```

**반환 데이터**:
```python
{
    'shorts': {
        'all': True,
        'music': True,
        'gaming': False,
        'film': True,
        ...
    },
    'video': {
        'all': False,
        'music': True,
        'gaming': True,
        ...
    },
    'channel_popular': {
        'all': False,
        'music': True,
        ...
    },
    'channel_growth': {
        'all': True,
        'vlog': True,
        ...
    }
}
```

### 3. 수집 요약 통계

#### `get_collection_summary(period_type, start_date, end_date)`

**목적**: 특정 기간의 수집 통계 요약

**SQL 쿼리**:
```sql
-- 총 크롤링 수
SELECT COUNT(*) as total_crawls FROM crawl_history
WHERE date(crawled_at) >= ?

-- 총 아이템 수
SELECT SUM(item_count) as total_items FROM crawl_history
WHERE date(crawled_at) >= ?

-- 타입별 통계
SELECT type, COUNT(*) as count, SUM(item_count) as total_items
FROM crawl_history
WHERE date(crawled_at) >= ?
GROUP BY type
```

**반환 데이터**:
```python
{
    'total_crawls': 15,
    'total_items': 750,
    'by_type': {
        'shorts': {'crawls': 5, 'items': 250},
        'video': {'crawls': 5, 'items': 250},
        'channel': {'crawls': 5, 'items': 250}
    },
    'daily_stats': [
        {'date': '2025-12-05', 'crawls': 5, 'items': 250},
        {'date': '2025-12-04', 'crawls': 3, 'items': 150},
        ...
    ]
}
```

### 4. 데이터베이스 검색 API

#### `/api/db_search` 엔드포인트

**목적**: shorts_rank, videos_rank, channels_rank 3개 테이블 통합 검색

**쿼리 파라미터**:
- `keyword`: 제목/채널명 검색 (LIKE 쿼리)
- `type`: 'shorts', 'videos', 'channels', 'all' (기본: 'all')
- `category`: 카테고리 필터
- `country`: 국가 필터
- `period`: 기간 필터

**SQL 쿼리 예시**:
```sql
-- 쇼츠 검색
SELECT 'shorts_rank' as table_name, * FROM shorts_rank
WHERE (title LIKE '%음악%' OR channel_name LIKE '%음악%')
  AND category = 'music'
  AND country = 'south-korea'
  AND period = 'daily'
ORDER BY crawled_at DESC

UNION ALL

-- 영상 검색
SELECT 'videos_rank' as table_name, * FROM videos_rank
WHERE (title LIKE '%음악%' OR channel_name LIKE '%음악%')
  AND category = 'music'
  AND country = 'south-korea'
  AND period = 'daily'
ORDER BY crawled_at DESC

UNION ALL

-- 채널 검색
SELECT 'channels_rank' as table_name, * FROM channels_rank
WHERE channel_name LIKE '%음악%'
  AND category = 'music'
  AND country = 'south-korea'
ORDER BY crawled_at DESC
```

**반환 데이터**:
```json
{
    "status": "success",
    "results": [
        {
            "table": "shorts_rank",
            "video_id": "rank_1_음악영상",
            "title": "인기 음악 쇼츠",
            "channel_name": "음악 채널",
            "views": 1500000,
            "rank": 1,
            "category": "music",
            "country": "south-korea",
            "period": "daily",
            "crawled_at": "2025-12-05 10:30:00"
        },
        ...
    ],
    "count": 15
}
```

**동적 WHERE 절 구성**:
```python
where_clauses = []
params = []

if keyword:
    where_clauses.append("(title LIKE ? OR channel_name LIKE ?)")
    params.extend([f'%{keyword}%', f'%{keyword}%'])

if category and category != 'all':
    where_clauses.append("category = ?")
    params.append(category)

if country:
    where_clauses.append("country = ?")
    params.append(country)

if period:
    where_clauses.append("period = ?")
    params.append(period)

where_sql = " AND ".join(where_clauses) if where_clauses else "1=1"
query = f"SELECT * FROM {table_name} WHERE {where_sql} ORDER BY crawled_at DESC"
```

---

**문서 관리자**: AI 자동 생성
**최종 검토일**: 2025-12-05
**다음 업데이트**: DB 마이그레이션 시
