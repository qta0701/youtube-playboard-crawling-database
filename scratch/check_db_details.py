import sqlite3
import pandas as pd

DB_PATH = 'output/db/youtube_data.db'
conn = sqlite3.connect(DB_PATH)

df = pd.read_sql_query(
    "SELECT category, country, period, criteria, count(*), min(rank), max(rank) "
    "FROM shorts_rank "
    "WHERE substr(crawled_at, 1, 10) = '2026-06-14' "
    "AND category = 'batch_게임' "
    "GROUP BY category, country, period, criteria",
    conn
)
print("=== batch_게임 DB 상태 ===")
print(df)

df_all = pd.read_sql_query(
    "SELECT category, country, period, criteria, count(*), min(rank), max(rank) "
    "FROM shorts_rank "
    "WHERE substr(crawled_at, 1, 10) = '2026-06-14' "
    "GROUP BY category, country, period, criteria",
    conn
)
print("\n=== 2026-06-14 전체 DB 상태 ===")
pd.set_option('display.max_rows', 100)
print(df_all)

conn.close()
