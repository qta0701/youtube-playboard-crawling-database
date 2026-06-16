import sqlite3
import pandas as pd

DB_PATH = 'output/db/youtube_data.db'
conn = sqlite3.connect(DB_PATH)

selected_date = '2026-06-14'
selected_country = '한국'
selected_period = '일간'
selected_criteria = '조회수 순위'

order_clause = "rank ASC"

df_dash = pd.read_sql_query(
    f"SELECT * FROM shorts_rank WHERE substr(crawled_at, 1, 10) = ? AND country = ? AND period = ? ORDER BY {order_clause}", 
    conn, params=(selected_date, selected_country, selected_period)
)
print("1. 최초 로드된 전체 행 수:", len(df_dash))
print("   이 중 category = 'batch_인물_블로그' 인 행 수:", len(df_dash[df_dash['category'] == 'batch_인물_블로그']))

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
print("\n2. 중복 제거 후 전체 행 수:", len(df_dedup))
print("   이 중 category = 'batch_인물_블로그' 인 행 수:", len(df_dedup[df_dedup['category'] == 'batch_인물_블로그']))

df_final = df_dedup[df_dedup['is_selected_crit'] == True]
print("\n3. 최종 criteria 필터 후 전체 행 수:", len(df_final))
print("   이 중 category = 'batch_인물_블로그' 인 행 수:", len(df_final[df_final['category'] == 'batch_인물_블로그']))

conn.close()
