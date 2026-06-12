"""
구글 시트 데이터 처리 유틸리티 함수

표시 형식 복사, 시트 구조 복제 등의 공용 함수를 제공합니다.
"""

import gspread
from typing import List, Optional
from sheet_config import (
    SheetType,
    HEADER_ROW,
    FORMULA_ROW,
    has_formula_row,
    get_data_start_row
)


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
    sheet_type: SheetType,
    header_mapping: dict = None
):
    """
    데이터 삽입 + 표시 형식 자동 복사 + 전역함수 열 데이터 삭제 (3단계 프로세스)

    올바른 데이터 삽입 방법:
    ✅ 1단계: 데이터 삽입 (RAW)
    ✅ 2단계: 표시 형식 복사 (원본 10행 기준)
    ✅ 3단계: 전역함수 열의 10행 이후 데이터 삭제

    이 함수는 세 단계를 자동으로 처리합니다.

    Args:
        source_sheet: 원본 시트 (표시 형식 참조용)
        target_sheet: 대상 시트 (데이터 삽입할 시트)
        data_rows: 삽입할 데이터 (2D 리스트)
        start_row: 시작 행 번호 (1-based)
        sheet_type: 시트 타입
        header_mapping: 헤더 매핑 (전역함수 열 삭제에 사용, None이면 자동 생성)

    예시:
        # 영상 데이터 100개 삽입 (10행부터)
        insert_data_with_format_preservation(
            source_sheet=video_sheet,
            target_sheet=filtered_sheet,
            data_rows=video_data,
            start_row=10,
            sheet_type=SheetType.VIDEO_LIST,
            header_mapping=header_mapping
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

    # 3단계: 전역함수 열의 10행 이후 데이터 삭제
    if header_mapping is None:
        # header_mapping이 없으면 자동 생성
        from sheet_config import create_header_mapping
        headers = target_sheet.row_values(1)
        header_mapping = create_header_mapping(headers, sheet_type)

    clear_formula_column_data(target_sheet, header_mapping, sheet_type)


def update_data_with_format_preservation(
    source_sheet: gspread.Worksheet,
    target_sheet: gspread.Worksheet,
    updates: List[dict],
    sheet_type: SheetType,
    header_mapping: dict = None
):
    """
    여러 셀 업데이트 + 표시 형식 자동 복사 + 전역함수 열 데이터 삭제

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
        header_mapping: 헤더 매핑 (전역함수 열 삭제에 사용, None이면 자동 생성)
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

    # 3단계: 전역함수 열의 10행 이후 데이터 삭제
    if header_mapping is None:
        # header_mapping이 없으면 자동 생성
        from sheet_config import create_header_mapping
        headers = target_sheet.row_values(1)
        header_mapping = create_header_mapping(headers, sheet_type)

    clear_formula_column_data(target_sheet, header_mapping, sheet_type)


def preserve_formula_row(
    sheet: gspread.Worksheet,
    sheet_type: SheetType
):
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
        return False

    return True


def clear_formula_column_data(
    sheet: gspread.Worksheet,
    header_mapping: dict,
    sheet_type: SheetType
):
    """
    전역함수 열의 10행 이후 데이터 삭제

    9행에 전역함수가 있는 열은 10행부터 데이터가 자동 계산되므로,
    데이터 삽입/업데이트 후 해당 열의 10행 이후 값을 삭제합니다.
    (1~9행은 보존)

    Args:
        sheet: 대상 시트
        header_mapping: create_header_mapping()으로 생성한 헤더 매핑
        sheet_type: 시트 타입

    예시:
        # 데이터 삽입 후 호출
        clear_formula_column_data(sheet, header_mapping, SheetType.VIDEO_LIST)
    """
    from sheet_config import get_formula_columns, get_data_start_row, normalize_header

    if not has_formula_row(sheet_type):
        return

    # 전역함수 열 가져오기
    formula_columns = get_formula_columns(sheet_type)
    if not formula_columns:
        return

    # 데이터 시작 행
    data_start_row = get_data_start_row(sheet_type)

    # 시트의 마지막 행 확인
    last_row = sheet.row_count

    # 전역함수 열의 인덱스 찾기
    formula_col_indices = []
    for formula_col_name in formula_columns:
        normalized = normalize_header(formula_col_name)
        col_index = header_mapping.get(normalized)
        if col_index is not None:
            formula_col_indices.append(col_index + 1)  # 1-based

    if not formula_col_indices:
        return

    # 각 전역함수 열의 10행 이후 데이터 삭제
    batch_clear_ranges = []
    for col_index in formula_col_indices:
        # A1 표기법으로 범위 생성
        col_letter = gspread.utils.rowcol_to_a1(1, col_index).replace('1', '')
        range_name = f"{col_letter}{data_start_row}:{col_letter}{last_row}"
        batch_clear_ranges.append(range_name)

    # 배치로 한 번에 삭제
    if batch_clear_ranges:
        sheet.batch_clear(batch_clear_ranges)


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
"""
