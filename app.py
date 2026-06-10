import os
import sys
import time
import json
import sqlite3
import logging
from datetime import datetime, date
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
# Helper: 기존 오늘 날짜의 중간 수집 파일 검색
# ==============================================================================
def find_existing_batch_file(base_dir, target_type, category, country, period, criteria=None):
    """오늘 날짜 폴더 내에 이미 수집된 특정 조건의 최신 CSV 파일 경로를 탐색합니다. (정규식 기반 윈도우 호환성 보장)"""
    import re
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
        
        # 탭 2 (DB 검색)
        "search_keyword_val": "",
        "search_source_val": "전체",
        "search_type_val": "전체",
        "search_view_min": 0,
        "search_view_max": 1000000000,
        "search_date_from": datetime(2020, 1, 1).date(),
        "search_date_to": datetime.today().date(),
        "search_sort_by": "최근 등록일순",
        
        # 탭 3 (API 동기화)
        "sync_channel_url": "https://www.youtube.com/@ebsdocumentary",
        "sync_limit_video": 50
    }
    if os.path.exists(SETTINGS_FILE):
        try:
            with open(SETTINGS_FILE, 'r', encoding='utf-8') as f:
                saved = json.load(f)
                for k, v in saved.items():
                    if k in ["crawl_date_val", "search_date_from", "search_date_to"] and isinstance(v, str):
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
        
        if not batch_mode:
            # 단일 카테고리 예측
            existing_filepath = find_existing_batch_file(
                base_dir=Config.OUTPUT_DIR,
                target_type=target_type,
                category=category,
                country=country,
                period=period,
                criteria=crawl_criteria
            )
            
            already_collected = 0
            if existing_filepath:
                try:
                    existing_df = load_and_standardize_csv(existing_filepath, crawl_criteria)
                    already_collected = len(existing_df)
                except Exception:
                    pass
            
            if already_collected == 0:
                st.info(f"📂 **[신규 파일 생성]** 오늘 자 파일이 없습니다. 새롭게 **{crawl_limit}**개를 수집합니다.")
            elif already_collected >= crawl_limit:
                st.success(f"✓ **[수집 완료 건너뜀]** 이미 **{already_collected}**개가 수집되어 목표치({crawl_limit}개)를 충족했습니다. 크롤링을 건너뜁니다.")
            else:
                st.warning(f"🔄 **[기존 파일 채우기]** 이미 **{already_collected}**개가 수집되어 있습니다. 부족한 **{crawl_limit - already_collected}**개를 추가 수집합니다.")
        else:
            # 일괄 카테고리 예측
            all_categories = [cat for cat in CATEGORIES if cat != '전체']
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
                    criteria=crawl_criteria
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
            
            with st.expander("🔍 카테고리별 상세 예측 현황 보기", expanded=False):
                st.dataframe(pd.DataFrame(batch_records), use_container_width=True, hide_index=True)

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
        log_shell.code("\n".join(st.session_state['log_history']))
        
        if start_btn:
            st.session_state['log_history'] = []  # 새로 크롤링 시작 시 로그 초기화
            st.session_state['stop_requested'] = False
            st.session_state['resume_requested'] = False
            
            streamlit_handler = StreamlitLogHandler(log_shell)
            streamlit_handler.setFormatter(logging.Formatter('[%(asctime)s] %(levelname)-8s - %(message)s', '%H:%M:%S'))
            logger.addHandler(streamlit_handler)
            
            try:
                ranking_date = specific_date if specific_date else datetime.now().strftime('%Y-%m-%d')
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
                        criteria=crawl_criteria
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
                            category=category
                        )
                        
                        progress_bar.progress(0.8)
                        if len(existing_df) > 0 and len(df_new) > 0:
                            df = pd.concat([existing_df, df_new], ignore_index=True)
                            if 'Video ID' in df.columns:
                                df = df.drop_duplicates(subset=['Video ID'], keep='last')
                            else:
                                df = df.drop_duplicates(subset=['Video Title', 'Channel Name'], keep='last')
                        else:
                            df = df_new if len(df_new) > 0 else existing_df
                            
                        # Rank 값 1부터 정렬해서 재정의 (이가 빠지지 않도록 연속적인 순번 부여)
                        if len(df) > 0 and 'Rank' in df.columns:
                            df = df.sort_values(by='Rank').reset_index(drop=True)
                            df['Rank'] = range(1, len(df) + 1)
                            
                        if existing_filepath:
                            filepath = existing_filepath
                            filename = os.path.basename(existing_filepath)
                        else:
                            filepath, filename = generate_safe_filepath(
                                base_dir=Config.OUTPUT_DIR,
                                target_type=target_type,
                                category=category,
                                country=country,
                                period=period,
                                criteria=crawl_criteria,
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
                        
                    if len(df) > 0:
                        db_count = db_handler.insert_dataframe(df, category, country, period, target_type)
                        db_handler.log_crawl_history(target_type, category, country, period, len(df), success=True)
                        
                        progress_bar.progress(1.0)
                        status_text.success(f"✓ 단일 크롤링 완료: 총 {len(df)}개 항목 수집 및 DB 저장 완료 ({filename if 'filename' in locals() else os.path.basename(filepath)})")
                        
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
                            batch_cat_name = f"batch_{cat}"
                            existing_filepath = find_existing_batch_file(
                                base_dir=Config.OUTPUT_DIR,
                                target_type=target_type,
                                category=batch_cat_name,
                                country=country,
                                period=period,
                                criteria=crawl_criteria
                            )
                            
                            already_collected = 0
                            existing_df = pd.DataFrame()
                            if existing_filepath:
                                try:
                                    existing_df = load_and_standardize_csv(existing_filepath, crawl_criteria)
                                    already_collected = len(existing_df)
                                    logger.info(f"이어서 수집: 기존 일괄 파일 발견 -> {existing_filepath} (기존 {already_collected}개)")
                                except Exception as csv_err:
                                    logger.warning(f"기존 CSV 파일 읽기 실패 (새로 수집 진행): {csv_err}")
                                    
                            if already_collected >= target_count:
                                logger.info(f"카테고리 '{cat}'은 이미 {already_collected}개 수집 완료되었습니다. 건너뜁니다.")
                                df_cat = existing_df.head(target_count)
                            else:
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
                                    category=cat
                                )
                                if len(existing_df) > 0 and len(df_cat_new) > 0:
                                    df_cat = pd.concat([existing_df, df_cat_new], ignore_index=True)
                                    if 'Video ID' in df_cat.columns:
                                        df_cat = df_cat.drop_duplicates(subset=['Video ID'], keep='last')
                                    else:
                                        df_cat = df_cat.drop_duplicates(subset=['Video Title', 'Channel Name'], keep='last')
                                else:
                                    df_cat = df_cat_new if len(df_cat_new) > 0 else existing_df
                                    
                                # Rank 값 1부터 정렬해서 재정의 (이가 빠지지 않도록 연속적인 순번 부여)
                                if len(df_cat) > 0 and 'Rank' in df_cat.columns:
                                    df_cat = df_cat.sort_values(by='Rank').reset_index(drop=True)
                                    df_cat['Rank'] = range(1, len(df_cat) + 1)
                                    
                                if existing_filepath:
                                    filepath = existing_filepath
                                else:
                                    filepath, filename = generate_safe_filepath(Config.OUTPUT_DIR, target_type, batch_cat_name, country, period, criteria=crawl_criteria)
                                    
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
                                    
                            if len(df_cat) > 0:
                                all_data.extend(df_cat.to_dict('records'))
                                success_count += 1
                                db_handler.insert_dataframe(df_cat, cat, country, period, target_type)
                                db_handler.log_crawl_history(target_type, cat, country, period, len(df_cat), success=True)
                            else:
                                fail_count += 1
                        except Exception as cat_err:
                            fail_count += 1
                            logger.error(f"Error in batch category '{cat}': {cat_err}", exc_info=True)
                            db_handler.log_crawl_history(target_type, cat, country, period, 0, success=False, error_message=str(cat_err))
                            
                    progress_bar.progress(1.0)
                    if all_data:
                        combined_df = pd.DataFrame(all_data)
                        filepath_comb, filename_comb = generate_safe_filepath(Config.OUTPUT_DIR, target_type, 'ALL', country, period, criteria=crawl_criteria)
                        
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
                        
                        status_text.success(f"✓ 일괄 크롤링 완료: 성공 {success_count}개, 실패 {fail_count}개 (총 {len(combined_df)}개 레코드 저장)")
                        
                        # 각 카테고리별로 실제 CSV 데이터를 로드하여 목표 개수 대비 수집 현황 집계
                        summary_records = []
                        for cat in all_categories:
                            batch_cat_name = f"batch_{cat}"
                            filepath = find_existing_batch_file(
                                base_dir=Config.OUTPUT_DIR,
                                target_type=target_type,
                                category=batch_cat_name,
                                country=country,
                                period=period,
                                criteria=crawl_criteria
                            )
                            collected_count = 0
                            if filepath and os.path.exists(filepath):
                                try:
                                    temp_df = load_and_standardize_csv(filepath, crawl_criteria)
                                    collected_count = len(temp_df)
                                except Exception:
                                    pass
                            
                            missing_count = max(0, target_count - collected_count)
                            status = "충족" if missing_count == 0 else "미달"
                            
                            summary_records.append({
                                "카테고리": cat,
                                "목표 개수": target_count,
                                "수집 개수": collected_count,
                                "미달 개수": missing_count,
                                "상태": status
                            })
                        
                        summary_df = pd.DataFrame(summary_records)
                        under_target_df = summary_df[summary_df["미달 개수"] > 0]
                        
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
                if 'crawler_instance' in st.session_state and st.session_state['crawler_instance'] is not None:
                    try:
                        st.session_state['crawler_instance'].close()
                    except:
                        pass
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
    
    if 'ui_settings' not in st.session_state or st.session_state['ui_settings'] is None:
        st.session_state['ui_settings'] = load_settings()
    saved_ui = st.session_state['ui_settings']

    col_s1, col_s2, col_s3 = st.columns([2, 1, 1])
    search_keyword = col_s1.text_input("검색 키워드 (제목, 채널명, 태그)", value=saved_ui.get("search_keyword_val", ""), key="search_keyword_val")
    
    source_options = ["전체", "크롤링 데이터만", "API 연동 데이터만"]
    source_val = saved_ui.get("search_source_val", "전체")
    source_idx = source_options.index(source_val) if source_val in source_options else 0
    search_source = col_s2.selectbox("데이터 소스", source_options, index=source_idx, key="search_source_val")
    
    type_options = ["전체", "shorts", "video", "channel"]
    type_val = saved_ui.get("search_type_val", "전체")
    type_idx = type_options.index(type_val) if type_val in type_options else 0
    search_type = col_s3.selectbox("컨텐츠 타입", type_options, index=type_idx, key="search_type_val")
    
    with st.expander("⚙️ 고급 필터 및 정렬 설정"):
        col_f1, col_f2, col_f3 = st.columns(3)
        view_min = col_f1.number_input("최소 조회수", min_value=0, value=int(saved_ui.get("search_view_min", 0)), step=1000, key="search_view_min")
        view_max = col_f1.number_input("최대 조회수", min_value=0, value=int(saved_ui.get("search_view_max", 1000000000)), step=100000, key="search_view_max")
        
        default_date_from = saved_ui.get("search_date_from", datetime(2020, 1, 1).date())
        default_date_to = saved_ui.get("search_date_to", datetime.today().date())
        if isinstance(default_date_from, str):
            try: default_date_from = datetime.strptime(default_date_from, '%Y-%m-%d').date()
            except: default_date_from = datetime(2020, 1, 1).date()
        if isinstance(default_date_to, str):
            try: default_date_to = datetime.strptime(default_date_to, '%Y-%m-%d').date()
            except: default_date_to = datetime.today().date()
            
        date_from = col_f2.date_input("게시일 시작 (API 데이터용)", value=default_date_from, key="search_date_from")
        date_to = col_f2.date_input("게시일 종료 (API 데이터용)", value=default_date_to, key="search_date_to")
        
        sort_options = ["최근 등록일순", "조회수 높은순", "좋아요 비율 높은순"]
        sort_val = saved_ui.get("search_sort_by", "최근 등록일순")
        sort_idx = sort_options.index(sort_val) if sort_val in sort_options else 0
        sort_by = col_f3.selectbox("정렬 기준", sort_options, index=sort_idx, key="search_sort_by")
        
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
            logger.error("DB 상세 검색 조건 실행 중 예외 발생!", exc_info=True)
            
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

