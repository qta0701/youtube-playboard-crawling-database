
import os

file_path = 'dashboard_app.py'

clean_code = '''
@app.route('/api/export/csv')
def api_export_csv():
    """CSV 내보내기 API (전체/필터)"""
    try:
        data_type = request.args.get('type', 'videos')  # videos, shorts, channels, all
        export_mode = request.args.get('mode', 'filtered')  # filtered, all

        # 필터 파라미터 (export_mode가 'filtered'일 때만 사용)
        category = request.args.get('category')
        country = request.args.get('country')
        period = request.args.get('period')
        keyword = request.args.get('keyword')
        
        # 날짜 필터
        crawl_date = request.args.get('crawl_date')
        crawl_period = request.args.get('crawl_period')
        crawl_date_from = request.args.get('crawl_date_from')
        crawl_date_to = request.args.get('crawl_date_to')
        
        upload_period = request.args.get('upload_period')
        upload_date_from = request.args.get('upload_date_from')
        upload_date_to = request.args.get('upload_date_to')

        # 정렬 (기본값: 수집일 최신순 + 순위 높은순)
        sort_by = request.args.get('sort_by', 'crawled_at')
        sort_order = request.args.get('sort_order', 'desc')

        # 영어 카테고리 -> 한국어 카테고리 매핑
        CATEGORY_MAP = {
            'Music': '음악',
            'Entertainment': '엔터테인먼트',
            'Gaming': '게임',
            'Sports': '스포츠',
            'Science & Technology': '과학기술',
            'Film & Animation': '영화/애니메이션',
            'People & Blogs': '인물/블로그',
            'Comedy': '코미디',
            'Education': '교육',
            'News & Politics': '뉴스/정치',
            'Howto & Style': '노하우/스타일'
        }

        if category and category in CATEGORY_MAP:
             logger.info(f"Category mapped: '{category}' -> '{CATEGORY_MAP[category]}'")
             category = CATEGORY_MAP[category]

        # 쿼리 생성
        conn = get_db_connection()
        cursor = conn.cursor()

        query = ""
        params = []

        # 'all' 모드면 필터 무시
        if export_mode == 'all':
            category = None
            country = None
            period = None
            keyword = None
            crawl_date = None
            crawl_period = None
            crawl_date_from = None
            crawl_date_to = None
            upload_period = None
            upload_date_from = None
            upload_date_to = None

        if data_type == 'channels':
            query = """
                SELECT 'channels' as data_type, channel_name, subscriber_count, total_views, 
                       rank, rank_change, category, country, period, received_views,
                       crawled_at, channel_url
                FROM channels_rank 
                WHERE 1=1
            """
        elif data_type == 'videos':
             query = """
                SELECT 'videos' as data_type, title, channel_name, views, rank, rank_change,
                       upload_date, subscriber_count, category, country, period,
                       crawled_at, video_id
                FROM videos_rank 
                WHERE 1=1
            """
        elif data_type == 'shorts':
             query = """
                SELECT 'shorts' as data_type, title, channel_name, views, rank, rank_change,
                       upload_date, subscriber_count, category, country, period,
                       crawled_at, video_id
                FROM shorts_rank 
                WHERE 1=1
            """
        else: # all
             query = """
                SELECT 'shorts' as data_type, title, channel_name, views, rank, rank_change,
                       upload_date, subscriber_count, category, country, period,
                       crawled_at, video_id
                FROM shorts_rank 
                WHERE 1=1
             """

        # Apply Filters
        if category:
            query += " AND category = ?"
            params.append(category)
        if country:
            query += " AND country = ?"
            params.append(country)
        if period:
            query += " AND period = ?"
            params.append(period)
        if keyword:
            if data_type == 'channels':
                query += " AND channel_name LIKE ?"
                params.append(f'%{keyword}%')
            else:
                query += " AND (title LIKE ? OR channel_name LIKE ?)"
                params.extend([f'%{keyword}%', f'%{keyword}%'])
        
        # Date Filters
        if crawl_date:
            query += " AND DATE(crawled_at) = ?"
            params.append(crawl_date)
        elif crawl_period:
            period_days = {'1d': 1, '3d': 3, '7d': 7, '14d': 14, '1m': 30, '3m': 90, '6m': 180, '1y': 365}
            if crawl_period in period_days:
                query += f" AND crawled_at >= datetime('now', '-{period_days[crawl_period]} days')"
        elif crawl_date_from:
            query += " AND DATE(crawled_at) >= ?"
            params.append(crawl_date_from)
            if crawl_date_to:
                 query += " AND DATE(crawled_at) <= ?"
                 params.append(crawl_date_to)

        if data_type != 'channels':
            if upload_period:
                period_days = {'1d': 1, '3d': 3, '7d': 7, '14d': 14, '1m': 30, '3m': 90, '6m': 180, '1y': 365}
                if upload_period in period_days:
                    query += f" AND upload_date >= date('now', '-{period_days[upload_period]} days')"
            elif upload_date_from:
                query += " AND upload_date >= ?"
                params.append(upload_date_from)
                if upload_date_to:
                    query += " AND upload_date <= ?"
                    params.append(upload_date_to)

        # UNION for 'all'
        if data_type == 'all':
            query += """
                UNION ALL
                SELECT 'videos' as data_type, title, channel_name, views, rank, rank_change,
                       upload_date, subscriber_count, category, country, period,
                       crawled_at, video_id
                FROM videos_rank 
                WHERE 1=1
            """
            if category:
                query += " AND category = ?"
                params.append(category)
            if country:
                query += " AND country = ?"
                params.append(country)
            if period:
                query += " AND period = ?"
                params.append(period)
            if keyword:
                query += " AND (title LIKE ? OR channel_name LIKE ?)"
                params.extend([f'%{keyword}%', f'%{keyword}%'])
            if crawl_date:
                query += " AND DATE(crawled_at) = ?"
                params.append(crawl_date)
            elif crawl_period:
                period_days = {'1d': 1, '3d': 3, '7d': 7, '14d': 14, '1m': 30, '3m': 90, '6m': 180, '1y': 365}
                if crawl_period in period_days:
                    query += f" AND crawled_at >= datetime('now', '-{period_days[crawl_period]} days')"
            elif crawl_date_from:
                query += " AND DATE(crawled_at) >= ?"
                params.append(crawl_date_from)
                if crawl_date_to:
                     query += " AND DATE(crawled_at) <= ?"
                     params.append(crawl_date_to)
            if upload_period:
                period_days = {'1d': 1, '3d': 3, '7d': 7, '14d': 14, '1m': 30, '3m': 90, '6m': 180, '1y': 365}
                if upload_period in period_days:
                    query += f" AND upload_date >= date('now', '-{period_days[upload_period]} days')"
            elif upload_date_from:
                query += " AND upload_date >= ?"
                params.append(upload_date_from)
                if upload_date_to:
                    query += " AND upload_date <= ?"
                    params.append(upload_date_to)

        # Sorting
        valid_sort_columns = ['views', 'rank', 'crawled_at', 'upload_date', 'channel_name', 'title', 'subscriber_count']
        if sort_by in valid_sort_columns:
            sort_dir = 'ASC' if sort_order.lower() == 'asc' else 'DESC'
            query += f" ORDER BY {sort_by} {sort_dir}"
            if sort_by == 'crawled_at':
                query += ", rank ASC"
        else:
            query += " ORDER BY crawled_at DESC, rank ASC"

        query += " LIMIT 50000" 

        cursor.execute(query, params)
        rows = cursor.fetchall()

        # CSV 생성
        si = io.StringIO()
        cw = csv.writer(si)
        
        if rows:
            # Headers
            headers = [d[0] for d in cursor.description]
            cw.writerow(headers)
            # Rows
            cw.writerows(rows)
        else:
            cw.writerow(['No Data Found'])

        conn.close()

        output = si.getvalue()
        
        # 파일명 생성
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        filename = f"youtube_data_{export_mode}_{data_type}_{timestamp}.csv"

        return Response(
            output,
            mimetype="text/csv",
            headers={"Content-disposition": f"attachment; filename={filename}"}
        )

    except Exception as e:
        logger.error(f"[Export CSV] Error: {e}", exc_info=True)
        return jsonify({'status': 'error', 'message': str(e)}), 500
'''

try:
    # Read original file (ignoring errors to bypass moji-bake)
    with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
        content = f.read()

    # Find the start of the bad function
    marker = "@app.route('/api/export/csv')"
    idx = content.find(marker)
    
    if idx != -1:
        # Keep everything before the bad function
        new_content = content[:idx] + clean_code
        print("Found matching marker, replacing function.")
    else:
        # If not found (maybe Mojibake destroyed the marker?), try to append.
        # But honestly, if not found, we risk duplication. 
        # Let's verify end of file.
        print("Marker not found. Checking if file was truncated or badly corrupted.")
        # If file ends with corruption, appending might be okay if we ensure newline.
        # But safer to just append clean code if not found, assuming it was cut off?
        # No, better to search for a known previous function.
        last_good_func = "def api_export_quota_estimate():"
        last_idx = content.find(last_good_func)
        if last_idx != -1:
             # Find end of that function? Naive way.
             # Better: assume the bad code is at the VERY END.
             # Truncate after api_export_quota_estimate ends?
             pass
        new_content = content + "\n\n" + clean_code
        
    # Write back with explicit UTF-8
    with open(file_path, 'w', encoding='utf-8') as f:
        f.write(new_content)
        
    print("Successfully re-wrote dashboard_app.py")

except Exception as e:
    print(f"Error: {e}")
