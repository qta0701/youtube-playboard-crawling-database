import os
import sys
import json
import time

current_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.abspath(os.path.join(current_dir, ".."))
sys.path.append(project_root)

# 격리 모듈 동적 로드
from modules.external_loader import load_isolated_module

gui_file = os.path.join(project_root, "외부프로그램", "롱폼-유튜브검색기", "GUI_Interface.py")
ext_gui = load_isolated_module("ext_search_gui_module", gui_file, os.path.dirname(gui_file))

def check_headers():
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
        
        worksheet = sheets_manager.get_or_create_worksheet(sheet_name)
        headers = sheets_manager.get_headers(sheet_name)
        print("\n=== [1행 헤더 목록] ===")
        for idx, h in enumerate(headers, 1):
            if h:
                print(f"{idx}: {h}")
                
        # 더미 데이터 키
        dummy_keys = [
            '영상 ID', '영상 업로드날짜', '수집날짜', '검색 키워드', '영상 링크', '제목', '채널명', 
            '조회수', '구독자수', '좋아요 수', '댓글수', '구독자 대비 조회수 배율', '조회수 대비 좋아요', 
            '조회수 대비 댓글', '33. 채널명', '채널국가', '채널 ID', '채널링크', '영상갯수', 
            '채널 전체 조회수', '영상당 평균 조회수', '채널 개설일', '카테고리 ID', '디스크립션', 
            '디스크립션 텍스트 수', '해시태그 유무', '썸네일 링크', '썸네일 이미지주소'
        ]
        
        mapping, unmatched = sheets_manager.create_header_mapping(headers, dummy_keys)
        print("\n=== [헤더 매핑 결과] ===")
        for key, col in mapping.items():
            print(f"데이터 키 '{key}' -> 열 번호 {col} (헤더: '{headers[col-1]}')")
            
        print("\n=== [매핑되지 않은 키 목록] ===")
        for key in unmatched:
            print(f"- {key}")

    except Exception as e:
        print(f"에러 발생: {e}")

if __name__ == "__main__":
    check_headers()
