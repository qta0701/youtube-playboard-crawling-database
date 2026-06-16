import sqlite3
import pandas as pd

DB_PATH = 'output/db/youtube_data.db'
conn = sqlite3.connect(DB_PATH)

df = pd.read_sql_query(
    "SELECT * FROM shorts_rank WHERE category = '게임'",
    conn
)
print("=== category = '게임' 인 레코드 ===")
print(df[['video_id', 'title', 'category', 'criteria', 'crawled_at']])

df_batch = pd.read_sql_query(
    "SELECT * FROM shorts_rank WHERE category = 'batch_게임'",
    conn
)
print("\n=== category = 'batch_게임' 인 레코드 ===")
print(df_batch[['video_id', 'title', 'category', 'criteria', 'crawled_at']])

conn.close()
