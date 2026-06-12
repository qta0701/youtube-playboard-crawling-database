"""
GUI 인터페이스 및 구글 시트 관리 모듈
"""
import os
import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext
from typing import List, Dict, Optional
import gspread
from google.oauth2.service_account import Credentials
from datetime import datetime
import threading
import sys
import traceback

# 공용 헤더 및 시트 구조 관리
from sheet_config import (
    SheetType,
    normalize_header,
    match_header,
    detect_sheet_type,
    create_header_mapping,
    get_column_index,
    is_formula_column,
    get_formula_columns,
    guess_sheet_type_from_name,
    HEADER_ROW,
    FORMULA_ROW,
    DATA_START_ROW
)

# 표시 형식 복사 및 데이터 처리 유틸리티
from sheet_utils import (
    copy_number_formats,
    copy_sheet_structure,
    insert_data_with_format_preservation,
    update_data_with_format_preservation,
    clear_formula_column_data
)


class GoogleSheetsManager:
    """구글 시트 관리 클래스"""

    def __init__(self, service_account_file: str, spreadsheet_url: str):
        """
        구글 시트 매니저 초기화

        Args:
            service_account_file: 서비스 계정 JSON 파일 경로
            spreadsheet_url: 구글 스프레드시트 URL
        """
        self.service_account_file = service_account_file
        self.spreadsheet_url = spreadsheet_url
        self.client = None
        self.spreadsheet = None
        self.worksheet = None
        self._authenticate()

    def get_all_sheet_names(self) -> List[str]:
        """
        스프레드시트의 모든 시트 탭 이름 가져오기

        Returns:
            시트 이름 리스트
        """
        worksheets = self.spreadsheet.worksheets()
        return [ws.title for ws in worksheets]

    def test_connection(self) -> tuple:
        """
        구글 시트 연결 테스트

        Returns:
            (성공 여부, 메시지)
        """
        try:
            sheet_names = self.get_all_sheet_names()
            return True, f"연결 성공! 시트 개수: {len(sheet_names)}"
        except Exception as e:
            return False, f"연결 실패: {str(e)}"

    def _authenticate(self):
        """구글 시트 인증"""
        scopes = [
            'https://www.googleapis.com/auth/spreadsheets',
            'https://www.googleapis.com/auth/drive'
        ]

        credentials = Credentials.from_service_account_file(
            self.service_account_file,
            scopes=scopes
        )

        self.client = gspread.authorize(credentials)

        # 스프레드시트 열기
        self.spreadsheet = self.client.open_by_url(self.spreadsheet_url)

    def get_or_create_worksheet(self, sheet_name: str) -> gspread.Worksheet:
        """
        워크시트 가져오기 또는 생성

        Args:
            sheet_name: 시트 이름

        Returns:
            워크시트 객체
        """
        try:
            worksheet = self.spreadsheet.worksheet(sheet_name)
        except gspread.WorksheetNotFound:
            worksheet = self.spreadsheet.add_worksheet(
                title=sheet_name,
                rows=5000,
                cols=100
            )

        return worksheet

    def get_headers(self, sheet_name: str) -> List[str]:
        """
        시트의 헤더 가져오기 (1행)

        Args:
            sheet_name: 시트 이름

        Returns:
            헤더 리스트
        """
        worksheet = self.get_or_create_worksheet(sheet_name)
        headers = worksheet.row_values(1)
        return headers

    def get_all_values_unformatted(self, sheet_name: str) -> List[List]:
        """
        시트의 모든 값을 포맷되지 않은 형태로 가져오기
        숫자나 수식의 결과값을 그대로 가져옴 ("1,265개" → 1265)

        Args:
            sheet_name: 시트 이름

        Returns:
            셀 값 리스트 (UNFORMATTED_VALUE)
        """
        worksheet = self.get_or_create_worksheet(sheet_name)
        result = self.spreadsheet.values_get(
            worksheet.title,
            params={'valueRenderOption': 'UNFORMATTED_VALUE'}
        )
        return result.get('values', [])

    # 헤더 정규화 함수는 sheet_config.normalize_header()를 사용

    def parse_korean_datetime(self, date_str) -> Optional[datetime]:
        """
        한국어 날짜/시간 형식을 파싱하여 datetime 객체로 변환

        지원 형식:
        - 구글 시트 날짜 숫자 형식 (예: 45971.85161)
        - "2025. 8. 1 오후 5:38:41" (한국어 AM/PM 형식)
        - ISO 8601 형식
        - YYYY-MM-DD 형식

        Args:
            date_str: 날짜 문자열 또는 숫자

        Returns:
            datetime 객체 또는 None (파싱 실패 시)
        """
        if not date_str and date_str != 0:
            return None

        try:
            # 구글 시트 날짜 숫자 형식 처리
            # UNFORMATTED_VALUE로 읽으면 숫자로 반환됨 (예: 45971.85161)
            # 1899년 12월 30일부터의 일수
            if isinstance(date_str, (int, float)):
                from datetime import timedelta
                google_sheets_epoch = datetime(1899, 12, 30)
                return google_sheets_epoch + timedelta(days=float(date_str))

            # 문자열이지만 숫자로 변환 가능한 경우
            date_str_lower = str(date_str).lower()
            if not any(char.isalpha() for char in date_str_lower.replace('.', '').replace('-', '').replace(':', '').replace(' ', '')):
                try:
                    date_num = float(date_str)
                    # 구글 시트 날짜 범위 확인 (1900-01-01 = 1, 현재 시점 약 45000-46000)
                    if 1 <= date_num <= 100000:
                        from datetime import timedelta
                        google_sheets_epoch = datetime(1899, 12, 30)
                        return google_sheets_epoch + timedelta(days=date_num)
                except (ValueError, OverflowError):
                    pass  # 숫자가 아니거나 범위 초과면 다른 형식 시도

            # 한국어 날짜 형식 처리: "2025. 8. 1 오후 5:38:41"
            if '오전' in str(date_str) or '오후' in str(date_str):
                # 오전/오후 분리
                is_pm = '오후' in date_str
                # 오전/오후를 공백으로 치환하여 제거
                cleaned = date_str.replace('오전', ' ').replace('오후', ' ')

                # 연속된 공백을 하나로 줄이고 trim
                cleaned = ' '.join(cleaned.split())

                # "2025. 8. 1 5:38:41" 형태
                # 먼저 마지막 공백으로 날짜 부분과 시간 부분 분리
                parts = cleaned.rsplit(maxsplit=1)

                if len(parts) == 2:
                    date_part = parts[0].strip()  # "2025. 8. 1"
                    time_part = parts[1].strip()  # "5:38:41"

                    # 날짜 파싱: "2025. 8. 1" -> [2025, 8, 1]
                    # .으로 분리하고 공백 제거 후 숫자만 추출
                    date_components = []
                    for x in date_part.split('.'):
                        x = x.strip()
                        if x:
                            date_components.append(int(x))

                    # 시간 파싱: "5:38:41" -> [5, 38, 41]
                    time_components = []
                    for x in time_part.split(':'):
                        x = x.strip()
                        if x:
                            time_components.append(int(x))

                    if len(date_components) >= 3 and len(time_components) >= 3:
                        year, month, day = date_components[0], date_components[1], date_components[2]
                        hour, minute, second = time_components[0], time_components[1], time_components[2]

                        # 오후이고 12시가 아니면 12 더하기
                        if is_pm and hour != 12:
                            hour += 12
                        # 오전이고 12시면 0시로 변경
                        elif not is_pm and hour == 12:
                            hour = 0

                        return datetime(year, month, day, hour, minute, second)

            # ISO 형식 시도
            elif 'T' in date_str:
                return datetime.fromisoformat(date_str.replace('Z', '+00:00'))

            # 날짜+시간 형식 시도: "2025-11-10 20:21:06"
            elif '-' in date_str and ' ' in date_str and ':' in date_str:
                return datetime.strptime(date_str.strip(), '%Y-%m-%d %H:%M:%S')

            # 단순 날짜 형식 시도: "2025-11-10"
            elif '-' in date_str:
                return datetime.strptime(date_str.strip(), '%Y-%m-%d')

        except Exception:
            pass

        return None

    def create_header_mapping(self, headers: List[str], data_keys: List[str]) -> tuple:
        """
        헤더와 데이터 키를 매칭하여 컬럼 인덱스 맵 생성
        매칭되지 않은 키는 마지막 빈 열에 추가

        Args:
            headers: 시트 헤더 리스트
            data_keys: 데이터 딕셔너리의 키 리스트

        Returns:
            ({데이터 키: 컬럼 인덱스} 딕셔너리, 매칭되지 않은 키 리스트)
        """
        # 정규화된 헤더 매핑 (중복 시 첫 번째 인덱스 우선)
        normalized_headers = {}
        for idx, h in enumerate(headers, 1):
            if h:
                normalized = normalize_header(h)
                # 중복 헤더의 경우 첫 번째 것을 사용 (7. 채널명이 33. 채널명보다 우선)
                if normalized not in normalized_headers:
                    normalized_headers[normalized] = idx

        # 원본 헤더도 직접 매핑 (넘버링 포함된 키 지원)
        original_headers = {h: idx for idx, h in enumerate(headers, 1) if h}

        mapping = {}
        unmatched_keys = []

        for key in data_keys:
            # 1. 먼저 원본 키로 직접 매칭 시도 (예: "33. 채널명")
            if key in original_headers:
                mapping[key] = original_headers[key]
            else:
                # 2. 정규화된 키로 매칭 시도
                normalized_key = normalize_header(key)
                if normalized_key in normalized_headers:
                    mapping[key] = normalized_headers[normalized_key]
                else:
                    # 매칭되지 않은 키는 별도 리스트에 저장
                    unmatched_keys.append(key)

        return mapping, unmatched_keys

    def get_last_row(self, sheet_name: str, start_row: int = 10) -> int:
        """
        A열 기준 마지막 데이터 행 찾기

        Args:
            sheet_name: 시트 이름
            start_row: 데이터 시작 행 (기본 10)

        Returns:
            마지막 행 번호
        """
        worksheet = self.get_or_create_worksheet(sheet_name)
        col_a_values = worksheet.col_values(1)  # A열 전체 값

        # start_row부터 데이터가 있는 마지막 행 찾기
        last_row = start_row - 1
        for idx, value in enumerate(col_a_values[start_row - 1:], start=start_row):
            if value:
                last_row = idx

        return last_row

    def bulk_append_data(self, sheet_name: str, data_list: List[Dict[str, any]],
                        start_row: int = 10):
        """
        데이터 리스트를 시트에 벌크로 추가
        헤더에 없는 데이터는 마지막 빈 열에 순서대로 추가
        전역함수가 적용된 헤더는 데이터 삽입 후 자동으로 삭제됨

        Args:
            sheet_name: 시트 이름
            data_list: 추가할 데이터 리스트 (딕셔너리 리스트)
            start_row: 데이터 시작 행 (기본 10)
        """
        if not data_list:
            return

        worksheet = self.get_or_create_worksheet(sheet_name)

        # 헤더 가져오기 및 시트 타입 감지
        headers = self.get_headers(sheet_name)
        sheet_type = detect_sheet_type(headers)
        if sheet_type is None:
            sheet_type = guess_sheet_type_from_name(sheet_name)

        # 시트 타입을 확실히 알 수 없으면 기본값(VIDEO_LIST) 사용
        if sheet_type is None:
            from sheet_config import SheetType
            sheet_type = SheetType.VIDEO_LIST

        # 전역함수 열 목록 가져오기
        from sheet_config import get_formula_columns
        formula_columns = get_formula_columns(sheet_type)
        excluded_headers = {normalize_header(col) for col in formula_columns}

        original_header_count = len(headers)

        # 제외할 헤더에 해당하는 데이터 키 제거
        filtered_data_list = []
        for data in data_list:
            filtered_data = {}
            for key, value in data.items():
                # 정규화된 키로 확인
                normalized_key = normalize_header(key)
                if normalized_key not in excluded_headers:
                    filtered_data[key] = value
            filtered_data_list.append(filtered_data)

        # 헤더 매칭 (데이터 키 -> 컬럼 인덱스)
        data_keys = list(filtered_data_list[0].keys()) if filtered_data_list else []
        header_mapping, unmatched_keys = self.create_header_mapping(headers, data_keys)

        # 매칭되지 않은 키가 있으면 헤더에 추가
        new_headers_added = []
        if unmatched_keys:
            # 첫 번째 빈 열 찾기
            first_empty_col = len(headers) + 1
            for idx, key in enumerate(unmatched_keys):
                col_index = first_empty_col + idx
                header_mapping[key] = col_index
                headers.append(key)
                new_headers_added.append(key)

            # 시트 크기 확장 (필요한 경우)
            required_cols = first_empty_col + len(new_headers_added) - 1
            current_cols = worksheet.col_count
            if required_cols > current_cols:
                worksheet.resize(cols=required_cols + 10)  # 여유 있게 10개 더 추가

            # 헤더 행 업데이트 (새로운 헤더만 추가)
            if new_headers_added:
                start_col_letter = self._col_num_to_letter(first_empty_col)
                end_col_letter = self._col_num_to_letter(first_empty_col + len(new_headers_added) - 1)
                header_range = f'{start_col_letter}1:{end_col_letter}1'
                worksheet.update(header_range, [new_headers_added], value_input_option='USER_ENTERED')

        # 마지막 행 찾기
        last_row = self.get_last_row(sheet_name, start_row)
        next_row = last_row + 1

        # 데이터를 행 단위로 변환 (필터링된 데이터 사용)
        rows_to_add = []
        for data in filtered_data_list:
            row = [''] * len(headers)  # 빈 행 초기화 (새 헤더 포함)

            for key, value in data.items():
                if key in header_mapping:
                    col_idx = header_mapping[key] - 1  # 0-based index
                    row[col_idx] = str(value) if value is not None else ''

            rows_to_add.append(row)

        # 벌크 업데이트
        if rows_to_add:
            # 시트 행 크기 확장 (필요한 경우)
            required_rows = next_row + len(rows_to_add) - 1
            current_rows = worksheet.row_count
            if required_rows > current_rows:
                # 여유 있게 1000개 행 추가
                worksheet.resize(rows=required_rows + 1000, cols=worksheet.col_count)

            end_col_letter = self._col_num_to_letter(len(headers))
            range_name = f'A{next_row}:{end_col_letter}{next_row + len(rows_to_add) - 1}'
            worksheet.update(range_name, rows_to_add, value_input_option='USER_ENTERED')

            # 전역함수 열의 10행 이후 데이터 삭제
            standard_header_mapping = create_header_mapping(headers, sheet_type)
            clear_formula_column_data(worksheet, standard_header_mapping, sheet_type)

    def bulk_append_data_in_batches(self, sheet_name: str, data_list: List[Dict[str, any]],
                                   batch_size: int = 100, start_row: int = 10,
                                   progress_callback=None):
        """
        대규모 데이터를 배치로 나눠서 시트에 추가 (1만+ 행 처리 최적화)

        Args:
            sheet_name: 시트 이름
            data_list: 추가할 데이터 리스트 (딕셔너리 리스트)
            batch_size: 배치 크기 (기본 100, API 제한 고려)
            start_row: 데이터 시작 행 (기본 10)
            progress_callback: 진행률 콜백 함수 (선택, 인자: current, total, message)
        """
        if not data_list:
            return

        total_items = len(data_list)
        total_batches = (total_items + batch_size - 1) // batch_size

        for batch_idx in range(total_batches):
            start_idx = batch_idx * batch_size
            end_idx = min(start_idx + batch_size, total_items)
            batch_data = data_list[start_idx:end_idx]

            # 배치 처리
            self.bulk_append_data(sheet_name, batch_data, start_row)

            # 진행률 업데이트
            if progress_callback:
                processed = end_idx
                message = f"데이터 저장 중... ({processed}/{total_items})"
                progress_callback(processed, total_items, message)

    def _col_num_to_letter(self, col_num: int) -> str:
        """
        컬럼 번호를 알파벳으로 변환 (1 -> A, 27 -> AA)

        Args:
            col_num: 컬럼 번호 (1부터 시작)

        Returns:
            컬럼 알파벳
        """
        result = ""
        while col_num > 0:
            col_num -= 1
            result = chr(65 + (col_num % 26)) + result
            col_num //= 26
        return result

    def initialize_headers(self, sheet_name: str):
        """
        시트 헤더 초기화 (영상 리스트 시트 62개 열)

        Args:
            sheet_name: 시트 이름
        """
        headers = [
            '1. 영상 ID', '2. 영상 업로드날짜', '3. 수집날짜', '4. 검색 키워드',
            '5. 영상 링크', '6. 제목', '7. 채널명', '8. 조회수', '9. 숏폼여부',
            '10. 영상길이', '11. 분야1', '12. 분야2', '13. 구독자수', '14. 좋아요 수',
            '15. 댓글수', '16. 구독자 대비 조회수 배율', '17. 조회수 대비 좋아요',
            '18. 조회수 대비 댓글', '19. 영상 업로드 이후 수집날짜까지 기간',
            '20. 일평균 조회수', '21. 조회수 100만 이상', '22. 조회수 500만 이상',
            '23. 조회수 1,000만 이상', '24. 구독자 대비 조회수 몇 배 이상',
            '25. 좋아요 3%이상', '26. 음성 나레이션 여부', '27. 퍼온 영상인가?',
            '28. AI생성 영상인가?', '29. 레퍼런스 사용할 영상인가?', '30. 자막 다운 여부',
            '31. 채널 수익화 여부', '32. 쇼핑 수익화여부', '33. 채널명', '34. 채널국가',
            '35. 사용언어', '36. 채널 ID', '37. 채널링크', '38. 썸네일 링크',
            '39. 재생목록 이름', '40. 영상갯수', '41. 채널 전체 조회수',
            '42. 영상당 평균 조회수', '43. 채널 개설일', '44. 채널개설 이후 수집일까지 경과일',
            '45. 카테고리 ID', '46. 카테고리 분류',
            '47. 디스크립션', '48. 디스크립션 텍스트 수', '49. 해시태그 유무',
            '50. 사용 해시태그', '51. 그래프', '52. 후킹자막', '53. 대본내용',
            '54. 대본유무', '55. 분석', '56. 대본파일', '57. 썸네일 여부',
            '58. 썸네일 이미지주소', '59. 썸네일 경로', '60. 원본 행순서',
            '61. 채널 디스크립션', '62. 채널 핸들'
        ]

        worksheet = self.get_or_create_worksheet(sheet_name)
        worksheet.update('A1', [headers], value_input_option='USER_ENTERED')

    def get_playlist_data(self, sheet_name: str = '재생목록ID') -> List[Dict]:
        """
        재생목록ID 시트에서 재생목록 데이터 가져오기

        Args:
            sheet_name: 시트 이름 (기본값: '재생목록ID')

        Returns:
            재생목록 데이터 리스트
        """
        worksheet = self.get_or_create_worksheet(sheet_name)
        headers = worksheet.row_values(1)

        # 헤더에서 필요한 컬럼 인덱스 찾기 (normalize_header 사용)
        col_mapping = {}
        for idx, header in enumerate(headers):
            normalized = normalize_header(header)
            if normalized == '재생목록 ID' or normalized == '재생목록ID':
                col_mapping['playlist_id'] = idx + 1
            elif normalized == '재생목록 이름':
                col_mapping['playlist_name'] = idx + 1
            elif normalized == '영상갯수' or normalized == '영상 갯수':
                col_mapping['video_count'] = idx + 1
            elif normalized == '마지막 체크일':
                col_mapping['last_check'] = idx + 1

        # 필수 컬럼 확인
        if 'playlist_id' not in col_mapping:
            raise Exception(f"필수 헤더 '재생목록 ID'를 찾을 수 없습니다. 현재 헤더: {headers}")

        # 모든 데이터 가져오기 (2행부터)
        all_values = worksheet.get_all_values()
        playlist_data = []

        for row_idx, row in enumerate(all_values[1:], start=2):  # 2행부터 시작
            playlist_id_col = col_mapping.get('playlist_id', 0)
            if playlist_id_col > 0 and len(row) >= playlist_id_col and row[playlist_id_col - 1]:
                playlist_data.append({
                    'row': row_idx,
                    'playlist_id': row[col_mapping['playlist_id'] - 1] if 'playlist_id' in col_mapping and len(row) >= col_mapping['playlist_id'] else '',
                    'playlist_name': row[col_mapping['playlist_name'] - 1] if 'playlist_name' in col_mapping and len(row) >= col_mapping['playlist_name'] else '',
                    'video_count': row[col_mapping['video_count'] - 1] if 'video_count' in col_mapping and len(row) >= col_mapping['video_count'] else '',
                    'last_check': row[col_mapping['last_check'] - 1] if 'last_check' in col_mapping and len(row) >= col_mapping['last_check'] else ''
                })

        return playlist_data

    def get_channel_data(self, sheet_name: str = '채널 리스트',
                         filter_fetched: Optional[List[bool]] = None,
                         filter_category1: Optional[List[str]] = None,
                         filter_category2: Optional[List[str]] = None) -> List[Dict]:
        """
        채널 리스트 시트에서 채널 데이터 가져오기 (필터링 지원)

        Args:
            sheet_name: 시트 이름 (기본값: '채널 리스트')
            filter_fetched: [True, False] 리스트로 합집합 (예: [True] = 가져온 것만, [False] = 안 가져온 것만, [True, False] = 전체)
            filter_category1: 분야1 필터 리스트 (합집합)
            filter_category2: 분야2 필터 리스트 (합집합)

            필터링 로직:
            - 가져왔는지 여부: 합집합
            - 분야1: 합집합
            - 분야2: 합집합
            - 분야1과 분야2: 교집합 (둘 다 조건을 만족해야 함)

        Returns:
            채널 데이터 리스트
        """
        worksheet = self.get_or_create_worksheet(sheet_name)
        headers = worksheet.row_values(1)

        # 필요한 컬럼 찾기
        col_mapping = {}
        for idx, header in enumerate(headers):
            normalized = normalize_header(header)
            if normalized == '채널ID':  # 공백 제거
                col_mapping['channel_id'] = idx + 1
            elif normalized == '가져왔는지여부':  # 공백 제거
                col_mapping['fetched'] = idx + 1
            elif normalized == '수집날짜':
                col_mapping['collection_date'] = idx + 1
            elif normalized == '채널링크':
                col_mapping['channel_link'] = idx + 1
            elif normalized == '채널명':
                col_mapping['channel_name'] = idx + 1
            elif normalized == '분야1':
                col_mapping['category1'] = idx + 1
            elif normalized == '분야2':
                col_mapping['category2'] = idx + 1
            elif normalized == '가져올채널':  # 공백 제거
                col_mapping['fetch_channel'] = idx + 1
            elif normalized == '구독자수':
                col_mapping['subscriber_count'] = idx + 1
            elif normalized == '사용언어':
                col_mapping['language'] = idx + 1
            elif normalized == '재생목록이름':  # 공백 제거
                col_mapping['playlist_name'] = idx + 1
            elif normalized == '벤치마킹채널여부':
                col_mapping['benchmark_channel'] = idx + 1

        if 'channel_id' not in col_mapping or 'fetch_channel' not in col_mapping:
            print(f"[디버그] 읽은 헤더: {headers}")
            print(f"[디버그] 정규화된 헤더: {[normalize_header(h) for h in headers]}")
            print(f"[디버그] 찾은 컬럼 매핑: {col_mapping}")
            raise Exception(f"필수 헤더를 찾을 수 없습니다. channel_id={('channel_id' in col_mapping)}, fetch_channel={('fetch_channel' in col_mapping)}")

        # 모든 데이터 가져오기 (10행부터) - UNFORMATTED_VALUE 사용
        all_values = self.get_all_values_unformatted(sheet_name)
        channel_data = []

        for row_idx, row in enumerate(all_values[9:], start=10):  # 10행부터 시작
            # 행이 완전히 비어있으면 스킵
            if not row or all(not str(cell).strip() for cell in row):
                continue

            if len(row) <= max(col_mapping.values()) - 1:
                # 행이 있지만 열이 부족한 경우에는 빈 값으로 채움
                row.extend([''] * (max(col_mapping.values()) - len(row)))

            # 기본 데이터 추출
            channel_id_raw = row[col_mapping['channel_id'] - 1] if len(row) > col_mapping['channel_id'] - 1 else ''
            channel_id = str(channel_id_raw).strip() if channel_id_raw else ''

            fetch_channel_raw = row[col_mapping['fetch_channel'] - 1] if len(row) > col_mapping['fetch_channel'] - 1 else ''
            fetch_channel = str(fetch_channel_raw).strip() if fetch_channel_raw else ''

            fetched_raw = row[col_mapping['fetched'] - 1] if 'fetched' in col_mapping and len(row) > col_mapping['fetched'] - 1 else ''
            fetched = str(fetched_raw).strip() if fetched_raw else ''

            category1_raw = row[col_mapping['category1'] - 1] if 'category1' in col_mapping and len(row) > col_mapping['category1'] - 1 else ''
            category1 = str(category1_raw).strip() if category1_raw else ''

            category2_raw = row[col_mapping['category2'] - 1] if 'category2' in col_mapping and len(row) > col_mapping['category2'] - 1 else ''
            category2 = str(category2_raw).strip() if category2_raw else ''

            # 가져올 채널이 표시되어 있고, 채널 ID가 있어야 함
            if not fetch_channel or not channel_id:
                continue

            # 가져올 채널이 'x'인 경우 제외 (삭제된 채널)
            if fetch_channel.lower() == 'x':
                continue

            # 분야1이 비어있으면 제외
            if not category1:
                continue

            # 필터 적용
            # 가져왔는지 여부 필터 (합집합)
            if filter_fetched is not None:
                fetched_bool = bool(fetched)  # 값이 있으면 True, 없으면 False
                if fetched_bool not in filter_fetched:
                    continue

            # 분야1 필터 (합집합) - 하나라도 일치하면 통과
            if filter_category1:
                if category1 not in filter_category1:
                    continue

            # 분야2 필터 (합집합) - 하나라도 일치하면 통과
            if filter_category2:
                if category2 not in filter_category2:
                    continue

            # 데이터 수집 (UNFORMATTED_VALUE이므로 숫자는 그대로, 문자열만 strip)
            channel_name_raw = row[col_mapping['channel_name'] - 1] if 'channel_name' in col_mapping and len(row) > col_mapping['channel_name'] - 1 else ''
            channel_name = str(channel_name_raw).strip() if channel_name_raw else ''

            # 수집날짜는 숫자 형식일 수 있으므로 원본 그대로 저장
            collection_date_raw = row[col_mapping['collection_date'] - 1] if 'collection_date' in col_mapping and len(row) > col_mapping['collection_date'] - 1 else ''
            collection_date = collection_date_raw  # 원본 그대로 저장 (숫자 또는 문자열)

            subscriber_count_raw = row[col_mapping['subscriber_count'] - 1] if 'subscriber_count' in col_mapping and len(row) > col_mapping['subscriber_count'] - 1 else ''
            subscriber_count = str(subscriber_count_raw).strip() if isinstance(subscriber_count_raw, str) else subscriber_count_raw

            language_raw = row[col_mapping['language'] - 1] if 'language' in col_mapping and len(row) > col_mapping['language'] - 1 else ''
            language = str(language_raw).strip() if language_raw else ''

            playlist_name_raw = row[col_mapping['playlist_name'] - 1] if 'playlist_name' in col_mapping and len(row) > col_mapping['playlist_name'] - 1 else ''
            playlist_name = str(playlist_name_raw).strip() if playlist_name_raw else ''

            benchmark_channel_raw = row[col_mapping['benchmark_channel'] - 1] if 'benchmark_channel' in col_mapping and len(row) > col_mapping['benchmark_channel'] - 1 else ''
            benchmark_channel = str(benchmark_channel_raw).strip() if benchmark_channel_raw else ''

            channel_data.append({
                'row': row_idx,
                'channel_id': channel_id,
                'channel_name': channel_name,
                'fetched': fetched,
                'collection_date': collection_date,  # 키 이름을 collection_date로 통일
                'category1': category1,
                'category2': category2,
                'subscriber_count': subscriber_count,
                'language': language,
                'playlist_name': playlist_name,
                'fetch_channel': fetch_channel,
                'benchmark_channel': benchmark_channel
            })

        return channel_data

    def get_all_categories(self, sheet_name: str = '채널 리스트') -> tuple:
        """
        채널 리스트 시트에서 모든 분야1, 분야2 값 가져오기

        Args:
            sheet_name: 시트 이름

        Returns:
            (분야1 리스트, 분야2 리스트)
        """
        worksheet = self.get_or_create_worksheet(sheet_name)
        headers = worksheet.row_values(1)

        # 분야1, 분야2 컬럼 찾기
        category1_col = None
        category2_col = None
        for idx, header in enumerate(headers):
            normalized = normalize_header(header)
            if normalized == '분야1':
                category1_col = idx + 1
            elif normalized == '분야2':
                category2_col = idx + 1

        if not category1_col or not category2_col:
            return [], []

        # 모든 값 가져오기
        all_values = worksheet.get_all_values()

        category1_set = set()
        category2_set = set()

        for row in all_values[9:]:  # 10행부터
            if len(row) > category1_col - 1:
                cat1 = row[category1_col - 1].strip()
                if cat1:
                    category1_set.add(cat1)

            if len(row) > category2_col - 1:
                cat2 = row[category2_col - 1].strip()
                if cat2:
                    category2_set.add(cat2)

        return sorted(list(category1_set)), sorted(list(category2_set))

    def update_channel_info(self, sheet_name: str, row: int, channel_info: Dict):
        """
        채널 리스트 시트의 채널 정보 업데이트

        Args:
            sheet_name: 시트 이름
            row: 업데이트할 행 번호
            channel_info: 업데이트할 채널 정보 딕셔너리
        """
        worksheet = self.get_or_create_worksheet(sheet_name)
        headers = worksheet.row_values(1)

        # 전역함수가 적용된 헤더 (업데이트하지 않음)
        excluded_headers = {
            '채널전체 조회수(변환)', '영상당 평균 조회수(전투력)',
            '구독자 대비 조회수배율(최근30개)', '콘텐츠파워(최근30개)', '공정성과지수(최근30개)',
            '영상당 구독자수', '구독자1명 당 조회수',
            '조회수 100만이상 비율', '조회수 500만이상 비율', '조회수 1,000만이상 비율',
            '개설 이후 수집날짜까지 기간', '수집날짜 경과일'
        }

        current_time = datetime.now().strftime('%Y-%m-%d')

        # 채널명 가져오기 (평균 계산에 필요)
        channel_name = channel_info.get('채널명', '')

        # 영상 리스트 시트에서 평균값 계산
        avg_duration = ''
        avg_views = 0.0
        if channel_name:
            avg_duration = self.calculate_avg_video_duration_by_channel_name(channel_name)
            avg_views = self.calculate_avg_views_by_channel_name(channel_name)

        # 업데이트할 셀 목록
        updates = []

        for idx, header in enumerate(headers):
            normalized = normalize_header(header)

            if normalized in excluded_headers:
                continue

            col_letter = self._col_num_to_letter(idx + 1)
            cell = f'{col_letter}{row}'

            if normalized == '가져왔는지 여부':
                updates.append({'range': cell, 'values': [['ㅇ']]})
            elif normalized == '수집날짜':
                updates.append({'range': cell, 'values': [[current_time]]})
            elif normalized == '평균 영상길이':
                updates.append({'range': cell, 'values': [[avg_duration]]})
            elif normalized == '수집한 영상 평균 조회수':
                updates.append({'range': cell, 'values': [[avg_views]]})
            elif normalized in channel_info:
                value = channel_info[normalized]
                updates.append({'range': cell, 'values': [[value]]})

        # 배치 업데이트
        if updates:
            worksheet.batch_update(updates, value_input_option='USER_ENTERED')

    def count_videos_in_video_sheet(self, channel_id: str, sheet_name: str = '영상 리스트') -> int:
        """
        영상 리스트 시트에서 해당 채널의 영상 갯수 카운트

        Args:
            channel_id: 채널 ID
            sheet_name: 시트 이름 (기본값: '영상 리스트')

        Returns:
            영상 갯수
        """
        worksheet = self.get_or_create_worksheet(sheet_name)
        headers = worksheet.row_values(1)

        # 채널 ID 컬럼 찾기
        channel_id_col = None
        for idx, header in enumerate(headers):
            if normalize_header(header) == '채널 ID':
                channel_id_col = idx
                break

        if channel_id_col is None:
            return 0

        # 해당 채널 ID를 가진 영상 개수 세기 (10행부터)
        all_values = worksheet.get_all_values()
        count = 0
        for row in all_values[9:]:  # 10행부터 시작
            if len(row) > channel_id_col:
                row_channel_id = row[channel_id_col].strip()
                if row_channel_id == channel_id:
                    count += 1

        return count

    def calculate_avg_video_duration_by_channel_name(self, channel_name: str, sheet_name: str = '영상 리스트') -> str:
        """
        영상 리스트 시트에서 채널명 기준으로 평균 영상길이 계산

        Args:
            channel_name: 채널명
            sheet_name: 시트 이름 (기본값: '영상 리스트')

        Returns:
            평균 영상길이 문자열 (예: "1분 30초", "45초")
        """
        worksheet = self.get_or_create_worksheet(sheet_name)
        headers = worksheet.row_values(1)

        # 채널명과 영상길이 컬럼 찾기
        channel_name_col = None
        duration_col = None
        for idx, header in enumerate(headers):
            normalized = normalize_header(header)
            if normalized == '채널명':
                channel_name_col = idx
            elif normalized == '영상길이':
                duration_col = idx

        if channel_name_col is None or duration_col is None:
            return ""

        # 해당 채널의 모든 영상길이 수집 (10행부터)
        all_values = worksheet.get_all_values()
        durations_in_seconds = []

        for row in all_values[9:]:  # 10행부터 시작
            if len(row) > max(channel_name_col, duration_col):
                row_channel_name = row[channel_name_col].strip()
                if row_channel_name == channel_name:
                    duration_str = row[duration_col].strip()
                    # 영상길이 텍스트를 초로 변환 (예: "1분 30초" -> 90, "45초" -> 45)
                    seconds = self._parse_duration_to_seconds(duration_str)
                    if seconds > 0:
                        durations_in_seconds.append(seconds)

        if not durations_in_seconds:
            return ""

        # 평균 계산
        avg_seconds = sum(durations_in_seconds) / len(durations_in_seconds)

        # 초를 "X분 Y초" 형식으로 변환
        return self._seconds_to_duration_string(int(avg_seconds))

    def _parse_duration_to_seconds(self, duration_str: str) -> int:
        """
        영상길이 문자열을 초로 변환

        Args:
            duration_str: 영상길이 문자열 (예: "1분 30초", "45초", "1분")

        Returns:
            초 단위 정수
        """
        if not duration_str:
            return 0

        import re
        total_seconds = 0

        # 분 추출
        minute_match = re.search(r'(\d+)\s*분', duration_str)
        if minute_match:
            total_seconds += int(minute_match.group(1)) * 60

        # 초 추출
        second_match = re.search(r'(\d+)\s*초', duration_str)
        if second_match:
            total_seconds += int(second_match.group(1))

        return total_seconds

    def _seconds_to_duration_string(self, seconds: int) -> str:
        """
        초를 "X분 Y초" 형식 문자열로 변환

        Args:
            seconds: 초 단위 정수

        Returns:
            포맷된 문자열 (예: "1분 30초", "45초")
        """
        if seconds < 60:
            return f"{seconds}초"

        minutes = seconds // 60
        remaining_seconds = seconds % 60

        if remaining_seconds == 0:
            return f"{minutes}분"

        return f"{minutes}분 {remaining_seconds}초"

    def calculate_avg_views_by_channel_name(self, channel_name: str, sheet_name: str = '영상 리스트') -> float:
        """
        영상 리스트 시트에서 채널명 기준으로 수집한 영상들의 평균 조회수 계산

        Args:
            channel_name: 채널명
            sheet_name: 시트 이름 (기본값: '영상 리스트')

        Returns:
            평균 조회수 (소수점 포함)
        """
        worksheet = self.get_or_create_worksheet(sheet_name)
        headers = worksheet.row_values(1)

        # 채널명과 조회수 컬럼 찾기
        channel_name_col = None
        views_col = None
        for idx, header in enumerate(headers):
            normalized = normalize_header(header)
            if normalized == '채널명':
                channel_name_col = idx
            elif normalized == '조회수':
                views_col = idx

        if channel_name_col is None or views_col is None:
            return 0.0

        # 해당 채널의 모든 조회수 수집 (10행부터)
        all_values = worksheet.get_all_values()
        views_list = []

        for row in all_values[9:]:  # 10행부터 시작
            if len(row) > max(channel_name_col, views_col):
                row_channel_name = row[channel_name_col].strip()
                if row_channel_name == channel_name:
                    views_str = row[views_col].strip()
                    # 조회수를 숫자로 변환 (콤마 제거)
                    try:
                        views = int(views_str.replace(',', ''))
                        views_list.append(views)
                    except (ValueError, AttributeError):
                        continue

        if not views_list:
            return 0.0

        # 평균 계산
        avg_views = sum(views_list) / len(views_list)
        return avg_views

    def get_channel_name_from_channel_list(self, channel_id: str, sheet_name: str = '채널 리스트') -> Optional[str]:
        """
        채널 리스트 시트에서 채널명 가져오기

        Args:
            channel_id: 채널 ID
            sheet_name: 시트 이름 (기본값: '채널 리스트')

        Returns:
            채널명 (없으면 None)
        """
        worksheet = self.get_or_create_worksheet(sheet_name)
        headers = worksheet.row_values(1)

        # 필요한 컬럼 인덱스 찾기
        channel_id_col = None
        channel_name_col = None
        for idx, header in enumerate(headers):
            normalized = normalize_header(header)
            if normalized == '채널 ID':
                channel_id_col = idx
            elif normalized == '채널명':
                channel_name_col = idx

        if channel_id_col is None or channel_name_col is None:
            return None

        # 해당 채널 ID를 가진 행 찾기 (10행부터)
        all_values = worksheet.get_all_values()
        for row in all_values[9:]:  # 10행부터 시작
            if len(row) > max(channel_id_col, channel_name_col):
                row_channel_id = row[channel_id_col].strip()
                if row_channel_id == channel_id:
                    return row[channel_name_col].strip()

        return None

    def get_channel_info_from_video_sheet(self, channel_id: str, sheet_name: str = '영상 리스트') -> Optional[Dict]:
        """
        영상 리스트 시트에서 채널 정보 추출 (API 호출 없음, 쿼터 0)

        Args:
            channel_id: 채널 ID
            sheet_name: 시트 이름 (기본값: '영상 리스트')

        Returns:
            채널 정보 딕셔너리 (없으면 None)
        """
        worksheet = self.get_or_create_worksheet(sheet_name)
        headers = worksheet.row_values(1)

        # 필요한 컬럼 인덱스 찾기 (여러 가능한 헤더 이름 고려)
        col_mapping = {}
        target_headers = {
            '채널 ID': 'channel_id',
            '채널명': 'channel_name',
            '구독자수': 'subscriber_count',
            '구독자': 'subscriber_count',
            '영상갯수': 'video_count',
            '채널 영상갯수': 'video_count',
            '채널전체 영상갯수': 'video_count',
            '채널 전체 조회수': 'total_view_count',
            '채널전체 조회수': 'total_view_count',
            '채널 조회수': 'total_view_count',
            '채널국가': 'channel_country',
            '채널 국가': 'channel_country',
            '채널 개설일': 'published_at',
            '개설일': 'published_at',
            '채널 디스크립션': 'channel_description',
            '채널디스크립션': 'channel_description',
            '채널 핸들': 'channel_handle',
            '채널핸들': 'channel_handle'
        }

        for idx, header in enumerate(headers):
            normalized = normalize_header(header)
            if normalized in target_headers:
                # 중복 방지: 이미 매핑된 키가 없을 때만 추가
                key = target_headers[normalized]
                if key not in col_mapping:
                    col_mapping[key] = idx + 1

        if 'channel_id' not in col_mapping:
            return None

        # 해당 채널 ID를 가진 첫 번째 영상 찾기 (10행부터)
        all_values = worksheet.get_all_values()
        for row_idx, row in enumerate(all_values[9:], start=10):  # 10행부터 시작
            if len(row) < col_mapping['channel_id']:
                continue

            row_channel_id = row[col_mapping['channel_id'] - 1].strip()
            if row_channel_id == channel_id:
                # 채널 정보 추출
                channel_info = {}
                for key, col_idx in col_mapping.items():
                    if col_idx - 1 < len(row):
                        value = row[col_idx - 1].strip()
                        channel_info[key] = value
                    else:
                        channel_info[key] = ''

                return channel_info

        return None

    def get_channels_with_missing_info(self, sheet_name: str = '채널 리스트') -> List[Dict]:
        """
        가져왔는지 여부가 O인 채널 중 필수 정보가 비어있거나 수집영상 갯수가 0인 채널 찾기

        Args:
            sheet_name: 시트 이름 (기본값: '채널 리스트')

        Returns:
            업데이트가 필요한 채널 정보 리스트
        """
        worksheet = self.get_or_create_worksheet(sheet_name)
        headers = worksheet.row_values(1)

        # 필요한 컬럼 인덱스 찾기
        col_mapping = {}
        target_headers = {
            '가져왔는지 여부': 'fetched',
            '채널 ID': 'channel_id',
            '채널명': 'channel_name',
            '구독자수': 'subscriber_count',
            '채널전체 영상갯수': 'video_count',
            '채널전체 조회수': 'total_view_count',
            '재생목록 이름': 'playlist_name',
            '채널국가': 'channel_country',
            '개설일': 'published_at',
            '채널 디스크립션': 'channel_description',
            '채널 핸들': 'channel_handle',
            '가져온 영상갯수': 'collected_video_count'
        }

        for idx, header in enumerate(headers):
            normalized = normalize_header(header)
            if normalized in target_headers:
                col_mapping[target_headers[normalized]] = idx + 1

        # 필수 컬럼 확인
        if 'fetched' not in col_mapping or 'channel_id' not in col_mapping:
            raise Exception("필수 헤더를 찾을 수 없습니다.")

        # 모든 데이터 가져오기 (10행부터)
        all_values = worksheet.get_all_values()
        channels_to_update = []

        for row_idx, row in enumerate(all_values[9:], start=10):  # 10행부터 시작
            if len(row) < max(col_mapping.values()):
                # 행이 충분히 길지 않으면 빈 값으로 확장
                row = row + [''] * (max(col_mapping.values()) - len(row))

            # 가져왔는지 여부 확인
            fetched = row[col_mapping['fetched'] - 1].strip() if col_mapping['fetched'] - 1 < len(row) else ''
            if not fetched:  # 비어있으면 제외
                continue

            # 채널 ID 확인
            channel_id = row[col_mapping['channel_id'] - 1].strip() if col_mapping['channel_id'] - 1 < len(row) else ''
            if not channel_id:
                continue

            # 수집영상 갯수 확인 (0이거나 비어있으면 업데이트 필요)
            collected_count_zero = False
            if 'collected_video_count' in col_mapping:
                col_idx = col_mapping['collected_video_count'] - 1
                collected_count_str = row[col_idx].strip() if col_idx < len(row) else ''
                try:
                    collected_count = int(collected_count_str) if collected_count_str else 0
                    if collected_count == 0:
                        collected_count_zero = True
                except ValueError:
                    collected_count_zero = True

            # 필수 정보 중 하나라도 비어있는지 확인
            has_missing = False
            for key in ['subscriber_count', 'video_count', 'total_view_count',
                       'playlist_name', 'channel_country', 'published_at',
                       'channel_description', 'channel_handle']:
                if key in col_mapping:
                    col_idx = col_mapping[key] - 1
                    value = row[col_idx].strip() if col_idx < len(row) else ''
                    if not value:
                        has_missing = True
                        break

            # 필수 정보가 비어있거나 수집영상 갯수가 0인 경우
            if has_missing or collected_count_zero:
                channel_name = row[col_mapping['channel_name'] - 1].strip() if 'channel_name' in col_mapping and col_mapping['channel_name'] - 1 < len(row) else ''
                channels_to_update.append({
                    'row': row_idx,
                    'channel_id': channel_id,
                    'channel_name': channel_name
                })

        return channels_to_update

    def update_playlist_info(self, sheet_name: str, row: int, video_count: int):
        """
        재생목록ID 시트의 영상갯수와 마지막 체크일 업데이트

        Args:
            sheet_name: 시트 이름
            row: 업데이트할 행 번호
            video_count: 영상 개수
        """
        worksheet = self.get_or_create_worksheet(sheet_name)
        headers = worksheet.row_values(1)

        # 헤더에서 컬럼 인덱스 찾기
        try:
            video_count_col = headers.index('영상갯수') + 1
            last_check_col = headers.index('마지막 체크일') + 1
        except ValueError as e:
            raise Exception(f"필수 헤더를 찾을 수 없습니다: {e}")

        current_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

        # 영상갯수 업데이트
        video_count_cell = f'{self._col_num_to_letter(video_count_col)}{row}'
        worksheet.update(video_count_cell, [[video_count]], value_input_option='USER_ENTERED')

        # 마지막 체크일 업데이트
        last_check_cell = f'{self._col_num_to_letter(last_check_col)}{row}'
        worksheet.update(last_check_cell, [[current_time]], value_input_option='USER_ENTERED')

    def find_video_row(self, sheet_name: str, video_id: str) -> Optional[int]:
        """
        영상 ID를 기반으로 시트에서 해당 영상의 행 번호 찾기

        Args:
            sheet_name: 시트 이름
            video_id: 영상 ID

        Returns:
            행 번호 (없으면 None)
        """
        worksheet = self.get_or_create_worksheet(sheet_name)
        headers = worksheet.row_values(1)

        # 영상 ID 컬럼 찾기
        try:
            video_id_col_index = None
            for idx, header in enumerate(headers):
                normalized = normalize_header(header)
                if normalized == '영상 ID':
                    video_id_col_index = idx + 1
                    break

            if not video_id_col_index:
                return None

            # A열 기준으로 영상 ID 찾기
            video_ids = worksheet.col_values(video_id_col_index)

            for row_idx, vid in enumerate(video_ids[1:], start=2):  # 2행부터
                if vid == video_id:
                    return row_idx

        except Exception:
            pass

        return None

    def get_all_video_ids(self, sheet_name: str) -> Dict[str, int]:
        """
        시트의 모든 영상 ID를 한 번에 가져와서 딕셔너리로 반환 (배치 처리)

        Args:
            sheet_name: 시트 이름

        Returns:
            {영상 ID: 행 번호} 딕셔너리
        """
        worksheet = self.get_or_create_worksheet(sheet_name)
        headers = worksheet.row_values(1)

        # 영상 ID 컬럼 찾기
        video_id_col_index = None
        for idx, header in enumerate(headers):
            normalized = normalize_header(header)
            if normalized == '영상 ID':
                video_id_col_index = idx + 1
                break

        if not video_id_col_index:
            return {}

        # 영상 ID 컬럼 전체 가져오기 (한 번의 API 호출)
        video_ids = worksheet.col_values(video_id_col_index)

        # 딕셔너리 생성 {영상 ID: 행 번호}
        video_id_map = {}
        for row_idx, vid in enumerate(video_ids[1:], start=2):  # 2행부터
            if vid:
                video_id_map[vid] = row_idx

        return video_id_map

    def bulk_update_video_rows(self, sheet_name: str, updates: List[tuple]):
        """
        여러 행의 비디오 데이터를 배치 업데이트 (최적화)

        Args:
            sheet_name: 시트 이름
            updates: [(row, data), ...] 형태의 업데이트 리스트
        """
        if not updates:
            return

        # 전역함수가 적용되어 업데이트하지 않을 헤더 목록 (영상 리스트 시트)
        # 9행에 전역함수가 있는 열은 10행 이후 데이터 삽입 불필요
        excluded_headers = {
            '숏폼여부',
            '영상 업로드 이후 수집날짜까지 기간',
            '일평균 조회수',
            '조회수 100만 이상',
            '조회수 500만 이상',
            '조회수 1,000만 이상',
            '구독자 대비 조회수 몇 배 이상',
            '좋아요 3%이상',
            '좋아요 3% 이상',  # 띄어쓰기 변형 허용
            '채널개설 이후 수집일까지 경과일',
            '카테고리 분류',
            '사용 해시태그',
            '대본유무'
        }

        worksheet = self.get_or_create_worksheet(sheet_name)
        headers = self.get_headers(sheet_name)

        # 배치 업데이트 데이터 준비
        batch_updates = []

        for row, data in updates:
            # 필터링된 데이터 생성 (excluded_headers 제외)
            filtered_data = {}
            for key, value in data.items():
                normalized_key = normalize_header(key)
                if normalized_key not in excluded_headers:
                    filtered_data[key] = value

            # 헤더 매칭
            header_mapping, _ = self.create_header_mapping(headers, list(filtered_data.keys()))

            # 행 데이터 생성 (모든 필드를 업데이트, 채널명 포함)
            row_data = [''] * len(headers)
            for key, value in filtered_data.items():
                if key in header_mapping:
                    col_idx = header_mapping[key] - 1
                    row_data[col_idx] = str(value) if value is not None else ''

            # 업데이트 범위 추가
            end_col_letter = self._col_num_to_letter(len(headers))
            range_name = f'A{row}:{end_col_letter}{row}'
            batch_updates.append({'range': range_name, 'values': [row_data]})

        # 배치 업데이트 실행 (100개씩)
        for i in range(0, len(batch_updates), 100):
            batch = batch_updates[i:i+100]
            worksheet.batch_update(batch, value_input_option='USER_ENTERED')

    def update_video_row(self, sheet_name: str, row: int, data: Dict[str, any]):
        """
        특정 행의 비디오 데이터 업데이트 (하위 호환성 유지)

        Args:
            sheet_name: 시트 이름
            row: 업데이트할 행 번호
            data: 업데이트할 데이터 딕셔너리
        """
        self.bulk_update_video_rows(sheet_name, [(row, data)])


class YouTubeSearchGUI:
    """YouTube 검색 GUI 인터페이스"""

    def __init__(self, youtube_api, sheets_manager: GoogleSheetsManager, log_file=None):
        """
        GUI 초기화

        Args:
            youtube_api: YouTubeSearchAPI 인스턴스
            sheets_manager: GoogleSheetsManager 인스턴스
            log_file: 로그 파일 경로 (선택사항)
        """
        self.youtube_api = youtube_api
        self.sheets_manager = sheets_manager
        self.log_file = log_file
        self.root = tk.Tk()
        self.root.title("YouTube 검색 → 구글 시트")
        self.root.geometry("1400x750")

        self.is_searching = False
        self.interrupt_requested = False  # 작업 중단 플래그

        # UI 구성 요소를 먼저 초기화 (로그 텍스트를 먼저 만들어야 _log를 사용 가능)
        self._setup_ui()

        # ESC 키 바인딩 - 작업 중단
        self.root.bind('<Escape>', self._on_escape_pressed)

    def _setup_ui(self):
        """UI 구성"""
        # 메인 컨테이너 프레임 생성
        container = ttk.Frame(self.root)
        container.pack(fill=tk.BOTH, expand=True)

        # 캔버스 생성 (스크롤 가능 영역)
        canvas = tk.Canvas(container)
        scrollbar_y = ttk.Scrollbar(container, orient="vertical", command=canvas.yview)
        scrollbar_x = ttk.Scrollbar(container, orient="horizontal", command=canvas.xview)

        # 스크롤 가능한 메인 프레임
        main_frame = ttk.Frame(canvas, padding="10")

        # 캔버스에 메인 프레임 추가
        canvas_frame = canvas.create_window((0, 0), window=main_frame, anchor="nw")

        # 스크롤바 설정
        canvas.configure(yscrollcommand=scrollbar_y.set, xscrollcommand=scrollbar_x.set)

        # 레이아웃
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar_y.pack(side=tk.RIGHT, fill=tk.Y)
        scrollbar_x.pack(side=tk.BOTTOM, fill=tk.X)

        # 캔버스 스크롤 영역 업데이트 함수
        def update_scroll_region(event=None):
            canvas.configure(scrollregion=canvas.bbox("all"))

        main_frame.bind("<Configure>", update_scroll_region)

        # 마우스휠 스크롤 이벤트 바인딩
        def _on_mousewheel(event):
            canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

        def _bind_mousewheel(event):
            canvas.bind_all("<MouseWheel>", _on_mousewheel)

        def _unbind_mousewheel(event):
            canvas.unbind_all("<MouseWheel>")

        # 마우스가 윈도우 안에 있을 때만 스크롤 활성화
        canvas.bind("<Enter>", _bind_mousewheel)
        canvas.bind("<Leave>", _unbind_mousewheel)

        # 왼쪽 컬럼 프레임
        left_frame = ttk.Frame(main_frame)
        left_frame.grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S), padx=(0, 5))

        # 오른쪽 컬럼 프레임
        right_frame = ttk.Frame(main_frame)
        right_frame.grid(row=0, column=1, sticky=(tk.W, tk.E, tk.N, tk.S), padx=(5, 0))

        # 로그 영역은 하단에 전체 너비로
        log_frame = ttk.Frame(main_frame)
        log_frame.grid(row=1, column=0, columnspan=2, sticky=(tk.W, tk.E, tk.N, tk.S), pady=(10, 0))

        # === 왼쪽 컬럼: 검색 및 재생목록 ===
        left_row = 0

        # === 구글 시트 연결 섹션 ===
        sheet_section = ttk.LabelFrame(left_frame, text="구글 시트 설정", padding="10")
        sheet_section.grid(row=left_row, column=0, columnspan=3, sticky=(tk.W, tk.E), pady=(0, 10))
        left_row += 1

        # 시트 이름 선택
        ttk.Label(sheet_section, text="시트 탭:").grid(row=0, column=0, sticky=tk.W, pady=5, padx=(0, 5))
        self.sheet_name_var = tk.StringVar(value='키워드 검색결과')
        self.sheet_name_combo = ttk.Combobox(
            sheet_section,
            textvariable=self.sheet_name_var,
            width=30,
            state='readonly'
        )
        self.sheet_name_combo.grid(row=0, column=1, sticky=tk.W, pady=5, padx=(0, 5))

        # 시트 새로고침 버튼
        refresh_button = ttk.Button(
            sheet_section, text="새로고침", command=self._refresh_sheet_list, width=10
        )
        refresh_button.grid(row=0, column=2, sticky=tk.W, pady=5, padx=(0, 5))

        # 연결 테스트 버튼
        test_button = ttk.Button(
            sheet_section, text="연결 테스트", command=self._test_connection, width=10
        )
        test_button.grid(row=0, column=3, sticky=tk.W, pady=5)

        # === 검색 옵션 섹션 ===
        search_section = ttk.LabelFrame(left_frame, text="검색 옵션", padding="10")
        search_section.grid(row=left_row, column=0, columnspan=3, sticky=(tk.W, tk.E), pady=(0, 10))
        left_row += 1

        # 검색 키워드 입력
        ttk.Label(search_section, text="검색 키워드:").grid(row=0, column=0, sticky=tk.W, pady=5)
        self.keyword_entry = ttk.Entry(search_section, width=50)
        self.keyword_entry.grid(row=0, column=1, columnspan=2, sticky=(tk.W, tk.E), pady=5)

        # 최대 결과 수
        ttk.Label(search_section, text="최대 결과 수:").grid(row=1, column=0, sticky=tk.W, pady=5)
        self.max_results_var = tk.IntVar(value=50)
        # 값 변경 시 자동으로 쿼터 업데이트 (실시간)
        self.max_results_var.trace_add('write', lambda *args: self._update_search_quota_display())
        max_results_spinbox = ttk.Spinbox(
            search_section, from_=1, to=50, textvariable=self.max_results_var, width=10
        )
        max_results_spinbox.grid(row=1, column=1, sticky=tk.W, pady=5)

        # 정렬 기준
        ttk.Label(search_section, text="정렬 기준:").grid(row=2, column=0, sticky=tk.W, pady=5)
        self.order_var = tk.StringVar(value='viewCount')

        # 한글 라벨과 실제 값을 매핑
        order_options = {
            '관련성': 'relevance',
            '최신순': 'date',
            '조회수': 'viewCount',
            '평점': 'rating',
            '제목': 'title'
        }
        self.order_options = order_options

        order_combo = ttk.Combobox(
            search_section,
            textvariable=self.order_var,
            values=list(order_options.keys()),
            state='readonly',
            width=20
        )
        order_combo.current(2)  # 조회수가 기본값
        order_combo.grid(row=2, column=1, sticky=tk.W, pady=5)

        # 정렬 순서 (오름차순/내림차순)
        ttk.Label(search_section, text="정렬 순서:").grid(row=2, column=2, sticky=tk.W, pady=5, padx=(10, 0))
        self.order_direction_var = tk.StringVar(value='desc')
        order_direction_frame = ttk.Frame(search_section)
        order_direction_frame.grid(row=2, column=3, sticky=tk.W, pady=5)

        ttk.Radiobutton(
            order_direction_frame, text="내림차순", variable=self.order_direction_var, value='desc'
        ).pack(side=tk.LEFT, padx=(0, 5))
        ttk.Radiobutton(
            order_direction_frame, text="오름차순", variable=self.order_direction_var, value='asc'
        ).pack(side=tk.LEFT)

        # 비디오 길이 필터
        ttk.Label(search_section, text="영상 길이:").grid(row=3, column=0, sticky=tk.W, pady=5)
        self.duration_var = tk.StringVar(value='쇼츠')

        # 한글 라벨과 실제 값을 매핑
        duration_options = {
            '전체': 'any',
            '쇼츠 (3분 이하)': 'short',
            '중간 (3-20분)': 'medium',
            '긴 영상 (20분 이상)': 'long'
        }
        self.duration_options = duration_options

        duration_combo = ttk.Combobox(
            search_section,
            textvariable=self.duration_var,
            values=list(duration_options.keys()),
            state='readonly',
            width=20
        )
        duration_combo.current(1)  # 쇼츠가 기본값
        duration_combo.grid(row=3, column=1, sticky=tk.W, pady=5)

        search_section.columnconfigure(1, weight=1)

        # 쿼터 비용 표시 (검색)
        self.search_quota_label = ttk.Label(
            left_frame,
            text="",
            foreground="blue",
            font=("", 9)
        )
        self.search_quota_label.grid(row=left_row, column=0, columnspan=3, pady=(5, 0))
        left_row += 1
        # 초기값 설정
        self._update_search_quota_display()

        # 검색 버튼
        self.search_button = ttk.Button(
            left_frame, text="검색 및 시트에 추가", command=self._on_search_click
        )
        self.search_button.grid(row=left_row, column=0, columnspan=3, pady=10)
        left_row += 1

        # === 재생목록 섹션 ===
        playlist_section = ttk.LabelFrame(left_frame, text="재생목록에서 영상 추출", padding="10")
        playlist_section.grid(row=left_row, column=0, columnspan=3, sticky=(tk.W, tk.E), pady=(0, 10))
        left_row += 1

        # 재생목록 선택
        ttk.Label(playlist_section, text="재생목록:").grid(row=0, column=0, sticky=tk.W, pady=5, padx=(0, 5))
        self.playlist_var = tk.StringVar()
        self.playlist_combo = ttk.Combobox(
            playlist_section,
            textvariable=self.playlist_var,
            width=50,
            state='readonly'
        )
        self.playlist_combo.grid(row=0, column=1, columnspan=2, sticky=tk.W, pady=5, padx=(0, 5))

        # 재생목록 새로고침 버튼
        refresh_playlist_button = ttk.Button(
            playlist_section, text="새로고침", command=self._refresh_playlist_list, width=10
        )
        refresh_playlist_button.grid(row=0, column=3, sticky=tk.W, pady=5)

        # 저장 시트 선택
        ttk.Label(playlist_section, text="저장 시트:").grid(row=1, column=0, sticky=tk.W, pady=5, padx=(0, 5))
        self.playlist_sheet_var = tk.StringVar(value='유튜브 재생목록')
        self.playlist_sheet_combo = ttk.Combobox(
            playlist_section,
            textvariable=self.playlist_sheet_var,
            width=30,
            state='readonly'
        )
        self.playlist_sheet_combo.grid(row=1, column=1, sticky=tk.W, pady=5, padx=(0, 5))

        # 저장 시트 새로고침 버튼
        refresh_playlist_sheet_button = ttk.Button(
            playlist_section, text="새로고침", command=self._refresh_playlist_sheet_list, width=10
        )
        refresh_playlist_sheet_button.grid(row=1, column=2, sticky=tk.W, pady=5)

        # 영상 갯수 옵션
        ttk.Label(playlist_section, text="영상 갯수:").grid(row=2, column=0, sticky=tk.W, pady=5, padx=(0, 5))

        video_count_frame = ttk.Frame(playlist_section)
        video_count_frame.grid(row=2, column=1, columnspan=2, sticky=tk.W, pady=5)

        self.playlist_video_count_var = tk.IntVar(value=50)
        # 값 변경 시 자동으로 쿼터 업데이트 (실시간)
        self.playlist_video_count_var.trace_add('write', lambda *args: self._update_playlist_quota_display())
        self.playlist_video_count_spinbox = ttk.Spinbox(
            video_count_frame, from_=1, to=500, textvariable=self.playlist_video_count_var, width=10
        )
        self.playlist_video_count_spinbox.pack(side=tk.LEFT, padx=(0, 10))

        self.playlist_all_videos_var = tk.BooleanVar(value=True)
        # 체크박스 변경 시에도 자동 업데이트
        self.playlist_all_videos_var.trace_add('write', lambda *args: self._toggle_playlist_video_count())
        self.playlist_all_videos_checkbox = ttk.Checkbutton(
            video_count_frame,
            text="전체 영상",
            variable=self.playlist_all_videos_var
        )
        self.playlist_all_videos_checkbox.pack(side=tk.LEFT)

        ttk.Label(video_count_frame, text="(최신 추가순)", font=('Arial', 8)).pack(side=tk.LEFT, padx=(5, 0))

        playlist_section.columnconfigure(1, weight=1)

        # 초기 상태 설정 (전체 영상 체크 시 입력창 비활성화)
        self._toggle_playlist_video_count()

        # 쿼터 비용 표시 (재생목록) - 동적으로 업데이트되도록 변수 저장
        self.playlist_quota_label = ttk.Label(
            left_frame,
            text="",
            foreground="blue",
            font=("", 9)
        )
        self.playlist_quota_label.grid(row=left_row, column=0, columnspan=3, pady=(5, 0))
        left_row += 1
        # 초기값 설정
        self._update_playlist_quota_display()

        # 재생목록 가져오기 버튼
        self.playlist_button = ttk.Button(
            left_frame, text="재생목록 영상 가져오기", command=self._on_playlist_click
        )
        self.playlist_button.grid(row=left_row, column=0, columnspan=3, pady=10)
        left_row += 1

        # === 오른쪽 컬럼: 채널 영상 추출 ===
        right_row = 0

        # === 채널 영상 추출 섹션 ===
        channel_section = ttk.LabelFrame(right_frame, text="채널 영상 추출", padding="10")
        channel_section.grid(row=right_row, column=0, columnspan=3, sticky=(tk.W, tk.E), pady=(0, 10))
        right_row += 1

        # 필터 옵션
        filter_row = 0
        filter_header = ttk.Frame(channel_section)
        filter_header.grid(row=filter_row, column=0, columnspan=4, sticky=(tk.W, tk.E), pady=(0, 5))
        ttk.Label(filter_header, text="필터:", font=('Arial', 9, 'bold')).pack(side=tk.LEFT)

        # 새로고침 버튼 추가
        ttk.Button(
            filter_header, text="새로고침", command=self._refresh_channel_filter, width=10
        ).pack(side=tk.RIGHT)
        filter_row += 1

        # 가져왔는지 여부 필터 (체크박스)
        fetch_filter_frame = ttk.LabelFrame(channel_section, text="가져왔는지 여부", padding="5")
        fetch_filter_frame.grid(row=filter_row, column=0, columnspan=2, sticky=(tk.W, tk.E), pady=5)

        self.channel_fetched_yes_var = tk.BooleanVar(value=False)
        self.channel_fetched_no_var = tk.BooleanVar(value=True)  # 기본값: X (안 가져온 채널)

        ttk.Checkbutton(
            fetch_filter_frame,
            text="O (가져온 채널)",
            variable=self.channel_fetched_yes_var,
            command=self._on_fetched_yes_changed
        ).pack(side=tk.LEFT, padx=5)

        ttk.Checkbutton(
            fetch_filter_frame,
            text="X (안 가져온 채널)",
            variable=self.channel_fetched_no_var,
            command=self._on_fetched_no_changed
        ).pack(side=tk.LEFT, padx=5)
        filter_row += 1

        # 벤치마킹 채널여부 필터 (체크박스)
        benchmark_filter_frame = ttk.LabelFrame(channel_section, text="벤치마킹 채널여부", padding="5")
        benchmark_filter_frame.grid(row=filter_row, column=0, columnspan=2, sticky="we", pady=5)

        self.channel_benchmark_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            benchmark_filter_frame,
            text="벤치마킹 채널만 (비어있지 않은 행)",
            variable=self.channel_benchmark_var,
            command=self._refresh_channel_filter
        ).pack(side=tk.LEFT, padx=5)
        filter_row += 1

        # 분야1 필터 (체크박스)
        category1_frame = ttk.LabelFrame(channel_section, text="분야1 (합집합)", padding="5")
        category1_frame.grid(row=filter_row, column=0, columnspan=2, sticky=(tk.W, tk.E), pady=5)

        # 분야1 전체선택 체크박스
        category1_header = ttk.Frame(category1_frame)
        category1_header.pack(fill=tk.X, pady=(0, 5))
        self.category1_select_all_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            category1_header,
            text="전체선택",
            variable=self.category1_select_all_var,
            command=self._toggle_category1_all
        ).pack(side=tk.LEFT)

        self.category1_canvas = tk.Canvas(category1_frame, height=80)
        category1_scrollbar = ttk.Scrollbar(category1_frame, orient="vertical", command=self.category1_canvas.yview)
        self.category1_checkbox_frame = ttk.Frame(self.category1_canvas)

        self.category1_canvas.create_window((0, 0), window=self.category1_checkbox_frame, anchor="nw")
        self.category1_canvas.configure(yscrollcommand=category1_scrollbar.set)

        self.category1_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        category1_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        self.category1_vars = {}  # {카테고리명: BooleanVar}
        filter_row += 1

        # 분야2 필터 (체크박스)
        category2_frame = ttk.LabelFrame(channel_section, text="분야2 (합집합)", padding="5")
        category2_frame.grid(row=filter_row, column=0, columnspan=2, sticky=(tk.W, tk.E), pady=5)

        # 분야2 전체선택 체크박스
        category2_header = ttk.Frame(category2_frame)
        category2_header.pack(fill=tk.X, pady=(0, 5))
        self.category2_select_all_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            category2_header,
            text="전체선택",
            variable=self.category2_select_all_var,
            command=self._toggle_category2_all
        ).pack(side=tk.LEFT)

        self.category2_canvas = tk.Canvas(category2_frame, height=80)
        category2_scrollbar = ttk.Scrollbar(category2_frame, orient="vertical", command=self.category2_canvas.yview)
        self.category2_checkbox_frame = ttk.Frame(self.category2_canvas)

        self.category2_canvas.create_window((0, 0), window=self.category2_checkbox_frame, anchor="nw")
        self.category2_canvas.configure(yscrollcommand=category2_scrollbar.set)

        self.category2_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        category2_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        self.category2_vars = {}  # {카테고리명: BooleanVar}
        filter_row += 1

        # 채널 갯수 표시
        self.channel_count_label = ttk.Label(channel_section, text="조건 부합 채널: 0개", font=('Arial', 9))
        self.channel_count_label.grid(row=filter_row, column=0, columnspan=2, sticky=tk.W, pady=5)
        filter_row += 1

        ttk.Separator(channel_section, orient='horizontal').grid(row=filter_row, column=0, columnspan=4, sticky=(tk.W, tk.E), pady=5)
        filter_row += 1

        # 채널 선택 (체크박스 리스트)
        channel_list_header = ttk.Frame(channel_section)
        channel_list_header.grid(row=filter_row, column=0, columnspan=4, sticky=(tk.W, tk.E), pady=(0, 5))
        ttk.Label(channel_list_header, text="채널 선택 (복수 선택 가능):", font=('Arial', 9, 'bold')).pack(side=tk.LEFT)

        # 채널 전체선택 체크박스
        self.channel_select_all_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            channel_list_header,
            text="전체선택",
            variable=self.channel_select_all_var,
            command=self._toggle_channel_all
        ).pack(side=tk.LEFT, padx=(10, 0))
        filter_row += 1

        # 채널 리스트박스를 스크롤 가능한 체크박스 리스트로 변경
        channel_list_container = ttk.Frame(channel_section)
        channel_list_container.grid(row=filter_row, column=0, columnspan=4, sticky=(tk.W, tk.E, tk.N, tk.S), pady=5)

        self.channel_canvas = tk.Canvas(channel_list_container, height=150)
        channel_scrollbar = ttk.Scrollbar(channel_list_container, orient="vertical", command=self.channel_canvas.yview)
        self.channel_checkbox_frame = ttk.Frame(self.channel_canvas)

        self.channel_canvas.create_window((0, 0), window=self.channel_checkbox_frame, anchor="nw")
        self.channel_canvas.configure(yscrollcommand=channel_scrollbar.set)

        self.channel_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        channel_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        self.channel_checkboxes = {}  # {display_text: (BooleanVar, channel_info)}
        filter_row += 1

        # 저장 시트는 '영상 리스트' 고정
        ttk.Label(channel_section, text="저장 시트:").grid(row=filter_row, column=0, sticky=tk.W, pady=5, padx=(0, 5))
        ttk.Label(channel_section, text="영상 리스트", font=('Arial', 10, 'bold')).grid(row=filter_row, column=1, sticky=tk.W, pady=5)
        filter_row += 1

        # 영상 갯수 옵션
        ttk.Label(channel_section, text="영상 갯수:").grid(row=filter_row, column=0, sticky=tk.W, pady=5, padx=(0, 5))

        channel_count_frame = ttk.Frame(channel_section)
        channel_count_frame.grid(row=filter_row, column=1, columnspan=2, sticky=tk.W, pady=5)

        self.channel_video_count_var = tk.IntVar(value=200)
        # 값 변경 시 자동으로 쿼터 업데이트 (실시간)
        self.channel_video_count_var.trace_add('write', lambda *args: self._update_channel_quota_display())
        self.channel_video_count_spinbox = ttk.Spinbox(
            channel_count_frame, from_=1, to=1000, textvariable=self.channel_video_count_var, width=10
        )
        self.channel_video_count_spinbox.pack(side=tk.LEFT, padx=(0, 10))

        self.channel_all_videos_var = tk.BooleanVar(value=False)
        # 체크박스 변경 시에도 자동 업데이트
        self.channel_all_videos_var.trace_add('write', lambda *args: self._toggle_channel_video_count())
        self.channel_all_videos_checkbox = ttk.Checkbutton(
            channel_count_frame,
            text="전체 영상",
            variable=self.channel_all_videos_var
        )
        self.channel_all_videos_checkbox.pack(side=tk.LEFT)
        filter_row += 1

        # 정렬 옵션
        ttk.Label(channel_section, text="정렬 기준:").grid(row=filter_row, column=0, sticky=tk.W, pady=5, padx=(0, 5))

        channel_order_frame = ttk.Frame(channel_section)
        channel_order_frame.grid(row=filter_row, column=1, columnspan=2, sticky=tk.W, pady=5)

        self.channel_order_var = tk.StringVar(value='date')
        channel_order_options = {
            '최신순': 'date',
            '조회수순': 'viewCount',
            '평점순': 'rating'
        }
        self.channel_order_options = channel_order_options

        channel_order_combo = ttk.Combobox(
            channel_order_frame,
            textvariable=self.channel_order_var,
            values=list(channel_order_options.keys()),
            state='readonly',
            width=15
        )
        channel_order_combo.current(0)  # 최신순이 기본값
        channel_order_combo.pack(side=tk.LEFT, padx=(0, 10))

        # 정렬 순서
        self.channel_order_direction_var = tk.StringVar(value='desc')
        ttk.Radiobutton(
            channel_order_frame, text="내림차순", variable=self.channel_order_direction_var, value='desc'
        ).pack(side=tk.LEFT, padx=(0, 5))
        ttk.Radiobutton(
            channel_order_frame, text="오름차순", variable=self.channel_order_direction_var, value='asc'
        ).pack(side=tk.LEFT)

        channel_section.columnconfigure(1, weight=1)

        # 초기 상태 설정
        self._toggle_channel_video_count()

        # 쿼터 비용 표시 (채널) - 동적으로 업데이트되도록 변수 저장
        self.channel_quota_label = ttk.Label(
            right_frame,
            text="",
            foreground="blue",
            font=("", 9)
        )
        self.channel_quota_label.grid(row=right_row, column=0, columnspan=3, pady=(5, 0))
        right_row += 1
        # 초기값 설정
        self._update_channel_quota_display()

        # 채널 영상 가져오기 버튼 및 설명
        self.channel_button = ttk.Button(
            right_frame, text="채널 영상 가져오기", command=self._on_channel_click
        )
        self.channel_button.grid(row=right_row, column=0, columnspan=3, pady=10)
        right_row += 1

        # 채널 영상 가져오기 대상 채널 개수 표시
        self.channel_fetch_count_label = ttk.Label(
            right_frame,
            text="대상 채널: 계산 중...",
            font=("", 9),
            foreground="blue"
        )
        self.channel_fetch_count_label.grid(row=right_row, column=0, columnspan=3, pady=(0, 5))
        right_row += 1

        # 채널 영상 가져오기 설명
        channel_info_text = "※ 선택한 분야/채널의 영상을 가져옵니다.\n   필터 조건에 따라 대상 채널이 결정됩니다."
        ttk.Label(right_frame, text=channel_info_text, font=("", 8), foreground="gray").grid(
            row=right_row, column=0, columnspan=3, pady=(0, 10)
        )
        right_row += 1

        # --- 채널 정보 업데이트 & 신규 영상 업데이트 통합 섹션 ---
        update_section = ttk.LabelFrame(right_frame, text="채널 정보 & 신규 영상 업데이트", padding=10)
        update_section.grid(row=right_row, column=0, columnspan=3, pady=10, sticky="we")
        right_row += 1

        update_row = 0

        # 신규 영상 업데이트 옵션
        self.update_new_videos_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            update_section,
            text="신규 영상 함께 업데이트",
            variable=self.update_new_videos_var,
            command=self._on_update_new_videos_toggle
        ).grid(row=update_row, column=0, sticky=tk.W, pady=5)
        update_row += 1

        # 일수 필터 체크박스
        self.use_days_filter = tk.BooleanVar(value=True)
        self.days_filter_check = ttk.Checkbutton(
            update_section, text="수집날짜 필터 사용", variable=self.use_days_filter,
            command=self._on_days_filter_toggle
        )
        self.days_filter_check.grid(row=update_row, column=0, sticky=tk.W, pady=5, padx=(20, 0))
        update_row += 1

        # 일수 입력
        days_input_frame = ttk.Frame(update_section)
        days_input_frame.grid(row=update_row, column=0, sticky=tk.W, pady=5, padx=(40, 0))
        update_row += 1

        ttk.Label(days_input_frame, text="수집 경과 일수:").grid(row=0, column=0, sticky=tk.W)
        self.days_threshold_entry = ttk.Entry(days_input_frame, width=10)
        self.days_threshold_entry.insert(0, "7")  # 기본값 7일
        self.days_threshold_entry.grid(row=0, column=1, padx=5)
        ttk.Label(days_input_frame, text="일 이상 경과한 채널").grid(row=0, column=2, sticky=tk.W)

        # 일수 입력 변경 시 실시간 업데이트
        self.days_threshold_entry.bind('<KeyRelease>', lambda e: self._update_channel_update_count())

        # 초기 비활성화
        self.days_filter_check.config(state='disabled')
        self.days_threshold_entry.config(state='disabled')

        # 채널 정보 업데이트 버튼
        self.update_channel_info_button = ttk.Button(
            update_section, text="채널 정보 업데이트",
            command=self._on_update_channel_info_click
        )
        self.update_channel_info_button.grid(row=update_row, column=0, pady=10)
        update_row += 1

        # 채널 정보 업데이트 대상 채널 개수 표시
        self.channel_update_count_label = ttk.Label(
            update_section,
            text="대상 채널: 계산 중...",
            font=("", 9),
            foreground="blue"
        )
        self.channel_update_count_label.grid(row=update_row, column=0, pady=(0, 5))
        update_row += 1

        # 채널 정보 업데이트 설명
        update_info_text = (
            "※ 필터 조건에 부합하는 채널의 정보를 업데이트합니다.\n"
            "   - 기본: 구독자수, 총 조회수, 영상 평균 조회수 등 갱신\n"
            "   - 신규 영상 함께 업데이트 체크 시:\n"
            "     · 가져왔는지 여부가 'O'인 채널의 신규 영상 추가\n"
            "     · 채널 정보 및 영상 리스트의 채널 정보 갱신\n"
            "     · 수집날짜 필터 사용 시 경과일 조건 충족 채널만 대상"
        )
        ttk.Label(update_section, text=update_info_text, font=("", 8), foreground="gray", justify=tk.LEFT).grid(
            row=update_row, column=0, pady=5, sticky=tk.W
        )
        update_row += 1

        # 진행 상황 표시 (하단 통합)
        log_row = 0
        self.progress_label = ttk.Label(log_frame, text="")
        self.progress_label.grid(row=log_row, column=0, columnspan=2, pady=5)
        log_row += 1

        self.progress_bar = ttk.Progressbar(log_frame, mode='indeterminate')
        self.progress_bar.grid(row=log_row, column=0, columnspan=2, sticky=(tk.W, tk.E), pady=5)
        log_row += 1

        # 로그 영역
        ttk.Label(log_frame, text="로그:").grid(row=log_row, column=0, sticky=tk.W, pady=5)
        log_row += 1
        self.log_text = scrolledtext.ScrolledText(log_frame, height=36, width=120)
        self.log_text.grid(row=log_row, column=0, columnspan=2, sticky=(tk.W, tk.E, tk.N, tk.S), pady=5)

        # 그리드 가중치 설정
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)
        main_frame.columnconfigure(0, weight=1)
        main_frame.columnconfigure(1, weight=1)
        main_frame.rowconfigure(1, weight=1)
        log_frame.columnconfigure(0, weight=1)
        log_frame.rowconfigure(log_row, weight=1)

        # UI 구성 완료 후 시트 목록 로드
        self.root.after(100, self._refresh_sheet_list)
        self.root.after(200, self._refresh_playlist_list)
        self.root.after(300, self._refresh_playlist_sheet_list)
        self.root.after(400, self._refresh_channel_filter)
        self.root.after(500, self._update_new_videos_count)

    def _toggle_category1_all(self):
        """분야1 전체선택 토글"""
        select_all = self.category1_select_all_var.get()
        for var in self.category1_vars.values():
            var.set(select_all)
        self._refresh_channel_list()

    def _toggle_category2_all(self):
        """분야2 전체선택 토글"""
        select_all = self.category2_select_all_var.get()
        for var in self.category2_vars.values():
            var.set(select_all)
        self._refresh_channel_list()

    def _toggle_channel_all(self):
        """채널 전체선택 토글"""
        select_all = self.channel_select_all_var.get()
        for var, _ in self.channel_checkboxes.values():
            var.set(select_all)
        # 전체선택/해제 후 쿼터 디스플레이 업데이트
        self._update_channel_quota_display()

    def _log(self, message: str):
        """로그 메시지 추가 (GUI, CMD, 파일)"""
        timestamp = datetime.now().strftime('%H:%M:%S')
        log_line = f"[{timestamp}] {message}"

        # CMD에 실시간 출력 (cp949 인코딩 오류 방지)
        try:
            print(log_line)
        except UnicodeEncodeError:
            # 특수 문자를 ? 로 변환하여 출력
            print(log_line.encode('cp949', errors='replace').decode('cp949'))

        # GUI에 로그 표시
        self.log_text.insert(tk.END, log_line + "\n")
        self.log_text.see(tk.END)
        self.root.update()

        # 파일에도 로그 기록
        if self.log_file:
            try:
                with open(self.log_file, 'a', encoding='utf-8') as f:
                    f.write(log_line + "\n")
            except Exception as e:
                try:
                    print(f"로그 파일 쓰기 실패: {e}")
                except UnicodeEncodeError:
                    print("로그 파일 쓰기 실패")

    def _show_notification(self, title: str, message: str):
        """오른쪽 하단에 알림 팝업 표시"""
        # 알림 창 생성
        notification = tk.Toplevel(self.root)
        notification.title(title)
        notification.overrideredirect(True)  # 타이틀바 제거

        # 화면 크기 가져오기
        screen_width = notification.winfo_screenwidth()
        screen_height = notification.winfo_screenheight()

        # 알림 창 크기
        notification_width = 300
        notification_height = 100

        # 오른쪽 하단 위치 계산
        x = screen_width - notification_width - 20
        y = screen_height - notification_height - 60

        notification.geometry(f"{notification_width}x{notification_height}+{x}+{y}")
        notification.configure(bg='#2d2d2d')

        # 내용 프레임
        content_frame = tk.Frame(notification, bg='#2d2d2d', padx=15, pady=15)
        content_frame.pack(fill='both', expand=True)

        # 제목
        title_label = tk.Label(
            content_frame, text=title, font=('Arial', 12, 'bold'),
            bg='#2d2d2d', fg='#4CAF50'
        )
        title_label.pack(anchor='w')

        # 메시지
        message_label = tk.Label(
            content_frame, text=message, font=('Arial', 9),
            bg='#2d2d2d', fg='white', wraplength=270
        )
        message_label.pack(anchor='w', pady=(5, 0))

        # 3초 후 자동 닫기
        notification.after(3000, notification.destroy)

        # 클릭하면 닫기
        notification.bind('<Button-1>', lambda e: notification.destroy())

    def _refresh_sheet_list(self):
        """시트 탭 목록 새로고침"""
        try:
            sheet_names = self.sheets_manager.get_all_sheet_names()
            self.sheet_name_combo['values'] = sheet_names

            # 기본값 설정
            if '키워드 검색결과' in sheet_names:
                self.sheet_name_var.set('키워드 검색결과')
            elif sheet_names:
                self.sheet_name_var.set(sheet_names[0])

            self._log(f"시트 목록 로드 완료: {len(sheet_names)}개")
        except Exception as e:
            self._log(f"시트 목록 로드 실패: {str(e)}")
            messagebox.showerror("오류", f"시트 목록을 불러올 수 없습니다.\n{str(e)}")

    def _test_connection(self):
        """구글 시트 연결 테스트"""
        try:
            success, message = self.sheets_manager.test_connection()
            if success:
                self._log(message)
                messagebox.showinfo("연결 테스트", message)
            else:
                self._log(message)
                messagebox.showerror("연결 테스트 실패", message)
        except Exception as e:
            error_msg = f"연결 테스트 중 오류 발생: {str(e)}"
            self._log(error_msg)
            messagebox.showerror("오류", error_msg)

    def _refresh_playlist_list(self):
        """재생목록 목록 새로고침"""
        try:
            playlist_data = self.sheets_manager.get_playlist_data('재생목록ID')

            # 콤보박스에 표시할 항목 생성 (재생목록 이름 - 재생목록 ID)
            self.playlist_items = []
            display_items = []

            for item in playlist_data:
                playlist_name = item['playlist_name'] if item['playlist_name'] else '(이름 없음)'
                display_text = f"{playlist_name} - {item['playlist_id']}"
                display_items.append(display_text)
                self.playlist_items.append(item)

            self.playlist_combo['values'] = display_items

            if display_items:
                self.playlist_var.set(display_items[0])

            self._log(f"재생목록 목록 로드 완료: {len(display_items)}개")
        except Exception as e:
            self._log(f"재생목록 목록 로드 실패: {str(e)}")

    def _refresh_playlist_sheet_list(self):
        """재생목록 저장 시트 목록 새로고침"""
        try:
            sheet_names = self.sheets_manager.get_all_sheet_names()
            self.playlist_sheet_combo['values'] = sheet_names

            # 기본값 설정
            if '유튜브 재생목록' in sheet_names:
                self.playlist_sheet_var.set('유튜브 재생목록')
            elif sheet_names:
                self.playlist_sheet_var.set(sheet_names[0])

        except Exception as e:
            self._log(f"저장 시트 목록 로드 실패: {str(e)}")

    def _toggle_playlist_video_count(self):
        """전체 영상 체크박스 토글 시 영상 갯수 입력창 활성화/비활성화"""
        if self.playlist_all_videos_var.get():
            self.playlist_video_count_spinbox.config(state='disabled')
        else:
            self.playlist_video_count_spinbox.config(state='normal')
        # 쿼터 표시 업데이트
        self._update_playlist_quota_display()

    def _toggle_channel_video_count(self):
        """전체 영상 체크박스 토글 시 영상 갯수 입력창 활성화/비활성화"""
        if self.channel_all_videos_var.get():
            self.channel_video_count_spinbox.config(state='disabled')
        else:
            self.channel_video_count_spinbox.config(state='normal')
        # 쿼터 표시 업데이트
        self._update_channel_quota_display()

    def _refresh_channel_filter(self):
        """필터 옵션 새로고침 (카테고리 목록 다시 불러오기)"""
        try:
            self._log("필터 옵션 새로고침 중...")
            category1_list, category2_list = self.sheets_manager.get_all_categories('채널 리스트')

            # 기존 체크박스 모두 제거
            for widget in self.category1_checkbox_frame.winfo_children():
                widget.destroy()
            for widget in self.category2_checkbox_frame.winfo_children():
                widget.destroy()

            self.category1_vars.clear()
            self.category2_vars.clear()

            # 분야1 체크박스 생성
            for idx, cat in enumerate(category1_list):
                var = tk.BooleanVar(value=False)
                self.category1_vars[cat] = var
                cb = ttk.Checkbutton(
                    self.category1_checkbox_frame,
                    text=cat,
                    variable=var,
                    command=self._refresh_channel_list
                )
                cb.grid(row=idx // 3, column=idx % 3, sticky=tk.W, padx=5, pady=2)

            # 분야2 체크박스 생성
            for idx, cat in enumerate(category2_list):
                var = tk.BooleanVar(value=False)
                self.category2_vars[cat] = var
                cb = ttk.Checkbutton(
                    self.category2_checkbox_frame,
                    text=cat,
                    variable=var,
                    command=self._refresh_channel_list
                )
                cb.grid(row=idx // 3, column=idx % 3, sticky=tk.W, padx=5, pady=2)

            # 캔버스 스크롤 영역 업데이트
            self.category1_checkbox_frame.update_idletasks()
            self.category1_canvas.configure(scrollregion=self.category1_canvas.bbox("all"))

            self.category2_checkbox_frame.update_idletasks()
            self.category2_canvas.configure(scrollregion=self.category2_canvas.bbox("all"))

            self._log(f"필터 옵션 로드 완료: 분야1 {len(category1_list)}개, 분야2 {len(category2_list)}개")

            # 채널 리스트도 새로고침
            self._refresh_channel_list()

        except Exception as e:
            self._log(f"필터 옵션 로드 실패: {str(e)}")

    def _on_fetched_yes_changed(self):
        """'O (가져온 채널)' 체크박스 변경 시"""
        if self.channel_fetched_yes_var.get():
            # O를 체크하면 X를 자동으로 해제
            self.channel_fetched_no_var.set(False)
        self._refresh_channel_list()

    def _on_fetched_no_changed(self):
        """'X (안 가져온 채널)' 체크박스 변경 시"""
        if self.channel_fetched_no_var.get():
            # X를 체크하면 O를 자동으로 해제
            self.channel_fetched_yes_var.set(False)
        self._refresh_channel_list()

    def _get_filtered_channels(self):
        """현재 필터 조건에 맞는 채널 리스트 반환"""
        # 가져왔는지 여부 필터 (합집합)
        filter_fetched_list = []
        if self.channel_fetched_yes_var.get():
            filter_fetched_list.append(True)
        if self.channel_fetched_no_var.get():
            filter_fetched_list.append(False)

        # 둘 다 선택 안 했으면 전체 표시
        filter_fetched = filter_fetched_list if filter_fetched_list else None

        # 분야1 필터 (합집합)
        filter_category1 = [cat for cat, var in self.category1_vars.items() if var.get()]
        if not filter_category1:
            filter_category1 = None

        # 분야2 필터 (합집합)
        filter_category2 = [cat for cat, var in self.category2_vars.items() if var.get()]
        if not filter_category2:
            filter_category2 = None

        # 벤치마킹 채널 필터 추가
        filter_benchmark = self.channel_benchmark_var.get()

        # 채널 데이터 가져오기 (필터 적용)
        channel_data = self.sheets_manager.get_channel_data(
            '채널 리스트',
            filter_fetched=filter_fetched,
            filter_category1=filter_category1,
            filter_category2=filter_category2
        )

        # 벤치마킹 채널 필터 적용 (별도 처리)
        if filter_benchmark:
            channel_data = [
                ch for ch in channel_data
                if ch.get('benchmark_channel', '').strip()  # 벤치마킹 채널여부가 비어있지 않은 것만
            ]

        return channel_data

    def _refresh_channel_list(self):
        """채널 목록 새로고침 (필터 적용)"""
        try:
            # 필터된 채널 데이터 가져오기
            channel_data = self._get_filtered_channels()

            # 기존 체크박스 모두 제거
            for widget in self.channel_checkbox_frame.winfo_children():
                widget.destroy()

            self.channel_checkboxes.clear()

            # 채널 체크박스 생성
            # 형식: 채널명 [가져옴] (수집날짜) | 분야1/분야2 | 구독자수
            for idx, item in enumerate(channel_data):
                channel_name = item['channel_name'] if item['channel_name'] else '(이름 없음)'
                fetched_mark = '[가져옴]' if item['fetched'] else ''

                # 수집날짜 표시 (숫자 형식이면 날짜로 변환)
                collection_date_display = ''
                if item['collection_date']:
                    parsed_date = self.sheets_manager.parse_korean_datetime(item['collection_date'])
                    if parsed_date:
                        collection_date_display = f"({parsed_date.strftime('%Y-%m-%d')})"
                    else:
                        collection_date_display = f"({item['collection_date']})"

                categories = f"{item['category1']}"
                if item['category2']:
                    categories += f" / {item['category2']}"
                subscriber = f"구독자 {item['subscriber_count']}" if item['subscriber_count'] else ''

                display_text = f"{channel_name} {fetched_mark} {collection_date_display} | {categories} | {subscriber}".strip()

                # 체크박스 생성
                var = tk.BooleanVar(value=False)
                # 체크박스 변경 시 쿼터 디스플레이 업데이트
                var.trace_add('write', lambda *args: self._update_channel_quota_display())
                cb = ttk.Checkbutton(
                    self.channel_checkbox_frame,
                    text=display_text,
                    variable=var
                )
                cb.grid(row=idx, column=0, sticky=tk.W, padx=5, pady=2)

                self.channel_checkboxes[display_text] = (var, item)

            # 캔버스 스크롤 영역 업데이트
            self.channel_checkbox_frame.update_idletasks()
            self.channel_canvas.configure(scrollregion=self.channel_canvas.bbox("all"))

            # 채널 갯수 표시
            self.channel_count_label.config(text=f"조건 부합 채널: {len(channel_data)}개")

            # 쿼터 디스플레이 업데이트 (필터 변경으로 채널 수가 바뀌었으므로)
            self._update_channel_quota_display()

            self._log(f"채널 목록 로드 완료: {len(channel_data)}개")
        except Exception as e:
            self._log(f"채널 목록 로드 실패: {str(e)}")

    def _on_update_channel_info_click(self):
        """채널 정보 업데이트 버튼 클릭 이벤트 (신규 영상 업데이트 통합)"""
        if self.is_searching:
            messagebox.showwarning("경고", "이미 작업이 진행 중입니다.")
            return

        # 신규 영상 함께 업데이트 여부 확인
        update_new_videos = self.update_new_videos_var.get()

        # 확인 메시지 구성
        if update_new_videos:
            # 일수 필터 체크
            if self.use_days_filter.get():
                try:
                    days_threshold = int(self.days_threshold_entry.get())
                    days_info = f"\n수집날짜 필터: {days_threshold}일 이상 경과한 채널만 대상"
                except:
                    messagebox.showerror("오류", "일수는 숫자로 입력해야 합니다.")
                    return
            else:
                days_info = "\n수집날짜 필터: 사용 안 함 (전체 채널 대상)"

            confirm_msg = (
                "필터 조건에 부합하는 채널의 정보를 업데이트하고,\n"
                "가져왔는지 여부가 있는 채널의 신규 영상을 추가하시겠습니까?\n\n"
                f"{days_info}\n\n"
                "수행 작업:\n"
                "1. 채널 정보 업데이트 (구독자수, 총 조회수 등)\n"
                "2. 신규 영상 추가 (가져온 채널 대상)\n"
                "3. 영상 리스트의 채널 정보 갱신\n\n"
                "※ 영상 리스트 시트에서 정보를 추출하므로 쿼터가 소비되지 않습니다."
            )
        else:
            confirm_msg = (
                "필터 조건에 부합하는 채널의 정보를 업데이트하시겠습니까?\n\n"
                "업데이트 대상 필드: 구독자수, 채널전체 영상갯수, 채널전체 조회수,\n"
                "재생목록 이름, 채널국가, 개설일, 채널 디스크립션, 채널 핸들\n\n"
                "※ 영상 리스트 시트에서 정보를 추출하므로 쿼터가 소비되지 않습니다."
            )

        response = messagebox.askyesno("채널 정보 업데이트 확인", confirm_msg)
        if not response:
            return

        # 별도 스레드에서 처리
        if update_new_videos:
            thread = threading.Thread(
                target=self._update_channel_info_and_new_videos,
                daemon=True
            )
        else:
            thread = threading.Thread(
                target=self._update_missing_channel_info,
                daemon=True
            )
        thread.start()

    def _on_update_new_videos_toggle(self):
        """신규 영상 함께 업데이트 체크박스 토글 이벤트"""
        if self.update_new_videos_var.get():
            # 체크 시: 일수 필터 옵션 활성화
            self.days_filter_check.config(state='normal')
            if self.use_days_filter.get():
                self.days_threshold_entry.config(state='normal')
        else:
            # 체크 해제 시: 일수 필터 옵션 비활성화
            self.days_filter_check.config(state='disabled')
            self.days_threshold_entry.config(state='disabled')

        # 채널 개수 업데이트
        self._update_channel_update_count()

    def _on_days_filter_toggle(self):
        """일수 필터 체크박스 토글 이벤트"""
        if self.use_days_filter.get():
            self.days_threshold_entry.config(state='normal')
        else:
            self.days_threshold_entry.config(state='disabled')

        # 채널 개수 업데이트
        self._update_channel_update_count()

    def _update_channel_update_count(self):
        """채널 정보 업데이트 대상 채널 개수 계산 및 표시"""
        try:
            # 신규 영상 함께 업데이트 체크 여부에 따라 다른 계산
            if self.update_new_videos_var.get():
                # 신규 영상 업데이트 대상 채널 개수 계산
                self._update_new_videos_count_for_channel_update()
            else:
                # 기본 채널 정보 업데이트 대상 개수 계산
                self._update_basic_channel_update_count()
        except Exception as e:
            self._log(f"[오류] 채널 개수 계산 중 오류: {str(e)}")
            self.channel_update_count_label.config(text="대상 채널: 계산 오류")

    def _update_basic_channel_update_count(self):
        """기본 채널 정보 업데이트 대상 개수 계산"""
        try:
            # 현재 필터 조건에 맞는 채널 리스트 가져오기
            filtered_channels = self._get_filtered_channels()
            count = len(filtered_channels)
            self.channel_update_count_label.config(
                text=f"대상 채널: {count}개 (기본 정보만)"
            )
        except Exception as e:
            self._log(f"[오류] 기본 채널 개수 계산 중 오류: {str(e)}")
            self.channel_update_count_label.config(text="대상 채널: 계산 오류")

    def _update_new_videos_count_for_channel_update(self):
        """신규 영상 업데이트 포함 시 대상 채널 개수 계산"""
        from datetime import datetime

        try:
            # 필터된 채널 중 가져왔는지 여부가 'O'인 채널만
            filtered_channels = self._get_filtered_channels()
            fetched_channels = [ch for ch in filtered_channels if ch.get('fetched', '').strip()]

            if not self.use_days_filter.get():
                # 필터 미사용
                count = len(fetched_channels)
                self.channel_update_count_label.config(
                    text=f"대상 채널: {count}개 (신규영상포함)"
                )
            else:
                # 일수 필터 적용
                try:
                    days_threshold = int(self.days_threshold_entry.get())
                    if days_threshold <= 0:
                        self.channel_update_count_label.config(
                            text="대상 채널: 일수는 1 이상"
                        )
                        return
                except ValueError:
                    self.channel_update_count_label.config(
                        text="대상 채널: 일수 입력 필요"
                    )
                    return

                # 경과 일수 계산
                today = datetime.now().date()
                filtered_by_days = []
                for ch in fetched_channels:
                    collected_date_str = ch.get('collected_date', '')
                    if collected_date_str:
                        try:
                            collected_date = datetime.strptime(
                                collected_date_str.split()[0], '%Y-%m-%d'
                            ).date()
                            elapsed_days = (today - collected_date).days
                            if elapsed_days >= days_threshold:
                                filtered_by_days.append(ch)
                        except:
                            pass

                count = len(filtered_by_days)
                self.channel_update_count_label.config(
                    text=f"대상 채널: {count}개 (신규영상, {days_threshold}일이상)"
                )
        except Exception as e:
            self._log(f"[오류] 신규 영상 채널 개수 계산 중 오류: {str(e)}")
            self.channel_update_count_label.config(text="대상 채널: 계산 오류")

    def _update_new_videos_count(self):
        """신규 영상 업데이트 대상 채널 개수 계산 및 표시"""
        try:
            from datetime import datetime, timedelta

            self._log("=" * 80)
            self._log("[신규 영상 업데이트 채널 개수 계산 시작]")
            self._log("=" * 80)

            # 채널 리스트에서 가져왔는지 여부가 'O'인 채널 가져오기
            self._log("▶ 시트: '채널 리스트' 시트에서 채널 정보 읽기 중...")
            all_channels = self.sheets_manager.get_channel_data('채널 리스트')
            self._log(f"  - 전체 채널: {len(all_channels)}개")

            # 전체 채널의 'fetched' 값 분포 확인
            if all_channels:
                fetched_values = {}
                for ch in all_channels:
                    fetched_val = ch.get('fetched', '')
                    fetched_values[fetched_val] = fetched_values.get(fetched_val, 0) + 1

                self._log("  - '가져왔는지 여부' 열 값 분포:")
                for val, count in sorted(fetched_values.items()):
                    display_val = f"'{val}'" if val else "(빈값)"
                    self._log(f"    • {display_val}: {count}개")

            self._log("▶ 필터링: '가져왔는지 여부' 열이 'ㅇ'이거나 비어있지 않은 채널만 추출 중...")
            fetched_channels = []
            for ch in all_channels:
                fetched_val = ch.get('fetched', '')
                # 'ㅇ' 또는 빈값이 아닌 경우 포함
                if fetched_val and fetched_val.strip():
                    fetched_channels.append(ch)
                    self._log(f"  - [매칭] {ch.get('channel_name', '(이름없음)')}: fetched='{fetched_val}' (행: {ch.get('row', '?')})")

            self._log(f"  - 가져왔는지 여부에 값이 있는 채널: {len(fetched_channels)}개")

            # 일수 필터 적용
            self._log("▶ 경과 일수 필터 확인 중...")
            if not self.use_days_filter.get():
                # 필터 미사용 - 전체 채널
                target_count = len(fetched_channels)
                self._log(f"  - 필터 사용 여부: 미사용")
                self._log(f"  - 최종 조건 부합 채널: {target_count}개 (전체)")
                # 통합 UI로 변경되어 별도 라벨 업데이트 불필요
                self._log("=" * 80)
            else:
                # 일수 필터 사용
                self._log(f"  - 필터 사용 여부: 사용")
                try:
                    days_threshold = int(self.days_threshold_entry.get())
                    if days_threshold <= 0:
                        self._log(f"  - [오류] 일수는 1 이상이어야 합니다: 입력값={days_threshold}")
                        return
                except ValueError:
                    self._log(f"  - [오류] 일수는 숫자로 입력해야 합니다: 입력값='{self.days_threshold_entry.get()}'")
                    return

                self._log(f"  - 기준 경과 일수: {days_threshold}일 이상")
                self._log(f"  - 비교 대상 열: '수집날짜' 열")
                self._log("")

                # 경과 일수 계산
                today = datetime.now().date()
                self._log(f"▶ 기준 날짜: {today} (오늘)")
                self._log(f"▶ 채널별 경과 일수 계산 중...")
                self._log("")

                target_channels = []

                for ch in fetched_channels:
                    collection_date_str = ch.get('collection_date', '')
                    channel_name = ch.get('channel_name', '(이름 없음)')

                    if collection_date_str:
                        try:
                            # 날짜 파싱 (한국어 형식 지원)
                            collection_datetime = self.sheets_manager.parse_korean_datetime(collection_date_str)

                            if collection_datetime:
                                collection_date = collection_datetime.date()
                                days_passed = (today - collection_date).days

                                if days_passed >= days_threshold:
                                    target_channels.append(ch)
                                    self._log(f"  ✓ {channel_name}")
                                    self._log(f"    - 수집날짜 (원본): '{collection_date_str}'")
                                    self._log(f"    - 수집날짜 (파싱): {collection_date}")
                                    self._log(f"    - 경과 일수: {days_passed}일")
                                    self._log(f"    - 판정: 조건 부합 ({days_passed}일 >= {days_threshold}일)")
                                else:
                                    self._log(f"  ✗ {channel_name}")
                                    self._log(f"    - 수집날짜: {collection_date}")
                                    self._log(f"    - 경과 일수: {days_passed}일")
                                    self._log(f"    - 판정: 조건 미달 ({days_passed}일 < {days_threshold}일)")
                            else:
                                # 날짜 파싱 실패 시 포함
                                target_channels.append(ch)
                                self._log(f"  ✓ {channel_name}")
                                self._log(f"    - 수집날짜 (원본): '{collection_date_str}'")
                                self._log(f"    - 판정: 날짜 파싱 실패 → 포함 처리")
                        except Exception as e:
                            # 날짜 파싱 실패 시 포함
                            target_channels.append(ch)
                            self._log(f"  ✓ {channel_name}")
                            self._log(f"    - 수집날짜 (원본): '{collection_date_str}'")
                            self._log(f"    - 판정: 예외 발생 ({str(e)}) → 포함 처리")
                    else:
                        # 수집날짜 없으면 포함
                        target_channels.append(ch)
                        self._log(f"  ✓ {channel_name}")
                        self._log(f"    - 수집날짜: (없음)")
                        self._log(f"    - 판정: 수집날짜 없음 → 포함 처리")

                self._log("")
                self._log("=" * 80)
                self._log(f"[결과] 최종 조건 부합 채널: {len(target_channels)}개 (기준: {days_threshold}일 이상 경과)")
                self._log("=" * 80)

        except Exception as e:
            error_msg = f"조건 부합 채널: 오류 ({str(e)})"
            self._log(f"[오류] {error_msg}")
            self._log(f"상세 오류: {traceback.format_exc()}")


    def _on_update_new_videos_click(self):
        """신규 영상 업데이트 버튼 클릭 이벤트"""
        if self.is_searching:
            messagebox.showwarning("경고", "이미 작업이 진행 중입니다.")
            return

        # 일수 필터 확인
        days_threshold = None
        if self.use_days_filter.get():
            try:
                days_threshold = int(self.days_threshold_entry.get())
                if days_threshold <= 0:
                    messagebox.showerror("오류", "일수는 1 이상이어야 합니다.")
                    return
            except ValueError:
                messagebox.showerror("오류", "일수는 숫자로 입력해주세요.")
                return

        # 확인 메시지
        if days_threshold:
            confirm_msg = (
                f"수집날짜가 {days_threshold}일 이상 경과한 채널의 신규 영상을 업데이트하시겠습니까?\n\n"
                "처리 내용:\n"
                "1. 가져왔는지 여부가 'O'인 채널 필터링\n"
                f"2. 수집날짜가 {days_threshold}일 이상 경과한 채널 선택\n"
                "3. 각 채널의 신규 영상 추가 (중복 제외)\n"
                "4. 채널 리스트 정보 업데이트\n"
                "5. 영상 리스트의 해당 채널 영상 정보 업데이트"
            )
        else:
            confirm_msg = (
                "수집날짜에 관계없이 모든 조건 부합 채널의 신규 영상을 업데이트하시겠습니까?\n\n"
                "처리 내용:\n"
                "1. 가져왔는지 여부가 'O'인 모든 채널 선택\n"
                "2. 각 채널의 신규 영상 추가 (중복 제외)\n"
                "3. 채널 리스트 정보 업데이트\n"
                "4. 영상 리스트의 해당 채널 영상 정보 업데이트"
            )

        response = messagebox.askyesno("신규 영상 업데이트 확인", confirm_msg)
        if not response:
            return

        # 별도 스레드에서 처리
        thread = threading.Thread(
            target=self._update_new_videos_for_channels,
            args=(days_threshold,),
            daemon=True
        )
        thread.start()

    def _on_channel_click(self):
        """채널 영상 가져오기 버튼 클릭 이벤트"""
        if self.is_searching:
            messagebox.showwarning("경고", "이미 작업이 진행 중입니다.")
            return

        # 선택된 채널 수집
        selected_channels = []
        for display_text, (var, channel_info) in self.channel_checkboxes.items():
            if var.get():
                selected_channels.append(channel_info)

        # 선택된 채널이 없으면 오류
        if not selected_channels:
            messagebox.showerror("오류", "최소 1개 이상의 채널을 선택해주세요.")
            return

        # 여러 채널 선택 시 확인
        if len(selected_channels) > 1:
            response = messagebox.askyesno(
                "일괄 처리 확인",
                f"선택한 {len(selected_channels)}개 채널의 영상을 순차적으로 가져오시겠습니까?\n\n"
                f"API 과부하 방지를 위해 각 채널이 완료된 후 다음 채널이 처리됩니다."
            )
            if not response:
                return

        # 별도 스레드에서 처리
        thread = threading.Thread(target=self._fetch_selected_channels_videos, args=(selected_channels,), daemon=True)
        thread.start()

    def _on_playlist_click(self):
        """재생목록 가져오기 버튼 클릭 이벤트"""
        if self.is_searching:
            messagebox.showwarning("경고", "이미 작업이 진행 중입니다.")
            return

        if not self.playlist_items or self.playlist_combo.current() == -1:
            messagebox.showerror("오류", "재생목록을 선택하세요.")
            return

        # 별도 스레드에서 재생목록 가져오기 실행
        thread = threading.Thread(target=self._fetch_playlist_videos, daemon=True)
        thread.start()

    def _on_search_click(self):
        """검색 버튼 클릭 이벤트"""
        if self.is_searching:
            messagebox.showwarning("경고", "이미 검색이 진행 중입니다.")
            return

        keyword = self.keyword_entry.get().strip()
        if not keyword:
            messagebox.showerror("오류", "검색 키워드를 입력하세요.")
            return

        # 별도 스레드에서 검색 실행
        thread = threading.Thread(target=self._search_and_add_to_sheet, daemon=True)
        thread.start()

    def _search_and_add_to_sheet(self):
        """YouTube 검색 및 시트에 추가"""
        self.is_searching = True
        self.search_button.config(state='disabled')
        self.progress_bar.start()

        try:
            keyword = self.keyword_entry.get().strip()
            max_results = self.max_results_var.get()

            # 한글 값을 API 값으로 변환
            order_display = self.order_var.get()
            order = self.order_options.get(order_display, 'viewCount')

            order_direction = self.order_direction_var.get()

            duration_display = self.duration_var.get()
            duration = self.duration_options.get(duration_display, 'short')
            if duration == 'any':
                duration = None

            sheet_name = self.sheet_name_var.get()

            self._log(f"검색 시작: '{keyword}' (최대 {max_results}개, {order_display} - {order_direction})")

            # YouTube 검색
            try:
                results, stats = self.youtube_api.search_videos(
                    keyword=keyword,
                    max_results=max_results,
                    order=order,
                    video_duration=duration
                )
            except Exception as api_error:
                # YouTube API 쿼터 초과 또는 기타 오류 처리
                error_str = str(api_error)
                if 'quota' in error_str.lower() or 'quotaExceeded' in error_str:
                    self._log("=" * 50)
                    self._log("[오류] YouTube API 쿼터가 초과되었습니다!")
                    self._log("다음날 오전 0시(미국 태평양 시간)에 쿼터가 리셋됩니다.")
                    self._log("=" * 50)
                    messagebox.showerror("쿼터 초과", "YouTube API 일일 쿼터가 초과되었습니다.\n내일 다시 시도해주세요.")
                    return
                else:
                    raise  # 다른 오류는 그대로 전파

            # API 통계 로그
            self._log(f"API 호출: {stats['api_calls']}회")
            self._log(f"쿼터 소비: {stats['quota_cost']} 유닛 (일일 할당량 대비 {stats['quota_percent']}%)")
            self._log(f"검색 완료: {len(results)}개의 결과 발견 (고유 채널 {stats['unique_channels']}개)")

            if not results:
                self._log("검색 결과가 없습니다.")
                messagebox.showinfo("알림", "검색 결과가 없습니다.")
                return

            # 3분 이하 영상만 필터링 (쇼츠 옵션인 경우)
            if duration == 'short':
                original_count = len(results)
                results = [r for r in results if r.get('영상길이(초)', 0) <= 180]
                filtered_count = original_count - len(results)
                if filtered_count > 0:
                    self._log(f"3분 초과 영상 {filtered_count}개 제외, {len(results)}개 남음")

            # 정렬 순서 적용 (오름차순인 경우 결과 뒤집기)
            if order_direction == 'asc':
                results.reverse()
                self._log(f"오름차순으로 정렬")

            # 각 영상 데이터에 '재생목록 이름' 필드 추가 (키워드 검색 형식)
            for video_data in results:
                video_data['재생목록 이름'] = f"키워드 검색 : {keyword}"

            # 구글 시트에 추가
            self._log(f"구글 시트 '{sheet_name}'에 데이터 추가 중...")
            self.sheets_manager.bulk_append_data(sheet_name, results)

            self._log(f"완료! {len(results)}개의 영상 정보가 시트에 추가되었습니다.")
            # 알림음과 함께 메시지박스 표시
            self.root.bell()  # 시스템 알림음
            messagebox.showinfo("완료", f"{len(results)}개의 영상 정보가 시트에 추가되었습니다.")

        except Exception as e:
            error_msg = f"오류 발생: {str(e)}"
            self._log(error_msg)
            messagebox.showerror("오류", error_msg)

        finally:
            self.is_searching = False
            self.search_button.config(state='normal')
            self.progress_bar.stop()

    def _fetch_playlist_videos(self):
        """재생목록 영상 가져오기"""
        self.is_searching = True
        self.playlist_button.config(state='disabled')
        self.search_button.config(state='disabled')
        self.progress_bar.start()

        try:
            # 선택된 재생목록 정보
            selected_index = self.playlist_combo.current()
            playlist_info = self.playlist_items[selected_index]
            playlist_id = playlist_info['playlist_id']
            playlist_row = playlist_info['row']

            target_sheet = self.playlist_sheet_var.get()

            # 영상 갯수 설정
            max_results = None
            if not self.playlist_all_videos_var.get():
                max_results = self.playlist_video_count_var.get()

            fetch_text = "전체 영상" if max_results is None else f"최신 {max_results}개 영상"
            self._log(f"재생목록 '{playlist_info['playlist_name']}' (ID: {playlist_id}) {fetch_text} 가져오기 시작...")

            # YouTube API로 재생목록 영상 가져오기
            try:
                results, stats = self.youtube_api.get_playlist_videos(playlist_id, max_results)
            except Exception as api_error:
                # YouTube API 쿼터 초과 또는 기타 오류 처리
                error_str = str(api_error)
                if 'quota' in error_str.lower() or 'quotaExceeded' in error_str:
                    self._log("=" * 50)
                    self._log("[오류] YouTube API 쿼터가 초과되었습니다!")
                    self._log("다음날 오전 0시(미국 태평양 시간)에 쿼터가 리셋됩니다.")
                    self._log("=" * 50)
                    messagebox.showerror("쿼터 초과", "YouTube API 일일 쿼터가 초과되었습니다.\n내일 다시 시도해주세요.")
                    return
                else:
                    raise  # 다른 오류는 그대로 전파

            # API 통계 로그
            self._log(f"API 호출: {stats['api_calls']}회")
            self._log(f"쿼터 소비: {stats['quota_cost']} 유닛 (일일 할당량 대비 {stats['quota_percent']}%)")
            self._log(f"영상 가져오기 완료: {len(results)}개 (고유 채널 {stats['unique_channels']}개)")

            if not results:
                self._log("재생목록에 영상이 없습니다.")
                messagebox.showinfo("알림", "재생목록에 영상이 없습니다.")
                return

            # 각 영상 데이터에 재생목록 이름 추가 및 채널명 업데이트
            for video_data in results:
                video_data['재생목록 이름'] = playlist_info['playlist_name']

                # 채널 리스트에서 최신 채널명 가져오기 (채널명이 변경된 경우 대비)
                channel_id = video_data.get('채널 ID', '')
                if channel_id:
                    channel_name_from_list = self.sheets_manager.get_channel_name_from_channel_list(channel_id, '채널 리스트')
                    if channel_name_from_list:
                        video_data['채널명'] = channel_name_from_list

            # 구글 시트에 추가 (중복 확인 - 배치 처리)
            self._log(f"구글 시트 '{target_sheet}'에 데이터 추가/업데이트 중...")
            self._log(f"기존 영상 ID 목록 조회 중...")

            # 기존 영상 ID를 한 번에 가져오기 (1회 API 호출)
            existing_video_ids = self.sheets_manager.get_all_video_ids(target_sheet)
            self._log(f"기존 영상 {len(existing_video_ids)}개 확인 완료")

            new_count = 0
            updated_count = 0
            last_added_row = None
            last_updated_row = None
            last_added_title = None
            last_updated_title = None

            # 업데이트할 영상과 새로 추가할 영상 분리
            videos_to_update = []
            videos_to_add = []

            for video_data in results:
                video_id = video_data.get('영상 ID', '')
                video_title = video_data.get('제목', '')

                if video_id in existing_video_ids:
                    # 기존 영상 - 업데이트 목록에 추가
                    existing_row = existing_video_ids[video_id]
                    videos_to_update.append((existing_row, video_data, video_title))
                else:
                    # 신규 영상 - 추가 목록에 추가
                    videos_to_add.append((video_data, video_title))

            # 기존 영상 업데이트 (배치 처리)
            if videos_to_update:
                self._log(f"{len(videos_to_update)}개 영상 업데이트 중...")

                # 처음 3개 영상 예시 표시
                sample_count = min(3, len(videos_to_update))
                for i in range(sample_count):
                    row, data, title = videos_to_update[i]
                    channel_name = data.get('채널명', '')
                    self._log(f"  예시 {i+1}: 행 {row} | '{title}' | 채널: {channel_name}")

                # 배치 업데이트 준비
                batch_updates = [(row, data) for row, data, _ in videos_to_update]

                # 100개씩 배치 업데이트 (Google Sheets API 제한 고려)
                batch_size = 100
                for i in range(0, len(batch_updates), batch_size):
                    batch = batch_updates[i:i+batch_size]
                    self.sheets_manager.bulk_update_video_rows(target_sheet, batch)
                    if i + batch_size < len(batch_updates):
                        self._log(f"  - {i+len(batch)}/{len(batch_updates)} 업데이트 완료...")

                # 업데이트 카운트 및 마지막 정보
                updated_count = len(videos_to_update)
                last_updated_row = videos_to_update[-1][0]
                last_updated_title = videos_to_update[-1][2]

                self._log(f"업데이트 완료")

            # 신규 영상 추가
            if videos_to_add:
                self._log(f"{len(videos_to_add)}개 신규 영상 추가 중...")
                last_row_before = self.sheets_manager.get_last_row(target_sheet)

                # 모든 신규 영상을 한 번에 추가
                new_videos_data = [video_data for video_data, _ in videos_to_add]
                self.sheets_manager.bulk_append_data(target_sheet, new_videos_data)

                new_count = len(videos_to_add)
                last_added_row = last_row_before + new_count
                last_added_title = videos_to_add[-1][1]  # 마지막 추가된 영상 제목
                self._log(f"추가 완료")

            # 재생목록ID 시트의 영상갯수와 마지막 체크일 업데이트
            self.sheets_manager.update_playlist_info('재생목록ID', playlist_row, len(results))

            # 상세 로그 출력
            self._log(f"=" * 50)
            self._log(f"[완료] 재생목록 영상 가져오기 완료")
            self._log(f"- 전체 영상 갯수: {len(results)}개")
            self._log(f"- 신규 추가: {new_count}개" + (f" (마지막 추가 행: {last_added_row})" if last_added_row else ""))
            self._log(f"- 중복 업데이트: {updated_count}개" + (f" (마지막 업데이트 행: {last_updated_row})" if last_updated_row else ""))

            if last_added_title:
                self._log(f"- 마지막 추가된 영상: {last_added_title}")
            if last_updated_title:
                self._log(f"- 마지막 업데이트된 영상: {last_updated_title}")

            self._log(f"- 재생목록ID 시트 업데이트 완료")
            self._log(f"=" * 50)

            # 결과 메시지 생성
            result_msg = f"전체 영상: {len(results)}개\n"
            result_msg += f"신규 추가: {new_count}개"
            if last_added_row:
                result_msg += f" (행 {last_added_row})"
            result_msg += f"\n중복 업데이트: {updated_count}개"
            if last_updated_row:
                result_msg += f" (행 {last_updated_row})"

            if last_added_title:
                result_msg += f"\n\n마지막 추가: {last_added_title}"
            elif last_updated_title:
                result_msg += f"\n\n마지막 업데이트: {last_updated_title}"

            # 알림음과 함께 메시지박스 표시
            self.root.bell()
            messagebox.showinfo("완료", result_msg)

        except Exception as e:
            error_msg = f"오류 발생: {str(e)}"
            self._log(error_msg)
            messagebox.showerror("오류", error_msg)

        finally:
            self.is_searching = False
            self.playlist_button.config(state='normal')
            self.search_button.config(state='normal')
            self.progress_bar.stop()

    def _fetch_selected_channels_videos(self, selected_channels):
        """선택된 채널들의 영상 순차적으로 가져오기"""
        self.is_searching = True
        self.interrupt_requested = False  # 중단 플래그 초기화
        self.channel_button.config(state='disabled')
        self.playlist_button.config(state='disabled')
        self.search_button.config(state='disabled')
        self.progress_bar.start()

        try:
            total_channels = len(selected_channels)
            show_result = (total_channels == 1)  # 단일 채널만 상세 결과 표시

            if total_channels > 1:
                self._log(f"=" * 50)
                self._log(f"[시작] {total_channels}개 채널 순차 처리")
                self._log(f"=" * 50)

            success_count = 0
            fail_count = 0

            for idx, channel_info in enumerate(selected_channels, 1):
                # 중단 요청 확인
                if self.interrupt_requested:
                    self._log(f"[중단됨] {idx-1}/{total_channels}개 채널 처리 후 중단")
                    break

                try:
                    if total_channels > 1:
                        self._log(f"[{idx}/{total_channels}] {channel_info['channel_name']} 처리 중...")

                    self._process_single_channel(channel_info, show_result=show_result)
                    success_count += 1

                    # API 과부하 방지: 각 채널 처리 후 잠시 대기 (다음 채널이 있을 경우)
                    if idx < total_channels:
                        import time
                        time.sleep(2)  # 2초 대기

                except Exception as e:
                    self._log(f"오류: {channel_info['channel_name']} - {str(e)}")
                    fail_count += 1

            # 여러 채널 처리 시 최종 결과
            if total_channels > 1:
                self._log(f"=" * 50)
                self._log(f"[완료] 일괄 처리 완료")
                self._log(f"- 성공: {success_count}개")
                self._log(f"- 실패: {fail_count}개")
                self._log(f"=" * 50)

                self.root.bell()
                messagebox.showinfo("완료", f"일괄 처리 완료\n성공: {success_count}개\n실패: {fail_count}개")

        except Exception as e:
            error_msg = f"채널 처리 오류: {str(e)}"
            self._log(error_msg)
            messagebox.showerror("오류", error_msg)

        finally:
            self.is_searching = False
            self.channel_button.config(state='normal')
            self.playlist_button.config(state='normal')
            self.search_button.config(state='normal')
            self.progress_bar.stop()

    def _process_single_channel(self, channel_info: Dict, show_result: bool = True):
        """단일 채널 처리"""
        try:
            channel_id = channel_info['channel_id']
            channel_row = channel_info['row']
            target_sheet = '영상 리스트'

            # 영상 갯수 설정
            max_results = None
            if not self.channel_all_videos_var.get():
                max_results = self.channel_video_count_var.get()

            # 정렬 기준
            order_display = self.channel_order_var.get()
            order = self.channel_order_options.get(order_display, 'date')

            # 최신순이 아닌 경우 쿼터 절약 안내
            if order != 'date':
                self._log(f"[알림] '{order_display}' 정렬은 업로드 재생목록을 사용할 수 없어 최신순으로 대체됩니다.")
                self._log("[팁] 쿼터 절약을 위해 업로드 재생목록 방식을 사용합니다 (97% 절감)")

            fetch_text = "전체 영상" if max_results is None else f"최신 {max_results}개 영상"
            self._log(f"채널 '{channel_info['channel_name']}' (ID: {channel_id}) {fetch_text} 가져오기 시작...")

            # YouTube API로 채널 영상 가져오기
            try:
                results, stats = self.youtube_api.get_channel_videos(channel_id, max_results, order)
            except Exception as api_error:
                # YouTube API 쿼터 초과 또는 기타 오류 처리
                error_str = str(api_error)
                if 'quota' in error_str.lower() or 'quotaExceeded' in error_str:
                    self._log("=" * 50)
                    self._log("[오류] YouTube API 쿼터가 초과되었습니다!")
                    self._log("다음날 오전 0시(미국 태평양 시간)에 쿼터가 리셋됩니다.")
                    self._log("=" * 50)
                    if show_result:
                        messagebox.showerror("쿼터 초과", "YouTube API 일일 쿼터가 초과되었습니다.\n내일 다시 시도해주세요.")
                    raise  # 상위로 예외 전파하여 작업 중단
                else:
                    raise  # 다른 오류는 그대로 전파

            # API 통계 로그
            self._log(f"API 호출: {stats['api_calls']}회")
            self._log(f"쿼터 소비: {stats['quota_cost']} 유닛 (일일 할당량 대비 {stats['quota_percent']}%)")
            self._log(f"영상 가져오기 완료: {len(results)}개 (고유 채널 {stats['unique_channels']}개)")

            if not results:
                self._log("채널에 영상이 없습니다.")
                if show_result:
                    messagebox.showinfo("알림", "채널에 영상이 없습니다.")
                return

            # 채널 리스트에서 추가 정보 가져오기 (분야1, 분야2, 사용언어, 재생목록 이름, 채널명)
            channel_extra_info = {
                '분야1': channel_info.get('category1', ''),
                '분야2': channel_info.get('category2', ''),
                '사용언어': channel_info.get('language', ''),
                '재생목록 이름': channel_info.get('playlist_name', '')
            }

            # 채널 리스트에서 최신 채널명 가져오기 (채널명이 변경된 경우 대비)
            channel_name_from_list = self.sheets_manager.get_channel_name_from_channel_list(channel_id, '채널 리스트')
            if channel_name_from_list:
                channel_extra_info['채널명'] = channel_name_from_list
                self._log(f"채널 리스트에서 채널명 '{channel_name_from_list}' 적용")

            self._log(f"채널 추가 정보: 분야1={channel_extra_info['분야1']}, 분야2={channel_extra_info['분야2']}")

            # 각 영상 데이터에 채널 추가 정보 병합 및 검색 키워드 제거
            for video_data in results:
                # 검색 키워드 제거 (채널 영상은 검색 키워드가 없음)
                if '검색 키워드' in video_data:
                    del video_data['검색 키워드']

                # 채널 추가 정보 병합 (채널명도 덮어씀)
                video_data.update(channel_extra_info)

            # 구글 시트에 추가 (중복 확인 - 배치 처리)
            self._log(f"구글 시트 '{target_sheet}'에 데이터 추가/업데이트 중...")
            self._log(f"기존 영상 ID 목록 조회 중...")

            # 기존 영상 ID를 한 번에 가져오기 (1회 API 호출)
            existing_video_ids = self.sheets_manager.get_all_video_ids(target_sheet)
            self._log(f"기존 영상 {len(existing_video_ids)}개 확인 완료")

            new_count = 0
            updated_count = 0
            last_added_row = None
            last_updated_row = None
            last_added_title = None
            last_updated_title = None

            # 업데이트할 영상과 새로 추가할 영상 분리
            videos_to_update = []
            videos_to_add = []

            for video_data in results:
                video_id = video_data.get('영상 ID', '')
                video_title = video_data.get('제목', '')

                if video_id in existing_video_ids:
                    # 기존 영상 - 업데이트 목록에 추가
                    existing_row = existing_video_ids[video_id]
                    videos_to_update.append((existing_row, video_data, video_title))
                else:
                    # 신규 영상 - 추가 목록에 추가
                    videos_to_add.append((video_data, video_title))

            # 기존 영상 업데이트 (배치 처리)
            if videos_to_update:
                self._log(f"{len(videos_to_update)}개 영상 업데이트 중...")

                # 처음 3개 영상 예시 표시
                sample_count = min(3, len(videos_to_update))
                for i in range(sample_count):
                    row, data, title = videos_to_update[i]
                    channel_name = data.get('채널명', '')
                    self._log(f"  예시 {i+1}: 행 {row} | '{title}' | 채널: {channel_name}")

                # 배치 업데이트 준비
                batch_updates = [(row, data) for row, data, _ in videos_to_update]

                # 100개씩 배치 업데이트 (Google Sheets API 제한 고려)
                batch_size = 100
                for i in range(0, len(batch_updates), batch_size):
                    batch = batch_updates[i:i+batch_size]
                    self.sheets_manager.bulk_update_video_rows(target_sheet, batch)
                    if i + batch_size < len(batch_updates):
                        self._log(f"  - {i+len(batch)}/{len(batch_updates)} 업데이트 완료...")

                # 업데이트 카운트 및 마지막 정보
                updated_count = len(videos_to_update)
                last_updated_row = videos_to_update[-1][0]
                last_updated_title = videos_to_update[-1][2]

                self._log(f"업데이트 완료")

            # 신규 영상 추가
            if videos_to_add:
                self._log(f"{len(videos_to_add)}개 신규 영상 추가 중...")
                last_row_before = self.sheets_manager.get_last_row(target_sheet, start_row=10)

                # 모든 신규 영상을 한 번에 추가
                new_videos_data = [video_data for video_data, _ in videos_to_add]
                self.sheets_manager.bulk_append_data(target_sheet, new_videos_data, start_row=10)

                new_count = len(videos_to_add)
                last_added_row = last_row_before + new_count
                last_added_title = videos_to_add[-1][1]  # 마지막 추가된 영상 제목
                self._log(f"추가 완료")

            # 상세 로그 출력
            self._log(f"=" * 50)
            self._log(f"[완료] 채널 영상 가져오기 완료")
            self._log(f"- 전체 영상 갯수: {len(results)}개")
            self._log(f"- 신규 추가: {new_count}개" + (f" (마지막 추가 행: {last_added_row})" if last_added_row else ""))
            self._log(f"- 중복 업데이트: {updated_count}개" + (f" (마지막 업데이트 행: {last_updated_row})" if last_updated_row else ""))

            if last_added_title:
                self._log(f"- 마지막 추가된 영상: {last_added_title}")
            if last_updated_title:
                self._log(f"- 마지막 업데이트된 영상: {last_updated_title}")

            self._log(f"=" * 50)

            # 결과 메시지 생성
            result_msg = f"전체 영상: {len(results)}개\n"
            result_msg += f"신규 추가: {new_count}개"
            if last_added_row:
                result_msg += f" (행 {last_added_row})"
            result_msg += f"\n중복 업데이트: {updated_count}개"
            if last_updated_row:
                result_msg += f" (행 {last_updated_row})"

            if last_added_title:
                result_msg += f"\n\n마지막 추가: {last_added_title}"
            elif last_updated_title:
                result_msg += f"\n\n마지막 업데이트: {last_updated_title}"

            # 채널 리스트 업데이트 (가져왔는지 여부, 수집날짜, 채널 상세 정보 등)
            from datetime import datetime
            channel_update_info = {
                '가져왔는지 여부': 'O',
                '수집날짜': datetime.now().strftime('%Y-%m-%d'),
                '영상갯수': len(results),
                '가져온 영상갯수': new_count + updated_count
            }

            # 첫 번째 영상에서 채널 상세 정보 추출 (모든 영상이 같은 채널의 정보를 가짐)
            if results:
                first_video = results[0]
                channel_update_info.update({
                    '구독자수': first_video.get('구독자수', ''),
                    '채널전체 영상갯수': first_video.get('영상갯수', ''),
                    '채널전체 조회수': first_video.get('채널 전체 조회수', ''),
                    '채널국가': first_video.get('채널국가', ''),
                    '개설일': first_video.get('채널 개설일', ''),
                    '채널 디스크립션': first_video.get('채널 디스크립션', ''),
                    '채널 핸들': first_video.get('채널 핸들', '')
                })

            self.sheets_manager.update_channel_info('채널 리스트', channel_row, channel_update_info)
            self._log("채널 리스트 업데이트 완료")

            # 알림음과 함께 메시지박스 표시 (show_result가 True일 때만)
            if show_result:
                self.root.bell()
                messagebox.showinfo("완료", result_msg)

        except Exception as e:
            error_msg = f"오류 발생: {str(e)}"
            self._log(error_msg)
            messagebox.showerror("오류", error_msg)

        finally:
            self.is_searching = False
            self.channel_button.config(state='normal')
            self.playlist_button.config(state='normal')
            self.search_button.config(state='normal')
            self.progress_bar.stop()

    def _update_channel_info_and_new_videos(self):
        """채널 정보 업데이트 + 신규 영상 업데이트 통합 함수"""
        from datetime import datetime

        self.is_searching = True
        self.interrupt_requested = False
        self.channel_button.config(state='disabled')
        self.playlist_button.config(state='disabled')
        self.search_button.config(state='disabled')
        self.update_channel_info_button.config(state='disabled')
        self.progress_bar.start()

        try:
            self._log("=" * 80)
            self._log("[시작] 채널 정보 업데이트 & 신규 영상 업데이트")
            self._log("=" * 80)

            # 일수 임계값 가져오기
            days_threshold = None
            if self.use_days_filter.get():
                try:
                    days_threshold = int(self.days_threshold_entry.get())
                    self._log(f"▶ 수집날짜 필터: {days_threshold}일 이상 경과한 채널만 대상")
                except:
                    self._log("[오류] 일수 입력 오류 - 필터 미사용")
                    days_threshold = None
            else:
                self._log("▶ 수집날짜 필터: 사용 안 함 (전체 채널 대상)")

            # 1단계: 채널 정보 업데이트
            self._log("\n" + "=" * 80)
            self._log("[1단계] 채널 정보 업데이트")
            self._log("=" * 80)

            # 필터된 채널 가져오기
            filtered_channels = self._get_filtered_channels()
            self._log(f"▶ 필터 조건에 맞는 채널: {len(filtered_channels)}개")

            if filtered_channels:
                self._update_filtered_channel_info(filtered_channels)
            else:
                self._log("  - 업데이트할 채널이 없습니다.")

            # 2단계: 신규 영상 업데이트
            self._log("\n" + "=" * 80)
            self._log("[2단계] 신규 영상 업데이트")
            self._log("=" * 80)

            # 신규 영상 업데이트 수행
            self._update_new_videos_for_channels(days_threshold)

            self._log("\n" + "=" * 80)
            self._log("[완료] 모든 작업 완료")
            self._log("=" * 80)

            messagebox.showinfo(
                "완료",
                "채널 정보 업데이트 및 신규 영상 업데이트가 완료되었습니다."
            )

        except Exception as e:
            error_msg = f"채널 정보 & 신규 영상 업데이트 오류: {str(e)}"
            self._log(f"[오류] {error_msg}")
            self._log(traceback.format_exc())
            messagebox.showerror("오류", error_msg)
        finally:
            self.progress_bar.stop()
            self.channel_button.config(state='normal')
            self.playlist_button.config(state='normal')
            self.search_button.config(state='normal')
            self.update_channel_info_button.config(state='normal')
            self.is_searching = False

    def _update_filtered_channel_info(self, channels):
        """필터링된 채널의 정보 업데이트 (영상 리스트에서 추출)"""
        self._log(f"▶ {len(channels)}개 채널 정보 업데이트 시작...")

        for idx, channel_info in enumerate(channels, 1):
            if self.interrupt_requested:
                self._log(f"[중단됨] {idx-1}/{len(channels)}개 채널 업데이트 후 중단")
                break

            try:
                channel_id = channel_info.get('channel_id', '')
                channel_name = channel_info.get('channel_name', '(이름 없음)')
                row = channel_info.get('row', 0)

                self._log(f"  [{idx}/{len(channels)}] '{channel_name}' (행 {row}) 정보 추출 중...")

                # 영상 리스트에서 해당 채널의 정보 가져오기
                channel_detail = self.sheets_manager.get_channel_info_from_video_sheet(
                    channel_id, '영상 리스트'
                )

                if not channel_detail:
                    self._log(f"    - 영상 리스트에 채널 정보 없음 (건너뜀)")
                    continue

                # 업데이트할 데이터 구성
                update_data = {}

                # 필수 필드만 업데이트
                if channel_detail.get('subscriber_count'):
                    update_data['subscriber_count'] = channel_detail['subscriber_count']
                if channel_detail.get('total_view_count'):
                    update_data['channel_total_views'] = channel_detail['total_view_count']
                if channel_detail.get('video_count'):
                    update_data['channel_total_video_count'] = channel_detail['video_count']
                if channel_detail.get('channel_country'):
                    update_data['channel_country'] = channel_detail['channel_country']
                if channel_detail.get('channel_description'):
                    update_data['channel_description'] = channel_detail['channel_description']
                if channel_detail.get('channel_handle'):
                    update_data['channel_handle'] = channel_detail['channel_handle']
                if channel_detail.get('published_at'):
                    update_data['published_at'] = channel_detail['published_at']

                # 업데이트 실행
                if update_data:
                    self.sheets_manager.update_channel_info('채널 리스트', row, update_data)
                    self._log(f"    ✓ 업데이트 완료: {len(update_data)}개 필드")
                else:
                    self._log(f"    - 업데이트할 데이터 없음")

            except Exception as e:
                self._log(f"    ✗ 오류: {str(e)}")

        self._log(f"▶ 채널 정보 업데이트 완료")

    def _update_missing_channel_info(self):
        """가져왔는지 여부가 O인 채널 중 빈 필드가 있거나 수집영상 갯수가 0인 채널들의 정보 업데이트"""
        self.is_searching = True
        self.interrupt_requested = False
        self.channel_button.config(state='disabled')
        self.playlist_button.config(state='disabled')
        self.search_button.config(state='disabled')
        self.update_channel_info_button.config(state='disabled')
        self.progress_bar.start()

        try:
            self._log("=" * 50)
            self._log("[시작] 채널 정보 업데이트 시작")
            self._log("=" * 50)

            # 업데이트가 필요한 채널 찾기
            self._log("업데이트가 필요한 채널 찾는 중...")
            channels_to_update = self.sheets_manager.get_channels_with_missing_info('채널 리스트')

            if not channels_to_update:
                self._log("업데이트가 필요한 채널이 없습니다.")
                messagebox.showinfo("알림", "업데이트가 필요한 채널이 없습니다.")
                return

            total_channels = len(channels_to_update)
            self._log(f"총 {total_channels}개 채널 정보 업데이트 필요")

            success_count = 0
            fail_count = 0
            not_found_count = 0
            fetched_count = 0
            total_quota_used = 0

            for idx, channel_info in enumerate(channels_to_update, 1):
                # 중단 요청 확인
                if self.interrupt_requested:
                    self._log(f"[중단됨] {idx-1}/{total_channels}개 채널 업데이트 후 중단")
                    break

                try:
                    # API 과부하 방지: 각 채널 처리 전 잠시 대기 (첫 번째 제외)
                    if idx > 1:
                        import time
                        time.sleep(1.5)  # 1.5초 대기로 분당 40회 이하 유지

                    channel_id = channel_info['channel_id']
                    channel_name = channel_info['channel_name']
                    row = channel_info['row']

                    self._log(f"[{idx}/{total_channels}] '{channel_name}' (행 {row}) 정보 추출 중...")

                    # 먼저 영상 리스트에서 해당 채널의 영상 갯수 확인
                    video_count_in_sheet = self.sheets_manager.count_videos_in_video_sheet(channel_id, '영상 리스트')
                    self._log(f"  - 영상 리스트에서 {video_count_in_sheet}개 영상 발견")

                    # 영상 리스트에서 채널 정보 추출 시도
                    channel_detail = None
                    if video_count_in_sheet > 0:
                        channel_detail = self.sheets_manager.get_channel_info_from_video_sheet(channel_id, '영상 리스트')

                    # 채널 정보가 없거나 필수 정보(구독자수 등)가 비어있는 경우 API로 가져오기
                    needs_api_fetch = False
                    if not channel_detail:
                        needs_api_fetch = True
                        self._log(f"  - 영상 리스트에 채널 정보 없음")
                    else:
                        # 필수 정보 중 하나라도 비어있으면 API 호출
                        essential_fields = ['subscriber_count', 'video_count', 'total_view_count']
                        missing_fields = [f for f in essential_fields if not channel_detail.get(f, '').strip()]
                        if missing_fields:
                            needs_api_fetch = True
                            self._log(f"  - 필수 정보 누락: {', '.join(missing_fields)}")

                    if needs_api_fetch:
                        self._log(f"  - YouTube API로 채널 정보 가져오는 중...")
                        try:
                            # YouTube API로 채널 정보 가져오기 (쿼터 1)
                            api_channel_info = self.youtube_api.get_channel_info(channel_id)
                            total_quota_used += 1  # channels().list() = 1 쿼터
                            self._log(f"  - API 호출 완료 (쿼터 1 사용)")

                            if api_channel_info:
                                # API 응답을 channel_detail 형식으로 변환
                                channel_detail = {
                                    'channel_id': api_channel_info.get('channel_id', ''),
                                    'channel_name': api_channel_info.get('channel_name', ''),
                                    'subscriber_count': str(api_channel_info.get('subscriber_count', '')),
                                    'video_count': str(api_channel_info.get('video_count', '')),
                                    'total_view_count': str(api_channel_info.get('total_view_count', '')),
                                    'channel_country': api_channel_info.get('channel_country', ''),
                                    'published_at': api_channel_info.get('published_at', ''),
                                    'channel_description': api_channel_info.get('channel_description', ''),
                                    'channel_handle': api_channel_info.get('channel_handle', '')
                                }
                                self._log(f"  - API에서 채널 정보 취득 성공")

                                # 영상이 0개인 경우 200개 영상도 가져오기
                                if video_count_in_sheet == 0:
                                    self._log(f"  - 영상이 없으므로 API로 200개 영상 가져오는 중...")
                                    try:
                                        results, stats = self.youtube_api.get_channel_videos(channel_id, max_results=200, order='date')
                                        self._log(f"  - 영상 API 호출: {stats['api_calls']}회, 쿼터: {stats['quota_cost']} 유닛")
                                        total_quota_used += stats['quota_cost']

                                        if results:
                                            # 채널 리스트에서 추가 정보 가져오기
                                            channel_list_worksheet = self.sheets_manager.get_or_create_worksheet('채널 리스트')
                                            channel_list_headers = channel_list_worksheet.row_values(1)
                                            channel_list_all_values = channel_list_worksheet.get_all_values()
                                            channel_row_data = channel_list_all_values[row - 1] if row <= len(channel_list_all_values) else []

                                            extra_col_mapping = {}
                                            for idx_col, header in enumerate(channel_list_headers):
                                                normalized = self.sheets_manager.normalize_header(header)
                                                if normalized in ['분야1', '분야2', '사용언어', '재생목록 이름']:
                                                    extra_col_mapping[normalized] = idx_col

                                            channel_extra_info = {
                                                '분야1': channel_row_data[extra_col_mapping['분야1']].strip() if '분야1' in extra_col_mapping and extra_col_mapping['분야1'] < len(channel_row_data) else '',
                                                '분야2': channel_row_data[extra_col_mapping['분야2']].strip() if '분야2' in extra_col_mapping and extra_col_mapping['분야2'] < len(channel_row_data) else '',
                                                '사용언어': channel_row_data[extra_col_mapping['사용언어']].strip() if '사용언어' in extra_col_mapping and extra_col_mapping['사용언어'] < len(channel_row_data) else '',
                                                '재생목록 이름': channel_row_data[extra_col_mapping['재생목록 이름']].strip() if '재생목록 이름' in extra_col_mapping and extra_col_mapping['재생목록 이름'] < len(channel_row_data) else ''
                                            }

                                            for video_data in results:
                                                if '검색 키워드' in video_data:
                                                    del video_data['검색 키워드']
                                                video_data.update(channel_extra_info)

                                            self._log(f"  - 영상 리스트 시트에 {len(results)}개 영상 추가 중...")
                                            self.sheets_manager.bulk_append_data('영상 리스트', results, start_row=10)
                                            self._log(f"  - 영상 추가 완료")
                                            fetched_count += 1
                                    except Exception as video_error:
                                        self._log(f"  - 영상 가져오기 오류: {str(video_error)}")
                            else:
                                self._log(f"  - API 응답에서 채널 정보 없음")
                                channel_detail = None

                        except Exception as api_error:
                            self._log(f"  - API 호출 오류: {str(api_error)}")
                            channel_detail = None

                    if not channel_detail:
                        self._log(f"  - 건너뜀: 채널 정보를 가져올 수 없습니다.")
                        not_found_count += 1
                        continue

                    # 업데이트할 정보 준비
                    # needs_api_fetch가 True였으면 모든 정보 업데이트, 아니면 빈 값이 아닌 것만
                    update_info = {}

                    if needs_api_fetch:
                        # API에서 가져온 경우 모든 정보 업데이트
                        update_info['구독자수'] = channel_detail.get('subscriber_count', '')
                        update_info['채널전체 영상갯수'] = channel_detail.get('video_count', '')
                        update_info['채널전체 조회수'] = channel_detail.get('total_view_count', '')
                        update_info['재생목록 이름'] = channel_detail.get('channel_name', '')
                        update_info['채널국가'] = channel_detail.get('channel_country', '')
                        update_info['개설일'] = channel_detail.get('published_at', '')
                        update_info['채널 디스크립션'] = channel_detail.get('channel_description', '')
                        update_info['채널 핸들'] = channel_detail.get('channel_handle', '')
                    else:
                        # 영상 리스트에서 가져온 경우 빈 값이 아닌 것만 업데이트
                        if channel_detail.get('subscriber_count', '').strip():
                            update_info['구독자수'] = channel_detail.get('subscriber_count', '')

                        if channel_detail.get('video_count', '').strip():
                            update_info['채널전체 영상갯수'] = channel_detail.get('video_count', '')

                        if channel_detail.get('total_view_count', '').strip():
                            update_info['채널전체 조회수'] = channel_detail.get('total_view_count', '')

                        if channel_detail.get('channel_name', '').strip():
                            update_info['재생목록 이름'] = channel_detail.get('channel_name', '')

                        if channel_detail.get('channel_country', '').strip():
                            update_info['채널국가'] = channel_detail.get('channel_country', '')

                        if channel_detail.get('published_at', '').strip():
                            update_info['개설일'] = channel_detail.get('published_at', '')

                        if channel_detail.get('channel_description', '').strip():
                            update_info['채널 디스크립션'] = channel_detail.get('channel_description', '')

                        if channel_detail.get('channel_handle', '').strip():
                            update_info['채널 핸들'] = channel_detail.get('channel_handle', '')

                    # 가져온 영상갯수는 항상 업데이트
                    update_info['가져온 영상갯수'] = self.sheets_manager.count_videos_in_video_sheet(channel_id, '영상 리스트')

                    # 업데이트할 항목이 있는지 확인
                    if not update_info or (len(update_info) == 1 and '가져온 영상갯수' in update_info):
                        self._log(f"  - 건너뜀: 업데이트할 정보가 없습니다.")
                        not_found_count += 1
                        continue

                    # 채널 리스트 업데이트
                    self.sheets_manager.update_channel_info('채널 리스트', row, update_info)

                    # 로그 출력
                    log_parts = []
                    if '구독자수' in update_info:
                        log_parts.append(f"구독자수 {update_info['구독자수']}")
                    if '채널전체 영상갯수' in update_info:
                        log_parts.append(f"채널전체 영상갯수 {update_info['채널전체 영상갯수']}")
                    if '가져온 영상갯수' in update_info:
                        log_parts.append(f"수집영상 갯수 {update_info['가져온 영상갯수']}")

                    source = "API" if needs_api_fetch else "영상 리스트"
                    self._log(f"  - 완료 ({source}): {', '.join(log_parts) if log_parts else '가져온 영상갯수만 업데이트'}")
                    success_count += 1

                except Exception as e:
                    error_str = str(e)
                    # Google Sheets API 제한 에러 처리
                    if 'RATE_LIMIT_EXCEEDED' in error_str or '429' in error_str:
                        self._log(f"  - API 제한 도달: 2분 대기 후 재시도...")
                        import time
                        time.sleep(120)  # 2분 대기

                        try:
                            # 재시도
                            video_count_in_sheet = self.sheets_manager.count_videos_in_video_sheet(channel_id, '영상 리스트')

                            if video_count_in_sheet == 0:
                                channel_detail = None
                            else:
                                channel_detail = self.sheets_manager.get_channel_info_from_video_sheet(channel_id, '영상 리스트')

                            if channel_detail:
                                # 빈 값이 아닌 것만 업데이트
                                update_info = {}
                                if channel_detail.get('subscriber_count', '').strip():
                                    update_info['구독자수'] = channel_detail.get('subscriber_count', '')
                                if channel_detail.get('video_count', '').strip():
                                    update_info['채널전체 영상갯수'] = channel_detail.get('video_count', '')
                                if channel_detail.get('total_view_count', '').strip():
                                    update_info['채널전체 조회수'] = channel_detail.get('total_view_count', '')
                                if channel_detail.get('channel_name', '').strip():
                                    update_info['재생목록 이름'] = channel_detail.get('channel_name', '')
                                if channel_detail.get('channel_country', '').strip():
                                    update_info['채널국가'] = channel_detail.get('channel_country', '')
                                if channel_detail.get('published_at', '').strip():
                                    update_info['개설일'] = channel_detail.get('published_at', '')
                                if channel_detail.get('channel_description', '').strip():
                                    update_info['채널 디스크립션'] = channel_detail.get('channel_description', '')
                                if channel_detail.get('channel_handle', '').strip():
                                    update_info['채널 핸들'] = channel_detail.get('channel_handle', '')
                                update_info['가져온 영상갯수'] = self.sheets_manager.count_videos_in_video_sheet(channel_id, '영상 리스트')

                                if update_info:
                                    self.sheets_manager.update_channel_info('채널 리스트', row, update_info)
                                    self._log(f"  - 재시도 성공")
                                    success_count += 1
                                else:
                                    self._log(f"  - 재시도 실패: 업데이트할 정보가 없음")
                                    fail_count += 1
                            else:
                                self._log(f"  - 재시도 실패: 채널 정보를 가져올 수 없음")
                                fail_count += 1
                        except Exception as retry_error:
                            self._log(f"  - 재시도 오류: {str(retry_error)}")
                            fail_count += 1
                    else:
                        self._log(f"  - 오류: {error_str}")
                        fail_count += 1

            # 최종 결과
            self._log(f"=" * 50)
            self._log(f"[완료] 채널 정보 업데이트 완료")
            self._log(f"- 성공: {success_count}개")
            self._log(f"- 영상 리스트에 없어서 API로 가져옴: {fetched_count}개")
            self._log(f"- 가져오지 못함: {not_found_count}개")
            self._log(f"- 실패: {fail_count}개")
            self._log(f"- 쿼터 사용: {total_quota_used} 유닛")
            self._log(f"=" * 50)

            result_msg = f"채널 정보 업데이트 완료\n\n성공: {success_count}개"
            if fetched_count > 0:
                result_msg += f"\nAPI로 영상 가져옴: {fetched_count}개"
            if not_found_count > 0:
                result_msg += f"\n가져오지 못함: {not_found_count}개"
            if fail_count > 0:
                result_msg += f"\n실패: {fail_count}개"
            result_msg += f"\n\n※ 쿼터 사용: {total_quota_used} 유닛"

            self.root.bell()
            messagebox.showinfo("완료", result_msg)

        except Exception as e:
            error_msg = f"채널 정보 업데이트 오류: {str(e)}"
            self._log(error_msg)
            messagebox.showerror("오류", error_msg)

        finally:
            self.is_searching = False
            self.channel_button.config(state='normal')
            self.playlist_button.config(state='normal')
            self.search_button.config(state='normal')
            self.update_channel_info_button.config(state='normal')
            self.progress_bar.stop()

    def _on_escape_pressed(self, event):
        """ESC 키 눌렀을 때 작업 중단"""
        if self.is_searching:
            self.interrupt_requested = True
            self._log("=" * 50)
            self._log("[중단 요청] ESC 키가 눌렸습니다. 현재 작업을 중단합니다...")
            self._log("=" * 50)

    def _update_playlist_quota_display(self):
        """재생목록 쿼터 비용 표시 업데이트"""
        try:
            if self.playlist_all_videos_var.get():
                video_count = "전체"
            else:
                video_count = self.playlist_video_count_var.get()

            if video_count == "전체":
                quota_text = "💰 예상 쿼터: 영상 수에 따라 다름 (예: 200개 = 9 유닛, 500개 = 21 유닛)"
            else:
                pages = (video_count + 49) // 50
                batches = (video_count + 49) // 50
                total_quota = pages + batches + 1
                percent = round(total_quota / 10000 * 100, 2)
                quota_text = f"💰 예상 쿼터: 재생목록조회 {pages} + 영상정보 {batches} + 채널정보 1 = {total_quota} 유닛 ({percent}%)"

            self.playlist_quota_label.config(text=quota_text)
        except:
            pass

    def _update_search_quota_display(self):
        """검색 쿼터 비용 표시 업데이트"""
        try:
            max_results = self.max_results_var.get()
            # 검색 100 + 영상정보 1 + 채널정보(평균 고유 채널 수 추정)
            estimated_channels = min(max_results, 30)  # 보통 50개 영상에 30개 정도 고유 채널
            total_quota = 100 + 1 + estimated_channels
            percent = round(total_quota / 10000 * 100, 2)
            quota_text = f"💰 예상 쿼터: 검색 100 + 영상정보 1 + 채널정보(약 {estimated_channels}) = 약 {total_quota} 유닛 ({percent}%)"
            self.search_quota_label.config(text=quota_text)
        except:
            pass

    def _update_channel_quota_display(self):
        """채널 쿼터 비용 표시 업데이트 (선택된 채널 수 반영)"""
        try:
            # 선택된 채널 수 계산
            selected_count = sum(1 for var, _ in self.channel_checkboxes.values() if var.get())

            if self.channel_all_videos_var.get():
                video_count = "전체"
            else:
                video_count = self.channel_video_count_var.get()

            if video_count == "전체":
                if selected_count == 0:
                    quota_text = "💰 예상 쿼터: 영상 수에 따라 다름 (예: 200개 = 9 유닛, 500개 = 21 유닛) - 업로드 재생목록 사용"
                else:
                    quota_text = f"💰 예상 쿼터: 선택된 채널 {selected_count}개 - 영상 수에 따라 다름 (예: 채널당 200개 = 9 유닛/채널)"
            else:
                pages = (video_count + 49) // 50
                batches = (video_count + 49) // 50
                total_quota_per_channel = pages + batches + 1

                if selected_count == 0:
                    percent = round(total_quota_per_channel / 10000 * 100, 2)
                    quota_text = f"💰 예상 쿼터: 재생목록조회 {pages} + 영상정보 {batches} + 채널정보 1 = {total_quota_per_channel} 유닛 ({percent}%) ⭐ 97% 절감!"
                else:
                    total_quota = total_quota_per_channel * selected_count
                    percent = round(total_quota / 10000 * 100, 2)
                    quota_text = f"💰 예상 쿼터: 선택된 채널 {selected_count}개 × {total_quota_per_channel} 유닛 = {total_quota} 유닛 ({percent}%) ⭐ 97% 절감!"

            self.channel_quota_label.config(text=quota_text)

            # 채널 영상 가져오기 대상 채널 개수 업데이트
            if selected_count == 0:
                self.channel_fetch_count_label.config(text="대상 채널: 0개 (채널을 선택해주세요)")
            else:
                self.channel_fetch_count_label.config(text=f"대상 채널: {selected_count}개")

            # 채널 정보 업데이트 대상 채널 개수 업데이트
            if selected_count == 0:
                self.channel_update_count_label.config(text="대상 채널: 0개 (채널을 선택해주세요)")
            else:
                self.channel_update_count_label.config(text=f"대상 채널: {selected_count}개")

        except:
            pass

    def _update_new_videos_for_channels(self, days_threshold: Optional[int]):
        """
        조건 부합 채널의 신규 영상 업데이트

        Args:
            days_threshold: 수집날짜 경과 일수 (None이면 모든 채널)
        """
        from datetime import datetime, timedelta

        self.is_searching = True
        self.interrupt_requested = False
        self.channel_button.config(state='disabled')
        self.playlist_button.config(state='disabled')
        self.search_button.config(state='disabled')
        self.update_channel_info_button.config(state='disabled')
        self.update_new_videos_button.config(state='disabled')
        self.progress_bar.start()

        try:
            self._log("=" * 50)
            self._log("[시작] 신규 영상 업데이트 시작")
            self._log("=" * 50)

            # 1. 채널 리스트에서 조건 부합 채널 찾기
            self._log("▶ 시트: '채널 리스트' 시트에서 채널 정보 읽기 중...")
            all_channels = self.sheets_manager.get_channel_data('채널 리스트')
            self._log(f"  - 전체 채널: {len(all_channels)}개")

            # 전체 채널의 'fetched' 값 분포 확인
            if all_channels:
                fetched_values = {}
                for ch in all_channels:
                    fetched_val = ch.get('fetched', '')
                    fetched_values[fetched_val] = fetched_values.get(fetched_val, 0) + 1

                self._log("  - '가져왔는지 여부' 열 값 분포:")
                for val, count in sorted(fetched_values.items()):
                    display_val = f"'{val}'" if val else "(빈값)"
                    self._log(f"    • {display_val}: {count}개")

            # 가져왔는지 여부가 'ㅇ'이거나 빈값이 아닌 채널만 필터링
            self._log("▶ 필터링: '가져왔는지 여부' 열이 'ㅇ'이거나 비어있지 않은 채널만 추출 중...")
            fetched_channels = []
            for ch in all_channels:
                fetched_val = ch.get('fetched', '')
                # 'ㅇ' 또는 빈값이 아닌 경우 포함
                if fetched_val and fetched_val.strip():
                    fetched_channels.append(ch)
            self._log(f"  - 가져왔는지 여부에 값이 있는 채널: {len(fetched_channels)}개")

            # 일수 필터 적용
            self._log("▶ 경과 일수 필터 확인 중...")
            if days_threshold is None:
                self._log(f"  - 필터 사용 여부: 미사용")
                self._log(f"  - 전체 채널 대상")
            else:
                self._log(f"  - 필터 사용 여부: 사용")
                self._log(f"  - 기준 경과 일수: {days_threshold}일 이상")
                self._log(f"  - 비교 대상 열: '수집날짜' 열")

            target_channels = []
            today = datetime.now().date()
            self._log(f"▶ 기준 날짜: {today} (오늘)")
            self._log("▶ 채널별 경과 일수 계산 중...")
            self._log("")

            for ch in fetched_channels:
                channel_name = ch.get('channel_name', '(이름 없음)')
                collection_date_str = ch.get('collection_date', '')

                if days_threshold is None:
                    # 필터 없음 - 모든 채널
                    target_channels.append(ch)
                    self._log(f"  ✓ {channel_name}: 필터 미사용 - 포함")
                else:
                    # 수집날짜 확인
                    if collection_date_str:
                        try:
                            # 날짜 파싱 (한국어 형식 지원)
                            collection_datetime = self.sheets_manager.parse_korean_datetime(collection_date_str)

                            if collection_datetime:
                                collection_date = collection_datetime.date()
                                days_passed = (today - collection_date).days

                                if days_passed >= days_threshold:
                                    target_channels.append(ch)
                                    self._log(f"  ✓ {channel_name}")
                                    self._log(f"    - 수집날짜 (원본): '{collection_date_str}'")
                                    self._log(f"    - 수집날짜 (파싱): {collection_date}")
                                    self._log(f"    - 경과 일수: {days_passed}일")
                                    self._log(f"    - 판정: 조건 부합 ({days_passed}일 >= {days_threshold}일)")
                                else:
                                    self._log(f"  ✗ {channel_name}")
                                    self._log(f"    - 수집날짜: {collection_date}")
                                    self._log(f"    - 경과 일수: {days_passed}일")
                                    self._log(f"    - 판정: 조건 미달 ({days_passed}일 < {days_threshold}일)")
                            else:
                                target_channels.append(ch)
                                self._log(f"  ✓ {channel_name}")
                                self._log(f"    - 수집날짜 (원본): '{collection_date_str}'")
                                self._log(f"    - 판정: 날짜 파싱 실패 → 포함 처리")
                        except Exception as e:
                            target_channels.append(ch)
                            self._log(f"  ✓ {channel_name}")
                            self._log(f"    - 수집날짜 (원본): '{collection_date_str}'")
                            self._log(f"    - 판정: 예외 발생 ({str(e)}) → 포함 처리")
                    else:
                        # 수집날짜 없으면 포함
                        target_channels.append(ch)
                        self._log(f"  ✓ {channel_name}")
                        self._log(f"    - 수집날짜: (없음)")
                        self._log(f"    - 판정: 수집날짜 없음 → 포함 처리")

            if not target_channels:
                self._log("=" * 50)
                self._log("[결과] 조건 부합 채널이 없습니다.")
                self._log("=" * 50)
                messagebox.showinfo("알림", "조건 부합 채널이 없습니다.")
                return

            self._log("=" * 50)
            self._log(f"[결과] 최종 처리 대상 채널: {len(target_channels)}개")
            self._log("=" * 50)

            # 2. 각 채널별로 신규 영상 추가 및 정보 업데이트
            success_count = 0
            fail_count = 0
            total_new_videos = 0
            total_updated_videos = 0
            total_quota_used = 0

            for idx, channel in enumerate(target_channels, 1):
                # 중단 요청 확인
                if self.interrupt_requested:
                    self._log(f"[중단됨] {idx-1}/{len(target_channels)}개 채널 처리 후 중단")
                    break

                try:
                    self._log(f"[{idx}/{len(target_channels)}] {channel['channel_name']} 처리 중...")

                    channel_id = channel['channel_id']
                    channel_row = channel['row']

                    # 2-1. 신규 영상 가져오기 (최신 영상만)
                    try:
                        # 최신 50개 영상 가져오기 (충분한 양)
                        results, stats = self.youtube_api.get_channel_videos(channel_id, max_results=50, order='date')
                        total_quota_used += stats['quota_cost']

                        self._log(f"  - 영상 {len(results)}개 조회 완료 (쿼터: {stats['quota_cost']} 유닛)")

                        if not results:
                            self._log(f"  - 채널에 영상이 없습니다.")
                            continue

                    except Exception as api_error:
                        error_str = str(api_error)
                        if 'quota' in error_str.lower() or 'quotaExceeded' in error_str:
                            self._log("=" * 50)
                            self._log("[오류] YouTube API 쿼터 초과!")
                            self._log("=" * 50)
                            messagebox.showerror("쿼터 초과", "YouTube API 쿼터가 초과되었습니다.")
                            break
                        else:
                            raise

                    # 2-2. 채널 추가 정보 병합
                    channel_extra_info = {
                        '분야1': channel.get('category1', ''),
                        '분야2': channel.get('category2', ''),
                        '사용언어': channel.get('language', ''),
                        '재생목록 이름': channel.get('playlist_name', ''),
                        '채널명': channel.get('channel_name', '')
                    }

                    for video_data in results:
                        # 검색 키워드 제거
                        if '검색 키워드' in video_data:
                            del video_data['검색 키워드']
                        # 채널 정보 병합
                        video_data.update(channel_extra_info)

                    # 2-3. 영상 리스트 시트에 신규 영상 추가
                    existing_video_ids = self.sheets_manager.get_all_video_ids('영상 리스트')

                    new_videos = []
                    duplicate_videos = []

                    for video in results:
                        video_id = video.get('영상 ID')
                        if video_id and video_id not in existing_video_ids:
                            new_videos.append(video)
                        else:
                            duplicate_videos.append(video)

                    self._log(f"  - 신규 영상: {len(new_videos)}개, 중복: {len(duplicate_videos)}개")

                    if new_videos:
                        self.sheets_manager.bulk_append_data('영상 리스트', new_videos, start_row=10)
                        total_new_videos += len(new_videos)
                        self._log(f"  - 신규 영상 {len(new_videos)}개 추가 완료")

                    # 2-4. 채널 리스트 정보 업데이트 (구독자수, 영상갯수 등)
                    if results:
                        latest_video = results[0]  # 최신 영상
                        update_data = {
                            '구독자수': latest_video.get('구독자수'),
                            '채널전체 영상갯수': latest_video.get('영상갯수'),
                            '채널전체 조회수': latest_video.get('채널 전체 조회수'),
                            '수집날짜': datetime.now().strftime('%Y-%m-%d'),
                            '채널국가': latest_video.get('채널국가'),
                            '개설일': latest_video.get('채널 개설일'),
                            '채널 디스크립션': latest_video.get('채널 디스크립션'),
                            '채널 핸들': latest_video.get('채널 핸들')
                        }

                        self.sheets_manager.update_channel_info('채널 리스트', channel_row, update_data)
                        self._log(f"  - 채널 정보 업데이트 완료")

                    # 2-5. 영상 리스트의 해당 채널 영상들 정보 업데이트
                    if duplicate_videos:
                        # 중복 영상들의 정보 업데이트 (조회수, 좋아요 등)
                        video_id_to_row = self.sheets_manager.get_video_id_to_row_mapping('영상 리스트')

                        updates = []
                        for video in duplicate_videos:
                            video_id = video.get('영상 ID')
                            if video_id and video_id in video_id_to_row:
                                row = video_id_to_row[video_id]
                                updates.append((row, video))

                        if updates:
                            self.sheets_manager.bulk_update_video_rows('영상 리스트', updates)
                            total_updated_videos += len(updates)
                            self._log(f"  - 기존 영상 {len(updates)}개 정보 업데이트 완료")

                    success_count += 1

                    # API 과부하 방지: 각 채널 처리 후 잠시 대기
                    if idx < len(target_channels):
                        import time
                        time.sleep(2)

                except Exception as e:
                    self._log(f"  - 오류: {str(e)}")
                    fail_count += 1

            # 최종 결과
            self._log("=" * 50)
            self._log("[완료] 신규 영상 업데이트 완료")
            self._log(f"- 처리 성공: {success_count}개 채널")
            self._log(f"- 처리 실패: {fail_count}개 채널")
            self._log(f"- 신규 영상 추가: {total_new_videos}개")
            self._log(f"- 기존 영상 업데이트: {total_updated_videos}개")
            self._log(f"- 총 쿼터 사용: {total_quota_used} 유닛")
            self._log("=" * 50)

            result_msg = (
                f"신규 영상 업데이트 완료\n\n"
                f"처리 성공: {success_count}개 채널\n"
                f"처리 실패: {fail_count}개 채널\n\n"
                f"신규 영상 추가: {total_new_videos}개\n"
                f"기존 영상 업데이트: {total_updated_videos}개\n\n"
                f"쿼터 사용: {total_quota_used} 유닛"
            )

            self.root.bell()
            messagebox.showinfo("완료", result_msg)

        except Exception as e:
            error_msg = f"신규 영상 업데이트 오류: {str(e)}"
            self._log(error_msg)
            messagebox.showerror("오류", error_msg)

        finally:
            self.is_searching = False
            self.channel_button.config(state='normal')
            self.playlist_button.config(state='normal')
            self.search_button.config(state='normal')
            self.update_channel_info_button.config(state='normal')
            self.update_new_videos_button.config(state='normal')
            self.progress_bar.stop()

    def run(self):
        """GUI 실행"""
        self.root.mainloop()
