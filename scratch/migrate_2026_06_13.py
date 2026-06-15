import os
import re
import sqlite3
import pandas as pd
from datetime import datetime

# 데이터베이스 및 CSV 폴더 경로 설정
DB_PATH = 'output/db/youtube_data.db'
TARGET_DIR = 'output/2026_06_13/Shorts'
COMBINED_FILE = os.path.join(TARGET_DIR, 'shorts_ALL_한국_일간_조회수 순위_2026_06_13.csv')

# 슬래시 카테고리 매핑 정보
CATEGORY_MAPPING = {
    "인물_블로그": "인물/블로그",
    "뉴스_정치": "뉴스/정치",
    "영화_애니메이션": "영화/애니메이션",
    "노하우_스타일": "노하우/스타일"
}

def parse_count_string(count_str):
    if not count_str or count_str == 'N/A':
        return 0
    count_str = str(count_str).strip().replace(',', '')
    try:
        if '억' in count_str:
            return int(float(count_str.replace('억', '')) * 100_000_000)
        elif '만' in count_str:
            return int(float(count_str.replace('만', '')) * 10_000)
        elif 'B' in count_str.upper():
            return int(float(count_str.upper().replace('B', '')) * 1_000_000_000)
        elif 'M' in count_str.upper():
            return int(float(count_str.upper().replace('M', '')) * 1_000_000)
        elif 'K' in count_str.upper():
            return int(float(count_str.upper().replace('K', '')) * 1_000)
        else:
            return int(float(count_str))
    except Exception:
        return 0

def migrate():
    if not os.path.exists(TARGET_DIR):
        print(f"[ERROR] Target directory not found: {TARGET_DIR}")
        return
        
    print(f"Scanning CSV files in {TARGET_DIR}...")
    files = os.listdir(TARGET_DIR)
    
    all_data = []
    
    # 1. 개별 batch CSV 파일들 로드 및 병합
    for f in files:
        if f.startswith('shorts_batch_') and f.endswith('.csv'):
            filepath = os.path.join(TARGET_DIR, f)
            print(f"Loading {f}...")
            try:
                df = pd.read_csv(filepath)
                
                # 파일명에서 카테고리 추출해 검증
                # 파일명 형식: shorts_batch_{category}_{country}_{period}_{criteria}_{date}.csv
                match = re.search(r'shorts_batch_(.+?)_한국_일간_', f)
                if match:
                    file_cat = match.group(1)
                    # 파일명에 포함된 카테고리명이 매핑에 있으면 본래 슬래시 형태의 명칭으로 복원
                    correct_cat = CATEGORY_MAPPING.get(file_cat, file_cat)
                    df['Category'] = correct_cat
                    
                all_data.append(df)
            except Exception as e:
                print(f"[ERROR] Failed to load {f}: {e}")
                
    if not all_data:
        print("[ERROR] No batch data loaded.")
        return
        
    # 데이터프레임 병합
    combined_df = pd.concat(all_data, ignore_index=True)
    
    # 중복 제거 (Video ID 기준, 없으면 Video Title 및 Channel Name 기준)
    if 'Video ID' in combined_df.columns:
        combined_df = combined_df.drop_duplicates(subset=['Video ID'], keep='last')
    else:
        combined_df = combined_df.drop_duplicates(subset=['Video Title', 'Channel Name'], keep='last')
        
    # 순위 재정렬
    if 'Rank' in combined_df.columns:
        combined_df['Rank'] = pd.to_numeric(combined_df['Rank'], errors='coerce').fillna(999).astype(int)
        combined_df = combined_df.sort_values(by=['Category', 'Rank']).reset_index(drop=True)
        
    # 2. 통합 CSV 파일 저장
    print(f"Saving combined CSV to {COMBINED_FILE}...")
    combined_df.to_csv(COMBINED_FILE, index=False, encoding='utf-8-sig')
    print(f"✓ Combined CSV saved successfully. Total rows: {len(combined_df)}")
    
    # 3. SQLite DB 동기화 적재
    print(f"Syncing data to SQLite DB: {DB_PATH}")
    if not os.path.exists(DB_PATH):
        print(f"[ERROR] Database not found at {DB_PATH}")
        return
        
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    upserted_count = 0
    
    for _, row in combined_df.iterrows():
        try:
            video_id = row.get('Video ID', '')
            if not video_id or video_id == 'N/A':
                # 대체 ID 생성
                rank = row.get('Rank', 999)
                title = row.get('Video Title', '')[:50]
                video_id = f"rank_{rank}_{hash(title) % 100000}"
                
            views = parse_count_string(row.get('Views', 0))
            likes = parse_count_string(row.get('Likes', 0))
            comments = parse_count_string(row.get('Comments', 0))
            
            ranking_date_val = row.get('Ranking Date', None)
            if ranking_date_val and ranking_date_val != 'N/A':
                crawled_at = str(ranking_date_val).strip()
                if len(crawled_at) == 10:
                    crawled_at = f"{crawled_at}T12:00:00"
            else:
                crawled_at = "2026-06-13T12:00:00"
                
            category = row.get('Category', '전체')
            country = row.get('Country', '한국')
            period = row.get('Period', '일간')
            
            # DB 중복 체크 (shorts_rank)
            cursor.execute('''
                SELECT id FROM shorts_rank
                WHERE video_id = ? AND category = ? AND country = ? AND period = ?
                ORDER BY crawled_at DESC LIMIT 1
            ''', (video_id, category, country, period))
            
            existing = cursor.fetchone()
            
            if existing:
                # Update
                cursor.execute('''
                    UPDATE shorts_rank SET
                        title = ?, thumbnail_url = ?, channel_name = ?, channel_id = ?,
                        views = ?, likes = ?, comments = ?, rank = ?, rank_change = ?, upload_date = ?,
                        subscriber_count = ?, tags = ?, updated_at = ?, crawled_at = ?
                    WHERE id = ?
                ''', (
                    row.get('Video Title', 'N/A'),
                    row.get('Thumbnail', 'N/A'),
                    row.get('Channel Name', 'N/A'),
                    row.get('Channel ID', 'N/A'),
                    views,
                    likes,
                    comments,
                    row.get('Rank', 0),
                    row.get('Rank Change', 'N/A'),
                    row.get('Upload Date', 'N/A'),
                    row.get('Subscribers', ''),
                    row.get('Tags', ''),
                    crawled_at,
                    crawled_at,
                    existing[0]
                ))
            else:
                # Insert
                cursor.execute('''
                    INSERT INTO shorts_rank (
                        video_id, title, thumbnail_url, channel_name, channel_id,
                        views, likes, comments, rank, rank_change, upload_date, subscriber_count, tags,
                        category, country, period, crawled_at, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (
                    video_id,
                    row.get('Video Title', 'N/A'),
                    row.get('Thumbnail', 'N/A'),
                    row.get('Channel Name', 'N/A'),
                    row.get('Channel ID', 'N/A'),
                    views,
                    likes,
                    comments,
                    row.get('Rank', 0),
                    row.get('Rank Change', 'N/A'),
                    row.get('Upload Date', 'N/A'),
                    row.get('Subscribers', ''),
                    row.get('Tags', ''),
                    category,
                    country,
                    period,
                    crawled_at,
                    crawled_at
                ))
            upserted_count += 1
            
        except Exception as e:
            print(f"[ERROR] Failed to upsert row: {row.get('Video Title', 'N/A')}, error: {e}")
            
    conn.commit()
    conn.close()
    
    print(f"✓ SQLite DB sync completed. Total processed: {upserted_count} items.")
    print("Migration finished successfully.")

if __name__ == '__main__':
    migrate()
