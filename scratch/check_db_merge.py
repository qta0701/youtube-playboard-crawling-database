import sqlite3
import pandas as pd

DB_PATH = 'output/db/youtube_data.db'
conn = sqlite3.connect(DB_PATH)

df = pd.read_sql_query(
    "SELECT * FROM shorts_rank WHERE country = '한국' AND period = '일간'", 
    conn
)

if not df.empty:
    df.columns = [c.lower() for c in df.columns]
    
    # merge_key는 날짜까지 포함해야 '동일 날짜 내 동일 비디오'가 됨!
    def get_merge_key(row):
        date_str = str(row.get('crawled_at', ''))[:10]
        vid = str(row.get('video_id', '')).strip()
        if vid and vid != 'N/A' and vid != 'None':
            return f"{date_str}|||{vid}"
        title_val = str(row.get('title', '')).strip()
        chan_val = str(row.get('channel_name', '')).strip()
        return f"{date_str}|||{title_val}|||{chan_val}"
    df['merge_key'] = df.apply(get_merge_key, axis=1)

    numeric_cols = ['views', 'likes', 'comments']
    for col in numeric_cols:
        df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0).astype('int64')

    grouped = df.groupby('merge_key')
    interesting_keys = []
    for key, group in grouped:
        if len(group) > 1:
            has_views = (group['views'] > 0).any()
            has_comments = (group['comments'] > 0).any()
            if has_views and has_comments:
                interesting_keys.append(key)
                if len(interesting_keys) >= 3:
                    break
                    
    if interesting_keys:
        for k in interesting_keys:
            print(f"\n[비디오 키: {k}]")
            print("병합 전:")
            print(df[df['merge_key'] == k][['merge_key', 'title', 'views', 'likes', 'comments', 'crawled_at']])
            
            # 병합
            df_max_nums = df.groupby('merge_key')[numeric_cols].max().reset_index()
            df_representatives = df.drop_duplicates(subset=['merge_key'], keep='first').copy()
            df_representatives.drop(columns=numeric_cols, inplace=True, errors='ignore')
            df_merged = pd.merge(df_representatives, df_max_nums, on='merge_key', how='left')
            
            print("병합 후:")
            print(df_merged[df_merged['merge_key'] == k][['merge_key', 'title', 'views', 'likes', 'comments', 'crawled_at']])
    else:
        print("전체 데이터 중 동일 날짜 내에 views와 comments가 교차로 0보다 큰 중복 수집 영상이 발견되지 않았습니다.")
        # 만약 그렇다면, views > 0 이고 likes > 0 인 다른 케이스 탐색
        interesting_keys_likes = []
        for key, group in grouped:
            if len(group) > 1:
                has_views = (group['views'] > 0).any()
                has_likes = (group['likes'] > 0).any()
                if has_views and has_likes:
                    interesting_keys_likes.append(key)
                    if len(interesting_keys_likes) >= 3:
                        break
        if interesting_keys_likes:
            print("\nviews와 likes가 교차로 존재하는 케이스:")
            for k in interesting_keys_likes:
                print(f"\n[비디오 키: {k}]")
                print("병합 전:")
                print(df[df['merge_key'] == k][['merge_key', 'title', 'views', 'likes', 'comments', 'crawled_at']])
                df_max_nums = df.groupby('merge_key')[numeric_cols].max().reset_index()
                df_representatives = df.drop_duplicates(subset=['merge_key'], keep='first').copy()
                df_representatives.drop(columns=numeric_cols, inplace=True, errors='ignore')
                df_merged = pd.merge(df_representatives, df_max_nums, on='merge_key', how='left')
                print("병합 후:")
                print(df_merged[df_merged['merge_key'] == k][['merge_key', 'title', 'views', 'likes', 'comments', 'crawled_at']])
        else:
            print("views와 likes가 교차로 존재하는 케이스도 없습니다.")

conn.close()
