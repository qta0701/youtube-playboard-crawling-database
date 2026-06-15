import sys
import os

# 모듈 경로 추가
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config_mappings import build_url
from datetime import datetime

# use_specific_date = False 모사
specific_date = datetime.today().strftime('%Y-%m-%d')
use_specific_date = False

timestamp = None
if use_specific_date and specific_date:
    dt = datetime.strptime(specific_date, '%Y-%m-%d')
    timestamp = int(dt.timestamp())

url = build_url('shorts', '전체', '한국', '일간', timestamp)
print("use_specific_date=False 일 때 URL:", url)

# use_specific_date = True 모사
use_specific_date = True
timestamp = None
if use_specific_date and specific_date:
    dt = datetime.strptime(specific_date, '%Y-%m-%d')
    timestamp = int(dt.timestamp())

url_specific = build_url('shorts', '전체', '한국', '일간', timestamp)
print("use_specific_date=True 일 때 URL:", url_specific)

try:
    assert "?period=" not in url
    assert "?period=" in url_specific
    print("✓ URL 생성 조건부 타임스탬프 로직 테스트 성공!")
except AssertionError:
    print("❌ Assertion Failed!")
