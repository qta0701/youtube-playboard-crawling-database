"""
YouTube API Manager
채널 동기화 및 영상 수집 관리

핵심 기능:
- Zero-Cost Channel ID 추출 후 API 동기화
- uploads 플레이리스트를 통한 영상 수집
- 영상/쇼츠 자동 분류
"""
import sqlite3
from datetime import datetime
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from config import Config
from logger_config import setup_logger
from modules.youtube_utils import (
    get_channel_id_from_url,
    parse_duration_iso,
    classify_video_type
)
from modules.quota_tracker import QuotaTracker

logger = setup_logger('youtube_manager')


class YouTubeManager:
    """YouTube API 연동 및 데이터 동기화 매니저"""

    def __init__(self, db_path='output/db/youtube_data.db'):
        """
        YouTube Manager 초기화

        Args:
            db_path: 데이터베이스 경로
        """
        self.db_path = db_path
        self.quota_tracker = QuotaTracker(db_path)
        self.youtube = None

        # API 초기화는 필요할 때 수행 (Lazy Loading)
        # self._init_youtube_api()

    def _init_youtube_api(self):
        """YouTube API 클라이언트 초기화"""
        logger.debug("Initializing YouTube API client...")
        try:
            api_key = getattr(Config, 'YOUTUBE_API_KEY', None)
            if api_key:
                logger.debug(f"API key found (length: {len(api_key)})")
                self.youtube = build('youtube', 'v3', developerKey=api_key)
                logger.info("✓ YouTube API initialized successfully with API key")
            else:
                logger.warning("✗ YouTube API key not found in Config")
                logger.debug("Check Config.YOUTUBE_API_KEY setting")
        except Exception as e:
            logger.error(f"✗ YouTube API initialization failed: {e}")
            logger.exception("API initialization exception details:")

    def _get_connection(self):
        """DB 연결 생성"""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def find_channel_by_name_and_subs(self, channel_name: str, expected_subs: int, tolerance: float = 0.2) -> dict:
        """
        채널명 검색 + 구독자 수 검증으로 Channel ID 찾기 (Cost: 101 Units)

        Args:
            channel_name: 채널명
            expected_subs: 예상 구독자 수
            tolerance: 허용 오차율 (0.2 = 20%)

        Returns:
            dict: {
                'channel_id': str or None,
                'quota_used': int,
                'matched_subs': int (매칭된 채널의 실제 구독자 수),
                'error': str
            }
        """
        result = {
            'channel_id': None,
            'quota_used': 0,
            'matched_subs': 0,
            'error': None
        }

        if not self.youtube:
            self._init_youtube_api()
        
        if not self.youtube:
            result['error'] = 'YouTube API not initialized'
            return result

        try:
            # 1. Search API (Cost: 100)
            if not self.quota_tracker.can_make_request('search.list'):
                result['error'] = 'API quota exceeded for search'
                logger.warning(f"[High-Cost] ✗ Quota exceeded for search.list")
                return result

            logger.info(f"[High-Cost] Calling search.list API - query: '{channel_name}', expected_subs: {expected_subs:,}, tolerance: {tolerance*100}%")

            search_res = self.youtube.search().list(
                q=channel_name,
                type='channel',
                part='snippet',
                maxResults=3  # 상위 3개만 확인
            ).execute()

            self.quota_tracker.log_usage('search.list', 100)
            result['quota_used'] += 100

            candidates = [item['snippet']['channelId'] for item in search_res.get('items', [])]
            if not candidates:
                result['error'] = f'No search results for: {channel_name}'
                logger.warning(f"[High-Cost] ✗ No search results (Quota: 100)")
                return result

            logger.debug(f"[High-Cost] ✓ Found {len(candidates)} candidate(s): {candidates}")

            # 2. Channels API for Stats (Cost: 1)
            if not self.quota_tracker.can_make_request('channels.list'):
                result['error'] = 'API quota exceeded for channels.list'
                logger.warning(f"[High-Cost] ✗ Quota exceeded for channels.list")
                return result

            logger.debug(f"[High-Cost] Calling channels.list API for validation - IDs: {candidates}")

            stats_res = self.youtube.channels().list(
                id=','.join(candidates),
                part='statistics,snippet'
            ).execute()

            self.quota_tracker.log_usage('channels.list', 1)
            result['quota_used'] += 1

            best_match = None
            min_diff = float('inf')
            best_subs = 0

            # ===== 개선 #38: expected_subs=0이면 첫 번째 유효한 채널 반환 =====
            if expected_subs == 0 and tolerance >= 1.0:
                logger.info(f"[High-Cost] No subscriber validation (tolerance=100%), using first valid result")
                for item in stats_res.get('items', []):
                    real_subs = int(item['statistics'].get('subscriberCount', 0))
                    best_match = item['id']
                    best_subs = real_subs
                    logger.debug(f"[High-Cost] Using first result: '{item['snippet']['title']}' - ID: {item['id']}, subs: {real_subs:,}")
                    break
            else:
                for item in stats_res.get('items', []):
                    # 구독자 수 비교 (숨김 채널은 0으로 처리)
                    real_subs = int(item['statistics'].get('subscriberCount', 0))
                    if real_subs == 0:
                        logger.debug(f"[High-Cost] Skipping '{item['snippet']['title']}' - hidden subscriber count")
                        continue  # 구독자 비공개는 매칭 불가

                    diff = abs(real_subs - expected_subs)

                    # 오차 범위 내이고, 이전 매칭보다 더 가까우면 선택
                    if diff <= (expected_subs * tolerance) and diff < min_diff:
                        min_diff = diff
                        best_match = item['id']
                        best_subs = real_subs
                        logger.debug(f"[High-Cost] Potential match: '{item['snippet']['title']}' - ID: {item['id']}, subs: {real_subs:,}, diff: {diff:,}")

            if best_match:
                result['channel_id'] = best_match
                result['matched_subs'] = best_subs
                if expected_subs == 0:
                    logger.info(f"[High-Cost] ✓ Channel found (no validation): {best_match} (subs: {best_subs:,}, Total Quota: {result['quota_used']})")
                else:
                    logger.info(f"[High-Cost] ✓ Channel matched: {best_match} (subs: {best_subs:,}, diff: {min_diff:,}, Total Quota: {result['quota_used']})")
            else:
                result['error'] = f'No matching channel within tolerance ({tolerance*100}%)'
                logger.warning(f"[High-Cost] ✗ No match within {tolerance*100}% tolerance (Total Quota: {result['quota_used']})")

        except HttpError as e:
            result['error'] = f'API Error: {e.resp.status}'
            logger.error(f"[Deep Search] API error: {e}")
        except Exception as e:
            result['error'] = str(e)
            logger.error(f"[Deep Search] Error: {e}")

        return result

    def recover_channel_id_via_video(self, channel_name: str) -> dict:
        """
        영상 ID 역추적으로 Channel ID 복구 (Low-Cost Recovery: 1 Unit)

        Args:
            channel_name: 채널명

        Returns:
            dict: {
                'channel_id': str or None,
                'quota_used': int,
                'error': str
            }
        """
        result = {
            'channel_id': None,
            'quota_used': 0,
            'error': None
        }

        if not self.youtube:
            self._init_youtube_api()

        if not self.youtube:
            result['error'] = 'YouTube API not initialized'
            return result

        try:
            # 1. DB에서 참조 영상 ID 조회 (Zero-Cost)
            from modules.database import DatabaseHandler
            db = DatabaseHandler(self.db_path)
            logger.debug(f"[Low-Cost] Searching DB for videos with channel_name: '{channel_name}'")
            video_id, db_channel_id = db.get_reference_video_id(channel_name)

            if not video_id:
                result['error'] = f'No reference video found for: {channel_name}'
                logger.debug(f"[Low-Cost] ✗ No video data found in DB for: '{channel_name}'")
                return result

            logger.debug(f"[Low-Cost] ✓ Found video_id: {video_id}, db_channel_id: {db_channel_id or 'N/A'}")

            # 1.5. DB에 channel_id가 이미 있으면 Zero-Cost로 승격! (API 호출 없음)
            if db_channel_id:
                result['channel_id'] = db_channel_id
                result['quota_used'] = 0  # Zero-Cost!
                logger.info(f"[Zero-Cost Upgrade!] ✓ Channel ID found directly in DB: {db_channel_id} (Quota: 0, no API call)")
                return result

            # 2. videos.list API로 Channel ID 추출 (Cost: 1)
            if not self.quota_tracker.can_make_request('videos.list'):
                result['error'] = 'API quota exceeded for videos.list'
                logger.warning(f"[Low-Cost] ✗ Quota exceeded for videos.list")
                return result

            logger.info(f"[Low-Cost] Calling videos.list API with video_id: {video_id}")

            video_response = self.youtube.videos().list(
                part='snippet',
                id=video_id
            ).execute()

            self.quota_tracker.log_usage('videos.list', 1)
            result['quota_used'] = 1

            if not video_response.get('items'):
                result['error'] = f'Video not found: {video_id}'
                logger.warning(f"[Low-Cost] ✗ Video deleted or private: {video_id}")
                return result

            channel_id = video_response['items'][0]['snippet'].get('channelId')
            if channel_id:
                result['channel_id'] = channel_id
                logger.info(f"[Low-Cost] ✓ Channel ID recovered via API: {channel_id} (video: {video_id}, Quota: 1)")
            else:
                result['error'] = 'channelId not found in video snippet'
                logger.warning(f"[Low-Cost] ✗ channelId not in video snippet: {video_id}")

        except HttpError as e:
            result['error'] = f'API Error: {e.resp.status}'
            logger.error(f"[Low-Cost Recovery] API error: {e}")
        except Exception as e:
            result['error'] = str(e)
            logger.error(f"[Low-Cost Recovery] Error: {e}")

        return result

    def sync_channel(self, channel_url: str, channel_name: str = None, subscriber_count: int = None, use_search_fallback: bool = False) -> dict:
        """
        채널 동기화 (Tiered Recovery Strategy: Zero-Cost → Low-Cost → High-Cost)

        Args:
            channel_url: Playboard 또는 YouTube 채널 URL
            channel_name: 채널명 (Fallback 검색용)
            subscriber_count: 구독자 수 (High-Cost 검색 검증용)
            use_search_fallback: True일 경우 Low/High-Cost Recovery 허용

        Returns:
            dict: {
                'success': bool,
                'channel_id': str,
                'data': dict (채널 정보),
                'quota_used': int,
                'recovery_method': str ('url', 'video', 'search', None),
                'error': str (실패 시)
            }
        """
        logger.info("=" * 80)
        logger.info(f"CHANNEL SYNC START - URL: {channel_url}")
        logger.info(f"Parameters: channel_name={channel_name}, subs={subscriber_count}, use_search={use_search_fallback}")
        logger.info("=" * 80)

        result = {
            'success': False,
            'channel_id': None,
            'data': None,
            'quota_used': 0,
            'recovery_method': None,
            'error': None
        }

        # === Tiered Recovery Strategy ===

        # Step 1: Zero-Cost - URL 파싱
        logger.debug(f"[Step 1: Zero-Cost] Attempting URL parsing...")
        channel_id = get_channel_id_from_url(channel_url)
        if channel_id:
            result['channel_id'] = channel_id
            result['recovery_method'] = 'url'
            logger.info(f"[Step 1: Zero-Cost] ✓ Channel ID extracted from URL: {channel_id}")

        # Step 2: Low-Cost - 영상 ID 역추적 (1 Quota)
        # channel_name이 없어도 시도 (DB에서 영상 검색 가능)
        else:
            logger.info(f"[Step 1: Zero-Cost] ✗ URL parsing failed: {channel_url}")
            logger.info(f"[Step 2: Low-Cost] Attempting recovery - channel_name: '{channel_name}'")

            video_recovery = self.recover_channel_id_via_video(channel_name)
            result['quota_used'] += video_recovery['quota_used']

            if video_recovery['channel_id']:
                channel_id = video_recovery['channel_id']
                result['channel_id'] = channel_id
                result['recovery_method'] = 'video'
                logger.info(f"[Step 2: Low-Cost] ✓ Recovery success: {channel_id} (Quota: {video_recovery['quota_used']})")

            # Step 3: High-Cost - Search API (101 Quota, 옵션 활성화 시에만)
            elif use_search_fallback:
                logger.info(f"[Step 2: Low-Cost] ✗ Recovery failed: {video_recovery.get('error', 'No video data')}")

                if channel_name:
                    # ===== 개선 #38: subscriber_count=0이어도 High-Cost 검색 허용 =====
                    if subscriber_count and subscriber_count > 0:
                        logger.info(f"[Step 3: High-Cost] Attempting search recovery - channel: '{channel_name}', subs: {subscriber_count:,}")
                        search_result = self.find_channel_by_name_and_subs(channel_name, subscriber_count)
                    else:
                        # 구독자 수 없이 검색 (첫 번째 결과 사용, tolerance=1.0 = 100%)
                        logger.info(f"[Step 3: High-Cost] Attempting search recovery WITHOUT subscriber validation - channel: '{channel_name}'")
                        search_result = self.find_channel_by_name_and_subs(channel_name, 0, tolerance=1.0)

                    result['quota_used'] += search_result['quota_used']

                    if search_result['channel_id']:
                        channel_id = search_result['channel_id']
                        result['channel_id'] = channel_id
                        result['recovery_method'] = 'search'
                        logger.info(f"[Step 3: High-Cost] ✓ Recovery success: {channel_id} (Quota: {search_result['quota_used']})")
                    else:
                        result['error'] = f"모든 복구 방법 실패 (Zero-Cost + Low-Cost + High-Cost): {search_result.get('error', 'Unknown')}"
                        logger.warning(f"[Step 3: High-Cost] ✗ All recovery methods failed: {search_result.get('error')}")
                        return result
                else:
                    result['error'] = f"Low-Cost 복구 실패 (영상 데이터 없음). High-Cost 검색 불가 (채널명 정보 없음)."
                    logger.warning(f"[Step 3: High-Cost] ✗ Cannot attempt - no channel_name")
                    return result
            else:
                result['error'] = f"Low-Cost 복구 실패 (영상 데이터 없음). High-Cost 검색 옵션을 활성화하세요."
                logger.warning(f"[Step 2: Low-Cost] ✗ Failed, High-Cost disabled (use_search_fallback=False)")
                return result
            return result

        result['channel_id'] = channel_id

        # DB에 기존 채널 정보가 이미 존재하면 추가 수집하지 않고 패스(중복 방지)
        try:
            conn = self._get_connection()
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute('SELECT * FROM sheet_channels WHERE channel_id = ?', (channel_id,))
            existing_channel = cursor.fetchone()
            conn.close()
            if existing_channel:
                logger.info(f"✓ Channel {channel_id} already exists in DB. Syncing skipped (Duplicate Bypass).")
                result['success'] = True
                row_dict = dict(existing_channel)
                result['data'] = {
                    'channel_id': row_dict.get('channel_id'),
                    'title': row_dict.get('channel_name', ''),
                    'subscriber_count': row_dict.get('subscribers', 0),
                    'view_count': row_dict.get('total_channel_views', 0),
                    'video_count': row_dict.get('total_video_count', 0),
                    'uploads_playlist_id': f"UU{channel_id[2:]}",  # Zero-cost UU 변환 적용
                    'crawled_url': row_dict.get('channel_link', '')
                }
                return result
        except Exception as db_chk_err:
            logger.error(f"Failed to check existing channel in DB: {db_chk_err}")

        # 2. API 사용 가능 여부 확인
        logger.debug("Checking API availability...")
        if not self.youtube:
            self._init_youtube_api()

        if not self.youtube:
            result['error'] = 'YouTube API not initialized'
            logger.error(f"✗ CHANNEL SYNC FAILED: {result['error']}")
            return result

        if not self.quota_tracker.can_make_request('channels.list'):
            result['error'] = 'API quota exceeded'
            logger.error(f"✗ CHANNEL SYNC FAILED: {result['error']}")
            return result

        logger.debug("API available, quota check passed")

        # 3. API 호출 (1 unit)
        try:
            logger.debug(f"Calling channels.list API for channel_id: {channel_id}")
            response = self.youtube.channels().list(
                part='snippet,statistics,contentDetails',
                id=channel_id
            ).execute()

            self.quota_tracker.log_usage('channels.list', 1)
            result['quota_used'] += 1
            logger.debug(f"API call successful (quota used: {result['quota_used']})")

            if not response.get('items'):
                result['error'] = f'Channel not found: {channel_id}'
                logger.warning(f"✗ CHANNEL SYNC FAILED: Channel not found in API response")
                return result

            channel_data = response['items'][0]
            logger.debug(f"Channel data received: {len(str(channel_data))} bytes")

            # 4. 데이터 추출
            logger.debug("Extracting channel data...")
            snippet = channel_data.get('snippet', {})
            statistics = channel_data.get('statistics', {})
            content_details = channel_data.get('contentDetails', {})

            data = {
                'channel_id': channel_id,
                'title': snippet.get('title', ''),
                'thumbnail_url': snippet.get('thumbnails', {}).get('default', {}).get('url', ''),
                'subscriber_count': int(statistics.get('subscriberCount', 0)),
                'view_count': int(statistics.get('viewCount', 0)),
                'video_count': int(statistics.get('videoCount', 0)),
                'uploads_playlist_id': content_details.get('relatedPlaylists', {}).get('uploads', f"UU{channel_id[2:]}"),
                'crawled_url': channel_url or f"https://www.youtube.com/channel/{channel_id}",
                'channel_handle': snippet.get('customUrl', ''),
                'created_at': snippet.get('publishedAt', ''),
                'description': snippet.get('description', ''),
                'channel_country': snippet.get('country', '')
            }

            logger.debug(f"Extracted data - title: {data['title']}, subs: {data['subscriber_count']:,}, videos: {data['video_count']}")

            # 5. DB 저장
            logger.debug("Saving channel to database...")
            self._save_channel(data)

            result['success'] = True
            result['data'] = data
            logger.info("=" * 80)
            logger.info(f"✓ CHANNEL SYNC SUCCESS: {data['title']} ({channel_id})")
            logger.info(f"Stats - Subscribers: {data['subscriber_count']:,}, Videos: {data['video_count']}, Total Quota: {result['quota_used']}")
            logger.info("=" * 80)

        except HttpError as e:
            result['error'] = f'API Error: {e.resp.status}'
            logger.error(f"✗ CHANNEL SYNC FAILED - YouTube API error: {e}")
            logger.exception("API HttpError details:")
        except Exception as e:
            result['error'] = str(e)
            logger.error(f"✗ CHANNEL SYNC FAILED - Error: {e}")
            logger.exception("Channel sync exception details:")

        return result

    def _save_channel(self, data: dict):
        """채널 데이터를 sheet_channels 테이블에 저장"""
        conn = self._get_connection()
        cursor = conn.cursor()

        try:
            cursor.execute("PRAGMA table_info(sheet_channels)")
            db_cols = [col[1] for col in cursor.fetchall()]

            row_dict = {
                'channel_id': data['channel_id'],
                'channel_name': data['title'],
                'subscribers': data['subscriber_count'],
                'total_channel_views': data['view_count'],
                'total_video_count': data['video_count'],
                'channel_link': data['crawled_url'],
                'crawl_date': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                'channel_description': data.get('description', ''),
                'channel_handle': data.get('channel_handle', ''),
                'created_at': data.get('created_at', ''),
                'channel_country': data.get('channel_country', ''),
                'is_fetched': 'ㅇ'
            }

            # existing data check for original_row_order
            cursor.execute("SELECT original_row_order FROM sheet_channels WHERE channel_id = ?", (data['channel_id'],))
            existing = cursor.fetchone()
            if existing and existing[0]:
                row_dict['original_row_order'] = existing[0]
            else:
                cursor.execute("SELECT MAX(original_row_order) FROM sheet_channels")
                max_order = cursor.fetchone()[0]
                row_dict['original_row_order'] = (max_order or 9) + 1

            columns = [c for c in row_dict.keys() if c in db_cols]
            placeholders = ', '.join(['?'] * len(columns))
            sql = f"INSERT OR REPLACE INTO sheet_channels ({', '.join(columns)}) VALUES ({placeholders})"
            cursor.execute(sql, [row_dict[col] for col in columns])
            conn.commit()
            logger.debug(f"Channel saved to sheet_channels: {data['channel_id']}")
        except Exception as e:
            logger.error(f"Channel save error to sheet_channels: {e}")
        finally:
            conn.close()

    def fetch_videos(self, channel_id: str = None, playlist_id: str = None, limit: int = 50, fetch_all: bool = False, since_year: int = None, sync_channel_info: bool = False, tab_name: str = "영상 리스트") -> dict:
        """
        채널 또는 재생목록의 영상 목록 수집 - sheet_videos 저장용
        """
        result = {
            'success': False,
            'videos': [],
            'shorts_count': 0,
            'video_count': 0,
            'quota_used': 0,
            'error': None
        }

        if not self.youtube:
            result['error'] = 'YouTube API not initialized'
            return result

        # 1. 대상 플레이리스트 ID 획득
        channel_data = None
        if channel_id:
            # sheet_channels 테이블에서 채널 정보 및 uploads_playlist_id 추출 시도
            conn = self._get_connection()
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute(
                'SELECT channel_name as title, subscribers as subscriber_count FROM sheet_channels WHERE channel_id = ?',
                (channel_id,)
            )
            row = cursor.fetchone()
            conn.close()

            if not row:
                if sync_channel_info:
                    # 채널 동기화 실행
                    sync_res = self.sync_channel(f"https://www.youtube.com/channel/{channel_id}")
                    result['quota_used'] += sync_res['quota_used']
                    if sync_res['success']:
                        channel_data = {
                            'subscriber_count': sync_res['data']['subscriber_count'],
                            'title': sync_res['data']['title']
                        }
                else:
                    result['error'] = f'Channel not synced in DB: {channel_id}'
                    return result
            else:
                channel_data = {
                    'subscriber_count': row['subscriber_count'],
                    'title': row['title']
                }

            playlist_id = f"UU{channel_id[2:]}"  # uploads playlist ID
        elif playlist_id:
            # 재생목록의 영상들을 직접 가져오는 경우
            channel_id = None
        else:
            result['error'] = 'Either channel_id or playlist_id must be provided'
            return result

        # DB에 존재하는 기존 영상 ID들 미리 획득 (중복 제외용)
        existing_video_ids = set()
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            cursor.execute("SELECT video_id FROM sheet_videos")
            existing_video_ids = {row[0] for row in cursor.fetchall() if row[0]}
            conn.close()
            logger.info(f"Duplicate Check: Found {len(existing_video_ids)} video IDs in sheet_videos DB.")
        except Exception as db_err:
            logger.error(f"Failed to fetch existing video IDs for duplicate check: {db_err}")

        # 2. 플레이리스트 아이템 조회
        video_ids = []
        next_page_token = None
        stop_by_year = False

        try:
            while True:
                # fetch_all이 False면 limit 체크
                if not fetch_all and len(video_ids) >= limit:
                    break

                if not self.quota_tracker.can_make_request('playlistItems.list'):
                    result['error'] = 'API quota exceeded during fetch'
                    break

                # fetch_all이면 항상 50개씩, 아니면 limit까지만
                max_results = 50 if fetch_all else min(50, limit - len(video_ids))

                response = self.youtube.playlistItems().list(
                    part='contentDetails',
                    playlistId=playlist_id,
                    maxResults=max_results,
                    pageToken=next_page_token
                ).execute()

                self.quota_tracker.log_usage('playlistItems.list', 1)
                result['quota_used'] += 1

                for item in response.get('items', []):
                    content_details = item.get('contentDetails', {})
                    video_id = content_details.get('videoId')
                    video_pub_at = content_details.get('videoPublishedAt')

                    # 연도 필터링: 지정 연도 이전 영상이 발견되면 수집 중단
                    if since_year and video_pub_at:
                        try:
                            # YYYY-MM-DD
                            pub_year = int(video_pub_at[:4])
                            if pub_year < since_year:
                                logger.info(f"Stop suiting: Video published at {video_pub_at} is older than since_year {since_year}")
                                stop_by_year = True
                                break
                        except Exception as parse_err:
                            logger.warning(f"Failed to parse videoPublishedAt year: {parse_err}")

                    if video_id:
                        if video_id in existing_video_ids:
                            logger.debug(f"Video {video_id} already exists in DB, skipping.")
                            continue
                        video_ids.append(video_id)

                if stop_by_year:
                    break

                next_page_token = response.get('nextPageToken')
                if not next_page_token:
                    break

            # 3. 영상 상세 정보 조회 (50개씩 배치)
            videos = []
            for i in range(0, len(video_ids), 50):
                batch = video_ids[i:i+50]

                if not self.quota_tracker.can_make_request('videos.list'):
                    result['error'] = 'API quota exceeded during video details'
                    break

                video_response = self.youtube.videos().list(
                    part='snippet,contentDetails,statistics',
                    id=','.join(batch)
                ).execute()

                self.quota_tracker.log_usage('videos.list', 1)
                result['quota_used'] += 1

                for video_data in video_response.get('items', []):
                    v_channel_id = video_data.get('snippet', {}).get('channelId')
                    
                    # 연쇄적인 채널 정보 수집 활성화 시
                    v_channel_data = channel_data
                    if sync_channel_info and v_channel_id and not channel_id:
                        sync_res = self.sync_channel(f"https://www.youtube.com/channel/{v_channel_id}")
                        result['quota_used'] += sync_res['quota_used']
                        if sync_res['success']:
                            v_channel_data = {
                                'subscriber_count': sync_res['data']['subscriber_count'],
                                'title': sync_res['data']['title']
                            }

                    video = self._parse_video_data(video_data, v_channel_id, v_channel_data)
                    videos.append(video)

                    if video['video_type'] == 'shorts':
                        result['shorts_count'] += 1
                    else:
                        result['video_count'] += 1

            # 4. DB 저장
            self._save_videos(videos, tab_name=tab_name)

            result['success'] = True
            result['videos'] = videos
            logger.info(f"Fetched {len(videos)} videos ({result['shorts_count']} shorts, {result['video_count']} videos)")

        except HttpError as e:
            result['error'] = f'API Error: {e.resp.status}'
            logger.error(f"YouTube API error: {e}")
        except Exception as e:
            result['error'] = str(e)
            logger.error(f"Video fetch error: {e}")

        return result

    def _parse_video_data(self, video_data: dict, channel_id: str, channel_data: dict = None) -> dict:
        """API 응답에서 영상 데이터 파싱 - PLAN.md Deep Data 확장"""
        snippet = video_data.get('snippet', {})
        content_details = video_data.get('contentDetails', {})
        statistics = video_data.get('statistics', {})

        video_id = video_data['id']
        duration_iso = content_details.get('duration', 'PT0S')
        duration_sec = parse_duration_iso(duration_iso)
        video_type = classify_video_type(duration_sec)

        # PLAN.md - Deep Data 필드
        title = snippet.get('title', '')
        channel_name = snippet.get('channelTitle', '')
        published_at = snippet.get('publishedAt', '')
        description = snippet.get('description', '')
        category_id = snippet.get('categoryId', '')
        tags = snippet.get('tags', [])
        tags_str = ','.join(tags) if tags else ''

        # 썸네일 (고화질 우선)
        thumbnails = snippet.get('thumbnails', {})
        thumbnail_url = (
            thumbnails.get('maxres', {}).get('url') or
            thumbnails.get('high', {}).get('url') or
            thumbnails.get('medium', {}).get('url') or
            ''
        )

        # 통계
        view_count = int(statistics.get('viewCount', 0))
        like_count = int(statistics.get('likeCount', 0))
        comment_count = int(statistics.get('commentCount', 0))

        # 파생 데이터 계산
        from datetime import datetime, timezone
        collected_at = datetime.now(timezone.utc).isoformat()

        # 업로드 경과일 계산
        days_since_upload = 0
        daily_avg_views = 0.0
        try:
            pub_date = datetime.fromisoformat(published_at.replace('Z', '+00:00'))
            now = datetime.now(timezone.utc)
            days_since_upload = max(1, (now - pub_date).days)
            daily_avg_views = view_count / days_since_upload if days_since_upload > 0 else 0.0
        except:
            pass

        # 구독자 대비 조회수 비율 (채널 데이터 필요)
        view_sub_ratio = 0.0
        if channel_data and channel_data.get('subscriber_count'):
            sub_count = channel_data['subscriber_count']
            if sub_count > 0:
                view_sub_ratio = (view_count / sub_count) * 100

        # 조회수 대비 좋아요/댓글 비율
        like_view_ratio = (like_count / view_count * 100) if view_count > 0 else 0.0
        comment_view_ratio = (comment_count / view_count * 100) if view_count > 0 else 0.0

        # 카테고리명 매핑 (YouTube Category IDs)
        category_map = {
            '1': 'Film & Animation', '2': 'Autos & Vehicles', '10': 'Music',
            '15': 'Pets & Animals', '17': 'Sports', '18': 'Short Movies',
            '19': 'Travel & Events', '20': 'Gaming', '21': 'Videoblogging',
            '22': 'People & Blogs', '23': 'Comedy', '24': 'Entertainment',
            '25': 'News & Politics', '26': 'Howto & Style', '27': 'Education',
            '28': 'Science & Technology', '29': 'Nonprofits & Activism',
            '30': 'Movies', '31': 'Anime/Animation', '32': 'Action/Adventure',
            '33': 'Classics', '34': 'Documentary', '35': 'Drama',
            '36': 'Family', '37': 'Foreign', '38': 'Horror',
            '39': 'Sci-Fi/Fantasy', '40': 'Thriller', '41': 'Shorts',
            '42': 'Shows', '43': 'Trailers'
        }
        category_name = category_map.get(category_id, 'Unknown')

        return {
            # 기본 필드
            'video_id': video_id,
            'channel_id': channel_id,
            'title': title,
            'published_at': published_at,
            'duration_iso': duration_iso,
            'duration_sec': duration_sec,
            'video_type': video_type,
            'view_count': view_count,
            'like_count': like_count,
            'tags': tags_str,

            # PLAN.md Deep Data 필드
            'video_link': f'https://youtu.be/{video_id}',
            'channel_name': channel_name,
            'category_id': category_id,
            'category_name': category_name,
            'thumbnail_url': thumbnail_url,
            'thumbnail_path': None,  # 향후 다운로드 기능 구현 시 사용
            'description': description,
            'comment_count': comment_count,

            # 파생 데이터
            'collected_at': collected_at,
            'days_since_upload': days_since_upload,
            'view_sub_ratio': view_sub_ratio,
            'like_view_ratio': like_view_ratio,
            'comment_view_ratio': comment_view_ratio,
            'daily_avg_views': daily_avg_views,

            # AI 활용 예비 컬럼 (추후)
            'transcript_txt': None,
            'is_ai_generated': None,
            'analysis_summary': None
        }

    def _save_videos(self, videos: list, tab_name="영상 리스트"):
        """영상 데이터 일괄 저장 - sheet_videos 테이블 구조로 변환 후 UPSERT"""
        conn = self._get_connection()
        cursor = conn.cursor()

        try:
            cursor.execute("PRAGMA table_info(sheet_videos)")
            db_cols = [col[1] for col in cursor.fetchall()]

            from modules.utils import calculate_sheet_video_metrics
            from modules.youtube_utils import format_duration

            inserted_count = 0
            for idx, v in enumerate(videos):
                row_dict = {
                    'video_id': v['video_id'],
                    'channel_id': v['channel_id'],
                    'title': v['title'],
                    'video_link': v['video_link'],
                    'channel_name': v['channel_name'],
                    'views': v['view_count'],
                    'likes': v['like_count'],
                    'comments': v['comment_count'],
                    'duration': format_duration(v['duration_sec']),
                    'is_shorts': 'ㅇ' if v['video_type'] == 'shorts' else 'x',
                    'thumbnail_link': v['thumbnail_url'],
                    'description': v['description'],
                    'category_id': v['category_id'],
                    'tab_name': tab_name,
                    'crawl_date': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                }

                # YYYY-MM-DD 포맷 변환
                if v.get('published_at'):
                    row_dict['upload_date'] = v['published_at'][:10]

                # 기존 original_row_order 검색 및 보존
                cursor.execute("SELECT original_row_order FROM sheet_videos WHERE video_id = ? AND tab_name = ?", (v['video_id'], tab_name))
                existing = cursor.fetchone()
                if existing and existing[0]:
                    row_dict['original_row_order'] = existing[0]
                else:
                    cursor.execute("SELECT MAX(original_row_order) FROM sheet_videos WHERE tab_name = ?", (tab_name,))
                    max_order = cursor.fetchone()[0]
                    row_dict['original_row_order'] = (max_order or 9) + 1

                # 파이썬 기반 통계 계산
                row_dict = calculate_sheet_video_metrics(row_dict)

                columns = [c for c in row_dict.keys() if c in db_cols]
                placeholders = ', '.join(['?'] * len(columns))
                sql = f"INSERT OR REPLACE INTO sheet_videos ({', '.join(columns)}) VALUES ({placeholders})"
                cursor.execute(sql, [row_dict[col] for col in columns])
                inserted_count += 1

            conn.commit()
            logger.debug(f"Saved {inserted_count} videos to sheet_videos DB (Deep Data)")
        except Exception as e:
            logger.error(f"Videos save error: {e}")
        finally:
            conn.close()

    def fetch_single_video(self, video_id: str, sync_channel_info: bool = False) -> dict:
        """단일 영상 상세 정보 조회 및 sheet_videos 저장"""
        result = {
            'success': False,
            'videos': [],
            'quota_used': 0,
            'error': None
        }

        if not self.youtube:
            result['error'] = 'YouTube API not initialized'
            return result

        if not self.quota_tracker.can_make_request('videos.list'):
            result['error'] = 'API quota exceeded'
            return result

        try:
            logger.info(f"Calling videos.list API for single video_id: {video_id}")
            response = self.youtube.videos().list(
                part='snippet,contentDetails,statistics',
                id=video_id
            ).execute()

            self.quota_tracker.log_usage('videos.list', 1)
            result['quota_used'] += 1

            if not response.get('items'):
                result['error'] = f'Video not found: {video_id}'
                return result

            video_data = response['items'][0]
            channel_id = video_data.get('snippet', {}).get('channelId')

            channel_data = None
            if channel_id:
                if sync_channel_info:
                    sync_res = self.sync_channel(f"https://www.youtube.com/channel/{channel_id}")
                    result['quota_used'] += sync_res['quota_used']
                    if sync_res['success']:
                        channel_data = {
                            'subscriber_count': sync_res['data']['subscriber_count'],
                            'title': sync_res['data']['title']
                        }
                else:
                    # DB에서 채널 정보 읽기 시도
                    conn = self._get_connection()
                    conn.row_factory = sqlite3.Row
                    cursor = conn.cursor()
                    cursor.execute("SELECT subscribers as subscriber_count, channel_name as title FROM sheet_channels WHERE channel_id = ?", (channel_id,))
                    row = cursor.fetchone()
                    conn.close()
                    if row:
                        channel_data = dict(row)

            video = self._parse_video_data(video_data, channel_id, channel_data)

            # DB 저장
            self._save_videos([video], tab_name="영상 리스트")

            # 채널 메트릭스 최신화
            if channel_id:
                self.update_channel_metrics(channel_id)

            result['success'] = True
            result['videos'] = [video]

        except Exception as e:
            result['error'] = str(e)
            logger.error(f"Failed to fetch single video: {e}")

        return result

    def update_channel_metrics(self, channel_id: str):
        """영상 리스트 DB 데이터를 집계하여 채널 리스트 DB의 통계 정보(수집영상 갯수, 평균 조회수 등)를 실시간 최신화"""
        conn = self._get_connection()
        cursor = conn.cursor()
        try:
            # 1. sheet_videos에서 해당 채널의 영상 수, 평균 조회수 집계
            cursor.execute('''
                SELECT COUNT(*), AVG(views)
                FROM sheet_videos
                WHERE channel_id = ? AND tab_name = '영상 리스트'
            ''', (channel_id,))
            row = cursor.fetchone()

            if row:
                video_count = row[0] or 0
                avg_views = int(row[1]) if row[1] is not None else 0

                # 2. 최근 30개 영상 평균 조회수 집계
                cursor.execute('''
                    SELECT AVG(views) FROM (
                        SELECT views FROM sheet_videos 
                        WHERE channel_id = ? AND tab_name = '영상 리스트' 
                        ORDER BY upload_date DESC LIMIT 30
                    )
                ''', (channel_id,))
                avg_views_30 = int(cursor.fetchone()[0] or 0)

                # 3. 상위 3개 제외 평균 조회수 집계
                cursor.execute('''
                    SELECT AVG(views) FROM (
                        SELECT views FROM sheet_videos 
                        WHERE channel_id = ? AND tab_name = '영상 리스트' 
                        ORDER BY views DESC LIMIT -1 OFFSET 3
                    )
                ''', (channel_id,))
                avg_exclude_top3 = int(cursor.fetchone()[0] or 0)

                # 4. sheet_channels 테이블 통계 갱신
                cursor.execute('''
                    UPDATE sheet_channels
                    SET collected_video_count = ?,
                        collected_video_avg_views = ?,
                        avg_views_30 = ?,
                        avg_views_exclude_top3 = ?
                    WHERE channel_id = ?
                ''', (video_count, avg_views, avg_views_30, avg_exclude_top3, channel_id))
                
                conn.commit()
                logger.info(f"✓ Channel {channel_id} metrics updated. Count: {video_count}, Avg: {avg_views}, Avg30: {avg_views_30}, ExclTop3: {avg_exclude_top3}")
        except Exception as e:
            logger.error(f"Failed to update channel metrics for {channel_id}: {e}")
        finally:
            conn.close()

    def get_synced_channels(self) -> list:
        """동기화된 채널 목록 조회"""
        conn = self._get_connection()
        cursor = conn.cursor()

        cursor.execute('''
            SELECT * FROM api_channels
            ORDER BY last_updated DESC
        ''')

        channels = [dict(row) for row in cursor.fetchall()]
        conn.close()

        return channels

    def get_channel_videos(self, channel_id: str, video_type: str = None) -> list:
        """
        채널의 영상 목록 조회

        Args:
            channel_id: 채널 ID
            video_type: 'shorts', 'video', None (전체)
        """
        conn = self._get_connection()
        cursor = conn.cursor()

        if video_type:
            cursor.execute('''
                SELECT * FROM api_videos
                WHERE channel_id = ? AND video_type = ?
                ORDER BY published_at DESC
            ''', (channel_id, video_type))
        else:
            cursor.execute('''
                SELECT * FROM api_videos
                WHERE channel_id = ?
                ORDER BY published_at DESC
            ''', (channel_id,))

        videos = [dict(row) for row in cursor.fetchall()]
        conn.close()

        return videos

    def get_api_stats(self) -> dict:
        """API 데이터 통계"""
        conn = self._get_connection()
        cursor = conn.cursor()

        # 채널 수
        cursor.execute('SELECT COUNT(*) as count FROM api_channels')
        channel_count = cursor.fetchone()['count']

        # 영상 수 (타입별)
        cursor.execute('''
            SELECT video_type, COUNT(*) as count
            FROM api_videos
            GROUP BY video_type
        ''')
        video_counts = {row['video_type']: row['count'] for row in cursor.fetchall()}

        # Quota 현황
        quota_status = self.quota_tracker.get_today_usage()

        conn.close()

        return {
            'channels': channel_count,
            'videos': video_counts.get('video', 0),
            'shorts': video_counts.get('shorts', 0),
            'total_videos': sum(video_counts.values()),
            'quota': quota_status
        }

    # ========== Playlist-Driven Channel Discovery ==========

    def fetch_playlist_metadata(self, playlist_id: str) -> dict:
        """
        재생목록 메타데이터 조회 (Cost: 1 Quota)

        Args:
            playlist_id: 재생목록 ID (PL로 시작)

        Returns:
            dict: {
                'success': bool,
                'playlist_id': str,
                'title': str,
                'thumbnail_url': str,
                'item_count': int,
                'channel_title': str,
                'quota_used': int,
                'error': str
            }
        """
        result = {
            'success': False,
            'playlist_id': playlist_id,
            'title': None,
            'thumbnail_url': None,
            'item_count': 0,
            'channel_title': None,
            'quota_used': 0,
            'error': None
        }

        if not self.youtube:
            self._init_youtube_api()

        if not self.youtube:
            result['error'] = 'YouTube API not initialized'
            return result

        if not self.quota_tracker.can_make_request('playlists.list'):
            result['error'] = 'API quota exceeded'
            return result

        try:
            response = self.youtube.playlists().list(
                part='snippet,contentDetails',
                id=playlist_id
            ).execute()

            self.quota_tracker.log_usage('playlists.list', 1)
            result['quota_used'] = 1

            if not response.get('items'):
                result['error'] = f'Playlist not found: {playlist_id}'
                return result

            item = response['items'][0]
            snippet = item['snippet']
            content = item['contentDetails']

            # 썸네일 URL (고화질 우선)
            thumbnails = snippet.get('thumbnails', {})
            thumb_url = (
                thumbnails.get('maxres', {}).get('url') or
                thumbnails.get('high', {}).get('url') or
                thumbnails.get('medium', {}).get('url') or
                thumbnails.get('default', {}).get('url')
            )

            result['success'] = True
            result['title'] = snippet.get('title')
            result['thumbnail_url'] = thumb_url
            result['item_count'] = content.get('itemCount', 0)
            result['channel_title'] = snippet.get('channelTitle')

            logger.info(f"[Playlist] Fetched metadata: '{result['title']}' ({result['item_count']} items)")

        except HttpError as e:
            result['error'] = f'API Error: {e.resp.status}'
            logger.error(f"[Playlist] API error fetching metadata: {e}")
        except Exception as e:
            result['error'] = str(e)
            logger.error(f"[Playlist] Error fetching metadata: {e}")

        return result

    def extract_channels_from_playlist(self, playlist_id: str, max_results: int = 50) -> dict:
        """
        재생목록 내 영상들의 채널 정보 추출 (Cost: 1 Quota per 50 items)

        videoOwnerChannelId를 활용하여 검색 API 없이 채널 ID 확보

        Args:
            playlist_id: 재생목록 ID
            max_results: 최대 조회 수 (기본 50, 최대 50)

        Returns:
            dict: {
                'success': bool,
                'total': int,
                'new': int,
                'updated': int,
                'channels': list[dict],
                'quota_used': int,
                'error': str
            }
        """
        result = {
            'success': False,
            'total': 0,
            'new': 0,
            'updated': 0,
            'channels': [],
            'quota_used': 0,
            'error': None
        }

        if not self.youtube:
            self._init_youtube_api()

        if not self.youtube:
            result['error'] = 'YouTube API not initialized'
            return result

        if not self.quota_tracker.can_make_request('playlistItems.list'):
            result['error'] = 'API quota exceeded'
            return result

        try:
            from modules.database import DatabaseHandler
            db = DatabaseHandler(self.db_path)

            response = self.youtube.playlistItems().list(
                part='snippet',
                playlistId=playlist_id,
                maxResults=min(max_results, 50)
            ).execute()

            self.quota_tracker.log_usage('playlistItems.list', 1)
            result['quota_used'] = 1

            channels_found = {}  # 중복 제거용
            new_count = 0
            updated_count = 0

            for item in response.get('items', []):
                snippet = item.get('snippet', {})

                # videoOwnerChannelId가 있는 경우만 처리 (삭제된 영상 등 제외)
                channel_id = snippet.get('videoOwnerChannelId')
                channel_title = snippet.get('videoOwnerChannelTitle')

                if channel_id and channel_title:
                    if channel_id not in channels_found:
                        channels_found[channel_id] = {
                            'channel_id': channel_id,
                            'channel_title': channel_title,
                            'video_id': snippet.get('resourceId', {}).get('videoId')
                        }

                        # DB Upsert (source='playlist'로 마킹, playlist_id 저장)
                        try:
                            upsert_result = db.upsert_channel_from_playlist(channel_id, channel_title, playlist_id)
                            if upsert_result == 'new':
                                new_count += 1
                            else:
                                updated_count += 1
                        except Exception as e:
                            logger.warning(f"[Playlist] Failed to upsert channel {channel_id}: {e}")

            result['success'] = True
            result['total'] = len(response.get('items', []))
            result['new'] = new_count
            result['updated'] = updated_count
            result['channels'] = list(channels_found.values())

            # 동기화 시간 업데이트
            db.update_playlist_sync_time(playlist_id)
            db.close()

            logger.info(f"[Playlist] Extracted {len(channels_found)} unique channels "
                       f"(new: {new_count}, updated: {updated_count}) from {result['total']} items")

        except HttpError as e:
            result['error'] = f'API Error: {e.resp.status}'
            logger.error(f"[Playlist] API error extracting channels: {e}")
        except Exception as e:
            result['error'] = str(e)
            logger.error(f"[Playlist] Error extracting channels: {e}")

        return result

    def extract_channels_from_playlist_robust(self, playlist_id: str, max_results: int = 50) -> dict:
        """
        재생목록에서 채널 추출 (Robust Version - PLAN.md Section 3.3)

        Strategy A: videoOwnerChannelId 활용 (Fast)
        Strategy B: video ID -> videos.list API -> channelId (Robust Fallback)

        Args:
            playlist_id: 재생목록 ID
            max_results: 최대 조회 수 (기본 50, 최대 50)

        Returns:
            dict: {
                'success': bool,
                'total': int,
                'new': int,
                'updated': int,
                'channels': list[dict],
                'quota_used': int,
                'error': str,
                'fallback_count': int  # videos.list로 보완한 채널 수
            }
        """
        result = {
            'success': False,
            'total': 0,
            'new': 0,
            'updated': 0,
            'channels': [],
            'quota_used': 0,
            'fallback_count': 0,
            'error': None
        }

        if not self.youtube:
            result['error'] = 'YouTube API not initialized'
            return result

        if not self.quota_tracker.can_make_request('playlistItems.list'):
            result['error'] = 'API quota exceeded'
            return result

        try:
            from modules.database import DatabaseHandler
            db = DatabaseHandler(self.db_path)

            # Step 1: playlistItems.list로 영상 목록 가져오기 (Cost: 1)
            response = self.youtube.playlistItems().list(
                part='snippet,contentDetails',
                playlistId=playlist_id,
                maxResults=min(max_results, 50)
            ).execute()

            self.quota_tracker.log_usage('playlistItems.list', 1)
            result['quota_used'] = 1

            channels_found = {}  # {channel_id: {title, video_id, video_url}}
            video_ids_to_fetch = []  # videoOwnerChannelId가 없는 영상 ID 목록
            new_count = 0
            updated_count = 0

            # Step 2: Strategy A - videoOwnerChannelId로 채널 정보 추출
            for item in response.get('items', []):
                snippet = item.get('snippet', {})
                content_details = item.get('contentDetails', {})
                video_id = content_details.get('videoId')

                channel_id = snippet.get('videoOwnerChannelId')
                channel_title = snippet.get('videoOwnerChannelTitle')

                if channel_id and channel_title:
                    # Strategy A 성공: snippet에서 채널 정보 추출
                    if channel_id not in channels_found:
                        channels_found[channel_id] = {
                            'channel_id': channel_id,
                            'channel_title': channel_title,
                            'discovery_video_id': video_id,
                            'discovery_video_url': f'https://youtu.be/{video_id}' if video_id else None
                        }
                elif video_id:
                    # Strategy A 실패: video_id를 fallback 목록에 추가
                    video_ids_to_fetch.append(video_id)

            # Step 3: Strategy B - videos.list로 누락된 채널 정보 가져오기 (Cost: 1 per 50 videos)
            if video_ids_to_fetch:
                if not self.quota_tracker.can_make_request('videos.list'):
                    logger.warning(f"[Playlist Robust] Quota exceeded for videos.list fallback. Skipping {len(video_ids_to_fetch)} videos.")
                else:
                    logger.info(f"[Playlist Robust] Fetching {len(video_ids_to_fetch)} videos via videos.list API (fallback)")

                    vid_req = self.youtube.videos().list(
                        part='snippet',
                        id=','.join(video_ids_to_fetch)
                    )
                    vid_res = vid_req.execute()

                    self.quota_tracker.log_usage('videos.list', 1)
                    result['quota_used'] += 1
                    result['fallback_count'] = len(vid_res.get('items', []))

                    for item in vid_res.get('items', []):
                        c_id = item['snippet']['channelId']
                        c_title = item['snippet']['channelTitle']
                        v_id = item['id']

                        if c_id not in channels_found:
                            channels_found[c_id] = {
                                'channel_id': c_id,
                                'channel_title': c_title,
                                'discovery_video_id': v_id,
                                'discovery_video_url': f'https://youtu.be/{v_id}'
                            }

            # Step 4: DB에 채널 저장 (discovery_video_id 포함)
            for c_id, data in channels_found.items():
                try:
                    upsert_result = db.upsert_channel_from_playlist(
                        channel_id=c_id,
                        channel_title=data['channel_title'],
                        playlist_id=playlist_id,
                        discovery_video_id=data.get('discovery_video_id'),
                        discovery_video_url=data.get('discovery_video_url')
                    )
                    if upsert_result == 'new':
                        new_count += 1
                    else:
                        updated_count += 1
                except Exception as e:
                    logger.warning(f"[Playlist Robust] Failed to upsert channel {c_id}: {e}")

            result['success'] = True
            result['total'] = len(response.get('items', []))
            result['new'] = new_count
            result['updated'] = updated_count
            result['channels'] = list(channels_found.values())

            # 동기화 시간 업데이트
            db.update_playlist_sync_time(playlist_id)
            db.close()

            logger.info(f"[Playlist Robust] Extracted {len(channels_found)} unique channels "
                       f"(new: {new_count}, updated: {updated_count}, fallback: {result['fallback_count']}) "
                       f"from {result['total']} items. Quota used: {result['quota_used']}")

        except HttpError as e:
            result['error'] = f'API Error: {e.resp.status}'
            logger.error(f"[Playlist Robust] API error: {e}")
        except Exception as e:
            result['error'] = str(e)
            logger.error(f"[Playlist Robust] Error: {e}")
            logger.exception("Detailed traceback:")

        return result

    # ========== Hybrid Playlist Export System ==========

    def create_playlist(self, youtube_service, title: str, description: str = '', privacy: str = 'private') -> dict:
        """
        새 재생목록 생성 (Cost: 50 Quota)

        Args:
            youtube_service: OAuth 인증된 YouTube 서비스 객체
            title: 재생목록 제목
            description: 재생목록 설명 (선택)
            privacy: 공개 설정 ('private', 'public', 'unlisted')

        Returns:
            dict: {
                'success': bool,
                'playlist_id': str,
                'title': str,
                'quota_used': int,
                'error': str
            }
        """
        result = {
            'success': False,
            'playlist_id': None,
            'title': title,
            'quota_used': 0,
            'error': None
        }

        try:
            if not self.quota_tracker.can_make_request('playlists.insert'):
                result['error'] = 'API quota exceeded for playlists.insert'
                logger.warning("[Playlist Export] Quota exceeded")
                return result

            request_body = {
                'snippet': {
                    'title': title,
                    'description': description
                },
                'status': {
                    'privacyStatus': privacy
                }
            }

            response = youtube_service.playlists().insert(
                part='snippet,status',
                body=request_body
            ).execute()

            self.quota_tracker.log_usage('playlists.insert', 50)
            result['quota_used'] = 50

            result['success'] = True
            result['playlist_id'] = response['id']
            result['title'] = response['snippet']['title']

            logger.info(f"[Playlist Export] Created playlist: '{title}' (ID: {result['playlist_id']})")

        except HttpError as e:
            result['error'] = f'API Error: {e.resp.status} - {e.reason}'
            logger.error(f"[Playlist Export] API error creating playlist: {e}")
        except Exception as e:
            result['error'] = str(e)
            logger.error(f"[Playlist Export] Error creating playlist: {e}")

        return result

    def add_video_to_playlist(self, youtube_service, playlist_id: str, video_id: str) -> dict:
        """
        재생목록에 영상 추가 (Cost: 50 Quota per video)

        Args:
            youtube_service: OAuth 인증된 YouTube 서비스 객체
            playlist_id: 대상 재생목록 ID
            video_id: 추가할 영상 ID

        Returns:
            dict: {
                'success': bool,
                'video_id': str,
                'quota_used': int,
                'error': str
            }
        """
        result = {
            'success': False,
            'video_id': video_id,
            'quota_used': 0,
            'error': None
        }

        try:
            if not self.quota_tracker.can_make_request('playlistItems.insert'):
                result['error'] = 'API quota exceeded for playlistItems.insert'
                logger.warning(f"[Playlist Export] Quota exceeded for video: {video_id}")
                return result

            request_body = {
                'snippet': {
                    'playlistId': playlist_id,
                    'resourceId': {
                        'kind': 'youtube#video',
                        'videoId': video_id
                    }
                }
            }

            response = youtube_service.playlistItems().insert(
                part='snippet',
                body=request_body
            ).execute()

            self.quota_tracker.log_usage('playlistItems.insert', 50)
            result['quota_used'] = 50

            result['success'] = True
            logger.debug(f"[Playlist Export] Added video: {video_id} to playlist: {playlist_id}")

        except HttpError as e:
            error_reason = e.reason if hasattr(e, 'reason') else str(e)
            result['error'] = f'API Error: {e.resp.status} - {error_reason}'

            # 특정 에러 유형 처리
            if e.resp.status == 404:
                result['error'] = f'영상을 찾을 수 없습니다 (삭제됨 또는 비공개): {video_id}'
            elif e.resp.status == 409:
                result['error'] = f'이미 재생목록에 있는 영상입니다: {video_id}'
                # 중복 추가는 성공으로 처리할 수도 있음
            elif e.resp.status == 403:
                result['error'] = f'영상 추가 권한이 없습니다: {video_id}'

            logger.warning(f"[Playlist Export] API error adding video {video_id}: {e}")

        except Exception as e:
            result['error'] = str(e)
            logger.error(f"[Playlist Export] Error adding video {video_id}: {e}")

        return result

    def add_videos_to_playlist_batch(self, youtube_service, playlist_id: str, video_ids: list) -> dict:
        """
        재생목록에 여러 영상 일괄 추가

        Args:
            youtube_service: OAuth 인증된 YouTube 서비스 객체
            playlist_id: 대상 재생목록 ID
            video_ids: 추가할 영상 ID 리스트

        Returns:
            dict: {
                'success': bool,
                'total': int,
                'added': int,
                'failed': int,
                'skipped': int (중복),
                'quota_used': int,
                'errors': list[dict],
                'error': str
            }
        """
        result = {
            'success': False,
            'total': len(video_ids),
            'added': 0,
            'failed': 0,
            'skipped': 0,
            'quota_used': 0,
            'errors': [],
            'error': None
        }

        if not video_ids:
            result['error'] = 'No video IDs provided'
            return result

        for video_id in video_ids:
            add_result = self.add_video_to_playlist(youtube_service, playlist_id, video_id)
            result['quota_used'] += add_result['quota_used']

            if add_result['success']:
                result['added'] += 1
            else:
                error_msg = add_result.get('error', 'Unknown error')
                if '이미 재생목록에 있는' in error_msg or '409' in error_msg:
                    result['skipped'] += 1
                else:
                    result['failed'] += 1
                    result['errors'].append({
                        'video_id': video_id,
                        'error': error_msg
                    })

        result['success'] = result['added'] > 0 or result['skipped'] > 0
        logger.info(f"[Playlist Export] Batch complete: {result['added']} added, "
                   f"{result['skipped']} skipped, {result['failed']} failed "
                   f"(Quota: {result['quota_used']})")

        return result

    @staticmethod
    def generate_playlist_url(video_ids: list) -> list:
        """
        영상 ID 리스트를 받아 'Watch Video Series' URL 생성 (Zero-Cost)

        YouTube의 watch_videos URL은 한 번에 최대 50개 영상까지 지원.
        50개 초과 시 여러 URL로 분할.

        Args:
            video_ids: 영상 ID 리스트

        Returns:
            list: 생성된 URL 리스트 (50개 단위로 분할)
        """
        urls = []
        chunk_size = 50

        for i in range(0, len(video_ids), chunk_size):
            chunk = video_ids[i:i + chunk_size]
            url = f"https://www.youtube.com/watch_videos?video_ids={','.join(chunk)}"
            urls.append({
                'url': url,
                'count': len(chunk),
                'start_index': i + 1,
                'end_index': min(i + chunk_size, len(video_ids))
            })

        logger.info(f"[Playlist URL] Generated {len(urls)} URL(s) for {len(video_ids)} videos")
        return urls
