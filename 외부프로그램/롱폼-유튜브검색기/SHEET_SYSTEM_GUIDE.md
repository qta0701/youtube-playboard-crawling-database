# 구글 시트 헤더 관리 시스템 사용 가이드

## 📋 목차
1. [개요](#개요)
2. [파일 구조](#파일-구조)
3. [기본 사용법](#기본-사용법)
4. [고급 사용법](#고급-사용법)
5. [자주 묻는 질문](#자주-묻는-질문)

---

## 개요

본 시스템은 구글 시트의 헤더 및 데이터를 효율적으로 관리하기 위한 중앙 관리 시스템입니다.

### 주요 기능
- ✅ **헤더 중앙 관리**: 모든 시트 헤더를 한 곳에서 정의
- ✅ **넘버링 자동 제거**: "1. 영상 ID" → "영상ID" 자동 변환
- ✅ **띄어쓰기 허용**: "영상 ID"와 "영상ID" 동일 처리
- ✅ **전역함수 자동 보존**: 9행 전역함수 자동 감지 및 보존
- ✅ **표시 형식 자동 복사**: 날짜, 퍼센트 등 형식 자동 유지
- ✅ **시트 타입 자동 감지**: 헤더로 시트 종류 자동 판별

---

## 파일 구조

```
유튜브 검색기/
├── sheet_config.py          # 헤더 정의 및 구조 관리
├── sheet_utils.py           # 표시 형식 복사 유틸리티
├── GUI_Interface.py         # 메인 로직 (위 두 파일 import)
└── SHEET_SYSTEM_GUIDE.md    # 본 가이드
```

### sheet_config.py
**역할**: 모든 시트 구조의 단일 진실 원천 (Single Source of Truth)

**포함 내용**:
- 영상 리스트 헤더 63개
- 채널 리스트 헤더 37개
- 재생목록ID 헤더 4개
- 전역함수 열 위치
- 헤더 정규화 함수
- 시트 타입 감지 함수

### sheet_utils.py
**역할**: 데이터 삽입 시 표시 형식 자동 복사

**포함 내용**:
- 표시 형식 복사 함수
- 데이터 삽입 + 형식 복사 함수
- 시트 구조 완전 복제 함수

---

## 기본 사용법

### 1️⃣ 시트 타입 자동 감지

```python
from sheet_config import detect_sheet_type

# 시트에서 1행 헤더 읽기
headers = sheet.row_values(1)

# 시트 타입 자동 감지
sheet_type = detect_sheet_type(headers)
# 결과: SheetType.VIDEO_LIST, SheetType.CHANNEL_LIST, 또는 SheetType.PLAYLIST_ID

print(f"감지된 시트 타입: {sheet_type}")
```

**자동 감지 기준**:
- **영상 리스트**: "영상ID", "영상업로드날짜", "영상링크" 포함
- **채널 리스트**: "채널ID", "가져왔는지여부", "영상당평균조회수(전투력)" 포함
- **재생목록ID**: "재생목록ID", "마지막체크일" 포함

---

### 2️⃣ 헤더 매핑 생성 (넘버링 자동 제거)

```python
from sheet_config import create_header_mapping, get_column_index

# 1. 헤더 매핑 생성
headers = sheet.row_values(1)
sheet_type = detect_sheet_type(headers)
header_mapping = create_header_mapping(headers, sheet_type)

# 2. 컬럼 인덱스 찾기 (넘버링/띄어쓰기 무관)
video_id_col = get_column_index("영상 ID", header_mapping)
# "1. 영상 ID", "1.영상ID", "영상ID" 모두 매칭됨

channel_name_col = get_column_index("채널명", header_mapping)
# "33. 채널명", "33. 채널 명", "채널명" 모두 매칭됨

# 3. 데이터 읽기
for row in data_rows:
    video_id = row[video_id_col] if video_id_col is not None else ""
    channel_name = row[channel_name_col] if channel_name_col is not None else ""
```

**장점**:
- ❌ 하드코딩: `row[0]`, `row[5]` (넘버링 바뀌면 오류)
- ✅ 매핑 사용: `row[video_id_col]` (헤더 순서 변경 대응)

---

### 3️⃣ 전역함수 열 자동 보존

```python
from sheet_config import is_formula_column

# 데이터 삽입 시 전역함수 열은 빈 문자열로 처리
row_data = []
for idx, header in enumerate(headers):
    if is_formula_column(header, sheet_type):
        row_data.append("")  # 전역함수 열은 비움 (수식 보존)
    else:
        row_data.append(video_data.get(normalize_header(header), ""))
```

**전역함수 열** (자동으로 빈 문자열 처리해야 함):
- 영상 리스트: 숏폼여부, 일평균 조회수, 조회수 100만 이상 등 13개
- 채널 리스트: 영상당 평균 조회수(전투력), 조회수 비율 등 8개

---

### 4️⃣ 데이터 삽입 + 표시 형식 자동 복사

```python
from sheet_utils import insert_data_with_format_preservation

# ✅ 올바른 방법 (자동)
insert_data_with_format_preservation(
    source_sheet=original_sheet,      # 표시 형식을 가져올 원본 시트
    target_sheet=filtered_sheet,      # 데이터를 삽입할 대상 시트
    data_rows=video_data,              # 삽입할 데이터 (2D 리스트)
    start_row=10,                      # 시작 행 (10행부터)
    sheet_type=SheetType.VIDEO_LIST   # 시트 타입
)
```

**자동 처리 내용**:
1. 데이터 삽입 (RAW)
2. 원본 시트 10행의 표시 형식을 각 행에 복사
3. 날짜, 퍼센트, 천 단위 구분자 등 자동 유지

**결과**:
- 날짜: `45971.85` → `2025-08-01` (자동 변환)
- 퍼센트: `0.15` → `15%` (자동 변환)
- 숫자: `1500000` → `1,500,000` (자동 변환)

---

### 5️⃣ 시트 구조 완전 복제

```python
from sheet_utils import copy_sheet_structure

# 새 시트 생성
new_sheet = spreadsheet.add_worksheet("필터링 결과", 5000, 100)

# 1~9행 완전 복제 (헤더 + 전역함수)
copy_sheet_structure(
    source_sheet=original_sheet,
    target_sheet=new_sheet,
    sheet_type=SheetType.VIDEO_LIST
)
```

**복제 내용**:
- 1행: 헤더
- 2~8행: 빈 행 (레이아웃 유지)
- 9행: 전역함수 (수식 포함)
- 표시 형식, 열 너비, 서식 모두 복제

---

## 고급 사용법

### 시나리오 1: 필터링된 데이터 새 시트로 복사

```python
from sheet_config import detect_sheet_type, create_header_mapping, get_column_index, is_formula_column
from sheet_utils import copy_sheet_structure, insert_data_with_format_preservation

# 1. 원본 시트 분석
headers = original_sheet.row_values(1)
sheet_type = detect_sheet_type(headers)
header_mapping = create_header_mapping(headers, sheet_type)

# 2. 새 시트 생성 및 구조 복제
new_sheet = spreadsheet.add_worksheet("조회수 100만 이상", 5000, 100)
copy_sheet_structure(original_sheet, new_sheet, sheet_type)

# 3. 필터링된 데이터 추출
all_data = original_sheet.get_all_values()[9:]  # 10행부터 (0-based이므로 9)
view_count_col = get_column_index("조회수", header_mapping)

filtered_data = []
for row in all_data:
    if view_count_col and int(row[view_count_col] or 0) >= 1000000:
        # 전역함수 열은 빈 문자열로 처리
        filtered_row = []
        for idx, header in enumerate(headers):
            if is_formula_column(header, sheet_type):
                filtered_row.append("")
            else:
                filtered_row.append(row[idx] if idx < len(row) else "")
        filtered_data.append(filtered_row)

# 4. 데이터 삽입 + 표시 형식 자동 복사
insert_data_with_format_preservation(
    original_sheet, new_sheet, filtered_data, 10, sheet_type
)

print(f"✅ {len(filtered_data)}개 행이 '{new_sheet.title}' 시트로 복사되었습니다.")
```

---

### 시나리오 2: 채널 리스트 ↔ 영상 시트 동기화

```python
from sheet_config import detect_sheet_type, create_header_mapping, get_column_index
from sheet_utils import update_data_with_format_preservation

# 1. 채널 리스트 헤더 분석
channel_headers = channel_sheet.row_values(1)
channel_type = detect_sheet_type(channel_headers)
channel_mapping = create_header_mapping(channel_headers, channel_type)

# 2. 영상 시트 헤더 분석
video_headers = video_sheet.row_values(1)
video_type = detect_sheet_type(video_headers)
video_mapping = create_header_mapping(video_headers, video_type)

# 3. 인덱스 찾기
channel_id_col_ch = get_column_index("채널 ID", channel_mapping)
subscriber_col_ch = get_column_index("구독자수", channel_mapping)

channel_id_col_vid = get_column_index("채널 ID", video_mapping)
subscriber_col_vid = get_column_index("구독자수", video_mapping)

# 4. 채널 데이터 읽기
channel_data = {}
for row_idx, row in enumerate(channel_sheet.get_all_values()[9:], start=10):
    channel_id = row[channel_id_col_ch] if channel_id_col_ch else ""
    subscriber = row[subscriber_col_ch] if subscriber_col_ch else ""
    channel_data[channel_id] = subscriber

# 5. 영상 시트 업데이트
updates = []
for row_idx, row in enumerate(video_sheet.get_all_values()[9:], start=10):
    channel_id = row[channel_id_col_vid] if channel_id_col_vid else ""
    if channel_id in channel_data:
        updates.append({
            'row': row_idx,
            'col': subscriber_col_vid + 1,  # 1-based
            'value': channel_data[channel_id]
        })

# 6. 배치 업데이트 + 표시 형식 자동 복사
if updates:
    update_data_with_format_preservation(
        video_sheet, video_sheet, updates, video_type
    )
    print(f"✅ {len(updates)}개 행의 구독자수가 업데이트되었습니다.")
```

---

### 시나리오 3: 시트 이름으로 타입 추정

```python
from sheet_config import guess_sheet_type_from_name

# 시트 이름으로 타입 추정 (헤더 읽기 전)
sheet_name = "조회수 100만 이상 영상"
sheet_type = guess_sheet_type_from_name(sheet_name)
# 결과: SheetType.VIDEO_LIST

sheet_name = "채널 리스트"
sheet_type = guess_sheet_type_from_name(sheet_name)
# 결과: SheetType.CHANNEL_LIST

sheet_name = "재생목록ID"
sheet_type = guess_sheet_type_from_name(sheet_name)
# 결과: SheetType.PLAYLIST_ID
```

---

## 자주 묻는 질문

### Q1. 헤더 순서가 바뀌면 코드를 수정해야 하나요?
**A**: 아니요. `create_header_mapping()`으로 매핑을 생성하면 헤더 순서가 바뀌어도 자동으로 대응합니다.

```python
# ❌ 하드코딩 (헤더 순서 바뀌면 오류)
video_id = row[0]
channel_name = row[32]

# ✅ 매핑 사용 (헤더 순서 무관)
video_id = row[get_column_index("영상 ID", header_mapping)]
channel_name = row[get_column_index("채널명", header_mapping)]
```

---

### Q2. 새로운 헤더를 추가하려면 어떻게 하나요?
**A**: [sheet_config.py](sheet_config.py)의 해당 리스트에 추가하면 됩니다.

```python
# sheet_config.py

VIDEO_LIST_HEADERS = [
    "영상 ID",
    "영상 업로드날짜",
    # ... 기존 헤더들 ...
    "새로운 헤더",  # ← 여기에 추가
]

# 전역함수 열이면 이것도 추가
VIDEO_LIST_FORMULA_COLUMNS = {
    "숏폼여부",
    # ... 기존 항목들 ...
    "새로운 헤더",  # ← 여기에 추가
}
```

---

### Q3. 표시 형식이 복사되지 않는데요?
**A**: `insert_data_with_format_preservation()` 또는 `copy_number_formats()`를 사용했는지 확인하세요.

```python
# ❌ 잘못된 방법 (표시 형식 손실)
target_sheet.update(range_name, row_data, value_input_option='RAW')

# ✅ 올바른 방법 (표시 형식 자동 복사)
insert_data_with_format_preservation(
    source_sheet, target_sheet, row_data, start_row, sheet_type
)
```

---

### Q4. 전역함수가 덮어씌워졌어요!
**A**: `is_formula_column()`으로 확인하고 빈 문자열을 삽입했는지 확인하세요.

```python
# ✅ 올바른 방법
row_data = []
for idx, header in enumerate(headers):
    if is_formula_column(header, sheet_type):
        row_data.append("")  # 전역함수 열은 비움
    else:
        row_data.append(data.get(normalize_header(header), ""))
```

---

### Q5. 넘버링이 있는 헤더를 어떻게 매칭하나요?
**A**: `normalize_header()`가 자동으로 처리합니다.

```python
from sheet_config import normalize_header

# 자동 정규화
print(normalize_header("1. 영상 ID"))     # → "영상ID"
print(normalize_header("33. 채널 명"))    # → "채널명"
print(normalize_header("영상 ID"))        # → "영상ID"

# 모두 동일하게 매칭됨
```

---

### Q6. 시트 타입을 잘못 감지했어요!
**A**: 헤더가 정확한지 확인하거나 수동으로 지정하세요.

```python
# 자동 감지
sheet_type = detect_sheet_type(headers)

# 또는 수동 지정
from sheet_config import SheetType
sheet_type = SheetType.VIDEO_LIST
```

---

### Q7. 배치 업데이트는 어떻게 하나요?
**A**: `update_data_with_format_preservation()`은 자동으로 100개씩 배치 처리합니다.

```python
# 1000개 업데이트도 자동으로 배치 처리
updates = [
    {'row': 10, 'col': 3, 'value': '2025-01-01'},
    {'row': 11, 'col': 3, 'value': '2025-01-02'},
    # ... 1000개
]

update_data_with_format_preservation(
    source_sheet, target_sheet, updates, sheet_type
)
# 내부적으로 100개씩 나눠서 처리
```

---

## 코딩 규칙 요약

### ✅ 해야 할 것
1. **헤더 매핑 사용**: `create_header_mapping()` + `get_column_index()`
2. **표시 형식 복사**: `insert_data_with_format_preservation()`
3. **전역함수 보존**: `is_formula_column()` 확인
4. **시트 타입 감지**: `detect_sheet_type()` 사용
5. **중앙 관리**: `sheet_config.py`에서 헤더 정의

### ❌ 하지 말아야 할 것
1. **헤더 하드코딩**: `row[0]`, `row[5]` 직접 사용
2. **표시 형식 생략**: RAW로만 삽입하고 형식 복사 안 함
3. **전역함수 덮어쓰기**: 9행 전역함수 열에 데이터 삽입
4. **넘버링 수동 제거**: `header.replace("1. ", "")` 직접 사용
5. **중복 정의**: 각 함수마다 헤더 리스트 재정의

---

## 문제 해결

### 문제: "normalize_header" is not defined
**해결**:
```python
from sheet_config import normalize_header
```

### 문제: 날짜가 숫자로 표시됨 (45971.85)
**해결**:
```python
# ❌ 잘못된 방법
target_sheet.update(range_name, row_data)

# ✅ 올바른 방법
from sheet_utils import insert_data_with_format_preservation
insert_data_with_format_preservation(
    source_sheet, target_sheet, row_data, start_row, sheet_type
)
```

### 문제: 전역함수가 사라짐
**해결**:
```python
# 전역함수 열 확인
from sheet_config import is_formula_column

if is_formula_column(header, sheet_type):
    row_data.append("")  # 비워두기
```

---

## 추가 자료

- **프로젝트 가이드라인**: [PROJECT_GUIDELINES.md](PROJECT_GUIDELINES.md)
- **헤더 정의**: [sheet_config.py](sheet_config.py)
- **유틸리티 함수**: [sheet_utils.py](sheet_utils.py)
- **메인 로직**: [GUI_Interface.py](GUI_Interface.py)

---

**마지막 업데이트: 2025-11-12**
