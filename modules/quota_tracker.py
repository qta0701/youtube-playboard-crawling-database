"""
YouTube API Quota Tracker
API 사용량 추적 및 관리

YouTube Data API v3 할당량:
- 기본 할당량: 10,000 units/day
- channels.list: 1 unit
- videos.list: 1 unit
- playlistItems.list: 1 unit
- search.list: 100 units (사용 지양)
"""
import sqlite3
from datetime import datetime, date
from logger_config import setup_logger

logger = setup_logger('quota_tracker')


class QuotaTracker:
    """YouTube API Quota 사용량 추적기"""

    # API 엔드포인트별 비용 (units)
    ENDPOINT_COSTS = {
        'channels.list': 1,
        'videos.list': 1,
        'playlistItems.list': 1,
        'search.list': 100,
        'captions.list': 50,
        'captions.download': 200,
    }

    # 일일 기본 할당량
    DAILY_QUOTA_LIMIT = 10000

    def __init__(self, db_path='output/db/youtube_data.db'):
        """
        Quota 트래커 초기화

        Args:
            db_path: 데이터베이스 경로
        """
        self.db_path = db_path
        logger.info(f"QuotaTracker initialized with db: {db_path}")

    def _get_connection(self):
        """DB 연결 생성"""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def log_usage(self, endpoint: str, units: int = None):
        """
        API 사용량 기록

        Args:
            endpoint: API 엔드포인트 이름 (예: 'channels.list')
            units: 사용된 할당량 (None이면 자동 계산)
        """
        if units is None:
            units = self.ENDPOINT_COSTS.get(endpoint, 1)

        conn = self._get_connection()
        cursor = conn.cursor()

        try:
            cursor.execute('''
                INSERT INTO quota_logs (request_date, endpoint, units_used, timestamp)
                VALUES (?, ?, ?, ?)
            ''', (
                date.today().isoformat(),
                endpoint,
                units,
                datetime.now().isoformat()
            ))
            conn.commit()
            logger.debug(f"Quota logged: {endpoint} = {units} units")
        except Exception as e:
            logger.error(f"Failed to log quota: {e}")
        finally:
            conn.close()

    def get_today_usage(self) -> dict:
        """
        오늘의 API 사용량 조회

        Returns:
            dict: {
                'total_used': int,
                'remaining': int,
                'percentage': float,
                'by_endpoint': {endpoint: units, ...}
            }
        """
        conn = self._get_connection()
        cursor = conn.cursor()

        try:
            today = date.today().isoformat()

            # 엔드포인트별 사용량
            cursor.execute('''
                SELECT endpoint, SUM(units_used) as total
                FROM quota_logs
                WHERE request_date = ?
                GROUP BY endpoint
            ''', (today,))

            by_endpoint = {row['endpoint']: row['total'] for row in cursor.fetchall()}

            # 총 사용량
            total_used = sum(by_endpoint.values()) if by_endpoint else 0
            remaining = self.DAILY_QUOTA_LIMIT - total_used
            percentage = (total_used / self.DAILY_QUOTA_LIMIT) * 100

            return {
                'total_used': total_used,
                'remaining': remaining,
                'percentage': round(percentage, 2),
                'limit': self.DAILY_QUOTA_LIMIT,
                'by_endpoint': by_endpoint,
                'date': today
            }
        finally:
            conn.close()

    def get_usage_history(self, days: int = 7) -> list:
        """
        최근 n일간 사용량 이력

        Args:
            days: 조회할 일수

        Returns:
            list: [{'date': str, 'total': int, 'by_endpoint': dict}, ...]
        """
        conn = self._get_connection()
        cursor = conn.cursor()

        try:
            cursor.execute('''
                SELECT request_date, endpoint, SUM(units_used) as total
                FROM quota_logs
                WHERE request_date >= date('now', ?)
                GROUP BY request_date, endpoint
                ORDER BY request_date DESC
            ''', (f'-{days} days',))

            rows = cursor.fetchall()

            # 날짜별로 그룹화
            history = {}
            for row in rows:
                date_str = row['request_date']
                if date_str not in history:
                    history[date_str] = {'date': date_str, 'total': 0, 'by_endpoint': {}}
                history[date_str]['total'] += row['total']
                history[date_str]['by_endpoint'][row['endpoint']] = row['total']

            return list(history.values())
        finally:
            conn.close()

    def can_make_request(self, endpoint: str, count: int = 1) -> bool:
        """
        요청 가능 여부 확인

        Args:
            endpoint: API 엔드포인트
            count: 요청 횟수

        Returns:
            bool: 요청 가능하면 True
        """
        cost = self.ENDPOINT_COSTS.get(endpoint, 1) * count
        usage = self.get_today_usage()
        return usage['remaining'] >= cost

    def estimate_cost(self, operations: dict) -> dict:
        """
        작업 예상 비용 계산

        Args:
            operations: {endpoint: count, ...}

        Returns:
            dict: {'total_cost': int, 'details': {...}, 'affordable': bool}
        """
        total_cost = 0
        details = {}

        for endpoint, count in operations.items():
            unit_cost = self.ENDPOINT_COSTS.get(endpoint, 1)
            cost = unit_cost * count
            details[endpoint] = {'count': count, 'unit_cost': unit_cost, 'total': cost}
            total_cost += cost

        usage = self.get_today_usage()

        return {
            'total_cost': total_cost,
            'details': details,
            'affordable': usage['remaining'] >= total_cost,
            'remaining_after': usage['remaining'] - total_cost
        }

    def get_quota_status_color(self) -> str:
        """
        사용량에 따른 상태 색상 반환

        Returns:
            str: 'green', 'yellow', 'red'
        """
        usage = self.get_today_usage()
        percentage = usage['percentage']

        if percentage < 50:
            return 'green'
        elif percentage < 80:
            return 'yellow'
        else:
            return 'red'
