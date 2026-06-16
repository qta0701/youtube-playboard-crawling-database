import os
import sys
from datetime import datetime

# 프로젝트 루트 추가
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from modules.crawler_runner import run_crawling_by_criteria
from modules.database import DatabaseHandler

class MockStreamlitWidget:
    def info(self, text, *args, **kwargs):
        print(f"[INFO] {text}")
        sys.stdout.flush()
    def success(self, text, *args, **kwargs):
        print(f"[SUCCESS] {text}")
        sys.stdout.flush()
    def warning(self, text, *args, **kwargs):
        print(f"[WARNING] {text}")
        sys.stdout.flush()
    def error(self, text, *args, **kwargs):
        print(f"[ERROR] {text}")
        sys.stdout.flush()
    def progress(self, val, *args, **kwargs):
        print(f"[PROGRESS] {val * 100:.1f}%")
        sys.stdout.flush()
    def code(self, text, *args, **kwargs):
        pass

def main():
    print("=== 2026-06-14 데이터 100개 일괄 재크롤링 시작 ===")
    sys.stdout.flush()
    
    mock_status = MockStreamlitWidget()
    mock_progress = MockStreamlitWidget()
    mock_shell = MockStreamlitWidget()
    
    # 세션 상태 변수 Mocking (crawler_runner 내부에서 사용)
    import streamlit as st
    if 'stop_requested' not in st.session_state:
        st.session_state['stop_requested'] = False
    if 'resume_requested' not in st.session_state:
        st.session_state['resume_requested'] = False
    if 'skip_requested' not in st.session_state:
        st.session_state['skip_requested'] = False
        
    try:
        run_crawling_by_criteria(
            target_type='shorts',
            batch_mode=True,       # 전체 카테고리 순회
            category='전체',        # batch_mode이므로 무시됨
            country='한국',
            period='일간',
            specific_date='2026-06-14',
            use_specific_date=True,
            login_mode=False,      # 로그인 없이 100개 수집 시도 (로그인 필요 시 True로 지정할 수도 있으나 기본 False)
            crawl_limit=100,        # 100개 목표 수집
            crawl_criteria='조회수 순위',
            crawl_all_criteria=True, # 조회수, 좋아요, 댓글 순위 모두 순차 순회
            status_text=mock_status,
            progress_bar=mock_progress,
            log_shell=mock_shell
        )
        print("=== 2026-06-14 데이터 100개 일괄 재크롤링 완료! ===")
        sys.stdout.flush()
    except Exception as e:
        print(f"오류 발생: {e}")
        import traceback
        traceback.print_exc()
        sys.stdout.flush()

if __name__ == '__main__':
    main()
