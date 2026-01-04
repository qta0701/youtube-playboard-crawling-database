# Playboard URL 동적 생성을 위한 매핑 데이터

# 1. 국가 매핑
COUNTRIES = {
    "한국": "south-korea",
    "미국": "united-states",
    "일본": "japan",
    "영국": "united-kingdom",
    "독일": "germany",
    "프랑스": "france",
    "캐나다": "canada",
    "호주": "australia"
}

# 2. 기간 매핑
PERIODS = {
    "일간": "daily",
    "주간": "weekly",
    "월간": "monthly"
}

# 3. 카테고리 매핑
CATEGORIES = {
    "전체": "all",
    "동물": "animals",
    "음악": "music",
    "게임": "gaming",  # 'game' 오타 수정
    "뉴스/정치": "news",
    "인물/블로그": "vlog",
    "스포츠": "sports",
    "코메디": "comedy",
    "엔터테인먼트": "entertainment",
    "영화/애니메이션": "film",
    "노하우/스타일": "howto",
    "교육": "education",
    "과학기술": "science"
}

# 4. 타겟(유형) 별 URL 템플릿
URL_TEMPLATES = {
    # 쇼츠: https://playboard.co/chart/short/most-viewed-{category}-videos-in-{country}-{period}
    "shorts": "https://playboard.co/chart/short/most-viewed-{category}-videos-in-{country}-{period}",

    # 영상: https://playboard.co/chart/video/most-viewed-{category}-videos-in-{country}-{period}
    "video": "https://playboard.co/chart/video/most-viewed-{category}-videos-in-{country}-{period}",

    # 채널: https://playboard.co/youtube-ranking/most-popular-{category}-channels-in-{country}-{period}
    "channel": "https://playboard.co/youtube-ranking/most-popular-{category}-channels-in-{country}-{period}"
}

def build_url(target_type, category, country, period, timestamp=None):
    """
    동적으로 Playboard URL 생성

    Args:
        target_type (str): 'shorts', 'video', 'channel'
        category (str): 카테고리 한글명
        country (str): 국가 한글명
        period (str): 기간 한글명
        timestamp (int, optional): Unix timestamp for specific date

    Returns:
        str: 생성된 URL
    """
    template = URL_TEMPLATES.get(target_type)
    if not template:
        raise ValueError(f"Invalid target_type: {target_type}")

    category_slug = CATEGORIES.get(category)
    country_slug = COUNTRIES.get(country)
    period_slug = PERIODS.get(period)

    if not all([category_slug, country_slug, period_slug]):
        raise ValueError("Invalid category, country, or period")

    url = template.format(
        category=category_slug,
        country=country_slug,
        period=period_slug
    )

    # 특정 날짜 선택 시 timestamp 추가
    if timestamp:
        url += f"?period={timestamp}"

    return url


def get_country_list():
    """GUI에 표시할 국가 목록 반환"""
    return list(COUNTRIES.keys())


def get_period_list():
    """GUI에 표시할 기간 목록 반환"""
    return list(PERIODS.keys())


def get_category_list():
    """GUI에 표시할 카테고리 목록 반환"""
    return list(CATEGORIES.keys())
