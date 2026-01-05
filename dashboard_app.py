"""
YouTube DB Dashboard Application
데이터베이스 관리 및 검색 전용 대시보드

Port: 5001 (크롤러 서버와 분리)
"""
import os
import sys
import sqlite3
import random
from datetime import datetime
import csv
import io
import pandas as pd

# [LOGGING FIX] 순서 중요: 로거 설정 모듈에서 함수만 먼저 가져옴
from logger_config import set_log_prefix

# 1. 로거 생성 전에 접두사 설정 (가장 먼저 실행)
set_log_prefix('log_START_DASHBOARD_')

# 2. 이후 setup_logger 임포트 및 로거 초기화
from logger_config import setup_logger
logger = setup_logger('dashboard')

logger.info("=" * 60)
logger.info("DASHBOARD APP INITIALIZATION START")
logger.info("=" * 60)

# 3. 나머지 모듈 임포트
try:
    logger.debug("Importing Flask modules...")
    from flask import Flask, render_template, request, jsonify, Response, g
    import json
    import time
    logger.debug("Flask modules imported successfully")

    logger.debug("Importing custom modules...")
    from modules.youtube_manager import YouTubeManager
    from modules.quota_tracker import QuotaTracker
    logger.debug("Custom modules imported successfully")
except ImportError as e:
    logger.error(f"Failed to import required modules: {e}")
    logger.exception("Import error details:")
    sys.exit(1)

# Flask 앱 초기화
logger.debug("Initializing Flask app...")
app = Flask(__name__)

# 템플릿 자동 재로드 설정 (개발 모드)
app.config['TEMPLATES_AUTO_RELOAD'] = True
app.jinja_env.auto_reload = True
logger.debug("Flask app configured")

# DB 경로
DB_PATH = 'output/db/youtube_data.db'
logger.info(f"Database path: {DB_PATH}")
logger.debug(f"Database exists: {os.path.exists(DB_PATH)}")

# 매니저 초기화
try:
    logger.debug("Initializing YouTubeManager...")
    youtube_manager = YouTubeManager(DB_PATH)
    logger.debug("YouTubeManager initialized successfully")

    logger.debug("Initializing QuotaTracker...")
    quota_tracker = QuotaTracker(DB_PATH)
    logger.debug("QuotaTracker initialized successfully")
except Exception as e:
    logger.error(f"Failed to initialize managers: {e}")
    logger.exception("Manager initialization error:")
    sys.exit(1)

logger.info("Dashboard app initialization completed successfully")
logger.info("=" * 60)


def get_db_connection():
    """DB 연결 생성"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


# ============================================================
# PLAN.md 2.1: Request Timing Middleware
# ============================================================

@app.before_request
def start_timer():
    """요청 시작 시간 기록"""
    g.start = time.time()

@app.after_request
def log_request(response):
    """요청 처리 시간 로깅"""
    if hasattr(g, 'start'):
        diff = time.time() - g.start
        # 1초 이상 걸리는 요청은 WARNING으로 로깅
        if diff > 1.0:
            logger.warning(f"SLOW REQUEST: {request.path} took {diff:.4f}s")
        # 그 외 API 요청은 DEBUG로 로깅
        elif request.path.startswith('/api/'):
            logger.debug(f"Request: {request.path} [{response.status_code}] took {diff:.4f}s")

    # PLAN.md Phase 3: 채널 관리 API에 Cache-Control 헤더 추가 (동기화 상태 갱신 버그 방지)
    if request.path.startswith('/api/channel_manager/'):
        response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
        response.headers['Pragma'] = 'no-cache'
        response.headers['Expires'] = '0'

    return response


# ============================================================
# 페이지 라우트
# ============================================================

@app.route('/')
def index():
    """대시보드 메인 페이지"""
    return render_template('db_dashboard.html')


# ============================================================
# API 엔드포인트
# ============================================================

@app.route('/api/stats')
def api_stats():
    """통합 통계 API"""
    logger.debug("GET /api/stats - Request received")
    try:
        logger.debug("Connecting to database...")
        conn = get_db_connection()
        cursor = conn.cursor()
        logger.debug("Database connection established")

        # 크롤링 데이터 통계
        logger.debug("Fetching crawl data statistics...")
        cursor.execute('SELECT COUNT(*) as count FROM shorts_rank')
        shorts_count = cursor.fetchone()['count']
        logger.debug(f"Shorts count: {shorts_count}")

        cursor.execute('SELECT COUNT(*) as count FROM videos_rank')
        videos_count = cursor.fetchone()['count']
        logger.debug(f"Videos count: {videos_count}")

        cursor.execute('SELECT COUNT(*) as count FROM channels_rank')
        channels_count = cursor.fetchone()['count']
        logger.debug(f"Channels count: {channels_count}")

        # API 데이터 통계
        logger.debug("Fetching API data statistics...")
        api_stats = youtube_manager.get_api_stats()
        logger.debug(f"API stats: {api_stats}")

        # Quota 현황
        logger.debug("Fetching quota status...")
        quota_status = quota_tracker.get_today_usage()
        logger.debug(f"Quota status: {quota_status}")

        conn.close()
        logger.debug("Database connection closed")

        response_data = {
            'status': 'success',
            'crawl_data': {
                'shorts': shorts_count,
                'videos': videos_count,
                'channels': channels_count
            },
            'api_data': {
                'channels': api_stats['channels'],
                'videos': api_stats['videos'],
                'shorts': api_stats['shorts']
            },
            'quota': quota_status
        }
        logger.info(f"GET /api/stats - Success (crawl: {shorts_count+videos_count+channels_count}, api: {api_stats['channels']+api_stats['videos']+api_stats['shorts']})")
        return jsonify(response_data)

    except Exception as e:
        logger.error(f"GET /api/stats - Error: {e}")
        logger.exception("Stats API exception details:")
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/api/search')
def api_search():
    """통합 검색 API (크롤링 + API 데이터)"""
    try:
        keyword = request.args.get('keyword', '')
        source = request.args.get('source', 'all')  # all, crawl, api
        data_type = request.args.get('type', 'all')  # all, shorts, video, channel
        limit = int(request.args.get('limit', 100))

        logger.info(f"GET /api/search - keyword='{keyword}', source={source}, type={data_type}, limit={limit}")

        results = []
        conn = get_db_connection()
        cursor = conn.cursor()
        logger.debug("Database connection established for search")

        # 크롤링 데이터 검색
        if source in ['all', 'crawl']:
            if data_type in ['all', 'shorts']:
                cursor.execute('''
                    SELECT 'shorts_rank' as source, video_id, title, channel_name,
                           views, rank, category, country, crawled_at
                    FROM shorts_rank
                    WHERE title LIKE ? OR channel_name LIKE ?
                    ORDER BY crawled_at DESC
                    LIMIT ?
                ''', (f'%{keyword}%', f'%{keyword}%', limit))
                results.extend([dict(row) for row in cursor.fetchall()])

            if data_type in ['all', 'video']:
                cursor.execute('''
                    SELECT 'videos_rank' as source, video_id, title, channel_name,
                           views, rank, category, country, crawled_at
                    FROM videos_rank
                    WHERE title LIKE ? OR channel_name LIKE ?
                    ORDER BY crawled_at DESC
                    LIMIT ?
                ''', (f'%{keyword}%', f'%{keyword}%', limit))
                results.extend([dict(row) for row in cursor.fetchall()])

            if data_type in ['all', 'channel']:
                cursor.execute('''
                    SELECT 'channels_rank' as source, channel_id, channel_name,
                           channel_url, subscriber_count as views, rank, category, country, crawled_at
                    FROM channels_rank
                    WHERE channel_name LIKE ?
                    ORDER BY crawled_at DESC
                    LIMIT ?
                ''', (f'%{keyword}%', limit))
                results.extend([dict(row) for row in cursor.fetchall()])

        # API 데이터 검색
        if source in ['all', 'api']:
            if data_type in ['all', 'channel']:
                cursor.execute('''
                    SELECT 'api_channels' as source, channel_id, title as channel_name,
                           subscriber_count, view_count, video_count, last_updated as crawled_at
                    FROM api_channels
                    WHERE title LIKE ?
                    ORDER BY last_updated DESC
                    LIMIT ?
                ''', (f'%{keyword}%', limit))
                results.extend([dict(row) for row in cursor.fetchall()])

            if data_type in ['all', 'shorts', 'video']:
                type_filter = ''
                if data_type == 'shorts':
                    type_filter = "AND video_type = 'shorts'"
                elif data_type == 'video':
                    type_filter = "AND video_type = 'video'"

                cursor.execute(f'''
                    SELECT 'api_videos' as source, video_id, title, channel_id,
                           view_count as views, video_type, duration_sec, last_updated as crawled_at
                    FROM api_videos
                    WHERE title LIKE ? {type_filter}
                    ORDER BY last_updated DESC
                    LIMIT ?
                ''', (f'%{keyword}%', limit))
                results.extend([dict(row) for row in cursor.fetchall()])

        conn.close()
        logger.debug(f"Search completed - {len(results)} results found")

        logger.info(f"GET /api/search - Success ({len(results)} results)")
        return jsonify({
            'status': 'success',
            'count': len(results),
            'results': results
        })

    except Exception as e:
        logger.error(f"GET /api/search - Error: {e}")
        logger.exception("Search API exception details:")
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/api/channels/crawled')
def api_crawled_channels():
    """크롤링된 채널 목록 (동기화 상태 포함)"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        # 크롤링 채널 + API 동기화 상태 LEFT JOIN
        cursor.execute('''
            SELECT
                cr.channel_id,
                cr.channel_name,
                cr.channel_url,
                cr.profile_url,
                cr.subscriber_count as score_1,
                cr.total_views as score_2,
                cr.category,
                cr.country,
                cr.ranking_type,
                cr.crawled_at,
                CASE WHEN ac.channel_id IS NOT NULL THEN 1 ELSE 0 END as is_synced,
                ac.subscriber_count as api_subscribers,
                ac.video_count as api_video_count,
                ac.uploads_playlist_id
            FROM channels_rank cr
            LEFT JOIN api_channels ac ON cr.channel_id = ac.channel_id
            GROUP BY cr.channel_id
            ORDER BY cr.crawled_at DESC
            LIMIT 200
        ''')

        channels = [dict(row) for row in cursor.fetchall()]
        conn.close()

        return jsonify({
            'status': 'success',
            'count': len(channels),
            'channels': channels
        })

    except Exception as e:
        logger.error(f"Crawled channels API error: {e}")
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/api/sync/channel', methods=['POST'])
def api_sync_channel():
    """채널 동기화 (Zero-Cost ID 추출 + API)"""
    try:
        data = request.json
        channel_url = data.get('channel_url')

        logger.info(f"POST /api/sync/channel - URL: {channel_url}")

        if not channel_url:
            logger.warning("POST /api/sync/channel - Missing channel_url parameter")
            return jsonify({'status': 'error', 'message': 'channel_url required'}), 400

        logger.debug(f"Starting channel sync for: {channel_url}")
        result = youtube_manager.sync_channel(channel_url)

        if result['success']:
            logger.info(f"POST /api/sync/channel - Success (channel_id: {result['channel_id']}, quota: {result['quota_used']})")
        else:
            logger.warning(f"POST /api/sync/channel - Failed: {result['error']}")

        return jsonify({
            'status': 'success' if result['success'] else 'error',
            'channel_id': result['channel_id'],
            'data': result['data'],
            'quota_used': result['quota_used'],
            'error': result['error']
        })

    except Exception as e:
        logger.error(f"POST /api/sync/channel - Error: {e}")
        logger.exception("Channel sync API exception details:")
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/api/sync/videos', methods=['POST'])
def api_sync_videos():
    """채널 영상 수집"""
    try:
        data = request.json
        channel_id = data.get('channel_id')
        limit = data.get('limit', 50)

        logger.info(f"POST /api/sync/videos - channel_id: {channel_id}, limit: {limit}")

        if not channel_id:
            logger.warning("POST /api/sync/videos - Missing channel_id parameter")
            return jsonify({'status': 'error', 'message': 'channel_id required'}), 400

        logger.debug(f"Starting video fetch for channel: {channel_id}")
        result = youtube_manager.fetch_videos(channel_id, limit)

        if result['success']:
            logger.info(f"POST /api/sync/videos - Success (shorts: {result['shorts_count']}, videos: {result['video_count']}, quota: {result['quota_used']})")
        else:
            logger.warning(f"POST /api/sync/videos - Failed: {result['error']}")

        return jsonify({
            'status': 'success' if result['success'] else 'error',
            'shorts_count': result['shorts_count'],
            'video_count': result['video_count'],
            'total': len(result['videos']),
            'quota_used': result['quota_used'],
            'error': result['error']
        })

    except Exception as e:
        logger.error(f"POST /api/sync/videos - Error: {e}")
        logger.exception("Videos sync API exception details:")
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/api/quota')
def api_quota():
    """Quota 현황 API"""
    try:
        today = quota_tracker.get_today_usage()
        history = quota_tracker.get_usage_history(7)

        return jsonify({
            'status': 'success',
            'today': today,
            'history': history,
            'color': quota_tracker.get_quota_status_color()
        })

    except Exception as e:
        logger.error(f"Quota API error: {e}")
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/api/channels/api')
def api_channels_list():
    """API로 수집된 채널 목록 (썸네일 포함)"""
    try:
        limit = int(request.args.get('limit', 50))
        offset = int(request.args.get('offset', 0))
        sort_by = request.args.get('sort_by', 'last_updated')
        sort_order = request.args.get('sort_order', 'DESC')

        # 정렬 컬럼 매핑 (보안: SQL 인젝션 방지)
        allowed_sort_columns = {
            'title': 'ac.title',
            'subscriber_count': 'ac.subscriber_count',
            'video_count': 'ac.video_count',
            'view_count': 'ac.view_count',
            'last_updated': 'ac.last_updated',
            'sync_status': 'ac.sync_status',
            'last_synced_at': 'ac.last_synced_at'
        }

        sort_column = allowed_sort_columns.get(sort_by, 'ac.last_updated')
        sort_order = 'ASC' if sort_order.upper() == 'ASC' else 'DESC'

        conn = get_db_connection()
        cursor = conn.cursor()

        query = f'''
            SELECT
                ac.channel_id,
                ac.title,
                ac.thumbnail_url,
                ac.subscriber_count,
                ac.view_count,
                ac.video_count,
                ac.uploads_playlist_id,
                ac.last_updated,
                ac.sync_status,
                ac.last_synced_at,
                ac.collected_video_count,
                CASE WHEN ac.sync_status = 'synced' THEN 1 ELSE 0 END as is_synced,
                cr.category,
                cr.ranking_type,
                cr.subscriber_count as score_1,
                cr.country,
                (SELECT COUNT(*) FROM api_videos av WHERE av.channel_id = ac.channel_id AND av.duration_sec <= 60) as shorts_count,
                (SELECT COUNT(*) FROM api_videos av WHERE av.channel_id = ac.channel_id AND (av.duration_sec > 60 OR av.duration_sec IS NULL)) as longform_count
            FROM api_channels ac
            LEFT JOIN channels_rank cr ON ac.channel_id = cr.channel_id
            ORDER BY {sort_column} {sort_order}
            LIMIT ? OFFSET ?
        '''

        cursor.execute(query, (limit, offset))

        channels = [dict(row) for row in cursor.fetchall()]

        # 총 개수 조회
        cursor.execute('SELECT COUNT(*) as count FROM api_channels')
        total = cursor.fetchone()['count']

        conn.close()

        return jsonify({
            'status': 'success',
            'count': len(channels),
            'total': total,
            'channels': channels
        })

    except Exception as e:
        logger.error(f"API Channels list error: {e}")
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/api/channels/synced_list')
def api_channels_synced_list():
    """
    추출 완료된 채널 목록 반환 (PLAN.md Phase 2)
    PLAN.md Phase 2: '동기화' → '추출 완료' 개념 재정의
    channel_id가 유효하거나 last_synced_at이 존재하면 추출 완료로 간주
    """
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        # PLAN.md Phase 2: 추출 완료 로직 적용
        cursor.execute('''
            SELECT
                ac.channel_id,
                ac.title,
                ac.thumbnail_url,
                ac.subscriber_count,
                ac.video_count,
                ac.sync_status,
                ac.last_synced_at,
                COUNT(av.video_id) as collected_videos
            FROM api_channels ac
            LEFT JOIN api_videos av ON ac.channel_id = av.channel_id
            WHERE (ac.channel_id IS NOT NULL AND ac.channel_id != 'N/A')
                  OR ac.last_synced_at IS NOT NULL
            GROUP BY ac.channel_id
            ORDER BY ac.last_synced_at DESC
            LIMIT 50
        ''')

        channels = [dict(row) for row in cursor.fetchall()]
        conn.close()

        logger.info(f"GET /api/channels/synced_list - Success ({len(channels)} synced channels)")

        return jsonify({
            'status': 'success',
            'count': len(channels),
            'channels': channels
        })

    except Exception as e:
        logger.error(f"API Synced Channels list error: {e}")
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/api/videos/list')
def api_videos_list():
    """API로 수집된 영상 목록 (썸네일 포함) - PLAN.md: 필터링 지원"""
    try:
        limit = int(request.args.get('limit', 50))
        offset = int(request.args.get('offset', 0))
        video_type = request.args.get('type', 'all')  # all, shorts, video
        channel_id = request.args.get('channel_id', '')

        # PLAN.md - 검색/필터 파라미터
        search = request.args.get('search', '')
        view_min = request.args.get('view_min', '')
        view_max = request.args.get('view_max', '')
        like_ratio_min = request.args.get('like_ratio_min', '')
        like_ratio_max = request.args.get('like_ratio_max', '')
        date_from = request.args.get('date_from', '')
        date_to = request.args.get('date_to', '')
        category = request.args.get('category', '')
        sort_by = request.args.get('sort_by', 'published_desc')

        conn = get_db_connection()
        cursor = conn.cursor()

        # PLAN.md Phase 3: Deep Data 컬럼 전체 포함
        query = '''
            SELECT
                av.video_id,
                av.channel_id,
                av.title,
                av.view_count,
                av.like_count,
                av.duration_sec,
                av.video_type,
                av.published_at,
                av.last_updated,
                av.channel_name,
                av.category_name,
                av.like_view_ratio,
                av.daily_avg_views,
                av.tags,
                ac.thumbnail_url as channel_thumbnail,
                ac.subscriber_count as channel_subscriber_count,
                CAST(JULIANDAY('now') - JULIANDAY(av.published_at) AS INTEGER) as days_since_upload,
                CASE
                    WHEN ac.subscriber_count > 0 THEN ROUND(CAST(av.view_count AS REAL) / ac.subscriber_count, 2)
                    ELSE 0
                END as view_sub_ratio
            FROM api_videos av
            LEFT JOIN api_channels ac ON av.channel_id = ac.channel_id
            WHERE 1=1
        '''
        params = []

        # 타입 필터
        if video_type == 'shorts':
            query += " AND av.video_type = 'shorts'"
        elif video_type == 'video':
            query += " AND av.video_type = 'video'"

        # 채널 필터
        if channel_id:
            query += " AND av.channel_id = ?"
            params.append(channel_id)

        # PLAN.md - 검색 필터 (제목, 채널명, 태그)
        if search:
            query += " AND (av.title LIKE ? OR av.channel_name LIKE ? OR av.tags LIKE ?)"
            search_param = f'%{search}%'
            params.extend([search_param, search_param, search_param])

        # PLAN.md - 조회수 범위
        if view_min:
            query += " AND av.view_count >= ?"
            params.append(int(view_min))
        if view_max:
            query += " AND av.view_count <= ?"
            params.append(int(view_max))

        # PLAN.md - 좋아요 비율 범위
        if like_ratio_min:
            query += " AND av.like_view_ratio >= ?"
            params.append(float(like_ratio_min))
        if like_ratio_max:
            query += " AND av.like_view_ratio <= ?"
            params.append(float(like_ratio_max))

        # PLAN.md - 게시일 범위
        if date_from:
            query += " AND av.published_at >= ?"
            params.append(f"{date_from}T00:00:00Z")
        if date_to:
            query += " AND av.published_at <= ?"
            params.append(f"{date_to}T23:59:59Z")

        # PLAN.md - 카테고리 필터
        if category:
            query += " AND av.category_name = ?"
            params.append(category)

        # PLAN.md - 정렬
        # 테이블 컬럼 정렬을 위한 sort_order 파라미터 지원
        sort_order_param = request.args.get('sort_order', '').upper()

        sort_map = {
            'published_desc': 'av.published_at DESC',
            'published_asc': 'av.published_at ASC',
            'view_desc': 'av.view_count DESC',
            'view_asc': 'av.view_count ASC',
            'like_ratio_desc': 'av.like_view_ratio DESC',
            'like_ratio_asc': 'av.like_view_ratio ASC',
            'daily_view_desc': 'av.daily_avg_views DESC',
            # 컬럼별 정렬 추가 (테이블 헤더 클릭용)
            'title': f'av.title {sort_order_param if sort_order_param in ["ASC", "DESC"] else "ASC"}',
            'channel_name': f'av.channel_name {sort_order_param if sort_order_param in ["ASC", "DESC"] else "ASC"}',
            'view_count': f'av.view_count {sort_order_param if sort_order_param in ["ASC", "DESC"] else "DESC"}',
            'like_count': f'av.like_count {sort_order_param if sort_order_param in ["ASC", "DESC"] else "DESC"}',
            'published_at': f'av.published_at {sort_order_param if sort_order_param in ["ASC", "DESC"] else "DESC"}',
            'video_type': f'av.video_type {sort_order_param if sort_order_param in ["ASC", "DESC"] else "ASC"}'
        }
        order_clause = sort_map.get(sort_by, 'av.published_at DESC')
        query += f" ORDER BY {order_clause} LIMIT ? OFFSET ?"
        params.extend([limit, offset])

        cursor.execute(query, params)
        videos = [dict(row) for row in cursor.fetchall()]

        # 총 개수 조회 - 동일한 필터 적용
        count_query = "SELECT COUNT(*) as count FROM api_videos av WHERE 1=1"
        count_params = []

        if video_type == 'shorts':
            count_query += " AND av.video_type = 'shorts'"
        elif video_type == 'video':
            count_query += " AND av.video_type = 'video'"
        if channel_id:
            count_query += " AND av.channel_id = ?"
            count_params.append(channel_id)
        if search:
            count_query += " AND (av.title LIKE ? OR av.channel_name LIKE ? OR av.tags LIKE ?)"
            count_params.extend([search_param, search_param, search_param])
        if view_min:
            count_query += " AND av.view_count >= ?"
            count_params.append(int(view_min))
        if view_max:
            count_query += " AND av.view_count <= ?"
            count_params.append(int(view_max))
        if like_ratio_min:
            count_query += " AND av.like_view_ratio >= ?"
            count_params.append(float(like_ratio_min))
        if like_ratio_max:
            count_query += " AND av.like_view_ratio <= ?"
            count_params.append(float(like_ratio_max))
        if date_from:
            count_query += " AND av.published_at >= ?"
            count_params.append(f"{date_from}T00:00:00Z")
        if date_to:
            count_query += " AND av.published_at <= ?"
            count_params.append(f"{date_to}T23:59:59Z")
        if category:
            count_query += " AND av.category_name = ?"
            count_params.append(category)

        cursor.execute(count_query, count_params)
        total = cursor.fetchone()['count']

        conn.close()

        return jsonify({
            'status': 'success',
            'count': len(videos),
            'total': total,
            'videos': videos
        })

    except Exception as e:
        logger.error(f"Videos list error: {e}")
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/api/channel/<channel_id>')
def api_channel_detail(channel_id):
    """채널 상세 정보 (API 데이터)"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        # 채널 정보
        cursor.execute('''
            SELECT
                ac.channel_id,
                ac.title,
                ac.thumbnail_url,
                ac.subscriber_count,
                ac.view_count,
                ac.video_count,
                ac.uploads_playlist_id,
                ac.last_updated,
                cr.category,
                cr.ranking_type,
                cr.subscriber_count as score_1,
                cr.total_views as score_2,
                cr.country
            FROM api_channels ac
            LEFT JOIN channels_rank cr ON ac.channel_id = cr.channel_id
            WHERE ac.channel_id = ?
        ''', (channel_id,))

        channel = cursor.fetchone()
        if not channel:
            return jsonify({'status': 'error', 'message': 'Channel not found'}), 404

        channel_data = dict(channel)

        # 채널의 영상 통계
        cursor.execute('''
            SELECT
                video_type,
                COUNT(*) as count,
                SUM(view_count) as total_views,
                AVG(view_count) as avg_views
            FROM api_videos
            WHERE channel_id = ?
            GROUP BY video_type
        ''', (channel_id,))

        video_stats = {}
        for row in cursor.fetchall():
            video_stats[row['video_type']] = {
                'count': row['count'],
                'total_views': row['total_views'] or 0,
                'avg_views': int(row['avg_views'] or 0)
            }

        # 최근 영상 5개 (api_videos에는 thumbnail_url 컬럼 없음)
        cursor.execute('''
            SELECT video_id, title, view_count, video_type, published_at
            FROM api_videos
            WHERE channel_id = ?
            ORDER BY published_at DESC
            LIMIT 5
        ''', (channel_id,))

        recent_videos = [dict(row) for row in cursor.fetchall()]

        conn.close()

        return jsonify({
            'status': 'success',
            'channel': channel_data,
            'video_stats': video_stats,
            'recent_videos': recent_videos
        })

    except Exception as e:
        logger.error(f"Channel detail error: {e}")
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/api/quota/estimate', methods=['POST'])
def api_quota_estimate():
    """작업 비용 예측 API"""
    try:
        data = request.json
        operations = data.get('operations', {})

        estimate = quota_tracker.estimate_cost(operations)

        return jsonify({
            'status': 'success',
            'estimate': estimate
        })

    except Exception as e:
        logger.error(f"Quota estimate API error: {e}")
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/api/collection_status')
def api_collection_status():
    """[PLAN Phase 3.3] 수집 현황 API - 최근 3일(일간) 또는 2주(주간) 데이터 반환"""
    try:
        from datetime import datetime, timedelta

        conn = get_db_connection()
        cursor = conn.cursor()

        # [PLAN Phase 3.3] 파라미터 받기 (base_date -> date로 통일)
        base_date_str = request.args.get('base_date') or request.args.get('date')
        period_type = request.args.get('period_type', 'daily')

        # [PLAN Phase 3.3] base_date가 없으면 오늘 날짜 사용
        if not base_date_str:
            base_date = datetime.now()
        else:
            try:
                base_date = datetime.strptime(base_date_str, '%Y-%m-%d')
            except ValueError:
                # 유효하지 않은 날짜 포맷 시 오늘 날짜 사용
                base_date = datetime.now()

        logger.info(f"[Collection Status] Request - Base Date: {base_date.strftime('%Y-%m-%d')}, Type: {period_type}")

        # [PLAN Phase 3.3] Period 한글/영문 매핑
        period_map = {
            'daily': '일간',
            'weekly': '주간',
            'monthly': '월간'
        }
        db_period_type = period_map.get(period_type, period_type)

        # [PLAN Phase 1.1] 날짜 리스트 생성
        dates_to_check = []

        if period_type == 'daily':
            # 오늘, 어제, 그제 (3일치)
            dates_to_check = [
                (base_date - timedelta(days=i)).strftime('%Y-%m-%d')
                for i in range(3)
            ]
            # [PLAN Phase 3.1] 디버그 로그 강화 (실제 조회 날짜)
            logger.debug(f"[Collection Status] Daily mode - Dates to check: {dates_to_check}")
        elif period_type == 'weekly':
            # 이번주, 지난주 (2주치)
            current_weekday = base_date.weekday()  # 0=월요일
            this_week_monday = base_date - timedelta(days=current_weekday)
            this_week_sunday = this_week_monday + timedelta(days=6)
            last_week_monday = this_week_monday - timedelta(days=7)
            last_week_sunday = last_week_monday + timedelta(days=6)

            dates_to_check = [
                (this_week_monday.strftime('%Y-%m-%d'), this_week_sunday.strftime('%Y-%m-%d'), '이번 주'),
                (last_week_monday.strftime('%Y-%m-%d'), last_week_sunday.strftime('%Y-%m-%d'), '지난 주')
            ]
            # [PLAN Phase 3.1] 디버그 로그 강화 (실제 조회 날짜 범위)
            logger.debug(f"[Collection Status] Weekly mode - Date ranges to check: {dates_to_check}")

        # [PLAN Phase 1.1] 각 날짜별 데이터 수집
        results = {}

        if period_type == 'daily':
            # 일간: 각 날짜별로 데이터 조회
            for date_str in dates_to_check:
                date_results = {}

                # [PLAN Phase 3.3] WHERE 조건 - db_period_type 사용
                where_clauses = ["DATE(crawled_at) = ?", "(period = ? OR period = ?)"]
                params = [date_str, period_type, db_period_type]
                date_condition = " WHERE " + " AND ".join(where_clauses)

                # 쇼츠
                cursor.execute(f'''
                    SELECT category, country, COUNT(*) as count, MAX(crawled_at) as last_crawled
                    FROM shorts_rank
                    {date_condition}
                    GROUP BY category, country
                    ORDER BY count DESC
                ''', params)
                date_results['shorts'] = [dict(row) for row in cursor.fetchall()]

                # 비디오
                cursor.execute(f'''
                    SELECT category, country, COUNT(*) as count, MAX(crawled_at) as last_crawled
                    FROM videos_rank
                    {date_condition}
                    GROUP BY category, country
                    ORDER BY count DESC
                ''', params)
                date_results['videos'] = [dict(row) for row in cursor.fetchall()]

                # 채널
                cursor.execute(f'''
                    SELECT category, country, ranking_type, COUNT(*) as count, MAX(crawled_at) as last_crawled
                    FROM channels_rank
                    {date_condition}
                    GROUP BY category, country, ranking_type
                    ORDER BY count DESC
                ''', params)
                date_results['channels'] = [dict(row) for row in cursor.fetchall()]

                results[date_str] = date_results

        elif period_type == 'weekly':
            # 주간: 각 주별로 데이터 조회
            for week_data in dates_to_check:
                start_date, end_date, week_label = week_data
                week_results = {}

                # [PLAN Phase 3.3] WHERE 조건 - db_period_type 사용
                where_clauses = ["DATE(crawled_at) BETWEEN ? AND ?", "(period = ? OR period = ?)"]
                params = [start_date, end_date, period_type, db_period_type]
                date_condition = " WHERE " + " AND ".join(where_clauses)

                # 쇼츠
                cursor.execute(f'''
                    SELECT category, country, COUNT(*) as count, MAX(crawled_at) as last_crawled
                    FROM shorts_rank
                    {date_condition}
                    GROUP BY category, country
                    ORDER BY count DESC
                ''', params)
                week_results['shorts'] = [dict(row) for row in cursor.fetchall()]

                # 비디오
                cursor.execute(f'''
                    SELECT category, country, COUNT(*) as count, MAX(crawled_at) as last_crawled
                    FROM videos_rank
                    {date_condition}
                    GROUP BY category, country
                    ORDER BY count DESC
                ''', params)
                week_results['videos'] = [dict(row) for row in cursor.fetchall()]

                # 채널
                cursor.execute(f'''
                    SELECT category, country, ranking_type, COUNT(*) as count, MAX(crawled_at) as last_crawled
                    FROM channels_rank
                    {date_condition}
                    GROUP BY category, country, ranking_type
                    ORDER BY count DESC
                ''', params)
                week_results['channels'] = [dict(row) for row in cursor.fetchall()]

                results[week_label] = week_results

        # API 동기화 현황
        cursor.execute('''
            SELECT
                COUNT(DISTINCT ac.channel_id) as synced_channels,
                (SELECT COUNT(DISTINCT channel_id) FROM channels_rank) as total_channels,
                (SELECT COUNT(*) FROM api_videos WHERE video_type = 'shorts') as api_shorts,
                (SELECT COUNT(*) FROM api_videos WHERE video_type = 'video') as api_videos
            FROM api_channels ac
        ''')
        sync_status = dict(cursor.fetchone())

        # 고유 카테고리 목록
        cursor.execute('''
            SELECT DISTINCT category FROM (
                SELECT category FROM shorts_rank
                UNION
                SELECT category FROM videos_rank
                UNION
                SELECT category FROM channels_rank
            ) ORDER BY category
        ''')
        categories = [row['category'] for row in cursor.fetchall() if row['category']]

        # 고유 국가 목록
        cursor.execute('''
            SELECT DISTINCT country FROM (
                SELECT country FROM shorts_rank
                UNION
                SELECT country FROM videos_rank
                UNION
                SELECT country FROM channels_rank
            ) ORDER BY country
        ''')
        countries = [row['country'] for row in cursor.fetchall() if row['country']]

        conn.close()

        # [PLAN Phase 1.1] 결과 로그
        logger.info(f"[Collection Status] Result - Dates: {list(results.keys())}, Period Type: {period_type}")
        for date_key, date_data in results.items():
            logger.debug(f"[Collection Status] {date_key} - Shorts: {len(date_data.get('shorts', []))}, Videos: {len(date_data.get('videos', []))}, Channels: {len(date_data.get('channels', []))}")

        # [PLAN Phase 1.1] 새로운 응답 형식 반환
        return jsonify({
            'status': 'success',
            'period_type': period_type,
            'data': results,  # 날짜별 데이터
            'sync_status': sync_status,
            'categories': categories,
            'countries': countries
        })

    except Exception as e:
        logger.error(f"Collection status API error: {e}")
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/api/crawl_history')
def api_crawl_history():
    """크롤링 히스토리 API"""
    try:
        limit = int(request.args.get('limit', 20))
        conn = get_db_connection()
        cursor = conn.cursor()

        # crawl_history 테이블이 있는지 확인
        cursor.execute("""
            SELECT name FROM sqlite_master
            WHERE type='table' AND name='crawl_history'
        """)

        if cursor.fetchone():
            # crawl_history 테이블이 있으면 사용
            # 테이블 컬럼명: id, target_type, category, country, period, item_count, success, error_message, crawled_at
            cursor.execute('''
                SELECT id, target_type as crawl_type, category, country, period as ranking_type,
                       item_count as items_count,
                       CASE WHEN success = 1 THEN 'completed' ELSE 'failed' END as status,
                       crawled_at, error_message
                FROM crawl_history
                ORDER BY crawled_at DESC
                LIMIT ?
            ''', (limit,))
            history = [dict(row) for row in cursor.fetchall()]
        else:
            # 테이블이 없으면 크롤링 데이터에서 최근 기록 추출
            history = []

            # shorts_rank에서 최근 크롤링 기록 추출
            cursor.execute('''
                SELECT 'shorts' as crawl_type, category, country, 'daily' as ranking_type,
                       COUNT(*) as items_count, 'completed' as status,
                       MAX(crawled_at) as crawled_at, NULL as error_message
                FROM shorts_rank
                GROUP BY DATE(crawled_at), category, country
                ORDER BY crawled_at DESC
                LIMIT ?
            ''', (limit // 3,))
            history.extend([dict(row) for row in cursor.fetchall()])

            # videos_rank에서 최근 크롤링 기록 추출
            cursor.execute('''
                SELECT 'videos' as crawl_type, category, country, 'daily' as ranking_type,
                       COUNT(*) as items_count, 'completed' as status,
                       MAX(crawled_at) as crawled_at, NULL as error_message
                FROM videos_rank
                GROUP BY DATE(crawled_at), category, country
                ORDER BY crawled_at DESC
                LIMIT ?
            ''', (limit // 3,))
            history.extend([dict(row) for row in cursor.fetchall()])

            # channels_rank에서 최근 크롤링 기록 추출
            cursor.execute('''
                SELECT 'channels' as crawl_type, category, country, ranking_type,
                       COUNT(*) as items_count, 'completed' as status,
                       MAX(crawled_at) as crawled_at, NULL as error_message
                FROM channels_rank
                GROUP BY DATE(crawled_at), category, country, ranking_type
                ORDER BY crawled_at DESC
                LIMIT ?
            ''', (limit // 3,))
            history.extend([dict(row) for row in cursor.fetchall()])

            # 날짜순 정렬
            history.sort(key=lambda x: x['crawled_at'] or '', reverse=True)
            history = history[:limit]

        conn.close()

        return jsonify({
            'status': 'success',
            'count': len(history),
            'history': history
        })

    except Exception as e:
        logger.error(f"Crawl history API error: {e}")
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/api/crawl_data')
def api_crawl_data():
    """크롤링 데이터 조회 API - 고급 필터 및 정렬 지원"""
    try:
        data_type = request.args.get('type', 'shorts')  # shorts, videos, channels, all
        category = request.args.get('category', '')
        country = request.args.get('country', '')
        period = request.args.get('period', '')  # 일간, 주간, 월간
        keyword = request.args.get('keyword', '')
        limit = int(request.args.get('limit', 100))
        offset = int(request.args.get('offset', 0))

        # [PLAN Phase 4.1] Request Parameter Logging
        logger.info(f"API Request: /api/crawl_data - Type: {data_type}, Category: '{category}', Keyword: '{keyword}', Limit: {limit}, Offset: {offset}")

        # [Fix] Frontend sends English categories, but DB stores Korean. Map them.
        CATEGORY_MAP = {
            'Music': '음악',
            'Entertainment': '엔터테인먼트',
            'Gaming': '게임',
            'Science & Technology': '과학기술',
            'People & Blogs': '인물/블로그',
            'News & Politics': '뉴스/정치',
            'Sports': '스포츠',
            'Education': '교육',
            'Pets & Animals': '동물',
            'Film & Animation': '영화/애니메이션',
            'Comedy': '코메디',
            'Howto & Style': '노하우/스타일',
            'Travel & Events': '여행/이벤트',
            'Autos & Vehicles': '자동차/교통',
            'Nonprofits & Activism': '비영리/사회운동',
            # Add reverse mapping just in case
            '음악': '음악',
            '엔터테인먼트': '엔터테인먼트',
            '게임': '게임'
        }
        
        # Translate category if present in map
        if category in CATEGORY_MAP:
            original_category = category
            category = CATEGORY_MAP[category]
            logger.info(f"Category mapped: '{original_category}' -> '{category}'")

        # 정렬 옵션
        sort_by = request.args.get('sort_by', 'crawled_at')  # 정렬 기준
        sort_order = request.args.get('sort_order', 'desc')  # asc, desc

        # 날짜 필터
        crawl_date = request.args.get('crawl_date', '')  # 특정 수집일 (YYYY-MM-DD)
        crawl_date_from = request.args.get('crawl_date_from', '')  # 수집일 시작
        crawl_date_to = request.args.get('crawl_date_to', '')  # 수집일 끝
        upload_date_from = request.args.get('upload_date_from', '')  # 업로드일 시작
        upload_date_to = request.args.get('upload_date_to', '')  # 업로드일 끝

        # 기간 프리셋 (수집일 기준)
        crawl_period = request.args.get('crawl_period', '')  # 1d, 3d, 7d, 14d, 1m, 3m, 6m, 1y
        upload_period = request.args.get('upload_period', '')  # 업로드 기간 프리셋

        conn = get_db_connection()
        cursor = conn.cursor()

        # 전체 데이터 조회 (all) - 쇼츠+비디오 통합
        if data_type == 'all':
            query = '''
                SELECT 'shorts' as data_type, sr.id, sr.video_id, sr.title, sr.thumbnail_url,
                       sr.channel_name, sr.channel_id, sr.views, sr.likes, sr.rank, sr.rank_change,
                       sr.upload_date, sr.subscriber_count, sr.tags, sr.category, sr.country, sr.period,
                       sr.crawled_at, sr.updated_at,
                       CASE WHEN av.video_id IS NOT NULL THEN 1 ELSE 0 END as has_api_data,
                       ac.thumbnail_url as channel_profile_url
                FROM shorts_rank sr
                LEFT JOIN api_videos av ON sr.video_id = av.video_id
                LEFT JOIN api_channels ac ON sr.channel_name = ac.title
                WHERE 1=1
            '''
        elif data_type == 'shorts':
            query = '''
                SELECT sr.id, sr.video_id, sr.title, sr.thumbnail_url, sr.channel_name, sr.channel_id,
                       sr.views, sr.likes, sr.rank, sr.rank_change, sr.upload_date, sr.subscriber_count,
                       sr.tags, sr.category, sr.country, sr.period, sr.crawled_at, sr.updated_at,
                       CASE WHEN av.video_id IS NOT NULL THEN 1 ELSE 0 END as has_api_data,
                       ac.thumbnail_url as channel_profile_url
                FROM shorts_rank sr
                LEFT JOIN api_videos av ON sr.video_id = av.video_id
                LEFT JOIN api_channels ac ON sr.channel_name = ac.title
                WHERE 1=1
            '''
        elif data_type == 'videos':
            query = '''
                SELECT vr.id, vr.video_id, vr.title, vr.thumbnail_url, vr.channel_name, vr.channel_id,
                       vr.views, vr.likes, vr.rank, vr.rank_change, vr.upload_date, vr.subscriber_count,
                       vr.tags, vr.category, vr.country, vr.period, vr.crawled_at, vr.updated_at,
                       CASE WHEN av.video_id IS NOT NULL THEN 1 ELSE 0 END as has_api_data,
                       ac.thumbnail_url as channel_profile_url
                FROM videos_rank vr
                LEFT JOIN api_videos av ON vr.video_id = av.video_id
                LEFT JOIN api_channels ac ON vr.channel_name = ac.title
                WHERE 1=1
            '''
        else:  # channels
            query = '''
                SELECT cr.id, cr.channel_id, cr.channel_name, cr.channel_url, cr.profile_url,
                       cr.rank, cr.rank_change, cr.subscriber_count, cr.total_views,
                       cr.tags, cr.category, cr.country, cr.period, cr.ranking_type,
                       cr.crawled_at, cr.updated_at,
                       CASE WHEN ac.channel_id IS NOT NULL THEN 1 ELSE 0 END as has_api_data,
                       ac.subscriber_count as api_subscribers, ac.video_count as api_video_count
                FROM channels_rank cr
                LEFT JOIN api_channels ac ON cr.channel_id = ac.channel_id
                WHERE 1=1
            '''

        params = []

        # 카테고리 필터
        if category:
            query += " AND category = ?"
            params.append(category)

        # 국가 필터
        if country:
            query += " AND country = ?"
            params.append(country)

        # 기간 필터 (일간/주간/월간)
        if period:
            query += " AND period = ?"
            params.append(period)

        # 키워드 검색
        if keyword:
            if data_type == 'channels':
                query += " AND channel_name LIKE ?"
                params.append(f'%{keyword}%')
            else:
                query += " AND (title LIKE ? OR channel_name LIKE ? OR tags LIKE ?)"
                params.extend([f'%{keyword}%', f'%{keyword}%', f'%{keyword}%'])

        # 수집일 필터
        if crawl_date:
            query += " AND DATE(crawled_at) = ?"
            params.append(crawl_date)
        else:
            # 수집일 기간 프리셋
            if crawl_period:
                period_days = {'1d': 1, '3d': 3, '7d': 7, '14d': 14, '1m': 30, '3m': 90, '6m': 180, '1y': 365}
                if crawl_period in period_days:
                    query += f" AND crawled_at >= datetime('now', '-{period_days[crawl_period]} days')"
            elif crawl_date_from or crawl_date_to:
                if crawl_date_from:
                    query += " AND DATE(crawled_at) >= ?"
                    params.append(crawl_date_from)
                if crawl_date_to:
                    query += " AND DATE(crawled_at) <= ?"
                    params.append(crawl_date_to)

        # 업로드일 필터 (영상/쇼츠만)
        if data_type != 'channels':
            if upload_period:
                period_days = {'1d': 1, '3d': 3, '7d': 7, '14d': 14, '1m': 30, '3m': 90, '6m': 180, '1y': 365}
                if upload_period in period_days:
                    query += f" AND upload_date >= date('now', '-{period_days[upload_period]} days')"
            elif upload_date_from or upload_date_to:
                if upload_date_from:
                    query += " AND upload_date >= ?"
                    params.append(upload_date_from)
                if upload_date_to:
                    query += " AND upload_date <= ?"
                    params.append(upload_date_to)

        # 전체 데이터일 때 videos_rank도 UNION
        if data_type == 'all':
            query += '''
                UNION ALL
                SELECT 'videos' as data_type, vr.id, vr.video_id, vr.title, vr.thumbnail_url,
                       vr.channel_name, vr.channel_id, vr.views, vr.likes, vr.rank, vr.rank_change,
                       vr.upload_date, vr.subscriber_count, vr.tags, vr.category, vr.country, vr.period,
                       vr.crawled_at, vr.updated_at,
                       CASE WHEN av.video_id IS NOT NULL THEN 1 ELSE 0 END as has_api_data,
                       ac.thumbnail_url as channel_profile_url
                FROM videos_rank vr
                LEFT JOIN api_videos av ON vr.video_id = av.video_id
                LEFT JOIN api_channels ac ON vr.channel_name = ac.title
                WHERE 1=1
            '''
            # 동일한 필터 적용
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
                query += " AND (title LIKE ? OR channel_name LIKE ? OR tags LIKE ?)"
                params.extend([f'%{keyword}%', f'%{keyword}%', f'%{keyword}%'])
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

        # 정렬
        valid_sort_columns = ['id', 'views', 'rank', 'crawled_at', 'upload_date', 'channel_name', 'title',
                              'subscriber_count', 'total_views', 'video_count', 'likes']
        if sort_by in valid_sort_columns:
            sort_dir = 'ASC' if sort_order.lower() == 'asc' else 'DESC'
            query += f" ORDER BY {sort_by} {sort_dir}"
            
            # [Modified] 수집일 기준 정렬 시 순위(rank)를 2차 정렬 기준으로 추가 (최신 날짜 + 높은 순위 우선)
            if sort_by == 'crawled_at':
                query += ", rank ASC"
        else:
            query += " ORDER BY crawled_at DESC, rank ASC"

        query += " LIMIT ? OFFSET ?"
        params.extend([limit, offset])

        # [Debug] Log final query and params
        # logger.debug(f"Executing Query: {query}")
        logger.info(f"Query Params: {params}")

        cursor.execute(query, params)
        results = [dict(row) for row in cursor.fetchall()]

        # PLAN.md Phase 2.1: 스키마 검증 - 첫 번째 row의 컬럼명 로깅
        if results:
            fetched_columns = list(results[0].keys())
            logger.debug(f"[PLAN Phase 2.1] Fetched Columns ({data_type}): {fetched_columns}")
        else:
            logger.debug(f"[PLAN Phase 2.1] No data returned for type: {data_type}")

        # 총 개수 조회 (필터 적용)
        count_params = []
        if data_type == 'all':
            count_query = "SELECT (SELECT COUNT(*) FROM shorts_rank WHERE 1=1"
        elif data_type == 'shorts':
            count_query = "SELECT COUNT(*) as count FROM shorts_rank WHERE 1=1"
        elif data_type == 'videos':
            count_query = "SELECT COUNT(*) as count FROM videos_rank WHERE 1=1"
        else:
            count_query = "SELECT COUNT(*) as count FROM channels_rank WHERE 1=1"

        if category:
            count_query += " AND category = ?"
            count_params.append(category)
        if country:
            count_query += " AND country = ?"
            count_params.append(country)
        if period:
            count_query += " AND period = ?"
            count_params.append(period)
        if keyword:
            if data_type == 'channels':
                count_query += " AND channel_name LIKE ?"
                count_params.append(f'%{keyword}%')
            else:
                count_query += " AND (title LIKE ? OR channel_name LIKE ?)"
                count_params.extend([f'%{keyword}%', f'%{keyword}%'])
        if crawl_date:
            count_query += " AND DATE(crawled_at) = ?"
            count_params.append(crawl_date)
        elif crawl_period:
            period_days = {'1d': 1, '3d': 3, '7d': 7, '14d': 14, '1m': 30, '3m': 90, '6m': 180, '1y': 365}
            if crawl_period in period_days:
                count_query += f" AND crawled_at >= datetime('now', '-{period_days[crawl_period]} days')"
        elif crawl_date_from:
            count_query += " AND DATE(crawled_at) >= ?"
            count_params.append(crawl_date_from)
        if crawl_date_to and not crawl_date:
            count_query += " AND DATE(crawled_at) <= ?"
            count_params.append(crawl_date_to)

        if data_type == 'all':
            # UNION 카운트
            count_query += ") + (SELECT COUNT(*) FROM videos_rank WHERE 1=1"
            if category:
                count_query += " AND category = ?"
                count_params.append(category)
            if country:
                count_query += " AND country = ?"
                count_params.append(country)
            if period:
                count_query += " AND period = ?"
                count_params.append(period)
            if keyword:
                count_query += " AND (title LIKE ? OR channel_name LIKE ?)"
                count_params.extend([f'%{keyword}%', f'%{keyword}%'])
            if crawl_date:
                count_query += " AND DATE(crawled_at) = ?"
                count_params.append(crawl_date)
            elif crawl_period:
                period_days = {'1d': 1, '3d': 3, '7d': 7, '14d': 14, '1m': 30, '3m': 90, '6m': 180, '1y': 365}
                if crawl_period in period_days:
                    count_query += f" AND crawled_at >= datetime('now', '-{period_days[crawl_period]} days')"
            elif crawl_date_from:
                count_query += " AND DATE(crawled_at) >= ?"
                count_params.append(crawl_date_from)
            if crawl_date_to and not crawl_date:
                count_query += " AND DATE(crawled_at) <= ?"
                count_params.append(crawl_date_to)
            count_query += ") as count"


        # [Fix] Double Fetch 오류 수정: fetchone() 결과를 변수에 할당
        cursor.execute(count_query, count_params)
        result_row = cursor.fetchone()
        total = result_row['count'] if result_row else 0
        
        # 전체 데이터 개수 (필터링 전) 조회 - UI 표시용
        total_overall = 0
        try:
            if data_type == 'all':
                cursor.execute("SELECT (SELECT COUNT(*) FROM shorts_rank) + (SELECT COUNT(*) FROM videos_rank)")
            elif data_type == 'shorts':
                cursor.execute("SELECT COUNT(*) FROM shorts_rank")
            elif data_type == 'videos':
                cursor.execute("SELECT COUNT(*) FROM videos_rank")
            else:
                cursor.execute("SELECT COUNT(*) FROM channels_rank")
            
            row = cursor.fetchone()
            total_overall = row[0] if row else 0
        except Exception as e:
            logger.error(f"Failed to get total_overall count: {e}")

        conn.close()

        # [PLAN.md 3.2] 백엔드 로그 최적화 - 테이블 정보 상세화
        if len(results) > 0:
            # 첫 번째 행 샘플 로그 (썸네일 URL 확인용)
            sample = results[0]
            logger.info(f"API Success: /api/crawl_data - Data type: {data_type}, Filtered: {total}, Total: {total_overall}")
            logger.debug(f"[Sample Row] video_id={sample.get('video_id')}, thumbnail_url={sample.get('thumbnail_url')}, "
                        f"channel_name={sample.get('channel_name')}, channel_profile_url={sample.get('channel_profile_url')}")
        else:
            logger.info(f"API Success: /api/crawl_data - No data found for current filters.")

        return jsonify({
            'status': 'success',
            'type': data_type,
            'count': len(results),
            'total': total,
            'total_overall': total_overall,
            'results': results,
            'sort_by': sort_by,
            'sort_order': sort_order
        })

    except Exception as e:
        logger.error(f"Crawl data API error: {e}", exc_info=True)
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/api/crawl_data/aggregated')
def api_crawl_data_aggregated():
    """수집일 기준 조회수 합산 순위 API"""
    try:
        data_type = request.args.get('type', 'all')  # shorts, videos, all
        category = request.args.get('category', '')
        country = request.args.get('country', '')
        crawl_date = request.args.get('crawl_date', '')  # 특정 수집일
        limit = int(request.args.get('limit', 100))
        offset = int(request.args.get('offset', 0))
        sort_by = request.args.get('sort_by', 'total_views')
        sort_order = request.args.get('sort_order', 'desc')

        conn = get_db_connection()
        cursor = conn.cursor()

        # [PLAN Phase 3] 수집일 기준 조회수 합산 (같은 video_id의 조회수를 합산)
        if data_type == 'all':
            query = '''
                SELECT video_id, title, channel_name, channel_id,
                       SUM(views) as total_views, COUNT(*) as crawl_count,
                       MAX(views) as max_views, MIN(views) as min_views,
                       GROUP_CONCAT(DISTINCT category) as categories,
                       MAX(crawled_at) as last_crawled, MIN(crawled_at) as first_crawled,
                       MAX(thumbnail_url) as thumbnail_url,
                       'shorts' as data_type
                FROM shorts_rank
                WHERE 1=1
            '''
        elif data_type == 'shorts':
            query = '''
                SELECT video_id, title, channel_name, channel_id,
                       SUM(views) as total_views, COUNT(*) as crawl_count,
                       MAX(views) as max_views, MIN(views) as min_views,
                       GROUP_CONCAT(DISTINCT category) as categories,
                       MAX(crawled_at) as last_crawled, MIN(crawled_at) as first_crawled,
                       MAX(thumbnail_url) as thumbnail_url
                FROM shorts_rank
                WHERE 1=1
            '''
        else:  # videos
            query = '''
                SELECT video_id, title, channel_name, channel_id,
                       SUM(views) as total_views, COUNT(*) as crawl_count,
                       MAX(views) as max_views, MIN(views) as min_views,
                       GROUP_CONCAT(DISTINCT category) as categories,
                       MAX(crawled_at) as last_crawled, MIN(crawled_at) as first_crawled,
                       MAX(thumbnail_url) as thumbnail_url
                FROM videos_rank
                WHERE 1=1
            '''

        params = []

        if category:
            query += " AND category = ?"
            params.append(category)
        if country:
            query += " AND country = ?"
            params.append(country)
        if crawl_date:
            query += " AND DATE(crawled_at) = ?"
            params.append(crawl_date)

        query += " GROUP BY video_id"

        # 전체 데이터 UNION
        if data_type == 'all':
            query += '''
                UNION ALL
                SELECT video_id, title, channel_name, channel_id,
                       SUM(views) as total_views, COUNT(*) as crawl_count,
                       MAX(views) as max_views, MIN(views) as min_views,
                       GROUP_CONCAT(DISTINCT category) as categories,
                       MAX(crawled_at) as last_crawled, MIN(crawled_at) as first_crawled,
                       MAX(thumbnail_url) as thumbnail_url,
                       'videos' as data_type
                FROM videos_rank
                WHERE 1=1
            '''
            if category:
                query += " AND category = ?"
                params.append(category)
            if country:
                query += " AND country = ?"
                params.append(country)
            if crawl_date:
                query += " AND DATE(crawled_at) = ?"
                params.append(crawl_date)
            query += " GROUP BY video_id"

        # 정렬
        sort_dir = 'ASC' if sort_order.lower() == 'asc' else 'DESC'
        if sort_by in ['total_views', 'crawl_count', 'max_views', 'last_crawled']:
            query = f"SELECT * FROM ({query}) ORDER BY {sort_by} {sort_dir}"
        else:
            query = f"SELECT * FROM ({query}) ORDER BY total_views DESC"

        query += " LIMIT ? OFFSET ?"
        params.extend([limit, offset])

        cursor.execute(query, params)
        results = [dict(row) for row in cursor.fetchall()]

        # 순위 부여
        for idx, item in enumerate(results, start=offset + 1):
            item['aggregated_rank'] = idx

        conn.close()

        return jsonify({
            'status': 'success',
            'type': data_type,
            'count': len(results),
            'results': results
        })

    except Exception as e:
        logger.error(f"Aggregated data API error: {e}", exc_info=True)
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/api/crawl_dates')
def api_crawl_dates():
    """수집 날짜 목록 조회 API"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        # 모든 테이블에서 수집 날짜 조회
        cursor.execute('''
            SELECT DISTINCT DATE(crawled_at) as crawl_date, COUNT(*) as count, 'shorts' as type
            FROM shorts_rank GROUP BY DATE(crawled_at)
            UNION ALL
            SELECT DISTINCT DATE(crawled_at) as crawl_date, COUNT(*) as count, 'videos' as type
            FROM videos_rank GROUP BY DATE(crawled_at)
            UNION ALL
            SELECT DISTINCT DATE(crawled_at) as crawl_date, COUNT(*) as count, 'channels' as type
            FROM channels_rank GROUP BY DATE(crawled_at)
            ORDER BY crawl_date DESC
        ''')

        results = [dict(row) for row in cursor.fetchall()]

        # 날짜별 합계 계산
        date_summary = {}
        for row in results:
            date = row['crawl_date']
            if date not in date_summary:
                date_summary[date] = {'date': date, 'shorts': 0, 'videos': 0, 'channels': 0, 'total': 0}
            date_summary[date][row['type']] = row['count']
            date_summary[date]['total'] += row['count']

        conn.close()

        return jsonify({
            'status': 'success',
            'dates': list(date_summary.values())
        })

    except Exception as e:
        logger.error(f"Crawl dates API error: {e}")
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/api/crawl_data/category_rank')
def api_category_rank():
    """카테고리별 전체 순위 계산 API

    각 카테고리 내에서 조회수 기준으로 전체 순위를 매깁니다.
    쇼츠, 비디오, 채널 각각의 카테고리에서 1위부터 순위를 계산합니다.
    """
    try:
        data_type = request.args.get('type', 'videos')  # shorts, videos, channels
        limit = int(request.args.get('limit', 100))
        offset = int(request.args.get('offset', 0))

        # 수집일 필터 (최신 데이터 기준)
        crawl_date = request.args.get('crawl_date', '')  # 특정 날짜 지정 가능

        conn = get_db_connection()
        cursor = conn.cursor()

        # 카테고리별 순위 계산 (ROW_NUMBER 사용)
        if data_type == 'shorts':
            query = '''
                WITH RankedData AS (
                    SELECT
                        sr.id, sr.video_id, sr.title, sr.thumbnail_url,
                        sr.channel_name, sr.channel_id, sr.views, sr.likes,
                        sr.rank as original_rank, sr.rank_change, sr.upload_date,
                        sr.subscriber_count, sr.tags, sr.category, sr.country,
                        sr.period, sr.crawled_at, sr.updated_at,
                        CASE WHEN av.video_id IS NOT NULL THEN 1 ELSE 0 END as has_api_data,
                        ROW_NUMBER() OVER (
                            PARTITION BY sr.category
                            ORDER BY sr.views DESC
                        ) as category_rank
                    FROM shorts_rank sr
                    LEFT JOIN api_videos av ON sr.video_id = av.video_id
                    WHERE 1=1
            '''
        elif data_type == 'videos':
            query = '''
                WITH RankedData AS (
                    SELECT
                        vr.id, vr.video_id, vr.title, vr.thumbnail_url,
                        vr.channel_name, vr.channel_id, vr.views, vr.likes,
                        vr.rank as original_rank, vr.rank_change, vr.upload_date,
                        vr.subscriber_count, vr.tags, vr.category, vr.country,
                        vr.period, vr.crawled_at, vr.updated_at,
                        CASE WHEN av.video_id IS NOT NULL THEN 1 ELSE 0 END as has_api_data,
                        ROW_NUMBER() OVER (
                            PARTITION BY vr.category
                            ORDER BY vr.views DESC
                        ) as category_rank
                    FROM videos_rank vr
                    LEFT JOIN api_videos av ON vr.video_id = av.video_id
                    WHERE 1=1
            '''
        else:  # channels
            query = '''
                WITH RankedData AS (
                    SELECT
                        cr.id, cr.channel_id, cr.channel_name, cr.channel_url, cr.profile_url,
                        cr.rank as original_rank, cr.rank_change, cr.subscriber_count, cr.total_views,
                        cr.tags, cr.category, cr.country,
                        cr.period, cr.ranking_type, cr.crawled_at, cr.updated_at,
                        CASE WHEN ac.channel_id IS NOT NULL THEN 1 ELSE 0 END as has_api_data,
                        ac.subscriber_count as api_subscribers, ac.video_count as api_video_count,
                        ROW_NUMBER() OVER (
                            PARTITION BY cr.category
                            ORDER BY cr.subscriber_count DESC
                        ) as category_rank
                    FROM channels_rank cr
                    LEFT JOIN api_channels ac ON cr.channel_id = ac.channel_id
                    WHERE 1=1
            '''

        params = []

        # 수집일 필터 (선택사항)
        if crawl_date:
            query += " AND DATE(crawled_at) = ?"
            params.append(crawl_date)

        # Window function 종료 및 메인 쿼리
        query += '''
                )
                SELECT * FROM RankedData
                ORDER BY category, category_rank
                LIMIT ? OFFSET ?
        '''
        params.extend([limit, offset])

        cursor.execute(query, params)
        results = [dict(row) for row in cursor.fetchall()]

        # 총 개수 조회
        count_query_table = {
            'shorts': 'shorts_rank',
            'videos': 'videos_rank',
            'channels': 'channels_rank'
        }
        count_query = f"SELECT COUNT(*) as count FROM {count_query_table[data_type]} WHERE 1=1"
        count_params = []
        if crawl_date:
            count_query += " AND DATE(crawled_at) = ?"
            count_params.append(crawl_date)

        cursor.execute(count_query, count_params)
        result_row = cursor.fetchone()
        total = result_row['count'] if result_row else 0

        conn.close()

        logger.info(f"Category rank API: Type={data_type}, Returned {len(results)} items (Total: {total})")

        return jsonify({
            'status': 'success',
            'type': data_type,
            'total': total,
            'results': results
        })

    except Exception as e:
        logger.error(f"Category rank API error: {e}", exc_info=True)
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/api/sync/bulk', methods=['POST'])
def api_sync_bulk():
    """채널 일괄 동기화 API"""
    try:
        data = request.json
        channel_ids = data.get('channel_ids', [])

        if not channel_ids:
            return jsonify({'status': 'error', 'message': 'channel_ids required'}), 400

        results = []
        total_quota = 0
        success_count = 0
        fail_count = 0

        conn = get_db_connection()
        cursor = conn.cursor()

        for channel_id in channel_ids:
            # 채널 URL 조회
            cursor.execute('''
                SELECT channel_url FROM channels_rank WHERE channel_id = ? LIMIT 1
            ''', (channel_id,))
            row = cursor.fetchone()

            if row and row['channel_url']:
                result = youtube_manager.sync_channel(row['channel_url'])
                results.append({
                    'channel_id': channel_id,
                    'success': result['success'],
                    'quota_used': result['quota_used'],
                    'error': result['error']
                })
                total_quota += result['quota_used']
                if result['success']:
                    success_count += 1
                else:
                    fail_count += 1
            else:
                results.append({
                    'channel_id': channel_id,
                    'success': False,
                    'quota_used': 0,
                    'error': 'Channel URL not found'
                })
                fail_count += 1

        conn.close()

        return jsonify({
            'status': 'success',
            'total': len(channel_ids),
            'success_count': success_count,
            'fail_count': fail_count,
            'total_quota': total_quota,
            'results': results
        })

    except Exception as e:
        logger.error(f"Bulk sync API error: {e}")
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/api/remove_duplicates', methods=['POST'])
def api_remove_duplicates():
    """중복 데이터 제거 API - Window Function을 활용한 Batch Delete (최신 1개만 유지)

    모드:
    - check: 삭제 예정 건수만 조회 (미리보기)
    - execute: 실제 삭제 실행
    """
    try:
        data_type = request.json.get('type', 'all')
        mode = request.json.get('mode', 'check')  # 'check' or 'execute'

        logger.info(f"[Remove Duplicates] Mode: {mode}, Type: {data_type}")

        conn = get_db_connection()
        cursor = conn.cursor()

        results = {
            'shorts_rank': 0,
            'videos_rank': 0,
            'channels_rank': 0
        }

        if mode == 'check':
            # ============ CHECK 모드: 삭제 예정 건수만 조회 ============

            # Shorts 중복 건수 조회
            if data_type in ['shorts', 'all']:
                cursor.execute('''
                    SELECT COUNT(*) FROM (
                        SELECT id,
                        ROW_NUMBER() OVER (
                            PARTITION BY title, channel_name
                            ORDER BY crawled_at DESC
                        ) as rn
                        FROM shorts_rank
                        WHERE title IS NOT NULL AND channel_name IS NOT NULL
                    ) t
                    WHERE t.rn > 1
                ''')
                results['shorts_rank'] = cursor.fetchone()[0]
                logger.debug(f"[Remove Duplicates Check] Shorts duplicates: {results['shorts_rank']}")

            # Videos 중복 건수 조회
            if data_type in ['videos', 'all']:
                cursor.execute('''
                    SELECT COUNT(*) FROM (
                        SELECT id,
                        ROW_NUMBER() OVER (
                            PARTITION BY title, channel_name
                            ORDER BY crawled_at DESC
                        ) as rn
                        FROM videos_rank
                        WHERE title IS NOT NULL AND channel_name IS NOT NULL
                    ) t
                    WHERE t.rn > 1
                ''')
                results['videos_rank'] = cursor.fetchone()[0]
                logger.debug(f"[Remove Duplicates Check] Videos duplicates: {results['videos_rank']}")

            # Channels 중복 건수 조회
            if data_type in ['channels', 'all']:
                cursor.execute('''
                    SELECT COUNT(*) FROM (
                        SELECT id,
                        ROW_NUMBER() OVER (
                            PARTITION BY channel_name
                            ORDER BY crawled_at DESC
                        ) as rn
                        FROM channels_rank
                        WHERE channel_name IS NOT NULL
                    ) t
                    WHERE t.rn > 1
                ''')
                results['channels_rank'] = cursor.fetchone()[0]
                logger.debug(f"[Remove Duplicates Check] Channels duplicates: {results['channels_rank']}")

            conn.close()

            total_count = sum(results.values())
            logger.info(f"[Remove Duplicates Check] Total duplicates found: {total_count}")

            return jsonify({
                'status': 'success',
                'mode': 'check',
                'results': results,
                'total': total_count
            })

        elif mode == 'execute':
            # ============ EXECUTE 모드: 실제 삭제 실행 ============

            # Shorts 중복 제거 (단일 쿼리 - Window Function 활용)
            if data_type in ['shorts', 'all']:
                logger.debug("[Remove Duplicates Execute] Processing Shorts table...")
                cursor.execute('''
                    DELETE FROM shorts_rank
                    WHERE id IN (
                        SELECT id FROM (
                            SELECT id,
                            ROW_NUMBER() OVER (
                                PARTITION BY title, channel_name
                                ORDER BY crawled_at DESC
                            ) as rn
                            FROM shorts_rank
                            WHERE title IS NOT NULL AND channel_name IS NOT NULL
                        ) t
                        WHERE t.rn > 1
                    )
                ''')
                results['shorts_rank'] = cursor.rowcount
                logger.info(f"[Remove Duplicates Execute] Shorts removed: {results['shorts_rank']}")

            # Videos 중복 제거 (단일 쿼리 - Window Function 활용)
            if data_type in ['videos', 'all']:
                logger.debug("[Remove Duplicates Execute] Processing Videos table...")
                cursor.execute('''
                    DELETE FROM videos_rank
                    WHERE id IN (
                        SELECT id FROM (
                            SELECT id,
                            ROW_NUMBER() OVER (
                                PARTITION BY title, channel_name
                                ORDER BY crawled_at DESC
                            ) as rn
                            FROM videos_rank
                            WHERE title IS NOT NULL AND channel_name IS NOT NULL
                        ) t
                        WHERE t.rn > 1
                    )
                ''')
                results['videos_rank'] = cursor.rowcount
                logger.info(f"[Remove Duplicates Execute] Videos removed: {results['videos_rank']}")

            # Channels 중복 제거 (단일 쿼리 - Window Function 활용)
            if data_type in ['channels', 'all']:
                logger.debug("[Remove Duplicates Execute] Processing Channels table...")
                cursor.execute('''
                    DELETE FROM channels_rank
                    WHERE id IN (
                        SELECT id FROM (
                            SELECT id,
                            ROW_NUMBER() OVER (
                                PARTITION BY channel_name
                                ORDER BY crawled_at DESC
                            ) as rn
                            FROM channels_rank
                            WHERE channel_name IS NOT NULL
                        ) t
                        WHERE t.rn > 1
                    )
                ''')
                results['channels_rank'] = cursor.rowcount
                logger.info(f"[Remove Duplicates Execute] Channels removed: {results['channels_rank']}")

            conn.commit()
            conn.close()

            total_removed = sum(results.values())
            logger.info(f"[Remove Duplicates Execute] Completed. Total removed: {total_removed}")

            return jsonify({
                'status': 'success',
                'mode': 'execute',
                'results': results,
                'total': total_removed,
                'message': 'Duplicate removal completed successfully.'
            })

        else:
            return jsonify({'status': 'error', 'message': f'Invalid mode: {mode}'}), 400

    except Exception as e:
        import traceback
        # [PLAN Phase 4.2] 에러 발생 시 Critical 레벨로 기록하고, exc_info=True로 트레이스백 포함
        logger.critical(f"[Remove Duplicates] CRITICAL Error: {str(e)}", exc_info=True)
        return jsonify({'status': 'error', 'message': str(e)}), 500


# ============================================================
# 재생목록 관리 API (Playlist-Driven Channel Discovery)
# ============================================================

@app.route('/api/playlists', methods=['GET'])
def api_playlists_list():
    """
    저장된 재생목록 목록 조회

    Returns:
        JSON: {status, count, playlists: [...]}
    """
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        cursor.execute('''
            SELECT playlist_id, title, thumbnail_url, item_count, channel_title,
                   last_synced_at, created_at
            FROM monitored_playlists
            ORDER BY created_at DESC
        ''')

        playlists = [dict(row) for row in cursor.fetchall()]
        conn.close()

        return jsonify({
            'status': 'success',
            'count': len(playlists),
            'playlists': playlists
        })

    except Exception as e:
        logger.error(f"[Playlist] Error listing playlists: {e}", exc_info=True)
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/api/playlists', methods=['POST'])
def api_playlists_add():
    """
    재생목록 추가 (URL 파싱 -> 메타데이터 조회 -> DB 저장)

    Request Body:
        {url_or_id: "PLxxxx" or "https://youtube.com/...&list=PLxxxx"}

    Returns:
        JSON: {status, result: 'new'|'updated', playlist: {...}}
    """
    try:
        data = request.get_json()
        url_or_id = data.get('url_or_id', '').strip()

        if not url_or_id:
            return jsonify({'status': 'error', 'message': 'URL or playlist ID required'}), 400

        # Smart URL Parsing: URL에서 list= 파라미터 추출
        import re
        from urllib.parse import urlparse, parse_qs

        playlist_id = url_or_id

        if 'youtube.com' in url_or_id or 'youtu.be' in url_or_id:
            try:
                parsed = urlparse(url_or_id)
                params = parse_qs(parsed.query)
                if 'list' in params:
                    playlist_id = params['list'][0]
            except Exception:
                pass

        # PL로 시작하는지 확인 (일반적인 재생목록 ID 형식)
        if not playlist_id.startswith('PL') and not playlist_id.startswith('UU') and not playlist_id.startswith('FL'):
            return jsonify({
                'status': 'error',
                'message': f'Invalid playlist ID format: {playlist_id}'
            }), 400

        # YouTube API로 메타데이터 조회
        from modules.youtube_manager import YouTubeManager
        logger.info(f"Fetching metadata for playlist ID: {playlist_id}")
        yt = YouTubeManager(DB_PATH)

        metadata = yt.fetch_playlist_metadata(playlist_id)

        if not metadata['success']:
            return jsonify({
                'status': 'error',
                'message': metadata.get('error', 'Failed to fetch playlist metadata'),
                'quota_used': metadata.get('quota_used', 0)
            }), 400

        # DB에 저장
        conn = get_db_connection()
        cursor = conn.cursor()

        # 기존 레코드 확인
        cursor.execute('SELECT 1 FROM monitored_playlists WHERE playlist_id = ?', (playlist_id,))
        exists = cursor.fetchone() is not None

        if exists:
            cursor.execute('''
                UPDATE monitored_playlists
                SET title = ?, thumbnail_url = ?, item_count = ?, channel_title = ?
                WHERE playlist_id = ?
            ''', (metadata['title'], metadata['thumbnail_url'], metadata['item_count'],
                  metadata['channel_title'], playlist_id))
            result = 'updated'
        else:
            cursor.execute('''
                INSERT INTO monitored_playlists
                (playlist_id, title, thumbnail_url, item_count, channel_title)
                VALUES (?, ?, ?, ?, ?)
            ''', (playlist_id, metadata['title'], metadata['thumbnail_url'],
                  metadata['item_count'], metadata['channel_title']))
            result = 'new'

        conn.commit()
        conn.close()

        logger.info(f"[Playlist] Added/Updated: {playlist_id} - {metadata['title']}")

        return jsonify({
            'status': 'success',
            'result': result,
            'playlist': {
                'playlist_id': playlist_id,
                'title': metadata['title'],
                'thumbnail_url': metadata['thumbnail_url'],
                'item_count': metadata['item_count'],
                'channel_title': metadata['channel_title']
            },
            'quota_used': metadata['quota_used']
        })

    except Exception as e:
        logger.error(f"[Playlist] Error adding playlist: {e}", exc_info=True)
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/api/playlists/<playlist_id>', methods=['DELETE'])
def api_playlists_delete(playlist_id):
    """
    재생목록 삭제

    Args:
        playlist_id: 재생목록 ID

    Returns:
        JSON: {status, deleted: bool}
    """
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        cursor.execute('DELETE FROM monitored_playlists WHERE playlist_id = ?', (playlist_id,))
        deleted = cursor.rowcount > 0

        conn.commit()
        conn.close()

        if deleted:
            logger.info(f"[Playlist] Deleted: {playlist_id}")
            return jsonify({'status': 'success', 'deleted': True})
        else:
            return jsonify({'status': 'error', 'message': 'Playlist not found'}), 404

    except Exception as e:
        logger.error(f"[Playlist] Error deleting playlist: {e}", exc_info=True)
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/api/playlists/<playlist_id>/sync', methods=['POST'])
def api_playlists_sync(playlist_id):
    """
    재생목록에서 채널 추출 및 동기화

    Args:
        playlist_id: 재생목록 ID

    Returns:
        JSON: {status, total, new, updated, channels: [...], quota_used}
    """
    try:
        from modules.youtube_manager import YouTubeManager
        yt = YouTubeManager(DB_PATH)

        result = yt.extract_channels_from_playlist(playlist_id)

        if not result['success']:
            return jsonify({
                'status': 'error',
                'message': result.get('error', 'Failed to extract channels'),
                'quota_used': result.get('quota_used', 0)
            }), 400

        logger.info(f"[Playlist] Sync complete: {playlist_id} - "
                   f"Total: {result['total']}, New: {result['new']}, Updated: {result['updated']}")

        return jsonify({
            'status': 'success',
            'total': result['total'],
            'new': result['new'],
            'updated': result['updated'],
            'channels': result['channels'],
            'quota_used': result['quota_used']
        })

    except Exception as e:
        logger.error(f"[Playlist] Error syncing playlist: {e}", exc_info=True)
        return jsonify({'status': 'error', 'message': str(e)}), 500


# ============================================================
# 채널 관리 API (Phase 2: Channel Manager)
# ============================================================

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

        # PLAN.md Phase 4: 요청 파라미터 로깅
        logger.debug(f"[API] Channel List Req: filter={sync_status}, sort={sort_by}, limit={limit}, offset={offset}")

        conn = get_db_connection()
        cursor = conn.cursor()

        # ===== 개선 #41: 재생목록 기반 채널 목록 =====
        # PLAN.md Phase 2: '동기화' → '추출 완료' 개념 재정의
        # api_channels에서 crawled_url='playlist'인 채널만 조회
        # LEFT JOIN with monitored_playlists to get playlist title
        main_query = """
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
                ac.discovery_video_id,
                ac.discovery_video_url,
                ac.last_updated,
                CASE
                    WHEN ac.sync_status = 'synced' THEN 1 ELSE 0
                END as is_synced,
                CASE
                    WHEN (ac.channel_id IS NOT NULL AND ac.channel_id != 'N/A')
                         OR ac.last_synced_at IS NOT NULL
                    THEN 1 ELSE 0
                END as is_extracted,
                CASE WHEN ac.last_synced_at IS NULL THEN 1 ELSE 0 END as sync_null_flag,
                CAST(COALESCE(JulianDay('now') - JulianDay(ac.last_synced_at), 9999) AS INTEGER) as days_since_sync
            FROM api_channels ac
            LEFT JOIN monitored_playlists mp ON ac.playlist_source = mp.playlist_id
            WHERE ac.crawled_url = 'playlist'
        """

        params = []

        # 필터 적용
        if sync_status == 'synced':
            main_query += " AND ac.sync_status = 'synced'"
        elif sync_status == 'unsynced':
            main_query += " AND (ac.sync_status IS NULL OR ac.sync_status != 'synced')"

        # 정렬 (NULLS LAST 효과를 위해 sync_null_flag 사용)
        sort_map = {
            'channel_name': 'ac.title',
            'subscriber_count': 'COALESCE(ac.subscriber_count, 0)',
            'last_synced_at': 'sync_null_flag ASC, ac.last_synced_at',
            'days_since_sync': 'sync_null_flag ASC, days_since_sync',
            'last_updated': 'ac.last_updated'
        }
        sort_column = sort_map.get(sort_by, 'sync_null_flag ASC, ac.last_synced_at')
        # For last_synced_at, we want DESC (newest first), but sync_null_flag should be ASC (nulls last)
        if sort_by in ['last_synced_at', 'days_since_sync']:
            main_query += f" ORDER BY {sort_column} {sort_order.upper()}"
        else:
            main_query += f" ORDER BY {sort_column} {sort_order.upper()}"

        # 페이지네이션
        main_query += " LIMIT ? OFFSET ?"
        params.extend([limit, offset])

        cursor.execute(main_query, params)
        channels = [dict(row) for row in cursor.fetchall()]

        # 총 개수 조회
        count_query = """
            SELECT COUNT(*) as total
            FROM api_channels ac
            WHERE ac.crawled_url = 'playlist'
        """
        count_params = []

        if sync_status == 'synced':
            count_query += " AND ac.sync_status = 'synced'"
        elif sync_status == 'unsynced':
            count_query += " AND (ac.sync_status IS NULL OR ac.sync_status != 'synced')"

        cursor.execute(count_query, count_params)
        total = cursor.fetchone()['total']

        conn.close()

        # [PLAN.md 3.2] 백엔드 로그 최적화 - 채널 목록 상세 정보
        logger.info(f"GET /api/channel_manager/list - Success ({len(channels)} channels, total: {total})")
        if len(channels) > 0:
            sample = channels[0]
            logger.debug(f"[Sample Channel] channel_id={sample.get('channel_id')}, "
                        f"title={sample.get('channel_name') or sample.get('title')}, "
                        f"is_synced={sample.get('is_synced')}, "
                        f"discovery_video_id={sample.get('discovery_video_id')}")

        return jsonify({
            'status': 'success',
            'count': len(channels),
            'total': total,
            'channels': channels
        })

    except Exception as e:
        logger.error(f"GET /api/channel_manager/list - Error: {e}")
        logger.exception("Channel Manager List exception details:")
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/api/channel_manager/status')
def api_channel_manager_status():
    """
    채널 관리 동기화 현황 통계 API (재생목록 기반)

    재생목록에서 추출한 채널의 동기화 상태 집계
    """
    logger.debug("GET /api/channel_manager/status - Request received")

    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        # 재생목록 기반 채널 통계 (api_channels에서 playlist로 추가된 채널)
        cursor.execute('''
            SELECT
                COUNT(*) as total_channels,
                SUM(CASE WHEN sync_status = 'synced' THEN 1 ELSE 0 END) as synced_channels,
                SUM(CASE WHEN sync_status IS NULL OR sync_status != 'synced' THEN 1 ELSE 0 END) as unsynced_channels
            FROM api_channels
            WHERE crawled_url = 'playlist'
        ''')

        row = cursor.fetchone()
        total_channels = row['total_channels'] or 0
        synced_channels = row['synced_channels'] or 0
        unsynced_channels = row['unsynced_channels'] or 0

        # 오래된 동기화 채널 수 (7일 이상)
        cursor.execute('''
            SELECT COUNT(*) as count
            FROM api_channels
            WHERE crawled_url = 'playlist'
              AND last_synced_at IS NOT NULL
              AND JulianDay('now') - JulianDay(last_synced_at) >= 7
        ''')
        outdated_count = cursor.fetchone()['count'] or 0

        # 재생목록별 채널 현황
        cursor.execute('''
            SELECT
                mp.playlist_id,
                mp.title as playlist_title,
                COUNT(ac.channel_id) as channel_count,
                SUM(CASE WHEN ac.sync_status = 'synced' THEN 1 ELSE 0 END) as synced_count
            FROM monitored_playlists mp
            LEFT JOIN api_channels ac ON ac.crawled_url = 'playlist'
            GROUP BY mp.playlist_id, mp.title
            ORDER BY channel_count DESC
        ''')
        playlist_stats = [dict(row) for row in cursor.fetchall()]

        # Quota 현황
        quota_status = quota_tracker.get_today_usage()

        conn.close()

        sync_rate = round(synced_channels * 100.0 / max(total_channels, 1), 1)

        logger.info(f"GET /api/channel_manager/status - Success (total: {total_channels}, synced: {synced_channels}, rate: {sync_rate}%)")

        return jsonify({
            'status': 'success',
            'summary': {
                'total_channels': total_channels,
                'synced_channels': synced_channels,
                'unsynced_channels': unsynced_channels,
                'sync_rate': sync_rate,
                'outdated_count': outdated_count,
                'low_cost_ready': unsynced_channels  # 재생목록 기반이므로 모두 Low-Cost 복구 가능
            },
            'by_playlist': playlist_stats,
            'quota': quota_status
        })

    except Exception as e:
        logger.error(f"GET /api/channel_manager/status - Error: {e}")
        logger.exception("Channel Manager Status exception details:")
        return jsonify({'status': 'error', 'message': str(e)}), 500


# 전역 배치 동기화 진행 상태
batch_sync_progress = {
    'is_running': False,
    'current': 0,
    'total': 0,
    'current_channel': '',
    'status': 'idle',
    'success_count': 0,
    'failed_count': 0,
    'recovered_count': 0
}


@app.route('/api/channel_manager/sync/batch', methods=['POST'])
def api_channel_manager_sync_batch():
    """
    채널 일괄 동기화 API (Smart Recovery 포함)
    """
    global batch_sync_progress

    try:
        data = request.json
        channel_ids = data.get('channel_ids', [])
        fetch_videos = data.get('fetch_videos', False)
        video_limit = data.get('video_limit', 50)
        fetch_all = data.get('fetch_all', False)
        use_search_fallback = data.get('use_search_fallback', True)

        if not channel_ids:
            return jsonify({'status': 'error', 'message': 'No channel IDs provided'}), 400

        if batch_sync_progress['is_running']:
            return jsonify({
                'status': 'error',
                'message': '이미 동기화가 진행 중입니다.',
                'progress': batch_sync_progress
            }), 409

        batch_sync_progress = {
            'is_running': True,
            'current': 0,
            'total': len(channel_ids),
            'current_channel': '',
            'status': 'running',
            'success_count': 0,
            'failed_count': 0,
            'recovered_count': 0
        }

        start_time = time.time()
        logger.info(f"[Batch Sync] ========== BATCH SYNC START ==========")
        logger.info(f"[Batch Sync] Total Channels: {len(channel_ids)}")
        logger.info(f"[Batch Sync] Fetch Videos: {fetch_videos}, Limit: {video_limit}, Fetch All: {fetch_all}")
        logger.info(f"[Batch Sync] Smart Recovery: use_search_fallback={use_search_fallback}")
        logger.info(f"[Batch Sync] Channel IDs: {channel_ids[:5]}{'...' if len(channel_ids) > 5 else ''}")

        results = {
            'success': [],
            'failed': [],
            'recovered': [],  # Smart Recovery로 복구된 채널
            'total_quota_used': 0
        }

        conn = get_db_connection()
        cursor = conn.cursor()

        for idx, channel_id in enumerate(channel_ids):
            logger.info(f"[Batch Sync] ========== [{idx + 1}/{len(channel_ids)}] Processing channel: {channel_id} ==========")
            # Quota 체크
            if not quota_tracker.can_make_request('channels.list'):
                logger.warning(f"[Batch Sync] Quota exceeded at channel {idx + 1}/{len(channel_ids)}")
                results['failed'].append({
                    'channel_id': channel_id,
                    'error': 'Quota exceeded'
                })
                continue

            try:
                # ===== 개선 #37: channel_id가 'N/A'이면 실제 channel_id 찾기 =====
                actual_channel_id = channel_id
                if channel_id == 'N/A' or channel_id.startswith('temp_'):
                    logger.info(f"[Batch Sync] Invalid channel_id detected: '{channel_id}', searching for actual ID...")

                    # videos_rank에서 채널명으로 실제 channel_id 찾기
                    cursor.execute('''
                        SELECT channel_id, channel_name FROM videos_rank
                        WHERE channel_id != 'N/A'
                          AND channel_id NOT LIKE 'temp_%'
                          AND channel_id IS NOT NULL
                        ORDER BY crawled_at DESC LIMIT 1
                    ''')
                    video_row = cursor.fetchone()

                    if video_row and video_row['channel_id']:
                        actual_channel_id = video_row['channel_id']
                        logger.info(f"[Batch Sync] ✓ Found actual channel_id from videos_rank: '{actual_channel_id}'")
                    else:
                        # shorts_rank에서도 찾기
                        cursor.execute('''
                            SELECT channel_id, channel_name FROM shorts_rank
                            WHERE channel_id != 'N/A'
                              AND channel_id NOT LIKE 'temp_%'
                              AND channel_id IS NOT NULL
                            ORDER BY crawled_at DESC LIMIT 1
                        ''')
                        short_row = cursor.fetchone()

                        if short_row and short_row['channel_id']:
                            actual_channel_id = short_row['channel_id']
                            logger.info(f"[Batch Sync] ✓ Found actual channel_id from shorts_rank: '{actual_channel_id}'")
                        else:
                            logger.warning(f"[Batch Sync] ✗ No valid channel_id found in DB, will use Smart Recovery")

                # 채널 URL 및 메타데이터 조회 (Smart Recovery용)
                cursor.execute('''
                    SELECT channel_url, channel_name, subscriber_count FROM channels_rank
                    WHERE channel_id = ?
                    ORDER BY crawled_at DESC LIMIT 1
                ''', (channel_id,))
                row = cursor.fetchone()

                # actual_channel_id가 다르면 업데이트된 URL 사용
                if actual_channel_id != channel_id:
                    channel_url = f'https://www.youtube.com/channel/{actual_channel_id}'
                    logger.info(f"[Batch Sync] Using actual channel URL: {channel_url}")
                else:
                    channel_url = row['channel_url'] if row and row['channel_url'] else f'https://www.youtube.com/channel/{channel_id}'

                # Invalid channel_name 필터링 (N/A, temp_xxx, 빈 값 등)
                raw_channel_name = row['channel_name'] if row else None
                channel_name = None

                if raw_channel_name and raw_channel_name != 'N/A' and not raw_channel_name.startswith('temp_'):
                    channel_name = raw_channel_name
                else:
                    # channels_rank에 유효한 채널명이 없으면 videos_rank/shorts_rank에서 찾기
                    logger.debug(f"[Batch Sync] Invalid channel_name in channels_rank: '{raw_channel_name}', searching in videos/shorts tables...")

                    cursor.execute('''
                        SELECT channel_name FROM videos_rank
                        WHERE channel_id = ?
                          AND channel_name IS NOT NULL
                          AND channel_name != ''
                          AND channel_name != 'N/A'
                          AND channel_name NOT LIKE 'temp_%'
                        ORDER BY crawled_at DESC LIMIT 1
                    ''', (channel_id,))

                    video_row = cursor.fetchone()
                    if video_row:
                        channel_name = video_row['channel_name']
                        logger.info(f"[Batch Sync] Found valid channel_name from videos_rank: '{channel_name}'")
                    else:
                        # videos_rank에 없으면 shorts_rank 확인
                        cursor.execute('''
                            SELECT channel_name FROM shorts_rank
                            WHERE channel_id = ?
                              AND channel_name IS NOT NULL
                              AND channel_name != ''
                              AND channel_name != 'N/A'
                              AND channel_name NOT LIKE 'temp_%'
                            ORDER BY crawled_at DESC LIMIT 1
                        ''', (channel_id,))

                        short_row = cursor.fetchone()
                        if short_row:
                            channel_name = short_row['channel_name']
                            logger.info(f"[Batch Sync] Found valid channel_name from shorts_rank: '{channel_name}'")
                        else:
                            logger.warning(f"[Batch Sync] No valid channel_name found for channel_id: {channel_id}")

                # 구독자 수 파싱 (Smart Recovery 검증용)
                from modules.utils import parse_count_string
                subscriber_count = parse_count_string(row['subscriber_count']) if row and row['subscriber_count'] else 0

                # ===== 개선 #38: subscriber_count가 0이면 다른 테이블에서 찾기 =====
                if subscriber_count == 0:
                    logger.debug(f"[Batch Sync] subscriber_count=0 in channels_rank, searching in videos/shorts tables...")

                    # videos_rank에서 구독자 수 찾기
                    cursor.execute('''
                        SELECT subscriber_count FROM videos_rank
                        WHERE channel_name = ?
                          AND subscriber_count IS NOT NULL
                          AND subscriber_count != ''
                          AND subscriber_count != '0'
                        ORDER BY crawled_at DESC LIMIT 1
                    ''', (channel_name,))
                    subs_row = cursor.fetchone()

                    if subs_row and subs_row['subscriber_count']:
                        subscriber_count = parse_count_string(subs_row['subscriber_count'])
                        logger.info(f"[Batch Sync] ✓ Found subscriber_count from videos_rank: {subscriber_count:,}")
                    else:
                        # shorts_rank에서도 찾기
                        cursor.execute('''
                            SELECT subscriber_count FROM shorts_rank
                            WHERE channel_name = ?
                              AND subscriber_count IS NOT NULL
                              AND subscriber_count != ''
                              AND subscriber_count != '0'
                            ORDER BY crawled_at DESC LIMIT 1
                        ''', (channel_name,))
                        subs_row = cursor.fetchone()

                        if subs_row and subs_row['subscriber_count']:
                            subscriber_count = parse_count_string(subs_row['subscriber_count'])
                            logger.info(f"[Batch Sync] ✓ Found subscriber_count from shorts_rank: {subscriber_count:,}")
                        else:
                            logger.warning(f"[Batch Sync] ✗ No subscriber_count found in any table for '{channel_name}'")

                logger.info(f"[Batch Sync] [{idx + 1}/{len(channel_ids)}] Processing: '{channel_name}' (ID: {channel_id}, Subs: {subscriber_count:,})")

                # Update progress
                batch_sync_progress['current'] = idx + 1
                batch_sync_progress['current_channel'] = channel_name or channel_id

                # 채널 동기화 (Smart Recovery 옵션 포함)
                sync_result = youtube_manager.sync_channel(
                    channel_url,
                    channel_name=channel_name,
                    subscriber_count=subscriber_count,
                    use_search_fallback=use_search_fallback
                )

                sync_quota = sync_result.get('quota_used', 0)
                results['total_quota_used'] += sync_quota

                logger.debug(f"[Batch Sync] [{idx + 1}/{len(channel_ids)}] Sync result: success={sync_result['success']}, quota={sync_quota}, recovery_method={sync_result.get('recovery_method')}")

                if sync_result['success']:
                    videos_fetched = 0

                    # Smart Recovery 성공 여부 추적 (video 또는 search)
                    recovery_method = sync_result.get('recovery_method')
                    if recovery_method in ['video', 'search']:
                        results['recovered'].append({
                            'old_id': channel_id,
                            'new_id': sync_result['channel_id'],
                            'channel_name': channel_name,
                            'recovery_method': recovery_method,
                            'quota_used': sync_result.get('quota_used', 0)
                        })
                        batch_sync_progress['recovered_count'] += 1
                        method_label = 'Low-Cost (Video)' if recovery_method == 'video' else 'High-Cost (Search)'
                        logger.info(f"[{method_label} Recovery] ✓ Channel recovered: {channel_name} ({channel_id} → {sync_result['channel_id']})")

                        # DB에서 임시 ID를 실제 ID로 교체
                        from modules.database import DatabaseHandler
                        db = DatabaseHandler()
                        db.update_channel_id(channel_id, sync_result['channel_id'])

                    # 영상 수집 (옵션) - PLAN.md: fetch_all 지원
                    if fetch_videos and sync_result.get('channel_id'):
                        logger.info(f"[Batch Sync] [{idx + 1}/{len(channel_ids)}] Fetching videos for '{channel_name}' (fetch_all={fetch_all}, limit={video_limit})")
                        video_result = youtube_manager.fetch_videos(
                            sync_result['channel_id'],
                            limit=video_limit,
                            fetch_all=fetch_all
                        )
                        video_quota = video_result.get('quota_used', 0)
                        results['total_quota_used'] += video_quota
                        videos_fetched = len(video_result.get('videos', []))
                        logger.info(f"[Batch Sync] [{idx + 1}/{len(channel_ids)}] Videos fetched: {videos_fetched}, quota: {video_quota}")

                        # api_channels 테이블 업데이트
                        if video_result['success']:
                            # 최신 영상 업로드일 조회
                            cursor.execute('''
                                SELECT MAX(published_at) as latest
                                FROM api_videos
                                WHERE channel_id = ?
                            ''', (sync_result['channel_id'],))
                            latest_row = cursor.fetchone()
                            latest_date = latest_row['latest'][:10] if latest_row and latest_row['latest'] else None

                            cursor.execute('''
                                UPDATE api_channels SET
                                    last_synced_at = ?,
                                    sync_status = 'synced',
                                    collected_video_count = ?,
                                    latest_video_upload_date = ?
                                WHERE channel_id = ?
                            ''', (
                                datetime.now().isoformat(),
                                videos_fetched,
                                latest_date,
                                sync_result['channel_id']
                            ))
                    else:
                        # 영상 미수집 - 채널 정보만 업데이트
                        cursor.execute('''
                            UPDATE api_channels SET
                                last_synced_at = ?,
                                sync_status = 'synced'
                            WHERE channel_id = ?
                        ''', (datetime.now().isoformat(), sync_result['channel_id']))

                    # 동기화 로그 저장
                    cursor.execute('''
                        INSERT INTO api_sync_logs (channel_id, channel_name, status, videos_fetched, used_quota)
                        VALUES (?, ?, 'success', ?, ?)
                    ''', (sync_result['channel_id'], channel_name, videos_fetched, sync_result.get('quota_used', 0)))

                    results['success'].append({
                        'channel_id': sync_result['channel_id'],
                        'channel_name': channel_name,
                        'videos_fetched': videos_fetched,
                        'recovery_method': sync_result.get('recovery_method')
                    })
                    batch_sync_progress['success_count'] += 1
                    logger.info(f"[Batch Sync] [{idx + 1}/{len(channel_ids)}] ✓ Success: '{channel_name}' (videos: {videos_fetched})")
                else:
                    error_msg = sync_result.get('error', 'Unknown error')
                    # 동기화 실패 로그
                    cursor.execute('''
                        INSERT INTO api_sync_logs (channel_id, channel_name, status, error_message)
                        VALUES (?, ?, 'failed', ?)
                    ''', (channel_id, channel_name, error_msg))

                    results['failed'].append({
                        'channel_id': channel_id,
                        'channel_name': channel_name,
                        'error': error_msg
                    })
                    batch_sync_progress['failed_count'] += 1
                    logger.warning(f"[Batch Sync] [{idx + 1}/{len(channel_ids)}] ✗ Failed: '{channel_name}' - {error_msg}")

                conn.commit()

                # Rate Limiting: 1.5~3.5초 랜덤 딜레이 (PLAN.md Section 4.2 - Bot Detection Avoidance)
                if idx < len(channel_ids) - 1:
                    delay = random.uniform(1.5, 3.5)
                    time.sleep(delay)

            except Exception as e:
                logger.error(f"[Batch Sync] Error processing {channel_id}: {e}")
                results['failed'].append({
                    'channel_id': channel_id,
                    'error': str(e)
                })

        conn.close()

        success_count = len(results['success'])
        failed_count = len(results['failed'])
        recovered_count = len(results['recovered'])
        total_count = len(channel_ids)
        elapsed_time = time.time() - start_time

        # Mark progress as completed
        batch_sync_progress['is_running'] = False
        batch_sync_progress['status'] = 'completed'

        logger.info(f"[Batch Sync] ========== BATCH SYNC COMPLETE ==========")
        logger.info(f"[Batch Sync] Total Time: {elapsed_time:.2f}s ({elapsed_time/60:.1f}m)")
        logger.info(f"[Batch Sync] Success: {success_count}/{total_count}")
        logger.info(f"[Batch Sync] Failed: {failed_count}/{total_count}")
        logger.info(f"[Batch Sync] Recovered: {recovered_count} (Low-Cost + High-Cost)")
        logger.info(f"[Batch Sync] Total Quota Used: {results['total_quota_used']}")
        logger.info(f"[Batch Sync] Average Time per Channel: {elapsed_time/total_count:.2f}s")

        message = f'{success_count}개 채널 동기화 완료'
        if recovered_count > 0:
            message += f' (검색 복구: {recovered_count}개)'
        if failed_count > 0:
            message += f', {failed_count}개 실패'
        message += f' (총 {total_count}개)'

        return jsonify({
            'status': 'success',
            'message': message,
            'summary': {
                'total': total_count,
                'success': success_count,
                'failed': failed_count,
                'recovered': recovered_count,
                'quota_used': results['total_quota_used']
            },
            'results': results
        })

    except Exception as e:
        logger.error(f"[Batch Sync] Error: {e}", exc_info=True)
        batch_sync_progress['is_running'] = False
        batch_sync_progress['status'] = 'error'
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/api/channel_manager/sync/batch_progress')
def api_channel_manager_sync_batch_progress():
    """
    SSE endpoint for batch sync progress updates

    실시간 동기화 진행 상황을 Server-Sent Events로 전송
    """
    def generate():
        import json as json_module
        while True:
            if batch_sync_progress['is_running']:
                total = batch_sync_progress['total']
                current = batch_sync_progress['current']
                percentage = int((current / total * 100)) if total > 0 else 0

                data = {
                    'current': current,
                    'total': total,
                    'current_channel': batch_sync_progress['current_channel'],
                    'percentage': percentage,
                    'success_count': batch_sync_progress['success_count'],
                    'failed_count': batch_sync_progress['failed_count'],
                    'recovered_count': batch_sync_progress['recovered_count'],
                    'status': batch_sync_progress['status']
                }
                yield f"data: {json_module.dumps(data)}\n\n"
                time.sleep(0.5)
            else:
                yield f"data: {{\"status\": \"completed\"}}\n\n"
                break

    return Response(generate(), mimetype='text/event-stream')


@app.route('/api/channel_manager/delete_all', methods=['POST'])
def api_channel_manager_delete_all():
    """
    전체 채널 삭제 API (개선 #41: 재생목록 기반 채널만 삭제)

    재생목록에서 추출한 채널(crawled_url='playlist')만 삭제합니다.
    크롤링 데이터는 영향받지 않습니다.
    """
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        # 삭제 전 카운트 확인
        cursor.execute("SELECT COUNT(*) as count FROM api_channels WHERE crawled_url = 'playlist'")
        count_before = cursor.fetchone()['count']

        # 재생목록 기반 채널만 삭제
        cursor.execute("DELETE FROM api_channels WHERE crawled_url = 'playlist'")
        deleted_count = cursor.rowcount

        conn.commit()
        conn.close()

        logger.info(f"[Channel Manager] ========== DELETE ALL CHANNELS ==========")
        logger.info(f"[Channel Manager] Deleted {deleted_count} playlist-based channels (Expected: {count_before})")

        return jsonify({
            'status': 'success',
            'deleted': deleted_count,
            'message': f'{deleted_count}개의 재생목록 채널이 삭제되었습니다.'
        })

    except Exception as e:
        logger.error(f"[Channel Manager] Delete all channels error: {e}", exc_info=True)
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/api/channel_manager/sync_logs')
def api_channel_manager_sync_logs():
    """동기화 로그 조회 API"""
    try:
        limit = int(request.args.get('limit', 50))
        channel_id = request.args.get('channel_id', '')

        conn = get_db_connection()
        cursor = conn.cursor()

        if channel_id:
            cursor.execute('''
                SELECT * FROM api_sync_logs
                WHERE channel_id = ?
                ORDER BY created_at DESC
                LIMIT ?
            ''', (channel_id, limit))
        else:
            cursor.execute('''
                SELECT * FROM api_sync_logs
                ORDER BY created_at DESC
                LIMIT ?
            ''', (limit,))

        logs = [dict(row) for row in cursor.fetchall()]
        conn.close()

        return jsonify({
            'status': 'success',
            'count': len(logs),
            'logs': logs
        })

    except Exception as e:
        logger.error(f"[Sync Logs] Error: {e}", exc_info=True)
        return jsonify({'status': 'error', 'message': str(e)}), 500


# ============================================================
# 채널 추출 및 중복 관리 API
# ============================================================

@app.route('/api/channel_manager/extract_from_crawl', methods=['POST'])
def api_extract_channels_from_crawl():
    """크롤링 데이터에서 채널 추출하여 api_channels 테이블에 추가

    크롤링 데이터에서는 channel_id가 없는 것이 정상이므로,
    channel_name을 기준으로 채널을 추출하고 channel_id는 NULL로 저장.
    API 동기화 시 실제 channel_id로 업데이트됨.
    """
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        extracted_count = 0
        skipped_count = 0
        sources = []

        logger.info("[Extract Channels] 크롤링 데이터에서 채널 추출 시작")

        # 1. shorts_rank에서 채널 추출 (최신 subscriber_count 기준)
        cursor.execute('''
            SELECT channel_name, subscriber_count, category, country, thumbnail_url,
                   MAX(crawled_at) as latest_crawled
            FROM shorts_rank
            WHERE channel_name IS NOT NULL AND channel_name != ''
            GROUP BY channel_name
        ''')
        shorts_channels = cursor.fetchall()
        sources.append({
            'source': 'shorts_rank',
            'found': len(shorts_channels)
        })
        logger.debug(f"[Extract Channels] shorts_rank: {len(shorts_channels)}개 채널")

        # 2. videos_rank에서 채널 추출 (최신 subscriber_count 기준)
        cursor.execute('''
            SELECT channel_name, subscriber_count, category, country, thumbnail_url,
                   MAX(crawled_at) as latest_crawled
            FROM videos_rank
            WHERE channel_name IS NOT NULL AND channel_name != ''
            GROUP BY channel_name
        ''')
        videos_channels = cursor.fetchall()
        sources.append({
            'source': 'videos_rank',
            'found': len(videos_channels)
        })
        logger.debug(f"[Extract Channels] videos_rank: {len(videos_channels)}개 채널")

        # 3. channels_rank에서 채널 추출 (profile_url을 thumbnail_url로 사용)
        cursor.execute('''
            SELECT channel_name, subscriber_count, category, country, profile_url as thumbnail_url,
                   MAX(crawled_at) as latest_crawled
            FROM channels_rank
            WHERE channel_name IS NOT NULL AND channel_name != ''
            GROUP BY channel_name
        ''')
        channels_channels = cursor.fetchall()
        sources.append({
            'source': 'channels_rank',
            'found': len(channels_channels)
        })
        logger.debug(f"[Extract Channels] channels_rank: {len(channels_channels)}개 채널")

        # 모든 채널 합치기 (channel_name 기준 중복 제거)
        all_channels = {}

        def add_channel(ch, source_type, thumbnail_url=None):
            """채널 추가 헬퍼 함수"""
            channel_name = ch['channel_name']
            if not channel_name or channel_name.strip() == '':
                return

            # channel_name을 키로 사용 (같은 채널명은 한 번만 추가)
            if channel_name in all_channels:
                return

            all_channels[channel_name] = {
                'title': channel_name,
                'subscriber_count': parse_subscriber_count(ch['subscriber_count']),
                'subscriber_count_raw': ch['subscriber_count'],  # 원본 문자열도 저장
                'category': ch['category'] if 'category' in ch.keys() else '',
                'country': ch['country'] if 'country' in ch.keys() else '',
                'thumbnail_url': thumbnail_url or (ch['thumbnail_url'] if 'thumbnail_url' in ch.keys() else '') or '',
                'source': source_type
            }

        for ch in shorts_channels:
            add_channel(ch, 'shorts')

        for ch in videos_channels:
            add_channel(ch, 'videos')

        for ch in channels_channels:
            add_channel(ch, 'channels', ch.get('profile_url', ''))

        logger.info(f"[Extract Channels] 총 {len(all_channels)}개 고유 채널 수집됨")

        # api_channels에 삽입 (중복 체크: title로)
        for channel_name, ch_data in all_channels.items():
            # 기존 채널 체크 (title로 - channel_id는 NULL일 수 있음)
            cursor.execute('''
                SELECT channel_id, title FROM api_channels
                WHERE title = ?
            ''', (channel_name,))
            existing = cursor.fetchone()

            if existing:
                skipped_count += 1
            else:
                # channel_id는 NULL로 저장 (API 동기화 시 업데이트)
                cursor.execute('''
                    INSERT INTO api_channels (channel_id, title, subscriber_count, thumbnail_url, country, sync_status, crawled_url)
                    VALUES (NULL, ?, ?, ?, ?, 'unsynced', ?)
                ''', (
                    ch_data['title'],
                    ch_data['subscriber_count'],
                    ch_data['thumbnail_url'],
                    ch_data['country'],
                    ch_data['source']
                ))
                extracted_count += 1

        conn.commit()
        conn.close()

        logger.info(f"[Extract Channels] 완료 - 추출: {extracted_count}, 스킵(중복): {skipped_count}")

        return jsonify({
            'status': 'success',
            'message': f'{extracted_count}개 채널 추출 완료 ({skipped_count}개 중복 스킵)',
            'extracted': extracted_count,
            'skipped': skipped_count,
            'sources': sources
        })

    except Exception as e:
        logger.error(f"[Extract Channels] Error: {e}", exc_info=True)
        return jsonify({'status': 'error', 'message': str(e)}), 500


def parse_subscriber_count(value):
    """구독자 수 문자열을 숫자로 변환 (예: '1.2M', '500K')"""
    if not value:
        return 0
    if isinstance(value, int):
        return value

    value = str(value).strip().upper().replace(',', '')

    try:
        if 'M' in value:
            return int(float(value.replace('M', '')) * 1000000)
        elif 'K' in value:
            return int(float(value.replace('K', '')) * 1000)
        elif '만' in value:
            return int(float(value.replace('만', '')) * 10000)
        elif '억' in value:
            return int(float(value.replace('억', '')) * 100000000)
        else:
            return int(float(value))
    except:
        return 0


@app.route('/api/channel_manager/delete_selected', methods=['POST'])
def api_delete_selected_channels():
    """선택한 채널 삭제 API"""
    try:
        data = request.json
        channel_ids = data.get('channel_ids', [])

        if not channel_ids:
            return jsonify({'status': 'error', 'message': '삭제할 채널이 선택되지 않았습니다.'}), 400

        conn = get_db_connection()
        cursor = conn.cursor()

        deleted_count = 0
        for ch_id in channel_ids:
            # channel_id 또는 title로 삭제 (channel_id가 NULL인 경우 title로)
            if ch_id:
                cursor.execute('DELETE FROM api_channels WHERE channel_id = ? OR title = ?', (ch_id, ch_id))
                deleted_count += cursor.rowcount

        conn.commit()
        conn.close()

        logger.info(f"[Delete Channels] {deleted_count}개 채널 삭제 완료")

        return jsonify({
            'status': 'success',
            'message': f'{deleted_count}개 채널이 삭제되었습니다.',
            'deleted': deleted_count
        })

    except Exception as e:
        logger.error(f"[Delete Channels] Error: {e}", exc_info=True)
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/api/channel_manager/sync_unsynced', methods=['POST'])
def api_sync_unsynced_channels():
    """미동기화 채널 전체 동기화 API"""
    try:
        data = request.json or {}
        fetch_videos = data.get('fetch_videos', False)
        video_limit = data.get('video_limit', 50)

        conn = get_db_connection()
        cursor = conn.cursor()

        # 미동기화 채널 조회 (last_synced_at이 NULL인 채널)
        cursor.execute('''
            SELECT channel_id, title FROM api_channels
            WHERE last_synced_at IS NULL OR last_synced_at = ''
            ORDER BY title
        ''')
        unsynced_channels = cursor.fetchall()
        conn.close()

        if not unsynced_channels:
            return jsonify({
                'status': 'success',
                'message': '미동기화 채널이 없습니다.',
                'total': 0,
                'synced': 0
            })

        # 채널 동기화 실행
        total = len(unsynced_channels)
        synced_count = 0
        failed_count = 0
        results = []

        for ch in unsynced_channels:
            channel_id = ch['channel_id']
            channel_name = ch['title']

            try:
                # channel_id가 NULL인 경우 채널명으로 검색하여 ID 획득
                if not channel_id:
                    # YouTube API로 채널 검색
                    search_result = youtube_manager.search_channel_by_name(channel_name)
                    if search_result:
                        channel_id = search_result.get('channel_id')
                        # api_channels 테이블에 channel_id 업데이트
                        conn = get_db_connection()
                        cursor = conn.cursor()
                        cursor.execute('UPDATE api_channels SET channel_id = ? WHERE title = ?', (channel_id, channel_name))
                        conn.commit()
                        conn.close()

                if channel_id:
                    # 채널 동기화
                    sync_result = youtube_manager.sync_channel(channel_id, fetch_videos=fetch_videos, video_limit=video_limit)
                    if sync_result.get('success'):
                        synced_count += 1
                        results.append({'channel': channel_name, 'status': 'success'})
                    else:
                        failed_count += 1
                        results.append({'channel': channel_name, 'status': 'failed', 'error': sync_result.get('error')})
                else:
                    failed_count += 1
                    results.append({'channel': channel_name, 'status': 'failed', 'error': '채널 ID를 찾을 수 없습니다.'})

                # Rate limiting
                import time
                import random
                time.sleep(random.uniform(1, 2))

            except Exception as e:
                failed_count += 1
                results.append({'channel': channel_name, 'status': 'failed', 'error': str(e)})

        logger.info(f"[Sync Unsynced] 완료 - 총: {total}, 성공: {synced_count}, 실패: {failed_count}")

        return jsonify({
            'status': 'success',
            'message': f'{synced_count}개 채널 동기화 완료 (실패: {failed_count}개)',
            'total': total,
            'synced': synced_count,
            'failed': failed_count,
            'results': results[:20]  # 결과는 최대 20개만 반환
        })

    except Exception as e:
        logger.error(f"[Sync Unsynced] Error: {e}", exc_info=True)
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/api/channel_manager/check_duplicates')
def api_check_duplicate_channels():
    """중복 채널 확인"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        # 중복 채널 찾기 (channel_id가 같은 것)
        cursor.execute('''
            SELECT channel_id, COUNT(*) as cnt
            FROM api_channels
            GROUP BY channel_id
            HAVING COUNT(*) > 1
        ''')

        duplicates = cursor.fetchall()
        total_duplicates = sum(row['cnt'] - 1 for row in duplicates)

        conn.close()

        return jsonify({
            'status': 'success',
            'duplicate_groups': len(duplicates),
            'total_duplicates': total_duplicates,
            'details': [dict(row) for row in duplicates]
        })

    except Exception as e:
        logger.error(f"[Check Duplicates] Error: {e}", exc_info=True)
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/api/channel_manager/remove_duplicates', methods=['POST'])
def api_remove_duplicate_channels():
    """중복 채널 삭제 (최신 동기화 일자 우선 보존)"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        # 중복 채널 그룹 찾기
        cursor.execute('''
            SELECT channel_id, COUNT(*) as cnt
            FROM api_channels
            GROUP BY channel_id
            HAVING COUNT(*) > 1
        ''')

        duplicate_groups = cursor.fetchall()
        removed_count = 0

        for group in duplicate_groups:
            ch_id = group['channel_id']

            # 해당 channel_id의 모든 레코드 조회 (last_synced_at 기준 정렬)
            cursor.execute('''
                SELECT rowid, last_synced_at, last_updated
                FROM api_channels
                WHERE channel_id = ?
                ORDER BY
                    CASE WHEN last_synced_at IS NOT NULL THEN 0 ELSE 1 END,
                    last_synced_at DESC,
                    last_updated DESC
            ''', (ch_id,))

            rows = cursor.fetchall()

            # 첫 번째 (최신) 제외하고 삭제
            for row in rows[1:]:
                cursor.execute('DELETE FROM api_channels WHERE rowid = ?', (row['rowid'],))
                removed_count += 1

        conn.commit()
        conn.close()

        logger.info(f"[Remove Duplicates] Removed: {removed_count} duplicate channels")

        return jsonify({
            'status': 'success',
            'message': f'{removed_count}개 중복 채널 삭제 완료',
            'removed': removed_count
        })

    except Exception as e:
        logger.error(f"[Remove Duplicates] Error: {e}", exc_info=True)
        return jsonify({'status': 'error', 'message': str(e)}), 500


# ============================================================
# Hybrid Playlist Export System API
# ============================================================

@app.route('/api/export/auth/status')
def api_export_auth_status():
    """OAuth 인증 상태 확인 API"""
    try:
        logger.debug("[Export Auth] Checking authentication status...")
        from modules.auth_manager import get_auth_manager
        auth_manager = get_auth_manager()
        status = auth_manager.get_auth_status()

        logger.info(f"[Export Auth] Status: is_authenticated={status.get('is_authenticated')}, "
                   f"token_exists={status.get('token_exists')}, token_valid={status.get('token_valid')}")

        return jsonify({
            'status': 'success',
            **status
        })

    except Exception as e:
        logger.error(f"[Export Auth] Status check error: {e}", exc_info=True)
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/api/export/auth/login', methods=['POST'])
def api_export_auth_login():
    """OAuth 로그인 실행 API"""
    try:
        logger.info("[Export Auth] Starting OAuth login flow...")
        from modules.auth_manager import get_auth_manager
        auth_manager = get_auth_manager()
        result = auth_manager.run_oauth_flow()

        if result['success']:
            logger.info("[Export Auth] OAuth login successful")
            return jsonify({
                'status': 'success',
                'message': result['message']
            })
        else:
            logger.warning(f"[Export Auth] OAuth login failed: {result.get('error')}")
            return jsonify({
                'status': 'error',
                'message': result['error']
            }), 400

    except Exception as e:
        logger.error(f"[Export Auth] Login error: {e}", exc_info=True)
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/api/export/auth/logout', methods=['POST'])
def api_export_auth_logout():
    """OAuth 로그아웃 (토큰 삭제) API"""
    try:
        logger.info("[Export Auth] Logging out and revoking token...")
        from modules.auth_manager import get_auth_manager
        auth_manager = get_auth_manager()
        auth_manager.revoke_token()

        logger.info("[Export Auth] Logout successful")
        return jsonify({
            'status': 'success',
            'message': '로그아웃되었습니다.'
        })

    except Exception as e:
        logger.error(f"[Export Auth] Logout error: {e}", exc_info=True)
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/api/export/playlist', methods=['POST'])
def api_export_playlist():
    """
    재생목록 내보내기 API (Hybrid)

    Request Body:
        {
            "method": "url" | "api",
            "video_ids": ["id1", "id2", ...],
            "playlist_title": "새 재생목록 이름" (api 방식, 새로 만들기 시),
            "playlist_id": "PLxxxxxx" (api 방식, 기존 목록에 추가 시),
            "privacy": "private" | "public" | "unlisted" (선택, 기본값: private)
        }

    Returns:
        URL 방식: { urls: [...] }
        API 방식: { playlist_id, added, failed, quota_used, ... }
    """
    try:
        data = request.json
        method = data.get('method', 'url')
        video_ids = data.get('video_ids', [])

        logger.info(f"[Playlist Export] ========== EXPORT REQUEST ==========")
        logger.info(f"[Playlist Export] Method: {method}, Video count: {len(video_ids)}")

        if not video_ids:
            logger.warning("[Playlist Export] No video IDs provided")
            return jsonify({
                'status': 'error',
                'message': '영상 ID가 없습니다.'
            }), 400

        # ========== URL 방식 (Zero-Cost) ==========
        if method == 'url':
            logger.debug(f"[Playlist Export] Using URL method (zero-cost)")
            from modules.youtube_manager import YouTubeManager
            urls = YouTubeManager.generate_playlist_url(video_ids)

            logger.info(f"[Playlist Export] URL 방식 완료: {len(video_ids)}개 영상 -> {len(urls)}개 링크 생성")

            return jsonify({
                'status': 'success',
                'method': 'url',
                'total_videos': len(video_ids),
                'urls': urls
            })

        # ========== API 방식 (자동화) ==========
        elif method == 'api':
            logger.debug(f"[Playlist Export] Using API method (quota-consuming)")
            from modules.auth_manager import get_auth_manager

            # 1. 인증 확인
            logger.debug("[Playlist Export] Step 1: Checking authentication...")
            auth_manager = get_auth_manager()
            auth_status = auth_manager.get_auth_status()

            if not auth_status['is_authenticated']:
                logger.warning("[Playlist Export] Authentication required but not authenticated")
                return jsonify({
                    'status': 'auth_required',
                    'message': 'OAuth 로그인이 필요합니다.'
                }), 401

            # 2. YouTube 서비스 획득
            logger.debug("[Playlist Export] Step 2: Getting authenticated YouTube service...")
            try:
                yt_service = auth_manager.get_authenticated_service()
                logger.debug("[Playlist Export] YouTube service acquired successfully")
            except Exception as e:
                logger.error(f"[Playlist Export] Auth service error: {e}", exc_info=True)
                return jsonify({
                    'status': 'auth_required',
                    'message': f'인증 서비스 오류: {str(e)}'
                }), 401

            # 3. 재생목록 ID 확보
            playlist_id = data.get('playlist_id')
            playlist_title = data.get('playlist_title', f'대시보드 내보내기 {datetime.now().strftime("%Y-%m-%d %H:%M")}')
            privacy = data.get('privacy', 'private')

            logger.info(f"[Playlist Export] Step 3: Playlist config - title='{playlist_title}', "
                       f"privacy={privacy}, existing_id={playlist_id}")

            total_quota = 0

            if not playlist_id:
                # 새 재생목록 생성
                logger.info(f"[Playlist Export] Creating new playlist: '{playlist_title}'")
                create_result = youtube_manager.create_playlist(
                    yt_service,
                    title=playlist_title,
                    description=f'YouTube 데이터 대시보드에서 내보낸 재생목록입니다. ({len(video_ids)}개 영상)',
                    privacy=privacy
                )

                total_quota += create_result['quota_used']
                logger.debug(f"[Playlist Export] Create result: success={create_result['success']}, "
                           f"quota={create_result['quota_used']}")

                if not create_result['success']:
                    logger.error(f"[Playlist Export] Failed to create playlist: {create_result['error']}")
                    return jsonify({
                        'status': 'error',
                        'message': f'재생목록 생성 실패: {create_result["error"]}',
                        'quota_used': total_quota
                    }), 500

                playlist_id = create_result['playlist_id']
                logger.info(f"[Playlist Export] New playlist created: {playlist_id}")

            # 4. 영상 추가
            logger.info(f"[Playlist Export] Step 4: Adding {len(video_ids)} videos to playlist {playlist_id}...")
            batch_result = youtube_manager.add_videos_to_playlist_batch(
                yt_service,
                playlist_id,
                video_ids
            )

            total_quota += batch_result['quota_used']

            logger.info(f"[Playlist Export] ========== EXPORT COMPLETE ==========")
            logger.info(f"[Playlist Export] Results: added={batch_result['added']}, "
                       f"skipped={batch_result['skipped']}, failed={batch_result['failed']}")
            logger.info(f"[Playlist Export] Total quota used: {total_quota}")

            return jsonify({
                'status': 'success',
                'method': 'api',
                'playlist_id': playlist_id,
                'playlist_url': f'https://www.youtube.com/playlist?list={playlist_id}',
                'total': batch_result['total'],
                'added': batch_result['added'],
                'failed': batch_result['failed'],
                'skipped': batch_result['skipped'],
                'quota_used': total_quota,
                'errors': batch_result['errors'][:5] if batch_result['errors'] else []
            })

        else:
            logger.warning(f"[Playlist Export] Unknown method: {method}")
            return jsonify({
                'status': 'error',
                'message': f'알 수 없는 방식입니다: {method}'
            }), 400

    except Exception as e:
        logger.error(f"[Playlist Export] Unexpected error: {e}", exc_info=True)
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/api/export/quota_estimate', methods=['POST'])
def api_export_quota_estimate():
    """
    API 방식 내보내기 Quota 예상치 계산

    Request Body:
        {
            "video_count": int,
            "new_playlist": bool (새 재생목록 생성 여부)
        }

    Returns:
        { estimated_quota: int, warning: str (선택) }
    """
    try:
        data = request.json
        video_count = data.get('video_count', 0)
        new_playlist = data.get('new_playlist', True)

        # Quota 계산
        # - playlists.insert: 50 Quota
        # - playlistItems.insert: 50 Quota per video
        playlist_quota = 50 if new_playlist else 0
        videos_quota = video_count * 50
        total_quota = playlist_quota + videos_quota

        # Quota 현황 확인
        today = quota_tracker.get_today_usage()
        remaining = today['daily_limit'] - today['used']

        response = {
            'status': 'success',
            'estimated_quota': total_quota,
            'breakdown': {
                'playlist_create': playlist_quota,
                'video_add': videos_quota
            },
            'current_usage': today['used'],
            'daily_limit': today['daily_limit'],
            'remaining': remaining
        }

        # 경고 메시지
        if total_quota > remaining:
            response['warning'] = f'예상 Quota({total_quota:,})가 남은 Quota({remaining:,})를 초과합니다!'
        elif total_quota > remaining * 0.5:
            response['warning'] = f'주의: 이 작업으로 남은 Quota의 50% 이상을 사용합니다.'

        return jsonify(response)

    except Exception as e:
        logger.error(f"[Playlist Export] Quota estimate error: {e}")
        return jsonify({'status': 'error', 'message': str(e)}), 500


# ============================================================
# 메인 실행
# ============================================================


@app.route('/api/export/csv')
def api_export_csv():
    """데이터 내보내기 API (CSV/Excel/TSV)"""
    try:
        data_type = request.args.get('type', 'videos')  # videos, shorts, channels, all
        export_mode = request.args.get('mode', 'filtered')  # filtered, all
        file_format = request.args.get('format', 'csv') # csv, excel, tsv
        
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
             category = CATEGORY_MAP[category]

        # 쿼리 생성
        conn = get_db_connection()
        
        # pandas read_sql_query 사용을 위해 params 준비
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

        # pandas로 데이터 로드
        df = pd.read_sql_query(query, conn, params=params)
        conn.close()

        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        
        if file_format == 'excel':
            output = io.BytesIO()
            filename = f"youtube_data_{export_mode}_{data_type}_{timestamp}.xlsx"
            with pd.ExcelWriter(output, engine='openpyxl') as writer:
                df.to_excel(writer, index=False, sheet_name='Sheet1')
            
            output.seek(0)
            return Response(
                output,
                mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                headers={"Content-disposition": f"attachment; filename={filename}"}
            )
            
        elif file_format == 'tsv':
            # TSV: Tab Separated (for cell-based copy)
            output = io.StringIO()
            # pandas to_csv with sep='\t'
            df.to_csv(output, sep='\t', index=False)
            result = output.getvalue()
            
            # 클립보드 복사용은 text/plain으로 반환하면 JS에서 text()로 읽기 편함
            return Response(result, mimetype="text/plain; charset=utf-8")
            
        else:
            # CSV (default)
            output = io.StringIO()
            filename = f"youtube_data_{export_mode}_{data_type}_{timestamp}.csv"
            # BOM for Excel compatibility with Korean
            output.write('\ufeff')
            df.to_csv(output, index=False)
            
            return Response(
                output.getvalue(),
                mimetype="text/csv",
                headers={"Content-disposition": f"attachment; filename={filename}"}
            )

    except Exception as e:
        logger.error(f"[Export Data] Error: {e}", exc_info=True)
        return jsonify({'status': 'error', 'message': str(e)}), 500


if __name__ == '__main__':
    # 필요한 디렉토리 생성
    os.makedirs('output/db', exist_ok=True)

    logger.info("=" * 60)
    logger.info("YouTube DB Dashboard Starting...")
    logger.info(f"Port: 5001")
    logger.info(f"Database: {DB_PATH}")
    logger.info("=" * 60)

    print("\n" + "=" * 60)
    print(" * YouTube DB Dashboard")
    print(" * Running on http://127.0.0.1:5001")
    print(" * Press CTRL+C to quit")
    print("=" * 60 + "\n")

    app.run(host='0.0.0.0', port=5001, debug=False, use_reloader=False)


