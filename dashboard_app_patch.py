
@app.route('/api/channel_manager/list')
def api_channel_manager_list():
    """
    채널 관리 목록 조회 API (개선 #41: 재생목록 기반으로 전환)

    재생목록에서 추출된 채널만 표시 (api_channels + monitored_playlists JOIN)

    Query Parameters:
        - sync_status: 'synced', 'unsynced', 'all' (기본값: 'all')
        - playlist_id: 특정 재생목록 필터 (선택)
        - sort_by: 'channel_name', 'subscriber_count', 'last_synced_at' (기본값: 'last_synced_at')
        - sort_order: 'asc', 'desc' (기본값: 'desc')
        - limit: 결과 개수 (기본값: 100)
        - offset: 페이지네이션 오프셋
    """
    try:
        sync_status = request.args.get('sync_status', 'all')
        playlist_id = request.args.get('playlist_id', '')
        sort_by = request.args.get('sort_by', 'last_synced_at')
        sort_order = request.args.get('sort_order', 'desc')
        limit = int(request.args.get('limit', 100))
        offset = int(request.args.get('offset', 0))

        conn = get_db_connection()
        cursor = conn.cursor()

        # ===== 개선 #41: 재생목록 기반 채널 목록 =====
        # api_channels에서 crawled_url='playlist'인 채널만 조회
        # monitored_playlists와 JOIN하여 재생목록 정보 표시
        main_query = '''
            SELECT
                ac.channel_id,
                ac.title as channel_name,
                ac.thumbnail_url,
                ac.subscriber_count,
                ac.video_count,
                ac.last_synced_at,
                ac.sync_status,
                ac.collected_video_count,
                ac.playlist_source,
                mp.title as playlist_title,
                mp.playlist_id,
                CASE WHEN ac.sync_status = 'synced' THEN 1 ELSE 0 END as is_synced,
                CAST(COALESCE(JulianDay('now') - JulianDay(ac.last_synced_at), 9999) AS INTEGER) as days_since_sync
            FROM api_channels ac
            LEFT JOIN monitored_playlists mp ON ac.playlist_source = mp.playlist_id
            WHERE ac.crawled_url = 'playlist'
        '''

        params = []

        # 필터 적용
        if sync_status == 'synced':
            main_query += " AND ac.sync_status = 'synced'"
        elif sync_status == 'unsynced':
            main_query += " AND (ac.sync_status IS NULL OR ac.sync_status != 'synced')"

        if playlist_id:
            main_query += " AND ac.playlist_source = ?"
            params.append(playlist_id)

        # 정렬
        sort_map = {
            'channel_name': 'ac.title',
            'subscriber_count': 'COALESCE(ac.subscriber_count, 0)',
            'last_synced_at': 'COALESCE(ac.last_synced_at, "")'
        }
        sort_column = sort_map.get(sort_by, 'COALESCE(ac.last_synced_at, "")')
        main_query += f" ORDER BY {sort_column} {sort_order.upper()}"

        # 페이지네이션
        main_query += " LIMIT ? OFFSET ?"
        params.extend([limit, offset])

        cursor.execute(main_query, params)
        channels = [dict(row) for row in cursor.fetchall()]

        # 총 개수 조회
        count_query = '''
            SELECT COUNT(*) as total
            FROM api_channels ac
            WHERE ac.crawled_url = 'playlist'
        '''
        count_params = []

        if sync_status == 'synced':
            count_query += " AND ac.sync_status = 'synced'"
        elif sync_status == 'unsynced':
            count_query += " AND (ac.sync_status IS NULL OR ac.sync_status != 'synced')"

        if playlist_id:
            count_query += " AND ac.playlist_source = ?"
            count_params.append(playlist_id)

        cursor.execute(count_query, count_params)
        total = cursor.fetchone()['total']

        conn.close()

        logger.debug(f"[Channel Manager List] Returned {len(channels)} channels (total: {total})")

        return jsonify({
            'status': 'success',
            'count': len(channels),
            'total': total,
            'channels': channels
        })

    except Exception as e:
        logger.error(f"[Channel Manager List] Error: {e}", exc_info=True)
        return jsonify({'status': 'error', 'message': str(e)}), 500

