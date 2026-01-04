# -*- coding: utf-8 -*-
import sqlite3

conn = sqlite3.connect('output/db/youtube_data.db')
conn.row_factory = sqlite3.Row
cursor = conn.cursor()

# 실제 API 쿼리 테스트 - N/A 포함
query = '''
SELECT DISTINCT
    vr.channel_id,
    vr.channel_name,
    vr.thumbnail_url,
    '' as channel_url,
    vr.category,
    vr.country,
    'video' as data_source,
    MAX(vr.crawled_at) as last_crawled_at
FROM videos_rank vr
WHERE vr.channel_name IS NOT NULL AND vr.channel_name != ''
GROUP BY vr.channel_name
LIMIT 5
'''

cursor.execute(query)
rows = cursor.fetchall()

print(f'Found {len(rows)} rows')
for i, row in enumerate(rows):
    print(f'\nRow {i+1}:')
    print(f'  channel_id: {row["channel_id"]}')
    print(f'  channel_name: {row["channel_name"]}')
    print(f'  category: {row["category"]}')
    print(f'  country: {row["country"]}')
    print(f'  data_source: {row["data_source"]}')

conn.close()
