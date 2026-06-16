import sqlite3
import pandas as pd

DB_PATH = 'output/db/youtube_data.db'
conn = sqlite3.connect(DB_PATH)

print("=== DB 내 전체 쇼츠 개수 ===")
df_total = pd.read_sql_query("SELECT count(*) as cnt FROM shorts_rank", conn)
print(df_total)

print("\n=== 2026-06-14 데이터 개수 및 유니크 카테고리 ===")
df_date = pd.read_sql_query(
    "SELECT category, country, period, criteria, count(*) as cnt "
    "FROM shorts_rank "
    "WHERE substr(crawled_at, 1, 10) = '2026-06-14' "
    "GROUP BY category, country, period, criteria", 
    conn
)
print(df_date)

print("\n=== 2026-06-14 '과학기술' 데이터 샘플 ===")
df_sample = pd.read_sql_query(
    "SELECT rank, title, channel_name, category, criteria, crawled_at "
    "FROM shorts_rank "
    "WHERE substr(crawled_at, 1, 10) = '2026-06-14' "
    "AND category LIKE '%과학기술%'",
    conn
)
print(df_sample)

conn.close()
