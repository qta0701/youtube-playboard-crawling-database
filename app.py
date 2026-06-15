import os
import sys
import time
import streamlit.components.v1 as components
import json
import sqlite3
import logging
from datetime import datetime, date, timedelta
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
from modules.utils import sanitize_filename, generate_safe_filepath, play_sound, parse_count_string

# 격리 모듈 동적 로더 및 알림음 유틸 임포트
from modules.external_loader import load_isolated_module
from modules.utils import play_notification_sound, show_notification

# 로거 및 클린업 획득
from logger_config import setup_logger, cleanup_old_logs
logger = setup_logger('crawler')

# 공통 유틸 및 크롤러 기동 엔진 임포트
from app_utils import get_actual_ranking_date_str, standardize_dataframe_types, load_and_standardize_csv
from modules.crawler_runner import run_crawling_by_criteria, find_existing_batch_file_runner

# Streamlit 페이지 설정 (프리미엄 테마 적용)
st.set_page_config(
    page_title="유튜브 대시보드 크롤러",
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
if 'ui_settings' not in st.session_state:
    st.session_state['ui_settings'] = None
if 'crawler_instance' not in st.session_state:
    st.session_state['crawler_instance'] = None
if 'stop_requested' not in st.session_state:
    st.session_state['stop_requested'] = False
if 'resume_requested' not in st.session_state:
    st.session_state['resume_requested'] = False
if 'log_history' not in st.session_state:
    st.session_state['log_history'] = ["대기 중... (옵션 설정 후 크롤링 시작을 눌러주세요)"]
if 'crawl_result' not in st.session_state:
    st.session_state['crawl_result'] = None

# 외부 프로그램 연동 세션 상태 초기화
if 'transcript_running' not in st.session_state:
    st.session_state['transcript_running'] = False
if 'transcript_log_history' not in st.session_state:
    st.session_state['transcript_log_history'] = ["대기 중..."]
if 'transcript_progress' not in st.session_state:
    st.session_state['transcript_progress'] = 0.0
if 'transcript_status' not in st.session_state:
    st.session_state['transcript_status'] = ""
if 'transcript_result' not in st.session_state:
    st.session_state['transcript_result'] = None
if 'transcript_stop_requested' not in st.session_state:
    st.session_state['transcript_stop_requested'] = False

if 'search_running' not in st.session_state:
    st.session_state['search_running'] = False
if 'search_log_history' not in st.session_state:
    st.session_state['search_log_history'] = ["대기 중..."]
if 'search_result' not in st.session_state:
    st.session_state['search_result'] = None
if 'search_stop_requested' not in st.session_state:
    st.session_state['search_stop_requested'] = False

if 'collection_running' not in st.session_state:
    st.session_state['collection_running'] = False
if 'collection_log_history' not in st.session_state:
    st.session_state['collection_log_history'] = ["대기 중... (채널 또는 영상 정보를 입력하고 수집을 눌러주세요)"]
if 'collection_progress' not in st.session_state:
    st.session_state['collection_progress'] = 0.0
if 'collection_status' not in st.session_state:
    st.session_state['collection_status'] = ""
if 'collection_result' not in st.session_state:
    st.session_state['collection_result'] = None
if 'collection_stop_requested' not in st.session_state:
    st.session_state['collection_stop_requested'] = False

# ==============================================================================
# 0. 실시간 로그 스트리밍을 위한 커스텀 logging Handler 정의
# ==============================================================================
class StreamlitLogHandler(logging.Handler):
    def __init__(self, log_widget):
        super().__init__()
        self.log_widget = log_widget

    def emit(self, record):
        try:
            log_entry = self.format(record)
            if 'log_history' not in st.session_state:
                st.session_state['log_history'] = []
            st.session_state['log_history'].append(log_entry)
            
            # 현황판에 표시할 로그 수를 최대 100개로 제약 (메모리 보존)
            if len(st.session_state['log_history']) > 100:
                st.session_state['log_history'].pop(0)
                
            # 스트림릿 위젯 업데이트
            self.log_widget.code("\n".join(st.session_state['log_history']))
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
# UI 설정 자동 보존 헬퍼 선언
# ==============================================================================
SETTINGS_FILE = 'output/settings.json'

def load_settings():
    """로컬 파일에서 이전에 저장된 UI 설정값을 로드합니다."""
    default_settings = {
        # 탭 1 (크롤러)
        "crawl_target": "shorts",
        "crawl_batch": False,
        "crawl_cat": "전체",
        "crawl_country": "한국",
        "crawl_period": "일간",
        "crawl_use_date": False,
        "crawl_date_val": datetime.today().date(),
        "crawl_login": False,
        "crawl_limit": 100,
        "crawl_criteria": "조회수 순위",
        "crawl_all_criteria": False,
        
        # 탭 2 (플레이보드 크롤링 데이터 대시보드 & 검색)
        "dash_criteria": "조회수 순위",
        "dash_country": "한국",
        "dash_period": "일간",
        "dash_category_selected": "All",
        "crawl_dash_date": "",
        "crawl_dash_type": "📱 쇼츠 (Shorts)",
        "crawl_dash_layout": "캐러셀형 (카드 그리드)",
        "dash_sort_order": "높은 순위순",
        "dash_highlight_ratio": 10.0,
        
        "crawl_search_keyword": "",
        "crawl_search_type": "전체",
        "crawl_search_sort": "최근 등록일순",
        "crawl_search_view_min": 0,
        "crawl_search_view_max": 1000000000,
        
        # 탭 3 (API 연동데이터 검색)
        "api_search_keyword": "",
        "api_search_type": "전체",
        "api_search_sort": "최근 등록일순",
        "api_search_view_min": 0,
        "api_search_view_max": 1000000000,
        "api_search_date_from": datetime(2020, 1, 1).date(),
        "api_search_date_to": datetime.today().date(),
        
        # 레거시 탭 2 (하위 호환성 유지)
        "search_keyword_val": "",
        "search_source_val": "전체",
        "search_type_val": "전체",
        "search_view_min": 0,
        "search_view_max": 1000000000,
        "search_date_from": datetime(2020, 1, 1).date(),
        "search_date_to": datetime.today().date(),
        "search_sort_by": "최근 등록일순",
        
        # 탭 4 (API 동기화)
        "sync_channel_url": "https://www.youtube.com/@ebsdocumentary",
        "sync_limit_video": 50,
        
        # 탭 5 (대본 추출기)
        "ext_trans_type": "롱폼 대본 추출기",
        "ext_trans_sheet_url": "https://docs.google.com/spreadsheets/d/15VuwgxcfjWUGzlQIstSNcK5SnTQZtaEi3XRfyffPxEE/edit",
        "ext_trans_sheet_name": "영상 리스트",
        "ext_trans_mode": "모드 A (전체 추출)",
        "ext_trans_limit": 50,
        "ext_trans_timestamp": True,
        "ext_trans_concurrency": 5,
        "ext_trans_delay": 1.0,
        "ext_trans_use_browser": True,
        "ext_trans_headless": True,
        "ext_trans_use_profile": False,
        "ext_trans_creds": "google_service_key/service-account-key.json",

        # 탭 6 (유튜브 검색기)
        "ext_search_type": "롱폼 유튜브 검색기",
        "ext_search_keyword": "",
        "ext_search_limit": 20,
        "ext_search_order": "relevance",
        "ext_search_duration": "any",
        "ext_search_sheet_url": "https://docs.google.com/spreadsheets/d/15VuwgxcfjWUGzlQIstSNcK5SnTQZtaEi3XRfyffPxEE/edit",
        "ext_search_sheet_name": "키워드 검색결과",
        "ext_search_creds": "google_service_key/service-account-key.json"
    }
    if os.path.exists(SETTINGS_FILE):
        try:
            with open(SETTINGS_FILE, 'r', encoding='utf-8') as f:
                saved = json.load(f)
                for k, v in saved.items():
                    if k in ["crawl_date_val", "search_date_from", "search_date_to", "api_search_date_from", "api_search_date_to"] and isinstance(v, str):
                        try:
                            default_settings[k] = datetime.strptime(v, '%Y-%m-%d').date()
                        except ValueError:
                            pass
                    else:
                        default_settings[k] = v
        except Exception as e:
            logger.warning(f"Failed to load settings: {e}")
    return default_settings

def save_settings(settings):
    """UI 설정값을 로컬 JSON 파일에 자동으로 저장합니다."""
    try:
        os.makedirs(os.path.dirname(SETTINGS_FILE), exist_ok=True)
        with open(SETTINGS_FILE, 'w', encoding='utf-8') as f:
            json.dump(settings, f, ensure_ascii=False, indent=4)
    except Exception as e:
        logger.warning(f"Failed to save settings: {e}")


# 기동 시 초기 설정 복원 바인딩
if 'settings_initialized' not in st.session_state:
    saved_settings = load_settings()
    st.session_state['ui_settings'] = saved_settings.copy()
    for k, v in saved_settings.items():
        st.session_state[k] = v
    st.session_state['settings_initialized'] = True


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
st.title("🎵 유튜브 대시보드 크롤러")
st.markdown("유튜브 크롤링, 데이터 분석 및 API 동기화를 원스톱으로 관리합니다.")

tabs = st.tabs(["🎯 플레이보드 크롤러", "📊 크롤링 데이터", "📥 채널 및 영상 수집", "📝 대본 추출기", "🔎 유튜브 검색기", "📊 구글 시트 연동 DB"])



# ==============================================================================
# 탭 1: 🎯 플레이보드 크롤러
# ==============================================================================
with tabs[0]:
    st.header("🎯 Playboard 크롤러")
    st.markdown("Playboard의 국가/카테고리별 실시간 랭킹 데이터를 무제한 수집합니다.")
    
    col1, col2 = st.columns([1, 2])
    
    with col1:
        st.subheader("🛠️ 수집 옵션 설정")
        if 'ui_settings' not in st.session_state or st.session_state['ui_settings'] is None:
            st.session_state['ui_settings'] = load_settings()
        saved_ui = st.session_state['ui_settings']

        # 1. 수집 대상
        target_options = ["shorts", "video", "channel"]
        target_val = saved_ui.get("crawl_target", "shorts")
        target_idx = target_options.index(target_val) if target_val in target_options else 0
        target_type = st.selectbox("수집 대상 (Target)", target_options, index=target_idx, key="crawl_target")
        
        # 2. 배치 모드
        batch_mode = st.checkbox(
            "📦 전체 카테고리 일괄 크롤링", 
            value=saved_ui.get("crawl_batch", False), 
            key="crawl_batch",
            help="종합 '전체' 카테고리가 아닌, 음악, 게임, 엔터테인먼트 등 플레이보드의 모든 세부 서브 카테고리를 순차적으로 자동 순회하여 크롤링하고 개별 및 통합 파일로 저장합니다."
        )
        
        # 3. 카테고리
        category_options = get_category_list()
        cat_val = saved_ui.get("crawl_cat", "전체")
        cat_idx = category_options.index(cat_val) if cat_val in category_options else 0
        category = st.selectbox(
            "카테고리 (Category)", 
            category_options, 
            index=cat_idx, 
            key="crawl_cat",
            disabled=batch_mode
        )
        
        # 9-1. 모든 수집기준 수집하기 체크박스
        crawl_all_criteria = st.checkbox(
            "📋 모든 수집기준 수집하기 (조회수/좋아요/댓글 순위 순차 순회)",
            value=saved_ui.get("crawl_all_criteria", False),
            key="crawl_all_criteria",
            help="이 옵션을 활성화하면, 아래 선택된 수집 기준에 관계없이 조회수 순위, 좋아요 순위, 댓글 순위의 3대 핵심 차트를 순차적으로 모두 자동 크롤링합니다."
        )

        # 9. 수집 기준
        criteria_options = ["조회수 순위", "좋아요 순위", "댓글 순위"]
        criteria_val = saved_ui.get("crawl_criteria", "조회수 순위")
        criteria_idx = criteria_options.index(criteria_val) if criteria_val in criteria_options else 0
        crawl_criteria = st.selectbox(
            "수집 기준 (Criteria)",
            criteria_options,
            index=criteria_idx,
            key="crawl_criteria",
            disabled=crawl_all_criteria,
            help="크롤링할 플레이보드 차트의 정렬 기준을 선택합니다. (조회수 순위, 좋아요 순위, 댓글 순위 중 선택)"
        )
        
        # 4. 국가
        country_options = get_country_list()
        country_val = saved_ui.get("crawl_country", "한국")
        country_idx = country_options.index(country_val) if country_val in country_options else 0
        country = st.selectbox("국가 (Country)", country_options, index=country_idx, key="crawl_country")
        
        # 5. 기간
        period_options = get_period_list()
        period_val = saved_ui.get("crawl_period", "일간")
        period_idx = period_options.index(period_val) if period_val in period_options else 0
        period = st.selectbox("기간 (Period)", period_options, index=period_idx, key="crawl_period")
        
        # 6. 특정 날짜
        use_specific_date = st.checkbox("과거 특정 날짜 랭킹 수집", value=saved_ui.get("crawl_use_date", False), key="crawl_use_date")
        specific_date = None
        default_date = saved_ui.get("crawl_date_val", datetime.today().date())
        if isinstance(default_date, str):
            try:
                default_date = datetime.strptime(default_date, '%Y-%m-%d').date()
            except:
                default_date = datetime.today().date()
                
        if use_specific_date:
            specific_date_val = st.date_input("날짜 선택", default_date, key="crawl_date_val")
            specific_date = specific_date_val.strftime('%Y-%m-%d')
        else:
            specific_date = datetime.today().strftime('%Y-%m-%d')
            
        # 7. 로그인 여부
        login_mode = st.checkbox("로그인 모드 활성화 (100개 이상 수집 시 필수)", value=saved_ui.get("crawl_login", False), key="crawl_login")
        
        # 8. 수집 개수
        crawl_limit = st.number_input(
            "수집할 개수 (Limit)",
            min_value=1,
            max_value=1000,
            value=int(saved_ui.get("crawl_limit", 100)),
            step=10,
            key="crawl_limit",
            help="수집하고자 하는 플레이보드 랭킹 순위의 한도를 지정합니다. 로그인 모드 활성화 시 더 많은 수집이 보장되며 기본값은 100개입니다."
        )
        
        st.markdown("---")
        
        # 🔍 크롤링 동작 예측 상태 표시 패널
        st.subheader("🔍 동작 예측 상태")
        if st.button("🔄 예측 상태 실시간 갱신", width='stretch', key="refresh_prediction_btn"):
            st.session_state['crawl_result'] = None
            st.rerun()
        
        # 3대 기준 리스트 결정
        active_criteria_list = ["조회수 순위", "좋아요 순위", "댓글 순위"] if crawl_all_criteria else [crawl_criteria]
        calc_ranking_date = specific_date if use_specific_date else get_actual_ranking_date_str(specific_date)

        if not batch_mode:
            # 단일 카테고리 예측
            predict_records = []
            for crit in active_criteria_list:
                existing_filepath = find_existing_batch_file_runner(
                    target_type=target_type,
                    category=category,
                    country=country,
                    period=period,
                    criteria=crit,
                    ranking_date=calc_ranking_date
                )
                already_collected = 0
                if existing_filepath:
                    try:
                        existing_df = load_and_standardize_csv(existing_filepath, crit)
                        already_collected = len(existing_df)
                    except Exception:
                        pass
                
                if already_collected == 0:
                    status = "📂 신규 생성"
                    add_count = crawl_limit
                elif already_collected >= crawl_limit:
                    status = "✓ 완료 건너뜀"
                    add_count = 0
                else:
                    status = "🔄 기존 파일 채우기"
                    add_count = crawl_limit - already_collected
                    
                predict_records.append({
                    "수집 기준": crit,
                    "예측 동작": status,
                    "현재 수집량": already_collected,
                    "추가 수집량": add_count
                })
            
            df_predict = pd.DataFrame(predict_records)
            st.dataframe(df_predict, use_container_width=True, hide_index=True)
            
            if len(active_criteria_list) == 1:
                rec = predict_records[0]
                if rec["현재 수집량"] == 0:
                    date_label = f"'{calc_ranking_date}' 기준"
                    st.info(f"📂 **[신규 파일 생성]** {date_label} 파일이 없습니다. 새롭게 **{crawl_limit}**개를 수집합니다.")
                elif rec["현재 수집량"] >= crawl_limit:
                    st.success(f"✓ **[수집 완료 건너뜀]** 이미 **{rec['현재 수집량']}**개가 수집되어 목표치({crawl_limit}개)를 충족했습니다. 크롤링을 건너뜁니다.")
                else:
                    st.warning(f"🔄 **[기존 파일 채우기]** 이미 **{rec['현재 수집량']}**개가 수집되어 있습니다. 부족한 **{crawl_limit - rec['현재 수집량']}**개를 추가 수집합니다.")
        else:
            # 일괄 카테고리 예측
            all_categories = get_category_list()
            batch_records = []
            new_cats = 0
            update_cats = 0
            skip_cats = 0
            
            for crit in active_criteria_list:
                for cat in all_categories:
                    batch_cat_name = f"batch_{cat}"
                    existing_filepath = find_existing_batch_file_runner(
                        target_type=target_type,
                        category=batch_cat_name,
                        country=country,
                        period=period,
                        criteria=crit,
                        ranking_date=calc_ranking_date
                    )
                    
                    already_collected = 0
                    if existing_filepath:
                        try:
                            existing_df = load_and_standardize_csv(existing_filepath, crit)
                            already_collected = len(existing_df)
                        except Exception:
                            pass
                    
                    if already_collected == 0:
                        status = "📂 신규 생성"
                        add_count = crawl_limit
                        new_cats += 1
                    elif already_collected >= crawl_limit:
                        status = "✓ 완료 건너뜀"
                        add_count = 0
                        skip_cats += 1
                    else:
                        status = "🔄 기존 파일 채우기"
                        add_count = crawl_limit - already_collected
                        update_cats += 1
                        
                    batch_records.append({
                        "수집 기준": crit,
                        "카테고리": cat,
                        "예측 동작": status,
                        "현재 수집량": already_collected,
                        "추가 수집량": add_count
                    })
            
            # 요약 표시
            summary_txt = []
            if new_cats > 0:
                summary_txt.append(f"📂 신규 생성: {new_cats}개")
            if update_cats > 0:
                summary_txt.append(f"🔄 기존 채우기: {update_cats}개")
            if skip_cats > 0:
                summary_txt.append(f"✓ 건너뜀: {skip_cats}개")
                
            st.info(f"📋 **일괄 크롤링 예측** ({', '.join(summary_txt)})")
            
            with st.expander("🔍 카테고리별 상세 예측 현황 보기", expanded=True):
                df_predict = pd.DataFrame(batch_records)
                st.dataframe(df_predict, use_container_width=True, hide_index=True)
                
                # 클립보드로 복사하기 편하도록 TSV 문자열 생성 및 st.code를 통한 네이티브 복사 지원 (브라우저 보안 제약 우회)
                tsv_data = df_predict.to_csv(index=False, sep='\t')
                st.markdown("💡 **예측 테이블 복사 방법**: 아래 텍스트 상자 우측 상단의 복사(📋) 단추를 누르면 엑셀이나 메모장에 즉시 붙여넣을 수 있습니다.")
                st.code(tsv_data, language="text", wrap_lines=False)

        st.markdown("---")
        btn_col1, btn_col2, btn_col3, btn_col4 = st.columns(4)
        with btn_col1:
            start_btn = st.button("🚀 크롤링 시작", width='stretch', key="crawl_start_btn")
        with btn_col2:
            resume_btn = st.button("⏯️ 수동 재개", width='stretch', key="crawl_resume_btn")
        with btn_col3:
            skip_btn = st.button("⏭️ 다음 카테고리", width='stretch', key="crawl_skip_btn")
        with btn_col4:
            stop_btn = st.button("🛑 프로세스 중단", width='stretch', key="crawl_stop_btn")
            
        # 수동 재개 처리
        if resume_btn:
            if st.session_state['crawler_instance'] is not None:
                st.session_state['crawler_instance'].resume_requested = True
                st.info("⏯️ 즉시 크롤링을 재개하도록 수동 재개 신호를 전송했습니다.")
            else:
                st.warning("동작 중인 크롤러 프로세스가 없습니다.")
                
        # 다음 카테고리 스킵 처리
        if skip_btn:
            if st.session_state['crawler_instance'] is not None:
                st.session_state['crawler_instance'].skip_requested = True
                st.info("⏭️ 현재 카테고리 수집을 건너뛰고 다음 카테고리로 넘어가도록 스킵 신호를 전송했습니다.")
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
        log_shell.code("\n".join(st.session_state['log_history']))
        
        # 이전 크롤링 결과가 세션에 존재하면 복원하여 상시 표시
        if 'crawl_result' in st.session_state and st.session_state['crawl_result'] is not None:
            res = st.session_state['crawl_result']
            progress_bar.progress(1.0)
            
            # 최종 수집 통계 카드 렌더링 (단일/일괄 공통)
            if "stats" in res:
                stats = res["stats"]
                st.markdown("### 🏆 최종 수집 통계 요약")
                s_col1, s_col2, s_col3, s_col4 = st.columns(4)
                with s_col1:
                    st.metric("목표 카테고리 수", f"{stats['target_cats_count']}개")
                with s_col2:
                    st.metric("카테고리당 목표량", f"{stats['target_limit']:,}개")
                with s_col3:
                    st.metric("업데이트된 파일 수", f"{stats['updated_files_count']}개")
                with s_col4:
                    st.metric("실제 업데이트 행 수", f"{stats['updated_rows_count']:,}개")
                
                # 새로 수집된 건수가 있으면 캡션이나 소형 정보 제공
                if stats.get('newly_crawled_rows', 0) > 0:
                    st.caption(f"💡 이번 크롤링을 통해 새로 추가 수집된 총 영상 수는 **{stats['newly_crawled_rows']:,}**개입니다.")
                
                if stats['failed_cats_count'] > 0:
                    st.error(f"❌ 수집 중단/실패 카테고리 ({stats['failed_cats_count']}개): {', '.join(stats['failed_cats_list'])}")
                else:
                    st.success("✓ 모든 카테고리가 에러 없이 완벽히 완료되었습니다.")
                st.markdown("---")
                
            if res["status"] == "success":
                status_text.success(res["msg"])
                if res.get("is_batch", False):
                    summary_df = pd.DataFrame(res["summary_data"])
                    under_target_df = pd.DataFrame(res["under_target_data"])
                    combined_df = pd.DataFrame(res["data"])
                    target_count = res.get("target_count", 100)
                    
                    st.subheader("📊 통합 데이터 프리뷰")
                    st.markdown("### 📈 카테고리별 수집 현황 요약")
                    st.dataframe(summary_df, use_container_width=True, hide_index=True)
                    
                    if len(under_target_df) > 0:
                        st.warning(f"⚠️ 총 {len(under_target_df)}개 카테고리의 수집 개수가 설정된 목표치({target_count}개)에 미달되었습니다.")
                        st.markdown("### ⚠️ 수집 개수 미달 카테고리 현황")
                        st.dataframe(under_target_df, use_container_width=True, hide_index=True)
                    else:
                        st.success("✓ 모든 카테고리가 목표 개수를 완벽히 충족하여 수집 완료되었습니다.")
                        
                    st.markdown("### 📋 수집 데이터 샘플 프리뷰")
                    st.dataframe(combined_df, use_container_width=True)
                else:
                    df = pd.DataFrame(res["data"])
                    st.subheader("📊 수집된 데이터 프리뷰")
                    st.dataframe(df, use_container_width=True)
                    
                if res.get("filepath") and os.path.exists(res["filepath"]):
                    with open(res["filepath"], "rb") as file:
                        st.download_button(
                            label="💾 CSV 파일 다운로드" if not res.get("is_batch") else "💾 통합 CSV 파일 다운로드",
                            data=file,
                            file_name=res["filename"],
                            mime="text/csv",
                            key="session_csv_download_btn"
                        )
            elif res["status"] == "warning":
                status_text.warning(res["msg"])
            elif res["status"] == "error":
                status_text.error(res["msg"])
        
        if start_btn:
            st.session_state['crawl_result'] = None  # 이전 크롤링 결과 초기화
            st.session_state['log_history'] = []  # 새로 크롤링 시작 시 로그 초기화
            st.session_state['stop_requested'] = False
            st.session_state['resume_requested'] = False
            st.session_state['skip_requested'] = False
            
            streamlit_handler = StreamlitLogHandler(log_shell)
            streamlit_handler.setFormatter(logging.Formatter('[%(asctime)s] %(levelname)-8s - %(message)s', '%H:%M:%S'))
            logger.addHandler(streamlit_handler)
            
            try:
                run_crawling_by_criteria(
                    target_type=target_type,
                    batch_mode=batch_mode,
                    category=category,
                    country=country,
                    period=period,
                    specific_date=specific_date,
                    use_specific_date=use_specific_date,
                    login_mode=login_mode,
                    crawl_limit=crawl_limit,
                    crawl_criteria=crawl_criteria,
                    crawl_all_criteria=crawl_all_criteria,
                    status_text=status_text,
                    progress_bar=progress_bar,
                    log_shell=log_shell
                )
            except Exception as e:
                logger.error(f"크롤러 실행 실패: {e}")
                st.error(f"크롤러 실행 도중 시스템 에러 발생: {e}")
            finally:
                if 'crawler_instance' in st.session_state and st.session_state['crawler_instance'] is not None:
                    try:
                        st.session_state['crawler_instance'].close()
                    except:
                        pass
                logger.removeHandler(streamlit_handler)
                st.rerun()
                
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
# 탭 2: 📊 크롤링 데이터
# ==============================================================================
with tabs[1]:
    st.header("📊 크롤링 데이터 대시보드")
    
    # 🗂️ 크롤링 DB 현황
    st.subheader("🗂️ 크롤링 데이터 현황")
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = [r[0] for r in cursor.fetchall()]
        
        shorts_count = 0
        videos_count = 0
        channels_count = 0
        
        if 'shorts_rank' in tables:
            cursor.execute("SELECT COUNT(*) FROM shorts_rank")
            shorts_count = cursor.fetchone()[0]
        if 'videos_rank' in tables:
            cursor.execute("SELECT COUNT(*) FROM videos_rank")
            videos_count = cursor.fetchone()[0]
        if 'channels_rank' in tables:
            cursor.execute("SELECT COUNT(*) FROM channels_rank")
            channels_count = cursor.fetchone()[0]
            
        conn.close()
        
        c1, c2, c3 = st.columns(3)
        c1.metric("Shorts 크롤링 건수", f"{shorts_count:,}건")
        c2.metric("일반 영상 크롤링 건수", f"{videos_count:,}건")
        c3.metric("채널 크롤링 건수", f"{channels_count:,}건")
    except Exception as db_err:
        st.error(f"DB 현황 조회 에러: {db_err}")
        
    st.markdown("---")
    
    # 대시보드 및 상세 검색/시각화 서브 탭 분리
    crawl_sub_tabs = st.tabs(["📅 날짜별 대시보드", "🔍 크롤링 상세 검색", "📊 트렌드 시각화"])
    
    with crawl_sub_tabs[0]:
        st.subheader("📅 수집 날짜별 대시보드 뷰")
        
        # 1. 탭 1 세션에서 복원하거나 load_settings에서 가져온 디폴트 값을 기반으로 st.pills 연동
        saved_ui = st.session_state.get('ui_settings', load_settings())
        
        # 세션 상태 변수 초기화 (st.pills의 양방향 바인딩 및 중복 오류 방지)
        if 'dash_criteria' not in st.session_state:
            st.session_state['dash_criteria'] = st.session_state.get('crawl_criteria', saved_ui.get('crawl_criteria', '조회수 순위'))
        if 'dash_country' not in st.session_state:
            st.session_state['dash_country'] = st.session_state.get('crawl_country', saved_ui.get('crawl_country', '한국'))
        if 'dash_period' not in st.session_state:
            st.session_state['dash_period'] = st.session_state.get('crawl_period', saved_ui.get('crawl_period', '일간'))
        if 'crawl_dash_type' not in st.session_state:
            st.session_state['crawl_dash_type'] = saved_ui.get('crawl_dash_type', "📱 쇼츠 (Shorts)")
        if 'crawl_dash_layout' not in st.session_state:
            st.session_state['crawl_dash_layout'] = saved_ui.get('crawl_dash_layout', "캐러셀형 (카드 그리드)")
        if 'dash_sort_order' not in st.session_state:
            st.session_state['dash_sort_order'] = saved_ui.get('dash_sort_order', "높은 순위순")
        if 'dash_category_selected' not in st.session_state:
            st.session_state['dash_category_selected'] = saved_ui.get('dash_category_selected', "All")
            
        # 가로 버튼 나열형 필터 컴포넌트 렌더링
        criteria_opts = ["조회수 순위", "좋아요 순위", "댓글 순위"]
        selected_criteria = st.pills(
            "수집 기준 (Criteria)", 
            criteria_opts, 
            key="dash_criteria"
        )
        if not selected_criteria:
            selected_criteria = st.session_state['dash_criteria'] = '조회수 순위'
            
        country_opts = ["한국", "미국", "일본", "영국", "독일", "프랑스", "캐나다", "호주"]
        selected_country = st.pills(
            "국가 (Country)", 
            country_opts, 
            key="dash_country"
        )
        if not selected_country:
            selected_country = st.session_state['dash_country'] = '한국'
            
        period_opts = ["일간", "주간", "월간"]
        selected_period = st.pills(
            "기간 (Period)", 
            period_opts, 
            key="dash_period"
        )
        if not selected_period:
            selected_period = st.session_state['dash_period'] = '일간'
            
        # 데이터가 있는 날짜 목록 가져오기 (필터 조건 반영)
        active_dates = []
        try:
            conn = get_db_connection()
            cursor = conn.cursor()
            
            query = """
                SELECT DISTINCT substr(crawled_at, 1, 10) as date_val FROM shorts_rank WHERE country = ? AND period = ?
                UNION
                SELECT DISTINCT substr(crawled_at, 1, 10) as date_val FROM videos_rank WHERE country = ? AND period = ?
                UNION
                SELECT DISTINCT substr(crawled_at, 1, 10) as date_val FROM channels_rank WHERE country = ? AND period = ?
                ORDER BY date_val DESC
            """
            cursor.execute(query, (selected_country, selected_period, selected_country, selected_period, selected_country, selected_period))
            active_dates = [r[0] for r in cursor.fetchall() if r[0]]
            conn.close()
        except Exception as date_err:
            st.error(f"수집 날짜 조회 오류: {date_err}")
            
        if not active_dates:
            st.info(f"💡 선택된 조건(국가: '{selected_country}', 기간: '{selected_period}')으로 수집된 크롤링 데이터가 존재하지 않습니다. 유튜브 크롤러 탭에서 먼저 수집을 진행해 주세요.")
        else:
            # 날짜 선택 복원
            default_dash_date = saved_ui.get("crawl_dash_date", "")
            date_idx = active_dates.index(default_dash_date) if default_dash_date in active_dates else 0
            selected_date = st.selectbox("수집 날짜 선택", active_dates, index=date_idx, key="crawl_dash_date")
            
            col_dash1, col_dash2, col_dash3 = st.columns([1, 1, 1])
            with col_dash1:
                # 구분 선택
                type_options = ["📱 쇼츠 (Shorts)", "📺 일반 영상 (Video)", "🏢 채널 (Channel)"]
                type_val = saved_ui.get('crawl_dash_type', "📱 쇼츠 (Shorts)")
                type_idx = type_options.index(type_val) if type_val in type_options else 0
                type_tab = st.radio("데이터 구분", type_options, index=type_idx, key="crawl_dash_type", horizontal=True)
            with col_dash2:
                # 레이아웃 선택
                layout_options = ["캐러셀형 (카드 그리드)", "리스트형 (썸네일 포함)"]
                layout_val = saved_ui.get('crawl_dash_layout', "캐러셀형 (카드 그리드)")
                layout_idx = layout_options.index(layout_val) if layout_val in layout_options else 0
                layout_style = st.radio("레이아웃 스타일", layout_options, index=layout_idx, key="crawl_dash_layout", horizontal=True)
            with col_dash3:
                # 정렬 순서 선택
                sort_order_opts = ["높은 순위순", "낮은 순위순", "지표 높은순", "지표 낮은순", "비율 높은순", "비율 낮은순"]
                sort_order_val = saved_ui.get('dash_sort_order', "높은 순위순")
                sort_order_idx = sort_order_opts.index(sort_order_val) if sort_order_val in sort_order_opts else 0
                sort_order = st.selectbox("정렬 순서", sort_order_opts, index=sort_order_idx, key="dash_sort_order")
            
            # 해당 날짜의 데이터를 국가/기간 필터 적용하여 로드
            conn = get_db_connection()
            df_dash = pd.DataFrame()
            
            # 수집 기준에 따른 DB 정렬 최적화 및 실제 수치 지표 컬럼 결정
            if "채널" in type_tab:
                if selected_criteria == "조회수 순위":
                    metric_column = "total_views"
                else:
                    metric_column = "subscriber_count"
            else:
                if selected_criteria == "조회수 순위":
                    metric_column = "views"
                elif selected_criteria == "좋아요 순위":
                    metric_column = "likes"
                elif selected_criteria == "댓글 순위":
                    metric_column = "comments"
                else:
                    metric_column = "views"
            
            # 정렬 옵션 처리
            is_high_rank = sort_order == "높은 순위순"
            is_low_rank = sort_order == "낮은 순위순"
            is_high_metric = sort_order == "지표 높은순"
            is_low_metric = sort_order == "지표 낮은순"
            is_high_ratio = sort_order == "비율 높은순"
            is_low_ratio = sort_order == "비율 낮은순"
            
            if is_high_rank:
                order_clause = "rank ASC"
            elif is_low_rank:
                order_clause = "rank DESC"
            elif is_high_metric:
                order_clause = f"{metric_column} DESC, rank ASC"
            elif is_low_metric:
                order_clause = f"{metric_column} ASC, rank DESC"
            else: # 비율 높은순, 비율 낮은순
                order_clause = "rank ASC"
            
            try:
                if "쇼츠" in type_tab:
                    df_dash = pd.read_sql_query(
                        f"SELECT * FROM shorts_rank WHERE substr(crawled_at, 1, 10) = ? AND country = ? AND period = ? ORDER BY {order_clause}", 
                        conn, params=(selected_date, selected_country, selected_period)
                    )
                elif "일반 영상" in type_tab:
                    df_dash = pd.read_sql_query(
                        f"SELECT * FROM videos_rank WHERE substr(crawled_at, 1, 10) = ? AND country = ? AND period = ? ORDER BY {order_clause}", 
                        conn, params=(selected_date, selected_country, selected_period)
                    )
                elif "채널" in type_tab:
                    df_dash = pd.read_sql_query(
                        f"SELECT * FROM channels_rank WHERE substr(crawled_at, 1, 10) = ? AND country = ? AND period = ? ORDER BY {order_clause}", 
                        conn, params=(selected_date, selected_country, selected_period)
                    )
            except Exception as load_err:
                st.error(f"데이터 로드 실패: {load_err}")
            finally:
                conn.close()
                
            if not df_dash.empty:
                # 비율 계산
                is_ch = "채널" in type_tab
                
                def calc_ratio(row, is_channel):
                    sub_val = row.get('subscriber_count')
                    if is_channel:
                        views_val = row.get('total_views', 0)
                    else:
                        views_val = row.get('views', 0)
                    
                    sub_num = parse_count_string(sub_val) if sub_val is not None else 0
                    try:
                        views_num = float(views_val) if views_val is not None else 0.0
                    except:
                        views_num = 0.0
                    
                    if sub_num > 0:
                        return round(views_num / sub_num, 1)
                    return 0.0
                
                df_dash['view_sub_ratio'] = df_dash.apply(lambda r: calc_ratio(r, is_ch), axis=1)
                
                # 정렬이 비율 정렬인 경우 Pandas 정렬 사전 적용
                if is_high_ratio:
                    df_dash = df_dash.sort_values(by=['view_sub_ratio', 'rank'], ascending=[False, True])
                elif is_low_ratio:
                    df_dash = df_dash.sort_values(by=['view_sub_ratio', 'rank'], ascending=[True, True])
                
            if df_dash.empty:
                st.warning("선택한 세부 조건에 해당하는 크롤링 데이터가 없습니다.")
            else:
                # 카테고리 목록 추출 및 pills 컴포넌트 바인딩
                raw_cats = df_dash["category"].unique()
                categories_list = []
                
                # '전체' 카테고리가 존재한다면 맨 앞에 위치시킴
                has_jeonche = False
                for r_cat in raw_cats:
                    if r_cat and r_cat.replace("batch_", "") == "전체":
                        has_jeonche = True
                        break
                
                if has_jeonche:
                    categories_list.append("전체")
                
                # 그 다음 'All'을 배치
                categories_list.append("All")
                
                # 나머지 카테고리들 추가
                for r_cat in raw_cats:
                    if r_cat:
                        clean_cat = r_cat.replace("batch_", "")
                        if clean_cat not in categories_list:
                            categories_list.append(clean_cat)
                
                # 세션 상태 카테고리 디폴트 초기화
                if 'dash_category_selected' not in st.session_state:
                    st.session_state['dash_category_selected'] = 'All'
                if st.session_state['dash_category_selected'] not in categories_list:
                    st.session_state['dash_category_selected'] = categories_list[0] if categories_list else 'All'
                
                # 조회수 배율 강조 기준 설정 파라미터 추가 (카테고리 선택 위쪽 배치)
                if 'dash_highlight_ratio' not in st.session_state:
                    st.session_state['dash_highlight_ratio'] = float(saved_ui.get('dash_highlight_ratio', 10.0))
                
                highlight_ratio = st.number_input(
                    "📊 조회수 비율 강조 기준 (배 이상)",
                    min_value=0.0,
                    value=float(st.session_state['dash_highlight_ratio']),
                    step=1.0,
                    key="dash_highlight_ratio",
                    help="구독자수 대비 조회수 배율이 설정한 배수 이상일 때 대시보드에서 강조 색상(형광색)으로 표출하고, 미만일 경우 일반 조회수 폰트 스타일로 보여줍니다."
                )
                    
                selected_cat = st.pills(
                    "카테고리 선택", 
                    categories_list, 
                    key="dash_category_selected"
                )
                if not selected_cat:
                    selected_cat = st.session_state['dash_category_selected'] = categories_list[0] if categories_list else 'All'

                # 카테고리별 데이터 개수 집계 테이블 렌더링
                try:
                    df_count_temp = df_dash.copy()
                    df_count_temp['clean_category'] = df_count_temp['category'].apply(lambda x: x.replace("batch_", "") if x else "")
                    counts = df_count_temp['clean_category'].value_counts()
                    
                    ordered_counts = []
                    for c_name in categories_list:
                        if c_name == "All":
                            # All은 '전체'를 제외한 다른 개별 카테고리의 중복 제거된 총합 행 수
                            df_all_temp = df_dash[df_dash["category"].apply(lambda x: x.replace("batch_", "") if x else "") != "전체"]
                            if not df_all_temp.empty:
                                if "채널" in type_tab:
                                    all_val = len(df_all_temp.drop_duplicates(subset=["channel_name"], keep="last")) if "channel_name" in df_all_temp.columns else len(df_all_temp)
                                else:
                                    if "title" in df_all_temp.columns and "channel_name" in df_all_temp.columns:
                                        all_val = len(df_all_temp.drop_duplicates(subset=["title", "channel_name"], keep="last"))
                                    else:
                                        all_val = len(df_all_temp)
                            else:
                                all_val = 0
                            ordered_counts.append({"카테고리": "All (통합)", "데이터 개수": f"{all_val}개"})
                        else:
                            val = counts.get(c_name, 0)
                            ordered_counts.append({"카테고리": c_name, "데이터 개수": f"{val}개"})
                    
                    if ordered_counts:
                        df_counts_summary = pd.DataFrame(ordered_counts)
                        with st.expander("📊 선택 날짜의 카테고리별 데이터 수집 개수 현황", expanded=False):
                            st.dataframe(df_counts_summary, use_container_width=True, hide_index=True)
                except Exception as tbl_err:
                    logger.warning(f"카테고리별 데이터 개수 테이블 생성 실패: {tbl_err}")
                
                # 선택한 카테고리로 필터링 (SettingWithCopy 방지를 위해 명시적 .copy() 수행)
                if selected_cat != "All":
                    df_filtered = df_dash[df_dash["category"].apply(lambda x: x.replace("batch_", "") if x else "") == selected_cat].copy()
                else:
                    df_filtered = df_dash[df_dash["category"].apply(lambda x: x.replace("batch_", "") if x else "") != "전체"].copy()
                
                # 모든 카테고리 필터 결과에서 중복 영상 제거 (동적 해시 방지 위해 타이틀/채널명 조합 활용)
                # 정렬 옵션에 관계없이 항상 가장 최근 수집된(crawled_at이 최신인) 고유 단 건만 보존하여 정합성을 보장하기 위해,
                # 중복 제거 직전에 'crawled_at' 및 'id' 기준 역순 정렬을 선 수행한 뒤 keep='first'를 적용합니다.
                if not df_filtered.empty:
                    # 컬럼명 대소문자 불일치 방지를 위해 소문자화
                    df_filtered.columns = [c.lower() for c in df_filtered.columns]
                    
                    # crawled_at 정렬 정밀도 확보를 위해 datetime 형식 변환 시도
                    if 'crawled_at' in df_filtered.columns:
                        df_filtered['crawled_at_parsed'] = pd.to_datetime(df_filtered['crawled_at'], errors='coerce')
                    else:
                        df_filtered['crawled_at_parsed'] = pd.NaT

                    sort_cols = []
                    sort_asc = []
                    
                    # crawled_at_parsed가 있으면 우선 정렬 기준으로 사용
                    if 'crawled_at_parsed' in df_filtered.columns and not df_filtered['crawled_at_parsed'].isna().all():
                        sort_cols.append('crawled_at_parsed')
                        sort_asc.append(False)
                    elif 'crawled_at' in df_filtered.columns:
                        sort_cols.append('crawled_at')
                        sort_asc.append(False)
                        
                    if 'id' in df_filtered.columns:
                        sort_cols.append('id')
                        sort_asc.append(False)
                    
                    if sort_cols:
                        df_filtered = df_filtered.sort_values(by=sort_cols, ascending=sort_asc).reset_index(drop=True)

                    if "채널" in type_tab:
                        if "channel_name" in df_filtered.columns:
                            df_filtered = df_filtered.drop_duplicates(subset=["channel_name"], keep="first")
                    else:
                        if "title" in df_filtered.columns and "channel_name" in df_filtered.columns:
                            df_filtered = df_filtered.drop_duplicates(subset=["title", "channel_name"], keep="first")
                
                # 모든 필터링 및 중복 제거 완료 후, 최종적으로 사용자가 선택한 기준으로 재정렬을 수행
                if not df_filtered.empty:
                    if is_high_rank:
                        df_filtered = df_filtered.sort_values(by='rank', ascending=True)
                    elif is_low_rank:
                        df_filtered = df_filtered.sort_values(by='rank', ascending=False)
                    elif is_high_metric:
                        df_filtered = df_filtered.sort_values(by=[metric_column, 'rank'], ascending=[False, True])
                    elif is_low_metric:
                        df_filtered = df_filtered.sort_values(by=[metric_column, 'rank'], ascending=[True, True])
                    elif is_high_ratio:
                        df_filtered = df_filtered.sort_values(by=['view_sub_ratio', 'rank'], ascending=[False, True])
                    elif is_low_ratio:
                        df_filtered = df_filtered.sort_values(by=['view_sub_ratio', 'rank'], ascending=[True, True])
                
                # ⚙️ 디버그 정보 복사 기능 제공 (상단)
                with st.expander("⚙️ 디버그 정보 복사 (JSON)", expanded=False):
                    st.markdown("디버깅을 위해 아래의 JSON 데이터를 복사(우측 상단 📋 버튼 클릭)하여 AI 모델에게 전달해 주세요.")
                    
                    # 원본 CSV 파일 경로 추적
                    calc_ranking_date = selected_date
                    search_criteria = selected_criteria
                    search_cat = selected_cat if selected_cat != "All" else "ALL"
                    if search_cat != "ALL" and search_cat != "전체":
                        search_cat = f"batch_{search_cat}"
                    
                    actual_target = "shorts"
                    if "일반 영상" in type_tab:
                        actual_target = "video"
                    elif "채널" in type_tab:
                        actual_target = "channel"
                    
                    existing_filepath = find_existing_batch_file(
                        base_dir=Config.OUTPUT_DIR,
                        target_type=actual_target,
                        category=search_cat,
                        country=selected_country,
                        period=selected_period,
                        criteria=search_criteria,
                        ranking_date=calc_ranking_date
                    )
                    
                    debug_file_path = existing_filepath if existing_filepath else "DB 직접 조회 (백업 파일 없음)"
                    debug_file_name = os.path.basename(existing_filepath) if existing_filepath else "N/A"
                    
                    # 상위 1~10위 영상 데이터 축소 변환
                    debug_items = []
                    if not df_filtered.empty:
                        top_df = df_filtered.head(10)
                        for idx, (_, row) in enumerate(top_df.iterrows(), 1):
                            title_val = row.get("title", row.get("channel_name", "N/A"))
                            ch_val = row.get("channel_name", "N/A")
                            rank_val = row.get("rank", idx)
                            metric_val = 0
                            if "채널" in type_tab:
                                metric_val = row.get("total_views", row.get("subscriber_count", 0))
                            else:
                                if selected_criteria == "조회수 순위":
                                    metric_val = row.get("views", 0)
                                elif selected_criteria == "좋아요 순위":
                                    metric_val = row.get("likes", 0)
                                elif selected_criteria == "댓글 순위":
                                    metric_val = row.get("comments", 0)
                            
                            debug_items.append({
                                "순위": f"{rank_val}위",
                                "제목/채널": title_val,
                                "채널명": ch_val,
                                "주요수치": metric_val,
                                "카테고리": row.get("category", "N/A")
                            })
                    
                    debug_json_data = {
                        "디버그_실행시간": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                        "파라미터_선택값": {
                            "수집날짜": selected_date,
                            "데이터구분": type_tab,
                            "카테고리": selected_cat,
                            "수집기준": selected_criteria,
                            "국가": selected_country,
                            "기간": selected_period,
                            "레이아웃": layout_style,
                            "정렬순서": sort_order
                        },
                        "소스_파일경로": debug_file_path,
                        "소스_파일명": debug_file_name,
                        "상위_10개_영상목록": debug_items
                    }
                    
                    # JSON을 화면에 출력하고 Streamlit의 네이티브 복사를 제공
                    st.code(json.dumps(debug_json_data, ensure_ascii=False, indent=2), language="json")
                
                # 카드 호버 효과 및 보더 스타일 지정을 위한 CSS 주입 (Vibrant & Premium)
                st.html("""
                    <style>
                    /* 캐러셀형 (그리드 카드) 스타일 */
                    .dashboard-card {
                        background-color: #1e1e24;
                        border: 1px solid #2d2d34;
                        border-radius: 12px;
                        padding: 16px; /* 패딩을 키워 전체적인 세로 영역 크기를 확대 */
                        margin-bottom: 15px;
                        transition: all 0.3s ease-in-out;
                        box-shadow: 0 4px 6px rgba(0, 0, 0, 0.3);
                    }
                    .dashboard-card:hover {
                        transform: translateY(-5px);
                        border-color: #ff4b4b;
                        box-shadow: 0 10px 15px rgba(255, 75, 75, 0.2);
                    }
                    .rank-badge {
                        background: linear-gradient(135deg, #ff4b4b, #ff8585);
                        color: white;
                        border-radius: 6px;
                        padding: 2px 8px;
                        font-weight: bold;
                        font-size: 1.05em; /* 기존 0.85em에서 키움 */
                        display: inline-block;
                        margin-bottom: 5px;
                    }
                    .card-title {
                        font-size: 1.25em; /* 리스트형(1.4em) 수준의 강력한 임팩트로 확대 */
                        font-weight: bold;
                        color: #ffffff;
                        margin-top: 5px;
                        margin-bottom: 5px;
                        display: -webkit-box;
                        -webkit-line-clamp: 2;
                        -webkit-box-orient: vertical;
                        overflow: hidden;
                        height: 3.2em; /* 폰트 확대 대응 */
                        line-height: 1.6em; /* 폰트 확대 대응 */
                        cursor: pointer;
                    }
                    .card-title:hover {
                        text-decoration: underline;
                        opacity: 0.9;
                    }
                    .card-info {
                        font-size: 1.1em; /* 리스트형과 일치 */
                        color: #ffffff !important;
                    }
                    .card-channel-name {
                        font-weight: bold;
                        color: #00d2ff !important; /* 리스트형의 형광 파란색 채널명과 완벽히 일치 */
                        cursor: pointer;
                    }
                    .card-channel-name:hover {
                        text-decoration: underline;
                        opacity: 0.9;
                    }
                    
                    /* 리스트형 (디자인 개편 적용) 스타일 */
                    .list-card {
                        background-color: #1e1e24; /* 기존 어두운 회색 배경으로 원복 */
                        border: 1px solid #2d2d34; /* 테두리 복원 */
                        border-radius: 14px;
                        padding: 16px;
                        margin-bottom: 15px;
                        display: flex;
                        align-items: center;
                        transition: all 0.3s ease-in-out;
                        box-shadow: 0 4px 10px rgba(0, 0, 0, 0.4);
                    }
                    .list-card:hover {
                        transform: translateY(-3px);
                        border-color: #007bff;
                        box-shadow: 0 8px 20px rgba(0, 123, 255, 0.3);
                    }
                    .list-thumbnail-container {
                        width: 240px; /* 썸네일 가로 폭 2배 확대 */
                        height: 135px; /* 세로 크기도 비례해서 대폭 확대 */
                        margin-right: 20px;
                        flex-shrink: 0;
                        border-radius: 10px;
                        overflow: hidden;
                    }
                    .list-thumbnail {
                        width: 100%;
                        height: 100%;
                        object-fit: cover;
                    }
                    .list-content {
                        flex-grow: 1;
                        min-width: 0;
                    }
                    .list-title {
                        font-size: 1.4em; /* 제목 폰트 크기 확대 */
                        font-weight: bold;
                        color: #ffffff;
                        margin-top: 5px;
                        margin-bottom: 10px;
                        display: -webkit-box;
                        -webkit-line-clamp: 2;
                        -webkit-box-orient: vertical;
                        overflow: hidden;
                        line-height: 1.4em;
                        cursor: pointer;
                    }
                    .list-title:hover {
                        text-decoration: underline;
                        opacity: 0.9;
                    }
                    .list-badge {
                        background-color: #0056b3; /* 순위 영역만 푸른색 단색 */
                        color: white;
                        border-radius: 8px;
                        padding: 4px 12px;
                        font-weight: bold;
                        font-size: 1.4em; /* 순위 폰트 크기 확대 */
                        margin-right: 20px;
                        flex-shrink: 0;
                        width: 110px;
                        text-align: center;
                        box-shadow: 0 2px 5px rgba(0,0,0,0.3);
                    }
                    .list-info {
                        font-size: 1.1em; /* 메타 정보 폰트 크기 확대 */
                        color: #ffffff; /* 텍스트 흰색 설정 */
                        display: flex;
                        flex-wrap: wrap;
                        gap: 20px;
                    }
                    .list-channel-name {
                        font-weight: bold;
                        color: #00d2ff; /* 형광 파란색으로 가독성 확보 */
                        cursor: pointer;
                    }
                    .list-channel-name:hover {
                        text-decoration: underline;
                        opacity: 0.9;
                    }
                    .list-metric {
                        color: #ffffff; /* 조회수 폰트 흰색 */
                    }
                    


                    /* st.pills 및 st.radio 스타일 커스터마이즈 */
                    div[data-baseweb="pill"], button[kind="pills"], button[kind="pillsActive"] {
                        font-size: 1.25em !important;
                        padding: 10px 20px !important;
                        font-weight: bold !important;
                        border-radius: 8px !important;
                        transition: all 0.2s ease-in-out !important;
                        border: 1px solid #4d4d54 !important;
                        margin: 4px 6px !important;
                        height: auto !important;
                    }
                    div[data-baseweb="pill"][aria-selected="true"], button[kind="pills"][aria-selected="true"], button[kind="pillsActive"] {
                        background-color: #007bff !important;
                        color: #ffffff !important;
                        border-color: #00a2ff !important;
                        box-shadow: 0 4px 15px rgba(0, 123, 255, 0.4) !important;
                    }
                    div[data-baseweb="pill"][aria-selected="false"], button[kind="pills"][aria-selected="false"], button[kind="pills"] {
                        background-color: #31313a !important;
                        color: #ffffff !important;
                    }
                    div[data-baseweb="pill"]:hover, button[kind="pills"]:hover, button[kind="pillsActive"]:hover {
                        border-color: #007bff !important;
                        transform: translateY(-2px) !important;
                    }
                    div[data-baseweb="pill"][aria-selected="false"]:hover, button[kind="pills"][aria-selected="false"]:hover, button[kind="pills"]:hover {
                        background-color: #44444f !important;
                    }
                    div[data-baseweb="pill"][aria-selected="true"]:hover, button[kind="pills"][aria-selected="true"]:hover, button[kind="pillsActive"]:hover {
                        background-color: #0056b3 !important;
                        border-color: #00a2ff !important;
                    }
                    
                    /* st.radio 가로 버튼 바 스타일링 */
                    div[data-testid="stRadio"] div[role="radiogroup"] {
                        flex-direction: row !important;
                        gap: 10px !important;
                    }
                    div[data-testid="stRadio"] div[role="radiogroup"] label {
                        background-color: #31313a !important;
                        color: #ffffff !important;
                        padding: 10px 20px !important;
                        border-radius: 8px !important;
                        border: 1px solid #4d4d54 !important;
                        font-size: 1.25em !important;
                        font-weight: bold !important;
                        cursor: pointer !important;
                        transition: all 0.2s ease-in-out !important;
                        display: inline-flex !important;
                        align-items: center !important;
                        justify-content: center !important;
                        margin: 4px 6px !important;
                        box-shadow: none !important;
                    }
                    div[data-testid="stRadio"] div[role="radiogroup"] label:has(input:checked) {
                        background-color: #007bff !important;
                        color: #ffffff !important;
                        border-color: #00a2ff !important;
                        box-shadow: 0 4px 15px rgba(0, 123, 255, 0.4) !important;
                    }
                    div[data-testid="stRadio"] div[role="radiogroup"] label:hover {
                        background-color: #44444f !important;
                        border-color: #007bff !important;
                        transform: translateY(-2px) !important;
                    }
                    div[data-testid="stRadio"] div[role="radiogroup"] label:has(input:checked):hover {
                        background-color: #0056b3 !important;
                        border-color: #00a2ff !important;
                    }
                    div[data-testid="stRadio"] div[role="radiogroup"] label div[role="presentation"],
                    div[data-testid="stRadio"] div[role="radiogroup"] label div[data-baseweb="radio"] {
                        display: none !important;
                    }
                    div[data-testid="stRadio"] div[role="radiogroup"] label div[data-testid="stMarkdownContainer"] {
                        margin-left: 0 !important;
                    }
                    div[data-testid="stRadio"] > label {
                        font-size: 1.15em !important;
                        font-weight: bold !important;
                        color: #ffffff !important;
                    }
                    /* 복사 모달 팝업 스타일 */
                    .copy-modal-overlay {
                        display: none;
                        position: fixed;
                        top: 0;
                        left: 0;
                        width: 100%;
                        height: 100%;
                        background: rgba(0, 0, 0, 0.7);
                        backdrop-filter: blur(5px);
                        z-index: 999999;
                        align-items: center;
                        justify-content: center;
                        opacity: 0;
                        transition: opacity 0.2s ease;
                    }
                    .copy-modal-overlay.active {
                        display: flex;
                        opacity: 1;
                    }
                    .copy-modal {
                        background: #1e1e24;
                        border: 1px solid #3d3d44;
                        border-radius: 12px;
                        width: 90%;
                        max-width: 600px;
                        padding: 24px;
                        box-shadow: 0 10px 25px rgba(0,0,0,0.5);
                        color: #ffffff;
                        position: relative;
                        transform: scale(0.95);
                        transition: transform 0.2s ease;
                    }
                    .copy-modal-overlay.active .copy-modal {
                        transform: scale(1);
                    }
                    .copy-modal-header {
                        display: flex;
                        justify-content: space-between;
                        align-items: center;
                        margin-bottom: 20px;
                        border-bottom: 1px solid #2d2d34;
                        padding-bottom: 12px;
                    }
                    .copy-modal-title {
                        font-size: 1.3em;
                        font-weight: bold;
                        color: #ffffff;
                    }
                    .copy-modal-close {
                        cursor: pointer;
                        background: none;
                        border: none;
                        color: #a0a0a5;
                        font-size: 1.5em;
                        padding: 0;
                        line-height: 1;
                    }
                    .copy-modal-close:hover {
                        color: #ffffff;
                    }
                    .copy-modal-body {
                        display: flex;
                        flex-direction: column;
                        gap: 16px;
                    }
                    .copy-item-label {
                        font-size: 0.95em;
                        font-weight: bold;
                        color: #a0a0a5;
                        margin-bottom: 6px;
                    }
                    .copy-code-container {
                        position: relative;
                        background-color: #0f1115;
                        border: 1px solid #2d3139;
                        border-radius: 6px;
                        padding: 12px;
                        padding-right: 80px;
                        font-family: Source Code Pro, Consolas, Monaco, monospace;
                        font-size: 0.95em;
                        color: #e6e6e6;
                        word-break: break-all;
                        min-height: 45px;
                        display: flex;
                        align-items: center;
                    }
                    .copy-code-btn {
                        position: absolute;
                        top: 6px;
                        right: 6px;
                        background-color: #1a1c23;
                        border: 1px solid #2d3139;
                        color: #a0a0a5;
                        border-radius: 4px;
                        padding: 4px 8px;
                        font-size: 0.85em;
                        cursor: pointer;
                        transition: all 0.2s ease;
                        font-family: sans-serif;
                    }
                    .copy-code-btn:hover {
                        background-color: #2d3139;
                        color: #ffffff;
                    }
                    .copy-code-btn.copied {
                        background-color: #2ea44f;
                        color: #ffffff;
                        border-color: #2ea44f;
                    }
                    </style>

                    <!-- 복사 모달 HTML 마크업 -->
                    <div id="copyModalOverlay" class="copy-modal-overlay">
                        <div class="copy-modal">
                            <div class="copy-modal-header">
                                <span class="copy-modal-title">📋 제목 / 채널명 복사</span>
                                <button class="copy-modal-close" onclick="window.closeCopyModal()">&times;</button>
                            </div>
                            <div class="copy-modal-body">
                                <div>
                                    <div class="copy-item-label">영상 제목 (Video Title)</div>
                                    <div class="copy-code-container">
                                        <span id="copyModalTitleText"></span>
                                        <button class="copy-code-btn" onclick="window.copyModalField('copyModalTitleText', this)">복사 📋</button>
                                    </div>
                                </div>
                                <div>
                                    <div class="copy-item-label">채널명 (Channel Name)</div>
                                    <div class="copy-code-container">
                                        <span id="copyModalChannelText"></span>
                                        <button class="copy-code-btn" onclick="window.copyModalField('copyModalChannelText', this)">복사 📋</button>
                                    </div>
                                </div>
                            </div>
                        </div>
                    </div>
                """)
                
                # Iframe 샌드박스를 우회하여 부모 페이지에 이벤트 리스너를 바인딩하는 JS 컴포넌트 실행 (React 렌더링 무시 극복)
                components.html("""
                    <script>
                    const parentDoc = window.parent.document;
                    
                    // 중복 등록 방지 처리를 통해 Streamlit 갱신 시 리스너 누수 방지
                    if (!window.parent.copyModalWired) {
                        window.parent.copyModalWired = true;
                        
                        parentDoc.addEventListener('click', function(e) {
                            // 1. 카드 클릭 감지 (캐러셀 카드 및 리스트 카드 공통 대응)
                            const card = e.target.closest('.dashboard-card, .list-card');
                            if (card) {
                                let title = '';
                                let channel = '';
                                
                                if (card.classList.contains('dashboard-card')) {
                                    const titleEl = card.querySelector('.card-title');
                                    const channelEl = card.querySelector('.card-channel-name');
                                    title = titleEl ? titleEl.textContent.trim() : '';
                                    channel = channelEl ? channelEl.textContent.replace('👤', '').trim() : '';
                                } else if (card.classList.contains('list-card')) {
                                    const titleEl = card.querySelector('.list-title');
                                    const channelEl = card.querySelector('.list-channel-name');
                                    title = titleEl ? titleEl.textContent.trim() : '';
                                    channel = channelEl ? channelEl.textContent.replace('👤 채널명:', '').replace('👤', '').trim() : '';
                                }
                                
                                const overlay = parentDoc.getElementById('copyModalOverlay');
                                const titleText = parentDoc.getElementById('copyModalTitleText');
                                const channelText = parentDoc.getElementById('copyModalChannelText');
                                
                                if (overlay && titleText && channelText) {
                                    titleText.textContent = title;
                                    channelText.textContent = channel;
                                    
                                    // 복사 버튼 상태 초기화
                                    const copyBtns = overlay.querySelectorAll('.copy-code-btn');
                                    copyBtns.forEach(btn => {
                                        btn.textContent = '복사 📋';
                                        btn.style.background = '';
                                        btn.style.color = '';
                                    });
                                    
                                    overlay.style.display = 'flex';
                                    overlay.offsetHeight; // Reflow 강제 실행으로 transition 애니메이션 작동 보장
                                    overlay.style.opacity = '1';
                                }
                                return;
                            }
                            
                            // 2. 모달 닫기 클릭 감지 (X 버튼 또는 바깥 배경 레이어 클릭 시)
                            if (e.target.classList.contains('copy-modal-close') || e.target.id === 'copyModalOverlay') {
                                const overlay = parentDoc.getElementById('copyModalOverlay');
                                if (overlay) {
                                    overlay.style.opacity = '0';
                                    setTimeout(() => {
                                        overlay.style.display = 'none';
                                    }, 200);
                                }
                                return;
                            }
                            
                            // 3. 개별 항목 복사 버튼 클릭 감지
                            if (e.target.classList.contains('copy-code-btn')) {
                                const textSpan = e.target.previousElementSibling;
                                if (textSpan) {
                                    const textToCopy = textSpan.textContent;
                                    
                                    // 기본 복사 API 시도
                                    navigator.clipboard.writeText(textToCopy).then(() => {
                                        const originalText = e.target.textContent;
                                        e.target.textContent = '완료 ✓';
                                        e.target.style.background = '#2ea44f';
                                        e.target.style.color = 'white';
                                        setTimeout(() => {
                                            e.target.textContent = originalText;
                                            e.target.style.background = '';
                                            e.target.style.color = '';
                                        }, 1500);
                                    }).catch(err => {
                                        // 복사 차단 환경 대비 폴백(Fallback) 텍스트 복사 방식
                                        const textarea = parentDoc.createElement('textarea');
                                        textarea.value = textToCopy;
                                        parentDoc.body.appendChild(textarea);
                                        textarea.select();
                                        try {
                                            parentDoc.execCommand('copy');
                                            e.target.textContent = '완료 ✓';
                                            e.target.style.background = '#2ea44f';
                                            e.target.style.color = 'white';
                                            setTimeout(() => {
                                                e.target.textContent = '복사 📋';
                                                e.target.style.background = '';
                                                e.target.style.color = '';
                                            }, 1500);
                                        } catch (e2) {}
                                        parentDoc.body.removeChild(textarea);
                                    });
                                }
                                return;
                            }
                        });
                    }
                    </script>
                """, height=0)
                
                # JS 안전 문자열 정제 헬퍼 함수
                def clean_js_text(text):
                    if not text:
                        return ""
                    return str(text).replace('\\\\', '\\\\\\\\').replace("'", "\\\\'").replace('"', '&quot;').replace('\\n', ' ').replace('\\r', ' ')
                
                # 데이터 렌더링 헬퍼 함수
                def render_layout(df_data, is_list_mode):
                    if df_data.empty:
                        st.info("해당 데이터가 존재하지 않습니다.")
                        return
                    
                    show_sort_rank = sort_order != "높은 순위순"
                    
                    if is_list_mode:
                        # 리스트형 레이아웃 렌더링
                        for idx, (_, row) in enumerate(df_data.iterrows()):
                            img_url = ""
                            if "쇼츠" in type_tab or "일반 영상" in type_tab:
                                img_url = row.get("thumbnail_url", "")
                            else:
                                img_url = row.get("profile_url", "")
                                
                            if not img_url or img_url == "N/A" or not img_url.startswith("http"):
                                img_url = "https://images.unsplash.com/photo-1611162617213-7d7a39e9b1d7?w=300&auto=format&fit=crop&q=60"
                                
                            rank = row.get("rank", idx + 1)
                            rank_change = row.get("rank_change", "N/A")
                            title = row.get("title", row.get("channel_name", "이름 없음"))
                            channel_name = row.get("channel_name", "N/A")
                            views_val = row.get("views", 0)
                            likes_val = row.get("likes", 0)
                            comments_val = row.get("comments", 0)
                            
                            rc_display = ""
                            if rank_change and rank_change != "N/A":
                                if "▲" in rank_change or "+" in rank_change:
                                    rc_display = f"<span style='color:#ff8585;'>{rank_change}</span>"
                                elif "▼" in rank_change or "-" in rank_change:
                                    rc_display = f"<span style='color:#85c4ff;'>{rank_change}</span>"
                                else:
                                    rc_display = f"<span style='color:#d0d0d5;'>{rank_change}</span>"
                            else:
                                rc_display = "-"
                                
                            # 메타 지표 및 순위별 노출 데이터 조정
                            if "채널" in type_tab:
                                sub_display = f"구독자: {row.get('subscriber_count', 'N/A')}"
                                if isinstance(row.get('subscriber_count'), int):
                                    sub_display = f"구독자: {row.get('subscriber_count', 0):,}"
                                elif isinstance(row.get('subscriber_count'), str) and row.get('subscriber_count').isdigit():
                                    sub_display = f"구독자: {int(row.get('subscriber_count')):,}"
                                    
                                views_display = f"누적 조회수: {row.get('total_views', 'N/A')}"
                                if isinstance(row.get('total_views'), int):
                                    views_display = f"누적 조회수: {row.get('total_views', 0):,}"
                                
                                ratio_val = row.get('view_sub_ratio', 0.0)
                                if ratio_val >= highlight_ratio:
                                    ratio_html = f"<span class='list-metric' style='color:#00ffcc; font-weight:bold;'>📊 조회수 비율: {ratio_val:.1f}배</span>"
                                else:
                                    ratio_html = f"<span class='list-metric'>📊 조회수 비율: {ratio_val:.1f}배</span>"
                                meta_info = f"<span class='list-metric'>👥 {sub_display}</span><span class='list-metric'>📈 {views_display}</span>{ratio_html}"
                            else:
                                # 수집 기준(조회수, 좋아요, 댓글)에 맞춰 렌더링 우선순위 표시
                                views_formatted = f"{views_val:,}" if isinstance(views_val, int) else views_val
                                likes_formatted = f"{likes_val:,}" if isinstance(likes_val, int) else likes_val
                                comments_formatted = f"{comments_val:,}" if isinstance(comments_val, int) else comments_val
                                
                                ratio_val = row.get('view_sub_ratio', 0.0)
                                if ratio_val >= highlight_ratio:
                                    ratio_html = f"<span class='list-metric' style='color:#00ffcc; font-weight:bold;'>📊 조회수 비율: {ratio_val:.1f}배</span>"
                                else:
                                    ratio_html = f"<span class='list-metric'>📊 조회수 비율: {ratio_val:.1f}배</span>"
                                meta_info = f"<span class='list-metric'>👁️ 조회수: {views_formatted}</span><span class='list-metric'>❤️ 좋아요: {likes_formatted}</span><span class='list-metric'>💬 댓글: {comments_formatted}</span>{ratio_html}"
                                
                            raw_cat = row.get("category", "")
                            clean_cat = raw_cat.replace("batch_", "") if raw_cat else "N/A"
                            
                            safe_title = clean_js_text(title)
                            safe_channel = clean_js_text(channel_name)
                            
                            # 채널명 아래 구독자 수 정보 분리 추가
                            sub_html = ""
                            if "채널" not in type_tab:
                                sub_val = row.get('subscriber_count', '')
                                if sub_val and sub_val != "N/A" and sub_val != 0 and sub_val != "0":
                                    if isinstance(sub_val, int):
                                        sub_text = f"{sub_val:,}"
                                    elif isinstance(sub_val, float):
                                        sub_text = f"{int(sub_val):,}"
                                    elif isinstance(sub_val, str):
                                        if sub_val.isdigit():
                                            sub_text = f"{int(sub_val):,}"
                                        else:
                                            sub_text = sub_val
                                    else:
                                        sub_text = str(sub_val)
                                    
                                    sub_html = f'<div style="margin-top: 4px;"><span class="list-subscribers" style="color: #ffffff; font-size: 1.1em;">👥 구독자수: {sub_text}</span></div>'
                            
                            sort_rank_html = ""
                            if show_sort_rank:
                                sort_rank_html = f"<span style='background-color: #007bff; color: white; border-radius: 4px; padding: 2px 6px; font-size: 0.95em; margin-right: 10px; font-weight: bold;'>🔄 정렬순위: {idx + 1}위</span>"

                            list_content = f"""
                            <div class="list-card" onclick="window.openCopyModal('{safe_title}', '{safe_channel}')" style="cursor: pointer;" title="클릭 시 제목/채널명 복사 팝업 표시">
                                <span class="list-badge">{rank}위 ({rc_display})</span>
                                <div class="list-thumbnail-container">
                                    <img src="{img_url}" class="list-thumbnail" onerror="this.src='https://images.unsplash.com/photo-1611162617213-7d7a39e9b1d7?w=300&auto=format&fit=crop&q=60'"/>
                                </div>
                                <div class="list-content">
                                    <div class="list-title">{title}</div>
                                    <div class="list-info">
                                        <div style="margin-bottom: 4px;">
                                            <span style='background-color: #2c3e50; color: #ecf0f1; border-radius: 4px; padding: 2px 6px; font-size: 0.95em; margin-right: 10px; font-weight: bold;'>🏷️ {clean_cat}</span>
                                            {sort_rank_html}
                                            <span class="list-channel-name">👤 채널명: {channel_name}</span>
                                        </div>
                                        {sub_html}
                                        <div style="margin-top: 4px;">
                                            {meta_info}
                                        </div>
                                    </div>
                                </div>
                            </div>
                            """
                            st.html(list_content)
                    else:
                        # 캐러셀형 (그리드 카드) 레이아웃 렌더링
                        cols = st.columns(4)
                        for idx, (_, row) in enumerate(df_data.iterrows()):
                            col_idx = idx % 4
                            with cols[col_idx]:
                                img_url = ""
                                if "쇼츠" in type_tab or "일반 영상" in type_tab:
                                    img_url = row.get("thumbnail_url", "")
                                else:
                                    img_url = row.get("profile_url", "")
                                    
                                if not img_url or img_url == "N/A" or not img_url.startswith("http"):
                                    img_url = "https://images.unsplash.com/photo-1611162617213-7d7a39e9b1d7?w=300&auto=format&fit=crop&q=60"
                                    
                                rank = row.get("rank", idx + 1)
                                rank_change = row.get("rank_change", "N/A")
                                title = row.get("title", row.get("channel_name", "이름 없음"))
                                channel_name = row.get("channel_name", "N/A")
                                views_val = row.get("views", 0)
                                likes_val = row.get("likes", 0)
                                comments_val = row.get("comments", 0)
                                
                                rc_display = ""
                                if rank_change and rank_change != "N/A":
                                    if "▲" in rank_change or "+" in rank_change:
                                        rc_display = f"<span style='color:#ff4b4b;'>{rank_change}</span>"
                                    elif "▼" in rank_change or "-" in rank_change:
                                        rc_display = f"<span style='color:#4b9eff;'>{rank_change}</span>"
                                    else:
                                        rc_display = f"<span style='color:#a0a0a5;'>{rank_change}</span>"
                                else:
                                    rc_display = "-"
                                    
                                if "채널" in type_tab:
                                    sub_display = f"구독자: {row.get('subscriber_count', 'N/A')}"
                                    if isinstance(row.get('subscriber_count'), int):
                                        sub_display = f"구독자: {row.get('subscriber_count', 0):,}"
                                    elif isinstance(row.get('subscriber_count'), str) and row.get('subscriber_count').isdigit():
                                        sub_display = f"구독자: {int(row.get('subscriber_count')):,}"
                                        
                                    views_display = f"누적 조회수: {row.get('total_views', 'N/A')}"
                                    if isinstance(row.get('total_views'), int):
                                        views_display = f"누적 조회수: {row.get('total_views', 0):,}"
                                    ratio_val = row.get('view_sub_ratio', 0.0)
                                    if ratio_val >= highlight_ratio:
                                        ratio_html = f"<span style='color:#00ffcc; font-weight:bold;'>📊 조회수 비율: {ratio_val:.1f}배</span>"
                                    else:
                                        ratio_html = f"📊 조회수 비율: {ratio_val:.1f}배"
                                    metric_html = f"<div class='card-info'>👥 {sub_display}<br>📈 {views_display}<br>{ratio_html}</div>"
                                else:
                                    views_formatted = f"{views_val:,}" if isinstance(views_val, int) else views_val
                                    likes_formatted = f"{likes_val:,}" if isinstance(likes_val, int) else likes_val
                                    comments_formatted = f"{comments_val:,}" if isinstance(comments_val, int) else comments_val
                                    ratio_val = row.get('view_sub_ratio', 0.0)
                                    if ratio_val >= highlight_ratio:
                                        ratio_html = f"<span style='color:#00ffcc; font-weight:bold;'>📊 조회수 비율: {ratio_val:.1f}배</span>"
                                    else:
                                        ratio_html = f"📊 조회수 비율: {ratio_val:.1f}배"
                                    metric_html = f"<div class='card-info'>👁️ 조회수: {views_formatted}<br>❤️ 좋아요: {likes_formatted}<br>💬 댓글: {comments_formatted}<br>{ratio_html}</div>"
                                
                                raw_cat = row.get("category", "")
                                clean_cat = raw_cat.replace("batch_", "") if raw_cat else "N/A"
                                
                                safe_title = clean_js_text(title)
                                safe_channel = clean_js_text(channel_name)
                                
                                # 채널명 아래 구독자 수 정보 분리 추가
                                sub_html = ""
                                if "채널" not in type_tab:
                                    sub_val = row.get('subscriber_count', '')
                                    if sub_val and sub_val != "N/A" and sub_val != 0 and sub_val != "0":
                                        if isinstance(sub_val, int):
                                            sub_text = f"{sub_val:,}"
                                        elif isinstance(sub_val, float):
                                            sub_text = f"{int(sub_val):,}"
                                        elif isinstance(sub_val, str):
                                            if sub_val.isdigit():
                                                sub_text = f"{int(sub_val):,}"
                                            else:
                                                sub_text = sub_val
                                        else:
                                            sub_text = str(sub_val)
                                        
                                        sub_html = f'<div class="card-info card-subscribers" style="color: #ffffff; font-size: 1.1em; margin-bottom: 5px;">👥 구독자수: {sub_text}</div>'
                                
                                # 콘텐츠 구분(type_tab)에 따라 최적의 썸네일 이미지 스타일 결정
                                img_style = "width:100%; border-radius:8px; object-fit:cover; margin-bottom:8px;"
                                if "쇼츠" in type_tab:
                                    img_style += " aspect-ratio: 2/3;" # 쇼츠 세로 크기 확대 최적화 (4/5 -> 2/3)
                                elif "일반 영상" in type_tab:
                                    img_style += " aspect-ratio: 1.1/1;" # 일반 영상 세로 크기 확대 최적화 (4/3 -> 1.1/1)
                                else: # 채널
                                    img_style = "width:150px; height:150px; border-radius:50%; object-fit:cover; margin: 0 auto 12px auto; display:block; box-shadow: 0 4px 8px rgba(0,0,0,0.2);" # 채널 프로필 크기 확대 (120px -> 150px)
                                
                                sort_rank_html = ""
                                if show_sort_rank:
                                    sort_rank_html = f"<span style='background-color: #007bff; color: white; border-radius: 4px; padding: 2px 6px; font-size: 0.85em; font-weight: bold; margin-left: 5px; display: inline-block; vertical-align: middle; margin-bottom: 5px;'>🔄 정렬순위: {idx + 1}위</span>"

                                card_content = f"""
                                <div class="dashboard-card" onclick="window.openCopyModal('{safe_title}', '{safe_channel}')" style="cursor: pointer;" title="클릭 시 제목/채널명 복사 팝업 표시">
                                    <span class="rank-badge">{rank}위 ({rc_display})</span>
                                    <span style='background-color: #2c3e50; color: #ecf0f1; border-radius: 4px; padding: 2px 6px; font-size: 0.85em; font-weight: bold; margin-left: 5px; display: inline-block; vertical-align: middle; margin-bottom: 5px;'>🏷️ {clean_cat}</span>
                                    {sort_rank_html}
                                    <img src="{img_url}" style="{img_style}" onerror="this.src='https://images.unsplash.com/photo-1611162617213-7d7a39e9b1d7?w=300&auto=format&fit=crop&q=60'"/>
                                    <div class="card-title">{title}</div>
                                    <div class="card-info card-channel-name" style="font-weight:bold; margin-bottom:2px;">👤 {channel_name}</div>
                                    {sub_html}
                                    {metric_html}
                                </div>
                                """
                                st.html(card_content)
                
                # 레이아웃 모드 판정
                is_list = "리스트" in layout_style
                
                # 렌더링 시작
                if selected_cat == "All":
                    # All 카테고리 선택 시에는 모든 카테고리를 하나의 단일 리스트로 일렬 병합하여 출력합니다.
                    render_layout(df_filtered, is_list)
                else:
                    # 선택된 개별 카테고리(종합 "전체" 카테고리 포함) 단독 렌더링
                    render_layout(df_filtered, is_list)
                
                # ⚙️ 디버그 정보 복사 기능 제공
                with st.expander("⚙️ 디버그 정보 복사 (JSON)", expanded=False):
                    st.markdown("디버깅을 위해 아래의 JSON 데이터를 복사(우측 상단 📋 버튼 클릭)하여 AI 모델에게 전달해 주세요.")
                    
                    # 원본 CSV 파일 경로 추적
                    calc_ranking_date = selected_date
                    search_criteria = selected_criteria
                    search_cat = selected_cat if selected_cat != "All" else "ALL"
                    if search_cat != "ALL" and search_cat != "전체":
                        search_cat = f"batch_{search_cat}"
                    
                    actual_target = "shorts"
                    if "일반 영상" in type_tab:
                        actual_target = "video"
                    elif "채널" in type_tab:
                        actual_target = "channel"
                    
                    existing_filepath = find_existing_batch_file(
                        base_dir=Config.OUTPUT_DIR,
                        target_type=actual_target,
                        category=search_cat,
                        country=selected_country,
                        period=selected_period,
                        criteria=search_criteria,
                        ranking_date=calc_ranking_date
                    )
                    
                    debug_file_path = existing_filepath if existing_filepath else "DB 직접 조회 (백업 파일 없음)"
                    debug_file_name = os.path.basename(existing_filepath) if existing_filepath else "N/A"
                    
                    # 상위 1~10위 영상 데이터 축소 변환
                    debug_items = []
                    if not df_filtered.empty:
                        top_df = df_filtered.head(10)
                        for idx, (_, row) in enumerate(top_df.iterrows(), 1):
                            title_val = row.get("title", row.get("channel_name", "N/A"))
                            ch_val = row.get("channel_name", "N/A")
                            rank_val = row.get("rank", idx)
                            metric_val = 0
                            if "채널" in type_tab:
                                metric_val = row.get("total_views", row.get("subscriber_count", 0))
                            else:
                                if selected_criteria == "조회수 순위":
                                    metric_val = row.get("views", 0)
                                elif selected_criteria == "좋아요 순위":
                                    metric_val = row.get("likes", 0)
                                elif selected_criteria == "댓글 순위":
                                    metric_val = row.get("comments", 0)
                            
                            debug_items.append({
                                "순위": f"{rank_val}위",
                                "제목/채널": title_val,
                                "채널명": ch_val,
                                "주요수치": metric_val,
                                "카테고리": row.get("category", "N/A")
                            })
                    
                    debug_json_data = {
                        "디버그_실행시간": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                        "파라미터_선택값": {
                            "수집날짜": selected_date,
                            "데이터구분": type_tab,
                            "카테고리": selected_cat,
                            "수집기준": selected_criteria,
                            "국가": selected_country,
                            "기간": selected_period,
                            "레이아웃": layout_style,
                            "정렬순서": sort_order
                        },
                        "소스_파일경로": debug_file_path,
                        "소스_파일명": debug_file_name,
                        "상위_10개_영상목록": debug_items
                    }
                    
                    # JSON을 화면에 출력하고 Streamlit의 네이티브 복사를 제공
                    st.code(json.dumps(debug_json_data, ensure_ascii=False, indent=2), language="json")
                    
    with crawl_sub_tabs[1]:
        st.subheader("🔍 크롤링 데이터 상세 검색")
        
        if 'ui_settings' not in st.session_state or st.session_state['ui_settings'] is None:
            st.session_state['ui_settings'] = load_settings()
        saved_ui = st.session_state['ui_settings']
        
        col_c1, col_c2, col_c3 = st.columns([2, 1, 1])
        c_keyword = col_c1.text_input("검색 키워드 (제목, 채널명, 태그)", value=saved_ui.get("crawl_search_keyword", ""), key="crawl_search_keyword")
        
        c_type_options = ["전체", "shorts", "video", "channel"]
        c_type_val = saved_ui.get("crawl_search_type", "전체")
        c_type_idx = c_type_options.index(c_type_val) if c_type_val in c_type_options else 0
        c_type = col_c2.selectbox("컨텐츠 타입", c_type_options, index=c_type_idx, key="crawl_search_type")
        
        c_sort_options = ["최근 등록일순", "조회수 높은순", "높은 순위순", "낮은 순위순"]
        c_sort_val = saved_ui.get("crawl_search_sort", "최근 등록일순")
        c_sort_idx = c_sort_options.index(c_sort_val) if c_sort_val in c_sort_options else 0
        c_sort = col_c3.selectbox("정렬 기준", c_sort_options, index=c_sort_idx, key="crawl_search_sort")
        
        with st.expander("⚙️ 고급 필터"):
            col_cf1, col_cf2 = st.columns(2)
            c_view_min = col_cf1.number_input("최소 조회수", min_value=0, value=int(saved_ui.get("crawl_search_view_min", 0)), step=1000, key="crawl_search_view_min")
            c_view_max = col_cf2.number_input("최대 조회수", min_value=0, value=int(saved_ui.get("crawl_search_view_max", 1000000000)), step=100000, key="crawl_search_view_max")
            
        c_search_btn = st.button("🔍 크롤링 데이터 검색 실행", width='stretch', key="crawl_search_btn")
        
        if c_search_btn or c_keyword:
            try:
                conn = get_db_connection()
                cursor = conn.cursor()
                c_results = []
                
                if c_type in ["전체", "shorts"] and 'shorts_rank' in tables:
                    cursor.execute('''
                        SELECT 'shorts_rank' as source, video_id, title, channel_name,
                               views, rank, category, country, crawled_at
                        FROM shorts_rank
                        WHERE (title LIKE ? OR channel_name LIKE ?) AND views >= ? AND views <= ?
                    ''', (f'%{c_keyword}%', f'%{c_keyword}%', c_view_min, c_view_max))
                    c_results.extend([dict(row) for row in cursor.fetchall()])
                    
                if c_type in ["전체", "video"] and 'videos_rank' in tables:
                    cursor.execute('''
                        SELECT 'videos_rank' as source, video_id, title, channel_name,
                               views, rank, category, country, crawled_at
                        FROM videos_rank
                        WHERE (title LIKE ? OR channel_name LIKE ?) AND views >= ? AND views <= ?
                    ''', (f'%{c_keyword}%', f'%{c_keyword}%', c_view_min, c_view_max))
                    c_results.extend([dict(row) for row in cursor.fetchall()])
                    
                if c_type in ["전체", "channel"] and 'channels_rank' in tables:
                    cursor.execute('''
                        SELECT 'channels_rank' as source, channel_id as video_id, channel_name as title, channel_name,
                               subscriber_count as views, rank, category, country, crawled_at
                        FROM channels_rank
                        WHERE channel_name LIKE ? AND subscriber_count >= ? AND subscriber_count <= ?
                    ''', (f'%{c_keyword}%', c_view_min, c_view_max))
                    c_results.extend([dict(row) for row in cursor.fetchall()])
                    
                conn.close()
                
                if c_results:
                    df_c_res = pd.DataFrame(c_results)
                    if c_sort == "최근 등록일순":
                        df_c_res = df_c_res.sort_values(by="crawled_at", ascending=False)
                    elif c_sort == "조회수 높은순":
                        df_c_res = df_c_res.sort_values(by="views", ascending=False)
                    elif c_sort == "높은 순위순":
                        df_c_res = df_c_res.sort_values(by="rank", ascending=True)
                    elif c_sort == "낮은 순위순":
                        df_c_res = df_c_res.sort_values(by="rank", ascending=False)
                        
                    st.dataframe(df_c_res, use_container_width=True)
                    st.success(f"🔍 검색 완료: 총 {len(df_c_res):,}건 발견")
                else:
                    st.warning("검색 조건에 일치하는 크롤링 데이터가 없습니다.")
            except Exception as q_err:
                st.error(f"조회 중 오류 발생: {q_err}")
                
    with crawl_sub_tabs[2]:
        st.subheader("📊 크롤링 데이터 트렌드 시각화")
        
        tab_fig1, tab_fig2 = st.columns(2)
        
        with tab_fig1:
            st.markdown("**인기 수집 카테고리 분포**")
            try:
                conn = get_db_connection()
                df_cat_s = pd.read_sql_query("SELECT category, COUNT(*) as count FROM shorts_rank GROUP BY category", conn)
                conn.close()
                
                if not df_cat_s.empty:
                    df_cat_s['category'] = df_cat_s['category'].str.replace('batch_', '')
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
# 대형 외부 프로그램 구동을 위한 백그라운드 Worker 스레드 정의
# ==============================================================================

def run_extractor_work(ext_state, trans_type, sheet_url, sheet_name, mode_val, limit_val, timestamp_val, concurrency_val, delay_val, use_browser_val, headless_val, use_profile_val, creds_val):
    """자막 추출기 백그라운드 스레드 작업 실행 함수"""
    import os
    import sys
    import time
    
    # 자격증명 경로 절대경로 보정 (공유 키 지원용)
    if creds_val:
        creds_val = os.path.abspath(creds_val)
        
    current_dir = os.path.dirname(os.path.abspath(__file__))
    if trans_type == "롱폼 대본 추출기":
        project_dir = os.path.join(current_dir, "외부프로그램", "롱폼-대본추출기")
    else:
        project_dir = os.path.join(current_dir, "외부프로그램", "숏폼-대본추출기")
        
    gui_file = os.path.join(project_dir, "GUI_Extract.py")
    main_file = os.path.join(project_dir, "Main_Extract.py")
    
    try:
        # 격리 모듈 동적 로드
        ext_gui = load_isolated_module("ext_gui_module", gui_file, project_dir)
        ext_main = load_isolated_module("ext_main_module", main_file, project_dir)
        
        # 로그 리디렉션 핸들러 생성
        import logging
        class ThreadLogHandler(logging.Handler):
            def emit(self, record):
                log_entry = self.format(record)
                ext_state.log_history.append(log_entry)
                if len(ext_state.log_history) > 150:
                    ext_state.log_history.pop(0)
                    
        th_handler = ThreadLogHandler()
        th_formatter = logging.Formatter('[%(asctime)s] %(levelname)-8s - %(message)s', '%H:%M:%S')
        th_handler.setFormatter(th_formatter)
        
        # 격리 모듈 로거 획득 및 핸들러 연동
        logger_gui = logging.getLogger("ext_gui_module")
        logger_main = logging.getLogger("ext_main_module")
        
        # 기존 핸들러 제거 후 추가 (중복 방지)
        for h in list(logger_gui.handlers):
            logger_gui.removeHandler(h)
        for h in list(logger_main.handlers):
            logger_main.removeHandler(h)
            
        logger_gui.addHandler(th_handler)
        logger_main.addHandler(th_handler)
        
        # 모듈 전역 중단 신호 리셋
        ext_main.interrupt_controller.should_stop = False
        
        ext_state.status = "구글 시트 인증 중..."
        ext_state.log_history.append("🔐 구글 서비스 계정 인증 시도 중...")
        
        # GoogleSheetsManager 인스턴스 생성
        sheets_manager = ext_gui.GoogleSheetsManager(creds_val)
        sheets_manager.authenticate()
        ext_state.log_history.append("✓ 서비스 계정(gspread client) 인증 성공")
        
        # OAuth2 클라이언트 파일이 존재한다면 자동 OAuth인증 수행
        if os.path.exists(sheets_manager.oauth_client_path):
            try:
                sheets_manager.authenticate_oauth()
                ext_state.log_history.append("✓ Google Drive & Docs API OAuth2 인증 성공")
            except Exception as oauth_err:
                ext_state.log_history.append(f"⚠️ OAuth2 인증 대기 또는 실패 (드라이브 저장 불가할 수 있음): {oauth_err}")
                
        ext_state.status = "시트 분석 및 영상 목록 파싱 중..."
        
        # 모드 문자 변환 ('A', 'B', 'C')
        m_char = 'A'
        if "모드 B" in mode_val:
            m_char = 'B'
        elif "모드 C" in mode_val:
            m_char = 'C'
            
        video_ids, start_row, transcript_col, row_mapping = sheets_manager.get_video_ids_from_sheet(
            sheet_url, sheet_name, mode=m_char, max_count=limit_val
        )
        
        if not video_ids:
            ext_state.status = "추출 완료 (대상 없음)"
            ext_state.log_history.append("✓ 처리할 영상 ID가 없습니다. (수집 조건을 이미 모두 만족함)")
            ext_state.progress = 1.0
            ext_state.is_running = False
            ext_state.result = {
                "status": "success",
                "msg": "✓ 자막 추출을 진행할 영상 ID가 없습니다. (모두 이미 수집됨)"
            }
            return
            
        ext_state.log_history.append(f"🎬 추출 대상 비디오 개수: {len(video_ids)}개 (시작 행: {start_row})")
        
        # Config 설정 구성
        config = ext_main.TranscriptConfig(
            max_concurrent=concurrency_val,
            delay_between_requests=delay_val,
            use_browser_automation=use_browser_val,
            headless=headless_val,
            use_user_profile=use_profile_val
        )
        
        ext_state.status = "브라우저 및 자막 추출기 가동 중..."
        extractor = ext_main.BrowserTranscriptExtractor(config)
        extractor.start_browser()
        
        results = []
        ext_state.status = "대본 추출 중..."
        
        for idx, vid in enumerate(video_ids, 1):
            if ext_state.stop_requested or ext_main.interrupt_controller.should_stop:
                ext_state.log_history.append("🛑 사용자 요청에 의해 처리가 강제 중단되었습니다.")
                break
                
            ext_state.status = f"대본 추출 및 업데이트 중 ({idx}/{len(video_ids)})..."
            ext_state.progress = float(idx - 1) / len(video_ids)
            
            try:
                # 단 건 추출 실행
                single_res = extractor.extract_transcript_from_video(vid)
                results.append(single_res)
                
                current_row = row_mapping[idx - 1] if row_mapping else (start_row + idx - 1)
                
                # 단 건 실시간 시트 업데이트
                sheets_manager.update_sheet_with_transcripts(
                    sheet_url=sheet_url,
                    sheet_name=sheet_name,
                    video_data_list=[single_res],
                    start_row=start_row,
                    transcript_col=transcript_col,
                    include_timestamp=timestamp_val,
                    row_mapping=[current_row]
                )
            except Exception as single_err:
                ext_state.log_history.append(f"⚠️ {vid} 처리 및 업데이트 실패: {single_err}")
                
            # 지연 대기
            if idx < len(video_ids) and not (ext_state.stop_requested or ext_main.interrupt_controller.should_stop):
                time.sleep(delay_val)
                
        # 추출기 브라우저 종료
        try:
            extractor.close_browser()
        except:
            pass
            
        ext_state.progress = 1.0
        ext_state.is_running = False
        
        success_count = sum(1 for r in results if not r.error)
        fail_count = len(results) - success_count
        
        ext_state.result = {
            "status": "success",
            "msg": f"✓ 대본 추출 완료: 성공 {success_count}개, 실패 {fail_count}개",
            "data": [{"video_id": r.video_id, "title": r.title, "length": len(r.transcript) if r.transcript else 0, "error": r.error} for r in results]
        }
        
    except Exception as work_err:
        ext_state.is_running = False
        ext_state.result = {
            "status": "error",
            "msg": f"✗ 대본 추출 중 오류 발생: {work_err}"
        }
        ext_state.log_history.append(f"❌ 작업 도중 예외 발생: {work_err}")


def run_search_work(search_state, search_type, keyword, limit, order, duration, sheet_url, sheet_name, creds_path):
    """유튜브 키워드 검색 백그라운드 스레드 작업 실행 함수"""
    import os
    import sys
    import time
    from datetime import datetime
    
    # 자격증명 경로 절대경로 보정 (공유 키 지원용)
    if creds_path:
        creds_path = os.path.abspath(creds_path)
        
    current_dir = os.path.dirname(os.path.abspath(__file__))
    if search_type == "롱폼 유튜브 검색기":
        project_dir = os.path.join(current_dir, "외부프로그램", "롱폼-유튜브검색기")
    else:
        project_dir = os.path.join(current_dir, "외부프로그램", "숏폼-유튜브검색기")
        
    gui_file = os.path.join(project_dir, "GUI_Interface.py")
    main_file = os.path.join(project_dir, "Main_Search.py")
    
    try:
        # 격리 모듈 동적 로드
        ext_gui = load_isolated_module("ext_search_gui_module", gui_file, project_dir)
        ext_main = load_isolated_module("ext_search_main_module", main_file, project_dir)
        
        search_state.log_history.append("🔐 구글 서비스 계정 인증 시도 중...")
        
        # 1. GoogleSheetsManager 생성 및 인증
        sheets_manager = ext_gui.GoogleSheetsManager(creds_path, sheet_url)
        search_state.log_history.append("✓ 구글 시트 gspread 인증 완료")
        
        # 2. YouTube Data API 인증
        client_secret_file = None
        secret_dir = os.path.join(current_dir, "google_service_key")
        if os.path.exists(secret_dir):
            for file in os.listdir(secret_dir):
                if file.startswith("client_secret") and file.endswith(".json"):
                    client_secret_file = os.path.join(secret_dir, file)
                    break
                    
        youtube_api = ext_main.YouTubeSearchAPI()
        
        if client_secret_file:
            search_state.log_history.append("🔑 OAuth2 client_secret 파일 발견. OAuth 인증 실행 중...")
            youtube_api.authenticate_oauth(client_secret_file)
        else:
            api_key_file = os.path.join(secret_dir, "api_key.txt")
            if os.path.exists(api_key_file):
                search_state.log_history.append("🔑 API Key 텍스트 발견. Key 인증 실행 중...")
                with open(api_key_file, "r") as f:
                    api_key = f.read().strip()
                youtube_api.authenticate_api_key(api_key)
            else:
                raise FileNotFoundError("GCP 연동용 client_secret JSON 또는 api_key.txt를 google_service_key 폴더 아래에서 찾을 수 없습니다.")
                
        search_state.log_history.append("✓ YouTube Data API v3 인증 완료")
        
        # 중복 체크 1단계: 검색 전 DB에 존재하는 영상 ID 목록 미리 획득
        try:
            import sqlite3
            conn = sqlite3.connect(os.path.join(current_dir, DB_PATH))
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("SELECT video_id FROM sheet_videos")
            existing_video_ids = {r[0] for r in cursor.fetchall() if r[0]}
            conn.close()
            search_state.log_history.append(f"ℹ️ 로컬 DB 내 기존 영상 ID {len(existing_video_ids)}개 확인 완료 (중복 감지 활성화)")
        except Exception as db_init_err:
            existing_video_ids = set()
            search_state.log_history.append(f"⚠️ 기존 영상 ID 확인 실패 (기본값 처리): {db_init_err}")

        # 유튜브 검색 시 타겟 탭을 무조건 '키워드 검색결과'로 강제
        sheet_name = "키워드 검색결과"
        search_state.log_history.append(f"🔍 유튜브 검색 실행: 키워드='{keyword}', 최대={limit}개, 정렬={order}, 길이={duration}")
        
        # 검색 실행
        results, stats = youtube_api.search_videos(
            keyword=keyword,
            max_results=limit,
            order=order,
            video_duration=None if duration == 'any' else duration
        )
        
        if not results:
            search_state.log_history.append("⚠️ 조건에 부합하는 유튜브 검색 결과가 없습니다.")
            search_state.is_running = False
            search_state.result = {
                "status": "warning",
                "msg": "검색 결과가 존재하지 않습니다."
            }
            return
            
        search_state.log_history.append(f"✓ 검색 완료! {len(results)}개 영상 획득 (Quota 소모: {stats.get('quota_cost')} pts)")
        
        # 중복 체크 2단계: 신규 데이터 중복 제거 필터링
        filtered_results = []
        skipped_count = 0
        for r in results:
            v_id = r.get('영상 ID') or r.get('video_id')
            if v_id and v_id in existing_video_ids:
                skipped_count += 1
                continue
            filtered_results.append(r)
            
        if skipped_count > 0:
            search_state.log_history.append(f"ℹ️ 기존 DB에 이미 존재하는 영상 {skipped_count}개를 제외(패스)했습니다.")
            
        if not filtered_results:
            search_state.log_history.append("⚠️ 모든 검색 결과 영상이 이미 DB에 존재하여 저장을 패스합니다.")
            search_state.is_running = False
            search_state.result = {
                "status": "success",
                "msg": f"✓ 중복 제외 완료 (모든 결과 {len(results)}개가 이미 DB에 존재함)",
                "data": []
            }
            return
            
        # 3. 구글 시트 저장
        search_state.log_history.append(f"📝 구글 시트 '{sheet_name}' 탭에 벌크 데이터 추가 시작 (신규 {len(filtered_results)}개)...")
        
        def progress_cb(current, total, message):
            search_state.log_history.append(f"  └> {message}")
            
        # 벌크 저장 (10행부터, 포맷 보존)
        sheets_manager.bulk_append_data_in_batches(
            sheet_name=sheet_name,
            data_list=filtered_results,
            batch_size=100,
            start_row=10,
            progress_callback=progress_cb
        )
        
        # 4. 로컬 DB 'sheet_videos' 에도 저장 (tab_name='키워드 검색결과')
        search_state.log_history.append("💾 로컬 DB 'sheet_videos' 테이블 적재 및 통계 수식 자동 계산 중...")
        try:
            from modules.utils import match_db_column_by_header, calculate_sheet_video_metrics
            conn = sqlite3.connect(os.path.join(current_dir, DB_PATH))
            cursor = conn.cursor()
            
            # DB 컬럼 조회
            cursor.execute("PRAGMA table_info(sheet_videos)")
            db_cols = [col['name'] for col in cursor.fetchall()]
            
            inserted_db_count = 0
            for idx, r in enumerate(filtered_results):
                row_dict = {}
                # 헤더명을 DB 컬럼명으로 동적 변환
                for kr_header, val in r.items():
                    db_col = match_db_column_by_header(kr_header, db_cols)
                    if db_col:
                        row_dict[db_col] = val
                
                vid = row_dict.get('video_id')
                if not vid:
                    continue
                    
                row_dict['tab_name'] = sheet_name
                row_dict['original_row_order'] = 10 + idx  # 10행부터 삽입되므로 오더 보정
                
                # 파이썬 기반 통계 자동 계산
                row_dict = calculate_sheet_video_metrics(row_dict)
                
                # DB Upsert 실행
                columns = list(row_dict.keys())
                placeholders = ', '.join(['?'] * len(columns))
                sql = f"INSERT OR REPLACE INTO sheet_videos ({', '.join(columns)}) VALUES ({placeholders})"
                cursor.execute(sql, [row_dict[col] for col in columns])
                inserted_db_count += 1
                
            conn.commit()
            conn.close()
            search_state.log_history.append(f"✓ 로컬 DB 'sheet_videos' ({sheet_name})에 {inserted_db_count}개 행 적재 완료")
        except Exception as db_save_err:
            logger.error(f"검색 결과 DB 저장 실패: {db_save_err}", exc_info=True)
            search_state.log_history.append(f"⚠️ 로컬 DB 저장 중 실패 (구글 시트는 저장 완료): {db_save_err}")
            
        search_state.log_history.append("✓ 구글 시트 저장 및 수식 열 자동 보호 완료!")
        search_state.is_running = False
        search_state.result = {
            "status": "success",
            "msg": f"✓ 검색 및 시트/DB 저장 완료: 총 {len(filtered_results)}개 항목 추가됨 (중복 {skipped_count}개 패스)",
            "data": [{"순위": i, "제목": r["제목"], "채널명": r["채널명"], "조회수": r["조회수"]} for i, r in enumerate(filtered_results, 1)]
        }
        
    except Exception as search_err:
        search_state.is_running = False
        search_state.result = {
            "status": "error",
            "msg": f"✗ 검색 진행 중 에러 발생: {search_err}"
        }
        search_state.log_history.append(f"❌ 작업 에러 발생: {search_err}")


def run_collection_work(
    state,
    mode,  # 'channel', 'video', 'playlist', 'bulk_channel'
    channel_input,
    video_input,
    sync_videos,
    sync_channel,
    video_range,
    recent_limit,
    since_year,
    bulk_channels_list=None,
    selected_playlist_ids=None,
    sheet_urls=None
):
    try:
        # 헬퍼 함수: 백그라운드 구글 스프레드시트 동기화
        def auto_sync_to_google_sheet(mode, sync_videos, sync_channel, sheet_urls, log_history_list):
            if not sheet_urls:
                return
            
            from modules.sheet_sync import sync_db_to_sheet
            # 각 모드별 동기화할 탭 선별
            tabs_to_sync = []
            if mode == 'channel':
                tabs_to_sync.append("채널 리스트")
                if sync_videos:
                    tabs_to_sync.append("영상 리스트")
            elif mode == 'video':
                tabs_to_sync.append("영상 리스트")
                if sync_channel:
                    tabs_to_sync.append("채널 리스트")
            elif mode == 'bulk_channel':
                tabs_to_sync.append("채널 리스트")
                if sync_videos:
                    tabs_to_sync.append("영상 리스트")
            elif mode == 'playlist':
                tabs_to_sync.append("재생목록ID")
                tabs_to_sync.append("유튜브 재생목록")
                tabs_to_sync.append("채널 리스트")
                
            for tab in tabs_to_sync:
                url = sheet_urls.get(tab)
                if not url:
                    log_history_list.append(f"[{datetime.now().strftime('%H:%M:%S')}] ⚠️ '{tab}' 탭 구글시트 URL이 설정되지 않아 동기화를 건너뜁니다.")
                    continue
                try:
                    log_history_list.append(f"[{datetime.now().strftime('%H:%M:%S')}] 구글 시트 '{tab}' 동기화 시작...")
                    exported_rows = sync_db_to_sheet(url, tab_name=tab)
                    log_history_list.append(f"[{datetime.now().strftime('%H:%M:%S')}] ✓ 구글 시트 '{tab}' 동기화 완료 ({exported_rows}개 행)")
                except Exception as e:
                    log_history_list.append(f"[{datetime.now().strftime('%H:%M:%S')}] ❌ '{tab}' 탭 구글시트 동기화 실패: {e}")

        state.is_running = True
        state.status = "수집 준비 중..."
        state.progress = 0.0
        state.log_history = [f"[{datetime.now().strftime('%H:%M:%S')}] 유튜브 데이터 수집 스레드가 구동되었습니다."]
        
        # 1. API 클라이언트 초기화 확인
        if not youtube_manager.youtube:
            youtube_manager._init_youtube_api()
        if not youtube_manager.youtube:
            raise Exception("유튜브 API 클라이언트를 초기화할 수 없습니다. API Key 설정을 확인하세요.")
            
        import re
        from modules.youtube_utils import get_channel_id_from_url, extract_video_id_from_url
        
        # Helper: 재생목록 ID 추출
        def extract_playlist_id(url):
            m = re.search(r'[&?]list=([^&]+)', url)
            if m:
                return m.group(1)
            if url.startswith('PL') or url.startswith('UU'):
                return url
            return None

        if mode == 'channel':
            state.status = "채널 정보 수집 중..."
            state.log_history.append(f"[{datetime.now().strftime('%H:%M:%S')}] 채널 정보 수집 시작: '{channel_input}'")
            
            channel_id = get_channel_id_from_url(channel_input)
            use_search = False
            channel_name = None
            if not channel_id:
                use_search = True
                channel_name = channel_input
                state.log_history.append(f"[{datetime.now().strftime('%H:%M:%S')}] 입력값이 채널 URL 형식이 아닙니다. 검색 복구를 활성화합니다.")
                
            res = youtube_manager.sync_channel(
                channel_url=channel_input if channel_id else "",
                channel_name=channel_name,
                subscriber_count=None,
                use_search_fallback=use_search
            )
            
            if not res['success']:
                raise Exception(f"채널 정보 수집 실패: {res.get('error', '알 수 없는 오류')}")
                
            target_channel_id = res['channel_id']
            
            # channel_name 등 정보 획득을 위해 DB 재조회
            conn = sqlite3.connect(youtube_manager.db_path)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("SELECT channel_name FROM sheet_channels WHERE channel_id = ?", (target_channel_id,))
            ch_row = cursor.fetchone()
            conn.close()
            
            ch_title = ch_row['channel_name'] if ch_row else "알 수 없음"
            state.log_history.append(f"[{datetime.now().strftime('%H:%M:%S')}] ✓ 채널 등록 성공: '{ch_title}' ({target_channel_id}) (쿼터 소모: {res.get('quota_used', 0)})")
            state.progress = 0.5
            
            if sync_videos:
                state.status = "연동 영상 수집 중..."
                state.log_history.append(f"[{datetime.now().strftime('%H:%M:%S')}] 채널 연동 영상 수집 시작 (옵션: {video_range})")
                
                if video_range == 'single':
                    state.log_history.append(f"[{datetime.now().strftime('%H:%M:%S')}] ⚠️ 경고: 채널 연동 수집에서 '단일 영상 수집'은 불가능합니다. 최근 50개 영상으로 대체합니다.")
                    limit_val = 50
                    fetch_all = False
                    since_y = None
                elif video_range == 'recent':
                    limit_val = recent_limit
                    fetch_all = False
                    since_y = None
                elif video_range == 'all':
                    limit_val = 50
                    fetch_all = True
                    since_y = None
                elif video_range == 'year':
                    limit_val = 50
                    fetch_all = True
                    since_y = since_year
                    
                v_res = youtube_manager.fetch_videos(
                    channel_id=target_channel_id,
                    limit=limit_val,
                    fetch_all=fetch_all,
                    since_year=since_y,
                    sync_channel_info=False
                )
                
                if not v_res['success']:
                    raise Exception(f"연동 영상 수집 중 오류: {v_res.get('error', '알 수 없는 오류')}")
                    
                state.log_history.append(f"[{datetime.now().strftime('%H:%M:%S')}] ✓ 연동 영상 수집 완료. 일반영상: {v_res.get('video_count', 0)}개, 쇼츠: {v_res.get('shorts_count', 0)}개 (쿼터 소모: {v_res.get('quota_used', 0)})")
                
                state.status = "실시간 채널 통계 갱신 중..."
                youtube_manager.update_channel_metrics(target_channel_id)
                state.log_history.append(f"[{datetime.now().strftime('%H:%M:%S')}] ✓ 채널 통계 실시간 최신화 완료 (sheet_channels)")
            
            state.progress = 0.9
            state.status = "구글 시트 동기화 중..."
            auto_sync_to_google_sheet(mode, sync_videos, False, sheet_urls, state.log_history)
            
            summary_row = {
                "채널 ID": target_channel_id,
                "채널명": ch_title,
                "구독자수": res.get('subs', 'N/A') if res else 'N/A',
                "총 영상수": res.get('videos', 'N/A') if res else 'N/A',
                "수집 비디오": f"일반 {v_res.get('video_count', 0)}개 / 쇼츠 {v_res.get('shorts_count', 0)}개" if sync_videos and 'v_res' in locals() else "미수집",
                "수집 시간": datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            }
            summary_df = pd.DataFrame([summary_row])
            
            state.progress = 1.0
            state.result = {
                "status": "success",
                "msg": f"✓ '{ch_title}' 채널 및 데이터 수집이 성공적으로 완료되었습니다!",
                "summary_df": summary_df
            }
            
        elif mode == 'video':
            state.status = "영상/재생목록 분석 중..."
            state.log_history.append(f"[{datetime.now().strftime('%H:%M:%S')}] 영상 정보 수집 시작: '{video_input}'")
            
            # 1. 단일 영상 수집 옵션
            if video_range == 'single':
                video_id = extract_video_id_from_url(video_input)
                if not video_id:
                    if len(video_input.strip()) == 11:
                        video_id = video_input.strip()
                    else:
                        raise Exception("유효한 유튜브 영상 URL 또는 영상 ID가 아닙니다.")
                
                state.status = "단일 영상 수집 중..."
                state.log_history.append(f"[{datetime.now().strftime('%H:%M:%S')}] 단일 영상 수집을 수행합니다. ID: {video_id} (채널 연동: {sync_channel})")
                
                v_res = youtube_manager.fetch_single_video(video_id, sync_channel_info=sync_channel)
                if not v_res['success']:
                    raise Exception(f"단일 영상 수집 실패: {v_res.get('error', '알 수 없는 오류')}")
                
                v_item = v_res['videos'][0] if v_res.get('videos') else None
                v_title = v_item['title'] if v_item else "알 수 없음"
                v_channel_id = v_item['channel_id'] if v_item else None
                state.log_history.append(f"[{datetime.now().strftime('%H:%M:%S')}] ✓ 영상 수집 성공: '{v_title}' (쿼터 소모: {v_res.get('quota_used', 0)})")
                
                if v_channel_id:
                    state.status = "실시간 채널 통계 갱신 중..."
                    youtube_manager.update_channel_metrics(v_channel_id)
                    state.log_history.append(f"[{datetime.now().strftime('%H:%M:%S')}] ✓ 해당 채널({v_channel_id}) 통계 실시간 최신화 완료 (sheet_channels)")
                
                state.progress = 0.9
                state.status = "구글 시트 동기화 중..."
                auto_sync_to_google_sheet(mode, False, sync_channel, sheet_urls, state.log_history)
                
                summary_row = {
                    "영상 ID": video_id,
                    "영상 제목": v_title,
                    "채널 ID": v_channel_id or 'N/A',
                    "조회수": f"{v_item.get('views', 0):,}" if v_item and v_item.get('views') is not None else 'N/A',
                    "좋아요수": f"{v_item.get('likes', 0):,}" if v_item and v_item.get('likes') is not None else 'N/A',
                    "수집 시간": datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                }
                summary_df = pd.DataFrame([summary_row])
                
                state.progress = 1.0
                state.result = {
                    "status": "success",
                    "msg": f"✓ 영상 '{v_title}' 수집 및 DB 저장이 완료되었습니다!",
                    "summary_df": summary_df
                }
                
            # 2. 범위형 수집 (최근/전체/연도별)
            else:
                playlist_id = extract_playlist_id(video_input)
                video_id_extracted = extract_video_id_from_url(video_input)
                
                target_channel_id = None
                target_playlist_id = None
                
                if playlist_id:
                    target_playlist_id = playlist_id
                    state.log_history.append(f"[{datetime.now().strftime('%H:%M:%S')}] 재생목록 ID 분석 성공: {target_playlist_id}")
                elif video_id_extracted:
                    state.status = "영상 소속 채널 추출 중..."
                    state.log_history.append(f"[{datetime.now().strftime('%H:%M:%S')}] 영상 URL에서 채널 정보를 조회하기 위해 API를 호출합니다.")
                    
                    vid_res = youtube_manager.youtube.videos().list(
                        part='snippet',
                        id=video_id_extracted
                    ).execute()
                    youtube_manager.quota_tracker.log_usage('videos.list', 1)
                    
                    if vid_res.get('items'):
                        target_channel_id = vid_res['items'][0]['snippet'].get('channelId')
                        state.log_history.append(f"[{datetime.now().strftime('%H:%M:%S')}] ✓ 채널 ID 획득: {target_channel_id}")
                    else:
                        raise Exception("영상 정보가 존재하지 않거나 비공개 영상입니다.")
                else:
                    target_channel_id = get_channel_id_from_url(video_input)
                    if not target_channel_id:
                        state.log_history.append(f"[{datetime.now().strftime('%H:%M:%S')}] 입력값에서 채널/영상 식별 불가. 채널 검색 시도: '{video_input}'")
                        search_res = youtube_manager.find_channel_by_name_and_subs(video_input, 0, tolerance=1.0)
                        if search_res['channel_id']:
                            target_channel_id = search_res['channel_id']
                            state.log_history.append(f"[{datetime.now().strftime('%H:%M:%S')}] ✓ 검색으로 채널 ID 획득: {target_channel_id}")
                        else:
                            raise Exception("영상 URL, 재생목록 URL, 또는 채널 식별값을 정확히 입력해 주세요.")
                
                limit_val = 50
                fetch_all = False
                since_y = None
                
                if video_range == 'recent':
                    limit_val = recent_limit
                    fetch_all = False
                    since_y = None
                elif video_range == 'all':
                    limit_val = 50
                    fetch_all = True
                    since_y = None
                elif video_range == 'year':
                    since_y = since_year
                    limit_val = 50
                    fetch_all = True
                
                state.status = "범위 영상 데이터 수집 중..."
                v_res = youtube_manager.fetch_videos(
                    channel_id=target_channel_id,
                    playlist_id=target_playlist_id,
                    limit=limit_val,
                    fetch_all=fetch_all,
                    since_year=since_y,
                    sync_channel_info=sync_channel
                )
                
                if not v_res['success']:
                    raise Exception(f"영상 수집 오류: {v_res.get('error', '알 수 없는 오류')}")
                
                state.log_history.append(f"[{datetime.now().strftime('%H:%M:%S')}] ✓ 영상 수집 완료. 일반영상: {v_res.get('video_count', 0)}개, 쇼츠: {v_res.get('shorts_count', 0)}개 (쿼터 소모: {v_res.get('quota_used', 0)})")
                
                channel_ids_to_update = set()
                if target_channel_id:
                    channel_ids_to_update.add(target_channel_id)
                else:
                    for v in v_res.get('videos', []):
                        if v.get('channel_id'):
                            channel_ids_to_update.add(v['channel_id'])
                
                if channel_ids_to_update:
                    state.status = "실시간 채널 통계 갱신 중..."
                    for cid in channel_ids_to_update:
                        youtube_manager.update_channel_metrics(cid)
                        state.log_history.append(f"[{datetime.now().strftime('%H:%M:%S')}] ✓ 채널({cid}) 통계 실시간 최신화 완료 (sheet_channels)")
                
                state.progress = 0.9
                state.status = "구글 시트 동기화 중..."
                auto_sync_to_google_sheet(mode, True, sync_channel, sheet_urls, state.log_history)
                
                summary_rows = []
                for v in v_res.get('videos', []):
                    summary_rows.append({
                        "영상 ID": v.get('video_id'),
                        "영상 제목": v.get('title'),
                        "채널명": v.get('channel_name'),
                        "조회수": f"{v.get('views', 0):,}" if v.get('views') is not None else '0',
                        "게시일": v.get('upload_date', 'N/A'),
                        "형태": "쇼츠" if v.get('is_shorts') == 'True' else "롱폼"
                    })
                summary_df = pd.DataFrame(summary_rows) if summary_rows else pd.DataFrame()
                
                state.progress = 1.0
                state.result = {
                    "status": "success",
                    "msg": f"✓ 총 {len(v_res.get('videos', []))}개 영상 수집 및 DB 저장이 완료되었습니다!",
                    "summary_df": summary_df
                }

        elif mode == 'playlist':
            state.status = "재생목록 분석 및 DB 로딩 중..."
            state.log_history.append(f"[{datetime.now().strftime('%H:%M:%S')}] 재생목록 ID 연동 채널 수집을 시작합니다.")
            
            p_ids = []
            if selected_playlist_ids:
                # 선택된 재생목록 ID들 사용
                for val in selected_playlist_ids:
                    if not val:
                        continue
                    pl_id = extract_playlist_id(val)
                    if pl_id:
                        p_ids.append(pl_id)
                    else:
                        p_ids.append(val.strip())
                state.log_history.append(f"[{datetime.now().strftime('%H:%M:%S')}] 선택된 재생목록 {len(p_ids)}개에 대해 수집을 진행합니다.")
            else:
                # DB에서 재생목록 가져오기 (전체 수집)
                conn = sqlite3.connect(youtube_manager.db_path)
                cursor = conn.cursor()
                cursor.execute("SELECT playlist_id FROM sheet_playlist_ids")
                playlist_rows = cursor.fetchall()
                conn.close()
                
                for r in playlist_rows:
                    val = r[0]
                    if not val:
                        continue
                    pl_id = extract_playlist_id(val)
                    if pl_id:
                        p_ids.append(pl_id)
                    else:
                        p_ids.append(val.strip())
                state.log_history.append(f"[{datetime.now().strftime('%H:%M:%S')}] 전체 재생목록 {len(p_ids)}개 확인 완료.")
            
            if not p_ids:
                raise Exception("수집할 재생목록 ID가 존재하지 않습니다. 먼저 새로고침을 하거나 대상을 선택해 주세요.")
            
            # 기존 sheet_channels 채널 ID 캐싱
            conn = sqlite3.connect(youtube_manager.db_path)
            cursor = conn.cursor()
            cursor.execute("SELECT channel_id FROM sheet_channels")
            existing_cids = {row[0] for row in cursor.fetchall() if row[0]}
            conn.close()
            
            collected_videos_count = 0
            new_channels_added = {} # {channel_id: channel_title}
            
            total_playlists = len(p_ids)
            for idx, pid in enumerate(p_ids, 1):
                state.status = f"재생목록 수집 중 ({idx}/{total_playlists})..."
                state.log_history.append(f"[{datetime.now().strftime('%H:%M:%S')}] 재생목록 수집 실행: {pid}")
                
                v_res = youtube_manager.fetch_videos(
                    playlist_id=pid,
                    fetch_all=True,
                    sync_channel_info=False,
                    tab_name="유튜브 재생목록"
                )
                
                if v_res['success']:
                    videos = v_res.get('videos', [])
                    collected_videos_count += len(videos)
                    state.log_history.append(f"[{datetime.now().strftime('%H:%M:%S')}] ✓ {pid}에서 {len(videos)}개 영상 수집 완료.")
                    
                    # DB의 sheet_playlist_ids 테이블에 영상 갯수와 마지막 체크일 업데이트
                    now_time_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                    try:
                        conn = sqlite3.connect(youtube_manager.db_path)
                        cursor = conn.cursor()
                        cursor.execute("""
                            UPDATE sheet_playlist_ids 
                            SET video_count = ?, last_checked_at = ? 
                            WHERE playlist_id = ?
                        """, (len(videos), now_time_str, pid))
                        conn.commit()
                        conn.close()
                    except Exception as db_err:
                        logger.error(f"Failed to update video_count for playlist {pid}: {db_err}")
                    
                    # 소속 채널 추출 및 신규 채널 식별
                    for v in videos:
                        cid = v.get('channel_id')
                        ctitle = v.get('channel_name')
                        if cid and cid not in existing_cids and cid not in new_channels_added:
                            new_channels_added[cid] = ctitle
                else:
                    state.log_history.append(f"[{datetime.now().strftime('%H:%M:%S')}] ⚠️ 재생목록 {pid} 수집 오류: {v_res.get('error')}")
                
                state.progress = 0.5 * (idx / total_playlists)
            
            # 신규 채널 등록
            if new_channels_added:
                state.status = f"신규 채널 {len(new_channels_added)}개 동기화 중..."
                state.log_history.append(f"[{datetime.now().strftime('%H:%M:%S')}] 신규 식별된 채널 {len(new_channels_added)}개를 중복 없이 채널 리스트 DB에 추가합니다.")
                
                total_new = len(new_channels_added)
                for c_idx, (cid, ctitle) in enumerate(new_channels_added.items(), 1):
                    state.status = f"신규 채널 수집 중 ({c_idx}/{total_new}): {ctitle}"
                    c_res = youtube_manager.sync_channel(
                        channel_url=f"https://www.youtube.com/channel/{cid}",
                        channel_name=ctitle,
                        use_search_fallback=True,
                        is_fetched=''
                    )
                    if c_res['success']:
                        state.log_history.append(f"[{datetime.now().strftime('%H:%M:%S')}] ✓ 신규 채널 등록 성공: {ctitle} ({cid})")
                    else:
                        state.log_history.append(f"[{datetime.now().strftime('%H:%M:%S')}] ❌ 채널 등록 실패: {ctitle} ({c_res.get('error')})")
                        
                    state.progress = 0.5 + 0.5 * (c_idx / total_new)
            else:
                state.log_history.append(f"[{datetime.now().strftime('%H:%M:%S')}] 재생목록 비디오 소속 채널 중 신규 채널이 발견되지 않았습니다. (전원 기존 DB 존재)")
                state.progress = 1.0
                
            state.progress = 0.9
            state.status = "구글 시트 동기화 중..."
            auto_sync_to_google_sheet(mode, False, False, sheet_urls, state.log_history)
            
            summary_rows = []
            summary_rows.append({
                "수집 구분": "재생목록 ID 연동 수집 완료",
                "총 대상 재생목록 수": f"{len(p_ids)}개",
                "총 수집 영상 수": f"{collected_videos_count}개",
                "신규 채널 발견 및 등록 수": f"{len(new_channels_added)}개"
            })
            for cid, ctitle in new_channels_added.items():
                summary_rows.append({
                    "수집 구분": "└─ 신규 등록 채널",
                    "총 대상 재생목록 수": ctitle,
                    "총 수집 영상 수": cid,
                    "신규 채널 발견 및 등록 수": "-"
                })
            summary_df = pd.DataFrame(summary_rows)
            
            state.progress = 1.0
            state.result = {
                "status": "success",
                "msg": f"✓ 재생목록 기반 수집 완료! 수집 영상: {collected_videos_count}개, 신규 채널 등록: {len(new_channels_added)}개",
                "summary_df": summary_df
            }

        elif mode == 'bulk_channel':
            if not bulk_channels_list:
                raise Exception("수집할 미수집 채널 목록이 전달되지 않았습니다.")
                
            total_bulk = len(bulk_channels_list)
            state.status = f"미수집 채널 {total_bulk}개 벌크 수집 중..."
            state.log_history.append(f"[{datetime.now().strftime('%H:%M:%S')}] 미수집 채널 {total_bulk}개 벌크 수집 작업을 가동합니다. (영상 연동: {sync_videos})")
            
            success_count = 0
            for idx, ch_info in enumerate(bulk_channels_list, 1):
                ch_id = ch_info.get('channel_id')
                ch_name = ch_info.get('channel_name')
                ch_link = ch_info.get('channel_link')
                
                ch_input_val = ch_link if ch_link else (ch_id if ch_id else ch_name)
                if not ch_input_val:
                    continue
                    
                state.status = f"채널 수집 중 ({idx}/{total_bulk}): {ch_name or ch_id}"
                state.log_history.append(f"[{datetime.now().strftime('%H:%M:%S')}] [{idx}/{total_bulk}] '{ch_name or ch_id}' 수집 시도...")
                
                target_cid = ch_id if ch_id else get_channel_id_from_url(ch_input_val)
                use_search = False
                search_name = None
                if not target_cid:
                    use_search = True
                    search_name = ch_name
                
                c_res = youtube_manager.sync_channel(
                    channel_url=ch_input_val if target_cid else "",
                    channel_name=search_name,
                    subscriber_count=None,
                    use_search_fallback=use_search
                )
                
                if c_res['success']:
                    resolved_cid = c_res['channel_id']
                    success_count += 1
                    
                    now_time_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                    conn = sqlite3.connect(youtube_manager.db_path)
                    cursor = conn.cursor()
                    cursor.execute("""
                        UPDATE sheet_channels 
                        SET is_fetched = 'ㅇ', crawl_date = ? 
                        WHERE channel_id = ?
                    """, (now_time_str, resolved_cid))
                    conn.commit()
                    conn.close()
                    
                    state.log_history.append(f"[{datetime.now().strftime('%H:%M:%S')}] ✓ 채널 등록 성공: '{ch_name or resolved_cid}' (DB 상태 업데이트 완료)")
                    
                    if sync_videos:
                        state.log_history.append(f"[{datetime.now().strftime('%H:%M:%S')}] -> 연쇄 영상 수집 수행 (옵션: {video_range})")
                        
                        limit_val = 50
                        fetch_all = False
                        since_y = None
                        
                        if video_range == 'single':
                            limit_val = 50
                        elif video_range == 'recent':
                            limit_val = recent_limit
                        elif video_range == 'all':
                            limit_val = 50
                            fetch_all = True
                        elif video_range == 'year':
                            since_y = since_year
                            limit_val = 50
                            fetch_all = True
                            
                        v_res = youtube_manager.fetch_videos(
                            channel_id=resolved_cid,
                            limit=limit_val,
                            fetch_all=fetch_all,
                            since_year=since_y,
                            sync_channel_info=False
                        )
                        
                        if v_res['success']:
                            state.log_history.append(f"[{datetime.now().strftime('%H:%M:%S')}]    ✓ 영상 수집 성공. 일반영상: {v_res.get('video_count', 0)}개, 쇼츠: {v_res.get('shorts_count', 0)}개")
                            youtube_manager.update_channel_metrics(resolved_cid)
                            state.log_history.append(f"    ✓ 통계 집계 최신화 완료.")
                        else:
                            state.log_history.append(f"    ❌ 연쇄 영상 수집 오류: {v_res.get('error')}")
                else:
                    state.log_history.append(f"[{datetime.now().strftime('%H:%M:%S')}] ❌ 수집 실패: '{ch_name or ch_input_val}' ({c_res.get('error')})")
                    
                state.progress = idx / total_bulk
                
            state.progress = 0.9
            state.status = "구글 시트 동기화 중..."
            auto_sync_to_google_sheet(mode, sync_videos, False, sheet_urls, state.log_history)
            
            summary_rows = []
            summary_rows.append({
                "벌크 수집 요약": "미수집 채널 벌크 수집 결과",
                "대상 채널 수": f"{total_bulk}개",
                "성공 채널 수": f"{success_count}개",
                "실패 채널 수": f"{total_bulk - success_count}개"
            })
            summary_df = pd.DataFrame(summary_rows)
            
            state.progress = 1.0
            state.result = {
                "status": "success",
                "msg": f"✓ 미수집 채널 벌크 수집 완료! 성공: {success_count}/{total_bulk}개",
                "summary_df": summary_df
            }
            
        state.is_running = False
        
    except Exception as run_err:
        logger.error(f"Collection work thread error: {run_err}", exc_info=True)
        state.is_running = False
        state.result = {
            "status": "error",
            "msg": f"✗ 수집 진행 중 에러 발생: {run_err}"
        }
        state.log_history.append(f"❌ 작업 에러 발생: {run_err}")


with tabs[2]:
    st.header("📥 유튜브 채널 및 영상 데이터 수집")
    st.markdown("YouTube Data API v3를 활용하여 채널 상세 정보 및 영상/재생목록 데이터를 로컬 연동 DB(`sheet_channels`, `sheet_videos`)에 중복 없이 안전하게 적재하고 실시간으로 통계를 갱신합니다.")

    default_sheet_url = "https://docs.google.com/spreadsheets/d/1o-Vm5eMfH6Mm1gCnsv-SuRqtVMYTBDnWThBxjwCOptY/edit?usp=sharing"
    if 'sheet_urls' not in st.session_state:
        from modules.sheet_sync import TAB_MAPPING
        st.session_state['sheet_urls'] = {tab: default_sheet_url for tab in TAB_MAPPING.keys()}

    # 1. 채널 정보 수집
    st.subheader("🏢 채널 정보 수집")
    
    # 1. 수동 수집
    st.markdown("**🔍 수동 입력 수집**")
    channel_input = st.text_input("채널명 / 핸들 / 채널 URL", placeholder="예: @록터뷰 또는 https://www.youtube.com/@록터뷰")
    sync_videos_manual = st.checkbox("영상 수집 옵션 연동", value=True, key="sync_videos_manual", help="채널 수집 시, 지정된 영상 범위 설정에 따라 동영상도 함께 수집합니다.")
    start_channel_btn = st.button("📥 채널 수집 실행", type="primary", use_container_width=True)
    
    st.markdown("---")
    
    # 2. 구글 시트 연동 수집
    st.markdown("**📂 구글 시트 연동 수집**")
    
    col_c_btn1, col_c_btn2 = st.columns([1, 1])
    with col_c_btn1:
        refresh_channels_btn = st.button("🔄 구글시트 채널 갱신", use_container_width=True)
    
    if refresh_channels_btn:
        with st.spinner("구글 시트에서 채널 데이터를 가져오는 중..."):
            current_url = st.session_state['sheet_urls'].get("채널 리스트", default_sheet_url)
            from modules.sheet_sync import sync_sheet_to_db
            try:
                res_sync = sync_sheet_to_db(current_url, target_tab="채널 리스트")
                imported_rows = res_sync.get("채널 리스트", 0)
                st.success(f"✓ 구글시트 동기화 완료: {imported_rows}개 행 가져옴.")
                time.sleep(1)
                st.rerun()
            except Exception as sync_err:
                st.error(f"❌ 동기화 중 에러 발생: {sync_err}")
                
    # 미수집 리스트 조회
    conn = sqlite3.connect(DB_PATH)
    df_unfetched = pd.read_sql_query("""
        SELECT channel_id, channel_name, category1, category2, channel_feature, channel_link, is_fetched 
        FROM sheet_channels 
        WHERE is_fetched IS NULL OR is_fetched = '' OR is_fetched != 'ㅇ'
    """, conn)
    conn.close()
    
    bulk_channels_list = []
    if not df_unfetched.empty:
        df_display = df_unfetched.rename(columns={
            'channel_name': '채널명',
            'category1': '분야1',
            'category2': '분야2',
            'channel_feature': '채널특징',
            'channel_link': '채널링크'
        })
        df_display.insert(0, "선택", False)
        
        st.markdown(f"미수집 채널 목록 (총 {len(df_unfetched)}개):")
        edited_df = st.data_editor(
            df_display[['선택', '채널명', '분야1', '분야2', '채널특징', '채널링크', 'channel_id']],
            hide_index=True,
            disabled=['채널명', '분야1', '분야2', '채널특징', '채널링크', 'channel_id'],
            use_container_width=True,
            key="bulk_channel_editor"
        )
        
        selected_channels = edited_df[edited_df["선택"] == True]
        for idx, row in selected_channels.iterrows():
            bulk_channels_list.append({
                'channel_id': row.get('channel_id'),
                'channel_name': row.get('채널명'),
                'channel_link': row.get('채널링크')
            })
            
        st.write(f"선택된 채널 수: {len(bulk_channels_list)}개")
    else:
        st.info("미수집 상태의 채널이 없습니다.")
        
    sync_videos_bulk = st.checkbox("선택 채널 영상도 함께 수집", value=True, key="sync_videos_bulk")
    start_bulk_btn = st.button("📥 선택한 채널 벌크 수집 실행", type="primary", use_container_width=True, disabled=(len(bulk_channels_list) == 0))

    st.divider()

    # 2. 영상 정보 수집
    st.subheader("🎥 영상 정보 수집")
    video_input = st.text_input("영상 URL / 재생목록 URL / 채널 식별값", placeholder="예: https://www.youtube.com/watch?v=... 또는 재생목록 주소")
    
    video_range = st.radio("수집 범위 설정", ["해당 영상만 수집", "최근 영상 지정 수집 (50개 단위)", "전체 영상 수집", "지정 연도부터 지금까지 수집"], index=1, key="col_video_range")
    
    recent_limit = 50
    since_year = 2024
    if video_range == "최근 영상 지정 수집 (50개 단위)":
        recent_limit = st.slider("최근 수집 영상 갯수", 50, 500, 50, 50)
    elif video_range == "지정 연도부터 지금까지 수집":
        since_year = st.number_input("기준 연도 입력", min_value=2000, max_value=2030, value=2024)
        
    sync_channel = st.checkbox("채널 정보 함께 수집", value=True, help="영상 수집을 수행할 때 해당 영상들의 소속 채널 상세 정보도 함께 수집하여 채널 DB를 갱신합니다.")
    start_video_btn = st.button("📥 영상 수집 실행", type="primary", use_container_width=True)

    st.divider()

    # 3. 재생목록 ID 연동 수집
    st.subheader("📋 재생목록 ID 연동 수집")
    st.markdown("로컬 DB '재생목록ID' 테이블의 모든 목록을 순회하여 영상을 수집하고, 그 중 채널 DB에 없는 신규 채널들을 추출 및 자동 등록합니다.")
    
    col_p_btn1, col_p_btn2 = st.columns([1, 1])
    with col_p_btn1:
        refresh_playlists_btn = st.button("🔄 재생목록 구글시트 갱신", use_container_width=True)
        
    if refresh_playlists_btn:
        with st.spinner("구글 시트에서 재생목록ID 데이터를 가져오는 중..."):
            current_url = st.session_state['sheet_urls'].get("재생목록ID", default_sheet_url)
            from modules.sheet_sync import sync_sheet_to_db
            try:
                res_sync = sync_sheet_to_db(current_url, target_tab="재생목록ID")
                imported_rows = res_sync.get("재생목록ID", 0)
                st.session_state['playlist_sel_df'] = None # 세션 초기화 유발
                st.success(f"✓ 구글시트 동기화 완료: {imported_rows}개 행 가져옴.")
                time.sleep(1)
                st.rerun()
            except Exception as sync_err:
                st.error(f"❌ 동기화 중 에러 발생: {sync_err}")
                
    # 재생목록ID 목록 조회
    conn = sqlite3.connect(DB_PATH)
    df_pids = pd.read_sql_query("""
        SELECT playlist_id, playlist_name, video_count, last_checked_at 
        FROM sheet_playlist_ids
    """, conn)
    conn.close()
    
    selected_pids = []
    if not df_pids.empty:
        # 세션 상태 초기화 및 관리
        if 'playlist_sel_df' not in st.session_state or st.session_state['playlist_sel_df'] is None or len(st.session_state['playlist_sel_df']) != len(df_pids):
            df_pids_display = df_pids.rename(columns={
                'playlist_name': '재생목록 이름',
                'playlist_id': '재생목록 ID',
                'video_count': '영상갯수',
                'last_checked_at': '마지막 체크일'
            })
            # '재생목록 이름' 컬럼이 '재생목록 ID' 왼쪽에 위치하도록 순서 재배열
            df_pids_display = df_pids_display[['재생목록 이름', '재생목록 ID', '영상갯수', '마지막 체크일']]
            # 재생목록 ID 테이블의 체크박스 선택 기본값은 체크해제(False)로 설정
            df_pids_display.insert(0, '선택', False)
            st.session_state['playlist_sel_df'] = df_pids_display
            st.session_state['playlist_editor_version'] = 0
            
        st.markdown(f"등록된 재생목록 ID (총 {len(df_pids)}개):")
        
        # 전체 선택 / 전체 선택해제 버튼
        col_sel1, col_sel2 = st.columns([1, 1])
        with col_sel1:
            if st.button("☑️ 전체 선택", key="btn_playlist_select_all", use_container_width=True):
                st.session_state['playlist_sel_df']['선택'] = True
                st.session_state['playlist_editor_version'] = st.session_state.get('playlist_editor_version', 0) + 1
                st.rerun()
        with col_sel2:
            if st.button("☒ 전체 선택해제", key="btn_playlist_deselect_all", use_container_width=True):
                st.session_state['playlist_sel_df']['선택'] = False
                st.session_state['playlist_editor_version'] = st.session_state.get('playlist_editor_version', 0) + 1
                st.rerun()
        
        version = st.session_state.get('playlist_editor_version', 0)
        
        # 사용자 체크박스 편집을 위한 data_editor
        edited_df = st.data_editor(
            st.session_state['playlist_sel_df'],
            column_config={
                "선택": st.column_config.CheckboxColumn(
                    "선택",
                    help="수집할 재생목록을 선택해 주세요.",
                    default=False
                )
            },
            disabled=["재생목록 ID", "재생목록 이름", "영상갯수", "마지막 체크일"],
            hide_index=True,
            use_container_width=True,
            key=f"playlist_editor_widget_v{version}"
        )
        
        # 에디터 수정값을 세션에 즉각 동기화
        st.session_state['playlist_sel_df'] = edited_df
        
        # 선택된 재생목록 ID 필터링
        selected_rows = edited_df[edited_df['선택'] == True]
        selected_pids = selected_rows['재생목록 ID'].tolist()
    else:
        st.info("등록된 재생목록 ID가 없습니다. 먼저 갱신을 실행해 주세요.")
        
    col_p_btn_run1, col_p_btn_run2 = st.columns([1, 1])
    with col_p_btn_run1:
        start_selected_playlist_btn = st.button(
            "📥 선택 재생목록 채널 수집", 
            type="primary", 
            use_container_width=True, 
            disabled=df_pids.empty or not selected_pids,
            help="체크박스로 선택한 재생목록에 속한 영상을 크롤링하여 채널을 수집합니다."
        )
    with col_p_btn_run2:
        start_all_playlist_btn = st.button(
            "📥 전체 재생목록 채널 수집", 
            use_container_width=True, 
            disabled=df_pids.empty,
            help="체크 여부와 상관없이 모든 재생목록을 순회하며 채널을 수집합니다."
        )

    st.markdown("---")
    st.subheader("📺 실시간 수집 작업 현황판")

    # 스레드 기동 로직
    if start_channel_btn or start_video_btn or start_bulk_btn or start_selected_playlist_btn or start_all_playlist_btn:
        if start_channel_btn and not channel_input.strip():
            st.error("채널 식별 정보를 입력해 주세요.")
        elif start_video_btn and not video_input.strip():
            st.error("영상 URL 또는 재생목록 주소를 입력해 주세요.")
        elif start_bulk_btn and len(bulk_channels_list) == 0:
            st.error("선택된 벌크 수집 채널이 없습니다.")
        else:
            active_pids = None
            if start_channel_btn:
                mode = 'channel'
                c_input = channel_input
                v_input = ""
                s_v = sync_videos_manual
                s_c = False
            elif start_video_btn:
                mode = 'video'
                c_input = ""
                v_input = video_input
                s_v = False
                s_c = sync_channel
            elif start_bulk_btn:
                mode = 'bulk_channel'
                c_input = ""
                v_input = ""
                s_v = sync_videos_bulk
                s_c = False
            elif start_selected_playlist_btn or start_all_playlist_btn:
                mode = 'playlist'
                c_input = ""
                v_input = ""
                s_v = False
                s_c = False
                if start_selected_playlist_btn:
                    active_pids = selected_pids
                
            st.session_state['collection_running'] = True
            st.session_state['collection_stop_requested'] = False
            st.session_state['collection_log_history'] = ["수집 스레드 가동 시작..."]
            st.session_state['collection_progress'] = 0.0
            st.session_state['collection_result'] = None
            
            class StateContainer:
                def __init__(self):
                    self.is_running = True
                    self.progress = 0.0
                    self.status = "초기화 중..."
                    self.log_history = []
                    self.stop_requested = False
                    self.result = None
            
            col_shared_state = StateContainer()
            st.session_state['col_shared_state'] = col_shared_state
            
            range_map = {
                "해당 영상만 수집": "single",
                "최근 영상 지정 수집 (50개 단위)": "recent",
                "전체 영상 수집": "all",
                "지정 연도부터 지금까지 수집": "year"
            }
            v_range_mapped = range_map[video_range]
            
            import threading
            th_col = threading.Thread(
                target=run_collection_work,
                args=(
                    col_shared_state,
                    mode,
                    c_input,
                    v_input,
                    s_v,
                    s_c,
                    v_range_mapped,
                    recent_limit,
                    since_year,
                    bulk_channels_list,
                    active_pids,
                    st.session_state.get('sheet_urls')
                ),
                daemon=True
            )
            th_col.start()
            st.rerun()

    # 모니터링 UI 및 결과 표시
    if st.session_state['collection_running'] and 'col_shared_state' in st.session_state:
        state = st.session_state['col_shared_state']
        
        c_progress = st.progress(state.progress)
        c_status = st.empty()
        c_log = st.empty()
        
        while state.is_running:
            c_progress.progress(state.progress)
            c_status.info(f"⏳ **상태**: {state.status} (진행률: {(state.progress * 100):.1f}%)")
            c_log.code("\n".join(state.log_history))
            time.sleep(1)
            
        st.session_state['collection_running'] = False
        st.session_state['collection_progress'] = 1.0
        st.session_state['collection_result'] = state.result
        
        if state.result and state.result.get('status') == 'success':
            play_sound()
            show_notification("수집 성공 완료", "요청하신 유튜브 데이터 수집이 성공적으로 완료되었습니다.")
        else:
            play_notification_sound()
            err_msg = state.result.get('msg') if state.result else '작업 실패'
            show_notification("수집 중단 또는 실패", f"수집 작업 중 오류가 발생했거나 중단되었습니다: {err_msg}")
            
        st.rerun()
    else:
        st.progress(st.session_state['collection_progress'])
        st.info(f"💡 현재 상태: {st.session_state.get('collection_status', '대기 중')}")
        
        if 'col_shared_state' in st.session_state and st.session_state['col_shared_state'] is not None:
            st.code("\n".join(st.session_state['col_shared_state'].log_history))
        else:
            st.code("\n".join(st.session_state['collection_log_history']))
            
        if st.session_state['collection_result'] is not None:
            res = st.session_state['collection_result']
            if res.get('status') == 'success':
                st.success(res.get('msg'))
                if 'summary_df' in res and res['summary_df'] is not None and not res['summary_df'].empty:
                    st.markdown("### 📊 수집 완료 결과 요약")
                    st.dataframe(res['summary_df'], use_container_width=True, hide_index=True)
            else:
                st.error(res.get('msg'))


# ==============================================================================
# 탭 4: 📝 대본 추출기 (외부프로그램 연동)
# ==============================================================================
with tabs[3]:
    st.header("📝 대본(Transcript) 자동 추출기")
    st.markdown("구글 시트의 영상 ID 목록을 기반으로 유튜브 자막 데이터를 자동 추출하고, 필요시 대용량 문서는 구글 드라이브 및 Docs에 업로드한 뒤 시트 주소를 실시간으로 기입합니다.")

    if 'ui_settings' not in st.session_state or st.session_state['ui_settings'] is None:
        st.session_state['ui_settings'] = load_settings()
    saved_ui = st.session_state['ui_settings']

    col_t1, col_t2 = st.columns([1, 2])
    
    with col_t1:
        st.subheader("🛠️ 대본 추출 옵션")
        
        # 1. 추출기 타입 선택
        trans_type = st.radio("추출기 종류", ["롱폼 대본 추출기", "숏폼 대본 추출기"], key="ext_trans_type")
        
        # 2. 구글 시트 정보
        st.markdown("**구글 스프레드시트 설정**")
        
        # 사전 정의된 스프레드시트 목록 매핑
        if trans_type == "롱폼 대본 추출기":
            preset_spreadsheets = {
                '채널수집_2.국내롱폼': 'https://docs.google.com/spreadsheets/d/15VuwgxcfjWUGzlQIstSNcK5SnTQZtaEi3XRfyffPxEE/edit',
                '채널수집_3.유튜브 노하우 재생목록': 'https://docs.google.com/spreadsheets/d/1hhgcMFS5v4F5ViBSBtj0ZfEFYdAFxFQz2HO1MUlZy28/edit'
            }
        else:
            preset_spreadsheets = {
                '쇼츠 스프레드시트': 'https://docs.google.com/spreadsheets/d/1o-Vm5eMfH6Mm1gCnsv-SuRqtVMYTBDnWThBxjwCOptY/edit',
                '유튜브 재생목록 요약': 'https://docs.google.com/spreadsheets/d/18q9GwlXm65t9IQi3kVy6Br1Y8PBmPCFthqoZFfsmhMU/edit'
            }
            
        preset_names = list(preset_spreadsheets.keys())
        selected_preset = st.selectbox("프리셋 시트 선택 (선택 시 URL 자동 입력)", ["직접 입력"] + preset_names)
        
        default_url = saved_ui.get("ext_trans_sheet_url", "https://docs.google.com/spreadsheets/d/15VuwgxcfjWUGzlQIstSNcK5SnTQZtaEi3XRfyffPxEE/edit")
        if selected_preset != "직접 입력":
            default_url = preset_spreadsheets[selected_preset]
            
        trans_sheet_url = st.text_input("구글 스프레드시트 URL", value=default_url, key="ext_trans_sheet_url")
        trans_sheet_name = st.text_input("대상 시트 탭 이름", value=saved_ui.get("ext_trans_sheet_name", "영상 리스트"), key="ext_trans_sheet_name")
        
        # 3. 인증 설정
        trans_creds = st.text_input("구글 서비스 계정 JSON키 경로", value=saved_ui.get("ext_trans_creds", "google_service_key/service-account-key.json"), key="ext_trans_creds")
        
        # 4. 실행 모드 및 파라미터
        st.markdown("**작업 실행 파라미터**")
        trans_modes = ["모드 A (전체 추출)", "모드 B (마지막 대본 이후)", "모드 C (중간 누락 대본 추출)"]
        trans_mode = st.selectbox("실행 모드", trans_modes, index=trans_modes.index(saved_ui.get("ext_trans_mode", "모드 A (전체 추출)")), key="ext_trans_mode")
        
        trans_limit = st.number_input("최대 수집 영상 수 제한", min_value=1, max_value=5000, value=int(saved_ui.get("ext_trans_limit", 50)), step=10, key="ext_trans_limit")
        trans_timestamp = st.checkbox("자막에 타임스탬프 표시 포함", value=saved_ui.get("ext_trans_timestamp", True), key="ext_trans_timestamp")
        
        # 5. 브라우저 및 네트워크 설정
        with st.expander("🌐 고급 브라우저/속도 설정"):
            trans_concurrency = st.slider("동시 처리 수 (Concurrency)", min_value=1, max_value=10, value=int(saved_ui.get("ext_trans_concurrency", 5)), step=1, key="ext_trans_concurrency")
            trans_delay = st.slider("요청 간 지연시간 (Delay, 초)", min_value=0.5, max_value=10.0, value=float(saved_ui.get("ext_trans_delay", 1.0)), step=0.5, key="ext_trans_delay")
            trans_use_browser = st.checkbox("브라우저 자동화(Selenium) 사용", value=saved_ui.get("ext_trans_use_browser", True), key="ext_trans_use_browser")
            trans_headless = st.checkbox("헤드리스 모드로 브라우저 감춤", value=saved_ui.get("ext_trans_headless", True), key="ext_trans_headless")
            trans_use_profile = st.checkbox("사용자 Chrome Profile 사용", value=saved_ui.get("ext_trans_use_profile", False), key="ext_trans_use_profile")

        st.markdown("---")
        
        # 제어 버튼 레이아웃
        btn_t_col1, btn_t_col2, btn_t_col3 = st.columns(3)
        
        with btn_t_col1:
            test_conn_btn = st.button("🔌 연결성 진단 테스트", width='stretch', key="ext_trans_test_btn")
        with btn_t_col2:
            start_trans_btn = st.button("🚀 대본 추출 시작", width='stretch', key="ext_trans_start_btn", disabled=st.session_state['transcript_running'])
        with btn_t_col3:
            stop_trans_btn = st.button("🛑 추출 프로세스 중단", width='stretch', key="ext_trans_stop_btn", disabled=not st.session_state['transcript_running'])

        # 연결성 진단 테스트 실행부
        if test_conn_btn:
            st.info("구글 시트 및 자막 API 연결 진단 중...")
            import os
            current_dir = os.path.dirname(os.path.abspath(__file__))
            if trans_type == "롱폼 대본 추출기":
                project_dir = os.path.join(current_dir, "외부프로그램", "롱폼-대본추출기")
            else:
                project_dir = os.path.join(current_dir, "외부프로그램", "숏폼-대본추출기")
            gui_file = os.path.join(project_dir, "GUI_Extract.py")
            
            try:
                ext_gui = load_isolated_module("ext_gui_test_module", gui_file, project_dir)
                sheets_manager = ext_gui.GoogleSheetsManager(os.path.abspath(trans_creds))
                sheets_manager.authenticate()
                
                # 비동기 테스트 실행을 위해 asyncio.run 적용
                import asyncio
                test_res = asyncio.run(sheets_manager.test_connection(trans_sheet_url))
                
                if test_res.get('status') == 'success':
                    st.success(f"✓ 구글 시트 연결성 검증 통과!")
                    st.markdown(f"  - **A2값 (영상수)**: {test_res.get('a2_value')}\n  - **A10값 (첫 영상 ID)**: {test_res.get('a10_value')}")
                    st.success(f"✓ 자막 추출 API 진단 통과: {test_res.get('message')}")
                else:
                    st.error(f"✗ 진단 실패: {test_res.get('message')}")
            except Exception as test_err:
                st.error(f"연결 진단 실패: {test_err}")

        # 대본 추출 시작 실행부
        if start_trans_btn:
            st.session_state['transcript_running'] = True
            st.session_state['transcript_stop_requested'] = False
            st.session_state['transcript_log_history'] = ["대본 추출 스레드 가동 시작..."]
            st.session_state['transcript_progress'] = 0.0
            st.session_state['transcript_result'] = None
            
            # 스레드 공유 상태 객체 생성
            class StateContainer:
                def __init__(self):
                    self.is_running = True
                    self.progress = 0.0
                    self.status = "초기화 중..."
                    self.log_history = []
                    self.stop_requested = False
                    self.result = None
            
            ext_shared_state = StateContainer()
            st.session_state['ext_shared_state'] = ext_shared_state
            
            import threading
            th = threading.Thread(
                target=run_extractor_work,
                args=(
                    ext_shared_state,
                    trans_type,
                    trans_sheet_url,
                    trans_sheet_name,
                    trans_mode,
                    trans_limit,
                    trans_timestamp,
                    trans_concurrency,
                    trans_delay,
                    trans_use_browser,
                    trans_headless,
                    trans_use_profile,
                    trans_creds
                ),
                daemon=True
            )
            th.start()
            st.rerun()

        # 대본 추출 중단 실행부
        if stop_trans_btn:
            if 'ext_shared_state' in st.session_state and st.session_state['ext_shared_state'] is not None:
                st.session_state['ext_shared_state'].stop_requested = True
                st.session_state['transcript_stop_requested'] = True
                play_notification_sound()
                show_notification("자막 추출 프로세스 중단", "사용자 요청에 의해 자막 추출 작업이 강제 중단되었습니다.")
                st.warning("🛑 작업 중단 신호를 보냈습니다. 진행 중인 루프가 끝나는 대로 안전하게 멈춥니다.")

    with col_t2:
        st.subheader("📺 실시간 대본 추출 현황판")
        
        # 스레드 실행 상태 모니터링 및 UI 갱신 루프
        if st.session_state['transcript_running'] and 'ext_shared_state' in st.session_state:
            state = st.session_state['ext_shared_state']
            
            # 메인 스레드 대기 UI
            t_progress = st.progress(state.progress)
            t_status = st.empty()
            t_log = st.empty()
            
            while state.is_running:
                t_progress.progress(state.progress)
                t_status.info(f"⏳ **상태**: {state.status} (진행률: {(state.progress * 100):.1f}%)")
                t_log.code("\n".join(state.log_history))
                time.sleep(1)
                
            st.session_state['transcript_running'] = False
            st.session_state['transcript_progress'] = 1.0
            st.session_state['transcript_result'] = state.result
            
            if state.result and state.result.get('status') == 'success':
                play_sound()
                show_notification("대본 추출 성공 완료", "모든 영상의 자막 추출 및 구글 시트 업데이트가 완벽하게 완료되었습니다.")
            else:
                play_notification_sound()
                err_msg = state.result.get('msg') if state.result else '작업 실패'
                show_notification("대본 추출 중단 또는 실패", f"작업 중 오류가 발생했거나 중단되었습니다: {err_msg}")
                
            st.rerun()
            
        else:
            st.progress(st.session_state['transcript_progress'])
            st.info(f"💡 현재 상태: {st.session_state.get('transcript_status', '대기 중')}")
            
            if 'ext_shared_state' in st.session_state and st.session_state['ext_shared_state'] is not None:
                st.code("\n".join(st.session_state['ext_shared_state'].log_history))
            else:
                st.code("\n".join(st.session_state['transcript_log_history']))
                
            if st.session_state['transcript_result'] is not None:
                res = st.session_state['transcript_result']
                if res.get('status') == 'success':
                    st.success(res.get('msg'))
                    if 'data' in res and res['data']:
                        df_res = pd.DataFrame(res['data'])
                        st.dataframe(df_res, use_container_width=True)
                else:
                    st.error(res.get('msg'))


# ==============================================================================
# 탭 4: 🔎 유튜브 검색기 (외부프로그램 연동)
# ==============================================================================
with tabs[4]:
    st.header("🔎 유튜브 키워드 검색기")
    st.markdown("YouTube Data API를 활용하여 지정된 키워드로 영상을 검색하고, 조회수 및 채널 등의 다양한 메타데이터와 계산 지표를 구글 시트에 일괄 추가(벌크 저장)합니다.")

    if 'ui_settings' not in st.session_state or st.session_state['ui_settings'] is None:
        st.session_state['ui_settings'] = load_settings()
    saved_ui = st.session_state['ui_settings']

    col_s1, col_s2 = st.columns([1, 2])
    
    with col_s1:
        st.subheader("🛠️ 유튜브 검색 옵션")
        
        # 1. 검색기 종류 선택
        search_type = st.radio("검색기 종류", ["롱폼 유튜브 검색기", "숏폼 유튜브 검색기"], key="ext_search_type")
        
        # 2. 검색 조건
        search_keyword = st.text_input("검색 키워드", value=saved_ui.get("ext_search_keyword", ""), key="ext_search_keyword", help="유튜브에서 검색할 검색어 키워드를 입력해 주세요.")
        search_limit = st.number_input("최대 결과 개수 (1~50)", min_value=1, max_value=50, value=int(saved_ui.get("ext_search_limit", 20)), key="ext_search_limit")
        
        search_orders = {
            'relevance (관련성 순)': 'relevance',
            'date (최신 순)': 'date',
            'viewCount (조회수 순)': 'viewCount',
            'rating (평점 순)': 'rating',
            'title (제목 순)': 'title'
        }
        selected_order_label = st.selectbox("정렬 기준", list(search_orders.keys()))
        search_order = search_orders[selected_order_label]
        
        search_durations = {
            'any (모든 길이)': 'any',
            'short (4분 이하)': 'short',
            'medium (4~20분)': 'medium',
            'long (20분 이상)': 'long'
        }
        selected_dur_label = st.selectbox("영상 길이 필터", list(search_durations.keys()))
        search_duration = search_durations[selected_dur_label]
        
        # 3. 구글 시트 저장 설정
        st.markdown("**구글 시트 저장 설정**")
        
        # 프리셋 스프레드시트 매핑
        if search_type == "롱폼 유튜브 검색기":
            preset_spreadsheets_s = {
                '채널수집_2.국내롱폼': 'https://docs.google.com/spreadsheets/d/15VuwgxcfjWUGzlQIstSNcK5SnTQZtaEi3XRfyffPxEE/edit',
                '채널수집_3.유튜브 노하우 재생목록': 'https://docs.google.com/spreadsheets/d/1hhgcMFS5v4F5ViBSBtj0ZfEFYdAFxFQz2HO1MUlZy28/edit'
            }
        else:
            preset_spreadsheets_s = {
                '쇼츠 스프레드시트': 'https://docs.google.com/spreadsheets/d/1o-Vm5eMfH6Mm1gCnsv-SuRqtVMYTBDnWThBxjwCOptY/edit',
                '유튜브 재생목록 요약': 'https://docs.google.com/spreadsheets/d/18q9GwlXm65t9IQi3kVy6Br1Y8PBmPCFthqoZFfsmhMU/edit'
            }
            
        preset_names_s = list(preset_spreadsheets_s.keys())
        selected_preset_s = st.selectbox("프리셋 시트 선택 (검색기용)", ["직접 입력"] + preset_names_s, key="ext_search_preset_sel")
        
        default_url_s = saved_ui.get("ext_search_sheet_url", "https://docs.google.com/spreadsheets/d/15VuwgxcfjWUGzlQIstSNcK5SnTQZtaEi3XRfyffPxEE/edit")
        if selected_preset_s != "직접 입력":
            default_url_s = preset_spreadsheets_s[selected_preset_s]
            
        search_sheet_url = st.text_input("구글 스프레드시트 URL (검색기용)", value=default_url_s, key="ext_search_sheet_url")
        search_sheet_name = st.text_input("저장할 시트 탭 이름", value=saved_ui.get("ext_search_sheet_name", "키워드 검색결과"), key="ext_search_sheet_name")
        
        # 4. 인증 설정
        search_creds = st.text_input("구글 서비스 계정 JSON키 경로 (검색기용)", value=saved_ui.get("ext_search_creds", "google_service_key/service-account-key.json"), key="ext_search_creds")
        
        st.markdown("---")
        
        # 검색 실행 버튼
        btn_s_col1, btn_s_col2 = st.columns(2)
        with btn_s_col1:
            start_search_btn = st.button("🔍 유튜브 검색 및 시트에 저장", width='stretch', key="ext_search_start_btn", disabled=st.session_state['search_running'])
        with btn_s_col2:
            stop_search_btn = st.button("🛑 검색 프로세스 중단", width='stretch', key="ext_search_stop_btn", disabled=not st.session_state['search_running'])

        # 검색 실행부
        if start_search_btn:
            if not search_keyword:
                st.warning("검색 키워드를 입력해 주세요.")
            else:
                st.session_state['search_running'] = True
                st.session_state['search_stop_requested'] = False
                st.session_state['search_log_history'] = ["유튜브 검색 스레드 가동 시작..."]
                st.session_state['search_result'] = None
                
                class SearchStateContainer:
                    def __init__(self):
                        self.is_running = True
                        self.log_history = []
                        self.stop_requested = False
                        self.result = None
                        
                search_shared_state = SearchStateContainer()
                st.session_state['search_shared_state'] = search_shared_state
                
                import threading
                th_s = threading.Thread(
                    target=run_search_work,
                    args=(
                        search_shared_state,
                        search_type,
                        search_keyword,
                        search_limit,
                        search_order,
                        search_duration,
                        search_sheet_url,
                        search_sheet_name,
                        search_creds
                    ),
                    daemon=True
                )
                th_s.start()
                st.rerun()

        # 검색 중단 실행부
        if stop_search_btn:
            if 'search_shared_state' in st.session_state and st.session_state['search_shared_state'] is not None:
                st.session_state['search_shared_state'].stop_requested = True
                st.session_state['search_stop_requested'] = True
                play_notification_sound()
                show_notification("유튜브 검색 중단", "사용자 요청에 의해 유튜브 검색 및 저장 작업이 중단되었습니다.")
                st.warning("🛑 작업 중단 신호를 보냈습니다.")

    with col_s2:
        st.subheader("📺 실시간 유튜브 검색 현황판")
        
        # 스레드 실행 상태 모니터링 및 UI 갱신 루프
        if st.session_state['search_running'] and 'search_shared_state' in st.session_state:
            state_s = st.session_state['search_shared_state']
            
            s_status = st.empty()
            s_log = st.empty()
            
            while state_s.is_running:
                s_status.info("⏳ **유튜브 키워드 검색 및 구글 시트 벌크 저장 중...**")
                s_log.code("\n".join(state_s.log_history))
                time.sleep(1)
                
            st.session_state['search_running'] = False
            st.session_state['search_result'] = state_s.result
            
            if state_s.result and state_s.result.get('status') == 'success':
                play_sound()
                show_notification("유튜브 검색 및 저장 완료", "검색 결과를 구글 시트에 안전하게 저장 및 서식 보존 처리를 완료했습니다.")
            else:
                play_notification_sound()
                err_msg = state_s.result.get('msg') if state_s.result else '작업 실패'
                show_notification("유튜브 검색 중단 또는 실패", f"작업 중 에러 발생: {err_msg}")
                
            st.rerun()
            
        else:
            st.info("💡 현재 상태: 대기 중")
            
            if 'search_shared_state' in st.session_state and st.session_state['search_shared_state'] is not None:
                st.code("\n".join(state_s.log_history))
            else:
                st.code("\n".join(st.session_state['search_log_history']))
                
            if st.session_state['search_result'] is not None:
                res_s = st.session_state['search_result']
                if res_s.get('status') == 'success':
                    st.success(res_s.get('msg'))
                    if 'data' in res_s and res_s['data']:
                        df_res_s = pd.DataFrame(res_s['data'])
                        st.dataframe(df_res_s, use_container_width=True)
                else:
                    st.error(res_s.get('msg'))


# ==============================================================================
# 탭 5: 📊 구글 시트 연동 DB
# ==============================================================================
with tabs[5]:
    st.header("📊 구글 시트 연동 DB 관리 및 시각화")
    st.markdown("구글 스프레드시트 탭 데이터를 로컬 DB와 동기화하고, 필터링 및 캐러셀 뷰를 통해 효과적으로 탐색합니다.")
    
    from modules.sheet_sync import sync_sheet_to_db, sync_db_to_sheet, TAB_MAPPING
    
    # 🎛️ 기본 설정 영역
    st.subheader("⚙️ 구글 시트 연동 설정")
    
    default_sheet_url = "https://docs.google.com/spreadsheets/d/1o-Vm5eMfH6Mm1gCnsv-SuRqtVMYTBDnWThBxjwCOptY/edit?usp=sharing"
    
    # 탭별 개별 URL, 선택된 탭 및 헤더 캐시 세션 상태 초기화
    if 'selected_sheet_tab' not in st.session_state:
        st.session_state['selected_sheet_tab'] = list(TAB_MAPPING.keys())[0]
        
    if 'sheet_urls' not in st.session_state:
        st.session_state['sheet_urls'] = {tab: default_sheet_url for tab in TAB_MAPPING.keys()}
        
    if 'sheet_headers_cache' not in st.session_state:
        st.session_state['sheet_headers_cache'] = {}
        
    # 1. 대상 시트 탭 선택 (가로 버튼 나열형 UI - 스타일 강화)
    st.markdown("**대상 시트 탭 선택**")
    tab_cols = st.columns(7)
    tab_list = list(TAB_MAPPING.keys())
    
    for idx, tab_name in enumerate(tab_list):
        is_selected = (st.session_state['selected_sheet_tab'] == tab_name)
        btn_label = f"▶️ {tab_name}" if is_selected else tab_name
        btn_type = "primary" if is_selected else "secondary"
        if tab_cols[idx].button(btn_label, width='stretch', type=btn_type, key=f"sheet_tab_btn_{tab_name}"):
            st.session_state['selected_sheet_tab'] = tab_name
            st.rerun()
            
    selected_tab = st.session_state['selected_sheet_tab']
    
    # 2. 선택된 탭의 개별 URL 입력 및 보존
    current_url = st.session_state['sheet_urls'].get(selected_tab, default_sheet_url)
    sheet_url = st.text_input(
        f"🔗 '{selected_tab}' 탭의 구글 시트 URL", 
        value=current_url, 
        key=f"sheet_url_input_{selected_tab}"
    )
    st.session_state['sheet_urls'][selected_tab] = sheet_url

    # 3. 최초 1회 선택된 탭의 구글 시트 실제 넘버링 헤더를 가져와 캐싱
    if selected_tab not in st.session_state['sheet_headers_cache'] and sheet_url:
        try:
            from modules.sheet_sync import get_gspread_client, get_tab_header_row_num
            gc = get_gspread_client()
            sh = gc.open_by_url(sheet_url)
            ws = sh.worksheet(selected_tab)
            header_row = get_tab_header_row_num(selected_tab)
            real_headers = ws.row_values(header_row)
            if real_headers:
                st.session_state['sheet_headers_cache'][selected_tab] = real_headers
        except Exception as e:
            logger.debug(f"구글 시트 헤더 캐싱 실패: {e}")
    
    # 동기화 제어 버튼
    col_btn1, col_btn2 = st.columns(2)
    
    if col_btn1.button("🔄 구글 시트에서 DB로 가져오기 (가져오기)", width='stretch', key="btn_sync_import"):
        with st.spinner("구글 시트로부터 데이터를 가져와 DB에 적재 중... (10행 이하 데이터 갱신)"):
            try:
                results = sync_sheet_to_db(sheet_url, target_tab=selected_tab)
                imported_rows = results.get(selected_tab, 0)
                if isinstance(imported_rows, int):
                    st.success(f"✓ '{selected_tab}' 탭에서 {imported_rows:,}개 데이터 행을 성공적으로 가져왔습니다!")
                    show_notification("동기화 완료", f"'{selected_tab}' 탭 데이터를 DB에 업데이트 완료했습니다.")
                    play_notification_sound()
                else:
                    st.error(f"가져오기 실패: {imported_rows}")
            except Exception as e:
                st.error(f"오류 발생: {e}")
                logger.error("가져오기 도중 예외", exc_info=True)
                
    if col_btn2.button("📤 DB 데이터를 구글 시트에 반영 (보내기)", width='stretch', key="btn_sync_export"):
        with st.spinner("DB 데이터를 구글 시트로 업데이트 중... (9행 수식 보호 및 10행 이하 영역 갱신)"):
            try:
                exported_rows = sync_db_to_sheet(sheet_url, tab_name=selected_tab)
                st.success(f"✓ DB 데이터를 구글 시트 '{selected_tab}' 탭에 {exported_rows:,}개 행 성공적으로 내보냈습니다!")
                show_notification("내보내기 완료", f"DB 데이터를 구글 시트 '{selected_tab}' 탭에 반영 완료했습니다.")
                play_notification_sound()
            except Exception as e:
                st.error(f"오류 발생: {e}")
                logger.error("내보내기 도중 예외", exc_info=True)
                
    st.markdown("---")
    
    # 🔍 데이터 조회 및 필터링
    col_ref1, col_ref2 = st.columns([5, 1])
    with col_ref1:
        st.subheader(f"🔍 '{selected_tab}' 데이터 실시간 필터링 조회")
    with col_ref2:
        if st.button("🔄 데이터 새로고침", key=f"btn_refresh_data_{selected_tab}", use_container_width=True):
            st.rerun()
    
    # DB 데이터 가져와 Pandas DataFrame 변환
    conn = get_db_connection()
    table_name = TAB_MAPPING.get(selected_tab)
    
    df_tab = pd.DataFrame()
    try:
        if table_name == "sheet_videos":
            df_tab = pd.read_sql_query("SELECT * FROM sheet_videos WHERE tab_name = ? ORDER BY original_row_order ASC", conn, params=(selected_tab,))
        else:
            df_tab = pd.read_sql_query(f"SELECT * FROM {table_name} ORDER BY original_row_order ASC", conn)
    except Exception as read_err:
        err_msg = str(read_err)
        # 만약 original_row_order 컬럼이 없어서 발생한 오류인 경우, 정렬 없이 조회하도록 폴백 처리
        if "no such column: original_row_order" in err_msg:
            try:
                logger.warning(f"'{table_name}' 테이블에 'original_row_order' 컬럼이 없어 정렬 없이 조회를 시도합니다.")
                if table_name == "sheet_videos":
                    df_tab = pd.read_sql_query("SELECT * FROM sheet_videos WHERE tab_name = ?", conn, params=(selected_tab,))
                else:
                    df_tab = pd.read_sql_query(f"SELECT * FROM {table_name}", conn)
            except Exception as retry_err:
                logger.error(f"로컬 DB '{selected_tab}' 조회 최종 실패: {retry_err}", exc_info=True)
                st.warning(f"로컬 DB에 '{selected_tab}' 데이터가 존재하지 않거나 조회가 불가능합니다. (에러: {retry_err})")
        else:
            logger.error(f"로컬 DB '{selected_tab}' 조회 실패: {read_err}", exc_info=True)
            st.warning(f"로컬 DB에 '{selected_tab}' 데이터가 존재하지 않거나 조회가 불가능합니다. 먼저 가져오기를 실행해 주세요. (에러: {read_err})")
        
    conn.close()
    
    if not df_tab.empty:
        # 필터 위젯 구성
        col_f1, col_f2, col_f3 = st.columns([2, 1, 1])
        
        # 1. 키워드 검색
        search_q = col_f1.text_input("검색어 입력 (제목, 채널명 등)", key=f"f_search_{selected_tab}")
        
        # 2. 숏폼 여부 (영상 데이터에만 적용)
        shorts_filter = "전체"
        if 'is_shorts' in df_tab.columns:
            shorts_filter = col_f2.selectbox("콘텐츠 형태", ["전체", "숏폼 (Shorts)", "롱폼 (일반 영상)"], key=f"f_shorts_{selected_tab}")
            
        # 3. 레이아웃
        layout_style = col_f3.selectbox("화면 레이아웃", ["리스트형 뷰", "캐러셀형 카드 뷰"], key=f"f_layout_{selected_tab}")
        
        # 4. 조회수 / 구독자수 고급 슬라이더
        with st.expander("⚙️ 고급 수치 필터"):
            col_fs1, col_fs2 = st.columns(2)
            
            # 조회수 필터
            min_v = 0
            max_v = 100000000
            if 'views' in df_tab.columns:
                max_v = int(df_tab['views'].max()) if pd.notna(df_tab['views'].max()) else 100000000
                max_v = max(1, max_v)
                v_range = col_fs1.slider("조회수 범위", min_value=0, max_value=max_v, value=(0, max_v), key=f"f_views_{selected_tab}")
            else:
                v_range = (0, 100000000)
                
            # 구독자수 필터
            min_s = 0
            max_s = 50000000
            if 'subscribers' in df_tab.columns:
                max_s = int(df_tab['subscribers'].max()) if pd.notna(df_tab['subscribers'].max()) else 50000000
                max_s = max(1, max_s)
                s_range = col_fs2.slider("구독자수 범위", min_value=0, max_value=max_s, value=(0, max_s), key=f"f_subs_{selected_tab}")
            else:
                s_range = (0, 50000000)
                
        # 데이터 필터링 실행
        filtered_df = df_tab.copy()
        
        # 키워드 필터링
        if search_q:
            q = search_q.lower()
            text_cols = [c for c in filtered_df.columns if filtered_df[c].dtype == 'object']
            mask = filtered_df[text_cols].apply(lambda x: x.str.lower().str.contains(q, na=False)).any(axis=1)
            filtered_df = filtered_df[mask]
            
        # 쇼츠 필터링
        if 'is_shorts' in filtered_df.columns and shorts_filter != "전체":
            is_shorts_target = "ㅇ" if shorts_filter == "숏폼 (Shorts)" else "x"
            filtered_df = filtered_df[filtered_df['is_shorts'] == is_shorts_target]
            
        # 조회수 필터링
        if 'views' in filtered_df.columns:
            filtered_df = filtered_df[(filtered_df['views'] >= v_range[0]) & (filtered_df['views'] <= v_range[1])]
            
        # 구독자수 필터링
        if 'subscribers' in filtered_df.columns:
            filtered_df = filtered_df[(filtered_df['subscribers'] >= s_range[0]) & (filtered_df['subscribers'] <= s_range[1])]
            
        # 5. 페이지네이션 (속도 개선을 위한 1,000개 단위 분할 및 페이지 선택 위젯)
        page_size = 1000
        total_rows = len(filtered_df)
        total_pages = max(1, (total_rows + page_size - 1) // page_size)
        
        page_key = f"sheet_sync_page_{selected_tab}"
        if page_key not in st.session_state:
            st.session_state[page_key] = 1
            
        # 페이지 안전 가드
        if st.session_state[page_key] > total_pages:
            st.session_state[page_key] = total_pages
        if st.session_state[page_key] < 1:
            st.session_state[page_key] = 1
            
        current_page = st.session_state[page_key]
        
        # 페이지 제어 바
        st.markdown(f"📊 총 **{total_rows:,}**개 행 중 **{(current_page-1)*page_size+1:,} ~ {min(current_page*page_size, total_rows):,}**번째 행 표시 (페이지당 {page_size:,}행)")
        
        p_col1, p_col2, p_col3, p_col4, p_col5 = st.columns([1, 1, 2, 1, 1])
        with p_col1:
            if st.button("⏮️ 처음", width='stretch', key=f"btn_page_first_{selected_tab}", disabled=(current_page == 1)):
                st.session_state[page_key] = 1
                st.rerun()
        with p_col2:
            if st.button("◀️ 이전", width='stretch', key=f"btn_page_prev_{selected_tab}", disabled=(current_page == 1)):
                st.session_state[page_key] -= 1
                st.rerun()
        with p_col3:
            target_page = st.number_input(
                "페이지 이동",
                min_value=1,
                max_value=total_pages,
                value=current_page,
                label_visibility="collapsed",
                key=f"input_page_num_{selected_tab}"
            )
            if target_page != current_page:
                st.session_state[page_key] = target_page
                st.rerun()
        with p_col4:
            if st.button("다음 ▶️", width='stretch', key=f"btn_page_next_{selected_tab}", disabled=(current_page == total_pages)):
                st.session_state[page_key] += 1
                st.rerun()
        with p_col5:
            if st.button("끝 ⏭️", width='stretch', key=f"btn_page_last_{selected_tab}", disabled=(current_page == total_pages)):
                st.session_state[page_key] = total_pages
                st.rerun()
                
        # 현재 페이지 데이터만 추출
        start_idx = (current_page - 1) * page_size
        end_idx = start_idx + page_size
        page_df = filtered_df.iloc[start_idx:end_idx]
        
        # 렌더링
        if layout_style == "리스트형 뷰":
            # 1. DB 컬럼 목록 획득
            db_cols = list(page_df.columns)
            
            # 2. 캐시된 실제 구글 시트 넘버링 헤더를 매칭
            db_col_to_display = {}
            real_headers = st.session_state['sheet_headers_cache'].get(selected_tab)
            if real_headers:
                from modules.utils import match_db_column_by_header
                for h in real_headers:
                    db_col = match_db_column_by_header(h, db_cols)
                    if db_col:
                        db_col_to_display[db_col] = h
            
            # 3. 폴백 한국어 헤더명 사전
            fallback_display = {
                "video_id": "영상 ID",
                "tab_name": "탭 이름",
                "upload_date": "업로드 날짜",
                "crawl_date": "수집 날짜",
                "keyword": "검색 키워드",
                "video_link": "영상 링크",
                "title": "제목",
                "views": "조회수",
                "is_benchmark_channel": "벤치마킹 채널여부",
                "is_shorts": "쇼츠 여부",
                "duration": "영상 길이",
                "channel_name": "채널명",
                "category1": "분야1",
                "category2": "분야2",
                "subscribers": "구독자수",
                "thumbnail_link": "썸네일 링크",
                "hooking_subtitle": "후킹 자막",
                "has_hooking_subtitle": "후킹 자막 유무",
                "transcript_content": "대본 내용",
                "has_transcript": "대본 유무",
                "transcript_char_count": "대본 글자수",
                "analysis": "영상 분석 내용",
                "likes": "좋아요수",
                "comments": "댓글수",
                "sub_to_view_ratio": "구독자 대비 조회수 비율",
                "view_to_like_ratio": "조회수 대비 좋아요 비율",
                "view_to_comment_ratio": "조회수 대비 댓글 비율",
                "days_since_upload": "업로드 경과일",
                "daily_avg_views": "일평균 조회수",
                "views_over_1m": "조회수 100만 이상",
                "views_over_5m": "조회수 500만 이상",
                "views_over_10m": "조회수 1000만 이상",
                "views_multiplier": "조회수 배수",
                "likes_over_3pct": "좋아요 3% 이상",
                "category_id": "카테고리 ID",
                "category_name": "카테고리명",
                "description": "설명",
                "description_char_count": "설명 글자수",
                "has_hashtag": "해시태그 유무",
                "used_hashtags": "사용 해시태그",
                "graph": "그래프",
                "video_count": "영상개수",
                "channel_total_views": "채널전체조회수",
                "avg_views_per_video": "영상당 평균 조회수",
                "channel_created_at": "채널 개설일",
                "days_since_channel_creation": "채널 개설 경과일",
                "is_narration": "음성 나레이션 여부",
                "is_scraped": "퍼온 영상인가",
                "is_ai_generated": "AI 생성 영상인가",
                "is_reference": "레퍼런스 여부",
                "has_subtitle_downloaded": "자막 다운로드 여부",
                "is_channel_monetized": "채널 수익화 여부",
                "is_shopping_monetized": "쇼핑 수익화 여부",
                "channel_country": "채널 국가",
                "used_language": "사용 언어",
                "channel_id": "채널 ID",
                "channel_link": "채널 링크",
                "playlist_name": "재생목록 이름",
                "transcript_file": "대본 파일명",
                "has_thumbnail": "썸네일 유무",
                "thumbnail_image_url": "썸네일 이미지 URL",
                "thumbnail_path": "썸네일 저장 경로",
                "original_row_order": "순번",
                "channel_description": "채널 설명",
                "channel_handle": "채널 핸들",
                "is_deleted_channel": "채널삭제 여부",
                
                # sheet_channels 전용
                "is_fetched": "가져왔는지 여부",
                "days_since_crawl": "수집날짜 경과일",
                "channel_feature": "채널 특징",
                "median_views_30": "최근 30개 영상 중위 조회수",
                "is_target_channel": "가져올 채널",
                "total_video_count": "채널전체 영상갯수",
                "total_channel_views_conv": "채널전체 조회수 변환",
                "total_channel_views": "채널전체조회수",
                "collected_video_avg_views": "수집한 영상 평균 조회수",
                "avg_views_30": "최근 30개 영상 평균 조회수",
                "collected_video_count": "수집 영상갯수",
                "avg_video_length": "평균 영상 길이",
                "views_over_1m_ratio": "조회수 100만 이상 비율",
                "views_over_5m_ratio": "조회수 500만 이상 비율",
                "views_over_10m_ratio": "조회수 1000만 이상 비율",
                "sub_to_view_multiplier_30": "구독자 대비 조회수 배율 최근 30개",
                "fairness_index_30": "공정성 지수 최근 30개",
                "subscribers_per_video": "영상당 구독자수",
                "views_per_subscriber": "구독자 1명당 조회수",
                "views_over_1m_count": "조회수 100만 이상 갯수",
                "views_over_5m_count": "조회수 500만 이상 갯수",
                "views_over_10m_count": "조회수 1000만 이상 갯수",
                "avg_views_exclude_top3": "조회수 상위 3개 제외 평균 조회수",
                "median_avg_views": "중위 평균 조회수",
                "created_at": "개설일",
                "days_since_creation": "개설이후 수집날짜까지 기간",
                "avg_upload_period": "영상 1개당 평균 업로드 주기",
                
                # sheet_playlist_ids 전용
                "playlist_id": "재생목록 ID",
                "last_checked_at": "마지막 체크일"
            }
            
            # 4. 최종 헤더 매핑 맵 구성
            final_col_map = {}
            for col in db_cols:
                final_col_map[col] = db_col_to_display.get(col) or fallback_display.get(col) or col
                
            display_df = page_df.rename(columns=final_col_map)
            
            # '채널 리스트' 탭일 때 '채널삭제 여부' 컬럼을 '가져왔는지 여부' 컬럼 오른쪽에 강제 재배치
            if selected_tab == "채널 리스트":
                cols_list = list(display_df.columns)
                target_fetched_col = None
                target_deleted_col = None
                for c in cols_list:
                    if "가져왔는지 여부" in c:
                        target_fetched_col = c
                    if "채널삭제 여부" in c:
                        target_deleted_col = c
                
                if target_fetched_col and target_deleted_col:
                    cols_list.remove(target_deleted_col)
                    fetched_idx = cols_list.index(target_fetched_col)
                    cols_list.insert(fetched_idx + 1, target_deleted_col)
                    display_df = display_df[cols_list]
            
            # '여부', '유무', '가져올 채널' 이 포함된 컬럼 이외의 모든 컬럼 수정 잠금
            disabled_cols = [col for col in display_df.columns if not any(x in col for x in ["여부", "유무", "가져올 채널"])]
            
            # st.data_editor 활용하여 셀 수정 활성화 렌더링
            editor_key = f"editor_{selected_tab}"
            st.data_editor(display_df, disabled=disabled_cols, use_container_width=True, key=editor_key)
            
            # 셀 편집 감지 시 실시간 DB 및 수집날짜 강제 갱신 처리
            if editor_key in st.session_state and st.session_state[editor_key].get("edited_rows"):
                edited_info = st.session_state[editor_key]["edited_rows"]
                display_to_db_col = {v: k for k, v in final_col_map.items()}
                
                conn = get_db_connection()
                cursor = conn.cursor()
                
                for row_idx_str, changes in edited_info.items():
                    try:
                        row_idx = int(row_idx_str)
                        if row_idx < len(page_df):
                            row_data = page_df.iloc[row_idx]
                            
                            # 탭별 PK 지정
                            if table_name == "sheet_videos":
                                pk_val = row_data['video_id']
                                pk_col = "video_id"
                                extra_where = "AND tab_name = ?"
                                where_args = (pk_val, selected_tab)
                            elif table_name == "sheet_channels":
                                pk_val = row_data['channel_id']
                                pk_col = "channel_id"
                                extra_where = ""
                                where_args = (pk_val,)
                            elif table_name == "sheet_playlist_ids":
                                pk_val = row_data['playlist_id']
                                pk_col = "playlist_id"
                                extra_where = ""
                                where_args = (pk_val,)
                            
                            # 수집 날짜 강제 갱신 (구글 시트 내보내기 시 수집날짜 필터 통과 위함)
                            now_time_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                            date_col = 'crawl_date' if table_name != 'sheet_playlist_ids' else 'last_checked_at'
                            
                            for display_col, new_val in changes.items():
                                db_col = display_to_db_col.get(display_col)
                                if db_col:
                                    if extra_where:
                                        sql = f"UPDATE {table_name} SET {db_col} = ?, {date_col} = ? WHERE {pk_col} = ? {extra_where}"
                                        cursor.execute(sql, (new_val, now_time_str, pk_val, selected_tab))
                                    else:
                                        sql = f"UPDATE {table_name} SET {db_col} = ?, {date_col} = ? WHERE {pk_col} = ?"
                                        cursor.execute(sql, (new_val, now_time_str, pk_val))
                                        
                            logger.info(f"✓ 대시보드 직접 수정 반영 완료 ({table_name} - ID: {pk_val}, 필드: {changes}, 갱신일시: {now_time_str})")
                    except Exception as parse_err:
                        logger.error(f"대시보드 수정 데이터 파싱 중 에러: {parse_err}")
                        
                conn.commit()
                conn.close()
                st.rerun()
        else:
            # 캐러셀형 카드 뷰
            if table_name == "sheet_videos" and 'thumbnail_link' in filtered_df.columns:
                cards_html = ""
                card_data_list = filtered_df.to_dict('records')
                
                # 최대 30개만 캐러셀에 바인딩
                display_list = card_data_list[:30]
                
                for idx, row in enumerate(display_list):
                    title = row.get('title', '제목 없음')
                    channel = row.get('channel_name', '채널명 없음')
                    views_val = row.get('views', 0)
                    if views_val >= 1000000:
                        views_str = f"{views_val / 1000000:.1f}M"
                    elif views_val >= 1000:
                        views_str = f"{views_val / 1000:.1f}K"
                    else:
                        views_str = str(views_val)
                        
                    thumb_url = row.get('thumbnail_image_url') or row.get('thumbnail_link') or "https://via.placeholder.com/320x180"
                    video_link = row.get('video_link') or "#"
                    is_shorts_badge = "<span style='background:#ef5350;color:white;padding:2px 6px;border-radius:4px;font-size:10px;margin-right:5px;'>SHORTS</span>" if row.get('is_shorts') == 'ㅇ' else ""
                    
                    cards_html += f"""
                    <div class="carousel-card">
                        <a href="{video_link}" target="_blank" style="text-decoration:none;color:inherit;">
                            <img class="card-thumb" src="{thumb_url}" onerror="this.src='https://via.placeholder.com/320x180';" />
                            <div class="card-title">{is_shorts_badge}{title}</div>
                            <div class="card-channel">👤 {channel}</div>
                            <div class="card-views">🔥 조회수 {views_str}</div>
                        </a>
                    </div>
                    """
                    
                if display_list:
                    carousel_id = f"carousel_{selected_tab.replace(' ', '_')}"
                    carousel_html = f"""
                    <style>
                    .{carousel_id}-container {{
                        display: flex;
                        align-items: center;
                        justify-content: center;
                        position: relative;
                        width: 100%;
                        margin: 20px auto;
                        overflow: hidden;
                        background: rgba(30, 30, 30, 0.45);
                        border-radius: 16px;
                        padding: 30px;
                        box-shadow: 0 8px 32px 0 rgba(0, 0, 0, 0.3);
                        backdrop-filter: blur(12px);
                        -webkit-backdrop-filter: blur(12px);
                        border: 1px solid rgba(255, 255, 255, 0.08);
                    }}
                    .{carousel_id}-wrapper {{
                        overflow: hidden;
                        width: 90%;
                    }}
                    .{carousel_id}-track {{
                        display: flex;
                        transition: transform 0.5s cubic-bezier(0.25, 1, 0.5, 1);
                        width: 100%;
                    }}
                    .carousel-card {{
                        min-width: 260px;
                        max-width: 260px;
                        margin: 0 12px;
                        background: rgba(255, 255, 255, 0.03);
                        border-radius: 12px;
                        border: 1px solid rgba(255, 255, 255, 0.06);
                        box-sizing: border-box;
                        padding: 12px;
                        color: #ffffff;
                        transition: transform 0.3s, box-shadow 0.3s, border 0.3s;
                    }}
                    .carousel-card:hover {{
                        transform: translateY(-6px);
                        box-shadow: 0 12px 24px rgba(239, 83, 80, 0.25);
                        border: 1px solid rgba(239, 83, 80, 0.4);
                        background: rgba(255, 255, 255, 0.06);
                    }}
                    .card-thumb {{
                        width: 100%;
                        height: 140px;
                        object-fit: cover;
                        border-radius: 8px;
                        margin-bottom: 10px;
                        border: 1px solid rgba(255, 255, 255, 0.05);
                    }}
                    .card-title {{
                        font-size: 13px;
                        font-weight: 600;
                        margin-bottom: 6px;
                        line-height: 1.35;
                        height: 35px;
                        overflow: hidden;
                        text-overflow: ellipsis;
                        display: -webkit-box;
                        -webkit-line-clamp: 2;
                        -webkit-box-orient: vertical;
                    }}
                    .card-channel {{
                        font-size: 11px;
                        color: #b0bec5;
                        margin-bottom: 4px;
                        white-space: nowrap;
                        overflow: hidden;
                        text-overflow: ellipsis;
                    }}
                    .card-views {{
                        font-size: 12px;
                        color: #ef5350;
                        font-weight: 700;
                    }}
                    .nav-btn {{
                        position: absolute;
                        top: 50%;
                        transform: translateY(-50%);
                        background: rgba(239, 83, 80, 0.75);
                        border: none;
                        color: white;
                        width: 38px;
                        height: 38px;
                        border-radius: 50%;
                        cursor: pointer;
                        font-size: 18px;
                        display: flex;
                        align-items: center;
                        justify-content: center;
                        z-index: 100;
                        transition: background 0.2s, transform 0.2s;
                    }}
                    .nav-btn:hover {{
                        background: rgba(239, 83, 80, 1);
                        transform: translateY(-50%) scale(1.1);
                    }}
                    .prev-btn {{ left: 10px; }}
                    .next-btn {{ right: 10px; }}
                    </style>

                    <div class="{carousel_id}-container">
                        <button class="nav-btn prev-btn" onclick="slidePrev_{carousel_id}()" aria-label="이전">&#10094;</button>
                        <div class="{carousel_id}-wrapper">
                            <div class="{carousel_id}-track" id="{carousel_id}_track">
                                {cards_html}
                            </div>
                        </div>
                        <button class="nav-btn next-btn" onclick="slideNext_{carousel_id}()" aria-label="다음">&#10095;</button>
                    </div>

                    <script>
                    (function() {{
                        let index = 0;
                        const track = document.getElementById('{carousel_id}_track');
                        if (!track) return;
                        const cards = track.querySelectorAll('.carousel-card');
                        const cardWidth = 284; // 260px width + 24px margins
                        
                        window.slideNext_{carousel_id} = function() {{
                            const containerWidth = track.parentElement.offsetWidth;
                            const visibleCards = Math.floor(containerWidth / cardWidth) || 1;
                            const maxIndex = cards.length - visibleCards;
                            if (index < maxIndex) {{
                                index++;
                            }} else {{
                                index = 0; // 루프
                            }}
                            track.style.transform = 'translateX(-' + (index * cardWidth) + 'px)';
                        }};
                        
                        window.slidePrev_{carousel_id} = function() {{
                            if (index > 0) {{
                                index--;
                            }} else {{
                                const containerWidth = track.parentElement.offsetWidth;
                                const visibleCards = Math.floor(containerWidth / cardWidth) || 1;
                                index = Math.max(0, cards.length - visibleCards);
                            }}
                            track.style.transform = 'translateX(-' + (index * cardWidth) + 'px)';
                        }};
                    }})();
                    </script>
                    """
                    st.html(carousel_html)
                else:
                    st.info("조건에 만족하는 영상 리스트 데이터가 캐러셀 뷰에 표시될 수 없습니다.")
            else:
                st.info("이 탭은 영상 리스트 형태가 아니므로 캐러셀 뷰를 지원하지 않습니다. '리스트형 뷰'로 전환해 주세요.")


# ==============================================================================
# 4. 대시보드 모든 설정값 자동 감지 및 스마트 백업 (Auto-Save)
# ==============================================================================
try:
    if 'ui_settings' not in st.session_state or st.session_state['ui_settings'] is None:
        st.session_state['ui_settings'] = load_settings()
        
    # 현재 화면에 렌더링되어 살아있는 위젯의 값만 ui_settings 고정 메모리 버퍼에 복사하여 업데이트합니다.
    # 탭 이동으로 인해 st.session_state에서 사라진 위젯(비활성화 탭 위젯)은 건드리지 않고 기존 백업본을 그대로 유지합니다.
    for key in st.session_state['ui_settings'].keys():
        if key in st.session_state:
            val = st.session_state[key]
            if isinstance(val, (datetime, date)) or hasattr(val, 'strftime'):
                st.session_state['ui_settings'][key] = val.strftime('%Y-%m-%d')
            else:
                st.session_state['ui_settings'][key] = val
                
    current_settings_serialized = st.session_state['ui_settings']

    if 'last_saved_settings' not in st.session_state or st.session_state['last_saved_settings'] != current_settings_serialized:
        st.session_state['last_saved_settings'] = current_settings_serialized.copy()
        save_settings(current_settings_serialized)
except Exception as e:
    logger.error("대시보드 UI 설정 자동 저장(Auto-Save) 중 예외 발생!", exc_info=True)


# ==============================================================================
# 사이드바 하단: 최근 7일 플레이보드 크롤러 수집현황 렌더링 (NameError 방지 위해 하단 배치)
# ==============================================================================
with st.sidebar:
    st.markdown("---")
    st.subheader("📅 최근 7일 수집 현황")
    
    try:
        has_any_history = False
        
        # 7일간 날짜 역순 순회 (오늘부터 6일 전까지)
        for i in range(7):
            target_date_obj = datetime.now() - timedelta(days=i)
            date_str_hyphen = target_date_obj.strftime('%Y-%m-%d')
            date_str_under = target_date_obj.strftime('%Y_%m_%d')
            
            # 타입별 서브 폴더링 결정
            if 'shorts' in target_type.lower():
                type_folder = 'Shorts'
            elif 'channel' in target_type.lower():
                type_folder = 'Channel'
            elif 'video' in target_type.lower():
                type_folder = 'Video'
            else:
                type_folder = 'Others'
                
            target_dir = os.path.join(Config.OUTPUT_DIR, date_str_under, type_folder)
            
            # 해당 날짜에 디렉토리가 존재하고 하위에 파일이 실제로 있는지 검사
            if os.path.exists(target_dir):
                try:
                    files = os.listdir(target_dir)
                except Exception:
                    files = []
                    
                if files:
                    has_any_history = True
                    
                    # 날짜별 Expander 생성 (오늘 자는 기본 펼침 상태)
                    with st.expander(f"📅 {date_str_hyphen} 현황", expanded=(i==0)):
                        # 수집 제어 메타데이터 요약 노출
                        st.markdown(f"🎯 **대상**: {target_type} | 🌍 **국가**: {country}\n\n🔍 **기준**: {crawl_criteria} | ⏱️ **기간**: {period}")
                        st.markdown("---")
                        summary_records = []
                        all_categories = get_category_list()
                        
                        for cat in all_categories:
                            batch_cat_name = f"batch_{cat}"
                            filepath = find_existing_batch_file(
                                base_dir=Config.OUTPUT_DIR,
                                target_type=target_type,
                                category=batch_cat_name,
                                country=country,
                                period=period,
                                criteria=crawl_criteria,
                                ranking_date=date_str_hyphen
                            )
                            
                            collected_count = 0
                            if filepath and os.path.exists(filepath):
                                try:
                                    temp_df = load_and_standardize_csv(filepath, crawl_criteria)
                                    collected_count = len(temp_df)
                                except Exception:
                                    pass
                            
                            # 수집량이 0보다 큰 카테고리만 표에 표시
                            if collected_count > 0:
                                missing_count = max(0, crawl_limit - collected_count)
                                summary_records.append({
                                    "카테고리": cat,
                                    "수집량": collected_count,
                                    "부족분": missing_count
                                })
                                
                        if summary_records:
                            df_summary = pd.DataFrame(summary_records)
                            st.dataframe(df_summary, use_container_width=True, hide_index=True)
                            
                            # 미달 카테고리 추출
                            under_cats = [r["카테고리"] for r in summary_records if r["부족분"] > 0]
                            if under_cats:
                                st.caption(f"⚠️ 미달: {', '.join(under_cats)}")
                            else:
                                st.caption("✓ 모든 카테고리가 수집 목표를 충족합니다.")
                        else:
                            st.info("조건에 일치하는 수집 이력이 없습니다.")
                            
        if not has_any_history:
            st.info("최근 7일간의 크롤러 수집 이력이 없습니다.")
    except Exception as side_err:
        st.error(f"수집 현황 집계 오류: {side_err}")

