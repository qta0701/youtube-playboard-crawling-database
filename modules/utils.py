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

