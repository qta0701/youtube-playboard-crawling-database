import os

class Config:
    # Google Cloud Platform Credentials
    GOOGLE_KEY_DIR = 'google_service_key'
    SERVICE_ACCOUNT_FILE = os.path.join(GOOGLE_KEY_DIR, 'service-account-key.json')
    CLIENT_SECRET_FILE = os.path.join(GOOGLE_KEY_DIR, 'client_secret_1024022923684-mbtc0m911bb01l295rd3b46act2urm88.apps.googleusercontent.com.json')

    # YouTube Data API
    YOUTUBE_API_KEY = "AIzaSyDnsynsw8S6_rWBBhmCem6lG-dpiqFzAtg"
    CLIENT_ID = "1024022923684-mbtc0m911bb01l295rd3b46act2urm88.apps.googleusercontent.com"
    CLIENT_SECRET = "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"

    # Output paths
    OUTPUT_DIR = 'output'
    TRANSCRIPTS_DIR = os.path.join(OUTPUT_DIR, 'transcripts')

    # Selenium options
    CHROME_HEADLESS = False  # Set to True for headless mode
    LOGIN_WAIT_TIME = 120  # 로그인 최대 대기 시간 (120초 = 2분)
    MAX_ITEMS_NO_LOGIN = 100  # Maximum items without login (실제로는 ~22개만 로드됨)
    MAX_ITEMS_WITH_LOGIN = 200  # Maximum items with login
    SCROLL_PAUSE_TIME = 2  # Seconds to pause between scrolls
    MAX_SCROLL_ATTEMPTS = 50  # Maximum scroll attempts

    # ========== 채널 랭킹 설정 ==========

    # 채널 랭킹 기준 (ranking_type)
    CHANNEL_RANKING_TYPES = {
        'popular': 'most-popular',       # 인기순위 (조회수, 좋아요)
        'growth': 'most-growth',         # 구독자 급상승 순위 (신규 구독자, 증가폭)
        'viewed': 'most-viewed'          # 조회수 순위 (전체)
    }

    # 채널 랭킹 카테고리 (URL slug 매핑)
    CHANNEL_CATEGORIES = {
        'all': 'all',                    # 전체
        'animals': 'animals',            # 동물
        'music': 'music',                # 음악
        'gaming': 'gaming',              # 게임
        'news': 'news',                  # 뉴스/정치
        'vlog': 'vlog',                  # 인물/블로그
        'travel': 'travel',              # 여행/이벤트
        'sports': 'sports',              # 스포츠
        'comedy': 'comedy',              # 코메디
        'entertainment': 'entertainment', # 엔터테인먼트
        'film': 'film',                  # 영화/애니메이션
        'howto': 'howto',                # 노하우/스타일
        'education': 'education',        # 교육
        'science': 'science'             # 과학기술
    }

    # 채널 카테고리 한글명
    CHANNEL_CATEGORIES_KO = {
        'all': '전체',
        'animals': '동물',
        'music': '음악',
        'gaming': '게임',
        'news': '뉴스/정치',
        'vlog': '인물/블로그',
        'travel': '여행/이벤트',
        'sports': '스포츠',
        'comedy': '코메디',
        'entertainment': '엔터테인먼트',
        'film': '영화/애니메이션',
        'howto': '노하우/스타일',
        'education': '교육',
        'science': '과학기술'
    }

    # 국가 코드 매핑 (채널 랭킹용)
    CHANNEL_COUNTRIES = {
        'kr': 'south-korea',
        'us': 'united-states',
        'jp': 'japan',
        'global': 'worldwide'
    }

    # 채널 랭킹 기간 (구독자 급상승은 일간 제외)
    CHANNEL_PERIODS = {
        'daily': 'daily',
        'weekly': 'weekly',
        'monthly': 'monthly'
    }

    # 구독자 급상승 순위에서 허용되는 기간 (일간 제외)
    GROWTH_ALLOWED_PERIODS = ['weekly', 'monthly']
