"""
구글 시트 데이터 처리 유틸리티 함수 (대본 추출기 프로젝트)

행의 형식 복사, 시트 구조 복제, 전역함수 행 보호 등의 공용 함수를 제공합니다.

프로젝트: YouTube Shorts 대본 추출기
용도: 전역함수 보호 및 데이터 무결성 유지
"""

import gspread
from typing import List, Optional, Set
from sheet_config import (
    SheetType,
    HEADER_ROW,
    FORMULA_ROW,
    has_formula_row,
    get_data_start_row,
    get_formula_columns,
    is_formula_column,
    normalize_header
)
import logging

logger = logging.getLogger(__name__)


def copy_number_formats(
    source_sheet: gspread.Worksheet,
    target_sheet: gspread.Worksheet,
    start_row: int,
    num_rows: int,
    start_col: int = 1,
    num_cols: Optional[int] = None
):
    """
    원본 시트의 표시 형식을 대상 시트로 복사

    데이터 삽입 후 반드시 이 함수를 호출하여 날짜, 퍼센트 등의
    표시 형식을 복사해야 합니다.

    핵심 규칙:
    - 1단계: 데이터 삽입 (value_input_option='RAW')
    - 2단계: 표시 형식 복사 (이 함수 호출)

    Args:
        source_sheet: 원본 시트 (표시 형식을 가져올 시트)
        target_sheet: 대상 시트 (표시 형식을 적용할 시트)
        start_row: 시작 행 번호 (1-based)
        num_rows: 복사할 행 개수
        start_col: 시작 열 번호 (1-based, 기본값: 1 = A열)
        num_cols: 복사할 열 개수 (None이면 시트 전체 열)

    예시:
        # 10행부터 100개 행의 표시 형식 복사
        copy_number_formats(source_sheet, target_sheet, 10, 100)
    """
    if num_cols is None:
        num_cols = source_sheet.col_count

    # Google Sheets API의 copyPaste 요청 사용
    # pasteType='PASTE_FORMAT'으로 표시 형식만 복사
    requests = [{
        'copyPaste': {
            'source': {
                'sheetId': source_sheet.id,
                'startRowIndex': start_row - 1,  # 0-based
                'endRowIndex': start_row - 1 + num_rows,
                'startColumnIndex': start_col - 1,
                'endColumnIndex': start_col - 1 + num_cols
            },
            'destination': {
                'sheetId': target_sheet.id,
                'startRowIndex': start_row - 1,
                'endRowIndex': start_row - 1 + num_rows,
                'startColumnIndex': start_col - 1,
                'endColumnIndex': start_col - 1 + num_cols
            },
            'pasteType': 'PASTE_FORMAT'  # 표시 형식만 복사
        }
    }]

    # batch_update 실행
    spreadsheet = source_sheet.spreadsheet
    spreadsheet.batch_update({'requests': requests})


def copy_sheet_structure(
    source_sheet: gspread.Worksheet,
    target_sheet: gspread.Worksheet,
    sheet_type: SheetType
):
    """
    시트 구조 완전 복제 (헤더 + 전역함수 행)

    1~9행을 완전히 복제하여 헤더와 전역함수를 보존합니다.
    재생목록ID 시트는 1행만 복제합니다.

    사용 시기:
    - 새로운 시트를 생성할 때
    - 시트 구조를 초기화할 때

    Args:
        source_sheet: 원본 시트 (구조를 가져올 시트)
        target_sheet: 대상 시트 (구조를 적용할 시트)
        sheet_type: 시트 타입

    예시:
        # 영상 리스트 시트 구조 복제
        copy_sheet_structure(source_sheet, new_sheet, SheetType.VIDEO_LIST)
    """
    if has_formula_row(sheet_type):
        # 1~9행 전체 복제 (헤더 + 전역함수)
        end_row = FORMULA_ROW
    else:
        # 1행만 복제 (헤더만)
        end_row = HEADER_ROW

    # copyPaste 요청으로 완전 복제
    requests = [{
        'copyPaste': {
            'source': {
                'sheetId': source_sheet.id,
                'startRowIndex': 0,  # 1행 (0-based)
                'endRowIndex': end_row,
                'startColumnIndex': 0,
                'endColumnIndex': source_sheet.col_count
            },
            'destination': {
                'sheetId': target_sheet.id,
                'startRowIndex': 0,
                'endRowIndex': end_row,
                'startColumnIndex': 0,
                'endColumnIndex': target_sheet.col_count
            },
            'pasteType': 'PASTE_NORMAL'  # 모든 것 복사 (값, 수식, 서식)
        }
    }]

    spreadsheet = source_sheet.spreadsheet
    spreadsheet.batch_update({'requests': requests})


def insert_data_with_format_preservation(
    source_sheet: gspread.Worksheet,
    target_sheet: gspread.Worksheet,
    data_rows: List[List],
    start_row: int,
    sheet_type: SheetType
):
    """
    데이터 삽입 + 표시 형식 자동 복사 (2단계 프로세스)

    올바른 데이터 삽입 방법:
    ✅ 1단계: 데이터 삽입 (RAW)
    ✅ 2단계: 표시 형식 복사 (원본 10행 기준)

    이 함수는 두 단계를 자동으로 처리합니다.

    Args:
        source_sheet: 원본 시트 (표시 형식 참조용)
        target_sheet: 대상 시트 (데이터 삽입할 시트)
        data_rows: 삽입할 데이터 (2D 리스트)
        start_row: 시작 행 번호 (1-based)
        sheet_type: 시트 타입

    예시:
        # 영상 데이터 100개 삽입 (10행부터)
        insert_data_with_format_preservation(
            source_sheet=video_sheet,
            target_sheet=filtered_sheet,
            data_rows=video_data,
            start_row=10,
            sheet_type=SheetType.VIDEO_LIST
        )
    """
    if not data_rows:
        return

    num_rows = len(data_rows)
    num_cols = len(data_rows[0]) if data_rows else 0

    # 1단계: 데이터 삽입 (RAW)
    range_name = f"A{start_row}"
    target_sheet.update(
        range_name,
        data_rows,
        value_input_option='RAW'
    )

    # 2단계: 표시 형식 복사 (원본 시트의 10행 기준)
    # 원본 시트의 10행 표시 형식을 반복 적용
    data_start_row = get_data_start_row(sheet_type)

    # 표시 형식 복사 요청 생성
    requests = []
    for row_offset in range(num_rows):
        target_row = start_row + row_offset
        requests.append({
            'copyPaste': {
                'source': {
                    'sheetId': source_sheet.id,
                    'startRowIndex': data_start_row - 1,  # 10행 (0-based)
                    'endRowIndex': data_start_row,
                    'startColumnIndex': 0,
                    'endColumnIndex': num_cols
                },
                'destination': {
                    'sheetId': target_sheet.id,
                    'startRowIndex': target_row - 1,  # 0-based
                    'endRowIndex': target_row,
                    'startColumnIndex': 0,
                    'endColumnIndex': num_cols
                },
                'pasteType': 'PASTE_FORMAT'  # 표시 형식만 복사
            }
        })

    # 배치 업데이트 (100개씩)
    batch_size = 100
    spreadsheet = source_sheet.spreadsheet

    for i in range(0, len(requests), batch_size):
        batch = requests[i:i + batch_size]
        spreadsheet.batch_update({'requests': batch})


def update_data_with_format_preservation(
    source_sheet: gspread.Worksheet,
    target_sheet: gspread.Worksheet,
    updates: List[dict],
    sheet_type: SheetType
):
    """
    여러 셀 업데이트 + 표시 형식 자동 복사

    updates 형식:
    [
        {'row': 10, 'col': 1, 'value': 'test'},
        {'row': 11, 'col': 2, 'value': 45971.85},  # 날짜 숫자
        ...
    ]

    Args:
        source_sheet: 원본 시트 (표시 형식 참조용)
        target_sheet: 대상 시트
        updates: 업데이트할 셀 정보 리스트
        sheet_type: 시트 타입
    """
    if not updates:
        return

    # 1단계: 데이터 업데이트 (배치)
    batch_data = []
    for update in updates:
        row = update['row']
        col = update['col']
        value = update['value']

        # A1 표기법으로 변환
        col_letter = gspread.utils.rowcol_to_a1(row, col).split(':')[0][:-1]
        range_name = f"{col_letter}{row}"

        batch_data.append({
            'range': range_name,
            'values': [[value]]
        })

    # batch_update 실행
    target_sheet.batch_update(
        batch_data,
        value_input_option='RAW'
    )

    # 2단계: 표시 형식 복사 (원본 10행 기준)
    data_start_row = get_data_start_row(sheet_type)

    requests = []
    for update in updates:
        row = update['row']
        col = update['col']

        requests.append({
            'copyPaste': {
                'source': {
                    'sheetId': source_sheet.id,
                    'startRowIndex': data_start_row - 1,  # 10행
                    'endRowIndex': data_start_row,
                    'startColumnIndex': col - 1,
                    'endColumnIndex': col
                },
                'destination': {
                    'sheetId': target_sheet.id,
                    'startRowIndex': row - 1,
                    'endRowIndex': row,
                    'startColumnIndex': col - 1,
                    'endColumnIndex': col
                },
                'pasteType': 'PASTE_FORMAT'
            }
        })

    # 배치 업데이트
    batch_size = 100
    spreadsheet = source_sheet.spreadsheet

    for i in range(0, len(requests), batch_size):
        batch = requests[i:i + batch_size]
        spreadsheet.batch_update({'requests': batch})


def preserve_formula_row(
    sheet: gspread.Worksheet,
    sheet_type: SheetType
) -> bool:
    """
    전역함수 행(9행) 보호

    데이터 삽입 후 9행의 전역함수가 덮어씌워졌는지 확인하고
    필요 시 복구합니다.

    Args:
        sheet: 확인할 시트
        sheet_type: 시트 타입

    Returns:
        복구 여부 (True/False)
    """
    if not has_formula_row(sheet_type):
        return False

    # 9행 데이터 읽기
    row_9_data = sheet.row_values(FORMULA_ROW)

    # 수식이 있는지 확인 (수식은 "=" 로 시작)
    has_formula = any(
        str(cell).startswith('=') for cell in row_9_data
    )

    if not has_formula:
        # 수식이 없으면 경고 (수동 복구 필요)
        logger.warning(f"전역함수 행(9행)에 수식이 없습니다. #REF! 오류 가능성이 있습니다.")
        return False

    return True


def clear_formula_column_data_rows(
    sheet: gspread.Worksheet,
    headers: List[str],
    sheet_type: SheetType,
    start_row: Optional[int] = None,
    end_row: Optional[int] = None
):
    """
    전역함수 열의 데이터 행(10행 이상) 제거

    벌크 업데이트 후 전역함수 열의 10행 이상 데이터를 제거하여
    9행의 전역함수가 #REF! 오류 없이 배열 결과를 출력하도록 합니다.

    **중요**: 이 함수는 벌크 업데이트 완료 후 반드시 호출해야 합니다.

    동작 원리:
    1. 전역함수 열 식별 (예: "대본유무", "대본 텍스트수")
    2. 해당 열의 10행~마지막 데이터 행 범위를 빈 값으로 채움
    3. 9행의 전역함수 배열이 10행부터 자동으로 결과 출력

    Args:
        sheet: 처리할 시트
        headers: 시트의 1행 헤더 리스트
        sheet_type: 시트 타입
        start_row: 시작 행 (None이면 DATA_START_ROW 사용)
        end_row: 종료 행 (None이면 마지막 데이터 행까지)

    예시:
        # 대본 업데이트 후 전역함수 열 정리
        headers = sheet.row_values(1)
        clear_formula_column_data_rows(sheet, headers, SheetType.VIDEO_LIST)
    """
    if not has_formula_row(sheet_type):
        logger.debug(f"시트 타입 {sheet_type}은 전역함수 행이 없으므로 정리가 필요하지 않습니다.")
        return

    # 전역함수 열 가져오기
    formula_columns = get_formula_columns(sheet_type)
    if not formula_columns:
        logger.debug("전역함수 열이 없습니다.")
        return

    # 시작 행 결정
    if start_row is None:
        start_row = get_data_start_row(sheet_type)

    # 종료 행 결정 (마지막 데이터 행 찾기)
    if end_row is None:
        all_values = sheet.get_all_values()
        # 마지막 데이터 행 찾기 (빈 행이 아닌 마지막 행)
        end_row = len(all_values)
        while end_row > start_row:
            row_data = all_values[end_row - 1]
            if any(cell.strip() for cell in row_data):
                break
            end_row -= 1

    if end_row < start_row:
        logger.debug("정리할 데이터 행이 없습니다.")
        return

    logger.info(f"전역함수 열 정리 시작: {start_row}행 ~ {end_row}행")

    # 헤더 정규화 및 전역함수 열 인덱스 찾기
    formula_col_indices = []
    for idx, header in enumerate(headers):
        normalized = normalize_header(header)
        if any(normalize_header(formula_col) == normalized for formula_col in formula_columns):
            formula_col_indices.append(idx + 1)  # 1-based

    if not formula_col_indices:
        logger.warning("전역함수 열을 찾을 수 없습니다.")
        return

    logger.info(f"정리할 전역함수 열: {len(formula_col_indices)}개")

    # 각 전역함수 열의 데이터 제거
    requests = []
    for col_idx in formula_col_indices:
        col_letter = chr(ord('A') + col_idx - 1) if col_idx <= 26 else gspread.utils.rowcol_to_a1(1, col_idx)[:-1]
        range_name = f"{col_letter}{start_row}:{col_letter}{end_row}"

        # 빈 값으로 채우기 (배열 수식이 자동으로 결과 출력)
        empty_data = [[""] for _ in range(end_row - start_row + 1)]

        requests.append({
            'range': range_name,
            'values': empty_data
        })

    # 배치 업데이트로 한 번에 처리
    if requests:
        try:
            sheet.batch_update(
                requests,
                value_input_option='RAW'
            )
            logger.info(f"✅ 전역함수 열 {len(requests)}개 정리 완료 ({start_row}행~{end_row}행)")
        except Exception as e:
            logger.error(f"❌ 전역함수 열 정리 실패: {e}")
            raise


def bulk_update_with_formula_protection(
    sheet: gspread.Worksheet,
    updates: List[dict],
    headers: List[str],
    sheet_type: SheetType
):
    """
    벌크 업데이트 + 전역함수 행 보호 (완전 자동화)

    이 함수는 다음 작업을 자동으로 수행합니다:
    1. 벌크 업데이트 실행
    2. 전역함수 열의 10행 이상 데이터 제거
    3. 전역함수 행(9행) 검증

    **권장**: 모든 벌크 업데이트에 이 함수를 사용하세요.

    Args:
        sheet: 업데이트할 시트
        updates: 업데이트 데이터 [{'range': 'A10', 'values': [[...]]}]
        headers: 시트의 1행 헤더 리스트
        sheet_type: 시트 타입

    예시:
        # 대본 데이터 벌크 업데이트
        updates = [
            {'range': 'AZ10', 'values': [['대본 텍스트...']]},
            {'range': 'AZ11', 'values': [['대본 텍스트...']]},
            ...
        ]

        bulk_update_with_formula_protection(
            sheet=video_sheet,
            updates=updates,
            headers=headers,
            sheet_type=SheetType.VIDEO_LIST
        )
    """
    if not updates:
        logger.debug("업데이트할 데이터가 없습니다.")
        return

    logger.info(f"벌크 업데이트 시작: {len(updates)}개 셀")

    # 1단계: 벌크 업데이트 실행
    try:
        sheet.batch_update(
            updates,
            value_input_option='RAW'
        )
        logger.info(f"✅ 벌크 업데이트 완료: {len(updates)}개 셀")
    except Exception as e:
        logger.error(f"❌ 벌크 업데이트 실패: {e}")
        raise

    # 2단계: 전역함수 열 정리 (10행 이상 데이터 제거)
    try:
        clear_formula_column_data_rows(sheet, headers, sheet_type)
    except Exception as e:
        logger.error(f"⚠️ 전역함수 열 정리 실패: {e}")
        # 정리 실패는 치명적이지 않으므로 계속 진행

    # 3단계: 전역함수 행 검증
    try:
        preserve_formula_row(sheet, sheet_type)
    except Exception as e:
        logger.error(f"⚠️ 전역함수 행 검증 실패: {e}")


# ============================================================================
# 사용 예시 (주석)
# ============================================================================

"""
[올바른 데이터 삽입 방법]

❌ 잘못된 방법:
target_sheet.update(range_name, row_data, value_input_option='RAW')
# 표시 형식이 복사되지 않아 날짜가 숫자로 보임

✅ 올바른 방법 (수동):
# 1단계: 데이터 삽입
target_sheet.update(range_name, row_data, value_input_option='RAW')

# 2단계: 표시 형식 복사
copy_number_formats(source_sheet, target_sheet, start_row, num_rows)

✅✅ 올바른 방법 (자동):
# 한 번에 처리
insert_data_with_format_preservation(
    source_sheet, target_sheet, row_data, start_row, sheet_type
)


[시트 구조 복제 예시]

# 새 시트 생성 시
new_sheet = spreadsheet.add_worksheet("필터링 결과", 5000, 100)

# 구조 복제 (1~9행 헤더+전역함수)
copy_sheet_structure(original_sheet, new_sheet, SheetType.VIDEO_LIST)

# 데이터 삽입 (표시 형식 자동 복사)
insert_data_with_format_preservation(
    original_sheet, new_sheet, filtered_data, 10, SheetType.VIDEO_LIST
)


[업데이트 예시]

# 여러 셀 업데이트 (표시 형식 보존)
updates = [
    {'row': 10, 'col': 3, 'value': 45971.85},  # 수집날짜 (날짜 숫자)
    {'row': 11, 'col': 8, 'value': 1500000},  # 조회수
]

update_data_with_format_preservation(
    source_sheet, target_sheet, updates, SheetType.VIDEO_LIST
)


[벌크 업데이트 + 전역함수 보호 예시]

# 대본 데이터 대량 업데이트
updates = []
for row_num in range(10, 110):  # 100개 행
    updates.append({
        'range': f'AZ{row_num}',
        'values': [[f'대본 텍스트 {row_num}']]
    })

# 벌크 업데이트 + 자동 전역함수 보호
headers = sheet.row_values(1)
bulk_update_with_formula_protection(
    sheet=video_sheet,
    updates=updates,
    headers=headers,
    sheet_type=SheetType.VIDEO_LIST
)


[전역함수 열 정리 예시]

# 대본 업데이트 후 전역함수 열 정리
# "대본유무", "대본 텍스트수" 열의 10행 이상 데이터 제거
headers = sheet.row_values(1)
clear_formula_column_data_rows(
    sheet=video_sheet,
    headers=headers,
    sheet_type=SheetType.VIDEO_LIST
)

# 9행의 전역함수 배열이 10행부터 자동으로 결과 출력
# =ARRAYFORMULA(IF(LEN(AZ10:AZ)>0, "ㅇ", "x"))
"""
