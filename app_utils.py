import os
import re
import logging
import pandas as pd
from datetime import datetime, date, timedelta
from config import Config
from modules.utils import sanitize_filename

logger = logging.getLogger('crawler')

def get_actual_ranking_date_str(specific_date_str):
    """선택한 수집 날짜 기준 1일 전 날짜를 계산해 실제 플레이보드 랭킹 날짜로 매칭시킵니다."""
    try:
        dt = datetime.strptime(specific_date_str, "%Y-%m-%d") - timedelta(days=1)
        return dt.strftime("%Y-%m-%d")
    except Exception as e:
        logger.warning(f"Failed to calculate actual ranking date: {e}")
        return specific_date_str


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
