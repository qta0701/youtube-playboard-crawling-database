
import sqlite3
import os

DB_PATH = 'output/db/youtube_data.db'

def check_categories():
    if not os.path.exists(DB_PATH):
        print(f"Database not found at {DB_PATH}")
        return

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    print("--- Videos Rank Categories ---")
    try:
        cursor.execute("SELECT DISTINCT category, count(*) FROM videos_rank GROUP BY category")
        for row in cursor.fetchall():
            print(row)
    except Exception as e:
        print(f"Error reading videos_rank: {e}")

    print("\n--- Shorts Rank Categories ---")
    try:
        cursor.execute("SELECT DISTINCT category, count(*) FROM shorts_rank GROUP BY category")
        for row in cursor.fetchall():
            print(row)
    except Exception as e:
        print(f"Error reading shorts_rank: {e}")

    conn.close()

if __name__ == "__main__":
    check_categories()
