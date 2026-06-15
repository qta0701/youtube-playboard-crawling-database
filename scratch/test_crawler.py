import sys
import os
# Add parent directory to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from modules.crawler_selenium import PlayboardCrawler
from logger_config import setup_logger

logger = setup_logger('crawler_test')

def run_test():
    # Initialize crawler (headless=True for testing)
    crawler = PlayboardCrawler(headless=True)
    try:
        url = "https://playboard.co/chart/short/most-viewed-all-videos-in-south-korea-daily"
        logger.info("Starting crawler test for Comment rankings...")
        data = crawler.crawl(
            url=url,
            target_count=5,
            target_type='shorts',
            login_mode=False,  # Run without login
            country='한국',
            period='일간',
            category='게임',
            ranking_criteria='댓글 순위'
        )
        logger.info(f"Test finished! Successfully parsed {len(data)} items.")
        if data is not None and not data.empty:
            logger.info(f"First item: {data.iloc[0].to_dict()}")
    except Exception as e:
        logger.exception(f"Test failed with exception: {e}")
    finally:
        if crawler.driver:
            try:
                crawler.driver.quit()
            except:
                pass

if __name__ == '__main__':
    run_test()
