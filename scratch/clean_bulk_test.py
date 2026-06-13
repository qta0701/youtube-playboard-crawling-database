import sqlite3

def clean_database():
    db_path = 'output/db/youtube_data.db'
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    # 1. sheet_channels 테이블에서 삭제
    cursor.execute("DELETE FROM sheet_channels WHERE channel_id = 'UC_BULK_TEST_9999'")
    deleted_channels = cursor.rowcount
    
    # 2. sheet_videos 테이블에서 삭제 (만약 존재한다면)
    cursor.execute("DELETE FROM sheet_videos WHERE channel_id = 'UC_BULK_TEST_9999'")
    deleted_videos = cursor.rowcount
    
    conn.commit()
    conn.close()
    print(f"Clean up complete: deleted {deleted_channels} channels and {deleted_videos} videos with ID 'UC_BULK_TEST_9999'.")

if __name__ == "__main__":
    clean_database()
