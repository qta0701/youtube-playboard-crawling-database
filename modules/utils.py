"""
유틸리티 함수 모음
파일명 정제, 숫자 변환 등
"""
import re
import os
import random
from datetime import datetime
from logger_config import setup_logger

logger = setup_logger('utils')


def play_sound():
    """
    크롤링 완료 시 시스템 알림음 재생 - rules.md 정책 반영
    윈도우 기본 미디어 경로의 Speech Off.wav를 사용
    """
    try:
        import winsound
        windir = os.environ.get('SystemRoot', 'C:\\Windows')
        wav_path = os.path.join(windir, 'Media', 'Speech Off.wav')
        if os.path.exists(wav_path):
            winsound.PlaySound(wav_path, winsound.SND_FILENAME | winsound.SND_ASYNC)
            logger.info(f"Completion sound played ({wav_path})")
        else:
            winsound.PlaySound("MailBeep", winsound.SND_ALIAS)
            logger.info("Completion sound played (MailBeep fallback)")
    except Exception as e:
        try:
            print('\a')
            logger.info("Completion beep played (fallback)")
        except Exception:
            logger.debug(f"Sound playback not available: {e}")


def clean_text(text):
    """
    공백 제거 및 불필요한 문자 처리 - PLAN.md 4.1

    Args:
        text (str): 정제할 텍스트

    Returns:
        str: 정제된 텍스트
    """
    if not text:
        return ""

    # 다중 공백을 단일 공백으로
    text = re.sub(r'\s+', ' ', text)

    # 앞뒤 공백 제거
    text = text.strip()

    return text


def sanitize_filename(filename):
    """
    파일명에서 OS에서 사용할 수 없는 특수문자 제거

    Args:
        filename (str): 원본 파일명

    Returns:
        str: 정제된 파일명
    """
    # Windows/Linux에서 파일명으로 사용 불가능한 문자들
    invalid_chars = r'[<>:"/\\|?*]'

    # 특수문자를 언더스코어로 치환
    sanitized = re.sub(invalid_chars, '_', filename)

    # 연속된 언더스코어를 하나로
    sanitized = re.sub(r'_+', '_', sanitized)

    # 앞뒤 공백 및 언더스코어 제거
    sanitized = sanitized.strip(' _')

    return sanitized


def clean_metric_string(text_value):
    """
    더럽혀진 문자열에서 순수 숫자와 단위만 추출

    Args:
        text_value (str): 정제할 텍스트

    Returns:
        int: 정제된 숫자 (정수)

    Examples:
        "1.2만 views #kpop #idol" -> 12000
        "500K views" -> 500000
        "#music 1.5M likes" -> 1500000
        "New" -> 0
        "N/A" -> 0
    """
    if not text_value or text_value == 'N/A' or text_value == 'NEW':
        return 0

    # 문자열로 변환
    text_value = str(text_value).strip()

    # 1. 해시태그 제거 (#으로 시작해서 공백 전까지)
    clean_text = re.sub(r'#\S+', '', text_value).strip()

    # 2. 불필요한 텍스트 제거 (views, likes, subscribers 등)
    clean_text = re.sub(r'\b(views|likes|subscribers|subs|조회수|좋아요|구독자)\b', '', clean_text, flags=re.IGNORECASE).strip()

    # 3. 숫자와 단위 추출
    # 한글 단위 (억, 만)
    match = re.search(r'([\d\.]+)\s*억', clean_text)
    if match:
        return int(float(match.group(1)) * 100_000_000)

    match = re.search(r'([\d\.]+)\s*만', clean_text)
    if match:
        return int(float(match.group(1)) * 10_000)

    # 영문 단위 (B, M, K)
    match = re.search(r'([\d\.]+)\s*([BMK])', clean_text, re.IGNORECASE)
    if match:
        number = float(match.group(1))
        unit = match.group(2).upper()

        multipliers = {
            'B': 1_000_000_000,
            'M': 1_000_000,
            'K': 1_000
        }
        return int(number * multipliers.get(unit, 1))

    # 4. 순수 숫자 (쉼표 포함)
    match = re.search(r'([\d,\.]+)', clean_text)
    if match:
        number_str = match.group(1).replace(',', '')
        try:
            return int(float(number_str))
        except (ValueError, AttributeError):
            return 0

    return 0


def parse_count_string(count_str):
    """
    조회수, 좋아요 수 등의 문자열을 숫자로 변환 (한글/영문 단위 지원)

    예시:
        "1.2M" -> 1200000
        "500K" -> 500000
        "1.2만" -> 12000
        "3.5억" -> 350000000
        "#kpop 500K" -> 500000
        "1,234" -> 1234

    Args:
        count_str (str): 숫자 문자열 (예: "1.2M", "500K", "1.2만")

    Returns:
        int: 변환된 숫자
    """
    if not count_str or count_str == 'N/A':
        return 0

    # 문자열로 변환 (이미 숫자인 경우 대비)
    count_str = str(count_str).strip()

    # 해시태그 및 불필요한 문자 제거
    count_str = re.sub(r'#\S+', '', count_str).strip()

    # 쉼표 제거
    count_str = count_str.replace(',', '')

    try:
        # 한글 단위 먼저 처리 (억, 만)
        if '억' in count_str:
            return int(float(count_str.replace('억', '')) * 100_000_000)
        elif '만' in count_str:
            return int(float(count_str.replace('만', '')) * 10_000)
        # 영문 단위 처리 (B, M, K)
        elif 'B' in count_str.upper():
            return int(float(count_str.upper().replace('B', '')) * 1_000_000_000)
        elif 'M' in count_str.upper():
            return int(float(count_str.upper().replace('M', '')) * 1_000_000)
        elif 'K' in count_str.upper():
            return int(float(count_str.upper().replace('K', '')) * 1_000)
        # 일반 숫자
        else:
            return int(float(count_str))
    except (ValueError, AttributeError):
        return 0


def ensure_directory_exists(directory_path):
    """
    디렉토리가 존재하지 않으면 생성

    Args:
        directory_path (str): 디렉토리 경로

    Returns:
        bool: 생성 성공 여부
    """
    try:
        os.makedirs(directory_path, exist_ok=True)
        return True
    except Exception as e:
        print(f"디렉토리 생성 실패: {directory_path}, 오류: {e}")
        return False


def generate_safe_filepath(base_dir, target_type, category, country, period, criteria=None, ranking_date=None, extension='csv'):
    """
    안전한 파일 경로 생성

    Args:
        base_dir (str): 기본 디렉토리
        target_type (str): 타겟 타입 (shorts, video, channel)
        category (str): 카테고리
        country (str): 국가
        period (str): 기간
        criteria (str, optional): 수집 기준 (예: 조회수 순위, 좋아요 순위, 댓글 순위)
        ranking_date (str, optional): 랭킹 날짜 (예: YYYY-MM-DD)
        extension (str): 확장자 (기본값: 'csv')

    Returns:
        tuple: (파일 경로, 파일명)
    """
    # 디렉토리 확인 및 생성
    ensure_directory_exists(base_dir)

    # PLAN.md Phase 3.2 - 날짜별 폴더링 추가
    # output/2026_01_04/ 형식으로 저장
    if ranking_date:
        try:
            clean_date = ranking_date.replace('-', '_').replace('/', '_')
            if re.match(r'^\d{4}_\d{2}_\d{2}$', clean_date):
                date_folder = clean_date
            else:
                date_folder = datetime.now().strftime('%Y_%m_%d')
        except Exception:
            date_folder = datetime.now().strftime('%Y_%m_%d')
    else:
        date_folder = datetime.now().strftime('%Y_%m_%d')
    
    # 타입별 서브 폴더링 (Shorts, Video, Channel)
    if 'shorts' in target_type.lower():
        type_folder = 'Shorts'
    elif 'channel' in target_type.lower():
        type_folder = 'Channel'
    elif 'video' in target_type.lower():
        type_folder = 'Video'
    else:
        type_folder = 'Others'

    target_dir = os.path.join(base_dir, date_folder, type_folder)
    ensure_directory_exists(target_dir)

    # 파일명 구성 요소 정제
    safe_target = sanitize_filename(target_type)
    safe_category = sanitize_filename(category)
    safe_country = sanitize_filename(country)
    safe_period = sanitize_filename(period)

    # 타임스탬프 추가 (PLAN.md 4.2 - 가독성 개선)
    # 파일명 생성 시 시간단위 제거하고 Ranking Date (date_folder)만 붙도록 수정
    if criteria:
        safe_criteria = sanitize_filename(criteria)
        filename = f"{safe_target}_{safe_category}_{safe_country}_{safe_period}_{safe_criteria}_{date_folder}.{extension}"
    else:
        filename = f"{safe_target}_{safe_category}_{safe_country}_{safe_period}_{date_folder}.{extension}"

    # 전체 경로 (날짜별 폴더 내부에 저장)
    filepath = os.path.join(target_dir, filename)

    return filepath, filename


def format_number(number):
    """
    숫자를 읽기 쉬운 형식으로 변환

    예시:
        1200000 -> "1.2M"
        500000 -> "500K"

    Args:
        number (int/float): 숫자

    Returns:
        str: 포맷된 문자열
    """
    try:
        number = float(number)
        if number >= 1_000_000_000:
            return f"{number / 1_000_000_000:.1f}B"
        elif number >= 1_000_000:
            return f"{number / 1_000_000:.1f}M"
        elif number >= 1_000:
            return f"{number / 1_000:.1f}K"
        else:
            return str(int(number))
    except (ValueError, TypeError):
        return "0"


def validate_video_id(video_id):
    """
    YouTube 비디오 ID 유효성 검증

    Args:
        video_id (str): YouTube 비디오 ID

    Returns:
        bool: 유효성 여부
    """
    if not video_id or video_id == 'N/A':
        return False

    # YouTube 비디오 ID는 11자리
    if len(video_id) != 11:
        return False

    # 알파벳, 숫자, -, _ 만 포함
    pattern = re.compile(r'^[a-zA-Z0-9_-]{11}$')
    return bool(pattern.match(video_id))


def extract_video_id_from_url(url):
    """
    YouTube URL에서 비디오 ID 추출

    Args:
        url (str): YouTube URL

    Returns:
        str or None: 비디오 ID 또는 None
    """
    patterns = [
        r'(?:v=|\/)([0-9A-Za-z_-]{11}).*',
        r'(?:embed\/)([0-9A-Za-z_-]{11})',
        r'(?:shorts\/)([0-9A-Za-z_-]{11})'
    ]

    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            return match.group(1)

    return None


def parse_korean_number_string(text):
    """
    한글 단위 및 쉼표 포함 숫자 문자열을 정수로 변환 (PLAN.md 3.0 - HTML 분석 기반)

    Args:
        text (str): 변환할 텍스트

    Returns:
        int: 변환된 정수 값

    Examples:
        '11,575,907' -> 11575907
        '23.8만' -> 238000
        '1.5억' -> 150000000
        'N/A' -> 0
        '' -> 0
    """
    if not text:
        return 0

    text = text.strip()

    # 1. 쉼표 제거 및 정수형일 경우
    if text.replace(',', '').isdigit():
        return int(text.replace(',', ''))

    # 2. 한글 단위 변환
    multiplier = 1
    if '억' in text:
        multiplier = 100_000_000
        text = text.replace('억', '')
    elif '만' in text:
        multiplier = 10_000
        text = text.replace('만', '')

    # 3. 숫자 부분 추출 및 변환
    try:
        # 숫자와 소수점만 추출
        number_match = re.search(r'[\d.]+', text)
        if number_match:
            return int(float(number_match.group(0)) * multiplier)
    except (ValueError, AttributeError):
        pass

    return 0


def get_latest_file(directory, pattern='*.csv'):
    """
    디렉토리에서 가장 최신 파일 찾기

    Args:
        directory (str): 디렉토리 경로
        pattern (str): 파일 패턴 (기본값: '*.csv')

    Returns:
        str or None: 최신 파일 경로 또는 None
    """
    import glob

    files = glob.glob(os.path.join(directory, pattern))
    if not files:
        return None

    # 수정 시간 기준으로 정렬
    latest_file = max(files, key=os.path.getmtime)
    return latest_file


def truncate_base64(text, max_length=30):
    """
    Base64 데이터를 로그용으로 축약 (PLAN.md Phase 3.1 - Base64 노이즈 제거)

    Args:
        text (str): 원본 텍스트 (Base64 포함 가능)
        max_length (int): 최대 출력 길이 (기본값: 30)

    Returns:
        str: 축약된 텍스트

    Examples:
        "data:image/gif;base64,R0lGODlhAQABA..." -> "data:image/gif;base64,R0lG...[Base64 Truncated]"
        "https://example.com/image.jpg" -> "https://example.com/image.jpg"
        "N/A" -> "N/A"
    """
    if not text or text == 'N/A':
        return text

    # Base64 데이터 감지
    if 'data:image' in text or ';base64,' in text:
        # 앞 30자만 표시하고 나머지는 truncate 표시
        if len(text) > max_length:
            return text[:max_length] + "...[Base64 Data Truncated]"

    return text


def get_random_headers():
    """
    랜덤 User-Agent 및 헤더 반환 (PLAN.md Section 4.1 - Bot Detection Avoidance)

    웹 스크레이핑 시 봇 탐지를 회피하기 위해 다양한 User-Agent를 순환 사용

    Returns:
        dict: HTTP 요청 헤더
    """
    user_agents = [
        # Chrome on Windows
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",

        # Chrome on macOS
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",

        # Firefox on Windows
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:120.0) Gecko/20100101 Firefox/120.0",

        # Safari on macOS
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Safari/605.1.15",

        # Edge on Windows
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 Edg/120.0.0.0",
    ]

    return {
        'User-Agent': random.choice(user_agents),
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
        'Accept-Language': 'ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7',
        'Accept-Encoding': 'gzip, deflate, br',
        'Referer': 'https://www.youtube.com/',
        'DNT': '1',
        'Connection': 'keep-alive',
        'Upgrade-Insecure-Requests': '1',
        'Sec-Fetch-Dest': 'document',
        'Sec-Fetch-Mode': 'navigate',
        'Sec-Fetch-Site': 'none',
        'Cache-Control': 'max-age=0'
    }


def get_chrome_profile_path(subdir="playboard"):
    """
    다른 PC 환경 간 이식성 보장을 위한 크롬 프로필 로컬 C 드라이브 리다이렉션 경로 반환
    """
    home = os.path.expanduser("~")
    profile_dir = os.path.join(home, ".adsense_auto_workflow", "chrome_profiles", subdir)
    os.makedirs(profile_dir, exist_ok=True)
    return os.path.abspath(profile_dir)


def show_notification(title, message):
    """
    Windows OS 팝업 알림 띄우기 (PowerShell 활용하여 외부 라이브러리 의존성 없음)
    """
    try:
        import subprocess
        # PowerShell을 이용한 Balloon/Toast 알림
        ps_script = f"""
        [void] [System.Reflection.Assembly]::LoadWithPartialName("System.Windows.Forms")
        $notification = New-Object System.Windows.Forms.NotifyIcon
        $notification.Icon = [System.Drawing.SystemIcons]::Information
        $notification.BalloonTipIcon = "Info"
        $notification.BalloonTipTitle = "{title}"
        $notification.BalloonTipText = "{message}"
        $notification.Visible = $True
        $notification.ShowBalloonTip(5000)
        """
        # PowerShell 명령어를 실행하여 풍선 도움말(알림)을 띄움
        subprocess.run(["powershell", "-Command", ps_script], capture_output=True, text=True)
        logger.info(f"OS Notification shown: {title} - {message}")
    except Exception as e:
        logger.error(f"Failed to show OS notification: {e}")


def play_notification_sound():
    """로그인 요구 및 중단 발생 시 알림음 재생 - rules.md 정책 반영"""
    try:
        import winsound
        windir = os.environ.get('SystemRoot', 'C:\\Windows')
        wav_path = os.path.join(windir, 'Media', 'Speech Off.wav')
        if os.path.exists(wav_path):
            winsound.PlaySound(wav_path, winsound.SND_FILENAME | winsound.SND_ASYNC)
            logger.info(f"Notification sound played ({wav_path})")
        else:
            winsound.PlaySound("SystemAsterisk", winsound.SND_ALIAS)
            logger.info("Notification sound played (SystemAsterisk fallback)")
    except Exception as e:
        try:
            print('\a')
            logger.info("Notification beep played (fallback)")
        except Exception:
            logger.debug(f"Failed to play notification sound: {e}")


def normalize_header_name(header):
    """
    구글 시트 헤더명을 정규화:
    - 선두 넘버링 제거 (예: '01. 영상 ID' -> '영상 ID', '10 - 채널링크' -> '채널링크')
    - 모든 공백, 쉼표, 물음표, 괄호 등 특수문자 제거
    - 소문자화
    """
    if not header:
        return ""
    header = str(header).strip()
    # 1. 선두 넘버링 및 마침표/공백/하이픈 제거
    header = re.sub(r'^\d+[\.\s\-]*', '', header)
    # 2. 공백 및 특수문자 제거
    header = re.sub(r'[\s,\?\(\)\*_\-\"\']', '', header)
    # 3. 소문자화
    header = header.lower()
    return header


def match_db_column_by_header(header, db_columns=None):
    """
    구글 시트 헤더명에 대응하는 DB 컬럼명을 동적으로 반환
    """
    normalized = normalize_header_name(header)
    
    # 미리 정의된 헤더 대 DB 컬럼명 매핑 딕셔너리
    HEADER_TO_DB_COLUMN = {
        # 재생목록 ID 탭
        "재생목록id": "playlist_id",
        "재생목록이름": "playlist_name",
        "영상갯수": "video_count",
        "마지막체크일": "last_checked_at",

        # 채널 리스트 탭
        "채널id": "channel_id",
        "가져왔는지여부": "is_fetched",
        "수집날짜": "crawl_date",
        "수집날짜경과일": "days_since_crawl",
        "채널링크": "channel_link",
        "채널명": "channel_name",
        "분야1": "category1",
        "분야2": "category2",
        "채널특징": "channel_feature",
        "벤치마킹채널여부": "is_benchmark_channel",
        "구독자수": "subscribers",
        "최근30개영상중위조회수": "median_views_30",
        "채널삭제여부": "is_deleted_channel",
        "가져올채널": "is_target_channel",
        "채널전체영상갯수": "total_video_count",
        "채널전체조회수변환": "total_channel_views_conv",
        "채널전체조회수": ["total_channel_views", "channel_total_views"],
        "영상당평균조회수전투력": "avg_views_per_video",
        "수집한영상평균조회수": "collected_video_avg_views",
        "최근30개영상평균조회수": "avg_views_30",
        "수집영상갯수": "collected_video_count",
        "평균영상길이": "avg_video_length",
        "조회수100만이상비율": "views_over_1m_ratio",
        "조회수500만이상비율": "views_over_5m_ratio",
        "조회수1000만이상비율": "views_over_10m_ratio",
        "구독자대비조회수배율최근30개": "sub_to_view_multiplier_30",
        "공정성과지수최근30개": "fairness_index_30",
        "영상당구독자수": "subscribers_per_video",
        "구독자1명당조회수": "views_per_subscriber",
        "조회수100만이상갯수": "views_over_1m_count",
        "조회수500만이상갯수": "views_over_5m_count",
        "조회수1000만이상갯수": "views_over_10m_count",
        "조회수상위3개제외평균조회수": "avg_views_exclude_top3",
        "중위평균조회수": "median_avg_views",
        "개설일": "created_at",
        "개설이후수집날짜까지기간": "days_since_creation",
        "영상1개당평균업로드주기": "avg_upload_period",
        "퍼온영상인가": "is_scraped",
        "ai생성영상인가": "is_ai_generated",
        "채널디스크립션": "channel_description",
        "채널핸들": "channel_handle",
        "원본행순서": "original_row_order",
        "순번": "original_row_order",

        # 영상 관련 통합 탭들
        "영상id": "video_id",
        "영상업로드날짜": "upload_date",
        "업로드날짜": "upload_date",
        "검색키워드": "keyword",
        "영상링크": "video_link",
        "제목": "title",
        "조회수": "views",
        "쇼츠여부": "is_shorts",
        "영상길이": "duration",
        "썸네일링크": "thumbnail_link",
        "후킹자막": "hooking_subtitle",
        "후킹자막유무": "has_hooking_subtitle",
        "대본내용": "transcript_content",
        "대본유무": "has_transcript",
        "대본텍스트수": "transcript_char_count",
        "대본글자수": "transcript_char_count",
        "분석": "analysis",
        "영상분석내용": "analysis",
        "좋아요수": "likes",
        "좋아요": "likes",
        "댓글수": "comments",
        "댓글": "comments",
        "구독자대비조회수배율": "sub_to_view_ratio",
        "구독자대비조회수비율": "sub_to_view_ratio",
        "조회수대비좋아요": "view_to_like_ratio",
        "조회수대비좋아요비율": "view_to_like_ratio",
        "조회수대비댓글": "view_to_comment_ratio",
        "조회수대비댓글비율": "view_to_comment_ratio",
        "영상업로드이후수집날짜까지기간": "days_since_upload",
        "업로드경과일": "days_since_upload",
        "일평균조회수": "daily_avg_views",
        "조회수100만이상": "views_over_1m",
        "100만이상": "views_over_1m",
        "조회수500만이상": "views_over_5m",
        "500만이상": "views_over_5m",
        "조회수1000만이상": "views_over_10m",
        "1000만이상": "views_over_10m",
        "구독자대비조회수몇배이상": "views_multiplier",
        "조회수배수": "views_multiplier",
        "좋아요3%이상": "likes_over_3pct",
        "카테고리id": "category_id",
        "카테고리분류": "category_name",
        "카테고리명": "category_name",
        "디스크립션": "description",
        "영상설명": "description",
        "디스크립션텍스트수": "description_char_count",
        "설명글자수": "description_char_count",
        "해시태그유무": "has_hashtag",
        "사용해시태그": "used_hashtags",
        "그래프": "graph",
        "영상당평균조회수": "avg_views_per_video",
        "채널개설일": "channel_created_at",
        "채널개설이후수집일까지경과일": "days_since_channel_creation",
        "채널개설경과일": "days_since_channel_creation",
        "음성나레이션여부": "is_narration",
        "성우여부": "is_narration",
        "레퍼런스사용할영상인가": "is_reference",
        "레퍼런스여부": "is_reference",
        "자막다운여부": "has_subtitle_downloaded",
        "자막다운로드완료여부": "has_subtitle_downloaded",
        "채널수익화여부": "is_channel_monetized",
        "수익창출여부": "is_channel_monetized",
        "쇼핑수익화여부": "is_shopping_monetized",
        "쇼핑수익창출여부": "is_shopping_monetized",
        "대본파일": "transcript_file",
        "대본파일명": "transcript_file",
        "썸네일여부": "has_thumbnail",
        "썸네일유무": "has_thumbnail",
        "썸네일이미지주소": "thumbnail_image_url",
        "썸네일이미지url": "thumbnail_image_url",
        "썸네일경로": "thumbnail_path",
        "썸네일저장경로": "thumbnail_path",
    }
    
    # 매핑 사전에서 찾기
    col_name = HEADER_TO_DB_COLUMN.get(normalized)
    
    # 만약 매핑값이 리스트나 튜플이면 db_columns 중 실재하는 컬럼을 동적으로 매치
    if isinstance(col_name, (list, tuple)):
        if db_columns:
            for cand in col_name:
                if cand in db_columns:
                    return cand
            return col_name[0]
        else:
            return col_name[0]
            
    # 만약 사전에 없는데 db_columns가 주어졌다면 db_columns 내의 이름과 직접 공백 제거 비교도 해봄 (폴백)
    if not col_name and db_columns:
        for db_col in db_columns:
            db_col_normalized = db_col.lower().replace('_', '')
            if normalized == db_col_normalized:
                return db_col
                
    return col_name


def parse_date_string(date_str):
    """
    다양한 포맷의 날짜 문자열을 datetime.date 객체로 파싱
    """
    if not date_str or str(date_str).strip() in ['N/A', '', 'None']:
        return None
    date_str = str(date_str).strip()
    
    # 정규식으로 YYYY-MM-DD 또는 YYYY.MM.DD 또는 YYYY년 MM월 DD일 등에서 숫자 3개 추출
    match = re.search(r'(\d{4})[\-\.\/\s년]+(\d{1,2})[\-\.\/\s월]+(\d{1,2})', date_str)
    if match:
        try:
            year = int(match.group(1))
            month = int(match.group(2))
            day = int(match.group(3))
            return datetime(year, month, day).date()
        except ValueError:
            pass
            
    # ISO 포맷 등 fallback
    for fmt in ('%Y-%m-%d', '%Y.%m.%d', '%Y/%m/%d', '%Y-%m-%d %H:%M:%S', '%Y.%m.%d %H:%M:%S'):
        try:
            return datetime.strptime(date_str, fmt).date()
        except ValueError:
            continue
            
    return None


def calculate_sheet_video_metrics(data):
    """
    유튜브 API 등으로 수집된 영상 데이터 딕셔너리를 받아
    9행 수식 조건에 맞는 파생 데이터 필드들을 자동으로 계산하여 주입
    """
    # 1. 수집 날짜 (없으면 오늘)
    crawl_date_str = data.get('crawl_date')
    crawl_date = parse_date_string(crawl_date_str) if crawl_date_str else datetime.now().date()
    if not crawl_date_str:
        data['crawl_date'] = crawl_date.strftime('%Y-%m-%d')
        
    # 2. 업로드 날짜 파싱
    upload_date_str = data.get('upload_date')
    upload_date = parse_date_string(upload_date_str) if upload_date_str else None
    
    # 3. 경과일 계산 (days_since_upload)
    days_since = None
    if upload_date:
        days_since = (crawl_date - upload_date).days
        data['days_since_upload'] = max(0, days_since)
    else:
        data['days_since_upload'] = 0
        
    # 4. 일평균 조회수 (daily_avg_views)
    views = parse_count_string(data.get('views', 0))
    data['views'] = views
    if days_since is not None:
        data['daily_avg_views'] = round(views / max(1, days_since), 2)
    else:
        data['daily_avg_views'] = 0.0
        
    # 5. 조회수 구간 (100만, 500만, 1000만 이상)
    data['views_over_1m'] = "ㅇ" if views >= 1_000_000 else "x"
    data['views_over_5m'] = "ㅇ" if views >= 5_000_000 else "x"
    data['views_over_10m'] = "ㅇ" if views >= 10_000_000 else "x"
    
    # 6. 구독자 수 파싱
    subs = parse_count_string(data.get('subscribers', 0))
    data['subscribers'] = subs
    
    # 7. 구독자 대비 조회수 비율/배율
    if subs > 0:
        data['sub_to_view_ratio'] = round(views / subs, 4)
        data['views_multiplier'] = round(views / subs, 2)
    else:
        data['sub_to_view_ratio'] = 0.0
        data['views_multiplier'] = 0.0
        
    # 8. 좋아요 수 파싱
    likes = parse_count_string(data.get('likes', 0))
    data['likes'] = likes
    
    # 9. 댓글 수 파싱
    comments = parse_count_string(data.get('comments', 0))
    data['comments'] = comments
    
    # 10. 조회수 대비 좋아요 비율
    if views > 0:
        data['view_to_like_ratio'] = round(likes / views, 4)
        data['view_to_comment_ratio'] = round(comments / views, 4)
        data['likes_over_3pct'] = "ㅇ" if (likes / views) >= 0.03 else "x"
    else:
        data['view_to_like_ratio'] = 0.0
        data['view_to_comment_ratio'] = 0.0
        data['likes_over_3pct'] = "x"
        
    # 11. 대본 관련
    transcript = data.get('transcript_content', '')
    if transcript:
        data['has_transcript'] = "ㅇ"
        data['transcript_char_count'] = len(str(transcript))
    else:
        data['has_transcript'] = "x"
        data['transcript_char_count'] = 0
        
    # 12. 후킹자막 관련
    hooking = data.get('hooking_subtitle', '')
    data['has_hooking_subtitle'] = "ㅇ" if hooking else "x"
    
    # 13. 디스크립션 관련
    desc = data.get('description', '')
    if desc:
        data['description_char_count'] = len(str(desc))
        data['has_hashtag'] = "ㅇ" if '#' in str(desc) else "x"
    else:
        data['description_char_count'] = 0
        data['has_hashtag'] = "x"
        
    # 14. 썸네일 관련
    thumb_url = data.get('thumbnail_image_url') or data.get('thumbnail_link')
    data['has_thumbnail'] = "ㅇ" if thumb_url else "x"
    
    # 15. 채널 전체 영상 정보 계산
    v_count = parse_count_string(data.get('video_count', 0))
    data['video_count'] = v_count
    ch_views = parse_count_string(data.get('channel_total_views', 0))
    data['channel_total_views'] = ch_views
    if v_count > 0:
        data['avg_views_per_video'] = int(ch_views / v_count)
    else:
        data['avg_views_per_video'] = 0
        
    # 16. 채널 개설 경과일
    ch_created_str = data.get('channel_created_at')
    ch_created = parse_date_string(ch_created_str) if ch_created_str else None
    if ch_created:
        data['days_since_channel_creation'] = max(0, (crawl_date - ch_created).days)
    else:
        data['days_since_channel_creation'] = 0
        
    # 17. 채널삭제 여부
    data['is_deleted_channel'] = data.get('is_deleted_channel', 'x')
        
    return data


def calculate_sheet_channel_metrics(data):
    """
    채널 리스트 탭의 파생 데이터 필드들을 자동으로 계산하여 주입
    """
    # 1. 수집 날짜
    crawl_date_str = data.get('crawl_date')
    crawl_date = parse_date_string(crawl_date_str) if crawl_date_str else datetime.now().date()
    if not crawl_date_str:
        data['crawl_date'] = crawl_date.strftime('%Y-%m-%d')
        
    # 2. 개설일 파싱 및 개설 경과일 계산 (days_since_creation)
    created_at_str = data.get('created_at')
    created_at = parse_date_string(created_at_str) if created_at_str else None
    if created_at:
        data['days_since_creation'] = max(0, (crawl_date - created_at).days)
    else:
        data['days_since_creation'] = 0
        
    # 3. 수집일 경과 계산 (days_since_crawl)
    data['days_since_crawl'] = 0
    
    # 4. 구독자수, 영상갯수, 채널조회수 정수화
    subs = parse_count_string(data.get('subscribers', 0))
    data['subscribers'] = subs
    
    total_videos = parse_count_string(data.get('total_video_count', 0))
    data['total_video_count'] = total_videos
    
    total_views = parse_count_string(data.get('total_channel_views', 0))
    data['total_channel_views'] = total_views
    
    # 5. 영상당 평균 조회수 (avg_views_per_video)
    if total_videos > 0:
        data['avg_views_per_video'] = int(total_views / total_videos)
        data['subscribers_per_video'] = int(subs / total_videos)
    else:
        data['avg_views_per_video'] = 0
        data['subscribers_per_video'] = 0
        
    # 6. 구독자 1명당 조회수 (views_per_subscriber)
    if subs > 0:
        data['views_per_subscriber'] = round(total_views / subs, 2)
    else:
        data['views_per_subscriber'] = 0.0
        
    # 7. 채널삭제 여부
    data['is_deleted_channel'] = data.get('is_deleted_channel', 'x')
        
    return data


# DB 컬럼명 -> 구글 시트 상의 한글 컬럼명 매핑 사전
DB_COLUMN_TO_DISPLAY_NAME = {
    "video_id": "영상 ID",
    "tab_name": "탭 이름",
    "upload_date": "업로드 날짜",
    "crawl_date": "수집 날짜",
    "keyword": "검색 키워드",
    "video_link": "영상 링크",
    "title": "제목",
    "views": "조회수",
    "is_benchmark_channel": "벤치마킹 채널여부",
    "is_shorts": "쇼츠 여부",
    "duration": "영상 길이",
    "channel_name": "채널명",
    "category1": "분야1",
    "category2": "분야2",
    "subscribers": "구독자수",
    "thumbnail_link": "썸네일 링크",
    "hooking_subtitle": "후킹 자막",
    "has_hooking_subtitle": "후킹 자막 유무",
    "transcript_content": "대본 내용",
    "has_transcript": "대본 유무",
    "transcript_char_count": "대본 글자수",
    "analysis": "영상 분석 내용",
    "likes": "좋아요수",
    "comments": "댓글수",
    "sub_to_view_ratio": "구독자 대비 조회수 비율",
    "view_to_like_ratio": "조회수 대비 좋아요 비율",
    "view_to_comment_ratio": "조회수 대비 댓글 비율",
    "days_since_upload": "업로드 경과일",
    "daily_avg_views": "일평균 조회수",
    "views_over_1m": "조회수 100만 이상",
    "views_over_5m": "조회수 500만 이상",
    "views_over_10m": "조회수 1000만 이상",
    "views_multiplier": "조회수 배수",
    "likes_over_3pct": "좋아요 3% 이상",
    "category_id": "카테고리 ID",
    "category_name": "카테고리명",
    "description": "설명",
    "description_char_count": "설명 글자수",
    "has_hashtag": "해시태그 유무",
    "used_hashtags": "사용 해시태그",
    "graph": "그래프",
    "video_count": "영상개수",
    "channel_total_views": "채널전체조회수",
    "avg_views_per_video": "영상당 평균 조회수",
    "channel_created_at": "채널 개설일",
    "days_since_channel_creation": "채널 개설 경과일",
    "is_narration": "음성 나레이션 여부",
    "is_scraped": "퍼온 영상인가",
    "is_ai_generated": "AI 생성 영상인가",
    "is_reference": "레퍼런스 여부",
    "has_subtitle_downloaded": "자막 다운로드 여부",
    "is_channel_monetized": "채널 수익화 여부",
    "is_shopping_monetized": "쇼핑 수익화 여부",
    "channel_country": "채널 국가",
    "used_language": "사용 언어",
    "channel_id": "채널 ID",
    "channel_link": "채널 링크",
    "playlist_name": "재생목록 이름",
    "transcript_file": "대본 파일명",
    "has_thumbnail": "썸네일 유무",
    "thumbnail_image_url": "썸네일 이미지 URL",
    "thumbnail_path": "썸네일 저장 경로",
    "original_row_order": "순번",
    "channel_description": "채널 설명",
    "channel_handle": "채널 핸들",
    "is_deleted_channel": "채널삭제 여부",
    
    # sheet_channels 전용
    "is_fetched": "가져왔는지 여부",
    "days_since_crawl": "수집날짜 경과일",
    "channel_feature": "채널 특징",
    "median_views_30": "최근 30개 영상 중위 조회수",
    "is_target_channel": "가져올 채널",
    "total_video_count": "채널전체 영상갯수",
    "total_channel_views_conv": "채널전체 조회수 변환",
    "total_channel_views": "채널전체조회수",
    "collected_video_avg_views": "수집한 영상 평균 조회수",
    "avg_views_30": "최근 30개 영상 평균 조회수",
    "collected_video_count": "수집 영상갯수",
    "avg_video_length": "평균 영상 길이",
    "views_over_1m_ratio": "조회수 100만 이상 비율",
    "views_over_5m_ratio": "조회수 500만 이상 비율",
    "views_over_10m_ratio": "조회수 1000만 이상 비율",
    "sub_to_view_multiplier_30": "구독자 대비 조회수 배율 최근 30개",
    "fairness_index_30": "공정성 지수 최근 30개",
    "subscribers_per_video": "영상당 구독자수",
    "views_per_subscriber": "구독자 1명당 조회수",
    "views_over_1m_count": "조회수 100만 이상 갯수",
    "views_over_5m_count": "조회수 500만 이상 갯수",
    "views_over_10m_count": "조회수 1000만 이상 갯수",
    "avg_views_exclude_top3": "조회수 상위 3개 제외 평균 조회수",
    "median_avg_views": "중위 평균 조회수",
    "created_at": "개설일",
    "days_since_creation": "개설이후 수집날짜까지 기간",
    "avg_upload_period": "영상 1개당 평균 업로드 주기",
    
    # sheet_playlist_ids 전용
    "playlist_id": "재생목록 ID",
    "last_checked_at": "마지막 체크일"
}



