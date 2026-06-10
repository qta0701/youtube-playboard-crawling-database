import logging
import os
from datetime import datetime
from logging.handlers import RotatingFileHandler

# 전역 로그 파일 경로 (세션당 1개만 생성)
_LOG_FILEPATH = None
_LOG_INITIALIZED = False
_LOG_PREFIX = 'log_'  # 기본 접두사


def set_log_prefix(prefix):
    """로그 파일 접두사 설정 (로거 초기화 전에 호출해야 함)"""
    global _LOG_PREFIX
    _LOG_PREFIX = prefix


def _get_or_create_log_filepath(log_dir='logs'):
    """세션당 단일 로그 파일 경로 반환"""
    global _LOG_FILEPATH, _LOG_INITIALIZED, _LOG_PREFIX

    if not os.path.exists(log_dir):
        os.makedirs(log_dir)

    if _LOG_FILEPATH is None or not _LOG_INITIALIZED:
        log_timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        log_filename = f'{_LOG_PREFIX}{log_timestamp}.log'
        _LOG_FILEPATH = os.path.join(log_dir, log_filename)
        _LOG_INITIALIZED = True

    return _LOG_FILEPATH


def setup_logger(name='youtube_crawler', log_dir='logs'):
    """
    통합 로깅 시스템 설정
    - 세션당 단일 로그 파일 사용 (log_YYYYMMDD_HHMMSS.log)
    - 모든 모듈이 동일한 로그 파일 공유
    - 콘솔 출력
    - 로테이션 지원 (50MB, 최대 10개 백업)
    """
    # 로거 생성
    logger = logging.getLogger(name)

    # 이미 핸들러가 있으면 기존 로거 반환 (중복 방지)
    if logger.handlers:
        return logger

    logger.setLevel(logging.DEBUG)

    # 포맷터 설정
    detailed_formatter = logging.Formatter(
        fmt='[%(asctime)s] %(levelname)-8s [%(name)s:%(module)s:%(funcName)s:%(lineno)d] - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )

    console_formatter = logging.Formatter(
        fmt='[%(asctime)s] %(levelname)-8s - %(message)s',
        datefmt='%H:%M:%S'
    )

    # 1. 콘솔 핸들러 (DEBUG 레벨로 변경하여 상세 로그가 cmd에 출력되도록 함)
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.DEBUG)
    console_handler.setFormatter(console_formatter)
    logger.addHandler(console_handler)

    # 2. 통합 로그 파일 핸들러 (DEBUG 레벨) - 세션당 단일 파일
    log_filepath = _get_or_create_log_filepath(log_dir)

    file_handler = RotatingFileHandler(
        log_filepath,
        maxBytes=50*1024*1024,  # 50MB
        backupCount=10,
        encoding='utf-8'
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(detailed_formatter)
    logger.addHandler(file_handler)

    # 첫 로거만 초기화 메시지 출력
    global _LOG_INITIALIZED
    if name == 'dashboard' or name == 'youtube_crawler':
        logger.info("=" * 80)
        logger.info(f"Logger initialized - Name: {name}")
        logger.info(f"Log directory: {os.path.abspath(log_dir)}")
        logger.info(f"Log file: {os.path.basename(log_filepath)}")
        logger.info(f"Log level: DEBUG (all events)")
        logger.info(f"Max file size: 50MB, Backup count: 10")
        logger.info("=" * 80)

    return logger


def cleanup_old_logs(log_dir='logs', keep_count=5):
    """최근 n개의 로그 파일만 남기고 오래된 로그 파일 삭제"""
    if not os.path.exists(log_dir):
        return
    try:
        # log_*.log 패턴의 파일들만 수집
        files = [os.path.join(log_dir, f) for f in os.listdir(log_dir) if f.startswith('log_') and f.endswith('.log')]
        # 파일 수정 시간(mtime) 기준으로 내림차순 정렬 (최신 파일이 앞에 옴)
        files.sort(key=os.path.getmtime, reverse=True)
        
        # keep_count를 초과하는 파일들은 삭제
        if len(files) > keep_count:
            files_to_delete = files[keep_count:]
            for file_path in files_to_delete:
                try:
                    os.remove(file_path)
                except Exception:
                    pass
    except Exception:
        pass


def log_exception(logger, exception, context=""):
    """
    예외 상세 로깅

    Args:
        logger: 로거 객체
        exception: 예외 객체
        context: 추가 컨텍스트 정보
    """
    import traceback

    logger.error("=" * 80)
    logger.error(f"EXCEPTION OCCURRED: {context}")
    logger.error(f"Exception Type: {type(exception).__name__}")
    logger.error(f"Exception Message: {str(exception)}")
    logger.error(f"Traceback:\n{''.join(traceback.format_tb(exception.__traceback__))}")
    logger.error("=" * 80)


def log_function_call(logger, func_name, **kwargs):
    """
    함수 호출 로깅 (디버그용)

    Args:
        logger: 로거 객체
        func_name: 함수명
        **kwargs: 함수 파라미터
    """
    params_str = ', '.join([f"{k}={v}" for k, v in kwargs.items()])
    logger.debug(f"▶ Function Call: {func_name}({params_str})")


def log_crawler_stats(logger, stats):
    """
    크롤링 통계 로깅

    Args:
        logger: 로거 객체
        stats: 통계 딕셔너리
    """
    logger.info("=" * 80)
    logger.info("CRAWLING STATISTICS")
    logger.info("=" * 80)
    for key, value in stats.items():
        logger.info(f"  {key}: {value}")
    logger.info("=" * 80)


def log_api_request(logger, api_name, endpoint, params=None, response_status=None):
    """
    API 요청/응답 로깅

    Args:
        logger: 로거 객체
        api_name: API 이름
        endpoint: 엔드포인트
        params: 요청 파라미터
        response_status: 응답 상태 코드
    """
    logger.debug(f"API Request: {api_name} - {endpoint}")
    if params:
        logger.debug(f"  Parameters: {params}")
    if response_status:
        logger.debug(f"  Response Status: {response_status}")


def log_state_change(logger, component, old_state, new_state, reason=""):
    """
    상태 변화 로깅

    Args:
        logger: 로거 객체
        component: 컴포넌트 이름
        old_state: 이전 상태
        new_state: 새 상태
        reason: 변경 이유
    """
    logger.info(f"State Change: {component} [{old_state}] → [{new_state}]" +
                (f" (Reason: {reason})" if reason else ""))


def log_user_action(logger, action, details=None):
    """
    사용자 액션 로깅

    Args:
        logger: 로거 객체
        action: 액션 이름
        details: 상세 정보
    """
    logger.info(f"User Action: {action}" + (f" - {details}" if details else ""))
