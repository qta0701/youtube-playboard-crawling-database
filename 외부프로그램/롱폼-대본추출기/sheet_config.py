"""
구글 시트 헤더 및 구조 관리 중앙 설정 파일 (대본 추출기 프로젝트)

이 파일은 대본 추출기 프로젝트의 시트 헤더, 전역함수 위치, 시트 타입 등을 정의합니다.
헤더 매칭 시 넘버링을 자동으로 제거하고 띄어쓰기를 정규화하여 비교합니다.

프로젝트: YouTube Shorts 대본 추출기
용도: 영상 리스트 시트의 대본 정보 추출 및 관리
"""

import re
from typing import List, Dict, Optional, Set
from enum import Enum


class SheetType(Enum):
    """시트 타입 정의 (대본 추출기 프로젝트)"""
    VIDEO_LIST = "video_list"  # 영상 리스트 시트 (대본 추출 대상)


# ============================================================================
# 시트 구조 상수
# ============================================================================

# 시트의 행 구조
HEADER_ROW = 1  # 헤더가 위치한 행 (1행)
FORMULA_ROW = 9  # 전역함수가 위치한 행 (9행)
DATA_START_ROW = 10  # 데이터 시작 행 (10행)


# ============================================================================
# 영상 리스트 시트 헤더 정의 (넘버링 제외)
# ============================================================================

VIDEO_LIST_HEADERS = [
    # 기본 영상 정보 (1-8)
    "영상 ID",
    "영상 업로드날짜",
    "수집날짜",
    "검색 키워드",
    "영상 링크",
    "제목",
    "채널명",
    "조회수",

    # 영상 특성 (9-12)
    "숏폼여부",
    "영상길이",
    "분야1",
    "분야2",

    # 참여 지표 (13-18)
    "구독자수",
    "좋아요 수",
    "댓글수",
    "구독자 대비 조회수 배율",
    "조회수 대비 좋아요",
    "조회수 대비 댓글",

    # 계산된 지표 (19-25)
    "영상 업로드 이후 수집날짜까지 기간",
    "일평균 조회수",
    "조회수 100만 이상",
    "조회수 500만 이상",
    "조회수 1,000만 이상",
    "구독자 대비 조회수 몇 배 이상",
    "좋아요 3%이상",

    # 영상 분류 (26-29)
    "음성 나레이션 여부",
    "퍼온 영상인가?",
    "AI생성 영상인가?",
    "레퍼런스 사용할 영상인가?",

    # 자막/수익화 (30-31)
    "자막 다운 여부",
    "채널 수익화 여부",
    "쇼핑 수익화여부",

    # 채널 정보 (32-42)
    "채널명",  # 중복 (32)
    "채널국가",
    "사용언어",
    "채널 ID",
    "채널링크",
    "썸네일 링크",
    "재생목록 이름",
    "영상갯수",
    "채널 전체 조회수",
    "영상당 평균 조회수",
    "채널 개설일",
    "채널개설 이후 수집일까지 경과일",

    # 카테고리/메타데이터 (43-50)
    "카테고리 ID",
    "카테고리 분류",
    "디스크립션",
    "디스크립션 텍스트 수",
    "해시태그 유무",
    "사용 해시태그",
    "그래프",
    "후킹자막",

    # ===== 대본 관련 열 (51-57) =====
    "대본내용",           # 51: 대본 텍스트 (데이터 입력 열)
    "대본유무",           # 52: 'ㅇ' 또는 'x' (전역함수 열 - 자동 계산, 9행 전역함수)
    "대본 텍스트수",      # 53: 글자 수 (전역함수 열 - 자동 계산, 9행 전역함수)
    "대본추출 여부",      # 54: 대본 추출 완료 여부 (전역함수 열 - 자동 계산, 9행 전역함수)
    "구글닥스여부",       # 55: Google Docs 업로드 여부 (데이터 입력 열) - 더 이상 업데이트하지 않음
    "대본파일 ID",        # 56: Google Docs 파일 ID (데이터 입력 열) - 기존 닥스파일ID
    "대본파일 경로",      # 57: Google Docs URL (데이터 입력 열) - 기존 닥스파일경로
    # TXT 관련 헤더는 제거됨 (58-60번 제거)

    # 분석/파일 (58-59)
    "분석",
    "대본파일",

    # ===== 썸네일 관련 열 (60-62) =====
    "썸네일 여부",        # 60: 썸네일 다운로드 여부 (전역함수 열 - 자동 계산, 9행 전역함수)
    "썸네일 이미지주소",  # 61: YouTube 썸네일 URL (데이터 입력 열)
    "썸네일 경로",        # 62: Google Drive 썸네일 경로 (데이터 입력 열)

    # 기타 (63-65)
    "원본 행순서",
    "채널 디스크립션",
    "채널 핸들"
]

# 영상 리스트 시트의 전역함수 열 (넘버링 제외)
# 주의: 이 열들은 데이터 삽입/업데이트 시 건너뛰어야 합니다 (9행의 전역함수가 자동 계산)
VIDEO_LIST_FORMULA_COLUMNS = {
    "숏폼여부",                          # 영상길이 기반 자동 판단
    "영상 업로드 이후 수집날짜까지 기간",  # 날짜 차이 계산
    "일평균 조회수",                     # 조회수 / 경과일
    "조회수 100만 이상",                 # 조회수 >= 1,000,000
    "조회수 500만 이상",                 # 조회수 >= 5,000,000
    "조회수 1,000만 이상",               # 조회수 >= 10,000,000
    "구독자 대비 조회수 몇 배 이상",       # 조회수 / 구독자수
    "좋아요 3%이상",                     # 좋아요수 / 조회수 >= 3%
    "채널개설 이후 수집일까지 경과일",    # 채널 개설일 기반 계산
    "카테고리 분류",                     # 카테고리 ID 기반 매핑
    "사용 해시태그",                     # 디스크립션에서 추출
    "대본유무",                          # 대본 텍스트수 > 0 이면 'ㅇ', 아니면 'x' (9행 전역함수)
    "대본 텍스트수",                     # LEN(대본내용) (9행 전역함수)
    "대본추출 여부",                     # 대본 추출 완료 여부 (9행 전역함수)
    "썸네일 여부"                        # 썸네일 이미지주소/썸네일 경로 기반 자동 판단 (9행 전역함수)
}

# 대본 추출 관련 주요 열 (빠른 접근용)
TRANSCRIPT_RELATED_COLUMNS = {
    "대본내용",           # 데이터 입력 열
    "대본유무",           # 전역함수 열 (자동 계산, 9행 전역함수) - 업데이트 금지
    "대본 텍스트수",      # 전역함수 열 (자동 계산, 9행 전역함수) - 업데이트 금지
    "대본추출 여부",      # 전역함수 열 (자동 계산, 9행 전역함수) - 업데이트 금지
    "구글닥스여부",       # 데이터 입력 열 (더 이상 업데이트하지 않음)
    "대본파일 ID",        # 데이터 입력 열 (기존 닥스파일ID)
    "대본파일 경로",      # 데이터 입력 열 (기존 닥스파일경로)
    # TXT 관련 헤더 제거됨
    "썸네일 여부",        # 전역함수 열 (자동 계산, 9행 전역함수) - 업데이트 금지
    "썸네일 이미지주소",  # 데이터 입력 열
    "썸네일 경로"         # 데이터 입력 열
}


# ============================================================================
# 시트 타입별 설정 매핑
# ============================================================================

SHEET_CONFIG_MAP: Dict[SheetType, Dict] = {
    SheetType.VIDEO_LIST: {
        "headers": VIDEO_LIST_HEADERS,
        "formula_columns": VIDEO_LIST_FORMULA_COLUMNS,
        "data_start_row": DATA_START_ROW,
        "has_formula_row": True
    }
}


# ============================================================================
# 헤더 정규화 및 매칭 함수
# ============================================================================

def normalize_header(header: str) -> str:
    """
    헤더 정규화: 넘버링 제거, 공백 정리

    예시:
        "1. 영상 ID" -> "영상ID"
        "51. 대본 내용" -> "대본내용"
        "영상 ID" -> "영상ID"
        "구글 닥스 여부" -> "구글닥스여부"
        "대본 txt 여부" -> "대본txt여부"

    Args:
        header: 원본 헤더 문자열

    Returns:
        정규화된 헤더 문자열
    """
    if not header:
        return ""

    # 넘버링 제거 (예: "1. ", "51. ")
    header = re.sub(r'^\d+\.\s*', '', str(header))

    # 모든 공백 제거 (띄어쓰기 차이 무시)
    header = re.sub(r'\s+', '', header)

    # trim
    header = header.strip()

    return header


def match_header(header: str, target_headers: List[str]) -> Optional[str]:
    """
    헤더 매칭: 넘버링과 띄어쓰기를 무시하고 매칭

    Args:
        header: 매칭할 헤더 문자열
        target_headers: 매칭 대상 헤더 리스트

    Returns:
        매칭된 헤더 또는 None
    """
    normalized = normalize_header(header)

    for target in target_headers:
        if normalize_header(target) == normalized:
            return target

    return None


def detect_sheet_type(headers: List[str]) -> Optional[SheetType]:
    """
    시트 타입 자동 감지

    헤더를 보고 어떤 시트인지 자동으로 판단합니다.

    Args:
        headers: 시트의 1행 헤더 리스트

    Returns:
        감지된 SheetType 또는 None
    """
    if not headers:
        return None

    normalized_headers = [normalize_header(h) for h in headers]

    # 영상 리스트 특징: "영상ID", "영상업로드날짜", "대본내용" 등
    video_markers = {"영상ID", "영상업로드날짜", "대본내용"}
    if all(marker in normalized_headers for marker in video_markers):
        return SheetType.VIDEO_LIST

    return None


def get_formula_columns(sheet_type: SheetType) -> Set[str]:
    """
    시트 타입의 전역함수 열 가져오기

    Args:
        sheet_type: 시트 타입

    Returns:
        전역함수 열 이름 세트
    """
    config = SHEET_CONFIG_MAP.get(sheet_type)
    if not config:
        return set()

    return config["formula_columns"]


def is_formula_column(header: str, sheet_type: SheetType) -> bool:
    """
    특정 헤더가 전역함수 열인지 확인

    Args:
        header: 확인할 헤더
        sheet_type: 시트 타입

    Returns:
        전역함수 열이면 True
    """
    formula_columns = get_formula_columns(sheet_type)
    normalized = normalize_header(header)

    for formula_col in formula_columns:
        if normalize_header(formula_col) == normalized:
            return True

    return False


def get_data_start_row(sheet_type: SheetType) -> int:
    """
    시트 타입의 데이터 시작 행 가져오기

    Args:
        sheet_type: 시트 타입

    Returns:
        데이터 시작 행 번호
    """
    config = SHEET_CONFIG_MAP.get(sheet_type)
    if not config:
        return DATA_START_ROW  # 기본값

    return config["data_start_row"]


def has_formula_row(sheet_type: SheetType) -> bool:
    """
    시트에 전역함수 행(9행)이 있는지 확인

    Args:
        sheet_type: 시트 타입

    Returns:
        전역함수 행이 있으면 True
    """
    config = SHEET_CONFIG_MAP.get(sheet_type)
    if not config:
        return True  # 기본값

    return config["has_formula_row"]


# ============================================================================
# 헤더 매핑 생성
# ============================================================================

def create_header_mapping(
    actual_headers: List[str],
    sheet_type: SheetType
) -> Dict[str, int]:
    """
    실제 시트의 헤더와 표준 헤더를 매핑하여 인덱스 딕셔너리 생성

    Args:
        actual_headers: 실제 시트의 1행 헤더 리스트
        sheet_type: 시트 타입

    Returns:
        {정규화된 헤더명: 컬럼 인덱스(0-based)} 딕셔너리
    """
    config = SHEET_CONFIG_MAP.get(sheet_type)
    if not config:
        return {}

    standard_headers = config["headers"]
    mapping = {}

    for idx, actual_header in enumerate(actual_headers):
        matched = match_header(actual_header, standard_headers)
        if matched:
            # 표준 헤더 이름(정규화된)을 키로 사용
            normalized_key = normalize_header(matched)
            mapping[normalized_key] = idx

    return mapping


def get_column_index(
    header_name: str,
    header_mapping: Dict[str, int]
) -> Optional[int]:
    """
    헤더 이름으로 컬럼 인덱스 가져오기

    Args:
        header_name: 찾을 헤더 이름 (넘버링/띄어쓰기 무관)
        header_mapping: create_header_mapping()으로 생성한 매핑

    Returns:
        컬럼 인덱스(0-based) 또는 None
    """
    normalized = normalize_header(header_name)
    return header_mapping.get(normalized)


def get_column_index_by_name(
    headers: List[str],
    header_name: str,
    sheet_type: SheetType = SheetType.VIDEO_LIST
) -> Optional[int]:
    """
    헤더 리스트에서 특정 헤더명의 인덱스 찾기 (1-based)

    기존 find_column_by_keyword() 함수를 대체하는 중앙화된 함수

    Args:
        headers: 시트의 1행 헤더 리스트
        header_name: 찾을 헤더 이름 (예: "대본내용", "대본유무")
        sheet_type: 시트 타입 (기본값: VIDEO_LIST)

    Returns:
        컬럼 인덱스(1-based) 또는 None
    """
    header_mapping = create_header_mapping(headers, sheet_type)
    col_idx_0based = get_column_index(header_name, header_mapping)

    if col_idx_0based is not None:
        return col_idx_0based + 1  # 1-based로 변환

    return None


# ============================================================================
# 시트명으로 시트 타입 추정
# ============================================================================

def guess_sheet_type_from_name(sheet_name: str) -> Optional[SheetType]:
    """
    시트 이름으로 시트 타입 추정

    Args:
        sheet_name: 시트 탭 이름

    Returns:
        추정된 SheetType 또는 None
    """
    # 대본 추출기 프로젝트는 VIDEO_LIST만 사용
    return SheetType.VIDEO_LIST


# ============================================================================
# 대본 관련 열 검증 함수
# ============================================================================

def is_transcript_related_column(header: str) -> bool:
    """
    대본 추출 관련 열인지 확인

    Args:
        header: 확인할 헤더

    Returns:
        대본 관련 열이면 True
    """
    normalized = normalize_header(header)

    for transcript_col in TRANSCRIPT_RELATED_COLUMNS:
        if normalize_header(transcript_col) == normalized:
            return True

    return False


def validate_transcript_headers(headers: List[str]) -> Dict[str, bool]:
    """
    대본 추출에 필요한 모든 헤더가 있는지 검증

    Args:
        headers: 시트의 1행 헤더 리스트

    Returns:
        {헤더명: 존재여부} 딕셔너리
    """
    header_mapping = create_header_mapping(headers, SheetType.VIDEO_LIST)

    required_headers = [
        "영상 ID",
        "영상길이",
        "대본내용",
        "대본유무",
        "대본 텍스트수"
    ]

    validation = {}
    for required_header in required_headers:
        normalized = normalize_header(required_header)
        validation[required_header] = normalized in header_mapping

    return validation


# ============================================================================
# 사용 예시 (주석)
# ============================================================================

"""
[사용 예시 1] 시트 타입 자동 감지 및 헤더 매핑 생성

# 1. 시트에서 1행 헤더 읽기
headers = sheet.row_values(1)

# 2. 시트 타입 자동 감지
sheet_type = detect_sheet_type(headers)

# 3. 헤더 매핑 생성
header_mapping = create_header_mapping(headers, sheet_type)

# 4. 특정 컬럼 인덱스 찾기 (0-based)
video_id_col = get_column_index("영상 ID", header_mapping)
transcript_col = get_column_index("대본내용", header_mapping)

# 5. 1-based 인덱스로 직접 찾기
transcript_col_1based = get_column_index_by_name(headers, "대본내용")


[사용 예시 2] 전역함수 열 확인하여 데이터 삽입 제외

# 데이터 삽입 시 전역함수 열은 빈 문자열로 처리
row_data = []
for idx, header in enumerate(headers):
    if is_formula_column(header, sheet_type):
        row_data.append("")  # 전역함수 열은 비움 (9행 전역함수가 자동 계산)
    else:
        row_data.append(video_data.get(normalize_header(header), ""))


[사용 예시 3] 대본 관련 열 검증

# 대본 추출에 필요한 헤더가 모두 있는지 확인
validation = validate_transcript_headers(headers)
if not all(validation.values()):
    missing = [k for k, v in validation.items() if not v]
    raise ValueError(f"필수 헤더 누락: {missing}")


[사용 예시 4] 기존 find_column_by_keyword() 대체

# 기존 코드:
# transcript_col = find_column_by_keyword(sheet, '대본내용')

# 새로운 코드:
headers = sheet.row_values(1)
transcript_col = get_column_index_by_name(headers, '대본내용')


[사용 예시 5] 전역함수 열 필터링

# 대본유무, 대본 텍스트수는 전역함수 열이므로 직접 업데이트하지 않음
if not is_formula_column("대본유무", SheetType.VIDEO_LIST):
    # 업데이트 가능
    sheet.update(cell_address, [['ㅇ']])
else:
    # 전역함수 열이므로 업데이트하지 않음
    logger.warning("'대본유무'는 전역함수 열입니다. 자동으로 계산됩니다.")
"""
