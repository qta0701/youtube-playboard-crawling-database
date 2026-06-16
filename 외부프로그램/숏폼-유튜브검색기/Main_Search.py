# -*- coding: utf-8 -*-
"""
YouTube 검색 API 및 데이터 추출 메인 모듈
"""
import os
from datetime import datetime, timezone
from typing import List, Dict, Optional
from googleapiclient.discovery import build
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
import pickle


class YouTubeSearchAPI:
    """YouTube Data API를 사용한 검색 기능"""

    SCOPES = ['https://www.googleapis.com/auth/youtube.force-ssl']
    DAILY_QUOTA = 10000  # 기본 일일 할당량

    def __init__(self, api_key: Optional[str] = None):
        """
        YouTube API 초기화

        Args:
            api_key: YouTube Data API 키 (선택사항, OAuth 사용 시 불필요)
        """
        self.api_key = api_key
        self.youtube = None
        self.credentials = None
        self.quota_used = 0  # 사용한 쿼터 추적

    def authenticate_oauth(self, client_secret_file: str):
        """
        OAuth 2.0 인증

        Args:
            client_secret_file: OAuth 클라이언트 시크릿 JSON 파일 경로
        """
        creds = None
        token_file = 'token.pickle'

        # 기존 토큰 로드
        if os.path.exists(token_file):
            try:
                with open(token_file, 'rb') as token:
                    creds = pickle.load(token)
            except Exception as e:
                print(f"기존 토큰 로드 실패: {e}")
                # 손상된 토큰 파일 삭제
                os.remove(token_file)
                creds = None

        # 토큰이 없거나 유효하지 않으면 새로 인증
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                try:
                    creds.refresh(Request())
                except Exception as e:
                    print(f"토큰 갱신 실패: {e}")
                    creds = None

            if not creds:
                try:
                    flow = InstalledAppFlow.from_client_secrets_file(
                        client_secret_file, self.SCOPES)
                    # 포트를 8080으로 고정하고 브라우저 자동 열기 활성화
                    creds = flow.run_local_server(
                        port=8080,
                        access_type='offline',
                        prompt='consent'
                    )
                except Exception as e:
                    print(f"OAuth 인증 실패: {e}")
                    print("\n대안: API Key 방식을 사용하려면 authenticate_api_key() 메서드를 사용하세요.")
                    raise

            # 토큰 저장
            try:
                with open(token_file, 'wb') as token:
                    pickle.dump(creds, token)
            except Exception as e:
                print(f"토큰 저장 실패: {e}")

        self.credentials = creds
        self.youtube = build('youtube', 'v3', credentials=creds)

    def authenticate_api_key(self, api_key: str):
        """
        API Key 인증

        Args:
            api_key: YouTube Data API 키
        """
        self.api_key = api_key
        self.youtube = build('youtube', 'v3', developerKey=api_key)

    def search_videos(self, keyword: str, max_results: int = 50,
                     order: str = 'relevance', video_duration: str = None,
                     published_after: datetime = None,
                     search_country: str = '한국',
                     exclude_country: bool = False,
                     only_country: bool = False,
                     db_cache: dict = None) -> tuple:
        """
        키워드로 YouTube 비디오 검색 (국가 필터링, 당일 캐싱 스킵 및 페이지네이션 지원)
        """
        if not self.youtube:
            raise Exception("YouTube API가 초기화되지 않았습니다. authenticate_oauth() 또는 authenticate_api_key()를 먼저 호출하세요.")

        if db_cache is None:
            db_cache = {}

        COUNTRY_ISO_MAP = {
            '한국': 'KR', '미국': 'US', '일본': 'JP', '인도네시아': 'ID', '베트남': 'VN',
            '태국': 'TH', '인도': 'IN', '대만': 'TW', '브라질': 'BR', '멕시코': 'MX',
            '필리핀': 'PH', '러시아': 'RU', '영국': 'GB', '프랑스': 'FR', '독일': 'DE', '캐나다': 'CA'
        }
        target_iso = COUNTRY_ISO_MAP.get(search_country, 'KR').upper()

        api_calls = 0
        quota_cost = 0
        results = []
        unique_channels = set()
        
        next_page_token = None
        total_scanned = 0
        max_scan_limit = 500 # 무한 루프 및 쿼터 과다 소모 차단 안전 한도

        while len(results) < max_results and total_scanned < max_scan_limit:
            search_params = {
                'part': 'id,snippet',
                'q': keyword,
                'type': 'video',
                'maxResults': 50,
                'order': order,
                'regionCode': 'KR'
            }
            if next_page_token:
                search_params['pageToken'] = next_page_token
            if video_duration:
                search_params['videoDuration'] = video_duration
            if published_after:
                search_params['publishedAfter'] = published_after.isoformat() + 'Z'

            # 검색 호출 (비용 100 pts)
            search_response = self.youtube.search().list(**search_params).execute()
            api_calls += 1
            quota_cost += 100

            items = search_response.get('items', [])
            if not items:
                break
                
            total_scanned += len(items)
            next_page_token = search_response.get('nextPageToken')

            # 당일 수집 캐시 조회 및 신규 영상 분류
            pending_video_ids = []
            
            for item in items:
                if item['id']['kind'] != 'youtube#video':
                    continue
                v_id = item['id']['videoId']
                
                # 당일 수집된 캐시 데이터 존재 확인
                if v_id in db_cache:
                    cached_video = db_cache[v_id]
                    # 캐시 영상의 채널 국가 필터링 체크
                    c_country = str(cached_video.get('채널국가') or '').strip().upper()
                    
                    if exclude_country and c_country == target_iso:
                        continue
                    if only_country and c_country != target_iso:
                        continue
                        
                    # 필터 통과 시 캐시 데이터를 결과에 바로 추가 (API 사용 0)
                    cached_video['검색 키워드'] = keyword
                    results.append(cached_video)
                else:
                    pending_video_ids.append(v_id)

            # 신규 수집 필요 비디오 상세 정보 및 채널 수집 진행
            if pending_video_ids:
                # 50개 단위 배치 조회
                for i in range(0, len(pending_video_ids), 50):
                    batch_ids = pending_video_ids[i:i+50]
                    videos_response = self.youtube.videos().list(
                        part='snippet,contentDetails,statistics,status',
                        id=','.join(batch_ids)
                    ).execute()
                    api_calls += 1
                    quota_cost += 1 # videos.list 비용: 1 pts

                    video_items = videos_response.get('items', [])
                    for video in video_items:
                        v_id = video['id']
                        snippet = video['snippet']
                        c_id = snippet['channelId']

                        # 채널 상세 정보 획득 (비비디 국가 필터 검사용)
                        channel_info = self.get_channel_info(c_id)
                        api_calls += 1
                        quota_cost += 1 # channels.list 비용: 1 pts
                        unique_channels.add(c_id)

                        c_country = str(channel_info.get('channel_country') or '').strip().upper()
                        
                        # 신규 영상 국가 필터링 체크
                        if exclude_country and c_country == target_iso:
                            continue
                        if only_country and c_country != target_iso:
                            continue

                        # 필터 통과 시 상세 파싱 진행 (중복 API 제거를 위해 channel_info 주입)
                        video_data = self._extract_video_data(video, keyword, channel_info)
                        results.append(video_data)

            if not next_page_token:
                break

        # 결과 개수 슬라이싱
        results = results[:max_results]
        self.quota_used += quota_cost

        stats = {
            'api_calls': api_calls,
            'quota_cost': quota_cost,
            'quota_percent': round((quota_cost / self.DAILY_QUOTA) * 100, 2),
            'unique_channels': len(unique_channels)
        }
        return results, stats

    def get_channel_info(self, channel_id: str) -> Dict:
        """
        채널 정보 조회

        Args:
            channel_id: 채널 ID

        Returns:
            채널 정보 딕셔너리
        """
        if not self.youtube:
            raise Exception("YouTube API가 초기화되지 않았습니다.")

        channel_response = self.youtube.channels().list(
            part='snippet,statistics,contentDetails',
            id=channel_id
        ).execute()

        if not channel_response.get('items'):
            return {}

        channel = channel_response['items'][0]
        return {
            'channel_id': channel_id,
            'channel_name': channel['snippet']['title'],
            'channel_country': channel['snippet'].get('country', ''),
            'channel_description': channel['snippet'].get('description', ''),
            'channel_handle': channel['snippet'].get('customUrl', ''),
            'subscriber_count': int(channel['statistics'].get('subscriberCount', 0)),
            'total_view_count': int(channel['statistics'].get('viewCount', 0)),
            'video_count': int(channel['statistics'].get('videoCount', 0)),
            'published_at': channel['snippet']['publishedAt'],
            'channel_link': f"https://www.youtube.com/channel/{channel_id}"
        }

    def _extract_video_data(self, video: Dict, keyword: str, channel_info: Dict = None) -> Dict:
        """
        YouTube API 응답에서 필요한 데이터 추출

        Args:
            video: YouTube API videos().list() 응답 아이템
            keyword: 검색 키워드
            channel_info: 이미 조회된 채널 정보 딕셔너리 (선택사항)

        Returns:
            추출된 비디오 데이터
        """
        video_id = video['id']
        snippet = video['snippet']
        statistics = video.get('statistics', {})
        content_details = video.get('contentDetails', {})

        # 채널 정보 조회
        if channel_info is None:
            channel_info = self.get_channel_info(snippet['channelId'])

        # 비디오 길이 파싱 (ISO 8601 duration)
        duration = content_details.get('duration', 'PT0S')
        duration_seconds = self._parse_duration(duration)

        # 쇼츠 여부 판단 (180초=3분 이하)
        is_short = duration_seconds <= 180

        # 업로드 날짜 (timezone aware)
        published_at = datetime.fromisoformat(snippet['publishedAt'].replace('Z', '+00:00'))

        # 수집 날짜 (timezone aware로 변환)
        collection_date = datetime.now(timezone.utc)
        days_since_upload = (collection_date - published_at).days

        # 통계 데이터
        view_count = int(statistics.get('viewCount', 0))
        like_count = int(statistics.get('likeCount', 0))
        comment_count = int(statistics.get('commentCount', 0))
        subscriber_count = channel_info.get('subscriber_count', 0)

        # 계산 지표
        subscriber_view_ratio = view_count / subscriber_count if subscriber_count > 0 else 0
        like_ratio = (like_count / view_count * 100) if view_count > 0 else 0
        comment_ratio = (comment_count / view_count * 100) if view_count > 0 else 0
        daily_avg_views = view_count / days_since_upload if days_since_upload > 0 else view_count
        avg_views_per_video = channel_info.get('total_view_count', 0) / channel_info.get('video_count', 1)

        # 디스크립션에서 해시태그 추출
        description = snippet.get('description', '')
        hashtags = self._extract_hashtags(description)

        # 채널 개설일
        try:
            channel_published_str = channel_info.get('published_at', snippet['publishedAt'])
            # ISO 형식 문자열 정리 (마이크로초가 너무 길면 6자리로 제한)
            if 'T' in channel_published_str:
                # Z를 +00:00으로 변경
                channel_published_str = channel_published_str.replace('Z', '+00:00')
                # 마이크로초 부분 처리 (6자리 이하로 제한)
                if '.' in channel_published_str and '+' in channel_published_str:
                    parts = channel_published_str.split('.')
                    microsec_and_tz = parts[1].split('+')
                    microsec = microsec_and_tz[0][:6]  # 최대 6자리
                    channel_published_str = f"{parts[0]}.{microsec}+{microsec_and_tz[1]}"

            channel_published_at = datetime.fromisoformat(channel_published_str)
        except Exception:
            # 파싱 실패 시 영상 업로드 날짜 사용
            channel_published_at = published_at

        days_since_channel_created = (collection_date - channel_published_at).days

        # 채널명 (7번째와 33번째 모두 동일한 값)
        channel_title = snippet['channelTitle']

        return {
            '영상 ID': video_id,
            '영상 업로드날짜': published_at.strftime('%Y-%m-%d'),
            '수집날짜': collection_date.strftime('%Y-%m-%d'),
            '검색 키워드': keyword,
            '영상 링크': f"https://www.youtube.com/watch?v={video_id}",
            '제목': snippet['title'],
            '채널명': channel_title,  # 7번째 채널명
            '조회수': view_count,
            '영상길이': self._format_duration(duration_seconds),
            '좋아요 수': like_count,
            '댓글수': comment_count,
            '구독자수': subscriber_count,
            '구독자 대비 조회수 배율': round(subscriber_view_ratio, 2) if subscriber_count > 0 else 0,
            '조회수 대비 좋아요': round(like_ratio / 100, 4) if view_count > 0 else 0,  # 백분율을 소수로 (1% = 0.01)
            '조회수 대비 댓글': round(comment_ratio / 100, 4) if view_count > 0 else 0,  # 백분율을 소수로
            '33. 채널명': channel_title,  # 33번째 채널명 (중복이지만 시트 헤더와 일치)
            '채널국가': channel_info.get('channel_country', ''),
            '채널 ID': snippet['channelId'],
            '채널링크': channel_info.get('channel_link', ''),
            '채널 디스크립션': channel_info.get('channel_description', ''),
            '채널 핸들': channel_info.get('channel_handle', ''),
            '썸네일 링크': snippet['thumbnails'].get('high', {}).get('url', ''),
            '영상갯수': channel_info.get('video_count', 0),
            '채널 전체 조회수': channel_info.get('total_view_count', 0),
            '영상당 평균 조회수': round(avg_views_per_video, 0),
            '채널 개설일': channel_published_at.strftime('%Y-%m-%d'),
            '카테고리 ID': snippet.get('categoryId', ''),
            '디스크립션': description,
            '디스크립션 텍스트 수': f"{len(description)}자" if len(description) > 0 else '',
            '해시태그 유무': 'ㅇ' if hashtags else '',
            '썸네일 이미지주소': snippet['thumbnails'].get('high', {}).get('url', ''),
        }

    def _parse_duration(self, duration: str) -> int:
        """
        ISO 8601 duration을 초 단위로 변환

        Args:
            duration: ISO 8601 형식 duration (예: PT1H2M10S)

        Returns:
            초 단위 duration
        """
        import re

        hours = re.search(r'(\d+)H', duration)
        minutes = re.search(r'(\d+)M', duration)
        seconds = re.search(r'(\d+)S', duration)

        total_seconds = 0
        if hours:
            total_seconds += int(hours.group(1)) * 3600
        if minutes:
            total_seconds += int(minutes.group(1)) * 60
        if seconds:
            total_seconds += int(seconds.group(1))

        return total_seconds

    def _format_duration(self, seconds: int) -> str:
        """
        초를 "1시간 23분 45초" 또는 "2분 35초" 또는 "37초" 형식으로 변환

        Args:
            seconds: 초 단위 시간

        Returns:
            한글 형식 문자열 (예: "2분 35초", "37초", "1시간 23분 45초")
        """
        hours = seconds // 3600
        minutes = (seconds % 3600) // 60
        secs = seconds % 60

        result_parts = []

        if hours > 0:
            result_parts.append(f"{hours}시간")
        if minutes > 0:
            result_parts.append(f"{minutes}분")
        if secs > 0 or (hours == 0 and minutes == 0):  # 0초인 경우도 표시
            result_parts.append(f"{secs}초")

        return " ".join(result_parts)

    def _extract_hashtags(self, text: str) -> List[str]:
        """
        텍스트에서 해시태그 추출

        Args:
            text: 텍스트

        Returns:
            해시태그 리스트
        """
        import re
        return re.findall(r'#(\w+)', text)

    def _get_category_name(self, category_id: str) -> str:
        """
        카테고리 ID를 카테고리 이름으로 변환

        Args:
            category_id: 카테고리 ID

        Returns:
            카테고리 이름
        """
        categories = {
            '1': '영화 및 애니메이션',
            '2': '자동차',
            '10': '음악',
            '15': '애완동물',
            '17': '스포츠',
            '19': '여행',
            '20': '게임',
            '22': '브이로그',
            '23': '코미디',
            '24': '엔터테인먼트',
            '25': '뉴스 및 정치',
            '26': '하우투 및 스타일',
            '27': '교육',
            '28': '과학 및 기술',
            '29': '비영리 및 행동주의'
        }
        return categories.get(category_id, '')

    def get_playlist_videos(self, playlist_id: str, max_results: Optional[int] = None) -> tuple:
        """
        재생목록의 비디오 정보 가져오기 (최신 추가순)

        Args:
            playlist_id: YouTube 재생목록 ID
            max_results: 최대 결과 수 (None이면 전체)

        Returns:
            (비디오 정보 리스트, API 사용 통계 딕셔너리)
        """
        if not self.youtube:
            raise Exception("YouTube API가 초기화되지 않았습니다.")

        # API 호출 추적 초기화
        api_calls = 0
        quota_cost = 0

        all_video_ids = []
        next_page_token = None

        # 재생목록의 비디오 ID 가져오기 (페이지네이션, 최신 추가순)
        while True:
            playlist_params = {
                'part': 'contentDetails',
                'playlistId': playlist_id,
                'maxResults': 50
            }

            if next_page_token:
                playlist_params['pageToken'] = next_page_token

            playlist_response = self.youtube.playlistItems().list(**playlist_params).execute()
            api_calls += 1
            quota_cost += 1  # playlistItems.list 비용: 1

            for item in playlist_response.get('items', []):
                video_id = item['contentDetails']['videoId']
                all_video_ids.append(video_id)

                # max_results가 지정되어 있고 도달하면 중단
                if max_results and len(all_video_ids) >= max_results:
                    break

            # max_results 도달 또는 다음 페이지 없으면 중단
            if max_results and len(all_video_ids) >= max_results:
                break

            next_page_token = playlist_response.get('nextPageToken')
            if not next_page_token:
                break

        # max_results만큼만 자르기
        if max_results:
            all_video_ids = all_video_ids[:max_results]

        if not all_video_ids:
            stats = {
                'api_calls': api_calls,
                'quota_cost': quota_cost,
                'quota_percent': round((quota_cost / self.DAILY_QUOTA) * 100, 2)
            }
            return [], stats

        # 비디오 상세 정보 가져오기 (50개씩 배치 처리)
        results = []
        unique_channels = set()

        for i in range(0, len(all_video_ids), 50):
            batch_ids = all_video_ids[i:i+50]

            videos_response = self.youtube.videos().list(
                part='snippet,contentDetails,statistics,status',
                id=','.join(batch_ids)
            ).execute()
            api_calls += 1
            quota_cost += 1  # videos.list 비용: 1

            for video in videos_response.get('items', []):
                unique_channels.add(video['snippet']['channelId'])
                # 재생목록에서 가져온 비디오는 검색 키워드 대신 '재생목록' 표시
                video_data = self._extract_video_data(video, f'재생목록:{playlist_id}')
                results.append(video_data)

        # 채널 정보 조회 비용 추가
        quota_cost += len(unique_channels)
        api_calls += len(unique_channels)

        # 쿼터 사용량 업데이트
        self.quota_used += quota_cost

        stats = {
            'api_calls': api_calls,
            'quota_cost': quota_cost,
            'quota_percent': round((quota_cost / self.DAILY_QUOTA) * 100, 2),
            'unique_channels': len(unique_channels)
        }

        return results, stats

    def get_channel_videos(self, channel_id: str, max_results: Optional[int] = None,
                          order: str = 'date') -> tuple:
        """
        채널의 비디오 정보 가져오기 (업로드 재생목록 사용)

        Args:
            channel_id: YouTube 채널 ID
            max_results: 최대 결과 수 (None이면 전체)
            order: 정렬 순서 (date: 최신순만 지원, 다른 옵션은 무시됨)
                   주의: 업로드 재생목록은 최신순만 지원 (쿼터 절약을 위함)

        Returns:
            (비디오 정보 리스트, API 사용 통계 딕셔너리)
        """
        if not self.youtube:
            raise Exception("YouTube API가 초기화되지 않았습니다.")

        # API 호출 추적 초기화
        api_calls = 0
        quota_cost = 0

        # 채널 ID를 업로드 재생목록 ID로 변환 (UC -> UU)
        # 예: UCxyz... -> UUxyz...
        if channel_id.startswith('UC'):
            uploads_playlist_id = 'UU' + channel_id[2:]
        else:
            # UC로 시작하지 않는 경우 기존 search 방식 사용
            print(f"경고: 채널 ID가 UC로 시작하지 않습니다. 기존 방식 사용: {channel_id}")
            return self._get_channel_videos_legacy(channel_id, max_results, order)

        all_video_ids = []
        next_page_token = None

        # 업로드 재생목록에서 비디오 ID 가져오기 (페이지네이션)
        # 비용: playlistItems.list = 1 유닛 (search.list의 1/100)
        while True:
            playlist_params = {
                'part': 'contentDetails',
                'playlistId': uploads_playlist_id,
                'maxResults': 50
            }

            if next_page_token:
                playlist_params['pageToken'] = next_page_token

            try:
                playlist_response = self.youtube.playlistItems().list(**playlist_params).execute()
                api_calls += 1
                quota_cost += 1  # playlistItems.list 비용: 1 (search.list의 1/100!)
            except Exception as e:
                # 업로드 재생목록을 찾을 수 없는 경우 기존 방식으로 폴백
                if 'playlistNotFound' in str(e) or 'notFound' in str(e):
                    print(f"업로드 재생목록을 찾을 수 없습니다. 기존 방식 사용: {channel_id}")
                    return self._get_channel_videos_legacy(channel_id, max_results, order)
                raise

            for item in playlist_response.get('items', []):
                video_id = item['contentDetails']['videoId']
                all_video_ids.append(video_id)

                # max_results가 지정되어 있고 도달하면 중단
                if max_results and len(all_video_ids) >= max_results:
                    break

            # max_results 도달 또는 다음 페이지 없으면 중단
            if max_results and len(all_video_ids) >= max_results:
                break

            next_page_token = playlist_response.get('nextPageToken')
            if not next_page_token:
                break

        # max_results만큼만 자르기
        if max_results:
            all_video_ids = all_video_ids[:max_results]

        if not all_video_ids:
            stats = {
                'api_calls': api_calls,
                'quota_cost': quota_cost,
                'quota_percent': round((quota_cost / self.DAILY_QUOTA) * 100, 2)
            }
            return [], stats

        # 비디오 상세 정보 가져오기 (50개씩 배치 처리)
        results = []
        unique_channels = set()

        for i in range(0, len(all_video_ids), 50):
            batch_ids = all_video_ids[i:i+50]

            videos_response = self.youtube.videos().list(
                part='snippet,contentDetails,statistics,status',
                id=','.join(batch_ids)
            ).execute()
            api_calls += 1
            quota_cost += 1  # videos.list 비용: 1

            for video in videos_response.get('items', []):
                unique_channels.add(video['snippet']['channelId'])
                # 채널에서 가져온 비디오는 검색 키워드 대신 '채널' 표시
                video_data = self._extract_video_data(video, f'채널:{channel_id}')
                results.append(video_data)

        # 채널 정보 조회 비용 추가
        quota_cost += len(unique_channels)
        api_calls += len(unique_channels)

        # 쿼터 사용량 업데이트
        self.quota_used += quota_cost

        stats = {
            'api_calls': api_calls,
            'quota_cost': quota_cost,
            'quota_percent': round((quota_cost / self.DAILY_QUOTA) * 100, 2),
            'unique_channels': len(unique_channels)
        }

        return results, stats

    def _get_channel_videos_legacy(self, channel_id: str, max_results: Optional[int] = None,
                                   order: str = 'date') -> tuple:
        """
        채널의 비디오 정보 가져오기 (기존 search 방식 - 높은 비용)

        이 메서드는 업로드 재생목록을 사용할 수 없는 경우에만 사용됩니다.
        비용이 매우 높으므로 (100 유닛/페이지) 가능하면 피하세요.

        Args:
            channel_id: YouTube 채널 ID
            max_results: 최대 결과 수 (None이면 전체)
            order: 정렬 순서 (date: 최신순, viewCount: 조회수순, rating: 평점순)

        Returns:
            (비디오 정보 리스트, API 사용 통계 딕셔너리)
        """
        if not self.youtube:
            raise Exception("YouTube API가 초기화되지 않았습니다.")

        # API 호출 추적 초기화
        api_calls = 0
        quota_cost = 0

        all_video_ids = []
        next_page_token = None

        # 채널의 비디오 ID 가져오기 (페이지네이션) - 높은 비용!
        while True:
            search_params = {
                'part': 'id',
                'channelId': channel_id,
                'type': 'video',
                'maxResults': 50,
                'order': order
            }

            if next_page_token:
                search_params['pageToken'] = next_page_token

            search_response = self.youtube.search().list(**search_params).execute()
            api_calls += 1
            quota_cost += 100  # search.list 비용: 100 (매우 높음!)

            for item in search_response.get('items', []):
                if item['id']['kind'] == 'youtube#video':
                    video_id = item['id']['videoId']
                    all_video_ids.append(video_id)

                    # max_results가 지정되어 있고 도달하면 중단
                    if max_results and len(all_video_ids) >= max_results:
                        break

            # max_results 도달 또는 다음 페이지 없으면 중단
            if max_results and len(all_video_ids) >= max_results:
                break

            next_page_token = search_response.get('nextPageToken')
            if not next_page_token:
                break

        # max_results만큼만 자르기
        if max_results:
            all_video_ids = all_video_ids[:max_results]

        if not all_video_ids:
            stats = {
                'api_calls': api_calls,
                'quota_cost': quota_cost,
                'quota_percent': round((quota_cost / self.DAILY_QUOTA) * 100, 2)
            }
            return [], stats

        # 비디오 상세 정보 가져오기 (50개씩 배치 처리)
        results = []
        unique_channels = set()

        for i in range(0, len(all_video_ids), 50):
            batch_ids = all_video_ids[i:i+50]

            videos_response = self.youtube.videos().list(
                part='snippet,contentDetails,statistics,status',
                id=','.join(batch_ids)
            ).execute()
            api_calls += 1
            quota_cost += 1  # videos.list 비용: 1

            for video in videos_response.get('items', []):
                unique_channels.add(video['snippet']['channelId'])
                video_data = self._extract_video_data(video, f'채널:{channel_id}')
                results.append(video_data)

        # 채널 정보 조회 비용 추가
        quota_cost += len(unique_channels)
        api_calls += len(unique_channels)

        # 쿼터 사용량 업데이트
        self.quota_used += quota_cost

        stats = {
            'api_calls': api_calls,
            'quota_cost': quota_cost,
            'quota_percent': round((quota_cost / self.DAILY_QUOTA) * 100, 2),
            'unique_channels': len(unique_channels)
        }

        return results, stats


class InterruptController:
    """작업 중단 제어"""

    def __init__(self):
        self.interrupted = False

    def interrupt(self):
        """중단 요청"""
        self.interrupted = True

    def is_interrupted(self) -> bool:
        """중단 여부 확인"""
        return self.interrupted

    def reset(self):
        """중단 상태 리셋"""
        self.interrupted = False


# main.py 통합
if __name__ == "__main__":
    from GUI_Interface import GoogleSheetsManager, YouTubeSearchGUI

    def main():
        """메인 함수"""

        # 설정
        current_dir = os.path.dirname(os.path.abspath(__file__))
        # 공유 구글 자격증명 폴더 지원
        key_dir = os.path.join(current_dir, 'google_service_key')
        if not os.path.exists(os.path.join(key_dir, 'service-account-key.json')):
            parent_key_dir = os.path.abspath(os.path.join(current_dir, '..', '..', 'google_service_key'))
            if os.path.exists(os.path.join(parent_key_dir, 'service-account-key.json')):
                key_dir = parent_key_dir

        SERVICE_ACCOUNT_FILE = os.path.join(key_dir, 'service-account-key.json')

        # OAuth 클라이언트 파일 찾기 (client_secret로 시작하는 JSON 파일)
        client_secret_dir = key_dir
        CLIENT_SECRET_FILE = None
        API_KEY_FILE = os.path.join(client_secret_dir, 'api_key.txt')

        if os.path.exists(client_secret_dir):
            for file in os.listdir(client_secret_dir):
                if file.startswith('client_secret') and file.endswith('.json'):
                    CLIENT_SECRET_FILE = os.path.join(client_secret_dir, file)
                    break

        SPREADSHEET_URL = 'https://docs.google.com/spreadsheets/d/1o-Vm5eMfH6Mm1gCnsv-SuRqtVMYTBDnWThBxjwCOptY/edit?gid=1434237297#gid=1434237297'
        SHEET_NAME = '키워드 검색결과'

        # 파일 존재 확인
        if not os.path.exists(SERVICE_ACCOUNT_FILE):
            print(f"오류: 서비스 계정 파일을 찾을 수 없습니다: {SERVICE_ACCOUNT_FILE}")
            return

        print("YouTube 검색 API 초기화 중...")

        # YouTube API 초기화
        youtube_api = YouTubeSearchAPI()
        auth_success = False

        # 1. API Key 방식 시도 (더 안정적)
        if os.path.exists(API_KEY_FILE):
            try:
                print("API Key 방식으로 인증 시도 중...")
                with open(API_KEY_FILE, 'r') as f:
                    api_key = f.read().strip()
                youtube_api.authenticate_api_key(api_key)
                print("YouTube API 인증 완료 (API Key)")
                auth_success = True
            except Exception as e:
                print(f"API Key 인증 실패: {e}")

        # 2. OAuth 방식 시도
        if not auth_success and CLIENT_SECRET_FILE and os.path.exists(CLIENT_SECRET_FILE):
            try:
                print("OAuth 방식으로 인증 시도 중...")
                print("브라우저가 열리면 Google 계정으로 로그인하세요.")
                youtube_api.authenticate_oauth(CLIENT_SECRET_FILE)
                print("YouTube API 인증 완료 (OAuth)")
                auth_success = True
            except Exception as e:
                print(f"OAuth 인증 실패: {e}")
                print("\n해결 방법:")
                print("1. Google Cloud Console에서 OAuth 2.0 클라이언트 ID를 다시 생성하세요.")
                print("2. 또는 API Key를 사용하세요:")
                print("   - Google Cloud Console에서 API Key 생성")
                print(f"   - {API_KEY_FILE} 파일에 API Key 저장")

        if not auth_success:
            print("\nYouTube API 인증에 실패했습니다.")
            print("google_service_key 폴더에 다음 중 하나를 준비하세요:")
            print("1. client_secret_*.json (OAuth 클라이언트)")
            print("2. api_key.txt (API Key)")
            input("\n종료하려면 Enter를 누르세요...")
            return

        print("구글 시트 연결 중...")

        # Google Sheets Manager 초기화
        try:
            sheets_manager = GoogleSheetsManager(SERVICE_ACCOUNT_FILE, SPREADSHEET_URL)
            print("구글 시트 연결 완료")

            # 헤더 초기화 (필요시)
            headers = sheets_manager.get_headers(SHEET_NAME)
            if not headers or len(headers) < 10:
                print("헤더 초기화 중...")
                sheets_manager.initialize_headers(SHEET_NAME)
                print("헤더 초기화 완료")

        except Exception as e:
            print(f"구글 시트 연결 실패: {e}")
            input("\n종료하려면 Enter를 누르세요...")
            return

        print("GUI 시작...")

        # 로그 파일 경로 생성
        log_dir = os.path.join(current_dir, 'logs')
        if not os.path.exists(log_dir):
            os.makedirs(log_dir)

        from datetime import datetime as dt
        timestamp = dt.now().strftime('%Y-%m-%d_%H-%M-%S')
        log_file = os.path.join(log_dir, f'debug_{timestamp}.log')

        # GUI 실행
        try:
            gui = YouTubeSearchGUI(youtube_api, sheets_manager, log_file=log_file)
            gui.run()
        except Exception as e:
            print(f"\nGUI 실행 중 오류 발생:")
            print(f"오류 타입: {type(e).__name__}")
            print(f"오류 메시지: {str(e)}")
            import traceback
            print("\n상세 오류:")
            traceback.print_exc()

            # 로그 파일에도 오류 기록
            try:
                with open(log_file, 'a', encoding='utf-8') as f:
                    f.write(f"\n=== GUI 실행 오류 ===\n")
                    f.write(f"오류 타입: {type(e).__name__}\n")
                    f.write(f"오류 메시지: {str(e)}\n")
                    f.write(f"상세:\n")
                    traceback.print_exc(file=f)
            except:
                pass

            input("\n종료하려면 Enter를 누르세요...")

    main()
