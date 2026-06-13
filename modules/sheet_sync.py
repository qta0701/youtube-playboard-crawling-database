"""
구글 스프레드시트 탭과 SQLite3 DB 연동 모듈
양방향 동기화 및 9행 수식 보존
"""
import os
import sqlite3
import pandas as pd
import gspread
from google.oauth2.service_account import Credentials
from datetime import datetime
from logger_config import setup_logger
from modules.utils import (
    normalize_header_name,
    match_db_column_by_header,
    calculate_sheet_video_metrics,
    calculate_sheet_channel_metrics,
    parse_date_string
)
from modules.database import DatabaseHandler

logger = setup_logger('sheet_sync')

# 구글 시트 탭이름 -> DB 테이블명 매핑
TAB_MAPPING = {
    "영상 리스트": "sheet_videos",
    "사용 레퍼런스 영상": "sheet_videos",
    "유튜브 재생목록": "sheet_videos",
    "조건 추출 영상": "sheet_videos",
    "키워드 검색결과": "sheet_videos",
    "채널 리스트": "sheet_channels",
    "재생목록ID": "sheet_playlist_ids"
}


def is_new_date_newer(old_date_str, new_date_str):
    """
    old_date_str과 new_date_str을 비교하여 new_date_str이 더 최신(크면) True를 반환.
    동일하거나 오래되었으면 False 반환.
    파싱 실패 시 보수적으로 True를 반환하여 업데이트가 차단되지 않도록 함.
    """
    if not old_date_str or str(old_date_str).strip() in ['N/A', '', 'None']:
        return True
    if not new_date_str or str(new_date_str).strip() in ['N/A', '', 'None']:
        return False
        
    try:
        # 먼저 날짜 객체로 파싱 시도
        old_date = parse_date_string(old_date_str)
        new_date = parse_date_string(new_date_str)
        if old_date and new_date:
            if new_date != old_date:
                return new_date > old_date
    except Exception:
        pass
        
    # 만약 date 객체 비교로 구분이 불가능하거나 동일한 날짜인 경우, 문자열 사전식 비교 시도
    return str(new_date_str) > str(old_date_str)


def format_datetime_for_sheet(val):
    """
    ISO 8601 형식의 날짜 문자열(예: 2025-05-24T03:16:00.971228Z)을
    구글 시트가 정상적으로 날짜로 인식하여 기존 서식을 따를 수 있도록
    T와 Z를 제거하고 YYYY-MM-DD HH:MM:SS 형태로 포맷팅합니다.
    """
    if not val or not isinstance(val, str):
        return val
    # URL 형식인 경우 날짜 변환 방지
    if val.startswith('http://') or val.startswith('https://'):
        return val
    # 연도-월-일 포맷으로 시작하지 않는 경우 날짜 변환 방지
    import re
    if not re.match(r'^\d{4}-\d{2}-\d{2}', val):
        return val
        
    # ISO 8601 문자열 검사 (T가 포함되어 있고 Z가 끝에 있거나 timezone offset이 있는 경우)
    if 'T' in val and (val.endswith('Z') or '+' in val or '-' in val.split('T')[1]):
        try:
            clean_val = val
            if clean_val.endswith('Z'):
                clean_val = clean_val[:-1] + '+00:00'
            dt = datetime.fromisoformat(clean_val)
            return dt.strftime('%Y-%m-%d %H:%M:%S')
        except Exception:
            try:
                temp = val.replace('T', ' ')
                if '.' in temp:
                    temp = temp.split('.')[0]
                elif 'Z' in temp:
                    temp = temp.replace('Z', '')
                return temp.strip()
            except Exception:
                return val
    return val


def get_tab_header_row_num(tab_name):
    """탭별 진짜 헤더가 위치한 행 번호 (1-indexed)"""
    if tab_name == "재생목록ID":
        return 1
    return 9


def get_tab_data_start_row_idx(tab_name):
    """탭별 실제 데이터가 시작되는 리스트 인덱스 (0-indexed)"""
    if tab_name == "재생목록ID":
        return 1  # 2행부터
    return 9  # 10행부터


def get_creds_path(custom_path=None):
    """
    서비스 계정 키 파일 경로 탐색
    - google_service_key/ 디렉토리 내 통합된 JSON 키 파일을 참조합니다.
    """
    if custom_path and os.path.exists(custom_path):
        return custom_path

    # 디폴트 탐색 경로 (절대경로 및 폴백)
    paths = [
        "google_service_key/service-account-key.json",
        "../google_service_key/service-account-key.json",
        "../../google_service_key/service-account-key.json",
    ]
    for p in paths:
        if os.path.exists(p):
            return os.path.abspath(p)

    # 전체 하위 디렉토리 내 탐색
    for root, dirs, files in os.walk('.'):
        if 'service-account-key.json' in files:
            return os.path.abspath(os.path.join(root, 'service-account-key.json'))

    return None


def get_gspread_client(creds_path=None):
    """gspread 클라이언트 인증 및 반환"""
    path = get_creds_path(creds_path)
    if not path:
        raise FileNotFoundError(
            "구글 서비스 계정 키 파일(service-account-key.json)을 프로젝트 내에서 찾을 수 없습니다. "
            "google_service_key/ 폴더 아래에 배치해주세요."
        )

    scopes = [
        'https://spreadsheets.google.com/feeds',
        'https://www.googleapis.com/auth/drive'
    ]
    credentials = Credentials.from_service_account_file(path, scopes=scopes)
    return gspread.authorize(credentials)


def sync_sheet_to_db(sheet_url, creds_path=None, target_tab=None):
    """
    구글 시트의 데이터를 읽어 DB로 동기화 (가져오기)
    - 탭별 구조(재생목록ID는 2행부터, 나머지는 10행부터)를 감지하여 유연하게 이식합니다.
    """
    gc = get_gspread_client(creds_path)
    sh = gc.open_by_url(sheet_url)

    db = DatabaseHandler()
    conn = db.conn
    cursor = conn.cursor()

    tabs_to_sync = [target_tab] if target_tab else list(TAB_MAPPING.keys())
    sync_results = {}

    for tab in tabs_to_sync:
        try:
            logger.info(f"구글 시트 '{tab}' 탭 (URL: {sheet_url}) -> DB 연동 시작...")
            worksheet = sh.worksheet(tab)

            header_row = get_tab_header_row_num(tab)
            data_start_idx = get_tab_data_start_row_idx(tab)

            # 진짜 헤더 가져오기
            headers = worksheet.row_values(header_row)
            if not headers:
                logger.warning(f"'{tab}' 탭 (URL: {sheet_url})의 헤더 정보가 비어 있습니다. 동기화를 건너뜁니다.")
                continue

            # 전체 행 가져오기
            all_values = worksheet.get_all_values()
            if len(all_values) <= data_start_idx:
                logger.info(f"'{tab}' 탭 (URL: {sheet_url})에 동기화할 데이터가 존재하지 않습니다.")
                sync_results[tab] = 0
                continue

            data_rows = all_values[data_start_idx:]
            table_name = TAB_MAPPING.get(tab)

            # DB 테이블의 실제 컬럼명 획득
            cursor.execute(f"PRAGMA table_info({table_name})")
            db_cols = [col['name'] for col in cursor.fetchall()]

            # 구글 시트 열 인덱스 -> DB 컬럼명 매핑 사전 생성
            header_to_col_idx = {}
            for idx, h in enumerate(headers):
                db_col = match_db_column_by_header(h, db_cols)
                if db_col:
                    header_to_col_idx[db_col] = idx

            # 1. DB의 기존 데이터 캐싱 (대량 업데이트 성능 최적화)
            db_existing_cache = {}
            if table_name == "sheet_videos":
                cursor.execute("SELECT video_id, crawl_date FROM sheet_videos WHERE tab_name = ?", (tab,))
                db_existing_cache = {row[0]: row[1] for row in cursor.fetchall() if row[0]}
            elif table_name == "sheet_channels":
                cursor.execute("SELECT channel_id, crawl_date FROM sheet_channels")
                db_existing_cache = {row[0]: row[1] for row in cursor.fetchall() if row[0]}
            elif table_name == "sheet_playlist_ids":
                cursor.execute("SELECT playlist_id, last_checked_at FROM sheet_playlist_ids")
                db_existing_cache = {row[0]: row[1] for row in cursor.fetchall() if row[0]}

            processed_count = 0
            skipped_count = 0
            for row_idx, row in enumerate(data_rows):
                # 데이터가 헤더보다 짧은 경우 패딩 처리
                if len(row) < len(headers):
                    row += [''] * (len(headers) - len(row))

                # 한 행의 딕셔너리 데이터 구성
                row_dict = {}
                for db_col, idx in header_to_col_idx.items():
                    row_dict[db_col] = row[idx]

                # 고유 키 식별 및 데이터 정제 및 날짜 비교
                is_exist = False
                db_crawl_date = None
                entity_id = None

                if table_name == "sheet_videos":
                    vid = row_dict.get('video_id')
                    if not vid or str(vid).strip() == '':
                        continue
                    entity_id = vid
                    row_dict['tab_name'] = tab
                    row_dict['original_row_order'] = row_idx + data_start_idx + 1
                    
                    if vid in db_existing_cache:
                        is_exist = True
                        db_crawl_date = db_existing_cache[vid]
                        
                    # 파이썬 기반 통계 자동 계산
                    row_dict = calculate_sheet_video_metrics(row_dict)
                    
                elif table_name == "sheet_channels":
                    cid = row_dict.get('channel_id')
                    if not cid or str(cid).strip() == '' or cid == 'UC_BULK_TEST_9999':
                        continue
                    entity_id = cid
                    row_dict['original_row_order'] = row_idx + data_start_idx + 1
                    
                    if cid in db_existing_cache:
                        is_exist = True
                        db_crawl_date = db_existing_cache[cid]
                        
                    # 파이썬 기반 통계 자동 계산
                    row_dict = calculate_sheet_channel_metrics(row_dict)
                    
                elif table_name == "sheet_playlist_ids":
                    pid = row_dict.get('playlist_id')
                    if not pid or str(pid).strip() == '':
                        continue
                    entity_id = pid
                    row_dict['original_row_order'] = row_idx + data_start_idx + 1
                    # 마지막 체크일 헤더열은 업데이트 시간(현재 실행 시간)을 기준으로 지정
                    row_dict['last_checked_at'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                    
                    if pid in db_existing_cache:
                        is_exist = True
                        db_crawl_date = db_existing_cache[pid]

                # DB에 저장된 기존 수집날짜가 더 최신이거나 같다면 업데이트 생략
                new_date_str = row_dict.get('crawl_date') if table_name != "sheet_playlist_ids" else row_dict.get('last_checked_at')
                if is_exist:
                    if not is_new_date_newer(db_crawl_date, new_date_str):
                        skipped_count += 1
                        logger.debug(f"-> 건너뜀: {table_name}의 ID {entity_id}는 DB의 수집날짜({db_crawl_date})가 시트 날짜({new_date_str})보다 최신이거나 같음")
                        continue

                # SQLite INSERT OR REPLACE (Upsert) 실행
                columns = list(row_dict.keys())
                placeholders = ', '.join(['?'] * len(columns))
                sql = f"INSERT OR REPLACE INTO {table_name} ({', '.join(columns)}) VALUES ({placeholders})"
                cursor.execute(sql, [row_dict[col] for col in columns])
                
                # 캐시 및 카운트 갱신
                db_existing_cache[entity_id] = new_date_str
                processed_count += 1

            conn.commit()
            if skipped_count > 0:
                logger.info(f"-> 중복 또는 오래된 데이터 {skipped_count}건 업데이트 건너뜀 (최신 유지)")
            logger.info(f"✓ '{tab}' 탭 동기화 완료 (URL: {sheet_url}): {processed_count}개 행 적재 완료 ({table_name})")
            sync_results[tab] = processed_count

        except gspread.WorksheetNotFound:
            logger.warning(f"⚠️ 스프레드시트 (URL: {sheet_url})에 '{tab}' 탭이 존재하지 않습니다.")
            sync_results[tab] = "WorksheetNotFound"
        except Exception as e:
            logger.error(f"❌ '{tab}' 탭 동기화 중 오류 발생 (URL: {sheet_url}): {e}", exc_info=True)
            sync_results[tab] = f"Error: {str(e)}"

    return sync_results


def sync_db_to_sheet(sheet_url, tab_name, creds_path=None):
    """
    DB 테이블의 데이터를 구글 시트에 업데이트 (보내기)
    - 9행의 본래 수식 포맷은 훼손 없이 보존하고, 데이터 영역부터 데이터를 갱신합니다.
    - 1행 헤더 순서를 동적으로 매칭하여 알맞은 열 위치에 정밀 기입합니다.
    """
    table_name = TAB_MAPPING.get(tab_name)
    if not table_name:
        raise ValueError(f"해당 탭 '{tab_name}'에 매핑된 DB 테이블이 없습니다.")

    gc = get_gspread_client(creds_path)
    sh = gc.open_by_url(sheet_url)
    worksheet = sh.worksheet(tab_name)

    db = DatabaseHandler()
    conn = db.conn
    cursor = conn.cursor()

    header_row = get_tab_header_row_num(tab_name)
    data_start_row = get_tab_data_start_row_idx(tab_name) + 1  # 1-indexed 행 번호

    # 진짜 헤더 가져오기
    headers = worksheet.row_values(header_row)
    if not headers:
        raise ValueError(f"시트 '{tab_name}' 탭의 헤더가 비어 있습니다.")

    # 9행(혹은 1행) 헤더 기준 전역 수식(FORMULA)이 걸려있는 컬럼 식별
    formula_col_indices = []
    try:
        headers_formula = worksheet.row_values(header_row, value_render_option='FORMULA')
        formula_col_indices = [idx for idx, cell in enumerate(headers_formula) if str(cell).startswith('=')]
        if formula_col_indices:
            logger.info(f"구글 시트 '{tab_name}' 탭에서 수식 컬럼 감지 (0-indexed): {formula_col_indices}")
    except Exception as formula_err:
        logger.warning(f"⚠️ 헤더 수식 컬럼 감지 중 예외 발생: {formula_err}")

    # DB 테이블의 실제 컬럼명 조회
    cursor.execute(f"PRAGMA table_info({table_name})")
    db_cols = [col['name'] for col in cursor.fetchall()]

    # 구글 시트 각 열 위치에 대응하는 DB 컬럼 리스트 구성 (순서 일치)
    col_mappings = []
    for h in headers:
        db_col = match_db_column_by_header(h, db_cols)
        col_mappings.append(db_col)

    # 1. 시트의 기존 데이터 읽어오기 (날짜 비교용)
    all_values = worksheet.get_all_values()
    sheet_existing_rows = {}
    
    # 테이블별 고유 ID 컬럼 및 날짜 컬럼의 시트 인덱스 찾기
    id_col_name = 'video_id' if table_name == 'sheet_videos' else ('channel_id' if table_name == 'sheet_channels' else 'playlist_id')
    date_col_name = 'crawl_date' if table_name != 'sheet_playlist_ids' else 'last_checked_at'
    
    id_sheet_idx = None
    date_sheet_idx = None
    for idx, col_name in enumerate(col_mappings):
        if col_name == id_col_name:
            id_sheet_idx = idx
        if col_name == date_col_name:
            date_sheet_idx = idx

    # 기존 시트 데이터를 딕셔너리로 빌드
    if len(all_values) >= data_start_row:
        for row_data in all_values[data_start_row - 1:]:
            if id_sheet_idx is not None and len(row_data) > id_sheet_idx:
                row_id = row_data[id_sheet_idx]
                if row_id and str(row_id).strip() != '':
                    row_dict = {}
                    for col_idx, col_name in enumerate(col_mappings):
                        if col_name and len(row_data) > col_idx:
                            row_dict[col_name] = row_data[col_idx]
                    sheet_existing_rows[row_id] = row_dict

    # 2. DB 데이터 가져오기
    if table_name == "sheet_videos":
        cursor.execute("SELECT * FROM sheet_videos WHERE tab_name = ? ORDER BY original_row_order ASC", (tab_name,))
    else:
        cursor.execute(f"SELECT * FROM {table_name} ORDER BY original_row_order ASC")

    db_rows = cursor.fetchall()

    # 3. 데이터 병합 (시트 날짜가 더 최신이면 유지, DB가 더 최신이면 DB 데이터 적용)
    merged_rows = {}
    for r_id, r_dict in sheet_existing_rows.items():
        merged_rows[r_id] = dict(r_dict)

    for db_row in db_rows:
        row_dict = dict(db_row)
        # 쓰기 직전 필요한 경우 파생 데이터 재계산
        if table_name == "sheet_videos":
            row_dict = calculate_sheet_video_metrics(row_dict)
        elif table_name == "sheet_channels":
            row_dict = calculate_sheet_channel_metrics(row_dict)
        elif table_name == "sheet_playlist_ids":
            # 내보내기(업데이트) 기준의 현재 시간으로 갱신
            current_time_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            row_dict['last_checked_at'] = current_time_str
            # DB에도 마지막 체크일 최신화 반영
            try:
                cursor.execute(
                    "UPDATE sheet_playlist_ids SET last_checked_at = ? WHERE playlist_id = ?",
                    (current_time_str, row_dict['playlist_id'])
                )
            except Exception as db_up_err:
                logger.debug(f"Failed to update last_checked_at in DB: {db_up_err}")

        r_id = row_dict.get(id_col_name)
        if not r_id or str(r_id).strip() == '' or r_id == 'UC_BULK_TEST_9999':
            continue

        if r_id in sheet_existing_rows:
            sheet_date = sheet_existing_rows[r_id].get(date_col_name)
            db_date = row_dict.get(date_col_name)
            if not is_new_date_newer(sheet_date, db_date):
                # 기존 시트 데이터가 더 최신이거나 같으면 DB 데이터를 쓰지 않고 건너뜀
                continue

        merged_rows[r_id] = row_dict

    # 4. 정렬 순서대로 2차원 리스트 복원
    def sort_key(item):
        val = item[1].get('original_row_order')
        if val is None or str(val).strip() == '':
            return 9999999
        try:
            return int(val)
        except ValueError:
            return 9999999

    sorted_merged_rows = sorted(merged_rows.items(), key=sort_key)

    # 2차원 리스트 형태의 데이터 준비
    sheet_data = []
    for r_id, row_dict in sorted_merged_rows:
        sheet_row = []
        for col_idx, col_name in enumerate(col_mappings):
            if col_idx in formula_col_indices:
                sheet_row.append("")  # 수식이 지정된 열은 자동 계산되므로 빈 셀로 설정하여 업데이트 생략
            elif col_name:
                val = row_dict.get(col_name, "")
                if val is None:
                    val = ""
                val = format_datetime_for_sheet(val)
                sheet_row.append(str(val))
            else:
                sheet_row.append("")  # DB와 매치되지 않는 시트 열은 빈 셀로 둠
        sheet_data.append(sheet_row)

    # 데이터 영역부터 끝까지 범위 클리어
    logger.info(f"구글 시트 '{tab_name}' (URL: {sheet_url})의 {data_start_row}행 이하 셀 데이터 클리어 중...")
    worksheet.batch_clear([f"A{data_start_row}:ZZ50000"])

    if sheet_data:
        # 끝 열 알파벳 구하기
        col_count = len(headers)

        def get_col_letter(col_idx):
            letter = ""
            while col_idx > 0:
                col_idx, remainder = divmod(col_idx - 1, 26)
                letter = chr(65 + remainder) + letter
            return letter

        end_col_letter = get_col_letter(col_count)
        
        # 1,000행 단위 청크 분할 전송 최적화 (대량 업데이트 Quota/Payload 제한 방지)
        chunk_size = 1000
        total_rows = len(sheet_data)
        
        logger.info(f"시트 '{tab_name}' (URL: {sheet_url}) {data_start_row}행부터 총 {total_rows}개 행을 {chunk_size}행 단위로 분할 업데이트 중...")
        
        for start_i in range(0, total_rows, chunk_size):
            chunk = sheet_data[start_i:start_i + chunk_size]
            chunk_start_row = data_start_row + start_i
            chunk_end_row = chunk_start_row + len(chunk) - 1
            chunk_range = f"A{chunk_start_row}:{end_col_letter}{chunk_end_row}"
            
            logger.info(f"-> 청크 전송: {chunk_range} ({len(chunk)}개 행)")
            worksheet.update(chunk_range, chunk, value_input_option='USER_ENTERED')
            
        # 기존 구글 시트 열 서식 복사 적용 (10행 기준, 재생목록ID 탭은 2행 기준)
        try:
            sheet_id = getattr(worksheet, 'id', worksheet._properties.get('sheetId'))
            source_row_idx = 1 if tab_name == "재생목록ID" else 9
            dest_start_row_idx = source_row_idx + 1 # 그 바로 다음 행부터
            dest_end_row_idx = data_start_row + total_rows  # 마지막 데이터 행
            
            if dest_end_row_idx > dest_start_row_idx:
                logger.info(f"구글 시트 '{tab_name}' 서식 복사 적용: 소스 {source_row_idx + 1}행 -> 대상 {dest_start_row_idx + 1}행~{dest_end_row_idx}행")
                body = {
                    "requests": [
                        {
                            "copyPaste": {
                                "source": {
                                    "sheetId": sheet_id,
                                    "startRowIndex": source_row_idx,
                                    "endRowIndex": source_row_idx + 1,
                                    "startColumnIndex": 0,
                                    "endColumnIndex": col_count
                                },
                                "destination": {
                                    "sheetId": sheet_id,
                                    "startRowIndex": dest_start_row_idx,
                                    "endRowIndex": dest_end_row_idx,
                                    "startColumnIndex": 0,
                                    "endColumnIndex": col_count
                                },
                                "pasteType": "PASTE_FORMAT",
                                "pasteOrientation": "NORMAL"
                            }
                        }
                    ]
                }
                sh.batch_update(body)
                logger.info("✓ 구글 시트 서식 복사 적용 완료")
        except Exception as format_err:
            logger.warning(f"⚠️ 구글 시트 서식 복사 중 오류 발생: {format_err}")
            
        logger.info(f"✓ 구글 시트 '{tab_name}' (URL: {sheet_url}) 내보내기 완료")
        return total_rows

    return 0
