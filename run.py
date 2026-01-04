"""
YouTube Crawler Pro - 메인 실행 파일
콘솔에서 실행하면 자동으로 Flask 서버가 시작됩니다.
"""

import os
import sys
import webbrowser
from threading import Timer
from logger_config import setup_logger

# 로거 설정
logger = setup_logger('main')

def open_browser():
    """서버 시작 후 브라우저 자동 열기"""
    try:
        webbrowser.open('http://localhost:5000')
        logger.info("Browser opened: http://localhost:5000")
    except Exception as e:
        logger.error(f"Failed to open browser: {e}")


def check_requirements():
    """필수 라이브러리 확인"""
    logger.info("Checking requirements...")

    # 패키지명과 실제 import 이름 매핑
    required_modules = {
        'flask': 'flask',
        'selenium': 'selenium',
        'pandas': 'pandas',
        'beautifulsoup4': 'bs4',
        'google-api-python-client': 'googleapiclient',
        'youtube-transcript-api': 'youtube_transcript_api',
        'webdriver-manager': 'webdriver_manager'
    }

    missing_modules = []

    for package_name, import_name in required_modules.items():
        try:
            __import__(import_name)
            logger.debug(f"✓ {package_name} is installed")
        except ImportError:
            missing_modules.append(package_name)
            logger.warning(f"✗ {package_name} is NOT installed")

    if missing_modules:
        logger.error(f"Missing modules: {', '.join(missing_modules)}")
        logger.error("Please run: pip install -r requirements.txt")
        return False

    logger.info("All required modules are installed")
    return True


def check_directories():
    """필수 디렉토리 확인 및 생성"""
    logger.info("Checking directories...")

    directories = [
        'output',
        'output/transcripts',
        'google_service_key',
        'logs',
        'static',
        'static/js',
        'static/css',
        'templates',
        'modules'
    ]

    for directory in directories:
        if not os.path.exists(directory):
            os.makedirs(directory)
            logger.info(f"Created directory: {directory}")
        else:
            logger.debug(f"Directory exists: {directory}")

    logger.info("All directories are ready")


def main():
    """메인 실행 함수"""
    print("=" * 60)
    print("  YouTube Crawler Pro - Starting...")
    print("=" * 60)

    logger.info("=" * 60)
    logger.info("YouTube Crawler Pro - Application Starting")
    logger.info("=" * 60)
    logger.info(f"Python version: {sys.version}")
    logger.info(f"Working directory: {os.getcwd()}")

    # 필수 항목 확인
    if not check_requirements():
        logger.error("Requirements check failed. Please install missing modules.")
        input("\nPress Enter to exit...")
        sys.exit(1)

    check_directories()

    # Flask 앱 import 및 실행
    try:
        logger.info("Importing Flask application...")
        from app import app

        logger.info("Flask application imported successfully")

        # 브라우저 자동 열기 (2초 후)
        Timer(2, open_browser).start()

        logger.info("Starting Flask server on http://localhost:5000")
        print("\n" + "=" * 60)
        print("  Server is running on http://localhost:5000")
        print("  Press Ctrl+C to stop the server")
        print("=" * 60 + "\n")

        # Flask 서버 시작
        app.run(
            host='0.0.0.0',
            port=5000,
            debug=True,
            use_reloader=False  # 콘솔 로그 중복 방지
        )

    except ImportError as e:
        logger.error(f"Failed to import Flask app: {e}", exc_info=True)
        print(f"\n[ERROR] Failed to import Flask app: {e}")
        print("Please check that all files are in place.")
        input("\nPress Enter to exit...")
        sys.exit(1)

    except KeyboardInterrupt:
        logger.info("Server stopped by user (Ctrl+C)")
        print("\n\nServer stopped.")

    except Exception as e:
        logger.error(f"Unexpected error: {e}", exc_info=True)
        print(f"\n[ERROR] Unexpected error: {e}")
        print("Check logs/error_*.log for details")
        input("\nPress Enter to exit...")
        sys.exit(1)


if __name__ == '__main__':
    try:
        main()
    except Exception as e:
        logger.error(f"Fatal error in main: {e}", exc_info=True)
        print(f"\n[FATAL ERROR] {e}")
        input("\nPress Enter to exit...")
        sys.exit(1)
