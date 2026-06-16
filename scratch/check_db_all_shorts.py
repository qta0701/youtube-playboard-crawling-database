import sqlite3
import pandas as pd

DB_PATH = 'output/db/youtube_data.db'
conn = sqlite3.connect(DB_PATH)

df = pd.read_sql_query(
    "SELECT substr(crawled_at, 1, 10) as date_val, category, criteria, count(*) "
    "FROM shorts_rank "
    "GROUP BY date_val, category, criteria "
    "ORDER BY date_val DESC, category, criteria",
    conn
)
pd.set_option('display.max_rows', 200)
print(df)

conn.close()
