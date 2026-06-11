import os
import sys
import time
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

# 로거 및 클린업 획득
from logger_config import setup_logger, cleanup_old_logs
logger = setup_logger('crawler')

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


def get_actual_ranking_date_str(specific_date_str):
    """선택한 수집 날짜 기준 1일 전 날짜를 계산해 실제 플레이보드 랭킹 날짜로 매칭시킵니다."""
    try:
        from datetime import datetime, timedelta
        dt = datetime.strptime(specific_date_str, "%Y-%m-%d") - timedelta(days=1)
        return dt.strftime("%Y-%m-%d")
    except:
        return specific_date_str


# ==============================================================================
# Helper: 기존 오늘 날짜의 중간 수집 파일 검색
# ==============================================================================
def find_existing_batch_file(base_dir, target_type, category, country, period, criteria=None, ranking_date=None):
    """지정된 랭킹 날짜 폴더 내에 이미 수집된 특정 조건의 최신 CSV 파일 경로를 탐색합니다. (정규식 기반 윈도우 호환성 보장)"""
    import re
    if ranking_date:
        try:
            # YYYY-MM-DD 또는 YYYY/MM/DD 등의 포맷을 YYYY_MM_DD로 치환
            clean_date = ranking_date.replace('-', '_').replace('/', '_')
            if re.match(r'^\d{4}_\d{2}_\d{2}$', clean_date):
                date_folder = clean_date
            else:
                date_folder = datetime.now().strftime('%Y_%m_%d')
        except Exception as e:
            logger.warning(f"Failed to parse ranking_date {ranking_date}: {e}")
            date_folder = datetime.now().strftime('%Y_%m_%d')
    else:
        date_folder = datetime.now().strftime('%Y_%m_%d')
    
    if 'shorts' in target_type.lower():
        type_folder = 'Shorts'
    elif 'channel' in target_type.lower():
        type_folder = 'Channel'
    elif 'video' in target_type.lower():
        type_folder = 'Video'
    else:
        type_folder = 'Others'
        
    target_dir = os.path.join(base_dir, date_folder, type_folder)
    if not os.path.exists(target_dir):
        return None
        
    safe_target = sanitize_filename(target_type)
    safe_category = sanitize_filename(category)
    safe_country = sanitize_filename(country)
    safe_period = sanitize_filename(period)
    safe_criteria = sanitize_filename(criteria) if criteria else None
    
    try:
        files = os.listdir(target_dir)
    except Exception as e:
        logger.warning(f"Failed to list directory {target_dir}: {e}")
        return None
        
    matching_files = []
    pure_category = safe_category.replace('batch_', '')
    
    # 정규식 패턴: 
    # criteria가 명시된 경우: {target}_{optional batch_}{category}_{country}_{period}_{criteria}_*.csv
    # criteria가 없는 경우: {target}_{optional batch_}{category}_{country}_{period}_*.csv (하위 호환성)
    # 하위 호환성 추가: criteria가 '조회수 순위'인 경우, 파일명에 criteria가 생략된 기존 파일도 매칭할 수 있도록 합니다.
    if safe_criteria:
        if safe_criteria in ['조회수 순위', '조회수_순위', '조회수']:
            pattern_regex = re.compile(
                rf"^{re.escape(safe_target)}_(?:batch_)?{re.escape(pure_category)}_{re.escape(safe_country)}_{re.escape(safe_period)}_(?:{re.escape(safe_criteria)}_)?.+\.csv$",
                re.IGNORECASE
            )
        else:
            pattern_regex = re.compile(
                rf"^{re.escape(safe_target)}_(?:batch_)?{re.escape(pure_category)}_{re.escape(safe_country)}_{re.escape(safe_period)}_{re.escape(safe_criteria)}_.+\.csv$",
                re.IGNORECASE
            )
    else:
        pattern_regex = re.compile(
            rf"^{re.escape(safe_target)}_(?:batch_)?{re.escape(pure_category)}_{re.escape(safe_country)}_{re.escape(safe_period)}_.+\.csv$",
            re.IGNORECASE
        )
    
    for f in files:
        if pattern_regex.match(f):
            full_path = os.path.join(target_dir, f)
            matching_files.append(full_path)
            
    if not matching_files:
        return None
        
    matching_files.sort(key=os.path.getmtime, reverse=True)
    return matching_files[0]


def standardize_dataframe_types(df, target_criteria):
    """
    데이터프레임의 모든 컬럼에 대해 데이터타입과 데이터 포맷을 표준 형식으로 강제 캐스팅하여
    기존 파일 데이터와 신규 수집 데이터 간의 불일치를 해소합니다.
    """
    if df is None or df.empty:
        return pd.DataFrame()
        
    df = df.copy()
    
    # 수치형 컬럼 표준화 (Rank, Views, Likes, Comments)
    numeric_cols = ['Rank', 'Views', 'Likes', 'Comments']
    for col in numeric_cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0).astype('int64')
            
    # 문자열형 컬럼 표준화 (결측값은 'N/A' 또는 빈값으로 대체)
    string_cols = [
        'Period', 'Ranking Date', 'Type', 'Country', 'Category', 'Criteria', 'Rank Change',
        'Video Title', 'Upload Date', 'Tags', 'Channel Name', 'Subscribers', 'Thumbnail', 'Video ID'
    ]
    for col in string_cols:
        if col in df.columns:
            df[col] = df[col].fillna('N/A').astype(str).str.strip()
            
    return df


def load_and_standardize_csv(filepath, target_criteria):
    """
    기존 CSV 파일을 읽어와 표준 스키마(Criteria 및 수집 기준에 부합하는 수치 컬럼)로 자동 정합 및 보정합니다.
    구버전 형식 감지 시 최신 표준 컬럼 순서 및 구성으로 덮어써서 마이그레이션을 자동 수행합니다.
    """
    import pandas as pd
    try:
        raw_df = pd.read_csv(filepath)
    except Exception as e:
        logger.warning(f"CSV load failed: {filepath}, error: {e}")
        return pd.DataFrame()

    if raw_df.empty:
        return raw_df

    original_cols = list(raw_df.columns)
    df = raw_df.copy()

    # 1. 수집 기준(Criteria) 컬럼 보완
    if 'Criteria' not in df.columns:
        df['Criteria'] = None
    
    # 2. 기존 수치 컬럼 분석을 통한 수집 기준(Criteria) 값 채우기
    detected_criteria = None
    if 'Views' in df.columns and df['Views'].notna().any():
        detected_criteria = '조회수 순위'
    elif 'Likes' in df.columns and df['Likes'].notna().any():
        detected_criteria = '좋아요 순위'
    elif 'Comments' in df.columns and df['Comments'].notna().any():
        detected_criteria = '댓글 순위'
    
    if not detected_criteria:
        detected_criteria = target_criteria if target_criteria else '조회수 순위'
        
    df['Criteria'] = df['Criteria'].fillna(detected_criteria)

    # 3. 현재 타겟 수집 기준에 상응하는 수치 컬럼 지정
    target_metric_col = 'Views'
    if target_criteria == '좋아요 순위':
        target_metric_col = 'Likes'
    elif target_criteria == '댓글 순위':
        target_metric_col = 'Comments'

    # 기존 파일에서 데이터가 들어있는 수치 컬럼 검색
    source_metric_col = None
    for col in ['Views', 'Likes', 'Comments']:
        if col in df.columns and df[col].notna().any():
            source_metric_col = col
            break

    # 수치 데이터 매핑 및 이전 컬럼 제거
    if source_metric_col and source_metric_col != target_metric_col:
        df[target_metric_col] = df[source_metric_col]
        if source_metric_col in df.columns:
            df.drop(columns=[source_metric_col], inplace=True)

    # 타겟 수치 컬럼이 존재하지 않으면 기본값 채움
    if target_metric_col not in df.columns:
        df[target_metric_col] = 0

    # 4. 표준 스키마 구성 정의
    standard_columns = [
        'Period', 'Ranking Date', 'Type', 'Country', 'Category', 'Criteria', 'Rank', 'Rank Change',
        'Video Title', target_metric_col, 'Upload Date', 'Tags',
        'Channel Name', 'Subscribers', 'Thumbnail', 'Video ID'
    ]
    
    # 누락된 표준 컬럼들을 None으로 보강
    for col in standard_columns:
        if col not in df.columns:
            df[col] = None

    # 표준 컬럼 순서 재배치
    standardized_df = df[standard_columns]

    # 5. 기존 파일의 컬럼 구조가 표준 구조와 다르면 자동 갱신(마이그레이션)
    if original_cols != standard_columns:
        logger.info(f"[Migration] 구버전 스키마 감지: '{filepath}'의 포맷을 표준 형식으로 강제 업데이트합니다.")
        try:
            standardized_df.to_csv(filepath, index=False, encoding='utf-8-sig')
            logger.info(f"✓ '{filepath}' 파일 포맷 마이그레이션 완료")
        except Exception as save_err:
            logger.warning(f"구버전 파일 표준 포맷 강제 갱신 저장 실패: {save_err}")

    standardized_df = standardize_dataframe_types(standardized_df, target_criteria)
    return standardized_df


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
        "sync_limit_video": 50
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

tabs = st.tabs(["🎯 플레이보드 크롤러", "📊 크롤링 데이터", "🔍 API 연동데이터", "🔌 API 동기화"])


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
        
        # 9. 수집 기준
        criteria_options = ["조회수 순위", "좋아요 순위", "댓글 순위"]
        criteria_val = saved_ui.get("crawl_criteria", "조회수 순위")
        criteria_idx = criteria_options.index(criteria_val) if criteria_val in criteria_options else 0
        crawl_criteria = st.selectbox(
            "수집 기준 (Criteria)",
            criteria_options,
            index=criteria_idx,
            key="crawl_criteria",
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
        if st.button("🔄 예측 상태 실시간 갱신", use_container_width=True, key="refresh_prediction_btn"):
            st.session_state['crawl_result'] = None
            st.rerun()
        
        if not batch_mode:
            # 단일 카테고리 예측
            calc_ranking_date = specific_date if use_specific_date else get_actual_ranking_date_str(specific_date)
            existing_filepath = find_existing_batch_file(
                base_dir=Config.OUTPUT_DIR,
                target_type=target_type,
                category=category,
                country=country,
                period=period,
                criteria=crawl_criteria,
                ranking_date=calc_ranking_date
            )
            
            already_collected = 0
            if existing_filepath:
                try:
                    existing_df = load_and_standardize_csv(existing_filepath, crawl_criteria)
                    already_collected = len(existing_df)
                except Exception:
                    pass
            
            if already_collected == 0:
                date_label = f"'{calc_ranking_date}' 기준"
                st.info(f"📂 **[신규 파일 생성]** {date_label} 파일이 없습니다. 새롭게 **{crawl_limit}**개를 수집합니다.")
            elif already_collected >= crawl_limit:
                st.success(f"✓ **[수집 완료 건너뜀]** 이미 **{already_collected}**개가 수집되어 목표치({crawl_limit}개)를 충족했습니다. 크롤링을 건너뜁니다.")
            else:
                st.warning(f"🔄 **[기존 파일 채우기]** 이미 **{already_collected}**개가 수집되어 있습니다. 부족한 **{crawl_limit - already_collected}**개를 추가 수집합니다.")
        else:
            # 일괄 카테고리 예측
            calc_ranking_date = specific_date if use_specific_date else get_actual_ranking_date_str(specific_date)
            all_categories = get_category_list()
            batch_records = []
            new_cats = 0
            update_cats = 0
            skip_cats = 0
            
            for cat in all_categories:
                batch_cat_name = f"batch_{cat}"
                existing_filepath = find_existing_batch_file(
                    base_dir=Config.OUTPUT_DIR,
                    target_type=target_type,
                    category=batch_cat_name,
                    country=country,
                    period=period,
                    criteria=crawl_criteria,
                    ranking_date=calc_ranking_date
                )
                
                already_collected = 0
                if existing_filepath:
                    try:
                        existing_df = load_and_standardize_csv(existing_filepath, crawl_criteria)
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
            start_btn = st.button("🚀 크롤링 시작", use_container_width=True, key="crawl_start_btn")
        with btn_col2:
            resume_btn = st.button("⏯️ 수동 재개", use_container_width=True, key="crawl_resume_btn")
        with btn_col3:
            skip_btn = st.button("⏭️ 다음 카테고리", use_container_width=True, key="crawl_skip_btn")
        with btn_col4:
            stop_btn = st.button("🛑 프로세스 중단", use_container_width=True, key="crawl_stop_btn")
            
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
                ranking_date = specific_date if specific_date else datetime.now().strftime('%Y-%m-%d')
                calc_ranking_date = ranking_date if use_specific_date else get_actual_ranking_date_str(ranking_date)
                target_count = crawl_limit
                
                logger.info("=" * 60)
                logger.info("🚀 [크롤링 기동 옵션 디버그]")
                logger.info(f"  - 수집 대상 (Target Type)  : {target_type}")
                logger.info(f"  - 배치 모드 (Batch Mode)   : {batch_mode}")
                logger.info(f"  - 카테고리 (Category)      : {category if not batch_mode else '전체 일괄'}")
                logger.info(f"  - 국가 (Country)           : {country}")
                logger.info(f"  - 기간 (Period)            : {period}")
                logger.info(f"  - 특정 날짜 수집 (Use Date): {use_specific_date} (날짜: {ranking_date})")
                logger.info(f"  - 로그인 모드 (Login Mode) : {login_mode}")
                logger.info(f"  - 수집 제한 개수 (Limit)   : {target_count}")
                logger.info(f"  - 수집 기준 (Criteria)     : {crawl_criteria}")
                logger.info("=" * 60)
                
                timestamp = None
                if specific_date:
                    dt = datetime.strptime(specific_date, '%Y-%m-%d')
                    timestamp = int(dt.timestamp())
                
                crawler = PlayboardCrawler(headless=Config.CHROME_HEADLESS)
                crawler.skip_requested = False
                st.session_state['crawler_instance'] = crawler
                
                if not batch_mode:
                    status_text.info(f"크롤링 시작 중: {target_type} / {category} / {country} / {period}...")
                    progress_bar.progress(0.1)
                    
                    url = build_url(target_type, category, country, period, timestamp)
                    logger.info(f"Built URL: {url}")
                    
                    existing_filepath = find_existing_batch_file(
                        base_dir=Config.OUTPUT_DIR,
                        target_type=target_type,
                        category=category,
                        country=country,
                        period=period,
                        criteria=crawl_criteria,
                        ranking_date=calc_ranking_date
                    )
                    
                    already_collected = 0
                    existing_df = pd.DataFrame()
                    if existing_filepath:
                        try:
                            existing_df = load_and_standardize_csv(existing_filepath, crawl_criteria)
                            already_collected = len(existing_df)
                            logger.info(f"이어서 수집: 기존 파일 발견 -> {existing_filepath} (기존 {already_collected}개)")
                        except Exception as csv_err:
                            logger.warning(f"기존 CSV 파일 읽기 실패 (새로 수집 진행): {csv_err}")
                            
                    if already_collected >= target_count:
                        logger.info(f"이미 {already_collected}개의 항목이 수집되어 목표치 {target_count}에 도달했습니다. 수집을 건너뜁니다.")
                        df = existing_df.head(target_count)
                        progress_bar.progress(0.8)
                    else:
                        progress_bar.progress(0.3)
                        df_new = crawler.crawl(
                            url=url,
                            target_type=target_type,
                            login_mode=login_mode,
                            target_count=target_count,
                            country=country,
                            period=period,
                            ranking_date=ranking_date,
                            ranking_criteria=crawl_criteria,
                            start_rank=already_collected,
                            keep_open=True,
                            category=category,
                            use_specific_date=use_specific_date
                        )
                        
                        progress_bar.progress(0.8)
                        if len(existing_df) > 0 and len(df_new) > 0:
                            existing_df = standardize_dataframe_types(existing_df, crawl_criteria)
                            df_new = standardize_dataframe_types(df_new, crawl_criteria)
                            df = pd.concat([existing_df, df_new], ignore_index=True)
                            if 'Video ID' in df.columns:
                                df = df.drop_duplicates(subset=['Video ID'], keep='last')
                            else:
                                df = df.drop_duplicates(subset=['Video Title', 'Channel Name'], keep='last')
                        else:
                            df = df_new if len(df_new) > 0 else existing_df
                            
                        # Rank 값 1부터 정렬해서 재정의 (이가 빠지지 않도록 연속적인 순번 부여)
                        if len(df) > 0 and 'Rank' in df.columns:
                            df = standardize_dataframe_types(df, crawl_criteria)
                            df = df.sort_values(by='Rank').reset_index(drop=True)
                            df['Rank'] = range(1, len(df) + 1)
                            
                        # 실제 수집 데이터의 랭킹 날짜로 최종 네이밍 확정 (수집일이 아닌 랭킹 날짜 기준)
                        final_ranking_date = calc_ranking_date
                        if len(df) > 0 and 'Ranking Date' in df.columns:
                            first_val = df['Ranking Date'].iloc[0]
                            if pd.notna(first_val) and str(first_val) != 'N/A':
                                final_ranking_date = str(first_val).strip()
                                logger.info(f"[Save Path] 실제 감지된 날짜 기준으로 경로를 확정합니다: {final_ranking_date}")

                        filepath, filename = generate_safe_filepath(
                            base_dir=Config.OUTPUT_DIR,
                            target_type=target_type,
                            category=category,
                            country=country,
                            period=period,
                            criteria=crawl_criteria,
                            ranking_date=final_ranking_date,
                            extension='csv'
                        )
                        
                        if len(df) > 0:
                            metric_col = 'Views'
                            if crawl_criteria == '좋아요 순위':
                                metric_col = 'Likes'
                            elif crawl_criteria == '댓글 순위':
                                metric_col = 'Comments'
                            
                            csv_columns = ['Period', 'Ranking Date', 'Type', 'Country', 'Category', 'Criteria', 'Rank', 'Rank Change',
                                           'Video Title', metric_col, 'Upload Date', 'Tags',
                                           'Channel Name', 'Subscribers', 'Thumbnail', 'Video ID']
                            csv_df = df[[col for col in csv_columns if col in df.columns]]
                            csv_df.to_csv(filepath, index=False, encoding='utf-8-sig')
                            logger.info(f"✓ [CSV 저장 완료] 경로: {filepath} | 파일명: {filename}")
                        
                    if len(df) > 0:
                        db_count = db_handler.insert_dataframe(df, category, country, period, target_type)
                        db_handler.log_crawl_history(target_type, category, country, period, len(df), success=True)
                        
                        stats = {
                            "target_cats_count": 1,
                            "target_limit": target_count,
                            "updated_files_count": 1,
                            "updated_rows_count": len(df),
                            "newly_crawled_rows": len(df_new) if 'df_new' in locals() and df_new is not None else len(df),
                            "failed_cats_count": 0,
                            "failed_cats_list": []
                        }
                        
                        logger.info("============================================================\n"
                                    "🏆 [단일 크롤링 최종 수집 통계 요약]\n"
                                    f"  - 목표 카테고리 개수 : 1개 ({category})\n"
                                    f"  - 카테고리당 목표 개수 : {target_count}개\n"
                                    f"  - 실제 업데이트된 파일 수 : 1개\n"
                                    f"  - 업데이트된 파일의 총 행 수 : {len(df)}개\n"
                                    f"  - 새로 추가 수집된 영상 수   : {stats['newly_crawled_rows']}개\n"
                                    f"  - 실패한 카테고리 개수 : 0개\n"
                                    "============================================================")

                        st.session_state['crawl_result'] = {
                            "status": "success",
                            "is_batch": False,
                            "data": df.head(20).to_dict('records') if hasattr(df, 'to_dict') else [],
                            "filepath": filepath,
                            "filename": filename if 'filename' in locals() else os.path.basename(filepath),
                            "msg": f"✓ 단일 크롤링 완료: 총 {len(df)}개 항목 수집 및 DB 저장 완료 ({filename if 'filename' in locals() else os.path.basename(filepath)})",
                            "stats": stats
                        }
                    else:
                        st.session_state['crawl_result'] = {
                            "status": "warning",
                            "is_batch": False,
                            "msg": "⚠ 수집된 데이터가 없습니다."
                        }
                        
                else:
                    all_categories = get_category_list()
                    
                    # 통계 트래킹용 변수
                    target_cats_count = len(all_categories)
                    target_limit = target_count
                    updated_files_count = 0  # 실제 크롤링이 진행되어 업데이트된 파일 수
                    updated_rows_count = 0   # 실제 크롤링이 진행되어 저장된 최종 행 개수의 합
                    newly_crawled_rows = 0   # 새로 추가로 수집된 영상 행 수
                    failed_cats_list = []
                    skip_cats_count = 0
                    
                    # 1. 크롤링 전수조사 및 분류
                    needs_crawl = []   # (cat, existing_filepath, existing_df, already_collected) 튜플 저장
                    skipped_data = []  # 이미 수집 완료된 데이터 목록
                    success_count = 0
                    fail_count = 0
                    
                    for cat in all_categories:
                        batch_cat_name = f"batch_{cat}"
                        existing_filepath = find_existing_batch_file(
                            base_dir=Config.OUTPUT_DIR,
                            target_type=target_type,
                            category=batch_cat_name,
                            country=country,
                            period=period,
                            criteria=crawl_criteria,
                            ranking_date=calc_ranking_date
                        )
                        
                        already_collected = 0
                        existing_df = pd.DataFrame()
                        if existing_filepath:
                            try:
                                existing_df = load_and_standardize_csv(existing_filepath, crawl_criteria)
                                already_collected = len(existing_df)
                            except Exception as csv_err:
                                logger.warning(f"기존 CSV 파일 읽기 실패 (새로 수집 진행): {csv_err}")
                                
                        if already_collected >= target_count:
                            logger.info(f"카테고리 '{cat}'은 이미 {already_collected}개 수집 완료되었습니다. 실제 수집 제외.")
                            df_cat = existing_df.head(target_count)
                            if len(df_cat) > 0:
                                skipped_data.extend(df_cat.to_dict('records'))
                            success_count += 1
                            skip_cats_count += 1
                        else:
                            needs_crawl.append((cat, existing_filepath, existing_df, already_collected))
                    
                    all_data = list(skipped_data)
                    
                    if not needs_crawl:
                        logger.info("모든 카테고리가 이미 수집 목표치를 충족하였습니다. 크롤링 순회를 스킵합니다.")
                        status_text.success("✓ 모든 카테고리가 이미 목표 개수를 충족하여 수집을 건너뜁니다.")
                        progress_bar.progress(1.0)
                    else:
                        status_text.info(f"일괄 크롤링 시작: 실제 수집 대상 {len(needs_crawl)}개 카테고리 순회 중...")
                        
                        for idx, (cat, existing_filepath, existing_df, already_collected) in enumerate(needs_crawl):
                            pct = (idx / len(needs_crawl))
                            progress_bar.progress(pct)
                            status_text.info(f"카테고리 수집 중 ({idx+1}/{len(needs_crawl)}): '{cat}' 진행 중...")
                            
                            try:
                                url = build_url(target_type, cat, country, period, timestamp)
                                df_cat_new = crawler.crawl(
                                    url=url,
                                    target_type=target_type,
                                    login_mode=login_mode,
                                    target_count=target_count,
                                    country=country,
                                    period=period,
                                    ranking_date=ranking_date,
                                    ranking_criteria=crawl_criteria,
                                    start_rank=already_collected,
                                    keep_open=True,
                                    category=cat,
                                    use_specific_date=use_specific_date
                                )
                                # 사용자의 다음 카테고리 스킵 감지
                                if getattr(crawler, 'skip_requested', False):
                                    crawler.skip_requested = False  # 플래그 초기화
                                    logger.warning(f"⏯️ 사용자 요청에 의해 카테고리 '{cat}' 수집이 스킵되었습니다. 다음 카테고리로 넘어갑니다.")
                                    continue
                                    
                                if len(existing_df) > 0 and len(df_cat_new) > 0:
                                    existing_df = standardize_dataframe_types(existing_df, crawl_criteria)
                                    df_cat_new = standardize_dataframe_types(df_cat_new, crawl_criteria)
                                    df_cat = pd.concat([existing_df, df_cat_new], ignore_index=True)
                                    if 'Video ID' in df_cat.columns:
                                        df_cat = df_cat.drop_duplicates(subset=['Video ID'], keep='last')
                                    else:
                                        df_cat = df_cat.drop_duplicates(subset=['Video Title', 'Channel Name'], keep='last')
                                else:
                                    df_cat = df_cat_new if len(df_cat_new) > 0 else existing_df
                                    
                                # Rank 값 1부터 정렬해서 재정의 (이가 빠지지 않도록 연속적인 순번 부여)
                                if len(df_cat) > 0 and 'Rank' in df_cat.columns:
                                    df_cat = standardize_dataframe_types(df_cat, crawl_criteria)
                                    df_cat = df_cat.sort_values(by='Rank').reset_index(drop=True)
                                    df_cat['Rank'] = range(1, len(df_cat) + 1)
                                    
                                # 실제 수집 데이터의 랭킹 날짜로 최종 네이밍 확정 (수집일이 아닌 랭킹 날짜 기준)
                                final_ranking_date = calc_ranking_date
                                if len(df_cat) > 0 and 'Ranking Date' in df_cat.columns:
                                    first_val = df_cat['Ranking Date'].iloc[0]
                                    if pd.notna(first_val) and str(first_val) != 'N/A':
                                        final_ranking_date = str(first_val).strip()

                                batch_cat_name = f"batch_{cat}"
                                filepath, filename = generate_safe_filepath(
                                    base_dir=Config.OUTPUT_DIR,
                                    target_type=target_type,
                                    category=batch_cat_name,
                                    country=country,
                                    period=period,
                                    criteria=crawl_criteria,
                                    ranking_date=final_ranking_date,
                                    extension='csv'
                                )
                                    
                                if len(df_cat) > 0:
                                    metric_col = 'Views'
                                    if crawl_criteria == '좋아요 순위':
                                        metric_col = 'Likes'
                                    elif crawl_criteria == '댓글 순위':
                                        metric_col = 'Comments'
                                    
                                    csv_columns = ['Period', 'Ranking Date', 'Type', 'Country', 'Category', 'Criteria', 'Rank', 'Rank Change',
                                                   'Video Title', metric_col, 'Upload Date', 'Tags',
                                                   'Channel Name', 'Subscribers', 'Thumbnail', 'Video ID']
                                    csv_df = df_cat[[col for col in csv_columns if col in df_cat.columns]]
                                    csv_df.to_csv(filepath, index=False, encoding='utf-8-sig')
                                    logger.info(f"✓ [CSV 저장 완료] 경로: {filepath} | 파일명: {os.path.basename(filepath)}")
                                    
                                    # 통계 정보 누적
                                    updated_files_count += 1
                                    updated_rows_count += len(df_cat)
                                    newly_crawled_rows += len(df_cat_new) if 'df_cat_new' in locals() and df_cat_new is not None else len(df_cat)
                                    
                                if len(df_cat) > 0:
                                    all_data.extend(df_cat.to_dict('records'))
                                    success_count += 1
                                    db_handler.insert_dataframe(df_cat, cat, country, period, target_type)
                                    db_handler.log_crawl_history(target_type, cat, country, period, len(df_cat), success=True)
                                else:
                                    fail_count += 1
                            except Exception as cat_err:
                                fail_count += 1
                                failed_cats_list.append(cat)
                                import traceback
                                err_detail = traceback.format_exc()
                                logger.error(f"Error in batch category '{cat}': {cat_err}\n{err_detail}")
                                db_handler.log_crawl_history(target_type, cat, country, period, 0, success=False, error_message=str(cat_err))
                                
                                # 윈도우 OS 알림 및 효과음 발송
                                try:
                                    from modules.utils import show_notification, play_notification_sound
                                    play_notification_sound()
                                    show_notification(
                                        "유튜브 일괄 크롤러 기동 에러 발생",
                                        f"카테고리 '{cat}' 수집 중 에러가 발생하여 수집이 중단되었습니다: {cat_err}"
                                    )
                                except Exception as notify_err:
                                    logger.debug(f"Failed to send exception notification: {notify_err}")
                                    
                                # 에러 상황 세션 바인딩 및 루프 즉시 중단(중지)
                                err_stats = {
                                    "target_cats_count": target_cats_count,
                                    "target_limit": target_limit,
                                    "skip_cats_count": skip_cats_count,
                                    "updated_files_count": updated_files_count,
                                    "updated_rows_count": updated_rows_count,
                                    "newly_crawled_rows": newly_crawled_rows,
                                    "failed_cats_count": len(failed_cats_list),
                                    "failed_cats_list": failed_cats_list
                                }
                                
                                logger.info("============================================================\n"
                                            "🏆 [일괄 크롤링 최종 수집 통계 요약 (실패)]\n"
                                            f"  - 목표 카테고리 개수 : {target_cats_count}개\n"
                                            f"  - 카테고리당 목표 개수 : {target_limit}개\n"
                                            f"  - 건너뛴 카테고리 개수 : {skip_cats_count}개 (이미 목표치 충족)\n"
                                            f"  - 실제 업데이트된 파일 수 : {updated_files_count}개\n"
                                            f"  - 업데이트된 파일의 총 행 수 : {updated_rows_count}개\n"
                                            f"  - 새로 추가 수집된 영상 수   : {newly_crawled_rows}개\n"
                                            f"  - 수집 성공 카테고리 수 : {success_count}개\n"
                                            f"  - 수집 실패 카테고리 수 : {len(failed_cats_list)}개\n"
                                            f"  - 실패한 카테고리 목록   : {', '.join(failed_cats_list)}\n"
                                            "============================================================")
                                            
                                st.session_state['crawl_result'] = {
                                    "status": "error",
                                    "is_batch": True,
                                    "msg": f"✗ 일괄 크롤링 수집 실패: 카테고리 '{cat}' 수집 중 에러 발생 ({cat_err})",
                                    "stats": err_stats
                                }
                                break
                                
                        progress_bar.progress(1.0)
                    
                    if all_data:
                        combined_df = pd.DataFrame(all_data)
                        
                        # 각 서브 카테고리별 수집 완성도 검증 및 summary_df / under_target_df 작성
                        summary_records = []
                        under_target_records = []
                        for cat in all_categories:
                            cat_df = combined_df[combined_df['Category'] == cat] if 'Category' in combined_df.columns else pd.DataFrame()
                            collected_count = len(cat_df)
                            status = "✓ 충족" if collected_count >= target_count else "⚠️ 미달"
                            shortage = max(0, target_count - collected_count)
                            
                            summary_records.append({
                                "카테고리": cat,
                                "목표 수량": target_count,
                                "실제 수집 수량": collected_count,
                                "부족분": shortage,
                                "상태": status
                            })
                            
                            if collected_count < target_count:
                                under_target_records.append({
                                    "카테고리": cat,
                                    "목표 수량": target_count,
                                    "실제 수집 수량": collected_count,
                                    "부족 수량": shortage
                                })
                        
                        summary_df = pd.DataFrame(summary_records)
                        under_target_df = pd.DataFrame(under_target_records)
                        
                        final_comb_date = calc_ranking_date
                        if len(combined_df) > 0 and 'Ranking Date' in combined_df.columns:
                            first_val = combined_df['Ranking Date'].iloc[0]
                            if pd.notna(first_val) and str(first_val) != 'N/A':
                                final_comb_date = str(first_val).strip()

                        filepath_comb, filename_comb = generate_safe_filepath(
                            base_dir=Config.OUTPUT_DIR,
                            target_type=target_type,
                            category='ALL',
                            country=country,
                            period=period,
                            criteria=crawl_criteria,
                            ranking_date=final_comb_date,
                            extension='csv'
                        )
                        
                        metric_col = 'Views'
                        if crawl_criteria == '좋아요 순위':
                            metric_col = 'Likes'
                        elif crawl_criteria == '댓글 순위':
                            metric_col = 'Comments'
                        
                        csv_columns = ['Period', 'Ranking Date', 'Type', 'Country', 'Category', 'Criteria', 'Rank', 'Rank Change',
                                       'Video Title', metric_col, 'Upload Date', 'Tags',
                                       'Channel Name', 'Subscribers', 'Thumbnail', 'Video ID']
                        csv_df = combined_df[[col for col in csv_columns if col in combined_df.columns]]
                        csv_df.to_csv(filepath_comb, index=False, encoding='utf-8-sig')
                        logger.info(f"✓ [통합 CSV 저장 완료] 경로: {filepath_comb} | 파일명: {filename_comb}")
                        
                        # 최종 성공 통계 딕셔너리 구성
                        success_stats = {
                            "target_cats_count": target_cats_count,
                            "target_limit": target_limit,
                            "skip_cats_count": skip_cats_count,
                            "updated_files_count": updated_files_count,
                            "updated_rows_count": updated_rows_count,
                            "newly_crawled_rows": newly_crawled_rows,
                            "failed_cats_count": len(failed_cats_list),
                            "failed_cats_list": failed_cats_list
                        }
                        
                        logger.info("============================================================\n"
                                    "🏆 [일괄 크롤링 최종 수집 통계 요약]\n"
                                    f"  - 목표 카테고리 개수 : {target_cats_count}개\n"
                                    f"  - 카테고리당 목표 개수 : {target_limit}개\n"
                                    f"  - 건너뛴 카테고리 개수 : {skip_cats_count}개 (이미 목표치 충족)\n"
                                    f"  - 실제 업데이트된 파일 수 : {updated_files_count}개\n"
                                    f"  - 업데이트된 파일의 총 행 수 : {updated_rows_count}개\n"
                                    f"  - 새로 추가 수집된 영상 수   : {newly_crawled_rows}개\n"
                                    f"  - 수집 성공 카테고리 수 : {success_count}개\n"
                                    f"  - 수집 실패 카테고리 수 : {len(failed_cats_list)}개\n"
                                    f"  - 실패한 카테고리 목록   : {', '.join(failed_cats_list) if failed_cats_list else '없음'}\n"
                                    "============================================================")

                        st.session_state['crawl_result'] = {
                            "status": "success",
                            "is_batch": True,
                            "summary_data": summary_df.to_dict('records') if hasattr(summary_df, 'to_dict') else [],
                            "under_target_data": under_target_df.to_dict('records') if hasattr(under_target_df, 'to_dict') else [],
                            "data": combined_df.head(20).to_dict('records') if hasattr(combined_df, 'to_dict') else [],
                            "filepath": filepath_comb,
                            "filename": filename_comb if 'filename_comb' in locals() else os.path.basename(filepath_comb),
                            "target_count": target_count,
                            "msg": f"✓ 일괄 크롤링 완료: 성공 {success_count}개, 실패 {fail_count}개 (총 {len(combined_df)}개 레코드 저장)",
                            "stats": success_stats
                        }
                    else:
                        st.session_state['crawl_result'] = {
                            "status": "error",
                            "is_batch": True,
                            "msg": "✗ 모든 카테고리 일괄 크롤링 수집 실패"
                        }
                        
            except Exception as e:
                progress_bar.progress(1.0)
                
                # 에러 상황 통계 구성 (단일 vs 일괄 분기)
                if not batch_mode:
                    err_stats = {
                        "target_cats_count": 1,
                        "target_limit": target_count if 'target_count' in locals() else 100,
                        "updated_files_count": 0,
                        "updated_rows_count": 0,
                        "newly_crawled_rows": 0,
                        "failed_cats_count": 1,
                        "failed_cats_list": [category] if 'category' in locals() else ["알수없음"]
                    }
                    logger.info("============================================================\n"
                                "🏆 [단일 크롤링 최종 수집 통계 요약 (실패)]\n"
                                f"  - 목표 카테고리 개수 : 1개 ({category if 'category' in locals() else '알수없음'})\n"
                                f"  - 카테고리당 목표 개수 : {err_stats['target_limit']}개\n"
                                f"  - 실제 업데이트된 파일 수 : 0개\n"
                                f"  - 업데이트된 파일의 총 행 수 : 0개\n"
                                f"  - 실패한 카테고리 개수 : 1개 ({category if 'category' in locals() else '알수없음'})\n"
                                "============================================================")
                else:
                    l_failed = failed_cats_list if 'failed_cats_list' in locals() else ([category] if 'category' in locals() else ["알수없음"])
                    err_stats = {
                        "target_cats_count": target_cats_count if 'target_cats_count' in locals() else (len(all_categories) if 'all_categories' in locals() else 12),
                        "target_limit": target_limit if 'target_limit' in locals() else (target_count if 'target_count' in locals() else 100),
                        "skip_cats_count": skip_cats_count if 'skip_cats_count' in locals() else 0,
                        "updated_files_count": updated_files_count if 'updated_files_count' in locals() else 0,
                        "updated_rows_count": updated_rows_count if 'updated_rows_count' in locals() else 0,
                        "newly_crawled_rows": newly_crawled_rows if 'newly_crawled_rows' in locals() else 0,
                        "failed_cats_count": len(l_failed),
                        "failed_cats_list": l_failed
                    }
                    logger.info("============================================================\n"
                                "🏆 [일괄 크롤링 최종 수집 통계 요약 (실패)]\n"
                                f"  - 목표 카테고리 개수 : {err_stats['target_cats_count']}개\n"
                                f"  - 카테고리당 목표 개수 : {err_stats['target_limit']}개\n"
                                f"  - 건너뛴 카테고리 개수 : {err_stats.get('skip_cats_count', 0)}개 (이미 목표치 충족)\n"
                                f"  - 실제 업데이트된 파일 수 : {err_stats['updated_files_count']}개\n"
                                f"  - 업데이트된 파일의 총 행 수 : {err_stats['updated_rows_count']}개\n"
                                f"  - 새로 추가 수집된 영상 수   : {err_stats['newly_crawled_rows']}개\n"
                                f"  - 수집 실패 카테고리 수 : {err_stats['failed_cats_count']}개\n"
                                f"  - 실패한 카테고리 목록   : {', '.join(err_stats['failed_cats_list'])}\n"
                                "============================================================")

                st.session_state['crawl_result'] = {
                    "status": "error",
                    "msg": f"✗ 크롤링 도중 예외가 발생했습니다: {e}",
                    "stats": err_stats
                }
                
                # 상세 트레이스백 및 예외 로그 인쇄
                import traceback
                err_detail = traceback.format_exc()
                logger.error(f"Crawler error: {e}\n{err_detail}")
                
                # 윈도우 OS 알림 및 효과음 발송
                try:
                    from modules.utils import show_notification, play_notification_sound
                    play_notification_sound()
                    show_notification(
                        "유튜브 크롤러 기동 에러 발생",
                        f"크롤링 동작 중 에러가 발생하여 중지되었습니다: {e}"
                    )
                except Exception as notify_err:
                    logger.debug(f"Failed to send exception notification: {notify_err}")
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
                    </style>
                    
                    <script>
                    function performCopy(text) {
                        if (navigator.clipboard && window.isSecureContext) {
                            navigator.clipboard.writeText(text).then(function() {
                                alert("📋 클립보드에 복사되었습니다:\n" + text);
                            }).catch(function(err) {
                                fallbackCopy(text);
                            });
                        } else {
                            fallbackCopy(text);
                        }
                    }

                    function fallbackCopy(text) {
                        var textArea = document.createElement("textarea");
                        textArea.value = text;
                        textArea.style.position = "fixed";
                        textArea.style.left = "-999999px";
                        textArea.style.top = "-999999px";
                        document.body.appendChild(textArea);
                        textArea.focus();
                        textArea.select();
                        try {
                            var successful = document.execCommand('copy');
                            if (successful) {
                                alert("📋 클립보드에 복사되었습니다:\n" + text);
                            } else {
                                alert("❌ 복사 실패 (브라우저 제한)");
                            }
                        } catch (err) {
                            console.error('fallback 복사 에러:', err);
                            alert("❌ 복사 실패: " + err);
                        }
                        document.body.removeChild(textArea);
                    }

                    // 전역 및 부모 창에 바인딩하여 iframe 샌드박스 우회
                    window.copyToClipboard = performCopy;
                    if (window.parent) {
                        window.parent.copyToClipboard = performCopy;
                    }

                    // Streamlit HTML Sanitizer 우회용 이벤트 리스너 바인딩
                    document.addEventListener('click', function(e) {
                        var copyTarget = e.target.closest('[title="클릭 시 복사"]');
                        if (copyTarget) {
                            var text = copyTarget.getAttribute('data-copy-text');
                            if (text && window.copyToClipboard) {
                                window.copyToClipboard(text);
                                e.preventDefault();
                            }
                        }
                    });
                    </script>
                """)
                
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
                                    
                                    safe_sub = clean_js_text(sub_text)
                                    sub_html = f'<div style="margin-top: 4px;"><span class="list-subscribers" style="color: #ffffff; font-size: 1.1em; cursor: pointer;" title="클릭 시 복사" data-copy-text="{safe_sub}" onclick="window.copyToClipboard(\'{safe_sub}\')">👥 구독자수: {sub_text}</span></div>'
                            
                            sort_rank_html = ""
                            if show_sort_rank:
                                sort_rank_html = f"<span style='background-color: #007bff; color: white; border-radius: 4px; padding: 2px 6px; font-size: 0.95em; margin-right: 10px; font-weight: bold;'>🔄 정렬순위: {idx + 1}위</span>"

                            list_content = f"""
                            <div class="list-card">
                                <span class="list-badge">{rank}위 ({rc_display})</span>
                                <div class="list-thumbnail-container">
                                    <img src="{img_url}" class="list-thumbnail" onerror="this.src='https://images.unsplash.com/photo-1611162617213-7d7a39e9b1d7?w=300&auto=format&fit=crop&q=60'"/>
                                </div>
                                <div class="list-content">
                                    <div class="list-title" title="클릭 시 복사" data-copy-text="{safe_title}" onclick="window.copyToClipboard('{safe_title}')">{title}</div>
                                    <div class="list-info">
                                        <div style="margin-bottom: 4px;">
                                            <span style='background-color: #2c3e50; color: #ecf0f1; border-radius: 4px; padding: 2px 6px; font-size: 0.95em; margin-right: 10px; font-weight: bold;'>🏷️ {clean_cat}</span>
                                            {sort_rank_html}
                                            <span class="list-channel-name" title="클릭 시 복사" data-copy-text="{safe_channel}" onclick="window.copyToClipboard('{safe_channel}')">👤 채널명: {channel_name}</span>
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
                                        
                                        safe_sub = clean_js_text(sub_text)
                                        sub_html = f'<div class="card-info card-subscribers" style="color: #ffffff; font-size: 1.1em; margin-bottom: 5px; cursor: pointer;" title="클릭 시 복사" data-copy-text="{safe_sub}" onclick="window.copyToClipboard(\'{safe_sub}\')">👥 구독자수: {sub_text}</div>'
                                
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
                                <div class="dashboard-card">
                                    <span class="rank-badge">{rank}위 ({rc_display})</span>
                                    <span style='background-color: #2c3e50; color: #ecf0f1; border-radius: 4px; padding: 2px 6px; font-size: 0.85em; font-weight: bold; margin-left: 5px; display: inline-block; vertical-align: middle; margin-bottom: 5px;'>🏷️ {clean_cat}</span>
                                    {sort_rank_html}
                                    <img src="{img_url}" style="{img_style}" onerror="this.src='https://images.unsplash.com/photo-1611162617213-7d7a39e9b1d7?w=300&auto=format&fit=crop&q=60'"/>
                                    <div class="card-title" title="클릭 시 복사" data-copy-text="{safe_title}" onclick="window.copyToClipboard('{safe_title}')">{title}</div>
                                    <div class="card-info card-channel-name" style="font-weight:bold; margin-bottom:2px;" title="클릭 시 복사" data-copy-text="{safe_channel}" onclick="window.copyToClipboard('{safe_channel}')">👤 {channel_name}</div>
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
            
        c_search_btn = st.button("🔍 크롤링 데이터 검색 실행", use_container_width=True, key="crawl_search_btn")
        
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
# 탭 3: 🔍 API 연동데이터
# ==============================================================================
with tabs[2]:
    st.header("🔍 YouTube API 연동데이터 조회")
    
    # 🗂️ API 연동 DB 현황
    st.subheader("🗂️ API 연동 데이터 현황")
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = [r[0] for r in cursor.fetchall()]
        
        api_channels_count = 0
        api_videos_count = 0
        
        if 'api_channels' in tables:
            cursor.execute("SELECT COUNT(*) FROM api_channels")
            api_channels_count = cursor.fetchone()[0]
        if 'api_videos' in tables:
            cursor.execute("SELECT COUNT(*) FROM api_videos")
            api_videos_count = cursor.fetchone()[0]
            
        conn.close()
        
        c_api1, c_api2 = st.columns(2)
        c_api1.metric("API 연동 채널 수", f"{api_channels_count:,}개")
        c_api2.metric("API 연동 영상 수", f"{api_videos_count:,}개")
    except Exception as db_err:
        st.error(f"DB 현황 조회 에러: {db_err}")
        
    st.markdown("---")
    
    api_sub_tabs = st.tabs(["🔍 API 데이터 상세 검색", "📊 API 데이터 시각화"])
    
    with api_sub_tabs[0]:
        st.subheader("🔍 API 연동 데이터 상세 검색")
        
        if 'ui_settings' not in st.session_state or st.session_state['ui_settings'] is None:
            st.session_state['ui_settings'] = load_settings()
        saved_ui = st.session_state['ui_settings']
        
        col_a1, col_a2, col_a3 = st.columns([2, 1, 1])
        a_keyword = col_a1.text_input("검색 키워드 (제목, 채널명, 태그)", value=saved_ui.get("api_search_keyword", ""), key="api_search_keyword")
        
        a_type_options = ["전체", "shorts", "video"]
        a_type_val = saved_ui.get("api_search_type", "전체")
        a_type_idx = a_type_options.index(a_type_val) if a_type_val in a_type_options else 0
        a_type = col_a2.selectbox("비디오 타입", a_type_options, index=a_type_idx, key="api_search_type")
        
        a_sort_options = ["최근 등록일순", "조회수 높은순", "좋아요 비율 높은순"]
        a_sort_val = saved_ui.get("api_search_sort", "최근 등록일순")
        a_sort_idx = a_sort_options.index(a_sort_val) if a_sort_val in a_sort_options else 0
        a_sort = col_a3.selectbox("정렬 기준", a_sort_options, index=a_sort_idx, key="api_search_sort")
        
        with st.expander("⚙️ 고급 필터"):
            col_af1, col_af2 = st.columns(2)
            a_view_min = col_af1.number_input("최소 조회수", min_value=0, value=int(saved_ui.get("api_search_view_min", 0)), step=1000, key="api_search_view_min")
            a_view_max = col_af1.number_input("최대 조회수", min_value=0, value=int(saved_ui.get("api_search_view_max", 1000000000)), step=100000, key="api_search_view_max")
            
            default_a_date_from = saved_ui.get("api_search_date_from", datetime(2020, 1, 1).date())
            default_a_date_to = saved_ui.get("api_search_date_to", datetime.today().date())
            if isinstance(default_a_date_from, str):
                try: default_a_date_from = datetime.strptime(default_a_date_from, '%Y-%m-%d').date()
                except: default_a_date_from = datetime(2020, 1, 1).date()
            if isinstance(default_a_date_to, str):
                try: default_a_date_to = datetime.strptime(default_a_date_to, '%Y-%m-%d').date()
                except: default_a_date_to = datetime.today().date()
                
            a_date_from = col_af2.date_input("게시일 시작", value=default_a_date_from, key="api_search_date_from")
            a_date_to = col_af2.date_input("게시일 종료", value=default_a_date_to, key="api_search_date_to")
            
        a_search_btn = st.button("🔍 API 데이터 검색 실행", use_container_width=True, key="api_search_btn")
        
        if a_search_btn or a_keyword:
            try:
                conn = get_db_connection()
                cursor = conn.cursor()
                a_results = []
                
                if 'api_videos' in tables:
                    type_filter = ""
                    if a_type == 'shorts':
                        type_filter = "AND video_type = 'shorts'"
                    elif a_type == 'video':
                        type_filter = "AND video_type = 'video'"
                        
                    cursor.execute(f'''
                        SELECT 'api_videos' as source, video_id, title, channel_name,
                               view_count as views, like_count as likes, category_name as category, 
                               published_at, last_updated as crawled_at, video_type
                        FROM api_videos
                        WHERE (title LIKE ? OR channel_name LIKE ? OR tags LIKE ?) 
                              AND view_count >= ? AND view_count <= ? 
                              AND published_at >= ? AND published_at <= ?
                              {type_filter}
                    ''', (f'%{a_keyword}%', f'%{a_keyword}%', f'%{a_keyword}%', a_view_min, a_view_max, f"{a_date_from}T00:00:00Z", f"{a_date_to}T23:59:59Z"))
                    a_results = [dict(row) for row in cursor.fetchall()]
                    
                conn.close()
                
                if a_results:
                    df_a_res = pd.DataFrame(a_results)
                    
                    if a_sort == "최근 등록일순":
                        df_a_res = df_a_res.sort_values(by="published_at", ascending=False)
                    elif a_sort == "조회수 높은순":
                        df_a_res = df_a_res.sort_values(by="views", ascending=False)
                    elif a_sort == "좋아요 비율 높은순":
                        df_a_res['like_ratio'] = df_a_res.apply(lambda r: (r['likes'] / r['views'] * 100) if r['views'] > 0 and r['likes'] is not None else 0, axis=1)
                        df_a_res = df_a_res.sort_values(by="like_ratio", ascending=False)
                        
                    st.dataframe(df_a_res, use_container_width=True)
                    st.success(f"🔍 검색 완료: 총 {len(df_a_res):,}건 발견")
                else:
                    st.warning("검색 조건에 일치하는 API 연동 데이터가 없습니다.")
            except Exception as q_err:
                st.error(f"조회 중 오류 발생: {q_err}")
                logger.error("API 데이터 검색 중 오류 발생", exc_info=True)
                
    with api_sub_tabs[1]:
        st.subheader("📊 API 연동 데이터 시각화")
        
        col_afig1, col_afig2 = st.columns(2)
        
        with col_afig1:
            st.markdown("**연동 비디오 타입 비율 (쇼츠 vs 일반)**")
            try:
                conn = get_db_connection()
                df_api_types = pd.read_sql_query("SELECT video_type, COUNT(*) as count FROM api_videos GROUP BY video_type", conn)
                conn.close()
                
                if not df_api_types.empty:
                    fig_api_pie = px.pie(df_api_types, values='count', names='video_type', title='API 연동 비디오 타입 비율', hole=.3)
                    st.plotly_chart(fig_api_pie, use_container_width=True)
                else:
                    st.info("시각화할 API 비디오 타입 데이터가 없습니다.")
            except Exception as e:
                st.info("데이터를 불러올 수 없습니다.")
                
        with col_afig2:
            st.markdown("**API 연동 비디오 카테고리 분포**")
            try:
                conn = get_db_connection()
                df_api_cats = pd.read_sql_query("SELECT category_name, COUNT(*) as count FROM api_videos GROUP BY category_name", conn)
                conn.close()
                
                if not df_api_cats.empty:
                    df_api_cats['category_name'] = df_api_cats['category_name'].fillna('N/A')
                    fig_api_bar = px.bar(
                        df_api_cats, 
                        x='count', 
                        y='category_name', 
                        orientation='h', 
                        title='API 연동 비디오 카테고리 분포',
                        labels={'count': '영상 수', 'category_name': '카테고리명'}
                    )
                    fig_api_bar.update_layout(yaxis={'categoryorder':'total ascending'})
                    st.plotly_chart(fig_api_bar, use_container_width=True)
                else:
                    st.info("시각화할 API 비디오 카테고리 데이터가 없습니다.")
            except Exception as e:
                st.info("데이터를 불러올 수 없습니다.")

# ==============================================================================
# 탭 4: 🔌 API 동기화
# ==============================================================================
with tabs[3]:
    st.header("🔌 YouTube API 연동 & 동기화")
    
    st.subheader("🔗 Zero-Cost 채널 URL 동기화")
    st.markdown("YouTube 채널 홈 URL(예: `@지식채널e`)을 분석하여 채널 ID 추출 후 DB 동기화를 실행합니다.")
    
    if 'ui_settings' not in st.session_state or st.session_state['ui_settings'] is None:
        st.session_state['ui_settings'] = load_settings()
    saved_ui = st.session_state['ui_settings']

    channel_url_input = st.text_input("YouTube 채널 URL 입력", value=saved_ui.get("sync_channel_url", "https://www.youtube.com/@ebsdocumentary"), key="sync_channel_url")
    limit_video = st.slider("수집할 최신 영상 개수", min_value=10, max_value=100, value=int(saved_ui.get("sync_limit_video", 50)), step=10, key="sync_limit_video")
    
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
            logger.error("YouTube API 동기화 실행 중 예외 발생!", exc_info=True)


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

