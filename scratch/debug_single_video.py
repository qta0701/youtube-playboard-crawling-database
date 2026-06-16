import sqlite3
import pandas as pd

DB_PATH = 'output/db/youtube_data.db'
conn = sqlite3.connect(DB_PATH)

df = pd.read_sql_query(
    "SELECT id, category, criteria, rank "
    "FROM shorts_rank "
    "WHERE video_id = 'QSlcUrZpEBE' AND substr(crawled_at, 1, 10) = '2026-06-14'",
    conn
)
print("=== QSlcUrZpEBE 카테고리 및 순위 ===")
print(df)

conn.close()
