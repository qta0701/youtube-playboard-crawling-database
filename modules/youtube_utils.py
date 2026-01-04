"""
YouTube Utility Functions
Zero-Cost ID Extraction 및 유틸리티 함수들

핵심 기능:
- Playboard/YouTube URL에서 Channel ID 추출 (API 비용 0)
- Duration ISO 8601 파싱 (영상/쇼츠 구분)
"""
import re
import requests
from typing import Optional
from bs4 import BeautifulSoup
from logger_config import setup_logger

logger = setup_logger('youtube_utils')

# HTTP 요청 헤더 (봇 감지 우회)
DEFAULT_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
    'Accept-Language': 'ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7',
}


def get_channel_id_from_url(url: str) -> Optional[str]:
    """
    URL에서 YouTube Channel ID 추출 (Zero-Cost)

    지원하는 URL 형식:
    - https://playboard.co/en/channel/UCxxxxxx
    - https://www.youtube.com/channel/UCxxxxxx
    - https://www.youtube.com/@handle (HTML 파싱 필요)
    - https://www.youtube.com/c/ChannelName (HTML 파싱 필요)

    Args:
        url: Playboard 또는 YouTube 채널 URL

    Returns:
        str: Channel ID (UC로 시작) 또는 None
    """
    if not url:
        return None

    # 1. URL에서 직접 추출 시도 (UC로 시작하는 ID)
    channel_id = _extract_channel_id_from_url(url)
    if channel_id:
        logger.debug(f"Channel ID extracted from URL: {channel_id}")
        return channel_id

    # 2. Playboard URL인 경우 -> YouTube URL로 변환 후 HTML 파싱
    if 'playboard.co' in url:
        youtube_url = _convert_playboard_to_youtube(url)
        if youtube_url:
            channel_id = _parse_channel_id_from_html(youtube_url)
            if channel_id:
                logger.debug(f"Channel ID from Playboard conversion: {channel_id}")
                return channel_id

    # 3. YouTube URL 직접 HTML 파싱
    if 'youtube.com' in url or 'youtu.be' in url:
        channel_id = _parse_channel_id_from_html(url)
        if channel_id:
            logger.debug(f"Channel ID from HTML parsing: {channel_id}")
            return channel_id

    logger.warning(f"Failed to extract Channel ID from: {url}")
    return None


def _extract_channel_id_from_url(url: str) -> Optional[str]:
    """URL 문자열에서 Channel ID 패턴 추출"""
    # UC로 시작하는 24자 ID 패턴
    patterns = [
        r'/channel/(UC[\w-]{22})',  # /channel/UCxxxxxx
        r'channel/(UC[\w-]{22})',   # channel/UCxxxxxx (Playboard)
        r'(UC[\w-]{22})',           # 단독 UC ID
    ]

    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            return match.group(1)
    return None


def _convert_playboard_to_youtube(playboard_url: str) -> Optional[str]:
    """
    Playboard URL을 YouTube 채널 URL로 변환

    Playboard: https://playboard.co/en/channel/UCxxxxxx
    YouTube: https://www.youtube.com/channel/UCxxxxxx
    """
    # UC ID가 URL에 있으면 바로 변환
    channel_id = _extract_channel_id_from_url(playboard_url)
    if channel_id:
        return f"https://www.youtube.com/channel/{channel_id}"

    # Playboard 페이지에서 YouTube 링크 추출 필요
    try:
        response = requests.get(playboard_url, headers=DEFAULT_HEADERS, timeout=10)
        if response.status_code == 200:
            soup = BeautifulSoup(response.text, 'html.parser')

            # YouTube 채널 링크 찾기
            yt_link = soup.find('a', href=lambda x: x and 'youtube.com/channel/' in x)
            if yt_link:
                return yt_link['href']

            # 메타 태그에서 찾기
            meta = soup.find('meta', attrs={'itemprop': 'channelId'})
            if meta and meta.get('content'):
                return f"https://www.youtube.com/channel/{meta['content']}"

    except Exception as e:
        logger.error(f"Playboard parsing error: {e}")

    return None


def _parse_channel_id_from_html(youtube_url: str) -> Optional[str]:
    """
    YouTube 페이지 HTML에서 Channel ID 추출

    찾는 위치:
    1. <meta itemprop="channelId" content="UCxxxxxx">
    2. <link rel="canonical" href="...channel/UCxxxxxx">
    3. JavaScript 데이터에서 추출
    """
    try:
        response = requests.get(youtube_url, headers=DEFAULT_HEADERS, timeout=10)
        if response.status_code != 200:
            logger.warning(f"HTTP {response.status_code} for {youtube_url}")
            return None

        html = response.text

        # 1. meta itemprop="channelId"
        match = re.search(r'<meta\s+itemprop="channelId"\s+content="(UC[\w-]{22})"', html)
        if match:
            return match.group(1)

        # 2. canonical link
        match = re.search(r'<link\s+rel="canonical"\s+href="[^"]*channel/(UC[\w-]{22})"', html)
        if match:
            return match.group(1)

        # 3. JavaScript 데이터 (ytInitialData)
        match = re.search(r'"channelId":"(UC[\w-]{22})"', html)
        if match:
            return match.group(1)

        # 4. externalId
        match = re.search(r'"externalId":"(UC[\w-]{22})"', html)
        if match:
            return match.group(1)

    except Exception as e:
        logger.error(f"HTML parsing error for {youtube_url}: {e}")

    return None


def parse_duration_iso(iso_duration: str) -> int:
    """
    ISO 8601 Duration을 초 단위로 변환

    Args:
        iso_duration: ISO 8601 형식 (예: PT1M30S, PT1H2M3S)

    Returns:
        int: 초 단위 시간

    Examples:
        PT1M30S -> 90
        PT1H2M3S -> 3723
        PT30S -> 30
    """
    if not iso_duration:
        return 0

    # PT로 시작하는지 확인
    if not iso_duration.startswith('PT'):
        return 0

    duration = iso_duration[2:]  # PT 제거

    hours = 0
    minutes = 0
    seconds = 0

    # 시간 추출
    match = re.search(r'(\d+)H', duration)
    if match:
        hours = int(match.group(1))

    # 분 추출
    match = re.search(r'(\d+)M', duration)
    if match:
        minutes = int(match.group(1))

    # 초 추출
    match = re.search(r'(\d+)S', duration)
    if match:
        seconds = int(match.group(1))

    total_seconds = hours * 3600 + minutes * 60 + seconds
    return total_seconds


def classify_video_type(duration_sec: int) -> str:
    """
    영상 길이로 유형 분류

    Args:
        duration_sec: 영상 길이 (초)

    Returns:
        str: 'shorts' (60초 이하) 또는 'video'
    """
    if duration_sec <= 60:
        return 'shorts'
    return 'video'


def format_duration(seconds: int) -> str:
    """
    초를 HH:MM:SS 또는 MM:SS 형식으로 변환

    Args:
        seconds: 초 단위 시간

    Returns:
        str: 포맷된 문자열
    """
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    secs = seconds % 60

    if hours > 0:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def extract_video_id_from_url(url: str) -> Optional[str]:
    """
    YouTube URL에서 Video ID 추출

    지원 형식:
    - https://www.youtube.com/watch?v=VIDEO_ID
    - https://youtu.be/VIDEO_ID
    - https://www.youtube.com/shorts/VIDEO_ID
    - https://www.youtube.com/embed/VIDEO_ID

    Args:
        url: YouTube URL

    Returns:
        str: Video ID (11자) 또는 None
    """
    if not url:
        return None

    patterns = [
        r'(?:v=|/videos/|/shorts/|/embed/|youtu\.be/)([A-Za-z0-9_-]{11})',
    ]

    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            return match.group(1)

    return None


def build_youtube_channel_url(channel_id: str) -> str:
    """Channel ID로 YouTube 채널 URL 생성"""
    return f"https://www.youtube.com/channel/{channel_id}"


def build_youtube_video_url(video_id: str) -> str:
    """Video ID로 YouTube 영상 URL 생성"""
    return f"https://www.youtube.com/watch?v={video_id}"
