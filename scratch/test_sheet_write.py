import os
import sys
import json
import time

current_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.abspath(os.path.join(current_dir, ".."))
sys.path.append(project_root)
sys.path.append(os.path.join(project_root, "외부프로그램", "롱폼-유튜브검색기"))

# 격리 모듈 동적 로드
from modules.external_loader import load_isolated_module

gui_file = os.path.join(project_root, "외부프로그램", "롱폼-유튜브검색기", "GUI_Interface.py")
ext_gui = load_isolated_module("ext_search_gui_module", gui_file, os.path.dirname(gui_file))

def check_sheet_write():
    # 자격증명 경로 및 시트 URL
    creds_path = os.path.join(project_root, "google_service_key", "service-account-key.json")
    sheet_url = 'https://docs.google.com/spreadsheets/d/1o-Vm5eMfH6Mm1gCnsv-SuRqtVMYTBDnWThBxjwCOptY/edit#gid=739326708'
    sheet_name = '키워드 검색결과'

    if not os.path.exists(creds_path):
        print(f"오류: 자격증명 파일을 찾을 수 없습니다: {creds_path}")
        return

    try:
        sheets_manager = ext_gui.GoogleSheetsManager(creds_path, sheet_url)
        print("gspread 인증 성공!")
        
        # 1. 10행 서식을 가져와 복사해보기 (서식 API 테스트)
        worksheet = sheets_manager.get_or_create_worksheet(sheet_name)
        headers = sheets_manager.get_headers(sheet_name)
        
        # 더미 데이터 생성
        test_video_data = {
            '영상 ID': 'TEST_VIDEO_ID_9999',
            '영상 업로드날짜': '2026-06-16',
            '수집날짜': '2026-06-16 16:15:00',
            '검색 키워드': 'Test Integration',
            '영상 링크': 'https://www.youtube.com/watch?v=TEST_VIDEO_ID_9999',
            '제목': '[테스트] 서식 및 누락 컬럼 자동 기입 테스트',
            '조회수': 50000,
            '영상길이': '1분 30초',
            '구독자수': 10000,
            '좋아요 수': 500,
            '댓글수': 50,
            '구독자 대비 조회수 배율': 5.0,
            '조회수 대비 좋아요': 0.01,
            '조회수 대비 댓글': 0.001,
            '채널명': '테스트 채널',         # 11번, 53번 둘 다 들어가야 함
            '33. 채널명': '테스트 채널',
            '채널국가': 'US',
            '채널 ID': 'UC_TEST_CHANNEL_9999',
            '채널링크': 'https://www.youtube.com/channel/UC_TEST_CHANNEL_9999',
            '영상갯수': 100,
            '채널 전체 조회수': 1000000,
            '영상당 평균 조회수': 10000,
            '채널 개설일': '2020-01-01',
            '카테고리 ID': '22',
            '디스크립션': '이것은 디스크립션 컬럼에 제대로 들어가는지 확인하기 위한 테스트 문구입니다.',
            '디스크립션 텍스트 수': '52자',
            '해시태그 유무': 'ㅇ',
            '썸네일 링크': 'https://i.ytimg.com/vi/TEST_VIDEO_ID_9999/hqdefault.jpg',
            '썸네일 이미지주소': 'https://i.ytimg.com/vi/TEST_VIDEO_ID_9999/hqdefault.jpg'
        }
        
        # 기존에 존재하는지 검사해서 업데이트 혹은 삽입
        col_a_values = sheets_manager._retry_on_rate_limit(worksheet.col_values, 1)
        target_row = None
        for idx, val in enumerate(col_a_values, 1):
            if str(val).strip() == 'TEST_VIDEO_ID_9999':
                target_row = idx
                break
                
        # 수식 제외
        from sheet_config import SheetType, get_formula_columns
        sheet_type = SheetType.VIDEO_LIST
        formula_columns = get_formula_columns(sheet_type)
        excluded_headers = {ext_gui.normalize_header(col) for col in formula_columns}
        
        filtered_r = {}
        for key, value in test_video_data.items():
            if ext_gui.normalize_header(key) not in excluded_headers:
                filtered_r[key] = value
                
        row_values = [''] * len(headers)
        for key, value in filtered_r.items():
            norm_key = ext_gui.normalize_header(key)
            for h_idx, h in enumerate(headers):
                if ext_gui.normalize_header(h) == norm_key:
                    row_values[h_idx] = str(value) if value is not None else ''
                    
        if target_row:
            print(f"-> 기존 행 발견: {target_row}행. 업데이트를 진행합니다.")
            end_col_letter = sheets_manager._col_num_to_letter(len(headers))
            range_name = f'A{target_row}:{end_col_letter}{target_row}'
            sheets_manager._retry_on_rate_limit(worksheet.update, range_name, [row_values], value_input_option='USER_ENTERED')
            print("✓ 업데이트 완료!")
        else:
            last_row = sheets_manager.get_last_row(sheet_name, 10)
            next_row = last_row + 1
            print(f"-> 신규 행 기입 예정: {next_row}행.")
            
            end_col_letter = sheets_manager._col_num_to_letter(len(headers))
            range_name = f'A{next_row}:{end_col_letter}{next_row}'
            sheets_manager._retry_on_rate_limit(worksheet.update, range_name, [row_values], value_input_option='USER_ENTERED')
            print("✓ 삽입 완료!")
            
            # 서식 복사 적용 (10행 기준)
            print("-> 10행 기준 서식 복사 적용 시도...")
            sheets_manager._copy_row_format(worksheet, 10, next_row, 1, len(headers))
            print("✓ 서식 복사 완료!")
            
            target_row = next_row

        # 작성 결과 확인
        print("\n=== [작성 결과 검증] ===")
        row_cells = worksheet.row_values(target_row)
        
        check_cols = {
            '11. 채널명': 11,
            '53. 채널명': 53,
            '54. 채널국가': 54,
            '44. 채널 개설일': 44,
            '36. 디스크립션': 36
        }
        
        for name, col_idx in check_cols.items():
            val = row_cells[col_idx - 1] if len(row_cells) >= col_idx else 'N/A'
            print(f"- {name} (열 {col_idx}): '{val}'")
            
    except Exception as e:
        print(f"에러 발생: {e}")

if __name__ == "__main__":
    check_sheet_write()
