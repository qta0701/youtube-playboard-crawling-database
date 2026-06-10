import os
import sys
import time
import sqlite3
import logging
from datetime import datetime
import pandas as pd
import streamlit as st
import plotly.express as px
import plotly.graph_objects as go

# 필수 모듈 임포트
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from config import Config
from config_mappings import build_url, CATEGORIES, get_country_list, get_category_list, get_period_list
from modules.crawler_selenium import PlayboardCrawler
from modules.youtube_handler import YouTubeTranscriptExtractor
from modules.database import DatabaseHandler
from modules.youtube_manager import YouTubeManager
from modules.quota_tracker import QuotaTracker
from modules.utils import sanitize_filename, generate_safe_filepath, play_sound

# 로거 및 클린업 획득
from logger_config import setup_logger, cleanup_old_logs
logger = logging.getLogger('crawler')

# Streamlit 페이지 설정 (프리미엄 테마 적용)
st.set_page_config(
    page_title="YouTube Crawler & DB Dashboard",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded"
)

# DB 및 핸들러 초기화
DB_PATH = 'output/db/youtube_data.db'
os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
db_handler = DatabaseHandler()
youtube_manager = YouTubeManager(DB_PATH)
quota_tracker = QuotaTracker(DB_PATH)

# ==============================================================================
# 세션 상태 변수 초기화 (비동기 프로세스 제어용)
# ==============================================================================
if 'crawler_instance' not in st.session_state:
    st.session_state['crawler_instance'] = None
if 'stop_requested' not in st.session_state:
    st.session_state['stop_requested'] = False
if 'resume_requested' not in st.session_state:
    st.session_state['resume_requested'] = False

# ==============================================================================
# 0. 실시간 로그 스트리밍을 위한 커스텀 logging Handler 정의
# ==============================================================================
class StreamlitLogHandler(logging.Handler):
    def __init__(self, log_widget):
        super().__init__()
        self.log_widget = log_widget
        self.logs = []

    def emit(self, record):
        try:
            log_entry = self.format(record)
            self.logs.append(log_entry)
            if len(self.logs) > 20:
                self.logs.pop(0)
            # 스트림릿 위젯 업데이트
            self.log_widget.code("\n".join(self.logs))
        except Exception:
            self.handleError(record)


# ==============================================================================
# Helper: DB 연결 생성
# ==============================================================================
def get_db_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


# ==============================================================================
# 앱 기동시 cmd 로그 출력 및 로그 파일 정제 (최근 5개만 유지)
# ==============================================================================
print("===================================================")
print("  YouTube Pro Dashboard & Crawler (Port 8501)")
print(f"  실행 시간: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
print("===================================================")
sys.stdout.flush()

cleanup_old_logs('logs', keep_count=5)


# ==============================================================================
# 사이드바 설정 및 Quota 정보 고정 노출
# ==============================================================================
st.sidebar.title("🎵 YouTube Pro")
st.sidebar.markdown("---")

st.sidebar.subheader("🎟 API Quota 사용량")
try:
    today_usage = quota_tracker.get_today_usage()
    today_usage_val = today_usage.get('total_used', 0)
    pct_used = (today_usage_val / 10000.0) * 100
    
    st.sidebar.metric("오늘 소모된 할당량", f"{today_usage_val:,} / 10,000 pts", f"{pct_used:.1f}% 사용")
    
    if pct_used > 80:
        st.sidebar.error("⚠ 할당량 80% 초과! 키 교체 권장")
    elif pct_used > 50:
        st.sidebar.warning("⚠ 할당량 50% 초과")
    else:
        st.sidebar.success("✓ 할당량 여유로움 (Safe)")
        
    st.sidebar.progress(min(pct_used / 100.0, 1.0))
    
    with st.sidebar.expander("📊 최근 7일 Quota 추이"):
        usage_history = quota_tracker.get_usage_history(7)
        if usage_history:
            df_q = pd.DataFrame(usage_history)
            if 'total' in df_q.columns:
                df_q = df_q.rename(columns={'total': 'daily_usage'})
            fig_q = px.line(
                df_q, 
                x='date', 
                y='daily_usage', 
                labels={'daily_usage': '사용량 (pts)', 'date': '날짜'}
            )
            fig_q.update_traces(mode='lines+markers', line_color='red')
            fig_q.update_layout(
                margin=dict(l=10, r=10, t=10, b=10),
                height=200
            )
            st.sidebar.plotly_chart(fig_q, use_container_width=True)
        else:
            st.sidebar.info("과거 사용량 이력 정보가 없습니다.")
except Exception as q_err:
    st.sidebar.error(f"Quota 로드 실패: {q_err}")

st.sidebar.markdown("---")
st.sidebar.info("개발자: YouTube Crawler Pro Team\n버전: v3.0 (Streamlit)")


# ==============================================================================
# 메인 페이지 헤더 및 탭 네비게이션
# ==============================================================================
st.title("🎵 YouTube Pro 통합 관리 시스템")
st.markdown("유튜브 크롤링, 데이터 분석 및 API 동기화를 원스톱으로 관리합니다.")

tabs = st.tabs(["🎯 플레이보드 크롤러", "📊 DB 통계 및 검색", "🔌 API 동기화"])


# ==============================================================================
# 탭 1: 🎯 플레이보드 크롤러
# ==============================================================================
with tabs[0]:
    st.header("🎯 Playboard 크롤러")
    st.markdown("Playboard의 국가/카테고리별 실시간 랭킹 데이터를 무제한 수집합니다.")
    
    col1, col2 = st.columns([1, 2])
    
    with col1:
        st.subheader("🛠️ 수집 옵션 설정")
        target_type = st.selectbox("수집 대상 (Target)", ["shorts", "video", "channel"], index=0, key="crawl_target")
        
        batch_mode = st.checkbox(
            "📦 전체 카테고리 일괄 크롤링", 
            value=False, 
            key="crawl_batch",
            help="종합 '전체' 카테고리가 아닌, 음악, 게임, 엔터테인먼트 등 플레이보드의 모든 세부 서브 카테고리를 순차적으로 자동 순회하여 크롤링하고 개별 및 통합 파일로 저장합니다."
        )
        
        category_options = get_category_list()
        category = st.selectbox(
            "카테고리 (Category)", 
            category_options, 
            index=0, 
            key="crawl_cat",
            disabled=batch_mode
        )
        
        country_options = get_country_list()
        country = st.selectbox("국가 (Country)", country_options, index=0, key="crawl_country")
        
        period_options = get_period_list()
        period = st.selectbox("기간 (Period)", period_options, index=0, key="crawl_period")
        
        use_specific_date = st.checkbox("과거 특정 날짜 랭킹 수집", key="crawl_use_date")
        specific_date = None
        if use_specific_date:
            specific_date_val = st.date_input("날짜 선택", datetime.today(), key="crawl_date_val")
            specific_date = specific_date_val.strftime('%Y-%m-%d')
            
        login_mode = st.checkbox("로그인 모드 활성화 (100개 이상 수집 시 필수)", value=False, key="crawl_login")
        
        st.markdown("---")
        btn_col1, btn_col2, btn_col3 = st.columns(3)
        with btn_col1:
            start_btn = st.button("🚀 크롤링 시작", use_container_width=True, key="crawl_start_btn")
        with btn_col2:
            resume_btn = st.button("⏯️ 수동 재개", use_container_width=True, key="crawl_resume_btn")
        with btn_col3:
            stop_btn = st.button("🛑 프로세스 중단", use_container_width=True, key="crawl_stop_btn")
            
        # 수동 재개 처리
        if resume_btn:
            if st.session_state['crawler_instance'] is not None:
                st.session_state['crawler_instance'].resume_requested = True
                st.info("⏯️ 로그인 대기를 중단하고 즉시 크롤링을 재개하도록 수동 재개 신호를 전송했습니다.")
            else:
                st.warning("동작 중인 크롤러 프로세스가 없습니다.")
                
        # 프로세스 중단 처리
        if stop_btn:
            st.session_state['stop_requested'] = True
            if st.session_state['crawler_instance'] is not None:
                st.session_state['crawler_instance'].stop_requested = True
                try:
                    if st.session_state['crawler_instance'].driver:
                        st.session_state['crawler_instance'].driver.quit()
                        st.session_state['crawler_instance'].driver = None
                except Exception:
                    pass
                st.success("🛑 크롤러 및 브라우저를 즉시 중단했습니다.")
            else:
                st.warning("동작 중인 크롤러 프로세스가 없습니다.")
        
    with col2:
        st.subheader("📺 실시간 크롤링 현황판")
        progress_bar = st.progress(0.0)
        status_text = st.empty()
        log_shell = st.empty()
        log_shell.code("대기 중... (옵션 설정 후 크롤링 시작을 눌러주세요)")
        
        if start_btn:
            st.session_state['stop_requested'] = False
            st.session_state['resume_requested'] = False
            
            streamlit_handler = StreamlitLogHandler(log_shell)
            streamlit_handler.setFormatter(logging.Formatter('[%(asctime)s] %(levelname)-8s - %(message)s', '%H:%M:%S'))
            logger.addHandler(streamlit_handler)
            
            try:
                ranking_date = specific_date if specific_date else datetime.now().strftime('%Y-%m-%d')
                target_count = Config.MAX_ITEMS_WITH_LOGIN if login_mode else Config.MAX_ITEMS_NO_LOGIN
                
                timestamp = None
                if specific_date:
                    dt = datetime.strptime(specific_date, '%Y-%m-%d')
                    timestamp = int(dt.timestamp())
                
                crawler = PlayboardCrawler(headless=Config.CHROME_HEADLESS)
                st.session_state['crawler_instance'] = crawler
                
                if not batch_mode:
                    status_text.info(f"크롤링 시작 중: {target_type} / {category} / {country} / {period}...")
                    progress_bar.progress(0.1)
                    
                    url = build_url(target_type, category, country, period, timestamp)
                    logger.info(f"Built URL: {url}")
                    
                    progress_bar.progress(0.3)
                    df = crawler.crawl(
                        url=url,
                        target_type=target_type,
                        login_mode=login_mode,
                        target_count=target_count,
                        country=country,
                        period=period,
                        ranking_date=ranking_date
                    )
                    
                    progress_bar.progress(0.8)
                    if len(df) > 0:
                        filepath, filename = generate_safe_filepath(
                            base_dir=Config.OUTPUT_DIR,
                            target_type=target_type,
                            category=category,
                            country=country,
                            period=period,
                            extension='csv'
                        )
                        csv_columns = ['Period', 'Ranking Date', 'Type', 'Country', 'Rank', 'Rank Change',
                                       'Video Title', 'Views', 'Upload Date', 'Tags',
                                       'Channel Name', 'Subscribers', 'Thumbnail']
                        csv_df = df[[col for col in csv_columns if col in df.columns]]
                        csv_df.to_csv(filepath, index=False, encoding='utf-8-sig')
                        
                        db_count = db_handler.insert_dataframe(df, category, country, period, target_type)
                        db_handler.log_crawl_history(target_type, category, country, period, len(df), success=True)
                        
                        progress_bar.progress(1.0)
                        status_text.success(f"✓ 단일 크롤링 완료: 총 {len(df)}개 항목 수집 및 DB 저장 완료 ({filename})")
                        
                        st.subheader("📊 수집된 데이터 프리뷰")
                        st.dataframe(df.head(20), use_container_width=True)
                        
                        with open(filepath, "rb") as file:
                            st.download_button(
                                label="💾 CSV 파일 다운로드",
                                data=file,
                                file_name=filename,
                                mime="text/csv"
                            )
                    else:
                        progress_bar.progress(1.0)
                        status_text.warning("⚠ 수집된 데이터가 없습니다.")
                        
                else:
                    all_categories = [cat for cat in CATEGORIES if cat != '전체']
                    status_text.info(f"일괄 크롤링 시작: {target_type} / 총 {len(all_categories)}개 카테고리 순회 중...")
                    
                    all_data = []
                    success_count = 0
                    fail_count = 0
                    
                    for idx, cat in enumerate(all_categories):
                        pct = (idx / len(all_categories))
                        progress_bar.progress(pct)
                        status_text.info(f"카테고리 수집 중 ({idx+1}/{len(all_categories)}): '{cat}' 진행 중...")
                        
                        try:
                            url = build_url(target_type, cat, country, period, timestamp)
                            df_cat = crawler.crawl(
                                url=url,
                                target_type=target_type,
                                login_mode=login_mode,
                                target_count=target_count,
                                country=country,
                                period=period,
                                ranking_date=ranking_date
                            )
                            if len(df_cat) > 0:
                                all_data.extend(df_cat.to_dict('records'))
                                success_count += 1
                                filepath, filename = generate_safe_filepath(Config.OUTPUT_DIR, target_type, f"batch_{cat}", country, period)
                                csv_columns = ['Period', 'Ranking Date', 'Type', 'Country', 'Rank', 'Rank Change',
                                               'Video Title', 'Views', 'Upload Date', 'Tags',
                                               'Channel Name', 'Subscribers', 'Thumbnail']
                                csv_df = df_cat[[col for col in csv_columns if col in df_cat.columns]]
                                csv_df.to_csv(filepath, index=False, encoding='utf-8-sig')
                                db_handler.insert_dataframe(df_cat, cat, country, period, target_type)
                                db_handler.log_crawl_history(target_type, cat, country, period, len(df_cat), success=True)
                            else:
                                fail_count += 1
                        except Exception as cat_err:
                            fail_count += 1
                            logger.error(f"Error in batch category '{cat}': {cat_err}")
                            db_handler.log_crawl_history(target_type, cat, country, period, 0, success=False, error_message=str(cat_err))
                            
                    progress_bar.progress(1.0)
                    if all_data:
                        combined_df = pd.DataFrame(all_data)
                        filepath_comb, filename_comb = generate_safe_filepath(Config.OUTPUT_DIR, target_type, 'ALL', country, period)
                        csv_columns = ['Period', 'Ranking Date', 'Type', 'Country', 'Rank', 'Rank Change',
                                       'Video Title', 'Views', 'Upload Date', 'Tags',
                                       'Channel Name', 'Subscribers', 'Thumbnail']
                        csv_df = combined_df[[col for col in csv_columns if col in combined_df.columns]]
                        csv_df.to_csv(filepath_comb, index=False, encoding='utf-8-sig')
                        
                        status_text.success(f"✓ 일괄 크롤링 완료: 성공 {success_count}개, 실패 {fail_count}개 (총 {len(combined_df)}개 레코드 저장)")
                        st.subheader("📊 통합 데이터 프리뷰")
                        st.dataframe(combined_df.head(20), use_container_width=True)
                        
                        with open(filepath_comb, "rb") as file:
                            st.download_button(
                                label="💾 통합 CSV 파일 다운로드",
                                data=file,
                                file_name=filename_comb,
                                mime="text/csv"
                            )
                    else:
                        status_text.error("✗ 모든 카테고리 일괄 크롤링 수집 실패")
                        
            except Exception as e:
                progress_bar.progress(1.0)
                status_text.error(f"✗ 크롤링 도중 예외가 발생했습니다: {e}")
                logger.error(f"Crawler error: {e}", exc_info=True)
            finally:
                logger.removeHandler(streamlit_handler)
                
    st.markdown("---")
    st.subheader("📝 자막(Transcript) 일괄 추출기")
    st.markdown("가장 최신에 수집된 CSV 파일을 업로드하거나, 특정 비디오 ID 목록을 입력하여 자막을 추출합니다.")
    
    upload_file = st.file_uploader("크롤링 결과 CSV 업로드 (선택)", type=["csv"], key="crawl_csv_upload")
    video_ids_input = st.text_input("직접 비디오 ID 입력 (콤마로 구분, 예: dQw4w9WgXcQ, v=xxxxxx)", key="crawl_vid_input")
    
    if st.button("자막 추출 시작", key="crawl_transcript_btn"):
        video_ids = []
        if upload_file:
            try:
                df_up = pd.read_csv(upload_file)
                if 'Video ID' in df_up.columns:
                    video_ids = df_up['Video ID'].dropna().tolist()
                    video_ids = [vid for vid in video_ids if vid != 'N/A']
            except Exception as up_err:
                st.error(f"CSV 파싱 에러: {up_err}")
                
        if video_ids_input:
            video_ids.extend([v.strip() for v in video_ids_input.split(",") if v.strip()])
            
        if video_ids:
            st.info(f"총 {len(video_ids)}개 비디오의 자막 추출 진행 중...")
            extractor = YouTubeTranscriptExtractor()
            results = extractor.extract_transcripts_batch(video_ids, save_to_file=True)
            
            success_count = sum(1 for r in results if r['status'] == 'success')
            st.success(f"자막 추출 성공: {success_count}/{len(video_ids)}개 완료! (output/transcripts/ 폴더 저장)")
            st.dataframe(pd.DataFrame(results), use_container_width=True)
        else:
            st.warning("추출할 유효한 비디오 ID를 발견하지 못했습니다.")


# ==============================================================================
# 탭 2: 📊 DB 통계 및 검색
# ==============================================================================
with tabs[1]:
    st.header("📊 YouTube 통합 데이터 통계 및 검색")
    
    st.subheader("🗂️ 수집 DB 현황")
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = [r[0] for r in cursor.fetchall()]
        
        shorts_count = 0
        videos_count = 0
        channels_count = 0
        api_channels_count = 0
        api_videos_count = 0
        
        if 'shorts_rank' in tables:
            cursor.execute("SELECT COUNT(*) FROM shorts_rank")
            shorts_count = cursor.fetchone()[0]
        if 'videos_rank' in tables:
            cursor.execute("SELECT COUNT(*) FROM videos_rank")
            videos_count = cursor.fetchone()[0]
        if 'channels_rank' in tables:
            cursor.execute("SELECT COUNT(*) FROM channels_rank")
            channels_count = cursor.fetchone()[0]
        if 'api_channels' in tables:
            cursor.execute("SELECT COUNT(*) FROM api_channels")
            api_channels_count = cursor.fetchone()[0]
        if 'api_videos' in tables:
            cursor.execute("SELECT COUNT(*) FROM api_videos")
            api_videos_count = cursor.fetchone()[0]
            
        conn.close()
        
        c1, c2, c3, c4, c5 = st.columns(5)
        c1.metric("Shorts 크롤링 건수", f"{shorts_count:,}건")
        c2.metric("Long video 크롤링 건수", f"{videos_count:,}건")
        c3.metric("채널 크롤링 건수", f"{channels_count:,}건")
        c4.metric("API 연동 채널", f"{api_channels_count:,}개")
        c5.metric("API 연동 영상", f"{api_videos_count:,}개")
    except Exception as db_err:
        st.error(f"DB 현황 조회 에러: {db_err}")
        
    st.markdown("---")
    
    st.subheader("🔍 데이터 통합 상세 검색")
    
    col_s1, col_s2, col_s3 = st.columns([2, 1, 1])
    search_keyword = col_s1.text_input("검색 키워드 (제목, 채널명, 태그)", "", key="search_keyword_val")
    search_source = col_s2.selectbox("데이터 소스", ["전체", "크롤링 데이터만", "API 연동 데이터만"], key="search_source_val")
    search_type = col_s3.selectbox("컨텐츠 타입", ["전체", "shorts", "video", "channel"], key="search_type_val")
    
    with st.expander("⚙️ 고급 필터 및 정렬 설정"):
        col_f1, col_f2, col_f3 = st.columns(3)
        view_min = col_f1.number_input("최소 조회수", min_value=0, value=0, step=1000, key="search_view_min")
        view_max = col_f1.number_input("최대 조회수", min_value=0, value=1000000000, step=100000, key="search_view_max")
        
        date_from = col_f2.date_input("게시일 시작 (API 데이터용)", value=datetime(2020, 1, 1), key="search_date_from")
        date_to = col_f2.date_input("게시일 종료 (API 데이터용)", value=datetime.today(), key="search_date_to")
        
        sort_by = col_f3.selectbox("정렬 기준", ["최근 등록일순", "조회수 높은순", "좋아요 비율 높은순"], key="search_sort_by")
        
    search_btn = st.button("🔍 조건 검색 실행", use_container_width=True, key="search_execute_btn")
    
    if search_btn or search_keyword:
        st.subheader("📋 검색 결과")
        try:
            conn = get_db_connection()
            cursor = conn.cursor()
            
            results = []
            
            if search_source in ["전체", "크롤링 데이터만"]:
                if search_type in ["전체", "shorts"] and 'shorts_rank' in tables:
                    cursor.execute('''
                        SELECT 'shorts_rank' as source, video_id, title, channel_name,
                               views, rank, category, country, crawled_at
                        FROM shorts_rank
                        WHERE (title LIKE ? OR channel_name LIKE ?) AND views >= ? AND views <= ?
                    ''', (f'%{search_keyword}%', f'%{search_keyword}%', view_min, view_max))
                    results.extend([dict(row) for row in cursor.fetchall()])
                if search_type in ["전체", "video"] and 'videos_rank' in tables:
                    cursor.execute('''
                        SELECT 'videos_rank' as source, video_id, title, channel_name,
                               views, rank, category, country, crawled_at
                        FROM videos_rank
                        WHERE (title LIKE ? OR channel_name LIKE ?) AND views >= ? AND views <= ?
                    ''', (f'%{search_keyword}%', f'%{search_keyword}%', view_min, view_max))
                    results.extend([dict(row) for row in cursor.fetchall()])
                if search_type in ["전체", "channel"] and 'channels_rank' in tables:
                    cursor.execute('''
                        SELECT 'channels_rank' as source, channel_id as video_id, channel_name as title, channel_name,
                               subscriber_count as views, rank, category, country, crawled_at
                        FROM channels_rank
                        WHERE channel_name LIKE ? AND subscriber_count >= ? AND subscriber_count <= ?
                    ''', (f'%{search_keyword}%', view_min, view_max))
                    results.extend([dict(row) for row in cursor.fetchall()])
                    
            if search_source in ["전체", "API 연동 데이터만"]:
                if search_type in ["전체", "shorts", "video"] and 'api_videos' in tables:
                    type_filter = ""
                    if search_type == 'shorts':
                        type_filter = "AND video_type = 'shorts'"
                    elif search_type == 'video':
                        type_filter = "AND video_type = 'video'"
                        
                    cursor.execute(f'''
                        SELECT 'api_videos' as source, video_id, title, channel_name,
                               view_count as views, 'N/A' as rank, category_name as category, '한국' as country, last_updated as crawled_at
                        FROM api_videos
                        WHERE (title LIKE ? OR channel_name LIKE ? OR tags LIKE ?) 
                              AND view_count >= ? AND view_count <= ? 
                              AND published_at >= ? AND published_at <= ?
                              {type_filter}
                    ''', (f'%{search_keyword}%', f'%{search_keyword}%', f'%{search_keyword}%', view_min, view_max, f"{date_from}T00:00:00Z", f"{date_to}T23:59:59Z"))
                    results.extend([dict(row) for row in cursor.fetchall()])
                    
            conn.close()
            
            if results:
                df_res = pd.DataFrame(results)
                if sort_by == "최근 등록일순":
                    df_res = df_res.sort_values(by="crawled_at", ascending=False)
                elif sort_by == "조회수 높은순":
                    df_res = df_res.sort_values(by="views", ascending=False)
                
                st.dataframe(df_res, use_container_width=True)
                st.info(f"검색 결과: 총 {len(df_res):,}건 발견")
            else:
                st.warning("검색 조건에 맞는 데이터가 없습니다.")
        except Exception as q_err:
            st.error(f"조회 중 쿼리 오류: {q_err}")
            
    st.markdown("---")
    
    st.subheader("📊 데이터 트렌드 시각화 분석")
    
    tab_fig1, tab_fig2 = st.columns(2)
    
    with tab_fig1:
        st.markdown("**인기 수집 카테고리 분포**")
        try:
            conn = get_db_connection()
            df_cat_s = pd.read_sql_query("SELECT category, COUNT(*) as count FROM shorts_rank GROUP BY category", conn)
            conn.close()
            
            if not df_cat_s.empty:
                fig = px.pie(df_cat_s, values='count', names='category', title='Shorts 수집 카테고리 비율', hole=.3)
                st.plotly_chart(fig, use_container_width=True)
            else:
                st.info("시각화할 Shorts 카테고리 데이터가 부족합니다.")
        except:
            st.info("데이터베이스 테이블 정보를 불러올 수 없습니다.")
            
    with tab_fig2:
        st.markdown("**인기 채널 TOP 10 (구독자수 기준)**")
        try:
            conn = get_db_connection()
            df_top_ch = pd.read_sql_query("""
                SELECT channel_name, MAX(subscriber_count) as subscribers 
                FROM channels_rank 
                WHERE channel_name != 'N/A'
                GROUP BY channel_name 
                ORDER BY subscribers DESC 
                LIMIT 10
            """, conn)
            conn.close()
            
            if not df_top_ch.empty:
                fig_bar = px.bar(
                    df_top_ch, 
                    x='subscribers', 
                    y='channel_name', 
                    orientation='h', 
                    title='수집 채널 구독자수 TOP 10',
                    labels={'subscribers': '구독자 수', 'channel_name': '채널명'}
                )
                fig_bar.update_layout(yaxis={'categoryorder':'total ascending'})
                st.plotly_chart(fig_bar, use_container_width=True)
            else:
                st.info("시각화할 채널 구독자 데이터가 부족합니다.")
        except:
            st.info("데이터베이스 테이블 정보를 불러올 수 없습니다.")


# ==============================================================================
# 탭 3: 🔌 API 동기화
# ==============================================================================
with tabs[2]:
    st.header("🔌 YouTube API 연동 & 동기화")
    
    st.subheader("🔗 Zero-Cost 채널 URL 동기화")
    st.markdown("YouTube 채널 홈 URL(예: `@지식채널e`)을 분석하여 채널 ID 추출 후 DB 동기화를 실행합니다.")
    
    channel_url_input = st.text_input("YouTube 채널 URL 입력", "https://www.youtube.com/@ebsdocumentary", key="sync_channel_url")
    limit_video = st.slider("수집할 최신 영상 개수", min_value=10, max_value=100, value=50, step=10, key="sync_limit_video")
    
    sync_btn = st.button("🔌 API 동기화 실행", use_container_width=True, key="sync_execute_btn")
    
    if sync_btn and channel_url_input:
        st.info(f"채널 분석 및 동기화 진행 중: {channel_url_input}...")
        
        try:
            sync_res = youtube_manager.sync_channel(channel_url_input)
            
            if sync_res['success']:
                channel_id = sync_res['channel_id']
                st.success(f"✓ 채널 추출 성공: {sync_res['data']['title']} (ID: {channel_id})")
                
                st.info(f"채널 영상 수집 중 (최대 {limit_video}개)...")
                video_res = youtube_manager.fetch_videos(channel_id, limit_video)
                
                if video_res['success']:
                    st.success(f"✓ 영상 수집 완료! Shorts: {video_res['shorts_count']}개, 일반 영상: {video_res['video_count']}개")
                    st.info(f"사용된 할당량(Quota): {video_res['quota_used']}포인트")
                else:
                    st.error(f"영상 수집 실패: {video_res['error']}")
            else:
                st.error(f"채널 동기화 실패: {sync_res['error']}")
        except Exception as sync_err:
            st.error(f"동기화 에러: {sync_err}")

