# DB 통계 및 검색 기술 가이드라인 (DB_STATISTICS_AND_SEARCH_GUIDE.md)

이 문서는 **DB 통계 및 검색 탭**의 SQLite3 데이터베이스 아키텍처, 테이블 스키마, 통합 검색 모듈 구조 및 쿼리 활용법을 설명합니다.

---

## 1. 🗄️ SQLite 데이터베이스 스키마 명세

대시보드와 검색 및 API 동기화 레이어가 통합 공유하는 SQLite3 데이터베이스(`youtube_data.db`)의 테이블 구조는 다음과 같습니다.

### 1.1. shorts_rank 및 videos_rank (쇼츠/일반 영상 랭킹 이력)
각각 쇼츠 카테고리 랭킹과 일반 비디오 랭킹의 데이터 이력을 저장합니다.
- `UNIQUE(video_id, category, country, period, crawled_at)` 제약조건을 걸어 동일 영상이 다양한 국가/카테고리/주기 차트에 진입했을 때의 데이터 무결성을 보장합니다.

### 1.2. channels_rank (채널 인기 및 급상승 랭킹)
인기 순위 및 급상승 랭킹 데이터를 수집합니다.
- **주요 속성**:
  - `channel_url`: Playboard 채널 상세 주소 (Zero-Cost ID Extraction의 소스가 됨)
  - `ranking_type`: 랭킹 유형 구분 (`popular`: 인기순위, `growth`: 구독자급상승)
  - `subscriber_count`: 구독자 수 문자열 (예: "1.2M", "500K")
  - `total_views`: 채널 전체 누적 조회수

### 1.3. api_channels (YouTube API 연동 채널 데이터)
- **역할**: Zero-Cost ID Extraction으로 추출한 Channel ID를 통해 YouTube API v3에서 수집한 상세 속성을 저장합니다.
- **주요 속성**:
  - `uploads_playlist_id`: 채널의 모든 업로드 비디오 목록 재생목록 ID (영상 동기화 시 필수 참조)

### 1.4. api_videos (YouTube API 연동 영상 데이터)
- **역할**: YouTube API를 통해 수집된 채널 소속 동영상들의 상세 수치 및 재생 시간 정보를 분류 보관합니다.
- **주요 속성**:
  - `duration_sec`: 재생 시간(초) 환산 정수 (검색/필터링용)
  - `video_type`: 재생 시간이 60초 이하이면 `shorts`, 그렇지 않으면 `video`로 자동 판별 및 저장

---

## 2. 🔍 데이터 통합 검색 시스템 구조

대시보드 메인페이지의 **DB 통계 및 검색 탭**에서는 수집된 크롤링 데이터와 API 연동 데이터를 하나의 키워드 검색 엔진으로 통합 탐색할 수 있는 고급 쿼리 필터를 제공합니다.

### 2.1. 통합 검색 필터 매개변수
* **검색 키워드**: 제목, 채널명, 태그 컬럼에 대한 `LIKE` 와일드카드 매칭
* **데이터 소스**:
  - `크롤링 데이터만`: `shorts_rank`, `videos_rank`, `channels_rank` 테이블 쿼리
  - `API 연동 데이터만`: `api_videos` 테이블 쿼리
* **컨텐츠 타입**: `전체`, `shorts`, `video`, `channel`
* **고급 제약조건**:
  - `조회수 범위`: 최소/최대 조회수 필터링
  - `게시일 범위`: 시작/종료일 지정 필터 (API 비디오 전용)
  - `정렬 기준`: 최근 등록일순, 조회수 높은순

---

## 3. 📈 데이터 통계 및 시각화 분석 (Plotly Express)

수집된 데이터베이스의 트렌드를 요약하여 시인성 높은 시각화 그래프를 제공합니다.

### 3.1. Shorts 수집 카테고리 분포 (Pie Chart)
- **역할**: 어떤 분야의 쇼츠 영상들이 주로 수집되었는지 카테고리별 비중을 나타냅니다.
- **쿼리 구현**:
  ```sql
  SELECT category, COUNT(*) as count FROM shorts_rank GROUP BY category
  ```

### 3.2. 인기 채널 TOP 10 (Bar Chart)
- **역할**: 데이터베이스 내 수집된 채널 정보 중 최대 구독자 수를 기준으로 정렬된 최상위 10개 채널 정보를 시각화합니다.
- **쿼리 구현**:
  ```sql
  SELECT channel_name, MAX(subscriber_count) as subscribers 
  FROM channels_rank 
  WHERE channel_name != 'N/A'
  GROUP BY channel_name 
  ORDER BY subscribers DESC 
  LIMIT 10
  ```

---

## 4. ⚙️ 검색 설정값 자동 보존 (Query Parameters Auto-Persistence)

검색 탭의 사용성 극대화를 위해 사용자가 필터링한 검색 키워드, 검색 대상 소스, 컨텐츠 종류, 최소/최대 조회수, 날짜 범위(시작/종료일) 및 정렬 조건의 변경이 발생하는 즉시 `output/settings.json`에 스마트 직렬화 저장됩니다.
이를 통해 대시보드를 새로고침하거나 브라우저를 재부팅해도 이전에 사용하던 검색 필터 상태가 그대로 복구되어 다시 복잡한 조건을 입력할 필요가 없도록 개선되었습니다.

