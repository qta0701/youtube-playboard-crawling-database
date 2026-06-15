import sys
import os

# 모듈 경로 추가
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from modules.crawler_selenium import PlayboardCrawler

def main():
    crawler = PlayboardCrawler(headless=False)
    # 미래의 날짜(404 유발)를 사용하는 URL
    url = "https://playboard.co/chart/short/most-viewed-all-videos-in-south-korea-daily?period=1781449200"
    try:
        print("Starting crawl with 404 URL fallback test...")
        # target_count=5 로 간단하게 수집
        df = crawler.crawl(
            url=url,
            target_type='shorts',
            login_mode=False,
            target_count=5,
            country='한국',
            period='일간',
            ranking_date='2026-06-15',
            ranking_criteria='조회수 순위',
            category='게임'
        )
        print("Crawl Finished! Resulting DataFrame size:", len(df))
        if not df.empty:
            print(df.head(2))
    except Exception as e:
        print("Crawl failed with error:", e)
    finally:
        crawler.close()

if __name__ == '__main__':
    main()
