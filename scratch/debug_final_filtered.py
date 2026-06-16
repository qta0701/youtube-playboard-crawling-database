import sqlite3
import pandas as pd

DB_PATH = 'output/db/youtube_data.db'
conn = sqlite3.connect(DB_PATH)

selected_date = '2026-06-14'
selected_country = '한국'
selected_period = '일간'
selected_criteria = '조회수 순위'
selected_cat = '인물/블로그'

order_clause = "rank ASC"

df_dash = pd.read_sql_query(
    f"SELECT * FROM shorts_rank WHERE substr(crawled_at, 1, 10) = ? AND country = ? AND period = ? ORDER BY {order_clause}", 
    conn, params=(selected_date, selected_country, selected_period)
)

def make_merge_key(row):
    vid = str(row.get('video_id', '')).strip()
    if vid and vid != 'N/A' and vid != 'None':
        return f"vid_{vid}"
    title = str(row.get('title', '')).strip()
    ch = str(row.get('channel_name', '')).strip()
    return f"tc_{title}_{ch}"

df_dash['merge_key'] = df_dash.apply(make_merge_key, axis=1)

agg_cols = ['views', 'likes', 'comments']
for col in agg_cols:
    df_dash[col] = pd.to_numeric(df_dash[col], errors='coerce').fillna(0).astype('int64')

max_metrics = df_dash.groupby('merge_key')[agg_cols].max().reset_index()

df_dash['is_selected_crit'] = df_dash['criteria'].astype(str) == str(selected_criteria)
df_dash['is_specific_cat'] = df_dash['category'].apply(lambda x: False if not x or str(x).upper() == 'ALL' or '전체' in str(x) else True)

df_dash['crawled_at_parsed'] = pd.to_datetime(df_dash['crawled_at'], errors='coerce')

sort_keys = ['is_selected_crit', 'is_specific_cat', 'crawled_at_parsed', 'id', 'rank']
sort_asc = [False, False, False, False, True]

df_sorted = df_dash.sort_values(by=sort_keys, ascending=sort_asc)
df_dedup = df_sorted.drop_duplicates(subset=['merge_key'], keep='first')

# 대표 행 기존 수치 제거 후 병합
df_dedup = df_dedup.drop(columns=agg_cols, errors='ignore')
df_dedup = pd.merge(df_dedup, max_metrics, on='merge_key', how='left')

# criteria 필터
df_crit = df_dedup[df_dedup['is_selected_crit'] == True]

# category 필터
df_filtered = df_crit[df_crit["category"].apply(lambda x: x.replace("batch_", "").replace("_", "/") if x else "") == selected_cat].copy()
print("1. 카테고리 필터 후 행 수:", len(df_filtered))

# df_filtered 소문자화 및 중복 제거
if not df_filtered.empty:
    df_filtered.columns = [c.lower() for c in df_filtered.columns]
    
    # crawled_at_parsed 정밀도
    df_filtered['crawled_at_parsed'] = pd.to_datetime(df_filtered['crawled_at'], errors='coerce')
    
    sort_cols = ['crawled_at_parsed', 'id']
    sort_asc = [False, False]
    df_filtered = df_filtered.sort_values(by=sort_cols, ascending=sort_asc).reset_index(drop=True)
    
    df_filtered = df_filtered.drop_duplicates(subset=["title", "channel_name"], keep="first")
    print("2. 최종 중복 제거 후 렌더링 대상 행 수:", len(df_filtered))
    if not df_filtered.empty:
        print(df_filtered[['rank', 'title', 'channel_name']])

conn.close()
