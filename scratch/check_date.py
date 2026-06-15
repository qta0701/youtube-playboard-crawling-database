import sqlite3

DB_PATH = 'output/db/youtube_data.db'

def check_date_details():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    print("--- 2026-06-13 Shorts details ---")
    cursor.execute("""
        SELECT country, period, category, COUNT(*) 
        FROM shorts_rank 
        WHERE substr(crawled_at, 1, 10) = '2026-06-13'
        GROUP BY country, period, category
    """)
    for row in cursor.fetchall():
        print(row)
        
    conn.close()

if __name__ == '__main__':
    check_date_details()
