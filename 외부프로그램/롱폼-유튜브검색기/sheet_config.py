"""
구글 시트 헤더 및 구조 관리 중앙 설정 파일

이 파일은 모든 시트의 헤더, 전역함수 위치, 시트 타입 등을 정의합니다.
헤더 매칭 시 넘버링을 자동으로 제거하고 띄어쓰기를 정규화하여 비교합니다.
"""

import re
from typing import List, Dict, Optional, Set
from enum import Enum


class SheetType(Enum):
    """시트 타입 정의"""
    VIDEO_LIST = "video_list"  # 영상 리스트 시트
    CHANNEL_LIST = "channel_list"  # 채널 리스트 시트
    PLAYLIST_ID = "playlist_id"  # 재생목록ID 시트


# ============================================================================
# 시트 구조 상수
# ============================================================================

# 시트의 행 구조
HEADER_ROW = 1  # 헤더가 위치한 행 (1행)
FORMULA_ROW = 9  # 전역함수가 위치한 행 (9행)
DATA_START_ROW = 10  # 데이터 시작 행 (10행)

# 재생목록ID 시트는 예외 (2행부터 데이터)
PLAYLIST_ID_DATA_START_ROW = 2


# ============================================================================
# 영상 리스트 시트 헤더 정의 (넘버링 제외)
# ============================================================================

VIDEO_LIST_HEADERS = [
    "영상 ID",
    "영상 업로드날짜",
    "수집날짜",
    "검색 키워드",
    "영상 링크",
    "제목",
    "채널명",
    "조회수",
    "벤치마킹 채널여부",
    "숏폼여부",
    "영상길이",
    "분야1",
    "분야2",
    "구독자수",
    "좋아요 수",
    "댓글수",
    "구독자 대비 조회수 배율",
    "조회수 대비 좋아요",
    "조회수 대비 댓글",
    "영상 업로드 이후 수집날짜까지 기간",
    "일평균 조회수",
    "조회수 100만 이상",
    "조회수 500만 이상",
    "조회수 1,000만 이상",
    "구독자 대비 조회수 몇 배 이상",
    "좋아요 3%이상",
    "음성 나레이션 여부",
    "퍼온 영상인가?",
    "AI생성 영상인가?",
    "레퍼런스 사용할 영상인가?",
    "자막 다운 여부",
    "채널 수익화 여부",
    "쇼핑 수익화여부",
    "채널명",
    "채널국가",
    "사용언어",
    "채널 ID",
    "채널링크",
    "재생목록 이름",
    "영상갯수",
    "채널 전체 조회수",
    "영상당 평균 조회수",
    "채널 개설일",
    "채널개설 이후 수집일까지 경과일",
    "카테고리 ID",
    "카테고리 분류",
    "디스크립션",
    "디스크립션 텍스트 수",
    "해시태그 유무",
    "사용 해시태그",
    "그래프",
    "후킹자막",
    "후킹자막 유무",
    "대본내용",
    "대본유무",
    "대본 텍스트수",
    "썸네일 링크",
    "분석",
    "대본파일",
    "썸네일 여부",
    "썸네일 이미지주소",
    "썸네일 경로",
    "원본 행순서",
    "채널 디스크립션",
    "채널 핸들"
]

# 영상 리스트 시트의 전역함수 열 (넘버링 제외)
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
    "후킹자막 유무",
    "대본유무",
    "대본 텍스트수"
}


# ============================================================================
# 채널 리스트 시트 헤더 정의 (넘버링 제외)
# ============================================================================

CHANNEL_LIST_HEADERS = [
    "채널 ID",
    "가져왔는지 여부",
    "수집날짜",
    "수집날짜 경과일",
    "채널링크",
    "채널명",
    "분야1",
    "분야2",
    "채널특징",
    "벤치마킹 채널여부",
    "구독자수",
    "최근 30개 영상 중위 조회수",
    "자막 여부",
    "가져올 채널",
    "채널전체 영상갯수",
    "채널전체 조회수(변환)",
    "채널전체 조회수",
    "영상당 평균 조회수(전투력)",
    "수집한 영상 평균 조회수",
    "최근 30개 영상 평균 조회수",
    "수집영상 갯수",
    "평균 영상길이",
    "조회수 100만이상 비율",
    "조회수 500만이상 비율",
    "조회수 1,000만이상 비율",
    "구독자 대비 조회수배율(최근30개)",
    "공정성과지수(최근30개)",
    "영상당 구독자수",
    "구독자1명 당 조회수",
    "조회수 100만이상 갯수",
    "조회수 500만이상 갯수",
    "조회수 1,000만이상 갯수",
    "재생목록 이름",
    "채널국가",
    "사용언어",
    "조회수 상위 3개 제외 평균 조회수",
    "중위 평균 조회수",
    "개설일",
    "개설 이후 수집날짜까지 기간",
    "영상 1개당 평균 업로드 주기",
    "퍼온 영상인가?",
    "AI생성 영상인가?",
    "채널 디스크립션",
    "채널 핸들",
    "원본 행순서"
]

# 채널 리스트 시트의 전역함수 열 (넘버링 제외)
CHANNEL_LIST_FORMULA_COLUMNS = {
    "수집날짜 경과일",
    "채널전체 조회수(변환)",
    "영상당 평균 조회수(전투력)",
    "조회수 100만이상 비율",
    "조회수 500만이상 비율",
    "조회수 1,000만이상 비율",
    "구독자 대비 조회수배율(최근30개)",
    "공정성과지수(최근30개)",
    "영상당 구독자수",
    "구독자1명 당 조회수",
    "개설 이후 수집날짜까지 기간"
}


# ============================================================================
# 재생목록ID 시트 헤더 정의 (넘버링 제외)
# ============================================================================

PLAYLIST_ID_HEADERS = [
    "재생목록 ID",
    "재생목록 이름",
    "영상갯수",
    "마지막 체크일"
]

# 재생목록ID 시트는 전역함수가 없음
PLAYLIST_ID_FORMULA_COLUMNS = set()


# ============================================================================
# 시트 타입별 설정 매핑
# ============================================================================

SHEET_CONFIG_MAP: Dict[SheetType, Dict] = {
    SheetType.VIDEO_LIST: {
        "headers": VIDEO_LIST_HEADERS,
        "formula_columns": VIDEO_LIST_FORMULA_COLUMNS,
        "data_start_row": DATA_START_ROW,
        "has_formula_row": True
    },
    SheetType.CHANNEL_LIST: {
        "headers": CHANNEL_LIST_HEADERS,
        "formula_columns": CHANNEL_LIST_FORMULA_COLUMNS,
        "data_start_row": DATA_START_ROW,
        "has_formula_row": True
    },
    SheetType.PLAYLIST_ID: {
        "headers": PLAYLIST_ID_HEADERS,
        "formula_columns": PLAYLIST_ID_FORMULA_COLUMNS,
        "data_start_row": PLAYLIST_ID_DATA_START_ROW,
        "has_formula_row": False
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
        "33. 채널 명" -> "채널명"
        "영상 ID" -> "영상ID"

    Args:
        header: 원본 헤더 문자열

    Returns:
        정규화된 헤더 문자열
    """
    if not header:
        return ""

    # 넘버링 제거 (예: "1. ", "33. ")
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

    # 각 시트 타입의 특징적인 헤더로 판단
    # 영상 리스트 특징: "영상ID", "영상업로드날짜" 등
    video_markers = {"영상ID", "영상업로드날짜", "영상링크"}
    if all(marker in normalized_headers for marker in video_markers):
        return SheetType.VIDEO_LIST

    # 채널 리스트 특징: "가져왔는지여부", "영상당평균조회수(전투력)" 등
    channel_markers = {"채널ID", "가져왔는지여부", "영상당평균조회수(전투력)"}
    if all(marker in normalized_headers for marker in channel_markers):
        return SheetType.CHANNEL_LIST

    # 재생목록ID 특징: "재생목록ID", "마지막체크일"
    playlist_markers = {"재생목록ID", "마지막체크일"}
    if all(marker in normalized_headers for marker in playlist_markers):
        return SheetType.PLAYLIST_ID

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

    중복 매칭 방지를 위해:
    1. 완전 일치 우선 (넘버링 제거 전 텍스트 일치)
    2. 정규화 후 일치
    3. 이미 매핑된 표준 헤더는 건너뛰기 (가장 빠른 열 우선)

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
    matched_standards = set()  # 이미 매칭된 표준 헤더 추적

    # 1단계: 완전 일치 우선 (넘버링만 제거, 띄어쓰기 유지)
    for idx, actual_header in enumerate(actual_headers):
        # 넘버링만 제거
        actual_without_num = re.sub(r'^\d+\.\s*', '', str(actual_header)).strip()

        for standard in standard_headers:
            if standard in matched_standards:
                continue

            # 완전 일치 확인
            if actual_without_num == standard:
                normalized_key = normalize_header(standard)
                mapping[normalized_key] = idx
                matched_standards.add(standard)
                break

    # 2단계: 정규화 후 일치 (1단계에서 매칭 안 된 것만)
    for idx, actual_header in enumerate(actual_headers):
        normalized_actual = normalize_header(actual_header)

        for standard in standard_headers:
            if standard in matched_standards:
                continue

            normalized_standard = normalize_header(standard)
            if normalized_actual == normalized_standard:
                # 이미 다른 인덱스로 매핑되어 있으면 건너뛰기 (가장 빠른 열 우선)
                if normalized_standard not in mapping:
                    mapping[normalized_standard] = idx
                    matched_standards.add(standard)
                break

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
    sheet_name_normalized = normalize_header(sheet_name)

    # 채널 리스트
    if "채널리스트" in sheet_name_normalized or "채널목록" in sheet_name_normalized:
        return SheetType.CHANNEL_LIST

    # 재생목록ID
    if "재생목록ID" in sheet_name_normalized or "재생목록아이디" in sheet_name_normalized:
        return SheetType.PLAYLIST_ID

    # 그 외는 영상 리스트로 간주
    return SheetType.VIDEO_LIST


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

# 4. 특정 컬럼 인덱스 찾기
video_id_col = get_column_index("영상 ID", header_mapping)  # "1. 영상 ID"도 매칭됨
channel_name_col = get_column_index("채널명", header_mapping)  # "33. 채널 명"도 매칭됨


[사용 예시 2] 전역함수 열 확인하여 데이터 삽입 제외

# 데이터 삽입 시 전역함수 열은 빈 문자열로 처리
row_data = []
for idx, header in enumerate(headers):
    if is_formula_column(header, sheet_type):
        row_data.append("")  # 전역함수 열은 비움
    else:
        row_data.append(video_data.get(normalize_header(header), ""))


[사용 예시 3] 시트 구조 복제 시 전역함수 보존

if has_formula_row(sheet_type):
    # 1~9행 전체 복제 (전역함수 포함)
    copy_rows(source_sheet, target_sheet, start_row=1, end_row=9)
else:
    # 1행만 복제 (헤더만)
    copy_rows(source_sheet, target_sheet, start_row=1, end_row=1)
"""
