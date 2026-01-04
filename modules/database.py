"""
SQLite 데이터베이스 핸들러
크롤링 데이터를 DB에 저장하고 관리
"""
import sqlite3
import os
from datetime import datetime
from logger_config import setup_logger
from modules.utils import parse_count_string

logger = setup_logger('database')


class DatabaseHandler:
    """
    SQLite 데이터베이스 핸들러
    - 영상 정보 저장
    - 중복 데이터 업데이트 (Upsert)
    - 조회 기능
    """

    def __init__(self, db_path='output/db/youtube_data.db'):
        """
        데이터베이스 초기화

        Args:
            db_path (str): DB 파일 경로
        """
        # 디렉토리 생성
        os.makedirs(os.path.dirname(db_path), exist_ok=True)

        self.db_path = db_path
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row  # dict 형태로 결과 반환

        self._init_db()
        self._migrate_db()  # 기존 테이블에 누락된 컬럼 추가
        logger.info(f"Database initialized: {db_path}")

    def _init_db(self):
        """데이터베이스 테이블 초기화 (3개 테이블 분리 구조)"""
        cursor = self.conn.cursor()

        # 1. 쇼츠 랭킹 테이블 (shorts_rank)
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS shorts_rank (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                video_id TEXT,
                title TEXT,
                thumbnail_url TEXT,
                channel_name TEXT,
                channel_id TEXT,
                views INTEGER DEFAULT 0,
                likes INTEGER DEFAULT 0,
                rank INTEGER,
                rank_change TEXT,
                upload_date TEXT,
                subscriber_count TEXT,
                tags TEXT,
                category TEXT,
                country TEXT,
                period TEXT,
                crawled_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(video_id, category, country, period, crawled_at)
            )
        ''')

        # 2. 일반 영상 랭킹 테이블 (videos_rank)
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS videos_rank (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                video_id TEXT,
                title TEXT,
                thumbnail_url TEXT,
                channel_name TEXT,
                channel_id TEXT,
                views INTEGER DEFAULT 0,
                likes INTEGER DEFAULT 0,
                rank INTEGER,
                rank_change TEXT,
                upload_date TEXT,
                subscriber_count TEXT,
                tags TEXT,
                category TEXT,
                country TEXT,
                period TEXT,
                crawled_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(video_id, category, country, period, crawled_at)
            )
        ''')

        # 3. 채널 랭킹 테이블 (channels_rank)
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS channels_rank (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                channel_id TEXT,
                channel_name TEXT,
                profile_url TEXT,
                channel_url TEXT,
                rank INTEGER,
                rank_change TEXT,
                score_1 INTEGER DEFAULT 0,
                score_2 INTEGER DEFAULT 0,
                video_count INTEGER DEFAULT 0,
                tags TEXT,
                category TEXT,
                country TEXT,
                period TEXT,
                ranking_type TEXT,
                crawled_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(channel_id, category, country, period, crawled_at)
            )
        ''')

        # 4. 크롤링 히스토리 테이블 (기존 유지)
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS crawl_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                target_type TEXT,
                category TEXT,
                country TEXT,
                period TEXT,
                item_count INTEGER,
                success BOOLEAN,
                error_message TEXT,
                crawled_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        # 5. 자막 정보 테이블 (기존 유지)
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS transcripts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                video_id TEXT,
                language TEXT,
                transcript_text TEXT,
                extracted_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        # 6. API 채널 데이터 테이블 (YouTube API 연동용) - Phase 1.1 확장
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS api_channels (
                channel_id TEXT PRIMARY KEY,
                title TEXT,
                thumbnail_url TEXT,
                subscriber_count INTEGER,
                view_count INTEGER,
                video_count INTEGER,
                uploads_playlist_id TEXT,
                crawled_url TEXT,
                last_updated DATETIME DEFAULT CURRENT_TIMESTAMP,
                last_synced_at DATETIME,
                sync_status TEXT,
                collected_video_count INTEGER DEFAULT 0,
                latest_video_upload_date DATE
            )
        ''')

        # 7. API 영상 데이터 테이블 (영상/쇼츠 구분)
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS api_videos (
                video_id TEXT PRIMARY KEY,
                channel_id TEXT,
                title TEXT,
                published_at DATETIME,
                duration_iso TEXT,
                duration_sec INTEGER,
                video_type TEXT,
                view_count INTEGER,
                like_count INTEGER,
                tags TEXT,
                last_updated DATETIME DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(channel_id) REFERENCES api_channels(channel_id)
            )
        ''')

        # 8. Quota 로그 테이블 (API 사용량 추적)
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS quota_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                request_date DATE,
                endpoint TEXT,
                units_used INTEGER,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        # 9. API 동기화 로그 테이블 (Phase 1.2 신설)
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS api_sync_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                channel_id TEXT,
                channel_name TEXT,
                status TEXT,
                videos_fetched INTEGER DEFAULT 0,
                used_quota INTEGER DEFAULT 0,
                error_message TEXT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        # 10. 모니터링 재생목록 테이블 (Playlist-Driven Channel Discovery)
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS monitored_playlists (
                playlist_id TEXT PRIMARY KEY,
                title TEXT,
                thumbnail_url TEXT,
                item_count INTEGER DEFAULT 0,
                channel_title TEXT,
                last_synced_at DATETIME,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        # 11. 레거시 videos 테이블 (기존 데이터 호환성 유지)
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS videos (
                video_id TEXT PRIMARY KEY,
                title TEXT,
                channel_name TEXT,
                channel_id TEXT,
                views INTEGER DEFAULT 0,
                likes INTEGER DEFAULT 0,
                upload_date TEXT,
                subscriber_count TEXT,
                rank INTEGER,
                rank_change TEXT,
                category TEXT,
                country TEXT,
                period TEXT,
                target_type TEXT,
                video_url TEXT,
                crawled_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        # 인덱스 생성
        # Shorts 인덱스
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_shorts_category ON shorts_rank(category)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_shorts_crawled_at ON shorts_rank(crawled_at)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_shorts_video_id ON shorts_rank(video_id)')

        # Videos 인덱스
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_videos_rank_category ON videos_rank(category)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_videos_rank_crawled_at ON videos_rank(crawled_at)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_videos_rank_video_id ON videos_rank(video_id)')

        # Channels 인덱스
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_channels_category ON channels_rank(category)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_channels_crawled_at ON channels_rank(crawled_at)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_channels_channel_id ON channels_rank(channel_id)')

        # 기존 테이블 인덱스
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_videos_category ON videos(category)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_videos_crawled_at ON videos(crawled_at)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_crawl_history_crawled_at ON crawl_history(crawled_at)')

        # API 테이블 인덱스
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_api_channels_title ON api_channels(title)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_api_channels_sync_status ON api_channels(sync_status)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_api_channels_last_synced ON api_channels(last_synced_at)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_api_videos_channel_id ON api_videos(channel_id)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_api_videos_video_type ON api_videos(video_type)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_api_videos_published ON api_videos(published_at)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_quota_logs_date ON quota_logs(request_date)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_api_sync_logs_channel ON api_sync_logs(channel_id)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_api_sync_logs_created ON api_sync_logs(created_at)')

        # monitored_playlists 인덱스
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_playlists_last_synced ON monitored_playlists(last_synced_at)')

        self.conn.commit()
        logger.debug("Database tables and indexes created (extended structure with API tables)")

    def _migrate_db(self):
        """기존 테이블에 누락된 컬럼 추가 (마이그레이션)"""
        cursor = self.conn.cursor()

        # 마이그레이션이 필요한 테이블과 컬럼 목록
        migrations = [
            ('shorts_rank', 'tags', 'TEXT'),
            ('shorts_rank', 'country', 'TEXT'),
            ('shorts_rank', 'period', 'TEXT'),
            ('videos_rank', 'tags', 'TEXT'),
            ('videos_rank', 'country', 'TEXT'),
            ('videos_rank', 'period', 'TEXT'),
            ('channels_rank', 'tags', 'TEXT'),
            ('channels_rank', 'country', 'TEXT'),
            ('channels_rank', 'period', 'TEXT'),
            ('channels_rank', 'channel_url', 'TEXT'),  # 채널 URL (ID 추출용)
            ('channels_rank', 'ranking_type', 'TEXT'),  # 랭킹 타입 (인기순위/구독자급상승)

            # Phase 1.1: api_channels 테이블 확장 컬럼
            ('api_channels', 'last_synced_at', 'DATETIME'),
            ('api_channels', 'sync_status', 'TEXT'),
            ('api_channels', 'collected_video_count', 'INTEGER DEFAULT 0'),
            ('api_channels', 'latest_video_upload_date', 'DATE'),

            # PLAN.md - api_videos 테이블 Deep Data 확장 (2025-12-10)
            ('api_videos', 'video_link', 'TEXT'),  # https://youtu.be/...
            ('api_videos', 'channel_name', 'TEXT'),  # 채널명 (Denormalization)
            ('api_videos', 'category_id', 'TEXT'),  # 카테고리 ID
            ('api_videos', 'category_name', 'TEXT'),  # 카테고리명
            ('api_videos', 'thumbnail_url', 'TEXT'),  # 썸네일 고화질 링크
            ('api_videos', 'thumbnail_path', 'TEXT'),  # 썸네일 로컬 저장 경로
            ('api_videos', 'description', 'TEXT'),  # 영상 설명 (AI 분석용)
            ('api_videos', 'comment_count', 'INTEGER'),  # 댓글 수

            # 파생/계산 데이터 - AI 분석 기초
            ('api_videos', 'collected_at', 'DATETIME'),  # 수집 시점
            ('api_videos', 'days_since_upload', 'INTEGER'),  # 업로드 경과일
            ('api_videos', 'view_sub_ratio', 'REAL'),  # 구독자 대비 조회수
            ('api_videos', 'like_view_ratio', 'REAL'),  # 조회수 대비 좋아요
            ('api_videos', 'comment_view_ratio', 'REAL'),  # 조회수 대비 댓글
            ('api_videos', 'daily_avg_views', 'REAL'),  # 일평균 조회수

            # AI 활용 예비 컬럼
            ('api_videos', 'transcript_txt', 'TEXT'),  # 대본 텍스트
            ('api_videos', 'is_ai_generated', 'BOOLEAN'),  # AI 생성 여부
            ('api_videos', 'analysis_summary', 'TEXT'),  # AI 분석 요약

            # PLAN.md - api_channels 테이블 Deep Data 확장 (2025-12-10)
            ('api_channels', 'channel_handle', 'TEXT'),  # 채널 핸들 (@name)
            ('api_channels', 'channel_link', 'TEXT'),  # 채널 URL
            ('api_channels', 'country', 'TEXT'),  # 국가 코드
            ('api_channels', 'description', 'TEXT'),  # 채널 설명
            ('api_channels', 'published_at', 'DATETIME'),  # 개설일
            ('api_channels', 'keywords', 'TEXT'),  # 채널 키워드

            # 파생/계산 데이터
            ('api_channels', 'days_since_published', 'INTEGER'),  # 개설 경과일
            ('api_channels', 'avg_views_recent', 'REAL'),  # 최근 영상 평균 조회수
            ('api_channels', 'video_upload_cycle', 'REAL'),  # 평균 업로드 주기 (일)
            ('api_channels', 'performance_index', 'REAL'),  # 채널 활성도 지수
            ('api_channels', 'last_deep_sync_at', 'DATETIME'),  # 마지막 정밀 수집일

            # Playlist-Driven Channel Discovery (개선 #40)
            ('api_channels', 'playlist_source', 'TEXT'),  # 채널을 추출한 재생목록 ID

            # PLAN.md Section 3.2: Robust Playlist Extraction (2025-12-11)
            ('api_channels', 'discovery_video_id', 'TEXT'),  # 채널 발견에 사용된 영상 ID
            ('api_channels', 'discovery_video_url', 'TEXT'),  # 채널 발견에 사용된 영상 URL
        ]

        for table, column, col_type in migrations:
            try:
                # 컬럼 존재 여부 확인
                cursor.execute(f"PRAGMA table_info({table})")
                columns = [row[1] for row in cursor.fetchall()]

                if column not in columns:
                    cursor.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}")
                    logger.info(f"Migration: Added column '{column}' to table '{table}'")
            except Exception as e:
                logger.debug(f"Migration check for {table}.{column}: {e}")

        self.conn.commit()

    def insert_dataframe(self, df, category, country, period, target_type):
        """
        Pandas DataFrame을 DB에 저장 (타겟 타입별 테이블 분리)

        Args:
            df (pd.DataFrame): 크롤링 데이터
            category (str): 카테고리
            country (str): 국가
            period (str): 기간
            target_type (str): 타겟 타입 (shorts, video, channel)

        Returns:
            int: 저장된 레코드 수
        """
        if df is None or len(df) == 0:
            logger.warning("Empty DataFrame provided")
            return 0

        cursor = self.conn.cursor()
        inserted_count = 0

        # 타겟 타입에 따라 적절한 테이블 선택
        if target_type == 'shorts':
            inserted_count = self._insert_shorts(cursor, df, category, country, period)
        elif target_type == 'video':
            inserted_count = self._insert_videos(cursor, df, category, country, period)
        elif target_type == 'channel':
            inserted_count = self._insert_channels(cursor, df, category, country, period)
        else:
            logger.warning(f"Unknown target_type: {target_type}, using legacy table")
            inserted_count = self._insert_legacy(cursor, df, category, country, period, target_type)

        self.conn.commit()
        logger.info(f"Inserted/Updated {inserted_count} items to {target_type} table")
        return inserted_count

    def _insert_shorts(self, cursor, df, category, country, period):
        """
        쇼츠 데이터를 shorts_rank 테이블에 저장 (Upsert - 중복 시 최신 데이터로 업데이트)
        중복 기준: video_id + category + country + period
        """
        count = 0
        for idx, (_, row) in enumerate(df.iterrows(), 1):
            try:
                # Video ID가 없어도 순위 기반으로 고유 ID 생성
                video_id = row.get('Video ID', '')
                if not video_id or video_id == 'N/A':
                    # Rank + Title 해시로 대체 ID 생성
                    rank = row.get('Rank', idx)
                    title = row.get('Video Title', '')[:50]
                    video_id = f"rank_{rank}_{hash(title) % 100000}"

                views = parse_count_string(row.get('Views', 0))
                likes = parse_count_string(row.get('Likes', 0))
                crawled_at = datetime.now().isoformat()

                # 중복 체크: 같은 video_id + category + country + period 존재 시 업데이트
                cursor.execute('''
                    SELECT id, crawled_at FROM shorts_rank
                    WHERE video_id = ? AND category = ? AND country = ? AND period = ?
                    ORDER BY crawled_at DESC LIMIT 1
                ''', (video_id, category, country, period))

                existing = cursor.fetchone()

                if existing:
                    # 중복 데이터 발견 - 최신 데이터로 업데이트
                    cursor.execute('''
                        UPDATE shorts_rank SET
                            title = ?, thumbnail_url = ?, channel_name = ?, channel_id = ?,
                            views = ?, likes = ?, rank = ?, rank_change = ?, upload_date = ?,
                            subscriber_count = ?, tags = ?, updated_at = ?, crawled_at = ?
                        WHERE id = ?
                    ''', (
                        row.get('Video Title', 'N/A'),
                        row.get('Thumbnail', 'N/A'),
                        row.get('Channel Name', 'N/A'),
                        row.get('Channel ID', 'N/A'),
                        views,
                        likes,
                        row.get('Rank', 0),
                        row.get('Rank Change', 'N/A'),
                        row.get('Upload Date', 'N/A'),
                        row.get('Subscribers', ''),
                        row.get('Tags', ''),
                        crawled_at,
                        crawled_at,
                        existing['id']
                    ))
                    logger.debug(f"Updated existing short: {video_id}")
                else:
                    # 새 데이터 삽입
                    cursor.execute('''
                        INSERT INTO shorts_rank (
                            video_id, title, thumbnail_url, channel_name, channel_id,
                            views, likes, rank, rank_change, upload_date, subscriber_count, tags,
                            category, country, period, crawled_at, updated_at
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ''', (
                        video_id,
                        row.get('Video Title', 'N/A'),
                        row.get('Thumbnail', 'N/A'),
                        row.get('Channel Name', 'N/A'),
                        row.get('Channel ID', 'N/A'),
                        views,
                        likes,
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
                    logger.debug(f"Inserted new short: {video_id}")

                count += 1

                # Batch Commit: 10개마다 commit (PLAN.md 3.2 - 중단 시 데이터 보존)
                if count % 10 == 0:
                    self.conn.commit()
                    logger.debug(f"Batch committed: {count} items saved")

            except Exception as e:
                logger.error(f"Failed to upsert short: {video_id}, Error: {e}")
                continue
        return count

    def _insert_videos(self, cursor, df, category, country, period):
        """
        비디오 데이터를 videos_rank 테이블에 저장 (Upsert - 중복 시 최신 데이터로 업데이트)
        중복 기준: video_id + category + country + period
        """
        count = 0
        for idx, (_, row) in enumerate(df.iterrows(), 1):
            try:
                # Video ID가 없어도 순위 기반으로 고유 ID 생성
                video_id = row.get('Video ID', '')
                if not video_id or video_id == 'N/A':
                    # Rank + Title 해시로 대체 ID 생성
                    rank = row.get('Rank', idx)
                    title = row.get('Video Title', '')[:50]
                    video_id = f"rank_{rank}_{hash(title) % 100000}"

                views = parse_count_string(row.get('Views', 0))
                likes = parse_count_string(row.get('Likes', 0))
                crawled_at = datetime.now().isoformat()

                # 중복 체크: 같은 video_id + category + country + period 존재 시 업데이트
                cursor.execute('''
                    SELECT id, crawled_at FROM videos_rank
                    WHERE video_id = ? AND category = ? AND country = ? AND period = ?
                    ORDER BY crawled_at DESC LIMIT 1
                ''', (video_id, category, country, period))

                existing = cursor.fetchone()

                if existing:
                    # 중복 데이터 발견 - 최신 데이터로 업데이트
                    cursor.execute('''
                        UPDATE videos_rank SET
                            title = ?, thumbnail_url = ?, channel_name = ?, channel_id = ?,
                            views = ?, likes = ?, rank = ?, rank_change = ?, upload_date = ?,
                            subscriber_count = ?, tags = ?, updated_at = ?, crawled_at = ?
                        WHERE id = ?
                    ''', (
                        row.get('Video Title', 'N/A'),
                        row.get('Thumbnail', 'N/A'),
                        row.get('Channel Name', 'N/A'),
                        row.get('Channel ID', 'N/A'),
                        views,
                        likes,
                        row.get('Rank', 0),
                        row.get('Rank Change', 'N/A'),
                        row.get('Upload Date', 'N/A'),
                        row.get('Subscribers', ''),
                        row.get('Tags', ''),
                        crawled_at,
                        crawled_at,
                        existing['id']
                    ))
                    logger.debug(f"Updated existing video: {video_id}")
                else:
                    # 새 데이터 삽입
                    cursor.execute('''
                        INSERT INTO videos_rank (
                            video_id, title, thumbnail_url, channel_name, channel_id,
                            views, likes, rank, rank_change, upload_date, subscriber_count, tags,
                            category, country, period, crawled_at, updated_at
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ''', (
                        video_id,
                        row.get('Video Title', 'N/A'),
                        row.get('Thumbnail', 'N/A'),
                        row.get('Channel Name', 'N/A'),
                        row.get('Channel ID', 'N/A'),
                        views,
                        likes,
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
                    logger.debug(f"Inserted new video: {video_id}")

                count += 1

                # Batch Commit: 10개마다 commit
                if count % 10 == 0:
                    self.conn.commit()
                    logger.debug(f"Batch committed: {count} items saved")

            except Exception as e:
                logger.error(f"Failed to upsert video: {video_id}, Error: {e}")
                continue
        return count

    def _insert_channels(self, cursor, df, category, country, period):
        """
        채널 데이터를 channels_rank 테이블에 저장 (Upsert - 중복 시 최신 데이터로 업데이트)
        중복 기준: channel_id + category + country + period + ranking_type
        """
        count = 0
        for idx, (_, row) in enumerate(df.iterrows(), 1):
            try:
                # Channel ID가 있으면 사용, 없으면 채널명 기반 ID 생성
                channel_id = row.get('Channel ID')
                if not channel_id or channel_id == 'N/A':
                    # Rank + Channel Name 해시로 대체 ID 생성
                    rank = row.get('Rank', idx)
                    channel_name = row.get('Channel Name', '')[:50]
                    channel_id = f"rank_{rank}_{hash(channel_name) % 100000}"

                # Score 1/2 또는 Total Subscribers/New Subscribers 지원
                score_1 = parse_count_string(row.get('Score 1', 0))
                if score_1 == 0:
                    score_1 = parse_count_string(row.get('Total Subscribers', 0))
                if score_1 == 0:
                    score_1 = parse_count_string(row.get('Views', 0))

                score_2 = parse_count_string(row.get('Score 2', 0))
                if score_2 == 0:
                    score_2 = parse_count_string(row.get('New Subscribers', 0))
                if score_2 == 0:
                    score_2 = parse_count_string(row.get('Likes', 0))

                video_count = int(row.get('Video Count', 0)) if row.get('Video Count') else 0
                ranking_type = row.get('Ranking Type', '')
                crawled_at = datetime.now().isoformat()

                # 중복 체크: 같은 channel_id + category + country + period + ranking_type 존재 시 업데이트
                cursor.execute('''
                    SELECT id, crawled_at FROM channels_rank
                    WHERE channel_id = ? AND category = ? AND country = ? AND period = ? AND ranking_type = ?
                    ORDER BY crawled_at DESC LIMIT 1
                ''', (channel_id, category, country, period, ranking_type))

                existing = cursor.fetchone()

                if existing:
                    # 중복 데이터 발견 - 최신 데이터로 업데이트
                    cursor.execute('''
                        UPDATE channels_rank SET
                            channel_name = ?, profile_url = ?, channel_url = ?,
                            rank = ?, rank_change = ?,
                            subscriber_count = ?, total_views = ?, video_count = ?, tags = ?,
                            updated_at = ?, crawled_at = ?
                        WHERE id = ?
                    ''', (
                        row.get('Channel Name', 'N/A'),
                        row.get('Profile Image', 'N/A'),
                        row.get('Channel URL', ''),
                        row.get('Rank', 0),
                        row.get('Rank Change', 'N/A'),
                        score_1,
                        score_2,
                        video_count,
                        row.get('Tags', ''),
                        crawled_at,
                        crawled_at,
                        existing['id']
                    ))
                    logger.debug(f"Updated existing channel: {channel_id}")
                else:
                    # 새 데이터 삽입
                    cursor.execute('''
                        INSERT INTO channels_rank (
                            channel_id, channel_name, profile_url, channel_url,
                            rank, rank_change,
                            subscriber_count, total_views, video_count, tags,
                            category, country, period, ranking_type, crawled_at, updated_at
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ''', (
                        channel_id,
                        row.get('Channel Name', 'N/A'),
                        row.get('Profile Image', 'N/A'),
                        row.get('Channel URL', ''),
                        row.get('Rank', 0),
                        row.get('Rank Change', 'N/A'),
                        score_1,
                        score_2,
                        video_count,
                        row.get('Tags', ''),
                        category,
                        country,
                        period,
                        ranking_type,
                        crawled_at,
                        crawled_at
                    ))
                    logger.debug(f"Inserted new channel: {channel_id}")

                count += 1

                # Batch Commit: 10개마다 commit
                if count % 10 == 0:
                    self.conn.commit()
                    logger.debug(f"Batch committed: {count} items saved")

            except Exception as e:
                logger.error(f"Failed to insert channel: {channel_id}, Error: {e}")
                continue
        return count

    def _insert_legacy(self, cursor, df, category, country, period, target_type):
        """레거시 videos 테이블에 저장 (하위 호환성)"""
        count = 0
        for _, row in df.iterrows():
            try:
                video_id = row.get('Video ID')
                if not video_id or video_id == 'N/A':
                    continue

                views = parse_count_string(row.get('Views', 0))
                likes = parse_count_string(row.get('Likes', 0))

                cursor.execute('''
                    INSERT INTO videos (
                        video_id, title, channel_name, channel_id,
                        views, likes, upload_date, subscriber_count,
                        rank, rank_change,
                        category, country, period, target_type, video_url,
                        crawled_at, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(video_id) DO UPDATE SET
                        views = excluded.views,
                        likes = excluded.likes,
                        upload_date = excluded.upload_date,
                        subscriber_count = excluded.subscriber_count,
                        rank = excluded.rank,
                        rank_change = excluded.rank_change,
                        updated_at = excluded.updated_at
                ''', (
                    video_id,
                    row.get('Video Title', 'N/A'),
                    row.get('Channel Name', 'N/A'),
                    row.get('Channel ID', 'N/A'),
                    views,
                    likes,
                    row.get('Upload Date', 'N/A'),
                    row.get('Subscriber Count', 'N/A'),
                    row.get('Rank', 0),
                    row.get('Rank Change', 'N/A'),
                    category,
                    country,
                    period,
                    target_type,
                    row.get('Video URL', 'N/A'),
                    datetime.now().isoformat(),
                    datetime.now().isoformat()
                ))
                count += 1
            except Exception as e:
                logger.error(f"Failed to insert legacy video: {video_id}, Error: {e}")
                continue
        return count

    def log_crawl_history(self, target_type, category, country, period, item_count, success=True, error_message=None):
        """
        크롤링 히스토리 기록

        Args:
            target_type (str): 타겟 타입
            category (str): 카테고리
            country (str): 국가
            period (str): 기간
            item_count (int): 수집된 아이템 수
            success (bool): 성공 여부
            error_message (str): 에러 메시지 (실패 시)
        """
        cursor = self.conn.cursor()

        cursor.execute('''
            INSERT INTO crawl_history (
                target_type, category, country, period,
                item_count, success, error_message, crawled_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            target_type,
            category,
            country,
            period,
            item_count,
            success,
            error_message,
            datetime.now().isoformat()
        ))

        self.conn.commit()
        logger.debug(f"Crawl history logged: {category}, Success: {success}")

    def get_videos_by_category(self, category, limit=100):
        """
        카테고리별 영상 조회

        Args:
            category (str): 카테고리
            limit (int): 조회 개수

        Returns:
            list: 영상 데이터 리스트
        """
        cursor = self.conn.cursor()

        cursor.execute('''
            SELECT * FROM videos
            WHERE category = ?
            ORDER BY crawled_at DESC
            LIMIT ?
        ''', (category, limit))

        rows = cursor.fetchall()
        return [dict(row) for row in rows]

    def get_recent_videos(self, limit=100):
        """
        최근 크롤링된 영상 조회

        Args:
            limit (int): 조회 개수

        Returns:
            list: 영상 데이터 리스트
        """
        cursor = self.conn.cursor()

        cursor.execute('''
            SELECT * FROM videos
            ORDER BY crawled_at DESC
            LIMIT ?
        ''', (limit,))

        rows = cursor.fetchall()
        return [dict(row) for row in rows]

    def get_crawl_history(self, limit=50):
        """
        크롤링 히스토리 조회

        Args:
            limit (int): 조회 개수

        Returns:
            list: 히스토리 데이터 리스트
        """
        cursor = self.conn.cursor()

        cursor.execute('''
            SELECT * FROM crawl_history
            ORDER BY crawled_at DESC
            LIMIT ?
        ''', (limit,))

        rows = cursor.fetchall()
        return [dict(row) for row in rows]

    def save_transcript(self, video_id, language, transcript_text):
        """
        자막 정보 저장

        Args:
            video_id (str): 비디오 ID
            language (str): 언어 코드
            transcript_text (str): 자막 텍스트

        Returns:
            bool: 저장 성공 여부
        """
        try:
            cursor = self.conn.cursor()

            cursor.execute('''
                INSERT INTO transcripts (video_id, language, transcript_text, extracted_at)
                VALUES (?, ?, ?, ?)
            ''', (
                video_id,
                language,
                transcript_text,
                datetime.now().isoformat()
            ))

            self.conn.commit()
            logger.debug(f"Transcript saved: {video_id}, Language: {language}")
            return True

        except Exception as e:
            logger.error(f"Failed to save transcript: {video_id}, Error: {e}")
            return False

    def get_statistics(self):
        """
        데이터베이스 통계 조회 (3개 테이블 통합)

        Returns:
            dict: 통계 정보
        """
        cursor = self.conn.cursor()

        # 쇼츠 수
        cursor.execute('SELECT COUNT(*) FROM shorts_rank')
        total_shorts = cursor.fetchone()[0]

        # 일반 영상 수
        cursor.execute('SELECT COUNT(*) FROM videos_rank')
        total_videos = cursor.fetchone()[0]

        # 채널 수
        cursor.execute('SELECT COUNT(*) FROM channels_rank')
        total_channels = cursor.fetchone()[0]

        # 레거시 테이블 (하위 호환)
        cursor.execute('SELECT COUNT(*) FROM videos')
        total_legacy = cursor.fetchone()[0]

        # 총 크롤링 횟수
        cursor.execute('SELECT COUNT(*) FROM crawl_history')
        total_crawls = cursor.fetchone()[0]

        # 카테고리별 쇼츠 수
        cursor.execute('''
            SELECT category, COUNT(*) as count
            FROM shorts_rank
            GROUP BY category
            ORDER BY count DESC
        ''')
        shorts_by_category = cursor.fetchall()

        # 카테고리별 일반 영상 수
        cursor.execute('''
            SELECT category, COUNT(*) as count
            FROM videos_rank
            GROUP BY category
            ORDER BY count DESC
        ''')
        videos_by_category = cursor.fetchall()

        # 카테고리별 채널 수
        cursor.execute('''
            SELECT category, COUNT(*) as count
            FROM channels_rank
            GROUP BY category
            ORDER BY count DESC
        ''')
        channels_by_category = cursor.fetchall()

        return {
            'total_shorts': total_shorts,
            'total_videos': total_videos,
            'total_channels': total_channels,
            'total_legacy': total_legacy,
            'total_items': total_shorts + total_videos + total_channels,
            'total_crawls': total_crawls,
            'shorts_by_category': [dict(row) for row in shorts_by_category],
            'videos_by_category': [dict(row) for row in videos_by_category],
            'channels_by_category': [dict(row) for row in channels_by_category]
        }

    # ========== 수집 이력 조회 메서드 (기간별 필터링) ==========

    def get_crawl_history_by_period(self, period_type='daily', start_date=None, end_date=None):
        """
        기간별 수집 이력 조회

        Args:
            period_type: 'daily' (일간), 'weekly' (주간), 'monthly' (월간), 'custom' (기간설정)
            start_date: 시작 날짜 (YYYY-MM-DD) - custom일 때 사용
            end_date: 종료 날짜 (YYYY-MM-DD) - custom일 때 사용

        Returns:
            list: 수집 이력 리스트
        """
        cursor = self.conn.cursor()
        today = datetime.now().date()

        if period_type == 'daily':
            # 오늘 수집 이력
            date_filter = today.isoformat()
            cursor.execute('''
                SELECT * FROM crawl_history
                WHERE DATE(crawled_at) = ?
                ORDER BY crawled_at DESC
            ''', (date_filter,))
        elif period_type == 'weekly':
            # 최근 7일
            from datetime import timedelta
            start = (today - timedelta(days=7)).isoformat()
            cursor.execute('''
                SELECT * FROM crawl_history
                WHERE DATE(crawled_at) >= ?
                ORDER BY crawled_at DESC
            ''', (start,))
        elif period_type == 'monthly':
            # 최근 30일
            from datetime import timedelta
            start = (today - timedelta(days=30)).isoformat()
            cursor.execute('''
                SELECT * FROM crawl_history
                WHERE DATE(crawled_at) >= ?
                ORDER BY crawled_at DESC
            ''', (start,))
        elif period_type == 'custom' and start_date and end_date:
            # 사용자 지정 기간
            cursor.execute('''
                SELECT * FROM crawl_history
                WHERE DATE(crawled_at) BETWEEN ? AND ?
                ORDER BY crawled_at DESC
            ''', (start_date, end_date))
        else:
            # 전체
            cursor.execute('''
                SELECT * FROM crawl_history
                ORDER BY crawled_at DESC
                LIMIT 500
            ''')

        rows = cursor.fetchall()
        return [dict(row) for row in rows]

    def get_collection_status(self, period_type='daily', start_date=None, end_date=None):
        """
        기간별 카테고리 수집 현황 조회

        Returns:
            dict: {
                'shorts': {'전체': True, '음악': False, ...},
                'video': {'전체': True, '음악': True, ...},
                'channel_popular': {...},
                'channel_growth': {...}
            }
        """
        from config_mappings import CATEGORIES
        from config import Config

        cursor = self.conn.cursor()
        today = datetime.now().date()

        # 기간 조건 설정
        if period_type == 'daily':
            date_condition = f"DATE(crawled_at) = '{today.isoformat()}'"
        elif period_type == 'weekly':
            from datetime import timedelta
            start = (today - timedelta(days=7)).isoformat()
            date_condition = f"DATE(crawled_at) >= '{start}'"
        elif period_type == 'monthly':
            from datetime import timedelta
            start = (today - timedelta(days=30)).isoformat()
            date_condition = f"DATE(crawled_at) >= '{start}'"
        elif period_type == 'custom' and start_date and end_date:
            date_condition = f"DATE(crawled_at) BETWEEN '{start_date}' AND '{end_date}'"
        else:
            date_condition = "1=1"  # 전체

        # 카테고리 목록
        category_list = list(CATEGORIES.keys())
        channel_category_list = list(Config.CHANNEL_CATEGORIES_KO.values())

        # 각 타입별 수집된 카테고리 조회
        result = {
            'shorts': {},
            'video': {},
            'channel_popular': {},
            'channel_growth': {}
        }

        # 쇼츠
        cursor.execute(f'''
            SELECT DISTINCT category FROM crawl_history
            WHERE target_type = 'shorts' AND success = 1 AND {date_condition}
        ''')
        collected_shorts = set(row['category'] for row in cursor.fetchall())

        for cat in category_list:
            result['shorts'][cat] = cat in collected_shorts

        # 영상
        cursor.execute(f'''
            SELECT DISTINCT category FROM crawl_history
            WHERE target_type = 'video' AND success = 1 AND {date_condition}
        ''')
        collected_videos = set(row['category'] for row in cursor.fetchall())

        for cat in category_list:
            result['video'][cat] = cat in collected_videos

        # 채널 인기순위
        cursor.execute(f'''
            SELECT DISTINCT category FROM crawl_history
            WHERE target_type = 'channel_popular' AND success = 1 AND {date_condition}
        ''')
        collected_channel_pop = set(row['category'] for row in cursor.fetchall())

        for cat in channel_category_list:
            result['channel_popular'][cat] = cat in collected_channel_pop

        # 채널 구독자 급상승
        cursor.execute(f'''
            SELECT DISTINCT category FROM crawl_history
            WHERE target_type = 'channel_growth' AND success = 1 AND {date_condition}
        ''')
        collected_channel_growth = set(row['category'] for row in cursor.fetchall())

        for cat in channel_category_list:
            result['channel_growth'][cat] = cat in collected_channel_growth

        return result

    def get_collection_summary(self, period_type='daily', start_date=None, end_date=None):
        """
        기간별 수집 요약 통계

        Returns:
            dict: 통계 요약 정보
        """
        cursor = self.conn.cursor()
        today = datetime.now().date()

        # 기간 조건 설정
        if period_type == 'daily':
            date_condition = f"DATE(crawled_at) = '{today.isoformat()}'"
        elif period_type == 'weekly':
            from datetime import timedelta
            start = (today - timedelta(days=7)).isoformat()
            date_condition = f"DATE(crawled_at) >= '{start}'"
        elif period_type == 'monthly':
            from datetime import timedelta
            start = (today - timedelta(days=30)).isoformat()
            date_condition = f"DATE(crawled_at) >= '{start}'"
        elif period_type == 'custom' and start_date and end_date:
            date_condition = f"DATE(crawled_at) BETWEEN '{start_date}' AND '{end_date}'"
        else:
            date_condition = "1=1"

        # 타입별 수집 횟수
        cursor.execute(f'''
            SELECT target_type, COUNT(*) as count, SUM(item_count) as total_items
            FROM crawl_history
            WHERE success = 1 AND {date_condition}
            GROUP BY target_type
        ''')
        type_summary = {row['target_type']: {'count': row['count'], 'items': row['total_items'] or 0}
                        for row in cursor.fetchall()}

        # 총 수집 횟수
        cursor.execute(f'''
            SELECT COUNT(*) as total_count, SUM(item_count) as total_items
            FROM crawl_history
            WHERE success = 1 AND {date_condition}
        ''')
        total_row = cursor.fetchone()

        # 실패 횟수
        cursor.execute(f'''
            SELECT COUNT(*) as fail_count FROM crawl_history
            WHERE success = 0 AND {date_condition}
        ''')
        fail_count = cursor.fetchone()['fail_count']

        # 날짜별 수집 통계 (최근 7일)
        cursor.execute(f'''
            SELECT DATE(crawled_at) as date, COUNT(*) as count, SUM(item_count) as items
            FROM crawl_history
            WHERE success = 1 AND {date_condition}
            GROUP BY DATE(crawled_at)
            ORDER BY date DESC
            LIMIT 7
        ''')
        daily_stats = [dict(row) for row in cursor.fetchall()]

        return {
            'total_crawls': total_row['total_count'] or 0,
            'total_items': total_row['total_items'] or 0,
            'fail_count': fail_count,
            'by_type': type_summary,
            'daily_stats': daily_stats
        }

    def upsert_api_video_deep(self, video_data):
        """
        Deep Data 영상 정보 UPSERT (PLAN.md 기준 확장 컬럼 포함)

        Args:
            video_data (dict): 영상 데이터 (확장 필드 포함)
        """
        cursor = self.conn.cursor()

        cursor.execute('''
            INSERT INTO api_videos (
                video_id, channel_id, title, published_at, duration_iso, duration_sec,
                video_type, view_count, like_count, tags,
                video_link, channel_name, category_id, category_name,
                thumbnail_url, thumbnail_path, description, comment_count,
                collected_at, days_since_upload, view_sub_ratio, like_view_ratio,
                comment_view_ratio, daily_avg_views, last_updated
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(video_id) DO UPDATE SET
                channel_id = excluded.channel_id,
                title = excluded.title,
                published_at = excluded.published_at,
                duration_iso = excluded.duration_iso,
                duration_sec = excluded.duration_sec,
                video_type = excluded.video_type,
                view_count = excluded.view_count,
                like_count = excluded.like_count,
                tags = excluded.tags,
                video_link = excluded.video_link,
                channel_name = excluded.channel_name,
                category_id = excluded.category_id,
                category_name = excluded.category_name,
                thumbnail_url = excluded.thumbnail_url,
                thumbnail_path = excluded.thumbnail_path,
                description = excluded.description,
                comment_count = excluded.comment_count,
                collected_at = excluded.collected_at,
                days_since_upload = excluded.days_since_upload,
                view_sub_ratio = excluded.view_sub_ratio,
                like_view_ratio = excluded.like_view_ratio,
                comment_view_ratio = excluded.comment_view_ratio,
                daily_avg_views = excluded.daily_avg_views,
                last_updated = CURRENT_TIMESTAMP
        ''', (
            video_data.get('video_id'),
            video_data.get('channel_id'),
            video_data.get('title'),
            video_data.get('published_at'),
            video_data.get('duration_iso'),
            video_data.get('duration_sec'),
            video_data.get('video_type'),
            video_data.get('view_count'),
            video_data.get('like_count'),
            video_data.get('tags'),
            video_data.get('video_link'),
            video_data.get('channel_name'),
            video_data.get('category_id'),
            video_data.get('category_name'),
            video_data.get('thumbnail_url'),
            video_data.get('thumbnail_path'),
            video_data.get('description'),
            video_data.get('comment_count'),
            video_data.get('collected_at'),
            video_data.get('days_since_upload'),
            video_data.get('view_sub_ratio'),
            video_data.get('like_view_ratio'),
            video_data.get('comment_view_ratio'),
            video_data.get('daily_avg_views'),
        ))

        self.conn.commit()

    def upsert_api_channel_deep(self, channel_data):
        """
        Deep Data 채널 정보 UPSERT (PLAN.md 기준 확장 컬럼 포함)

        Args:
            channel_data (dict): 채널 데이터 (확장 필드 포함)
        """
        cursor = self.conn.cursor()

        cursor.execute('''
            INSERT INTO api_channels (
                channel_id, title, thumbnail_url, subscriber_count, view_count,
                video_count, uploads_playlist_id, crawled_url,
                channel_handle, channel_link, country, description, published_at, keywords,
                days_since_published, avg_views_recent, video_upload_cycle, performance_index,
                last_updated, last_synced_at, sync_status, collected_video_count,
                latest_video_upload_date, last_deep_sync_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, ?, ?, ?, ?, ?)
            ON CONFLICT(channel_id) DO UPDATE SET
                title = excluded.title,
                thumbnail_url = excluded.thumbnail_url,
                subscriber_count = excluded.subscriber_count,
                view_count = excluded.view_count,
                video_count = excluded.video_count,
                uploads_playlist_id = excluded.uploads_playlist_id,
                crawled_url = excluded.crawled_url,
                channel_handle = excluded.channel_handle,
                channel_link = excluded.channel_link,
                country = excluded.country,
                description = excluded.description,
                published_at = excluded.published_at,
                keywords = excluded.keywords,
                days_since_published = excluded.days_since_published,
                avg_views_recent = excluded.avg_views_recent,
                video_upload_cycle = excluded.video_upload_cycle,
                performance_index = excluded.performance_index,
                last_updated = CURRENT_TIMESTAMP,
                last_synced_at = excluded.last_synced_at,
                sync_status = excluded.sync_status,
                collected_video_count = excluded.collected_video_count,
                latest_video_upload_date = excluded.latest_video_upload_date,
                last_deep_sync_at = excluded.last_deep_sync_at
        ''', (
            channel_data.get('channel_id'),
            channel_data.get('title'),
            channel_data.get('thumbnail_url'),
            channel_data.get('subscriber_count'),
            channel_data.get('view_count'),
            channel_data.get('video_count'),
            channel_data.get('uploads_playlist_id'),
            channel_data.get('crawled_url'),
            channel_data.get('channel_handle'),
            channel_data.get('channel_link'),
            channel_data.get('country'),
            channel_data.get('description'),
            channel_data.get('published_at'),
            channel_data.get('keywords'),
            channel_data.get('days_since_published'),
            channel_data.get('avg_views_recent'),
            channel_data.get('video_upload_cycle'),
            channel_data.get('performance_index'),
            channel_data.get('last_synced_at'),
            channel_data.get('sync_status'),
            channel_data.get('collected_video_count'),
            channel_data.get('latest_video_upload_date'),
            channel_data.get('last_deep_sync_at'),
        ))

        self.conn.commit()

    def get_reference_video_id(self, channel_name: str) -> tuple:
        """
        채널명으로 참조 가능한 영상 ID 조회 (Low-Cost Recovery용)

        우선순위:
        1. videos_rank에서 channel_id가 있는 경우 (Zero-Cost로 승격 가능)
        2. videos_rank (정확한 일치)
        3. shorts_rank (정확한 일치)
        4. videos_rank (유사 일치 - LIKE)
        5. shorts_rank (유사 일치 - LIKE)

        Args:
            channel_name: 채널명

        Returns:
            tuple: (video_id, channel_id) - channel_id는 있으면 반환, 없으면 None
        """
        if not channel_name or channel_name == 'N/A':
            logger.debug(f"[Low-Cost Recovery] Invalid channel_name: {channel_name}")
            return (None, None)

        try:
            cursor = self.conn.cursor()

            # === Priority 0: channel_id가 이미 있는 경우 (Zero-Cost 승격!) ===
            cursor.execute('''
                SELECT video_id, channel_id FROM videos_rank
                WHERE channel_name = ?
                  AND channel_id IS NOT NULL
                  AND channel_id != ''
                  AND channel_id != 'N/A'
                  AND video_id IS NOT NULL
                  AND video_id != ''
                  AND video_id != 'N/A'
                ORDER BY crawled_at DESC
                LIMIT 1
            ''', (channel_name,))

            row = cursor.fetchone()
            if row and row['channel_id']:
                logger.info(f"[Zero-Cost Upgrade!] ✓ Found channel_id directly in videos_rank: {row['channel_id']} for '{channel_name}'")
                return (row['video_id'], row['channel_id'])

            # shorts_rank에서도 확인
            cursor.execute('''
                SELECT video_id, channel_id FROM shorts_rank
                WHERE channel_name = ?
                  AND channel_id IS NOT NULL
                  AND channel_id != ''
                  AND channel_id != 'N/A'
                  AND video_id IS NOT NULL
                  AND video_id != ''
                  AND video_id != 'N/A'
                ORDER BY crawled_at DESC
                LIMIT 1
            ''', (channel_name,))

            row = cursor.fetchone()
            if row and row['channel_id']:
                logger.info(f"[Zero-Cost Upgrade!] ✓ Found channel_id directly in shorts_rank: {row['channel_id']} for '{channel_name}'")
                return (row['video_id'], row['channel_id'])

            cursor = self.conn.cursor()

            # === Priority 1: videos_rank (정확한 일치) ===
            cursor.execute('''
                SELECT video_id, channel_name FROM videos_rank
                WHERE channel_name = ?
                  AND video_id IS NOT NULL
                  AND video_id != ''
                  AND video_id != 'N/A'
                ORDER BY crawled_at DESC
                LIMIT 1
            ''', (channel_name,))

            row = cursor.fetchone()
            if row:
                logger.info(f"[Low-Cost Recovery] ✓ Found video_id (videos_rank): {row['video_id']} for '{channel_name}'")
                return (row['video_id'], None)

            # === Priority 2: shorts_rank (정확한 일치) ===
            cursor.execute('''
                SELECT video_id, channel_name FROM shorts_rank
                WHERE channel_name = ?
                  AND video_id IS NOT NULL
                  AND video_id != ''
                  AND video_id != 'N/A'
                ORDER BY crawled_at DESC
                LIMIT 1
            ''', (channel_name,))

            row = cursor.fetchone()
            if row:
                logger.info(f"[Low-Cost Recovery] ✓ Found video_id (shorts_rank): {row['video_id']} for '{channel_name}'")
                return (row['video_id'], None)

            # === Priority 3: videos_rank (유사 일치 - 공백/특수문자 무시) ===
            # 예: "엉 준"과 "엉준", "MrBeast"와 "Mr Beast" 매칭
            cursor.execute('''
                SELECT video_id, channel_name FROM videos_rank
                WHERE REPLACE(REPLACE(REPLACE(channel_name, ' ', ''), '-', ''), '_', '') = REPLACE(REPLACE(REPLACE(?, ' ', ''), '-', ''), '_', '')
                  AND video_id IS NOT NULL
                  AND video_id != ''
                  AND video_id != 'N/A'
                ORDER BY crawled_at DESC
                LIMIT 1
            ''', (channel_name,))

            row = cursor.fetchone()
            if row:
                logger.info(f"[Low-Cost Recovery] ✓ Found video_id (videos_rank, fuzzy): {row['video_id']} for '{channel_name}' (matched: '{row['channel_name']}')")
                return (row['video_id'], None)

            # === Priority 4: shorts_rank (유사 일치) ===
            cursor.execute('''
                SELECT video_id, channel_name FROM shorts_rank
                WHERE REPLACE(REPLACE(REPLACE(channel_name, ' ', ''), '-', ''), '_', '') = REPLACE(REPLACE(REPLACE(?, ' ', ''), '-', ''), '_', '')
                  AND video_id IS NOT NULL
                  AND video_id != ''
                  AND video_id != 'N/A'
                ORDER BY crawled_at DESC
                LIMIT 1
            ''', (channel_name,))

            row = cursor.fetchone()
            if row:
                logger.info(f"[Low-Cost Recovery] ✓ Found video_id (shorts_rank, fuzzy): {row['video_id']} for '{channel_name}' (matched: '{row['channel_name']}')")
                return (row['video_id'], None)

            logger.warning(f"[Low-Cost Recovery] ✗ No reference video found for: '{channel_name}'")
            return (None, None)

        except Exception as e:
            logger.error(f"[Low-Cost Recovery] Error getting reference video_id for '{channel_name}': {e}")
            return (None, None)

    def update_channel_id(self, old_channel_id: str, new_channel_id: str) -> bool:
        """
        채널 ID 업데이트 (Smart Recovery 후 임시 ID를 실제 ID로 교체)

        Args:
            old_channel_id: 기존 ID (temp_xxx 또는 N/A)
            new_channel_id: 새로운 YouTube Channel ID (UCxxx...)

        Returns:
            bool: 업데이트 성공 여부
        """
        try:
            cursor = self.conn.cursor()

            # api_channels 테이블 업데이트
            cursor.execute('''
                UPDATE api_channels
                SET channel_id = ?,
                    last_updated = ?
                WHERE channel_id = ?
            ''', (new_channel_id, datetime.now().isoformat(), old_channel_id))

            updated_rows = cursor.rowcount

            # api_videos 테이블도 FK 업데이트 (있는 경우)
            cursor.execute('''
                UPDATE api_videos
                SET channel_id = ?
                WHERE channel_id = ?
            ''', (new_channel_id, old_channel_id))

            self.conn.commit()

            if updated_rows > 0:
                logger.info(f"Channel ID updated: {old_channel_id} -> {new_channel_id}")
                return True
            else:
                logger.warning(f"No rows updated for channel ID: {old_channel_id}")
                return False

        except Exception as e:
            logger.error(f"Failed to update channel ID: {e}")
            self.conn.rollback()
            return False

    # ========== Playlist CRUD (Playlist-Driven Channel Discovery) ==========

    def add_playlist(self, playlist_id: str, title: str = None, thumbnail_url: str = None,
                     item_count: int = 0, channel_title: str = None) -> str:
        """
        재생목록 추가 또는 업데이트

        Args:
            playlist_id: 재생목록 ID
            title: 재생목록 제목
            thumbnail_url: 썸네일 URL
            item_count: 영상 개수
            channel_title: 소유자 채널명

        Returns:
            'new' | 'updated'
        """
        cursor = self.conn.cursor()

        try:
            # 기존 레코드 존재 여부 확인
            cursor.execute('SELECT 1 FROM monitored_playlists WHERE playlist_id = ?', (playlist_id,))
            exists = cursor.fetchone() is not None

            if exists:
                cursor.execute('''
                    UPDATE monitored_playlists
                    SET title = COALESCE(?, title),
                        thumbnail_url = COALESCE(?, thumbnail_url),
                        item_count = COALESCE(?, item_count),
                        channel_title = COALESCE(?, channel_title)
                    WHERE playlist_id = ?
                ''', (title, thumbnail_url, item_count, channel_title, playlist_id))
                result = 'updated'
            else:
                cursor.execute('''
                    INSERT INTO monitored_playlists
                    (playlist_id, title, thumbnail_url, item_count, channel_title)
                    VALUES (?, ?, ?, ?, ?)
                ''', (playlist_id, title, thumbnail_url, item_count, channel_title))
                result = 'new'

            self.conn.commit()
            logger.info(f"Playlist {result}: {playlist_id} - {title}")
            return result

        except Exception as e:
            logger.error(f"Failed to add playlist: {e}")
            self.conn.rollback()
            raise

    def get_all_playlists(self) -> list:
        """
        모든 재생목록 조회

        Returns:
            list of dict: 재생목록 목록
        """
        cursor = self.conn.cursor()
        cursor.execute('''
            SELECT playlist_id, title, thumbnail_url, item_count, channel_title,
                   last_synced_at, created_at
            FROM monitored_playlists
            ORDER BY created_at DESC
        ''')
        return [dict(row) for row in cursor.fetchall()]

    def get_playlist(self, playlist_id: str) -> dict:
        """
        특정 재생목록 조회

        Args:
            playlist_id: 재생목록 ID

        Returns:
            dict or None
        """
        cursor = self.conn.cursor()
        cursor.execute('''
            SELECT playlist_id, title, thumbnail_url, item_count, channel_title,
                   last_synced_at, created_at
            FROM monitored_playlists
            WHERE playlist_id = ?
        ''', (playlist_id,))
        row = cursor.fetchone()
        return dict(row) if row else None

    def delete_playlist(self, playlist_id: str) -> bool:
        """
        재생목록 삭제

        Args:
            playlist_id: 재생목록 ID

        Returns:
            bool: 삭제 성공 여부
        """
        cursor = self.conn.cursor()
        try:
            cursor.execute('DELETE FROM monitored_playlists WHERE playlist_id = ?', (playlist_id,))
            self.conn.commit()
            deleted = cursor.rowcount > 0
            if deleted:
                logger.info(f"Playlist deleted: {playlist_id}")
            return deleted
        except Exception as e:
            logger.error(f"Failed to delete playlist: {e}")
            self.conn.rollback()
            return False

    def update_playlist_sync_time(self, playlist_id: str):
        """
        재생목록 동기화 시간 업데이트

        Args:
            playlist_id: 재생목록 ID
        """
        cursor = self.conn.cursor()
        cursor.execute('''
            UPDATE monitored_playlists
            SET last_synced_at = CURRENT_TIMESTAMP
            WHERE playlist_id = ?
        ''', (playlist_id,))
        self.conn.commit()

    def upsert_channel_from_playlist(self, channel_id: str, channel_title: str, playlist_id: str = None, discovery_video_id: str = None, discovery_video_url: str = None) -> str:
        """
        재생목록에서 추출한 채널 정보를 api_channels에 저장 (source='playlist')

        Args:
            channel_id: 채널 ID
            channel_title: 채널명
            playlist_id: 재생목록 ID (선택)
            discovery_video_id: 채널 발견에 사용된 영상 ID (선택)
            discovery_video_url: 채널 발견에 사용된 영상 URL (선택)

        Returns:
            'new' | 'updated'
        """
        cursor = self.conn.cursor()

        try:
            cursor.execute('SELECT 1 FROM api_channels WHERE channel_id = ?', (channel_id,))
            exists = cursor.fetchone() is not None

            if exists:
                # 기존 채널: title, playlist_source, discovery 정보 업데이트
                cursor.execute('''
                    UPDATE api_channels
                    SET title = COALESCE(?, title),
                        playlist_source = COALESCE(?, playlist_source),
                        discovery_video_id = COALESCE(?, discovery_video_id),
                        discovery_video_url = COALESCE(?, discovery_video_url),
                        last_updated = CURRENT_TIMESTAMP
                    WHERE channel_id = ?
                ''', (channel_title, playlist_id, discovery_video_id, discovery_video_url, channel_id))
                result = 'updated'
            else:
                # 신규 채널: crawled_url='playlist', playlist_source, discovery 정보 저장
                cursor.execute('''
                    INSERT INTO api_channels
                    (channel_id, title, crawled_url, playlist_source, discovery_video_id, discovery_video_url, last_updated)
                    VALUES (?, ?, 'playlist', ?, ?, ?, CURRENT_TIMESTAMP)
                ''', (channel_id, channel_title, playlist_id, discovery_video_id, discovery_video_url))
                result = 'new'

            self.conn.commit()
            logger.debug(f"Channel from playlist {result}: {channel_id} - {channel_title} (playlist: {playlist_id}, video: {discovery_video_id})")
            return result

        except Exception as e:
            logger.error(f"Failed to upsert channel from playlist: {e}")
            self.conn.rollback()
            raise

    def close(self):
        """데이터베이스 연결 종료"""
        if self.conn:
            self.conn.close()
            logger.info("Database connection closed")

    def __del__(self):
        """소멸자 - 자동으로 연결 종료"""
        self.close()
