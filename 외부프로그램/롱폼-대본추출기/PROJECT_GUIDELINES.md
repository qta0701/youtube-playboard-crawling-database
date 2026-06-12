# 프로젝트 파일 구조 및 역할 분담 지침

## 파일별 역할 분담

### Main_Extract.py
**담당 영역: 대본 추출 및 브라우저 자동화**

#### 주요 기능
- YouTube 대본 추출 (youtube-transcript-api, Innertube API, HTTP 방식, 브라우저 자동화)
- Selenium/ChromeDriver 브라우저 자동화
- 대본 데이터 처리 및 변환
- 영상 메타데이터 추출
- 배치 처리 로직

#### 포함되는 클래스/함수
- `BrowserTranscriptExtractor`: 브라우저 자동화 대본 추출
- `MainYouTubeShortsTranscriptExtractor`: HTTP 방식 대본 추출
- `InterruptController`: 중단 제어
- 브라우저 관련 유틸리티 함수들
- 다중 폴백 시스템 (youtube-transcript-api → Innertube API → HTTP)

#### 제외되는 기능
- ❌ 구글 닥스/TXT 파일 생성
- ❌ 구글 드라이브 업로드
- ❌ 구글 시트 헤더 업데이트 (구글닥스여부, 닥스파일ID 등)
- ❌ 썸네일 처리
- ❌ 40000자 이상 대본 특별 처리
- ❌ 구글 시트 데이터 필터링/조건부 추출

### GUI_Extract.py
**담당 영역: 구글 시트 관리, 데이터 처리 및 GUI 인터페이스**

#### 주요 기능
- 구글 닥스/TXT 파일 생성 및 업로드
- 구글 드라이브 폴더 관리
- 40000자 이상 대본 특별 처리
- 썸네일 다운로드 및 처리
- 구글 시트 관리 및 헤더 업데이트
- **구글 시트 조건부 필터링 및 데이터 추출**
- **채널 리스트 - 영상 시트 데이터 동기화**
- GUI 인터페이스 제공

#### 포함되는 클래스/함수
- `GoogleSheetsManager`: 구글 시트 관리
- `TranscriptExtractorGUI`: GUI 인터페이스
- 구글 닥스/드라이브 관련 함수들
- 썸네일 처리 함수들
- **데이터 필터링 및 추출 함수들**
- **시트 간 데이터 동기화 함수들**

#### 제외되는 기능
- ❌ 직접적인 YouTube 대본 추출
- ❌ 브라우저 자동화 로직
- ❌ Selenium/ChromeDriver 제어

## 상호 협력 방식

### Main_Extract.py → GUI_Extract.py
- 대본 추출 완료 후 VideoData 객체 반환
- GUI_Extract.py에서 Main_Extract의 추출기 클래스를 import하여 사용

```python
# GUI_Extract.py에서 Main_Extract 사용 예시
from Main_Extract import MainYouTubeShortsTranscriptExtractor

async with MainYouTubeShortsTranscriptExtractor(config) as extractor:
    results = await extractor.process_videos_batch(video_ids, progress_callback=callback)
```

### GUI_Extract.py → Main_Extract.py
- 대본 추출 결과를 받아서 40000자 이상인 경우 구글 닥스 생성
- 구글 시트 헤더 업데이트는 GUI_Extract.py에서만 처리

## 데이터 흐름

```
1. 사용자 요청 (GUI_Extract.py)
   ↓
2. 대본 추출 (Main_Extract.py)
   ↓
3. 결과 분석 (GUI_Extract.py)
   ↓
4. 40000자 이상? → 구글 닥스 생성 (GUI_Extract.py)
   ↓
5. 시트 헤더 업데이트 (GUI_Extract.py)
```

## 코딩 규칙

### Main_Extract.py 수정 시
1. 구글 닥스/TXT 관련 코드 추가 금지
2. 구글 시트 헤더 업데이트 로직 추가 금지
3. 순수 대본 추출 기능에만 집중
4. VideoData 객체로 결과 반환
5. 다중 폴백 시스템 유지 (youtube-transcript-api → Innertube API → HTTP)

### GUI_Extract.py 수정 시
1. 직접적인 YouTube 대본 추출 금지
2. 브라우저 자동화 로직 추가 금지
3. Main_Extract의 추출기 클래스 활용
4. 40000자 체크 후 구글 닥스 처리
5. **구글 시트 데이터 읽기 시 UNFORMATTED_VALUE 사용 필수**
6. **API 호출 최소화를 위한 캐싱 전략 준수**

## Google Sheets 데이터 처리 지침

### 1. 데이터 읽기 원칙
**문제**: 포맷된 값("1,265개")을 읽으면 수식/함수에서 인식 불가
**해결**: 항상 UNFORMATTED_VALUE 사용

```python
# ✅ 올바른 방법
result = sheet.spreadsheet.values_get(
    sheet.title,
    params={'valueRenderOption': 'UNFORMATTED_VALUE'}
)

# ❌ 잘못된 방법 (기본값은 FORMATTED_VALUE)
values = sheet.get_all_values()  # "1,265개" 반환
```

**핵심 함수**: `get_all_values_unformatted()` (GUI_Extract.py Line 200-215)

### 2. 시트 구조 복제 원칙
**문제**: 값만 복사하면 수식/포맷이 깨짐
**해결**: Google Sheets API의 copyPaste 요청 사용

```python
# ✅ 올바른 방법 - 1~9행 완전 복제
requests = [{
    'copyPaste': {
        'source': {
            'sheetId': source_sheet.id,
            'startRowIndex': 0,
            'endRowIndex': 9,
            ...
        },
        'destination': {...},
        'pasteType': 'PASTE_NORMAL'
    }
}]
spreadsheet.batch_update({'requests': requests})

# ❌ 잘못된 방법
values = source_sheet.get_all_values()
target_sheet.update(values)  # 수식이 값으로 변환됨
```

**적용 위치**: GUI_Extract.py Line 4427-4471

### 3. 숫자 포맷 보존 원칙
**문제**: raw 값만 복사하면 날짜/퍼센트 등 표시 형식 손실
**해결**: numberFormat 별도 복사

```python
# 1단계: raw 값 복사 (UNFORMATTED_VALUE)
target_sheet.update(raw_values, value_input_option='RAW')

# 2단계: 숫자 포맷 복사
_copy_number_formats(source_sheet, target_sheet, start_row=10)
```

**핵심 함수**: `_copy_number_formats()` (GUI_Extract.py Line 4316-4387)

### 4. API 호출 최적화 원칙
**문제**: Google Sheets API 읽기 제한 (60회/분) 초과
**해결**: 3단계 캐싱 전략

#### 캐싱 레벨
1. **헤더 매핑 캐시** (GUI_Extract.py Line 3652-3718)
   - 시트당 1회만 헤더 읽기
   - 컬럼명 → 인덱스 매핑 저장

2. **시트 데이터 캐시** (GUI_Extract.py Line 4651-4657)
   - 실시간 카운트 조회 시 재사용
   - 시트 변경 시에만 갱신

3. **디바운스 패턴** (GUI_Extract.py Line 6708-6726)
   - 숫자 입력 필드: 2초 지연 후 API 호출
   - 불필요한 중간 호출 제거

```python
# 디바운스 구현 예시
def on_numeric_condition_change(self):
    if self.debounce_timer is not None:
        self.root.after_cancel(self.debounce_timer)

    self.debounce_timer = self.root.after(
        2000,  # 2초 대기
        self._debounced_update_real_time_count
    )
```

## 데이터 동기화 지침

### 채널 리스트 ↔ 영상 시트 양방향 동기화 (대규모 데이터 최적화)
**기능**: 채널별 최신 수집 날짜 기준으로 양방향 데이터 동기화

**대규모 데이터 처리 최적화**:
- **영상 데이터**: 10,000개 이상
- **채널 수**: 100개 이상
- **처리 시간**: 기존 O(n²) → 최적화 O(n)

**양방향 동기화 프로세스**:
1. **데이터 인덱싱**:
   - 영상 시트 전체를 채널 ID별로 사전 구조화 (defaultdict 사용)
   - 채널 ID별 모든 영상 행 번호 추적 (영상 시트 업데이트용)

2. **날짜 비교 및 방향 결정**:
   - **영상 시트가 더 최신** → 채널 리스트 업데이트
   - **채널 리스트가 더 최신** → 영상 시트의 해당 채널 모든 행 업데이트
   - 업데이트 시 양쪽 시트의 수집날짜도 동일하게 갱신

3. **분야1/분야2 특별 처리** (단방향):
   - 채널 리스트 → 영상 시트만 업데이트
   - 값이 다른 경우에만 갱신
   - 해당 채널의 모든 영상 행에 일괄 적용

4. **기간 필터 적용** (선택):
   - N일 이상 차이나는 채널만 처리
   - 불필요한 업데이트 제거

5. **배치 업데이트**:
   - 100개씩 묶어서 API 호출 (429 에러 방지)
   - 채널 리스트, 영상 시트 각각 배치 처리

6. **9행 수집날짜 열 갱신**:
   - 양쪽 시트의 **9행 수집날짜 컬럼**만 최신 날짜로 자동 업데이트
   - 전체 처리 중 가장 최신 날짜 사용
   - ⚠️ 9행은 헤더/전역함수가 있으므로 수집날짜 열만 업데이트

**핵심 함수**: `update_channel_list_from_video_sheet()` (GUI_Extract.py Line 4389-4777)

**파라미터**:
- `spreadsheet_url`: 스프레드시트 URL
- `video_sheet_name`: 영상 시트 이름
- `days_threshold`: 기간 필터 (일수, None이면 전체 업데이트)
- `progress_callback`: 진행률 콜백 함수 (선택)

**주의사항**:
- 날짜 형식: "yyyy-mm-dd" 문자열 비교
- 빈 셀 처리: 빈 문자열("")로 비교 시 제외
- 헤더 매칭: 정규식으로 번호 제거 후 비교
- **인덱싱 먼저**: 반복문 전에 전체 데이터 인덱싱 필수 (2개 인덱스)
- **배치 크기**: 100개 단위 (너무 크면 타임아웃, 너무 작으면 비효율)
- **양방향 처리**: 영상 시트 업데이트 시 해당 채널의 모든 영상 행 갱신
- **분야 필드**: 무조건 채널 리스트 우선 (단방향)

**동기화 규칙 요약**:
| 조건 | 업데이트 방향 | 대상 |
|------|--------------|------|
| 영상 시트 수집날짜 > 채널 리스트 | 영상 → 채널 | 채널 리스트 1개 행 |
| 채널 리스트 수집날짜 > 영상 시트 | 채널 → 영상 | 영상 시트 모든 해당 채널 행 |
| 분야1/분야2 값 다름 | 채널 → 영상 | 영상 시트 모든 해당 채널 행 |
| 처리 완료 | 양쪽 모두 | 9행 **수집날짜 열**만 최신 날짜로 갱신 |

**성능 개선 결과**:
```
Before: 100개 채널 × 10,000개 영상 = 1,000,000번 비교 (약 30분)
After:  100개 채널 + 10,000개 인덱싱 × 2 = 20,100번 연산 (약 2-3분)
→ 약 10-15배 성능 향상 (양방향 처리 포함)
```

## 주의사항

### 절대 금지 사항
- Main_Extract.py에 구글 닥스 관련 기능 추가
- GUI_Extract.py에 브라우저 자동화 기능 추가
- 두 파일 간 기능 중복 구현
- **FORMATTED_VALUE로 데이터 읽기 (조건부 추출/필터링 시)**
- **시트 구조 복제 시 값만 복사하기**
- **캐시 없이 반복적인 API 호출**

### 권장 사항
- 새로운 기능 개발 시 위 역할 분담 준수
- 상호 의존성 최소화
- 인터페이스 통일 (VideoData, TranscriptConfig 등)
- **데이터 읽기 전 UNFORMATTED_VALUE 사용 여부 확인**
- **API 호출 빈도가 높은 기능은 캐싱 적용**
- **시트 구조 변경 시 copyPaste 우선 고려**

## 버전 관리
- 파일 수정 시 이 지침 문서도 함께 업데이트
- 역할 변경이 필요한 경우 사전 검토 필수
- API 호출 최적화 관련 변경 사항은 반드시 문서화

## 주요 개선 이력

### 2025-11-11 (3차): 양방향 동기화 구현
- **양방향 동기화**: 채널 리스트 ↔ 영상 시트 (수집날짜 기준)
- **분야1/분야2 단방향 처리**: 채널 리스트 → 영상 시트 (값이 다른 경우만)
- **수집날짜 동기화**: 업데이트 시 양쪽 시트 날짜 동일하게 갱신
- **9행 수집날짜 열 양쪽 갱신**: 채널 리스트 + 영상 시트의 9행 수집날짜 컬럼만 최신 날짜로 업데이트
- **영상 시트 일괄 갱신**: 채널 리스트가 최신일 때 해당 채널의 모든 영상 행 업데이트

### 2025-11-11 (2차): 대규모 데이터 처리 최적화
- 채널-영상 매칭 업데이트 대규모 최적화 (영상 1만개, 채널 100개)
- 영상 데이터 인덱싱 (O(n²) → O(n) 시간복잡도 개선)
- 배치 업데이트 (100개씩 묶어서 API 호출 최소화)
- 기간 필터 기능 (N일 이상 차이나는 채널만 선택적 업데이트)
- 진행률 표시 (실시간 처리 상황 모니터링)
- 9행 헤더 날짜 자동 갱신 (최신 수집날짜로 동기화)

### 2025-11-11 (1차): Google Sheets 데이터 처리 개선
- UNFORMATTED_VALUE 전략 도입 (포맷된 값 문제 해결)
- copyPaste API를 이용한 시트 구조 완전 복제
- 숫자 포맷 보존 기능 추가
- 3단계 캐싱 전략 구현 (API 429 에러 해결)
- 디바운스 패턴 적용 (입력 필드 성능 개선)
- 채널-영상 데이터 동기화 기능 추가

### 2025-10-09: YouTube 자막 추출 안정화
- youtube-transcript-api 라이브러리 통합
- Innertube API 직접 호출 폴백 추가
- UnboundLocalError 수정
- GCP 연결 상태 진단 추가

### 2025-08-11: 초기 파일 역할 분담 정의
- Main_Extract.py: 대본 추출 전담
- GUI_Extract.py: 구글 서비스 및 GUI 전담

---
**최종 업데이트: 2025-11-11**
**담당자: AI Assistant**