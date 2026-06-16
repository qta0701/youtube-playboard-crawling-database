import sqlite3
import pandas as pd

DB_PATH = 'output/db/youtube_data.db'
conn = sqlite3.connect(DB_PATH)

df = pd.read_sql_query(
    "SELECT id, video_id, title, category, criteria, crawled_at "
    "FROM shorts_rank "
    "WHERE category = 'batch_게임' "
    "ORDER BY crawled_at, criteria, id",
    conn
)
print("=== batch_게임 전체 레코드 ===")
pd.set_option('display.max_rows', 200)
pd.set_option('display.width', 1000)
print(df)

conn.close()
