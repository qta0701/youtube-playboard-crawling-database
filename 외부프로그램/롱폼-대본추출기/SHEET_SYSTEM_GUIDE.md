# 구글 시트 시스템 가이드 (대본 추출기 프로젝트)

## 📋 목차

1. [개요](#개요)
2. [시트 구조](#시트-구조)
3. [헤더 정의 및 관리](#헤더-정의-및-관리)
4. [전역함수 행 보호](#전역함수-행-보호)
5. [공용 함수 사용법](#공용-함수-사용법)
6. [벌크 업데이트 패턴](#벌크-업데이트-패턴)
7. [주의사항 및 Best Practices](#주의사항-및-best-practices)

---

## 개요

### 프로젝트 정보
- **프로젝트명**: YouTube Shorts 대본 추출기
- **목적**: 영상 리스트 시트의 대본 정보 추출 및 관리
- **핵심 파일**:
  - `sheet_config.py`: 시트 헤더 및 구조 중앙 관리
  - `sheet_utils.py`: 시트 데이터 처리 유틸리티 함수

### 시스템 설계 철학

이 시스템은 다음 원칙을 따릅니다:

1. **중앙화된 헤더 관리**: 모든 헤더 정의는 `sheet_config.py`에서 관리
2. **전역함수 보호**: 9행의 전역함수가 #REF! 오류 없이 작동하도록 보장
3. **유연한 헤더 매칭**: 넘버링("1. ", "51. ")과 띄어쓰기 차이 무시
4. **자동화된 보호**: 벌크 업데이트 시 자동으로 전역함수 열 정리

---

## 시트 구조

### 시트 타입

```python
class SheetType(Enum):
    VIDEO_LIST = "video_list"     # 영상 리스트 시트 (대본 추출 대상)
    CHANNEL_LIST = "channel_list" # 채널 리스트 시트 (채널 정보 관리)
```

대본 추출기 프로젝트는 **VIDEO_LIST**와 **CHANNEL_LIST** 시트를 사용합니다.

### 행 구조

```
행 1: 헤더 (HEADER_ROW)
행 2-8: 서브헤더/메모 (선택)
행 9: 전역함수 (FORMULA_ROW)
행 10~: 데이터 (DATA_START_ROW)
```

#### 중요 상수

```python
HEADER_ROW = 1         # 헤더가 위치한 행
FORMULA_ROW = 9        # 전역함수가 위치한 행
DATA_START_ROW = 10    # 데이터 시작 행
```

### 전역함수 행(9행)의 역할

9행은 **배열 수식(ARRAYFORMULA)**을 포함하며, 10행부터 마지막 데이터 행까지 자동으로 결과를 출력합니다.

**예시**:
```
행 9, BA열(대본유무): =ARRAYFORMULA(IF(LEN(AZ10:AZ)>0, "ㅇ", "x"))
행 9, BB열(대본 텍스트수): =ARRAYFORMULA(LEN(AZ10:AZ))
```

이 수식은 10행부터 마지막 행까지 자동으로 계산 결과를 출력합니다.

---

## 헤더 정의 및 관리

### 헤더 매칭 규칙

모든 시트에서 헤더를 찾을 때 다음 규칙을 따릅니다:

#### 매칭 우선순위

1. **완전 일치 우선** (최우선)
   - 넘버링 제거 후 텍스트가 완전히 일치하는 첫 번째 열 선택
   - 예: `1. 채널 ID` → `채널 ID`, `18. 최근 30개 영상 평균 조회수` → `최근 30개 영상 평균 조회수`

2. **부분 일치** (완전 일치가 없을 때만)
   - 대상 텍스트가 헤더에 포함되거나, 헤더가 대상 텍스트에 포함되는 경우
   - 예: `영상 업로드날짜` ⊂ `영상 업로드 이후 수집날짜까지 기간` (X - 의도와 다름)
   - 예: `영상 업로드날짜` == `영상 업로드날짜` (O - 완전 일치)

#### 중복 매칭 방지

- **최초 매칭 원칙**: 동일한 이름의 헤더가 여러 개 있을 경우, 가장 앞쪽(왼쪽) 열 선택
- **한 번만 매칭**: 이미 매칭된 열은 다시 매칭되지 않음

#### 구현 함수

```python
# GoogleSheetsManager 클래스 메서드
def find_header_column(self, headers, target_header, allow_partial=True):
    """
    헤더 리스트에서 대상 헤더를 찾아 열 번호 반환 (1-based)

    매칭 우선순위:
    1. 넘버링 제거 후 완전 일치 (최우선)
    2. 부분 일치 (allow_partial=True인 경우만)

    Args:
        headers: 헤더 리스트 (1행 데이터)
        target_header: 찾을 헤더 이름 (넘버링 제외)
        allow_partial: 부분 일치 허용 여부 (기본값: True)

    Returns:
        int: 열 번호 (1-based), 찾지 못하면 None
    """
```

#### 사용 예시

```python
# 채널 리스트 시트에서 헤더 찾기
channel_headers = ['1. 채널 ID', '2. 가져왔는지 여부', '18. 최근 30개 영상 평균 조회수']

# 완전 일치만 허용
channel_id_col = self.find_header_column(channel_headers, '채널 ID', allow_partial=False)
# 결과: 1 (첫 번째 열)

recent_30_col = self.find_header_column(channel_headers, '최근 30개 영상 평균 조회수', allow_partial=False)
# 결과: 3 (18번째 헤더이지만 열 번호는 3)
```

---

### 영상 리스트 시트 전역함수 열 정의

```python
VIDEO_LIST_FORMULA_COLUMNS = {
    "숏폼여부",
    "영상 업로드 이후 수집날짜까지 기간",
    "일평균 조회수",
    "조회수 100만 이상",
    "조회수 500만 이상",
    "조회수 1,000만 이상",
    "구독자 대비 조회수 몇 배 이상",
    "좋아요 3%이상",
    "채널개설 이후 수집일까지 경과일",
    "카테고리 분류",
    "사용 해시태그",
    "대본유무",           # ⚠️ 전역함수 열 - 직접 업데이트 금지!
    "대본 텍스트수"       # ⚠️ 전역함수 열 - 직접 업데이트 금지!
}
```

### 채널 리스트 시트 전역함수 열 정의

```python
CHANNEL_LIST_FORMULA_COLUMNS = {
    "구독자 대비 조회수배율",  # ⚠️ 전역함수 열 - 자동 계산 (채널 전체 조회수 / 구독자수)
    "콘텐츠파워",             # ⚠️ 전역함수 열 - 자동 계산 (복합 지표)
    "공정성과지수",           # ⚠️ 전역함수 열 - 자동 계산 (복합 지표)
    "수집날짜 경과일"         # ⚠️ 전역함수 열 - 자동 계산 (수집날짜로부터 경과한 일수)
}
```

**중요**: 이 열들은 **절대로 직접 데이터를 삽입하면 안 됩니다**. 9행의 전역함수가 자동으로 계산합니다.

#### 채널 리스트 시트의 전역함수 계산 방식

- **구독자 대비 조회수배율**: `=ARRAYFORMULA(채널전체조회수10:채널전체조회수 / 구독자수10:구독자수)`
- **콘텐츠파워**: 여러 지표를 조합한 복합 계산 (구독자수, 조회수, 영상갯수 등)
- **공정성과지수**: 채널의 성과를 종합 평가하는 지수
- **수집날짜 경과일**: 수집날짜로부터 현재까지 경과한 일수 계산

---

## 전역함수 행 보호

### 문제점: #REF! 오류

벌크 업데이트 시 전역함수 열의 데이터 행(10행 이상)에 직접 값을 쓰면, 9행의 배열 수식이 깨지면서 `#REF!` 오류가 발생합니다.

**발생 원인**:
```
행 9, BA열: =ARRAYFORMULA(IF(LEN(AZ10:AZ)>0, "ㅇ", "x"))

벌크 업데이트로 BA10에 "ㅇ" 직접 삽입
→ 9행의 배열 수식이 10행을 덮어쓸 수 없어 #REF! 오류 발생
```

### 해결 방법

벌크 업데이트 완료 후, 전역함수 열의 10행 이상 데이터를 제거합니다:

```python
from sheet_utils import clear_formula_column_data_rows
from sheet_config import SheetType

# 벌크 업데이트 실행
sheet.batch_update(updates)

# 전역함수 열 정리 (10행 이상 데이터 제거)
headers = sheet.row_values(1)
clear_formula_column_data_rows(sheet, headers, SheetType.VIDEO_LIST)
```

### 자동화된 보호

```python
from sheet_utils import bulk_update_with_formula_protection
from sheet_config import SheetType

updates = [
    {'range': 'AZ10', 'values': [['대본 텍스트 1']]},
    {'range': 'AZ11', 'values': [['대본 텍스트 2']]},
]

headers = sheet.row_values(1)
bulk_update_with_formula_protection(
    sheet=sheet,
    updates=updates,
    headers=headers,
    sheet_type=SheetType.VIDEO_LIST
)
```

---

## 공용 함수 사용법

### 1. 헤더 컬럼 인덱스 찾기

#### 새로운 방식 (권장)

```python
from sheet_config import get_column_index_by_name

# 헤더 한 번만 읽기
headers = sheet.row_values(1)

# 컬럼 인덱스 찾기 (1-based)
transcript_col = get_column_index_by_name(headers, '대본내용')
video_id_col = get_column_index_by_name(headers, '영상 ID')
```

### 2. 전역함수 열 확인

```python
from sheet_config import is_formula_column, SheetType

if is_formula_column("대본유무", SheetType.VIDEO_LIST):
    print("이 열은 전역함수 열입니다. 직접 업데이트하지 마세요!")
```

---

## 벌크 업데이트 패턴

### 패턴 1: 대본 내용 업데이트

```python
from sheet_utils import bulk_update_with_formula_protection
from sheet_config import SheetType, get_column_index_by_name

headers = sheet.row_values(1)
transcript_col = get_column_index_by_name(headers, '대본내용')
col_letter = chr(ord('A') + transcript_col - 1)

updates = []
for row_num, video_data in enumerate(video_data_list, start=10):
    updates.append({
        'range': f'{col_letter}{row_num}',
        'values': [[video_data.transcript_text]]
    })

bulk_update_with_formula_protection(
    sheet=sheet,
    updates=updates,
    headers=headers,
    sheet_type=SheetType.VIDEO_LIST
)
```

---

## 주의사항 및 Best Practices

### ⚠️ 절대 하지 말아야 할 것

1. **전역함수 열에 직접 데이터 삽입 금지**
2. **9행의 전역함수 수식 수정 금지**
3. **벌크 업데이트 후 전역함수 열 정리 생략 금지**

### ✅ 권장사항

1. **항상 `bulk_update_with_formula_protection()` 사용**
2. **헤더 인덱스는 중앙화된 함수 사용**
3. **전역함수 열 확인 후 업데이트**

---

## 채널 리스트 시트 특별 규칙

### 1. 전역함수 열 보호

채널 리스트 시트의 다음 열들은 9행에 전역함수가 있으므로 **절대 직접 업데이트하지 마세요**:

- **구독자 대비 조회수배율**: 채널 전체 조회수를 구독자수로 나눈 값
- **콘텐츠파워**: 여러 채널 지표를 종합한 복합 계산
- **공정성과지수**: 채널의 성과를 종합 평가하는 지수
- **수집날짜 경과일**: 수집날짜로부터 경과한 일수

### 2. 자동 업데이트 규칙

채널 리스트 업데이트 시 다음 열들은 자동으로 계산됩니다:

```python
# 영상 리스트 시트에서 자동 계산되는 채널 지표
- 평균 영상길이: 채널의 모든 영상 길이 평균
- 수집한 영상 평균 조회수: 수집한 영상들의 조회수 평균 (전체)
- 최근 30개 영상 평균 조회수: 업로드날짜 기준 최근 30개 영상의 조회수 평균
  * 업로드날짜 기준으로 내림차순 정렬 후 최근 30개 선택
  * 30개 미만인 경우 전체 영상 평균 계산
  * 영상 리스트 시트의 "영상 업로드날짜"와 "조회수" 열 필요
- 최근 30개 영상 중위 조회수: 최근 30개 영상 조회수의 trimmed mean (상위 20%, 하위 20% 제외 평균)
  * 업로드날짜 기준으로 최근 30개 영상 선택
  * 조회수를 정렬 후 상위 20%와 하위 20% 제거
  * 남은 영상들의 평균 조회수 계산
  * 최소 5개 이상의 영상이 필요

# 채널 리스트에서 영상 시트로 동기화되는 필드
- 분야1: 채널 리스트의 분야1 값이 영상 시트의 모든 해당 채널 영상에 동기화
- 분야2: 채널 리스트의 분야2 값이 영상 시트의 모든 해당 채널 영상에 동기화
- 벤치마킹 채널여부: 채널 리스트의 벤치마킹 채널여부 값이 영상 시트의 모든 해당 채널 영상에 동기화
  * 채널 리스트 업데이트 시 자동 동기화
  * 최근 30개 영상 평균 조회수 업데이트 시 자동 동기화
  * 선택 시트 분야1/분야2 업데이트 시 자동 동기화
```

### 3. 행 추가 시 주의사항

채널 리스트에 새 행을 추가할 때:
1. 9행의 전역함수는 자동으로 복사됩니다
2. 전역함수 열(구독자 대비 조회수배율, 콘텐츠파워, 공정성과지수, 수집날짜 경과일)은 빈 값으로 추가
3. 추가 후 9행의 함수가 자동으로 계산 결과를 출력

### 4. 채널 리스트 업데이트 워크플로우

```python
# 1. 영상 리스트에서 채널 리스트로 데이터 동기화
extractor.update_channel_list_from_video_sheet(
    spreadsheet_url=spreadsheet_url,
    video_sheet_name="영상 리스트"
)

# 자동으로 수행되는 작업:
# - 구독자수, 채널 전체 조회수 등 업데이트
# - 평균 영상길이 계산 및 업데이트
# - 수집한 영상 평균 조회수 계산 및 업데이트
# - 최근 30개 영상 평균 조회수 계산 및 업데이트
# - 최근 30개 영상 중위 조회수 계산 및 업데이트 (상위 20%, 하위 20% 제외)
# - 수집날짜를 yyyy-mm-dd 형식으로 표시
# - 벤치마킹 채널여부 값을 영상 시트의 모든 해당 채널 영상에 동기화
# - 전역함수 열(구독자 대비 조회수배율, 콘텐츠파워, 공정성과지수, 수집날짜 경과일)은 건너뛰고 9행 함수가 자동 계산
```

---

**작성일**: 2025년 1월
**버전**: 1.3
**최종 수정**: 2025년 11월 (최근 30개 영상 중위 조회수 추가, 벤치마킹 채널여부 동기화 기능 추가)
