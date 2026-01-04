"""
OAuth 2.0 인증 관리자
YouTube API 쓰기 권한(재생목록 관리)을 위한 사용자 인증 처리

주요 기능:
- OAuth 2.0 인증 흐름 처리
- 액세스 토큰 관리 (저장/갱신)
- 쓰기 권한이 있는 YouTube Service 객체 생성
"""
import os
import pickle
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from config import Config
from logger_config import setup_logger

logger = setup_logger('auth_manager')

# 재생목록 관리 권한 (쓰기 권한 포함)
SCOPES = ['https://www.googleapis.com/auth/youtube']

# 토큰 파일 경로 (프로젝트 루트)
TOKEN_FILE = 'token_playlist.pickle'


class AuthManager:
    """OAuth 2.0 인증 관리자"""

    def __init__(self):
        self.credentials = None
        self.youtube_service = None

    def get_authenticated_service(self, force_refresh: bool = False):
        """
        쓰기 권한이 있는 YouTube Service 객체 반환

        Args:
            force_refresh: True일 경우 기존 토큰 무시하고 재인증

        Returns:
            googleapiclient.discovery.Resource: YouTube API 서비스 객체

        Raises:
            Exception: 인증 실패 시
        """
        # 이미 인증된 서비스가 있고, 강제 갱신이 아니면 재사용
        if self.youtube_service and not force_refresh and self._is_token_valid():
            return self.youtube_service

        self.credentials = self._get_credentials(force_refresh)

        if not self.credentials:
            raise Exception("인증에 실패했습니다. OAuth 로그인이 필요합니다.")

        self.youtube_service = build('youtube', 'v3', credentials=self.credentials)
        logger.info("YouTube Service 생성 완료 (쓰기 권한)")

        return self.youtube_service

    def _get_credentials(self, force_refresh: bool = False):
        """
        OAuth 2.0 인증 정보 획득

        Args:
            force_refresh: True일 경우 기존 토큰 무시

        Returns:
            google.oauth2.credentials.Credentials: 인증 정보
        """
        creds = None

        # 1. 기존 토큰 파일 확인 (강제 갱신이 아닌 경우)
        if not force_refresh and os.path.exists(TOKEN_FILE):
            try:
                with open(TOKEN_FILE, 'rb') as token:
                    creds = pickle.load(token)
                logger.info(f"기존 토큰 로드: {TOKEN_FILE}")
            except Exception as e:
                logger.warning(f"토큰 파일 로드 실패: {e}")
                creds = None

        # 2. 토큰 유효성 검사 및 갱신
        if creds:
            if creds.valid:
                logger.info("토큰이 유효합니다.")
                return creds
            elif creds.expired and creds.refresh_token:
                try:
                    creds.refresh(Request())
                    self._save_token(creds)
                    logger.info("토큰이 갱신되었습니다.")
                    return creds
                except Exception as e:
                    logger.warning(f"토큰 갱신 실패: {e}")
                    creds = None

        # 3. 새로운 OAuth 인증 필요
        logger.info("새로운 OAuth 인증이 필요합니다.")
        return None

    def run_oauth_flow(self):
        """
        OAuth 2.0 인증 흐름 실행 (브라우저 로그인)

        Returns:
            dict: {
                'success': bool,
                'message': str,
                'error': str (실패 시)
            }
        """
        result = {
            'success': False,
            'message': None,
            'error': None
        }

        client_secret_file = Config.CLIENT_SECRET_FILE

        if not os.path.exists(client_secret_file):
            result['error'] = f"클라이언트 시크릿 파일을 찾을 수 없습니다: {client_secret_file}"
            logger.error(result['error'])
            return result

        try:
            flow = InstalledAppFlow.from_client_secrets_file(
                client_secret_file,
                SCOPES
            )

            # 로컬 서버로 OAuth 콜백 처리 (브라우저 자동 열림)
            creds = flow.run_local_server(
                port=8090,  # 고정 포트 사용
                prompt='consent',  # 항상 동의 화면 표시
                access_type='offline'  # 오프라인 액세스 (refresh token 획득)
            )

            # 토큰 저장
            self._save_token(creds)
            self.credentials = creds

            # 서비스 객체 생성
            self.youtube_service = build('youtube', 'v3', credentials=creds)

            result['success'] = True
            result['message'] = "OAuth 인증이 완료되었습니다."
            logger.info("OAuth 인증 성공")

        except Exception as e:
            result['error'] = f"OAuth 인증 실패: {str(e)}"
            logger.error(result['error'])

        return result

    def _save_token(self, creds):
        """토큰을 파일로 저장"""
        try:
            with open(TOKEN_FILE, 'wb') as token:
                pickle.dump(creds, token)
            logger.info(f"토큰 저장 완료: {TOKEN_FILE}")
        except Exception as e:
            logger.error(f"토큰 저장 실패: {e}")

    def _is_token_valid(self) -> bool:
        """현재 토큰 유효성 확인"""
        if not self.credentials:
            return False
        return self.credentials.valid or (
            self.credentials.expired and
            self.credentials.refresh_token
        )

    def get_auth_status(self) -> dict:
        """
        현재 인증 상태 반환

        Returns:
            dict: {
                'is_authenticated': bool,
                'token_exists': bool,
                'token_valid': bool,
                'token_expired': bool,
                'has_refresh_token': bool
            }
        """
        status = {
            'is_authenticated': False,
            'token_exists': os.path.exists(TOKEN_FILE),
            'token_valid': False,
            'token_expired': False,
            'has_refresh_token': False
        }

        if status['token_exists']:
            try:
                with open(TOKEN_FILE, 'rb') as token:
                    creds = pickle.load(token)
                    status['token_valid'] = creds.valid
                    status['token_expired'] = creds.expired
                    status['has_refresh_token'] = bool(creds.refresh_token)
                    status['is_authenticated'] = creds.valid or (
                        creds.expired and creds.refresh_token
                    )
            except:
                pass

        return status

    def revoke_token(self):
        """토큰 삭제 (로그아웃)"""
        if os.path.exists(TOKEN_FILE):
            os.remove(TOKEN_FILE)
            logger.info("토큰 파일 삭제됨")

        self.credentials = None
        self.youtube_service = None


# 싱글톤 인스턴스
_auth_manager = None

def get_auth_manager() -> AuthManager:
    """AuthManager 싱글톤 인스턴스 반환"""
    global _auth_manager
    if _auth_manager is None:
        _auth_manager = AuthManager()
    return _auth_manager
