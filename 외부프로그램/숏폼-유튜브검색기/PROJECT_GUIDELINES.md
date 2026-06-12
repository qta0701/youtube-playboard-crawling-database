# 프로젝트 파일 구조 및 역할 분담 지침

## 프로젝트 개요
본 프로젝트는 YouTube Data API를 사용하여 영상 정보를 검색하고 수집한 뒤, 구글 시트에 저장 및 관리하는 시스템입니다.

## 파일별 역할 분담

### Main_Search.py
**담당 영역: YouTube 검색 API 및 데이터 추출**

#### 주요 기능
- YouTube Data API를 사용한 영상 검색
- 영상 상세 정보 추출 (조회수, 좋아요, 댓글 등)
- 채널 정보 조회
- 재생목록/채널의 영상 수집
- API 쿼터 사용량 추적
- OAuth/API Key 인증 관리

#### 포함되는 클래스/함수
- `YouTubeSearchAPI`: YouTube Data API 검색 및 데이터 추출
- `InterruptController`: 중단 제어
- 영상 데이터 추출 관련 유틸리티 함수들
- API 인증 관련 함수들

#### 제외되는 기능
- ❌ 구글 시트 관리
- ❌ 데이터 필터링/조건부 추출
- ❌ GUI 인터페이스
- ❌ 채널 리스트 동기화

### GUI_Interface.py
**담당 영역: 구글 시트 관리 및 GUI 인터페이스**

#### 주요 기능
- 구글 시트 관리 및 헤더 업데이트
- **구글 시트 조건부 필터링 및 데이터 추출**
- **채널 리스트 ↔ 영상 시트 데이터 동기화**
- **대규모 데이터(1만+ 행) 배치 처리**
- GUI 인터페이스 제공
- 실시간 진행률 표시

#### 포함되는 클래스/함수
- `GoogleSheetsManager`: 구글 시트 관리
- `YouTubeSearchGUI`: GUI 인터페이스
- **데이터 필터링 및 추출 함수들**
- **시트 간 데이터 동기화 함수들**
- 헤더 매칭 및 매핑 함수들

#### 제외되는 기능
- ❌ 직접적인 YouTube API 호출
- ❌ YouTube 검색 로직

## 상호 협력 방식

### Main_Search.py → GUI_Interface.py
- YouTube 검색 완료 후 영상 데이터 딕셔너리 리스트 반환
- GUI_Interface.py에서 Main_Search의 API 클래스를 import하여 사용

```python
# GUI_Interface.py에서 Main_Search 사용 예시
from Main_Search import YouTubeSearchAPI

youtube_api = YouTubeSearchAPI()
youtube_api.authenticate_oauth(client_secret_file)
results, stats = youtube_api.search_videos(keyword, max_results=50)
```

### GUI_Interface.py → Main_Search.py
- 검색 결과를 받아서 구글 시트에 저장
- 구글 시트 헤더 업데이트는 GUI_Interface.py에서만 처리

## 데이터 흐름

```
1. 사용자 요청 (GUI_Interface.py)
   ↓
2. YouTube 검색 (Main_Search.py)
   ↓
3. 결과 저장 (GUI_Interface.py → Google Sheets)
   ↓
4. 채널 리스트 동기화 (GUI_Interface.py)
   ↓
5. 시트 헤더 업데이트 (GUI_Interface.py)
```

## 코딩 규칙

### Main_Search.py 수정 시
1. 구글 시트 관련 코드 추가 금지
2. 순수 YouTube API 호출 및 데이터 추출 기능에만 집중
3. 딕셔너리 형식으로 결과 반환
4. API 쿼터 사용량 추적 유지
5. 인증 관련 에러 처리 철저히

### GUI_Interface.py 수정 시
1. 직접적인 YouTube API 호출 금지
2. Main_Search의 API 클래스 활용
3. **구글 시트 데이터 읽기 시 UNFORMATTED_VALUE 사용 필수**
4. **API 호출 최소화를 위한 캐싱 전략 준수**
5. **대규모 데이터 처리 시 배치 처리 사용**

## Google Sheets 데이터 처리 지침

### 시트 헤더 구조 및 중앙 관리
본 프로젝트는 **sheet_config.py**에서 모든 시트 구조를 중앙 관리합니다.

#### 공용 헤더 관리 시스템 (sheet_config.py)
- **모든 시트 헤더 정의**: 영상 리스트, 채널 리스트, 재생목록ID
- **전역함수 열 정의**: 시트별 9행 전역함수 위치 관리
- **넘버링 자동 제거**: "1. 영상 ID" → "영상ID" 자동 정규화
- **띄어쓰기 허용 매칭**: "영상 ID"와 "영상ID" 동일하게 처리
- **시트 타입 자동 감지**: 헤더로 시트 종류 자동 판별

**핵심 함수**:
- `normalize_header()`: 헤더 정규화 (넘버링 제거, 공백 제거)
- `detect_sheet_type()`: 헤더로 시트 타입 자동 감지
- `create_header_mapping()`: 헤더→인덱스 매핑 생성
- `is_formula_column()`: 전역함수 열 여부 확인

#### 영상 리스트 시트
- **1행**: 헤더 (63개 열, A~BK)
- **9행**: 전역함수 (일부 열만)
- **10행 이후**: 데이터 (1만+ 행)

**전역함수 열** (9행) - sheet_config.VIDEO_LIST_FORMULA_COLUMNS:
- 숏폼여부, 영상 업로드 이후 수집날짜까지 기간, 일평균 조회수
- 조회수 100만/500만/1000만 이상, 구독자 대비 조회수 몇 배 이상, 좋아요 3% 이상
- 채널개설 이후 수집일까지 경과일, 카테고리 분류, 사용 해시태그, 대본유무, 대본 텍스트수

#### 채널 리스트 시트
- **1행**: 헤더 (37개 열, A~AK)
- **9행**: 전역함수 (일부 열만)
- **10행 이후**: 데이터

**전역함수 열** (9행) - sheet_config.CHANNEL_LIST_FORMULA_COLUMNS:
- 채널전체 조회수(변환), 영상당 평균 조회수(전투력), 영상당 구독자수, 구독자1명 당 조회수
- 조회수 100만/500만/1000만 이상 비율, 개설 이후 수집날짜까지 기간

#### 재생목록ID 시트
- **1행**: 헤더 (4개 열, A~D)
- **2행 이후**: 데이터 (전역함수 없음)

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

**핵심 함수**: `get_all_values_unformatted()` (GUI_Interface.py)

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

**적용 위치**: GUI_Interface.py (시트 구조 복제 시)

### 3. 숫자 포맷 보존 원칙
**문제**: raw 값만 복사하면 날짜/퍼센트 등 표시 형식 손실
**해결**: numberFormat 별도 복사

```python
# 1단계: raw 값 복사 (UNFORMATTED_VALUE)
target_sheet.update(raw_values, value_input_option='RAW')

# 2단계: 숫자 포맷 복사
_copy_number_formats(source_sheet, target_sheet, start_row=10)
```

**핵심 함수**: `_copy_number_formats()` (GUI_Interface.py)

### 4. API 호출 최적화 원칙
**문제**: Google Sheets API 읽기 제한 (60회/분) 초과
**해결**: 3단계 캐싱 전략

#### 캐싱 레벨
1. **헤더 매핑 캐시** (GUI_Interface.py)
   - 시트당 1회만 헤더 읽기
   - 컬럼명 → 인덱스 매핑 저장

2. **시트 데이터 캐시** (GUI_Interface.py)
   - 실시간 카운트 조회 시 재사용
   - 시트 변경 시에만 갱신

3. **디바운스 패턴** (GUI_Interface.py)
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

**핵심 함수**: `update_channel_list_from_video_sheet()` (GUI_Interface.py)

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

### 5. 표시 형식 자동 복사 원칙 ✨ 신규
**문제**: raw 값만 복사하면 날짜/퍼센트 등 표시 형식 손실
**해결**: 2단계 프로세스 (데이터 삽입 → 표시 형식 복사)

**핵심 규칙**:
```python
# ✅ 올바른 방법 (수동)
# 1단계: 데이터 삽입
target_sheet.update(range_name, row_data, value_input_option='RAW')

# 2단계: 표시 형식 복사 (원본 시트 10행 기준)
from sheet_utils import copy_number_formats
copy_number_formats(source_sheet, target_sheet, start_row, num_rows)

# ✅✅ 올바른 방법 (자동) - 권장
from sheet_utils import insert_data_with_format_preservation
insert_data_with_format_preservation(
    source_sheet, target_sheet, row_data, start_row, sheet_type
)
```

**핵심 함수** (sheet_utils.py):
- `copy_number_formats()`: 표시 형식만 복사
- `insert_data_with_format_preservation()`: 데이터 삽입 + 표시 형식 자동 복사
- `update_data_with_format_preservation()`: 셀 업데이트 + 표시 형식 자동 복사
- `copy_sheet_structure()`: 시트 구조 완전 복제 (1~9행)

**적용 시점**:
- 새 시트에 데이터 삽입 시
- 필터링된 데이터를 다른 시트로 복사 시
- 채널 리스트 ↔ 영상 시트 동기화 시
- 엑셀 익스포트 시 (날짜가 숫자로 보이는 문제 방지)

### 6. 헤더 매칭 원칙 (중앙 관리)
**문제**: 헤더에 넘버링이 있어서 정확한 매칭이 어려움
**해결**: sheet_config.py의 공용 함수 사용

```python
from sheet_config import (
    normalize_header,
    detect_sheet_type,
    create_header_mapping,
    get_column_index,
    is_formula_column
)

# 1. 시트 타입 자동 감지
headers = sheet.row_values(1)
sheet_type = detect_sheet_type(headers)

# 2. 헤더 매핑 생성 (넘버링 자동 제거)
header_mapping = create_header_mapping(headers, sheet_type)

# 3. 컬럼 인덱스 찾기 (띄어쓰기 무관)
video_id_col = get_column_index("영상 ID", header_mapping)  # "1. 영상 ID"도 매칭
channel_name_col = get_column_index("채널명", header_mapping)  # "33. 채널 명"도 매칭

# 4. 전역함수 열 확인 (데이터 삽입 제외)
for idx, header in enumerate(headers):
    if is_formula_column(header, sheet_type):
        row_data.append("")  # 전역함수 열은 비움
    else:
        row_data.append(video_data.get(normalize_header(header), ""))
```

**핵심 원칙**:
- **넘버링 자동 제거**: "1. 영상 ID" → "영상ID"
- **띄어쓰기 정규화**: "영상 ID"와 "영상ID" 동일 처리
- **전역함수 열 자동 보존**: is_formula_column() 사용
- **중앙 관리**: sheet_config.py에서 모든 헤더 정의
- **하드코딩 금지**: 직접 헤더 인덱스 사용 금지, 매핑 사용 필수

## 주의사항

### 절대 금지 사항
- Main_Search.py에 구글 시트 관련 기능 추가
- GUI_Interface.py에 YouTube API 직접 호출 추가
- 두 파일 간 기능 중복 구현
- **FORMATTED_VALUE로 데이터 읽기 (조건부 추출/필터링 시)**
- **시트 구조 복제 시 값만 복사하기**
- **캐시 없이 반복적인 API 호출**
- **9행 전역함수 열에 10행 이후 데이터 삽입**
- ❌ **헤더 하드코딩**: `row[0]`, `row[5]` 같은 직접 인덱스 사용 금지
- ❌ **표시 형식 없이 데이터 삽입**: RAW로만 삽입하고 형식 복사 생략
- ❌ **sheet_config 무시**: 직접 헤더 리스트 정의 금지

### 권장 사항
- 새로운 기능 개발 시 위 역할 분담 준수
- 상호 의존성 최소화
- 딕셔너리 키 이름 통일 (헤더 이름과 일치)
- **데이터 읽기 전 UNFORMATTED_VALUE 사용 여부 확인**
- **API 호출 빈도가 높은 기능은 캐싱 적용**
- **시트 구조 변경 시 copyPaste 우선 고려**
- **대규모 데이터(1만+) 처리 시 배치 업데이트 사용 (100개씩)**
- ✅ **헤더 매핑 사용**: sheet_config의 함수로 헤더→인덱스 매핑
- ✅ **표시 형식 자동 복사**: sheet_utils의 함수 활용
- ✅ **전역함수 보존**: is_formula_column()으로 확인 후 빈 문자열 삽입
- ✅ **시트 타입 감지**: detect_sheet_type()으로 자동 판별

## 버전 관리
- 파일 수정 시 이 지침 문서도 함께 업데이트
- 역할 변경이 필요한 경우 사전 검토 필수
- API 호출 최적화 관련 변경 사항은 반드시 문서화

## 주요 개선 이력

### 2025-11-12: 공용 헤더 관리 시스템 및 표시 형식 복사 도입 ✨
**신규 파일**:
- **sheet_config.py**: 모든 시트 헤더 및 구조 중앙 관리
- **sheet_utils.py**: 표시 형식 복사 및 데이터 처리 유틸리티

**주요 개선 사항**:
1. **공용 헤더 관리 시스템**
   - 넘버링 자동 제거: "1. 영상 ID" → "영상ID"
   - 띄어쓰기 허용 매칭: "영상 ID"와 "영상ID" 동일 처리
   - 시트 타입 자동 감지: 헤더로 시트 종류 자동 판별
   - 전역함수 열 자동 보존: 데이터 삽입 시 자동 제외
   - 중앙 관리로 유지보수성 향상

2. **표시 형식 자동 복사**
   - 2단계 프로세스 확립: 데이터 삽입 → 표시 형식 복사
   - 원본 시트 10행 기준 형식 자동 복사
   - 날짜, 퍼센트 등 표시 형식 보존
   - 엑셀 익스포트 시 형식 유지

3. **가이드라인 강화**
   - 헤더 하드코딩 금지 명시
   - 표시 형식 복사 필수 규칙 추가
   - sheet_config 사용 권장사항 추가
   - 코드 예시 추가

**이전 문제점**:
- ❌ 넘버링 때문에 헤더 매칭 실패
- ❌ 하드코딩된 헤더 리스트
- ❌ 전역함수가 덮어씌워짐
- ❌ 띄어쓰기 차이로 매칭 실패
- ❌ 날짜가 숫자로 표시됨

**개선 후**:
- ✅ 넘버링 자동 제거
- ✅ 공용 상수로 중앙 관리
- ✅ 전역함수 자동 보존
- ✅ 띄어쓰기 정규화 매칭
- ✅ 시트 타입 자동 감지
- ✅ 표시 형식 자동 복사
- ✅ 확장 가능한 구조

### 2025-11-11: YouTube 검색 프로젝트로 가이드라인 수정
- 프로젝트 개요를 YouTube 검색 및 데이터 수집으로 변경
- Main_Search.py: YouTube Data API 검색 전담
- GUI_Interface.py: 구글 시트 관리 및 GUI 전담
- 시트 헤더 구조 명시 (영상 리스트 63개 열, 채널 리스트 37개 열)
- 9행 전역함수 열 처리 지침 추가
- 헤더 매칭 원칙 추가 (넘버링 제거, 띄어쓰기 허용)
- 대규모 데이터(1만+) 배치 처리 지침 추가

### 이전 개선 이력 (다른 프로젝트)
- Google Sheets 데이터 처리 지침은 다른 프로젝트(YouTube 대본 추출)에서 검증된 방법론
- UNFORMATTED_VALUE 전략, copyPaste API, 캐싱 전략 등은 본 프로젝트에도 적용 가능

---
**최종 업데이트: 2025-11-12**
**프로젝트: YouTube 검색 및 데이터 수집**