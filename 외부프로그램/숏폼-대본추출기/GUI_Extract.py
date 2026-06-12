import asyncio
import aiohttp
import json
import re
import time
import threading
import requests
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass
from urllib.parse import urlparse, parse_qs
import logging
from pathlib import Path
import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext
from datetime import datetime, timedelta

# Google APIs
import gspread
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build

# Main_Extract 모듈 import
from Main_Extract import MainYouTubeShortsTranscriptExtractor
from googleapiclient.errors import HttpError

# Sheet 시스템 import
from sheet_config import (
    SheetType,
    get_column_index_by_name,
    is_formula_column,
    normalize_header
)
from sheet_utils import (
    bulk_update_with_formula_protection,
    clear_formula_column_data_rows
)

# OAuth 관련 추가 imports
import pickle
import os
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request

# 로깅 설정 (파일과 콘솔 동시 출력)
import os
from datetime import datetime

# 로그 디렉토리 생성
log_dir = Path(__file__).parent / "logs"
log_dir.mkdir(exist_ok=True)

# 로그 파일명 (날짜_시분초)
log_filename = log_dir / f"transcript_extractor_{datetime.now().strftime('%m%d_%H%M%S')}.log"

# 로거 설정
logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)

# 포맷터
formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(funcName)s:%(lineno)d - %(message)s')

# 파일 핸들러 (모든 로그)
file_handler = logging.FileHandler(log_filename, encoding='utf-8')
file_handler.setLevel(logging.DEBUG)
file_handler.setFormatter(formatter)

# 콘솔 핸들러 (INFO 이상)
console_handler = logging.StreamHandler()
console_handler.setLevel(logging.INFO)
console_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))

# 핸들러 추가
logger.addHandler(file_handler)
logger.addHandler(console_handler)

@dataclass
class TranscriptConfig:
    """자막 추출 설정"""
    target_language: Optional[str] = None
    max_concurrent: int = 5
    retry_attempts: int = 1
    delay_between_requests: float = 1.0

@dataclass
class VideoData:
    """비디오 데이터"""
    video_id: str
    title: str = ""
    transcript: List[Tuple[str, str]] = None
    language: str = ""
    error: str = ""


class GoogleSheetsManager:
    """구글 시트 관리"""
    
    def __init__(self, credentials_path: str):
        self.credentials_path = credentials_path
        self.client = None
        self.drive_service = None
        self.docs_service = None
        
        # OAuth 관련 설정
        self.oauth_client_path = Path(credentials_path).parent / "client_secret_1024022923684-mbtc0m911bb01l295rd3b46act2urm88.apps.googleusercontent.com.json"
        self.token_path = Path(credentials_path).parent / "token.pickle"
        self.oauth_scopes = [
            'https://www.googleapis.com/auth/drive.file',
            'https://www.googleapis.com/auth/spreadsheets',
            'https://www.googleapis.com/auth/documents'
        ]
        self.oauth_credentials = None
        
        self.available_spreadsheets = {
            '쇼츠 스프레드시트': 'https://docs.google.com/spreadsheets/d/1o-Vm5eMfH6Mm1gCnsv-SuRqtVMYTBDnWThBxjwCOptY/edit?gid=495259183#gid=495259183',
            '유튜브 재생목록 요약': 'https://docs.google.com/spreadsheets/d/18q9GwlXm65t9IQi3kVy6Br1Y8PBmPCFthqoZFfsmhMU/edit?gid=1434237297#gid=1434237297'
        }
        self.available_sheets = {
            '사용 레퍼런스 영상': '사용 레퍼런스 영상',
            '쇼핑 레퍼런스 영상': '쇼핑 레퍼런스 영상', 
            '유튜브 재생목록': '유튜브 재생목록',
            '채널 리스트': '채널 리스트',
            '영상 리스트': '영상 리스트'
        }
        
    def authenticate_oauth(self):
        """OAuth 인증 (자동 토큰 갱신 포함)"""
        try:
            logger.info("🔐 OAuth 인증 시작...")
            
            # 저장된 토큰 확인
            if os.path.exists(self.token_path):
                logger.debug("저장된 토큰 파일 발견, 로드 중...")
                with open(self.token_path, 'rb') as token:
                    self.oauth_credentials = pickle.load(token)
            
            # 토큰이 없거나 유효하지 않으면 새로 인증
            if not self.oauth_credentials or not self.oauth_credentials.valid:
                if self.oauth_credentials and self.oauth_credentials.expired and self.oauth_credentials.refresh_token:
                    logger.info("토큰 갱신 중...")
                    self.oauth_credentials.refresh(Request())
                else:
                    logger.info("새 OAuth 인증 필요 - 브라우저가 열립니다...")
                    if not os.path.exists(self.oauth_client_path):
                        raise FileNotFoundError(f"OAuth 클라이언트 파일을 찾을 수 없습니다: {self.oauth_client_path}")
                    
                    flow = InstalledAppFlow.from_client_secrets_file(
                        str(self.oauth_client_path), 
                        self.oauth_scopes
                    )
                    self.oauth_credentials = flow.run_local_server(port=8080)
                
                # 새 토큰 저장
                logger.debug("토큰 저장 중...")
                with open(self.token_path, 'wb') as token:
                    pickle.dump(self.oauth_credentials, token)
            
            # Google API 서비스 생성 (OAuth 사용)
            self.drive_service = build('drive', 'v3', credentials=self.oauth_credentials)
            self.docs_service = build('docs', 'v1', credentials=self.oauth_credentials)
            
            logger.info("✅ OAuth 인증 성공 - Drive 및 Docs API 준비 완료")
            
        except Exception as e:
            logger.error(f"❌ OAuth 인증 실패: {e}")
            raise

    def authenticate(self):
        """구글 시트 인증 (서비스 계정 + OAuth)"""
        try:
            logger.debug(f"서비스 계정 키 파일 경로: {self.credentials_path}")
            
            scope = [
                'https://spreadsheets.google.com/feeds',
                'https://www.googleapis.com/auth/drive',
                'https://www.googleapis.com/auth/documents'
            ]
            
            credentials = Credentials.from_service_account_file(
                self.credentials_path, scopes=scope
            )
            
            logger.debug(f"서비스 계정 이메일: {credentials.service_account_email}")
            
            self.client = gspread.authorize(credentials)
            
            logger.info("✅ 구글 시트 인증 성공")
            
        except FileNotFoundError as e:
            logger.error("❌ 서비스 계정 키 파일을 찾을 수 없습니다")
            logger.error("🔧 해결방법: JSON 키 파일 경로 및 파일명을 재확인하세요")
            logger.error(f"   현재 경로: {self.credentials_path}")
            raise Exception("서비스 계정 키 파일이 존재하지 않습니다")
        except ValueError as e:
            if "Invalid" in str(e) or "credentials" in str(e).lower():
                logger.error("❌ 잘못된 서비스 계정 키 파일입니다")
                logger.error("🔧 해결방법: 올바른 JSON 키 파일인지 확인하고 다시 다운로드하세요")
                raise Exception("서비스 계정 키 파일이 유효하지 않습니다")
            else:
                raise
        except Exception as e:
            error_msg = str(e).lower()
            if "api not enabled" in error_msg or "403" in error_msg:
                logger.error("❌ Google Sheets API가 활성화되지 않았습니다")
                logger.error("🔧 해결방법: Google Cloud Console에서 Google Sheets API를 활성화하세요")
                logger.error("   1. https://console.cloud.google.com/apis/library 접속")
                logger.error("   2. 'Google Sheets API' 검색 후 활성화")
                raise Exception("Google Sheets API가 활성화되지 않았습니다")
            else:
                logger.exception("❌ 구글 시트 인증 실패:")
                raise

    def get_all_values_unformatted(self, sheet):
        """
        시트의 모든 값을 UNFORMATTED_VALUE로 가져오기
        (표시 형식이 아닌 실제 값을 가져옴)
        예: "1,265개" -> 1265, "50%" -> 0.5, "2024-01-01" -> 날짜 시리얼 번호
        """
        try:
            # Google Sheets API를 직접 호출하여 UNFORMATTED_VALUE로 가져오기
            result = sheet.spreadsheet.values_get(
                sheet.title,
                params={'valueRenderOption': 'UNFORMATTED_VALUE'}
            )
            return result.get('values', [])
        except Exception as e:
            logger.warning(f"UNFORMATTED_VALUE로 데이터 가져오기 실패, 기본 방식 사용: {str(e)}")
            return sheet.get_all_values()

    def find_video_id_column(self, sheet, sheet_name: str) -> int:
        """'영상 ID'가 포함된 열 찾기"""
        try:
            logger.debug(f"'{sheet_name}' 시트에서 '영상 ID' 열 찾기 시작")
            all_values = sheet.get_all_values()
            logger.debug(f"시트 데이터 읽기 완료: {len(all_values)}행")
            
            for row_idx, row in enumerate(all_values[:20]):
                for col_idx, cell in enumerate(row):
                    if '영상 ID' in str(cell):
                        logger.debug(f"'영상 ID' 열 발견: {row_idx+1}행 {col_idx+1}열")
                        return col_idx + 1
            
            # 헤더 정보 로그
            if all_values:
                logger.debug(f"첫 번째 행 헤더: {all_values[0]}")
                
            raise ValueError(f"'{sheet_name}' 시트에서 '영상 ID' 열을 찾을 수 없습니다.")
        except Exception as e:
            logger.exception(f"'{sheet_name}' 시트 구조 분석 실패:")
            raise ValueError(f"시트 구조 분석 실패: {e}")
    
    def find_transcript_column(self, sheet, sheet_name: str) -> int:
        """'대본내용'이 포함된 열 찾기"""
        try:
            logger.debug(f"'{sheet_name}' 시트에서 '대본내용' 열 찾기 시작")
            all_values = sheet.get_all_values()
            
            for row_idx, row in enumerate(all_values[:20]):
                for col_idx, cell in enumerate(row):
                    if '대본내용' in str(cell):
                        logger.debug(f"'대본내용' 열 발견: {row_idx+1}행 {col_idx+1}열")
                        return col_idx + 1
            
            # 헤더 정보 로그 (대본내용을 찾지 못한 경우)
            if all_values:
                logger.debug(f"헤더 행들: {all_values[:5]}")
                
            raise ValueError(f"'{sheet_name}' 시트에서 '대본내용' 열을 찾을 수 없습니다.")
        except Exception as e:
            logger.exception(f"'{sheet_name}' 시트 '대본내용' 열 찾기 실패:")
            raise ValueError(f"시트 구조 분석 실패: {e}")
            
    def find_last_transcript_row(self, sheet, transcript_col: int) -> int:
        """대본내용 열에서 마지막 데이터가 있는 행 찾기"""
        transcript_values = sheet.col_values(transcript_col)
        last_row = 0
        for i in range(len(transcript_values) - 1, -1, -1):
            if transcript_values[i].strip():
                last_row = i + 1
                break
        return last_row
    
    def get_video_ids_from_sheet(self, sheet_url: str, sheet_name: str, mode: str = 'A', max_count: Optional[int] = None) -> Tuple[List[str], int, int, Optional[List[int]]]:
        """시트에서 비디오 ID 목록 가져오기"""
        try:
            logger.debug(f"시트 URL 접근: {sheet_url}")
            try:
                workbook = self.client.open_by_url(sheet_url)
                logger.debug(f"워크북 열기 성공")
            except Exception as e:
                error_msg = str(e).lower()
                if "permission" in error_msg or "denied" in error_msg:
                    logger.error("❌ 시트 접근 권한이 없습니다")
                    logger.error("🔧 해결방법: 구글 시트에서 서비스 계정을 편집자로 공유하세요")
                    logger.error(f"   서비스 계정 이메일: {self.client.auth.service_account_email}")
                    raise Exception("시트 접근 권한이 없습니다")
                elif "429" in str(e) or "quota" in error_msg:
                    logger.error("❌ API 사용량 한도를 초과했습니다")
                    logger.error("🔧 해결방법: 잠시 후 다시 시도하거나 요청 간격을 늘리세요")
                    raise Exception("API 사용량 한도 초과")
                else:
                    raise
            
            try:
                sheet = workbook.worksheet(sheet_name)
                logger.debug(f"'{sheet_name}' 워크시트 접근 성공")
            except Exception as e:
                if "not found" in str(e).lower():
                    logger.error(f"❌ '{sheet_name}' 시트를 찾을 수 없습니다")
                    available_sheets = [ws.title for ws in workbook.worksheets()]
                    logger.error(f"🔧 사용 가능한 시트: {available_sheets}")
                    raise Exception(f"'{sheet_name}' 시트를 찾을 수 없습니다")
                else:
                    raise
            
            video_id_col = self.find_video_id_column(sheet, sheet_name)
            transcript_col = self.find_transcript_column(sheet, sheet_name)
            
            logger.debug(f"컬럼 위치 - 영상ID: {video_id_col}, 대본내용: {transcript_col}")
            
            try:
                video_ids = sheet.col_values(video_id_col)
                logger.debug(f"영상 ID 컬럼에서 {len(video_ids)}개 값 읽기 완료")
            except Exception as e:
                if "429" in str(e) or "quota" in str(e).lower():
                    logger.error("❌ API 사용량 한도를 초과했습니다")
                    logger.error("🔧 해결방법: 잠시 후 다시 시도하거나 요청 간격을 늘리세요")
                    raise Exception("API 사용량 한도 초과")
                else:
                    raise
            
            row_mapping = None
            
            if mode == 'A':
                start_row = 10
                filtered_ids = [vid.strip() for vid in video_ids[9:] if vid.strip()]
                logger.debug(f"모드 A: 10행부터 {len(filtered_ids)}개 영상 ID 필터링")
            elif mode == 'B':
                last_transcript_row = self.find_last_transcript_row(sheet, transcript_col)
                start_row = last_transcript_row + 1
                filtered_ids = [vid.strip() for vid in video_ids[start_row-1:] if vid.strip()]
                logger.debug(f"모드 B: {start_row}행부터 {len(filtered_ids)}개 영상 ID 필터링")
            else:  # mode == 'C' (중간 누락 대본 추출)
                # 대본유무 열 찾기
                script_exists_col = self.find_column_by_keyword(sheet, '대본유무')
                if not script_exists_col:
                    raise Exception("'대본유무' 열을 찾을 수 없습니다")
                
                # 대본유무 열의 모든 값 가져오기
                script_exists_values = sheet.col_values(script_exists_col)
                
                # 마지막 데이터가 있는 행 찾기 (마지막 행 제외를 위해)
                last_data_row = len(video_ids)
                for i in range(len(video_ids) - 1, 9, -1):  # 10행부터 역순으로 검색
                    if video_ids[i].strip():
                        last_data_row = i + 1
                        break
                
                # 10행부터 마지막 데이터 행 전까지 중간에 비어있는 '대본유무' 찾기
                missing_ids = []
                missing_rows = []
                for i in range(9, last_data_row - 1):  # 마지막 행 제외
                    video_id = video_ids[i].strip() if i < len(video_ids) else ""
                    script_exists = script_exists_values[i].strip() if i < len(script_exists_values) else ""
                    
                    # 영상 ID는 있지만 대본유무가 비어있는 경우
                    if video_id and not script_exists:
                        missing_ids.append(video_id)
                        missing_rows.append(i + 1)  # 1-based row number
                
                filtered_ids = missing_ids
                start_row = missing_rows[0] if missing_rows else 10
                row_mapping = missing_rows  # 중간 누락 모드에서는 행 매핑 정보 저장
                logger.debug(f"모드 C (중간 누락): {len(filtered_ids)}개 누락 대본 발견 (마지막 데이터 행 {last_data_row} 제외)")
                if missing_rows:
                    logger.debug(f"누락된 행들: {missing_rows}")
            
            # max_count 제한 적용
            if max_count is not None and max_count > 0:
                original_count = len(filtered_ids)
                filtered_ids = filtered_ids[:max_count]
                logger.info(f"수집 갯수 제한 적용: {original_count}개 → {len(filtered_ids)}개 (최대 {max_count}개)")
            
            logger.info(f"비디오 ID 추출 완료: {len(filtered_ids)}개 ({start_row}행부터)")
            return filtered_ids, start_row, transcript_col, row_mapping
            
        except Exception as e:
            if "API 사용량 한도 초과" in str(e) or "시트 접근 권한이 없습니다" in str(e) or "시트를 찾을 수 없습니다" in str(e):
                raise
            else:
                logger.exception(f"'{sheet_name}' 시트에서 비디오 ID 가져오기 실패:")
                raise
        
    def update_sheet_with_transcripts(self, sheet_url: str, sheet_name: str, video_data_list: List[VideoData], 
                                    start_row: int, transcript_col: int, include_timestamp: bool = True, row_mapping: Optional[List[int]] = None):
        """시트에 자막 데이터 업데이트"""
        try:
            logger.info(f"📝 시트 업데이트 시작: {len(video_data_list)}개 영상 데이터")
            workbook = self.client.open_by_url(sheet_url)
            sheet = workbook.worksheet(sheet_name)
            
            updates = []
            success_count = 0
            error_count = 0
            
            # 열 번호를 올바른 열 문자로 변환 (52열 = AZ열)
            def column_number_to_letter(col_num):
                result = ""
                while col_num > 0:
                    col_num -= 1
                    result = chr(col_num % 26 + ord('A')) + result
                    col_num //= 26
                return result
            
            for i, video_data in enumerate(video_data_list):
                # row_mapping이 있으면 해당 매핑 사용, 없으면 연속된 행 사용
                if row_mapping and i < len(row_mapping):
                    row = row_mapping[i]
                else:
                    row = start_row + i
                    
                col_letter = column_number_to_letter(transcript_col)
                cell_address = f'{col_letter}{row}'
                
                if video_data.transcript:
                    if include_timestamp:
                        transcript_text = '\n'.join([f"({ts}) {text}" for ts, text in video_data.transcript])
                    else:
                        transcript_text = '\n'.join([text for ts, text in video_data.transcript])

                    # 타임스탬프를 제외한 순수 텍스트만 추출
                    text_only = '\n'.join([text for ts, text in video_data.transcript])

                    # [음악], [박수] 등 특수 표기 제거
                    import re
                    text_cleaned = re.sub(r'\[.*?\]', '', text_only)

                    # 세그먼트(줄) 개수 계산 (빈 줄과 특수 표기만 있는 줄 제외)
                    segments = [line.strip() for line in text_cleaned.split('\n') if line.strip()]
                    segment_count = len(segments)

                    # 대본이 6개 세그먼트(줄) 이하인 경우: 대본내용 셀에 'x' 저장
                    if segment_count <= 6:
                        logger.info(f"⚠️ {video_data.video_id}: 대본 내용이 6개 세그먼트 이하 ({segment_count}개) - 대본내용 셀에 'x' 저장")
                        updates.append({
                            'range': cell_address,
                            'values': [['x']]  # 대본내용 셀에 'x' 저장
                        })
                        success_count += 1
                        continue

                    # 구글 시트 셀 길이 제한 확인 (32,767자 이하로 설정)
                    MAX_CELL_LENGTH = 30000  # 여유를 둔 제한값
                    
                    if len(transcript_text) > MAX_CELL_LENGTH:
                        # 긴 대본은 드라이브에 자동 저장
                        logger.info(f"📄 {video_data.video_id}: 대본 길이 초과 ({len(transcript_text)}자) - 드라이브에 자동 저장")
                        
                        try:
                            # 영상 제목 추출 (video_data에서 가져오거나 video_id 사용)
                            title = getattr(video_data, 'title', video_data.video_id)
                            
                            # 해당 행에서 재생목록 이름 추출
                            if row_mapping and i < len(row_mapping):
                                current_row = row_mapping[i]
                            else:
                                current_row = start_row + i
                            playlist_name = self.get_playlist_name_from_sheet(sheet_url, sheet_name, current_row)
                            
                            # 시트에서 메타데이터 추출
                            try:
                                metadata = self.get_video_metadata_from_sheet(sheet_url, sheet_name, current_row, video_data.video_id)
                            except Exception as meta_error:
                                logger.warning(f"⚠️ {video_data.video_id}: 메타데이터 추출 실패 - {meta_error}")
                                metadata = {}
                            
                            # 드라이브에 문서 저장
                            docs_info, txt_info, thumbnail_info = self.create_drive_documents_for_long_transcript(
                                video_data.video_id, title, transcript_text, playlist_name, metadata, sheet_name
                            )
                            
                            # 대본내용 셀에는 대체 메시지 입력
                            updates.append({
                                'range': cell_address,
                                'values': [["길이 초과로 드라이브 닥스 저장처리"]]
                            })
                            
                            # 관련 헤더들 업데이트를 위한 정보 저장
                            setattr(video_data, '_drive_docs_info', docs_info)
                            setattr(video_data, '_drive_txt_info', txt_info)
                            setattr(video_data, '_drive_thumbnail_info', thumbnail_info)
                            setattr(video_data, '_is_long_transcript', True)
                            
                            success_count += 1
                            logger.info(f"✅ {video_data.video_id}: 긴 대본 드라이브 저장 완료")
                            
                        except Exception as e:
                            logger.error(f"❌ {video_data.video_id}: 긴 대본 드라이브 저장 실패 - {e}")
                            # 실패 시 에러 메시지 입력
                            updates.append({
                                'range': cell_address,
                                'values': [[f"에러: 대본 길이 초과, 드라이브 저장 실패 - {str(e)}"]]
                            })
                            error_count += 1
                    else:
                        # 일반적인 경우: 셀에 직접 입력
                        updates.append({
                            'range': cell_address,
                            'values': [[transcript_text]]
                        })
                        
                        # 일반 대본에 대해서도 썸네일 처리 추가
                        try:
                            # 해당 행에서 재생목록 이름 추출
                            if row_mapping and i < len(row_mapping):
                                current_row = row_mapping[i]
                            else:
                                current_row = start_row + i
                            
                            title = getattr(video_data, 'title', video_data.video_id)
                            playlist_name = self.get_playlist_name_from_sheet(sheet_url, sheet_name, current_row)
                            
                            # 시트에서 메타데이터 추출
                            try:
                                metadata = self.get_video_metadata_from_sheet(sheet_url, sheet_name, current_row, video_data.video_id)
                            except Exception as meta_error:
                                logger.warning(f"⚠️ {video_data.video_id}: 메타데이터 추출 실패 - {meta_error}")
                                metadata = {}
                            
                            # 재생목록 폴더 찾기/생성 (Google Drive 기준)
                            base_folder_id = "1qJy-LUcKdnTw0wgMOj-o26wsgb-ZY8oA"
                            playlist_folder_id = self.find_or_create_folder(playlist_name, base_folder_id)
                            
                            # 썸네일 이미지 다운로드 및 저장
                            thumbnail_info = self.download_and_save_thumbnail(video_data.video_id, title, playlist_folder_id)
                            
                            # 썸네일 처리 결과를 video_data에 저장
                            setattr(video_data, '_thumbnail_info', thumbnail_info)
                            logger.info(f"🖼️ {video_data.video_id}: 일반 대본과 함께 썸네일 처리 완료")
                            
                        except Exception as thumb_error:
                            logger.error(f"❌ {video_data.video_id}: 썸네일 처리 실패 - {thumb_error}")
                            # 썸네일 실패해도 대본 처리는 계속 진행
                            setattr(video_data, '_thumbnail_info', {'success': False})
                        
                        success_count += 1
                        timestamp_info = "타임스탬프 포함" if include_timestamp else "텍스트만"
                        logger.debug(f"📝 {video_data.video_id}: {cell_address}에 {len(video_data.transcript)}개 세그먼트 업데이트 준비 ({timestamp_info})")
                elif video_data.error:
                    # 에러 메시지가 "Transcript not available"인 경우 'x'로 표시
                    if "Transcript not available" in str(video_data.error) or "자막을 사용할 수 없습니다" in str(video_data.error):
                        updates.append({
                            'range': cell_address,
                            'values': [['x']]
                        })
                        error_count += 1
                        logger.debug(f"📝 {video_data.video_id}: {cell_address}에 'x' 업데이트 준비 (자막 없음)")
                    else:
                        # 기타 에러는 에러 메시지 표시
                        updates.append({
                            'range': cell_address,
                            'values': [[f"에러: {video_data.error}"]]
                        })
                        error_count += 1
                        logger.debug(f"📝 {video_data.video_id}: {cell_address}에 오류 메시지 업데이트 준비")
                else:
                    # transcript도 error도 없는 경우 (자막 데이터가 없는 경우)
                    updates.append({
                        'range': cell_address,
                        'values': [['x']]
                    })
                    error_count += 1
                    logger.info(f"⚠️ {video_data.video_id}: 자막 데이터 없음 - 'x' 표시")
                    
            if updates:
                logger.info(f"📝 실제 업데이트 실행: {len(updates)}개 셀 (성공: {success_count}, 실패: {error_count})")

                # 전역함수 보호를 위해 헤더 정보 가져오기
                headers = sheet.row_values(1)

                # 벌크 업데이트 + 전역함수 열 자동 보호
                bulk_update_with_formula_protection(
                    sheet=sheet,
                    updates=updates,
                    headers=headers,
                    sheet_type=SheetType.VIDEO_LIST
                )
                logger.info(f"✅ 시트 업데이트 완료: {len(updates)}개 셀 (전역함수 보호 적용)")

                # 모든 대본에 대해 헤더 업데이트 (긴 대본 + 일반 대본의 썸네일 포함)
                self.update_all_transcript_headers(
                    sheet, video_data_list, row_mapping, start_row
                )
            else:
                logger.warning("⚠️  업데이트할 데이터가 없습니다")
                
        except Exception as e:
            logger.error(f"❌ 시트 업데이트 중 오류 발생: {e}")
            raise
    
    def backup_sheet(self, sheet_url: str, sheet_name: str) -> str:
        """시트 백업 생성"""
        try:
            workbook = self.client.open_by_url(sheet_url)
            source_sheet = workbook.worksheet(sheet_name)
            
            # 백업 시트 이름 생성 (중복 체크)
            backup_name = self.generate_backup_name(workbook, sheet_name)
            
            logger.info(f"📋 '{sheet_name}' 시트를 '{backup_name}'으로 백업 중...")
            
            # 시트 복사
            new_sheet = source_sheet.duplicate(new_sheet_name=backup_name)
            
            # 백업 시트를 맨 뒤로 이동
            all_sheets = workbook.worksheets()
            workbook.reorder_worksheets([sheet for sheet in all_sheets if sheet.title != backup_name] + [new_sheet])
            
            logger.info(f"✅ 백업 완료: '{backup_name}' (맨 뒤에 배치)")
            return backup_name
            
        except Exception as e:
            error_msg = f"백업 생성 실패: {str(e)}"
            logger.error(error_msg)
            raise Exception(error_msg)
    
    def generate_backup_name(self, workbook, original_name: str) -> str:
        """백업 시트 이름 생성 (중복 시 번호 증가)"""
        existing_sheets = [sheet.title for sheet in workbook.worksheets()]
        
        # 기본 백업 이름
        base_name = f"{original_name}_백업"
        
        # 번호 없는 백업 이름 시도
        if f"{base_name}01" not in existing_sheets:
            return f"{base_name}01"
        
        # 번호 증가하며 사용 가능한 이름 찾기
        for i in range(2, 100):  # 최대 99까지
            backup_name = f"{base_name}{i:02d}"
            if backup_name not in existing_sheets:
                return backup_name
        
        # 100개 이상인 경우 타임스탬프 추가
        from datetime import datetime
        timestamp = datetime.now().strftime("%m%d_%H%M")
        return f"{base_name}_{timestamp}"
    
    async def test_connection(self, sheet_url: str) -> Dict[str, str]:
        """구글 시트 연결 테스트"""
        result = {
            'status': 'success',
            'message': '',
            'a2_value': '',
            'a10_value': ''
        }
        
        try:
            logger.info("📋 구글 시트 연결 테스트 시작")
            logger.info("=" * 80)
            logger.info("🔍 GCP 연결 상태 진단")
            logger.info("=" * 80)

            # 0단계: GCP 연결 상태 체크
            logger.info("0단계: GCP(Google Cloud Platform) 연결 상태 체크")
            try:
                # 인터넷 연결 확인
                import socket
                socket.create_connection(("www.google.com", 80), timeout=5)
                logger.info("✅ 인터넷 연결 정상")

                # Google API 엔드포인트 접근 확인
                import requests
                gcp_test = requests.get("https://www.googleapis.com", timeout=5)
                logger.info(f"✅ Google APIs 접근 가능 (HTTP {gcp_test.status_code})")

                # Google Sheets API 엔드포인트 확인
                sheets_test = requests.get("https://sheets.googleapis.com/$discovery/rest?version=v4", timeout=5)
                if sheets_test.status_code == 200:
                    logger.info("✅ Google Sheets API 엔드포인트 정상")
                else:
                    logger.warning(f"⚠️ Google Sheets API 응답: HTTP {sheets_test.status_code}")

            except socket.timeout:
                logger.error("❌ 인터넷 연결 타임아웃")
                raise Exception("인터넷 연결을 확인하세요")
            except socket.gaierror:
                logger.error("❌ DNS 확인 실패 - 인터넷 연결을 확인하세요")
                raise Exception("인터넷 연결 문제")
            except requests.exceptions.RequestException as e:
                logger.warning(f"⚠️ Google API 접근 확인 실패: {e}")
                logger.info("   계속 진행합니다...")
            except Exception as e:
                logger.warning(f"⚠️ GCP 연결 체크 예외: {e}")
                logger.info("   계속 진행합니다...")

            logger.info("")

            # 1단계: 인증 테스트
            logger.info("1단계: 구글 서비스 계정 인증 테스트")
            if not self.client:
                self.authenticate()
            logger.info("✅ 구글 서비스 계정 인증 성공")
            logger.debug(f"   서비스 계정: {self.client.auth.service_account_email if hasattr(self.client.auth, 'service_account_email') else '알 수 없음'}")
            
            # 2단계: 시트 접근 테스트
            logger.info("2단계: 구글 시트 접근 테스트")
            try:
                workbook = self.client.open_by_url(sheet_url)
                logger.info(f"✅ 워크북 접근 성공: {workbook.title}")
            except Exception as e:
                error_msg = str(e).lower()
                if "permission" in error_msg or "denied" in error_msg:
                    logger.error("❌ 시트 접근 권한이 없습니다")
                    logger.error("🔧 해결방법: 구글 시트에서 서비스 계정을 편집자로 공유하세요")
                    logger.error(f"   서비스 계정 이메일: {self.client.auth.service_account_email}")
                    raise Exception("시트 접근 권한이 없습니다")
                elif "429" in str(e) or "quota" in error_msg:
                    logger.error("❌ API 사용량 한도를 초과했습니다")
                    logger.error("🔧 해결방법: 잠시 후 다시 시도하거나 요청 간격을 늘리세요")
                    raise Exception("API 사용량 한도 초과")
                else:
                    raise
            
            # 3단계: 첫 번째 시트 접근 테스트
            available_sheets = [ws.title for ws in workbook.worksheets()]
            if not available_sheets:
                raise Exception("워크시트가 없습니다")
            
            first_sheet_name = available_sheets[0]
            logger.info(f"3단계: '{first_sheet_name}' 워크시트 접근 테스트")
            try:
                sheet = workbook.worksheet(first_sheet_name)
                logger.info(f"✅ '{first_sheet_name}' 워크시트 접근 성공")
            except Exception as e:
                if "not found" in str(e).lower():
                    logger.error(f"❌ '{first_sheet_name}' 시트를 찾을 수 없습니다")
                    logger.error("🔧 해결방법: 시트 이름을 확인하거나 해당 시트가 존재하는지 확인하세요")
                    logger.error(f"   사용 가능한 시트: {available_sheets}")
                    raise Exception(f"'{first_sheet_name}' 시트를 찾을 수 없습니다")
                else:
                    raise
            
            # 4단계: A2 셀 값 읽기 테스트 (영상 ID 갯수)
            logger.info("4단계: A2 셀 값 읽기 테스트 (영상 ID 갯수)")
            try:
                a2_value = sheet.cell(2, 1).value
                result['a2_value'] = str(a2_value) if a2_value else ""
                logger.info(f"✅ A2 셀 값 읽기 성공: '{a2_value}'")
            except Exception as e:
                if "429" in str(e) or "quota" in str(e).lower():
                    logger.error("❌ API 사용량 한도를 초과했습니다")
                    logger.error("🔧 해결방법: 잠시 후 다시 시도하거나 요청 간격을 늘리세요")
                    raise Exception("API 사용량 한도 초과")
                else:
                    raise
            
            # 5단계: A10 셀 값 읽기 테스트 (첫번째 영상 ID값)  
            logger.info("5단계: A10 셀 값 읽기 테스트 (첫번째 영상 ID값)")
            try:
                a10_value = sheet.cell(10, 1).value
                result['a10_value'] = str(a10_value) if a10_value else ""
                logger.info(f"✅ A10 셀 값 읽기 성공: '{a10_value}'")
            except Exception as e:
                if "429" in str(e) or "quota" in str(e).lower():
                    logger.error("❌ API 사용량 한도를 초과했습니다")
                    logger.error("🔧 해결방법: 잠시 후 다시 시도하거나 요청 간격을 늘리세요")
                    raise Exception("API 사용량 한도 초과")
                else:
                    raise
            
            # 6단계: 첫번째 영상 ID로 실제 대본 추출 테스트
            logger.info("6단계: 첫번째 영상으로 실제 대본 추출 테스트")
            if result['a10_value']:
                test_video_id = result['a10_value'].strip()
                logger.info(f"테스트 영상 ID: {test_video_id}")
                
                # 대본 추출 테스트 (데이터 변경하지 않음)
                transcript_test_result = await self._test_transcript_extraction(test_video_id)
                result.update(transcript_test_result)
                
                if transcript_test_result['transcript_success']:
                    extraction_method = transcript_test_result.get('extraction_method', '알 수 없음')
                    logger.info(f"✅ 대본 추출 테스트 성공: {transcript_test_result['transcript_segments']}개 세그먼트")
                    logger.info(f"   추출 방식: {extraction_method}")
                    logger.info("🎉 모든 연결 및 대본 추출 테스트 완료!")
                    result['message'] = f"모든 테스트가 성공적으로 완료되었습니다.\n대본 추출: {transcript_test_result['transcript_segments']}개 세그먼트 ({extraction_method})"
                else:
                    error_msg = transcript_test_result['transcript_error']
                    logger.warning(f"⚠️ 대본 추출 테스트 실패: {error_msg}")
                    logger.info("🎉 구글 시트 연결 테스트는 완료되었으나 대본 추출에 문제가 있습니다.")

                    # 해결 방법 제안
                    if 'youtube-transcript-api 라이브러리 미설치' in error_msg:
                        logger.warning("📌 해결 방법: pip install youtube-transcript-api 명령어를 실행하세요")
                        result['message'] = f"구글 시트 연결 성공, 대본 추출 실패\n원인: {error_msg}\n해결 방법: pip install youtube-transcript-api"
                    elif 'PoToken' in error_msg or '빈 응답' in error_msg:
                        logger.warning("📌 해결 방법: youtube-transcript-api 라이브러리 설치 필요 (pip install youtube-transcript-api)")
                        result['message'] = f"구글 시트 연결 성공, 대본 추출 실패\n원인: YouTube API 변경 (PoToken 요구)\n해결 방법: youtube-transcript-api 라이브러리 설치"
                    else:
                        result['message'] = f"구글 시트 연결은 성공했으나 대본 추출 실패\n원인: {error_msg}"
            else:
                logger.warning("⚠️ A10 셀이 비어있어 대본 추출 테스트를 건너뜁니다.")
                result['message'] = "구글 시트 연결 테스트는 완료되었으나 테스트할 영상 ID가 없습니다."
            
        except Exception as e:
            logger.exception("❌ 구글 시트 연결 테스트 실패:")
            result['status'] = 'failed'
            result['message'] = f"연결 테스트 실패: {str(e)}"
            
        return result
    
    async def _test_transcript_extraction(self, video_id: str) -> Dict[str, any]:
        """대본 추출 테스트 (읽기 전용)"""
        test_result = {
            'transcript_success': False,
            'transcript_segments': 0,
            'transcript_error': '',
            'transcript_language': '',
            'transcript_urls_tried': [],
            'transcript_final_url': '',
            'extraction_method': ''
        }

        try:
            # 임시 추출기 생성 (기존 설정과 동일)
            config = TranscriptConfig(
                target_language=None,
                max_concurrent=1,
                retry_attempts=1,
                delay_between_requests=1.0
            )

            logger.info("6-1단계: 대본 추출기 초기화")

            # 0단계: youtube-transcript-api 라이브러리 먼저 시도
            youtube_api_success = False
            try:
                from youtube_transcript_api import YouTubeTranscriptApi
                from youtube_transcript_api._errors import TranscriptsDisabled, NoTranscriptFound, VideoUnavailable

                logger.info("6-0단계: youtube-transcript-api 라이브러리로 자막 추출 시도 (v1.2.2 API)")
                logger.debug(f"6-0A단계: 영상 ID - {video_id}")

                try:
                    # youtube-transcript-api v1.2.2는 인스턴스 생성 후 fetch() 메서드 사용
                    logger.debug("6-0B단계: YouTubeTranscriptApi 인스턴스 생성...")
                    ytt_api = YouTubeTranscriptApi()

                    logger.debug("6-0C단계: 한국어 자막 다운로드 시도 (fetch API)...")
                    transcript_data = None

                    try:
                        # 한국어 자막 시도
                        transcript_data = ytt_api.fetch(video_id, languages=['ko', 'kr'])
                        logger.info(f"6-0D단계: 한국어 자막 다운로드 성공 - {len(transcript_data)}개 세그먼트")
                    except NoTranscriptFound:
                        logger.debug("6-0E단계: 한국어 자막 없음, 영어 자막 시도...")
                        try:
                            # 영어 자막 시도
                            transcript_data = ytt_api.fetch(video_id, languages=['en'])
                            logger.info(f"6-0F단계: 영어 자막 다운로드 성공 - {len(transcript_data)}개 세그먼트")
                        except NoTranscriptFound:
                            logger.debug("6-0G단계: 영어 자막 없음, 모든 언어 자막 시도...")
                            # 모든 사용 가능한 자막 시도
                            transcript_data = ytt_api.fetch(video_id)
                            logger.info(f"6-0H단계: 자막 다운로드 성공 - {len(transcript_data)}개 세그먼트")

                    if transcript_data:
                        logger.debug("6-0I단계: 자막 데이터 처리 중...")
                        logger.info(f"6-0J단계: 자막 데이터 처리 완료 - {len(transcript_data)}개 세그먼트")

                        # 성공
                        test_result['transcript_success'] = True
                        test_result['transcript_segments'] = len(transcript_data)
                        test_result['transcript_language'] = 'ko'
                        test_result['transcript_final_url'] = f"https://www.youtube.com/watch?v={video_id}"
                        test_result['extraction_method'] = 'youtube-transcript-api (v1.2.2 fetch)'

                        logger.info(f"✅ 6-0K단계: youtube-transcript-api로 자막 추출 성공! ({len(transcript_data)}개 세그먼트)")
                        return test_result

                except TranscriptsDisabled:
                    logger.warning("6-0-ERR1: 이 영상은 자막이 비활성화되어 있습니다")
                    test_result['transcript_error'] = '자막이 비활성화됨 (youtube-transcript-api)'
                except NoTranscriptFound:
                    logger.warning("6-0-ERR2: 사용 가능한 자막을 찾을 수 없습니다")
                    test_result['transcript_error'] = '사용 가능한 자막 없음 (youtube-transcript-api)'
                except VideoUnavailable:
                    logger.warning("6-0-ERR3: 영상을 사용할 수 없습니다")
                    test_result['transcript_error'] = '영상을 사용할 수 없음 (youtube-transcript-api)'
                except Exception as e:
                    logger.warning(f"6-0-ERR5: youtube-transcript-api 실패 - {str(e)}")
                    logger.exception("상세 오류:")
                    test_result['transcript_error'] = f'youtube-transcript-api 실패: {str(e)}'

            except ImportError as e:
                logger.warning("6-0-ERR4: youtube-transcript-api 라이브러리를 찾을 수 없습니다")
                logger.warning("   → pip install youtube-transcript-api 명령어로 설치하세요")
                logger.debug(f"   ImportError 상세: {e}")
                test_result['transcript_error'] = 'youtube-transcript-api 라이브러리 미설치'
            except Exception as e:
                logger.warning(f"6-0-ERR6: youtube-transcript-api import/초기화 실패 - {str(e)}")
                logger.exception("상세 오류:")
                test_result['transcript_error'] = f'youtube-transcript-api 초기화 실패: {str(e)}'

            logger.info("6-1A단계: youtube-transcript-api 실패, Innertube API 직접 호출 시도")

            # 1단계: Innertube API 직접 호출 시도 (2025년 방식)
            try:
                from innertube_extractor import InnertubeTranscriptExtractor

                logger.info("6-1단계: Innertube API 직접 호출로 자막 추출 시도")
                innertube_extractor = InnertubeTranscriptExtractor()
                innertube_result = innertube_extractor.extract_transcript(video_id)

                if innertube_result['success']:
                    test_result['transcript_success'] = True
                    test_result['transcript_segments'] = len(innertube_result['transcript'])
                    test_result['transcript_language'] = innertube_result['language']
                    test_result['transcript_final_url'] = f"https://www.youtube.com/watch?v={video_id}"
                    test_result['extraction_method'] = 'Innertube API (Android/Web Client)'

                    logger.info(f"✅ 6-1Z단계: Innertube API로 자막 추출 성공! ({len(innertube_result['transcript'])}개 세그먼트)")
                    return test_result
                else:
                    logger.warning(f"6-1-ERR1: Innertube API 실패 - {innertube_result['error']}")
                    test_result['transcript_error'] = f"Innertube API 실패: {innertube_result['error']}"

            except ImportError:
                logger.warning("6-1-ERR2: innertube_extractor 모듈을 찾을 수 없습니다")
                test_result['transcript_error'] = 'innertube_extractor 모듈 없음'
            except Exception as e:
                logger.warning(f"6-1-ERR3: Innertube API 예외 - {str(e)}")
                logger.exception("상세 오류:")
                test_result['transcript_error'] = f'Innertube API 예외: {str(e)}'

            logger.info("6-2A단계: Innertube API 실패, 기존 HTTP 방식으로 폴백 시도")

            async with MainYouTubeShortsTranscriptExtractor(config) as extractor:
                logger.info("6-2단계: 대본 추출 시도 시작")
                
                # 시도할 URL 목록
                urls_to_try = [
                    f"https://www.youtube.com/shorts/{video_id}",
                    f"https://www.youtube.com/watch?v={video_id}"
                ]
                test_result['transcript_urls_tried'] = urls_to_try
                
                last_error = None
                
                for attempt, url in enumerate(urls_to_try, 1):
                    try:
                        url_type = "Shorts" if "/shorts/" in url else "일반 YouTube"
                        logger.info(f"6-2-{attempt}단계: {url_type} URL 테스트 - {url}")
                        
                        # YouTube 페이지 요청
                        async with extractor.session.get(url) as response:
                            if response.status != 200:
                                last_error = f"HTTP {response.status}"
                                logger.warning(f"6-2-{attempt}A단계: HTTP 요청 실패 - {response.status}")
                                continue
                                
                            logger.info(f"6-2-{attempt}B단계: HTML 페이지 다운로드 성공")
                            html = await response.text()
                            logger.info(f"6-2-{attempt}C단계: HTML 크기 - {len(html):,} 바이트")
                        
                        # HTML에서 플레이어 데이터 추출
                        logger.info(f"6-2-{attempt}D단계: ytInitialPlayerResponse 추출 시도")
                        player_data = extractor.extract_json_from_html(html, 'ytInitialPlayerResponse')
                        if not player_data:
                            last_error = "ytInitialPlayerResponse를 찾을 수 없음"
                            logger.warning(f"6-2-{attempt}E단계: ytInitialPlayerResponse 추출 실패")
                            continue
                            
                        logger.info(f"6-2-{attempt}F단계: 플레이어 데이터 추출 성공")
                        
                        # 비디오 제목 확인
                        title = player_data.get('videoDetails', {}).get('title', '')
                        logger.info(f"6-2-{attempt}G단계: 비디오 제목 - '{title[:50]}{'...' if len(title) > 50 else ''}'")
                        
                        # 자막 트랙 찾기
                        logger.info(f"6-2-{attempt}H단계: 자막 트랙 검색")
                        captions = player_data.get('captions', {})
                        caption_tracks = captions.get('playerCaptionsTracklistRenderer', {}).get('captionTracks', [])
                        
                        if not caption_tracks:
                            last_error = "자막을 사용할 수 없음"
                            logger.warning(f"6-2-{attempt}I단계: 자막 트랙이 없음")
                            continue
                            
                        logger.info(f"6-2-{attempt}J단계: {len(caption_tracks)}개 자막 트랙 발견")
                        for i, track in enumerate(caption_tracks):
                            lang = track.get('languageCode', 'unknown')
                            kind = track.get('kind', 'unknown')
                            name = track.get('name', {}).get('simpleText', 'unnamed')
                            logger.debug(f"   트랙 {i+1}: {lang} ({kind}) - {name}")
                        
                        # 최적의 자막 트랙 선택
                        logger.info(f"6-2-{attempt}K단계: 최적 자막 트랙 선택")
                        selected_track = extractor.get_best_caption_track(caption_tracks, config.target_language)
                        if not selected_track:
                            last_error = "적절한 자막 트랙을 찾을 수 없음"
                            logger.warning(f"6-2-{attempt}L단계: 적절한 자막 트랙 없음")
                            continue
                            
                        selected_lang = selected_track.get('languageCode', 'unknown')
                        selected_kind = selected_track.get('kind', 'unknown')
                        logger.info(f"6-2-{attempt}M단계: 선택된 트랙 - {selected_lang} ({selected_kind})")
                        test_result['transcript_language'] = selected_lang
                        
                        # 자막 데이터 요청
                        caption_url = selected_track['baseUrl'] + "&fmt=json3"
                        logger.info(f"6-2-{attempt}N단계: 자막 API 요청")
                        logger.debug(f"자막 URL: {caption_url}")
                        
                        async with extractor.session.get(caption_url) as caption_response:
                            logger.info(f"6-2-{attempt}O단계: 자막 API 응답 수신 - HTTP {caption_response.status}")
                            
                            if caption_response.status != 200:
                                last_error = f"자막 요청 실패: HTTP {caption_response.status}"
                                logger.warning(f"6-2-{attempt}P단계: 자막 API HTTP 오류 - {caption_response.status}")
                                continue
                            
                            # Content-Type 확인
                            content_type = caption_response.headers.get('content-type', '')
                            logger.info(f"6-2-{attempt}Q단계: 응답 Content-Type - {content_type}")
                            
                            if 'application/json' not in content_type:
                                # HTML 응답인 경우 내용 분석
                                response_text = await caption_response.text()
                                logger.warning(f"6-2-{attempt}R단계: 예상치 못한 응답 타입 - {content_type}")
                                logger.debug(f"응답 내용 (처음 500자): {response_text[:500]}")

                                # 응답 헤더 전체 로깅
                                all_headers = dict(caption_response.headers)
                                logger.debug(f"6-2-{attempt}R-1단계: 전체 응답 헤더 - {all_headers}")

                                # 응답이 비어있는지 확인
                                response_text_clean = response_text.strip()
                                if not response_text_clean:
                                    last_error = "자막 API가 빈 응답을 반환함 (PoToken 필요 가능성)"
                                    logger.warning(f"6-2-{attempt}S단계: 빈 응답 수신")
                                    logger.warning(f"   → YouTube가 2025년부터 PoToken(Proof of Origin Token)을 요구합니다")
                                    logger.warning(f"   → youtube-transcript-api 라이브러리 설치를 권장합니다: pip install youtube-transcript-api")
                                else:
                                    last_error = f"자막 API가 HTML을 반환함 (JSON 예상) - 길이: {len(response_text)} 바이트"
                                    logger.warning(f"6-2-{attempt}T단계: HTML 응답 수신 - {len(response_text)} 바이트")
                                    # HTML 내용에 오류 메시지가 있는지 확인
                                    if 'error' in response_text.lower() or 'bot' in response_text.lower():
                                        logger.warning(f"6-2-{attempt}T-1단계: 응답에 'error' 또는 'bot' 키워드 발견")
                                        logger.debug(f"   응답 전체: {response_text}")
                                continue
                                
                            logger.info(f"6-2-{attempt}U단계: JSON 응답 파싱 시도")
                            caption_data = await caption_response.json()
                            logger.info(f"6-2-{attempt}V단계: JSON 파싱 성공")
                            
                        # 자막 파싱
                        logger.info(f"6-2-{attempt}W단계: 자막 이벤트 파싱")
                        events = caption_data.get('events', [])
                        logger.info(f"6-2-{attempt}X단계: {len(events)}개 자막 이벤트 발견")
                        
                        transcript_count = 0
                        for event in events:
                            if 'segs' in event:
                                for seg in event['segs']:
                                    if 'utf8' in seg and seg['utf8'].strip():
                                        transcript_count += 1
                        
                        logger.info(f"6-2-{attempt}Y단계: {transcript_count}개 자막 세그먼트 추출 완료")
                        
                        # 성공
                        test_result['transcript_success'] = True
                        test_result['transcript_segments'] = transcript_count
                        test_result['transcript_final_url'] = url
                        logger.info(f"6-2-{attempt}Z단계: ✅ {url_type} URL로 대본 추출 테스트 성공!")
                        return test_result
                        
                    except Exception as e:
                        last_error = str(e)
                        logger.error(f"6-2-{attempt}ERROR단계: {url_type} URL 테스트 실패 - {e}")
                        logger.exception(f"상세 오류 ({video_id}):")
                        continue
                
                # 모든 URL 실패 - 브라우저 자동화 시도
                logger.info(f"6-3A단계: HTTP 방식 실패, 브라우저 자동화(JavaScript) 방식 시도")

            # 6-3단계: 브라우저 자동화로 마지막 시도
            try:
                from Main_Extract import BrowserTranscriptExtractor, TranscriptConfig as BrowserConfig

                logger.info("6-3단계: 브라우저 자동화 모드로 자막 추출 시도")

                browser_config = BrowserConfig(
                    target_language=None,
                    max_concurrent=1,
                    retry_attempts=1,
                    delay_between_requests=1.0,
                    use_browser_automation=True,
                    headless=True,
                    use_user_profile=False
                )

                with BrowserTranscriptExtractor(browser_config) as browser_extractor:
                    result = browser_extractor.extract_transcript_from_video(video_id)

                    if result and result.get('success') and result.get('transcript'):
                        transcript = result['transcript']
                        test_result['transcript_success'] = True
                        test_result['transcript_segments'] = len(transcript)
                        test_result['transcript_language'] = result.get('language', 'unknown')
                        test_result['transcript_final_url'] = f"https://www.youtube.com/watch?v={video_id}"
                        test_result['extraction_method'] = '브라우저 자동화 (JavaScript)'

                        logger.info(f"✅ 6-3Z단계: 브라우저 자동화로 자막 추출 성공! ({len(transcript)}개 세그먼트)")
                        return test_result
                    else:
                        error_msg = result.get('error', '알 수 없는 오류') if result else '결과 없음'
                        logger.warning(f"6-3-ERR1: 브라우저 자동화 실패 - {error_msg}")
                        test_result['transcript_error'] = f"모든 방식 실패 (마지막: 브라우저 자동화 - {error_msg})"

            except ImportError as ie:
                logger.warning(f"6-3-ERR2: Main_Extract 모듈 import 실패 - {ie}")
                test_result['transcript_error'] = f"모든 URL 형식 실패: {last_error} (브라우저 자동화 모듈 없음)"
            except Exception as browser_error:
                logger.exception(f"6-3-ERR3: 브라우저 자동화 예외:")
                test_result['transcript_error'] = f"모든 방식 실패 (마지막: 브라우저 자동화 오류 - {str(browser_error)})"

            logger.error(f"6-4단계: ❌ 모든 추출 방식 실패")

        except Exception as e:
            test_result['transcript_error'] = f"대본 추출 테스트 중 예외 발생: {str(e)}"
            logger.exception(f"6-ERROR단계: 대본 추출 테스트 예외:")

        return test_result
    
    def find_column_by_keyword(self, sheet, keyword: str) -> Optional[int]:
        """키워드를 포함한 열 찾기"""
        try:
            all_values = sheet.get_all_values()
            logger.debug(f"'{keyword}' 키워드를 포함한 열 찾기 시작")

            # 상위 20행에서 검색
            for row_idx, row in enumerate(all_values[:20]):
                for col_idx, cell in enumerate(row):
                    if keyword in str(cell):
                        logger.debug(f"'{keyword}' 키워드를 포함한 열 발견: {row_idx+1}행 {col_idx+1}열")
                        return col_idx + 1

            logger.warning(f"'{keyword}' 키워드를 포함한 열을 찾을 수 없습니다")
            return None

        except Exception as e:
            logger.error(f"'{keyword}' 열 찾기 실패: {e}")
            return None

    def is_deleted_channel(self, row, fetch_channel_col):
        """
        채널이 삭제된 채널인지 확인 (가져올 채널 값이 'x'인 경우)

        Args:
            row: 채널 데이터 행
            fetch_channel_col: 가져올 채널 컬럼 인덱스 (1-based)

        Returns:
            bool: 삭제된 채널이면 True
        """
        if not fetch_channel_col or len(row) < fetch_channel_col:
            return False

        fetch_value = str(row[fetch_channel_col - 1]).strip().lower()
        return fetch_value == 'x'
    
    def get_docs_extraction_data(self, sheet_url: str, sheet_name: str, max_count: int = 10, extraction_mode: str = "both") -> List[Dict]:
        """구글 닥스 추출을 위한 데이터 가져오기"""
        try:
            logger.info(f"📋 구글 닥스 추출 데이터 수집 시작 (최대 {max_count}개, 모드: {extraction_mode})")
            
            workbook = self.client.open_by_url(sheet_url)
            sheet = workbook.worksheet(sheet_name)
            
            # 필요한 열 찾기
            transcript_col = self.find_column_by_keyword(sheet, '대본내용')
            script_exists_col = self.find_column_by_keyword(sheet, '대본유무')
            playlist_col = self.find_column_by_keyword(sheet, '재생목록 이름')
            title_col = self.find_column_by_keyword(sheet, '제목')
            
            # 비디오 ID 추출을 위한 열 찾기
            video_id_col = self.find_column_by_keyword(sheet, '영상 ID')
            
            # shorts_thumbnail 모드가 아닌 경우에만 Docs/TXT 관련 열 찾기
            docs_status_col = None
            docs_id_col = None
            docs_path_col = None
            txt_status_col = None
            txt_id_col = None
            txt_path_col = None
            
            if extraction_mode != "shorts_thumbnail":
                # Docs 관련 열
                docs_status_col = self.find_column_by_keyword(sheet, '구글 닥스 여부') or self.find_column_by_keyword(sheet, '구글닥스 여부')
                docs_id_col = self.find_column_by_keyword(sheet, '닥스파일 ID')
                docs_path_col = self.find_column_by_keyword(sheet, '닥스파일 경로')
                
                # TXT 관련 열
                txt_status_col = self.find_column_by_keyword(sheet, '대본txt 여부') or self.find_column_by_keyword(sheet, '대본 txt 여부')
                txt_id_col = self.find_column_by_keyword(sheet, '대본txt ID')
                txt_path_col = self.find_column_by_keyword(sheet, '대본txt 경로')
            
            # 썸네일 관련 열
            thumbnail_status_col = self.find_column_by_keyword(sheet, '썸네일 여부')
            thumbnail_image_col = self.find_column_by_keyword(sheet, '썸네일 이미지주소')
            thumbnail_path_col = self.find_column_by_keyword(sheet, '썸네일 경로')
            
            if not all([transcript_col, script_exists_col, playlist_col, title_col]):
                missing_cols = []
                if not transcript_col: missing_cols.append('대본내용')
                if not script_exists_col: missing_cols.append('대본유무')
                if not playlist_col: missing_cols.append('재생목록 이름')
                if not title_col: missing_cols.append('제목')
                
                raise Exception(f"필수 열을 찾을 수 없습니다: {', '.join(missing_cols)}")
            
            # 비디오 ID 관련 로그
            if video_id_col:
                logger.info(f"📊 영상 ID 열 찾음: {video_id_col}")
            else:
                logger.warning(f"⚠️ '영상 ID' 열을 찾을 수 없습니다. 썸네일 기능이 제한될 수 있습니다.")
            
            logger.info(f"📊 기본 열 위치 - 대본내용: {transcript_col}, 대본유무: {script_exists_col}, 재생목록이름: {playlist_col}, 제목: {title_col}")
            if docs_status_col:
                logger.info(f"📊 Docs 열 - 구글닥스여부: {docs_status_col}, 닥스파일ID: {docs_id_col}, 닥스파일경로: {docs_path_col}")
            if txt_status_col:
                logger.info(f"📊 TXT 열 - 대본txt여부: {txt_status_col}, 대본txtID: {txt_id_col}, 대본txt경로: {txt_path_col}")
            
            # 썸네일 관련 열 (썸네일 모드용)
            thumbnail_status_col = self.find_column_by_keyword(sheet, '썸네일 여부')
            if thumbnail_status_col:
                logger.info(f"🖼️ 썸네일 열 - 썸네일여부: {thumbnail_status_col}")

            # 모든 데이터 가져오기 (헤더는 formatted, 데이터는 읽기만 하므로 formatted 사용 가능)
            all_values = sheet.get_all_values()

            extraction_data = []
            processed_count = 0
            
            # 10행부터 시작해서 조건에 맞는 행 찾기
            for row_idx in range(9, len(all_values)):  # 10행부터 (인덱스 9)
                if processed_count >= max_count:
                    break
                
                row = all_values[row_idx]
                row_num = row_idx + 1
                
                # 기본 조건 확인
                transcript_content = row[transcript_col - 1] if transcript_col - 1 < len(row) else ""
                script_exists = row[script_exists_col - 1] if script_exists_col - 1 < len(row) else ""
                playlist_name = row[playlist_col - 1] if playlist_col - 1 < len(row) else ""
                title = row[title_col - 1] if title_col - 1 < len(row) else ""
                
                # 비디오 ID 추출
                video_id = None
                if video_id_col and video_id_col - 1 < len(row):
                    video_id_value = row[video_id_col - 1].strip()
                    if video_id_value:
                        video_id = video_id_value
                        logger.debug(f"🔍 {row_num}행: 비디오 ID 추출 = {video_id}")
                
                if not video_id:
                    logger.debug(f"🔍 {row_num}행: 비디오 ID 없음 - 썸네일 기능 사용 불가")
                
                # 기본 조건: 모드에 따라 다르게 처리
                if extraction_mode == "shorts_thumbnail":
                    # 쇼츠용 썸네일 모드: 대본유무 열에 데이터만 있으면 됨
                    if not (script_exists.strip() and video_id):
                        continue
                else:
                    # 다른 모드: 대본내용이 있고, 대본유무가 'ㅇ'
                    if not (transcript_content.strip() and script_exists.strip() == 'ㅇ'):
                        continue
                
                # 모드별 조건 확인  
                docs_status = row[docs_status_col - 1] if docs_status_col and docs_status_col - 1 < len(row) else ""
                txt_status = row[txt_status_col - 1] if txt_status_col and txt_status_col - 1 < len(row) else ""
                
                should_process = False
                needs_docs = False
                needs_txt = False
                
                if extraction_mode == "both":
                    # 둘 다 저장 모드: Docs가 비어있으면 처리
                    if not docs_status.strip():
                        should_process = True
                        needs_docs = True
                        needs_txt = True
                elif extraction_mode == "missing":
                    # 누락 대본 추출 모드: Docs나 TXT 중 하나라도 비어있으면 처리
                    if not docs_status.strip() or not txt_status.strip():
                        should_process = True
                        needs_docs = not docs_status.strip()
                        needs_txt = not txt_status.strip()
                elif extraction_mode == "thumbnail":
                    # 썸네일 추출 모드: Docs와 TXT가 이미 있고, 썸네일이 비어있으면 처리
                    thumbnail_status = row[thumbnail_status_col - 1] if thumbnail_status_col and thumbnail_status_col - 1 < len(row) else ""
                    if (docs_status.strip() == 'ㅇ' and txt_status.strip() == 'ㅇ' and 
                        not thumbnail_status.strip() and video_id):
                        should_process = True
                        needs_docs = False  # 기존 Docs 업데이트만
                        needs_txt = False   # TXT는 처리 안함
                elif extraction_mode == "shorts_thumbnail":
                    # 쇼츠용 썸네일만 추출 모드: 대본여부 헤더열에 데이터가 존재하고, 썸네일 여부가 비어있는 경우만 처리
                    thumbnail_status = row[thumbnail_status_col - 1] if thumbnail_status_col and thumbnail_status_col - 1 < len(row) else ""
                    if (script_exists.strip() and video_id and not thumbnail_status.strip()):
                        should_process = True
                        needs_docs = False  # Google Docs 생성하지 않음
                        needs_txt = False   # TXT는 처리 안함
                
                if should_process:
                    extraction_data.append({
                        'row_number': row_num,
                        'transcript_content': transcript_content,
                        'playlist_name': playlist_name,
                        'title': title,
                        'video_id': video_id,
                        'needs_docs': needs_docs,
                        'needs_txt': needs_txt,
                        'docs_status_col': docs_status_col,
                        'docs_id_col': docs_id_col,
                        'docs_path_col': docs_path_col,
                        'txt_status_col': txt_status_col,
                        'txt_id_col': txt_id_col,
                        'txt_path_col': txt_path_col
                    })
                    processed_count += 1
                    doc_txt_info = []
                    if extraction_mode == "shorts_thumbnail":
                        doc_txt_info.append("썸네일만")
                    else:
                        if needs_docs: doc_txt_info.append("Docs")
                        if needs_txt: doc_txt_info.append("TXT")
                    logger.debug(f"📝 {row_num}행: 조건 만족 - 제목: '{title[:30]}...', 재생목록: '{playlist_name}', 필요: {'/'.join(doc_txt_info) if doc_txt_info else '없음'}")
            
            logger.info(f"📊 조건에 맞는 데이터 {len(extraction_data)}개 수집 완료")
            return extraction_data
            
        except Exception as e:
            logger.error(f"구글 닥스 추출 데이터 수집 실패: {e}")
            raise
    
    def find_or_create_folder(self, folder_name: str, parent_folder_id: str = None) -> str:
        """구글 드라이브에서 폴더 찾기 또는 생성"""
        try:
            logger.debug(f"📁 폴더 '{folder_name}' 찾기/생성 시도")
            
            # 부모 폴더 내에서 해당 이름의 폴더 검색
            if parent_folder_id:
                query = f"name='{folder_name}' and parents in '{parent_folder_id}' and mimeType='application/vnd.google-apps.folder' and trashed=false"
            else:
                # 루트 폴더(마이 드라이브)에서 검색
                query = f"name='{folder_name}' and mimeType='application/vnd.google-apps.folder' and trashed=false"
            results = self.drive_service.files().list(q=query, fields="files(id, name)").execute()
            folders = results.get('files', [])
            
            if folders:
                folder_id = folders[0]['id']
                logger.info(f"📁 기존 폴더 발견: '{folder_name}' (ID: {folder_id})")
                return folder_id
            else:
                # 폴더 생성
                folder_metadata = {
                    'name': folder_name,
                    'mimeType': 'application/vnd.google-apps.folder'
                }
                if parent_folder_id:
                    folder_metadata['parents'] = [parent_folder_id]
                
                folder = self.drive_service.files().create(body=folder_metadata, fields='id').execute()
                folder_id = folder.get('id')
                logger.info(f"📁 새 폴더 생성: '{folder_name}' (ID: {folder_id})")
                return folder_id
                
        except HttpError as e:
            logger.error(f"구글 드라이브 폴더 작업 실패: {e}")
            raise
        except Exception as e:
            logger.error(f"폴더 찾기/생성 실패: {e}")
            raise
    
    def create_local_doc_file(self, title: str, content: str, playlist_name: str, custom_path: str = None, file_format: str = 'txt') -> str:
        """로컬에 문서 파일 생성"""
        try:
            logger.debug(f"📄 로컬 문서 파일 생성 시작: '{title[:30]}...'")
            
            # 1. 출력 폴더 결정
            if custom_path:
                docs_output_dir = Path(custom_path) / playlist_name
            else:
                script_dir = Path(__file__).parent
                docs_output_dir = script_dir / "output_docs" / playlist_name
            
            docs_output_dir.mkdir(parents=True, exist_ok=True)
            
            # 2. 파일명 정리 (Windows 파일명 규칙)
            safe_filename = "".join(c for c in title if c.isalnum() or c in (' ', '-', '_')).rstrip()
            safe_filename = safe_filename[:100]  # 파일명 길이 제한
            
            # 3. 파일 확장자 결정 (로컬에서는 모두 TXT로 저장)
            file_extension = '.txt'
            
            file_path = docs_output_dir / f"{safe_filename}{file_extension}"
            
            # 파일명 중복 처리
            counter = 1
            while file_path.exists():
                file_path = docs_output_dir / f"{safe_filename}_{counter}{file_extension}"
                counter += 1
            
            # 4. 내용 작성
            with open(file_path, 'w', encoding='utf-8') as f:
                f.write(f"제목: {title}\n")
                f.write(f"재생목록: {playlist_name}\n")
                f.write(f"생성일시: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
                f.write("="*50 + "\n\n")
                f.write(content)
            
            logger.info(f"📄 로컬 문서 파일 생성 완료: {file_path}")
            return str(file_path)
            
        except Exception as e:
            logger.error(f"로컬 문서 파일 생성 실패: {e}")
            raise
    
    def create_drive_txt_file(self, title: str, content: str, folder_id: str, metadata: dict = None) -> str:
        """Google Drive에 TXT 파일 생성"""
        try:
            logger.debug(f"📄 Google Drive TXT 파일 생성 시작: '{title[:30]}...'")
            
            # 1. 파일 내용 준비 (메타데이터 포함)
            file_content = f"제목: {title}\n"
            
            # 메타데이터 추가
            if metadata:
                if metadata.get('channel_name'):
                    file_content += f"채널명: {metadata['channel_name']}\n"
                if metadata.get('upload_date'):
                    file_content += f"업로드 날짜: {metadata['upload_date']}\n"
                if metadata.get('view_count'):
                    file_content += f"조회수: {metadata['view_count']}\n"
            
            file_content += f"생성일시: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
            file_content += "="*50 + "\n\n"
            
            # 대본 내용 줄바꿈 제거 후 추가
            processed_content = content.replace('\n', ' ').replace('\r', ' ')
            # 연속된 공백을 하나로 통합
            processed_content = ' '.join(processed_content.split())
            file_content += processed_content
            
            # 2. 파일명 정리
            safe_filename = "".join(c for c in title if c.isalnum() or c in (' ', '-', '_')).rstrip()
            safe_filename = safe_filename[:100]  # 파일명 길이 제한
            
            # 3. Drive API로 TXT 파일 생성
            file_metadata = {
                'name': f"{safe_filename}.txt",
                'mimeType': 'text/plain'
            }
            
            if folder_id:
                file_metadata['parents'] = [folder_id]
            
            # 4. 파일 업로드
            from googleapiclient.http import MediaInMemoryUpload
            
            media = MediaInMemoryUpload(
                file_content.encode('utf-8'), 
                mimetype='text/plain'
            )
            
            file = self.drive_service.files().create(
                body=file_metadata,
                media_body=media
            ).execute()
            
            file_id = file.get('id')
            logger.info(f"📄 Google Drive TXT 파일 생성 완료: '{title}' (ID: {file_id})")
            return file_id
            
        except HttpError as e:
            logger.error(f"Google Drive TXT 파일 생성 실패: {e}")
            raise
        except Exception as e:
            logger.error(f"TXT 파일 생성 실패: {e}")
            raise
    
    def create_google_doc(self, title: str, content: str, folder_id: str, video_id: str = None, metadata: dict = None, thumbnail_info: dict = None) -> str:
        """Google Drive에 Google Docs 문서 생성 (OAuth 사용)"""
        try:
            logger.debug(f"📄 Google Docs 문서 생성 시작: '{title[:30]}...'")
            
            # 1. 파일명 정리
            safe_filename = "".join(c for c in title if c.isalnum() or c in (' ', '-', '_')).rstrip()
            safe_filename = safe_filename[:100]  # 파일명 길이 제한
            
            # 2. Drive API로 Google Docs 문서 생성
            file_metadata = {
                'name': safe_filename,
                'mimeType': 'application/vnd.google-apps.document'
            }
            
            if folder_id:
                file_metadata['parents'] = [folder_id]
            
            doc = self.drive_service.files().create(body=file_metadata).execute()
            doc_id = doc.get('id')
            
            # 3. 문서에 내용 추가 (Docs API 사용)
            try:
                requests = []
                current_index = 1
                
                # 드라이브에 저장된 썸네일 이미지 삽입 (성공적으로 저장된 경우만)
                if thumbnail_info and thumbnail_info.get('success') and thumbnail_info.get('thumbnail_id'):
                    # 드라이브 파일을 공개 설정으로 변경하여 Google Docs에서 접근 가능하게 만들기
                    try:
                        # 파일을 링크를 아는 사람들이 볼 수 있도록 설정
                        permission = {
                            'type': 'anyone',
                            'role': 'reader'
                        }
                        self.drive_service.permissions().create(
                            fileId=thumbnail_info['thumbnail_id'],
                            body=permission
                        ).execute()
                        
                        # 공개 설정된 이미지 URL 사용
                        public_image_url = f"https://drive.google.com/uc?id={thumbnail_info['thumbnail_id']}"
                        
                        requests.append({
                            'insertInlineImage': {
                                'location': {
                                    'index': current_index
                                },
                                'uri': public_image_url,
                                'objectSize': {
                                    'height': {
                                        'magnitude': 200,
                                        'unit': 'PT'
                                    },
                                    'width': {
                                        'magnitude': 356,
                                        'unit': 'PT'
                                    }
                                }
                            }
                        })
                        current_index += 1
                        
                        # 썸네일 후 줄바꿈 추가
                        requests.append({
                            'insertText': {
                                'location': {
                                    'index': current_index
                                },
                                'text': '\n\n'
                            }
                        })
                        current_index += 2
                        
                        logger.debug(f"🖼️ 썸네일 이미지 Google Docs에 삽입 완료: {public_image_url}")
                        
                    except Exception as thumbnail_error:
                        logger.warning(f"⚠️ 썸네일 이미지 삽입 실패: {thumbnail_error}")
                        # 썸네일 삽입 실패는 전체 문서 생성을 중단시키지 않음
                
                # 메타데이터 추가
                metadata_text = f"제목: {title}\n"
                if metadata:
                    if metadata.get('channel_name'):
                        metadata_text += f"채널명: {metadata['channel_name']}\n"
                    if metadata.get('upload_date'):
                        metadata_text += f"업로드 날짜: {metadata['upload_date']}\n"
                    if metadata.get('view_count'):
                        metadata_text += f"조회수: {metadata['view_count']}\n"
                
                metadata_text += f"생성일시: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n{'='*50}\n\n"
                
                requests.append({
                    'insertText': {
                        'location': {
                            'index': current_index
                        },
                        'text': metadata_text
                    }
                })
                current_index += len(metadata_text)
                
                # 대본 내용 줄바꿈 제거 후 추가
                processed_content = content.replace('\n', ' ').replace('\r', ' ')
                # 연속된 공백을 하나로 통합
                processed_content = ' '.join(processed_content.split())
                
                requests.append({
                    'insertText': {
                        'location': {
                            'index': current_index
                        },
                        'text': processed_content
                    }
                })
                
                self.docs_service.documents().batchUpdate(
                    documentId=doc_id, 
                    body={'requests': requests}
                ).execute()
                logger.debug(f"📝 문서 내용 추가 완료")
                
            except Exception as content_error:
                # 내용 추가 실패해도 빈 문서라도 생성은 성공
                logger.warning(f"⚠️ 문서 내용 추가 실패 (빈 문서로 생성됨): {content_error}")
            
            logger.info(f"📄 Google Docs 문서 생성 완료: '{title}' (ID: {doc_id})")
            return doc_id
            
        except HttpError as e:
            logger.error(f"Google Docs 문서 생성 실패: {e}")
            raise
        except Exception as e:
            logger.error(f"Docs 문서 생성 실패: {e}")
            raise
    
    def find_existing_file(self, filename: str, folder_id: str, mime_type: str = None):
        """기존 파일 검색"""
        try:
            # 파일명의 작은따옴표를 이스케이프 처리
            safe_filename = filename.replace("'", "\\'")
            query = f"name='{safe_filename}' and '{folder_id}' in parents and trashed=false"
            if mime_type:
                query += f" and mimeType='{mime_type}'"
            
            logger.debug(f"📂 파일 검색 쿼리: {query}")
            
            results = self.drive_service.files().list(
                q=query,
                fields='files(id, name, mimeType)'
            ).execute()
            
            files = results.get('files', [])
            logger.debug(f"📂 검색 결과: {len(files)}개 파일 발견")
            
            if files:
                found_file = files[0]
                logger.info(f"📁 중복 파일 발견: '{found_file['name']}' (ID: {found_file['id']})")
                return found_file
            else:
                logger.debug(f"📂 '{filename}' 파일을 찾지 못함")
                return None
            
        except Exception as e:
            logger.warning(f"기존 파일 검색 실패: {e}")
            return None
    
    def create_drive_documents_for_long_transcript(self, video_id: str, title: str, transcript_text: str, playlist_name: str = None, metadata: dict = None, sheet_name: str = None) -> tuple:
        """긴 대본을 위한 드라이브 문서 생성 (Docs + TXT)"""
        try:
            # OAuth 인증 확인
            if not self.oauth_credentials or not self.oauth_credentials.valid:
                self.authenticate_oauth()
            
            # 재생목록별 대본 폴더 ID
            base_folder_id = "1qJy-LUcKdnTw0wgMOj-o26wsgb-ZY8oA"
            
            # 시트 이름에 '영상 리스트'가 포함되어 있고 채널명이 있는 경우 채널별 폴더 구조 적용
            # 40,000자 이상의 긴 대본도 일반 닥스처럼 채널명 폴더 내부에 저장
            if sheet_name and '영상 리스트' in sheet_name and metadata and metadata.get('channel_name'):
                channel_name = metadata['channel_name']
                logger.debug(f"📁 채널별 폴더 '{channel_name}' 찾기/생성")
                channel_folder_id = self.find_or_create_folder(channel_name, base_folder_id)
                
                # 채널 폴더 내에 하위 폴더들 생성 (txt, 썸네일만)
                txt_folder_id = self.find_or_create_folder("txt", channel_folder_id)
                thumbnail_folder_id = self.find_or_create_folder("썸네일", channel_folder_id)
                
                playlist_folder_id = channel_folder_id  # 닥스 파일은 채널 폴더에 직접 저장
            else:
                # 재생목록 폴더 이름 결정 (playlist_name이 없으면 기본값 사용)
                if not playlist_name:
                    playlist_name = "긴대본_기타"  # 재생목록 정보가 없는 경우의 기본값
                
                # 기존 로직 (재생목록명으로 폴더 생성)
                logger.debug(f"📁 재생목록 폴더 '{playlist_name}' 찾기/생성")
                playlist_folder_id = self.find_or_create_folder(playlist_name, base_folder_id)
                # 기본 txt 하위 폴더
                txt_folder_id = self.find_or_create_folder("txt", playlist_folder_id)
            
            # 안전한 파일명 생성
            safe_title = "".join(c for c in title if c.isalnum() or c in (' ', '-', '_')).rstrip()
            safe_title = safe_title[:100] if safe_title else video_id  # 파일명 길이 제한
            
            logger.info(f"📝 {video_id}: 원본 제목 '{title}' → 안전한 파일명 '{safe_title}'")
            
            # 썸네일 이미지 다운로드 및 저장 (채널별 폴더 구조인 경우 썸네일 폴더 사용)
            if sheet_name and '영상 리스트' in sheet_name and metadata and metadata.get('channel_name'):
                thumbnail_info = self.download_and_save_thumbnail(video_id, title, thumbnail_folder_id)
            else:
                thumbnail_info = self.download_and_save_thumbnail(video_id, title, playlist_folder_id)
            
            # 중복 파일 확인 (Google Docs)
            logger.debug(f"📝 기존 Google Docs 확인: {safe_title}")
            existing_docs = self.find_existing_file(safe_title, playlist_folder_id, 'application/vnd.google-apps.document')
            
            if existing_docs:
                docs_id = existing_docs['id']
                docs_url = f"https://docs.google.com/document/d/{docs_id}/edit"
                logger.info(f"📁 기존 Google Docs 재사용: '{safe_title}' (ID: {docs_id}) - 새 업로드 없이 시트 업데이트만 진행")
            else:
                # Google Docs 문서 생성 (재생목록 폴더에 직접 저장)
                logger.debug(f"📝 새 Google Docs 생성: {safe_title}")
                docs_id = self.create_google_doc(
                    title=f"{safe_title}",
                    content=transcript_text,
                    folder_id=playlist_folder_id,
                    video_id=video_id,
                    metadata=metadata,
                    thumbnail_info=thumbnail_info
                )
                docs_url = f"https://docs.google.com/document/d/{docs_id}/edit"
            
            # 중복 파일 확인 (TXT) - .txt 확장자 포함하여 검색
            txt_filename_with_ext = f"{safe_title}.txt"
            logger.debug(f"📄 기존 TXT 파일 확인: {txt_filename_with_ext}")
            existing_txt = self.find_existing_file(txt_filename_with_ext, txt_folder_id, 'text/plain')
            
            if existing_txt:
                txt_id = existing_txt['id']
                txt_url = f"https://drive.google.com/file/d/{txt_id}/view"
                logger.info(f"📁 기존 TXT 파일 재사용: '{txt_filename_with_ext}' (ID: {txt_id}) - 새 업로드 없이 시트 업데이트만 진행")
            else:
                # TXT 파일 생성
                logger.debug(f"📄 새 TXT 파일 생성: {safe_title}")
                txt_id = self.create_drive_txt_file(
                    title=f"{safe_title}",
                    content=transcript_text,
                    folder_id=txt_folder_id,
                    metadata=metadata
                )
                txt_url = f"https://drive.google.com/file/d/{txt_id}/view"
            
            docs_info = {'id': docs_id, 'url': docs_url}
            txt_info = {'id': txt_id, 'url': txt_url}
            
            logger.info(f"✅ 긴 대본 드라이브 저장 완료 - Docs: {docs_id}, TXT: {txt_id}")
            return docs_info, txt_info, thumbnail_info
            
        except Exception as e:
            logger.error(f"긴 대본 드라이브 저장 실패: {e}")
            raise
    
    def get_video_metadata_from_sheet(self, sheet_url: str, sheet_name: str, row: int, video_id: str) -> dict:
        """시트에서 비디오 메타데이터 추출"""
        try:
            workbook = self.client.open_by_url(sheet_url)
            sheet = workbook.worksheet(sheet_name)
            
            # 필요한 헤더 열 찾기
            channel_col = self.find_column_by_keyword(sheet, '채널명')
            views_col = self.find_column_by_keyword(sheet, '조회수')
            upload_date_col = self.find_column_by_keyword(sheet, '영상 업로드날짜')
            
            metadata = {}
            
            # 각 컬럼에서 데이터 추출
            if channel_col:
                try:
                    channel_value = sheet.cell(row, channel_col).value
                    if channel_value:
                        metadata['channel_name'] = str(channel_value).strip()
                        logger.debug(f"채널명 추출: {metadata['channel_name']}")
                except Exception as e:
                    logger.warning(f"채널명 추출 실패 (row {row}, col {channel_col}): {e}")
            
            if views_col:
                try:
                    views_value = sheet.cell(row, views_col).value
                    if views_value:
                        metadata['view_count'] = str(views_value).strip()
                        logger.debug(f"조회수 추출: {metadata['view_count']}")
                except Exception as e:
                    logger.warning(f"조회수 추출 실패 (row {row}, col {views_col}): {e}")
            
            if upload_date_col:
                try:
                    upload_date_value = sheet.cell(row, upload_date_col).value
                    if upload_date_value:
                        metadata['upload_date'] = str(upload_date_value).strip()
                        logger.debug(f"업로드 날짜 추출: {metadata['upload_date']}")
                except Exception as e:
                    logger.warning(f"업로드 날짜 추출 실패 (row {row}, col {upload_date_col}): {e}")
            
            logger.info(f"🔍 {video_id}: 메타데이터 추출 완료 - 채널명: {metadata.get('channel_name', 'N/A')}, 조회수: {metadata.get('view_count', 'N/A')}, 업로드일: {metadata.get('upload_date', 'N/A')}")
            return metadata
            
        except Exception as e:
            logger.error(f"메타데이터 추출 실패 ({video_id}): {e}")
            return {}
    
    def get_youtube_thumbnail_url(self, video_id: str) -> str:
        """YouTube 썸네일 URL 생성 (최고 화질)"""
        return f"https://img.youtube.com/vi/{video_id}/maxresdefault.jpg"
    
    def update_existing_docs_with_thumbnail(self, sheet_url: str, sheet_name: str, data: dict, playlist_folder_id: str, thumbnail_info: dict) -> bool:
        """기존 Google Docs 파일에 썸네일 이미지 추가"""
        try:
            # 시트에서 Docs ID 추출
            workbook = self.client.open_by_url(sheet_url)
            sheet = workbook.worksheet(sheet_name)
            
            docs_id_col = self.find_column_by_keyword(sheet, '닥스파일 ID')
            docs_path_col = self.find_column_by_keyword(sheet, '닥스파일 경로')
            
            docs_id = None
            
            # Docs ID로 찾기
            if docs_id_col:
                docs_id_value = sheet.cell(data['row_number'], docs_id_col).value
                if docs_id_value and docs_id_value.strip():
                    docs_id = docs_id_value.strip()
                    logger.debug(f"📝 Docs ID 추출: {docs_id}")
            
            # Docs ID가 없으면 경로에서 추출
            if not docs_id and docs_path_col:
                docs_path_value = sheet.cell(data['row_number'], docs_path_col).value
                if docs_path_value and docs_path_value.strip():
                    import re
                    # Google Docs URL에서 ID 추출
                    path_match = re.search(r'/document/d/([a-zA-Z0-9-_]+)', docs_path_value)
                    if path_match:
                        docs_id = path_match.group(1)
                        logger.debug(f"📝 Docs ID 경로에서 추출: {docs_id}")
            
            if not docs_id:
                logger.warning(f"⚠️ {data['video_id']}: Docs ID를 찾을 수 없습니다.")
                return False
            
            # 썸네일 이미지가 성공적으로 저장된 경우만 처리
            if not thumbnail_info.get('success'):
                logger.warning(f"⚠️ {data['video_id']}: 썸네일 이미지가 성공적으로 저장되지 않았습니다.")
                return False
            
            # Google Docs에 썸네일 이미지 삽입
            try:
                # 공개 권한 설정
                permission = {
                    'type': 'anyone',
                    'role': 'reader'
                }
                self.drive_service.permissions().create(
                    fileId=thumbnail_info['thumbnail_id'],
                    body=permission
                ).execute()
                
                # 공개 이미지 URL
                public_image_url = f"https://drive.google.com/uc?id={thumbnail_info['thumbnail_id']}"
                
                # 썸네일 이미지를 문서 상단에 삽입
                requests = [
                    {
                        'insertInlineImage': {
                            'location': {
                                'index': 1  # 문서 상단에 삽입
                            },
                            'uri': public_image_url,
                            'objectSize': {
                                'height': {
                                    'magnitude': 200,
                                    'unit': 'PT'
                                },
                                'width': {
                                    'magnitude': 356,
                                    'unit': 'PT'
                                }
                            }
                        }
                    },
                    {
                        'insertText': {
                            'location': {
                                'index': 2  # 이미지 후에 줄바꿈 추가
                            },
                            'text': '\n\n'
                        }
                    }
                ]
                
                self.docs_service.documents().batchUpdate(
                    documentId=docs_id, 
                    body={'requests': requests}
                ).execute()
                
                logger.info(f"✅ {data['video_id']}: 기존 Docs에 썸네일 이미지 추가 완료")
                return True
                
            except Exception as docs_error:
                logger.error(f"❌ {data['video_id']}: Docs 썸네일 삽입 실패 - {docs_error}")
                return False
                
        except Exception as e:
            logger.error(f"❌ {data['video_id']}: 기존 Docs 업데이트 실패 - {e}")
            return False
    
    def download_and_save_thumbnail(self, video_id: str, title: str, playlist_folder_id: str) -> dict:
        """썸네일 이미지 다운로드 및 드라이브 저장"""
        try:
            import requests
            from googleapiclient.http import MediaInMemoryUpload
            
            # 썸네일 폴더 찾기/생성
            thumbnail_folder_id = self.find_or_create_folder("썸네일", playlist_folder_id)
            
            # 안전한 파일명 생성
            safe_title = "".join(c for c in title if c.isalnum() or c in (' ', '-', '_')).rstrip()
            safe_title = safe_title[:100] if safe_title else video_id
            thumbnail_filename = f"{safe_title}.jpg"
            
            # 중복 파일 확인
            existing_thumbnail = self.find_existing_file(thumbnail_filename, thumbnail_folder_id, 'image/jpeg')
            if existing_thumbnail:
                thumbnail_id = existing_thumbnail['id']
                thumbnail_url = f"https://drive.google.com/file/d/{thumbnail_id}/view"
                logger.info(f"🖼️ 기존 썸네일 재사용: '{thumbnail_filename}' (ID: {thumbnail_id})")
                
                return {
                    'success': True,
                    'thumbnail_id': thumbnail_id,
                    'thumbnail_url': thumbnail_url,
                    'youtube_thumbnail_url': self.get_youtube_thumbnail_url(video_id),
                    'filename': thumbnail_filename
                }
            
            # YouTube에서 썸네일 다운로드
            youtube_thumbnail_url = self.get_youtube_thumbnail_url(video_id)
            logger.debug(f"🖼️ 썸네일 다운로드 시작: {youtube_thumbnail_url}")
            
            response = requests.get(youtube_thumbnail_url, timeout=30)
            response.raise_for_status()
            
            if response.status_code == 200 and len(response.content) > 1000:  # 유효한 이미지 확인
                # 드라이브에 썸네일 업로드
                file_metadata = {
                    'name': thumbnail_filename,
                    'parents': [thumbnail_folder_id],
                    'mimeType': 'image/jpeg'
                }
                
                media = MediaInMemoryUpload(
                    response.content,
                    mimetype='image/jpeg'
                )
                
                thumbnail_file = self.drive_service.files().create(
                    body=file_metadata,
                    media_body=media
                ).execute()
                
                thumbnail_id = thumbnail_file.get('id')
                thumbnail_url = f"https://drive.google.com/file/d/{thumbnail_id}/view"
                
                logger.info(f"✅ {video_id}: 썸네일 이미지 저장 성공 - '{thumbnail_filename}' (ID: {thumbnail_id})")
                
                return {
                    'success': True,
                    'thumbnail_id': thumbnail_id,
                    'thumbnail_url': thumbnail_url,
                    'youtube_thumbnail_url': youtube_thumbnail_url,
                    'filename': thumbnail_filename
                }
            else:
                logger.warning(f"⚠️ {video_id}: 썸네일 이미지 다운로드 실패 - 잘못된 이미지 데이터")
                return {
                    'success': False,
                    'error': '잘못된 이미지 데이터',
                    'youtube_thumbnail_url': youtube_thumbnail_url
                }
                
        except requests.exceptions.RequestException as e:
            logger.error(f"❌ {video_id}: 썸네일 다운로드 실패 - 네트워크 오류: {e}")
            return {
                'success': False,
                'error': f'네트워크 오류: {str(e)}',
                'youtube_thumbnail_url': self.get_youtube_thumbnail_url(video_id)
            }
        except Exception as e:
            logger.error(f"❌ {video_id}: 썸네일 저장 실패 - {e}")
            return {
                'success': False,
                'error': str(e),
                'youtube_thumbnail_url': self.get_youtube_thumbnail_url(video_id)
            }
    
    def update_all_transcript_headers(self, sheet, video_data_list: List, row_mapping: Optional[List[int]], start_row: int):
        """모든 대본에 대한 헤더 업데이트 (긴 대본 + 일반 대본의 썸네일)"""
        try:
            # 필요한 열 찾기
            script_exists_col = self.find_column_by_keyword(sheet, '대본유무')
            docs_status_col = self.find_column_by_keyword(sheet, '구글닥스여부')
            docs_file_id_col = self.find_column_by_keyword(sheet, '닥스파일ID')
            docs_file_path_col = self.find_column_by_keyword(sheet, '닥스파일경로')
            txt_status_col = self.find_column_by_keyword(sheet, '대본txt여부')
            txt_file_id_col = self.find_column_by_keyword(sheet, '대본txtID')
            txt_file_path_col = self.find_column_by_keyword(sheet, '대본txt경로')
            thumbnail_status_col = self.find_column_by_keyword(sheet, '썸네일여부')
            thumbnail_image_col = self.find_column_by_keyword(sheet, '이미지주소')
            thumbnail_path_col = self.find_column_by_keyword(sheet, '경로')

            def column_number_to_letter(col_num):
                result = ""
                while col_num > 0:
                    col_num -= 1
                    result = chr(col_num % 26 + ord('A')) + result
                    col_num //= 26
                return result

            additional_updates = []

            # 각 비디오 데이터에 대해 처리
            for i, video_data in enumerate(video_data_list):
                # 해당 행 번호 결정
                if row_mapping and i < len(row_mapping):
                    row = row_mapping[i]
                else:
                    row = start_row + i

                # 긴 대본 정보 (기존 로직)
                docs_info = getattr(video_data, '_drive_docs_info', {})
                txt_info = getattr(video_data, '_drive_txt_info', {})
                long_thumbnail_info = getattr(video_data, '_drive_thumbnail_info', {})
                
                # 일반 대본의 썸네일 정보 (새 로직)
                normal_thumbnail_info = getattr(video_data, '_thumbnail_info', {})
                
                # 썸네일 정보 우선순위: 긴 대본용 > 일반 대본용
                thumbnail_info = long_thumbnail_info if long_thumbnail_info else normal_thumbnail_info

                # 대본유무 열은 전역함수이므로 업데이트하지 않음
                # (전역함수가 자동으로 처리)

                # 긴 대본에 대한 Docs/TXT 헤더 업데이트
                if docs_info:  # 긴 대본인 경우
                    # Docs 관련 헤더 업데이트
                    if docs_status_col:
                        col_letter = column_number_to_letter(docs_status_col)
                        additional_updates.append({
                            'range': f'{col_letter}{row}',
                            'values': [['ㅇ']]
                        })

                    if docs_file_id_col and docs_info.get('docs_id'):
                        col_letter = column_number_to_letter(docs_file_id_col)
                        additional_updates.append({
                            'range': f'{col_letter}{row}',
                            'values': [[docs_info['docs_id']]]
                        })

                    if docs_file_path_col and docs_info.get('docs_url'):
                        col_letter = column_number_to_letter(docs_file_path_col)
                        additional_updates.append({
                            'range': f'{col_letter}{row}',
                            'values': [[docs_info['docs_url']]]
                        })

                    # TXT 관련 헤더 업데이트
                    if txt_info:
                        if txt_status_col:
                            col_letter = column_number_to_letter(txt_status_col)
                            additional_updates.append({
                                'range': f'{col_letter}{row}',
                                'values': [['ㅇ']]
                            })

                        if txt_file_id_col and txt_info.get('txt_id'):
                            col_letter = column_number_to_letter(txt_file_id_col)
                            additional_updates.append({
                                'range': f'{col_letter}{row}',
                                'values': [[txt_info['txt_id']]]
                            })

                        if txt_file_path_col and txt_info.get('txt_url'):
                            col_letter = column_number_to_letter(txt_file_path_col)
                            additional_updates.append({
                                'range': f'{col_letter}{row}',
                                'values': [[txt_info['txt_url']]]
                            })

                # 썸네일 헤더 업데이트 (성공한 경우만)
                if thumbnail_info and thumbnail_info.get('success'):
                    if thumbnail_status_col:
                        col_letter = column_number_to_letter(thumbnail_status_col)
                        additional_updates.append({
                            'range': f'{col_letter}{row}',
                            'values': [['ㅇ']]
                        })

                    if thumbnail_image_col and thumbnail_info.get('youtube_thumbnail_url'):
                        col_letter = column_number_to_letter(thumbnail_image_col)
                        additional_updates.append({
                            'range': f'{col_letter}{row}',
                            'values': [[thumbnail_info['youtube_thumbnail_url']]]
                        })

                    if thumbnail_path_col and thumbnail_info.get('thumbnail_url'):
                        col_letter = column_number_to_letter(thumbnail_path_col)
                        additional_updates.append({
                            'range': f'{col_letter}{row}',
                            'values': [[thumbnail_info['thumbnail_url']]]
                        })
                # 썸네일 실패 시에는 헤더를 업데이트하지 않음 (기존 값 유지)

                logger.debug(f"📝 {video_data.video_id}: {row}행 헤더 업데이트 준비 (썸네일 포함)")

            # 추가 헤더 업데이트 실행 (전역함수 보호 적용)
            if additional_updates:
                logger.info(f"📝 모든 대본 관련 헤더 업데이트: {len(additional_updates)}개 셀")

                # 헤더 정보 가져오기
                headers = sheet.row_values(1)

                # 벌크 업데이트 + 전역함수 열 자동 보호
                bulk_update_with_formula_protection(
                    sheet=sheet,
                    updates=additional_updates,
                    headers=headers,
                    sheet_type=SheetType.VIDEO_LIST
                )
                logger.info(f"✅ 모든 대본 관련 헤더 업데이트 완료 (전역함수 보호 적용)")

        except Exception as e:
            logger.error(f"❌ 모든 대본 관련 헤더 업데이트 실패: {e}")
            # 헤더 업데이트 실패는 치명적이지 않으므로 예외를 다시 던지지 않음

    def update_additional_headers_for_long_transcripts(self, sheet, video_data_list: List, row_mapping: Optional[List[int]], start_row: int):
        """긴 대본으로 드라이브 저장된 경우 추가 헤더 업데이트"""
        try:
            # 필요한 헤더 열 찾기
            script_exists_col = self.find_column_by_keyword(sheet, '대본유무')
            docs_status_col = self.find_column_by_keyword(sheet, '구글 닥스 여부') or self.find_column_by_keyword(sheet, '구글닥스 여부')
            docs_id_col = self.find_column_by_keyword(sheet, '닥스파일 ID')
            docs_path_col = self.find_column_by_keyword(sheet, '닥스파일 경로')
            txt_status_col = self.find_column_by_keyword(sheet, '대본txt 여부') or self.find_column_by_keyword(sheet, '대본 txt 여부')
            txt_id_col = self.find_column_by_keyword(sheet, '대본txt ID')
            txt_path_col = self.find_column_by_keyword(sheet, '대본txt 경로')
            
            # 썸네일 관련 열
            thumbnail_status_col = self.find_column_by_keyword(sheet, '썸네일 여부')
            thumbnail_image_col = self.find_column_by_keyword(sheet, '썸네일 이미지주소')
            thumbnail_path_col = self.find_column_by_keyword(sheet, '썸네일 경로')
            
            # 열 번호를 문자로 변환 함수
            def column_number_to_letter(col_num):
                result = ""
                while col_num > 0:
                    col_num -= 1
                    result = chr(col_num % 26 + ord('A')) + result
                    col_num //= 26
                return result
            
            additional_updates = []
            
            for i, video_data in enumerate(video_data_list):
                # 긴 대본으로 드라이브에 저장된 경우만 처리
                if not getattr(video_data, '_is_long_transcript', False):
                    continue
                
                # 해당 행 번호 결정
                if row_mapping and i < len(row_mapping):
                    row = row_mapping[i]
                else:
                    row = start_row + i
                
                docs_info = getattr(video_data, '_drive_docs_info', {})
                txt_info = getattr(video_data, '_drive_txt_info', {})
                thumbnail_info = getattr(video_data, '_drive_thumbnail_info', {})
                
                # 대본유무 열 업데이트 (ㅇ)
                if script_exists_col:
                    col_letter = column_number_to_letter(script_exists_col)
                    additional_updates.append({
                        'range': f'{col_letter}{row}',
                        'values': [['ㅇ']]
                    })
                
                # Docs 관련 헤더 업데이트
                if docs_status_col:
                    col_letter = column_number_to_letter(docs_status_col)
                    additional_updates.append({
                        'range': f'{col_letter}{row}',
                        'values': [['ㅇ']]
                    })
                
                if docs_id_col and docs_info.get('id'):
                    col_letter = column_number_to_letter(docs_id_col)
                    additional_updates.append({
                        'range': f'{col_letter}{row}',
                        'values': [[docs_info['id']]]
                    })
                
                if docs_path_col and docs_info.get('url'):
                    col_letter = column_number_to_letter(docs_path_col)
                    additional_updates.append({
                        'range': f'{col_letter}{row}',
                        'values': [[docs_info['url']]]
                    })
                
                # TXT 관련 헤더 업데이트
                if txt_status_col:
                    col_letter = column_number_to_letter(txt_status_col)
                    additional_updates.append({
                        'range': f'{col_letter}{row}',
                        'values': [['ㅇ']]
                    })
                
                if txt_id_col and txt_info.get('id'):
                    col_letter = column_number_to_letter(txt_id_col)
                    additional_updates.append({
                        'range': f'{col_letter}{row}',
                        'values': [[txt_info['id']]]
                    })
                
                if txt_path_col and txt_info.get('url'):
                    col_letter = column_number_to_letter(txt_path_col)
                    additional_updates.append({
                        'range': f'{col_letter}{row}',
                        'values': [[txt_info['url']]]
                    })
                
                # 썸네일 관련 헤더 업데이트 (성공한 경우만)
                if thumbnail_info and thumbnail_info.get('success'):
                    if thumbnail_status_col:
                        col_letter = column_number_to_letter(thumbnail_status_col)
                        additional_updates.append({
                            'range': f'{col_letter}{row}',
                            'values': [['ㅇ']]
                        })
                    
                    if thumbnail_image_col and thumbnail_info.get('youtube_thumbnail_url'):
                        col_letter = column_number_to_letter(thumbnail_image_col)
                        additional_updates.append({
                            'range': f'{col_letter}{row}',
                            'values': [[thumbnail_info['youtube_thumbnail_url']]]
                        })
                    
                    if thumbnail_path_col and thumbnail_info.get('thumbnail_url'):
                        col_letter = column_number_to_letter(thumbnail_path_col)
                        additional_updates.append({
                            'range': f'{col_letter}{row}',
                            'values': [[thumbnail_info['thumbnail_url']]]
                        })
                # 썸네일 실패 시에는 헤더를 업데이트하지 않음 (기존 값 유지)
                
                logger.debug(f"📝 {video_data.video_id}: {row}행 추가 헤더 업데이트 준비 (썸네일 포함)")
            
            # 추가 헤더 업데이트 실행 (전역함수 보호 적용)
            if additional_updates:
                logger.info(f"📝 긴 대본 관련 추가 헤더 업데이트: {len(additional_updates)}개 셀")

                # 헤더 정보 가져오기
                headers = sheet.row_values(1)

                # 벌크 업데이트 + 전역함수 열 자동 보호
                bulk_update_with_formula_protection(
                    sheet=sheet,
                    updates=additional_updates,
                    headers=headers,
                    sheet_type=SheetType.VIDEO_LIST
                )
                logger.info(f"✅ 긴 대본 관련 헤더 업데이트 완료 (전역함수 보호 적용)")
                
        except Exception as e:
            logger.error(f"❌ 긴 대본 관련 헤더 업데이트 실패: {e}")
            # 헤더 업데이트 실패는 치명적이지 않으므로 예외를 다시 던지지 않음
    
    def handle_long_transcript_for_browser_automation(self, video_id: str, title: str, transcript_text: str, 
                                                    sheet_url: str, sheet_name: str, row: int, transcript_col: int) -> dict:
        """브라우저 자동화에서 긴 대본 처리용 메서드"""
        try:
            MAX_CELL_LENGTH = 50000  # Google Sheets의 실제 제한값
            
            if len(transcript_text) > MAX_CELL_LENGTH:
                logger.info(f"📄 {video_id}: 대본 길이 초과 ({len(transcript_text)}자) - 드라이브에 자동 저장")
                
                # 시트에서 재생목록 이름 추출
                playlist_name = self.get_playlist_name_from_sheet(sheet_url, sheet_name, row)
                
                # 시트에서 메타데이터 추출
                try:
                    metadata = self.get_video_metadata_from_sheet(sheet_url, sheet_name, row, video_id)
                except Exception as meta_error:
                    logger.warning(f"⚠️ {video_id}: 메타데이터 추출 실패 - {meta_error}")
                    metadata = {}
                
                # 드라이브에 문서 저장
                docs_info, txt_info, thumbnail_info = self.create_drive_documents_for_long_transcript(
                    video_id, title or video_id, transcript_text, playlist_name, metadata, sheet_name
                )
                
                # 시트의 관련 헤더들 업데이트
                self.update_headers_for_long_transcript_single_row(
                    sheet_url, sheet_name, row, docs_info, txt_info, thumbnail_info
                )
                
                logger.info(f"✅ {video_id}: 긴 대본 드라이브 저장 및 헤더 업데이트 완료")
                
                return {
                    'handled': True, 
                    'skip_cell_update': True,
                    'docs_info': docs_info,
                    'txt_info': txt_info,
                    'thumbnail_info': thumbnail_info
                }
            
            return {'handled': False}
            
        except Exception as e:
            logger.error(f"❌ {video_id}: 긴 대본 처리 실패 - {e}")
            return {'handled': False, 'error': str(e)}
    
    def update_headers_for_long_transcript_single_row(self, sheet_url: str, sheet_name: str, row: int, 
                                                     docs_info: dict, txt_info: dict, thumbnail_info: dict = None):
        """단일 행에 대한 긴 대본 관련 헤더 업데이트"""
        try:
            workbook = self.client.open_by_url(sheet_url)
            sheet = workbook.worksheet(sheet_name)
            
            # 필요한 헤더 열 찾기
            script_exists_col = self.find_column_by_keyword(sheet, '대본유무')
            docs_status_col = self.find_column_by_keyword(sheet, '구글 닥스 여부') or self.find_column_by_keyword(sheet, '구글닥스 여부')
            docs_id_col = self.find_column_by_keyword(sheet, '닥스파일 ID')
            docs_path_col = self.find_column_by_keyword(sheet, '닥스파일 경로')
            txt_status_col = self.find_column_by_keyword(sheet, '대본txt 여부') or self.find_column_by_keyword(sheet, '대본 txt 여부')
            txt_id_col = self.find_column_by_keyword(sheet, '대본txt ID')
            txt_path_col = self.find_column_by_keyword(sheet, '대본txt 경로')
            
            # 열 번호를 문자로 변환 함수
            def column_number_to_letter(col_num):
                result = ""
                while col_num > 0:
                    col_num -= 1
                    result = chr(col_num % 26 + ord('A')) + result
                    col_num //= 26
                return result
            
            updates = []
            
            # 대본유무 열 업데이트 (ㅇ)
            if script_exists_col:
                col_letter = column_number_to_letter(script_exists_col)
                updates.append({
                    'range': f'{col_letter}{row}',
                    'values': [['ㅇ']]
                })
            
            # Docs 관련 헤더 업데이트
            if docs_status_col:
                col_letter = column_number_to_letter(docs_status_col)
                updates.append({
                    'range': f'{col_letter}{row}',
                    'values': [['ㅇ']]
                })
            
            if docs_id_col and docs_info.get('id'):
                col_letter = column_number_to_letter(docs_id_col)
                updates.append({
                    'range': f'{col_letter}{row}',
                    'values': [[docs_info['id']]]
                })
            
            if docs_path_col and docs_info.get('url'):
                col_letter = column_number_to_letter(docs_path_col)
                updates.append({
                    'range': f'{col_letter}{row}',
                    'values': [[docs_info['url']]]
                })
            
            # TXT 관련 헤더 업데이트
            if txt_status_col:
                col_letter = column_number_to_letter(txt_status_col)
                updates.append({
                    'range': f'{col_letter}{row}',
                    'values': [['ㅇ']]
                })
            
            if txt_id_col and txt_info.get('id'):
                col_letter = column_number_to_letter(txt_id_col)
                updates.append({
                    'range': f'{col_letter}{row}',
                    'values': [[txt_info['id']]]
                })
            
            if txt_path_col and txt_info.get('url'):
                col_letter = column_number_to_letter(txt_path_col)
                updates.append({
                    'range': f'{col_letter}{row}',
                    'values': [[txt_info['url']]]
                })
            
            # 썸네일 관련 헤더 업데이트 (성공한 경우만)
            if thumbnail_info and thumbnail_info.get('success'):
                if thumbnail_status_col:
                    col_letter = column_number_to_letter(thumbnail_status_col)
                    updates.append({
                        'range': f'{col_letter}{row}',
                        'values': [['ㅇ']]
                    })
                
                if thumbnail_image_col and thumbnail_info.get('youtube_thumbnail_url'):
                    col_letter = column_number_to_letter(thumbnail_image_col)
                    updates.append({
                        'range': f'{col_letter}{row}',
                        'values': [[thumbnail_info['youtube_thumbnail_url']]]
                    })
                
                if thumbnail_path_col and thumbnail_info.get('thumbnail_url'):
                    col_letter = column_number_to_letter(thumbnail_path_col)
                    updates.append({
                        'range': f'{col_letter}{row}',
                        'values': [[thumbnail_info['thumbnail_url']]]
                    })
            # 썸네일 실패 시에는 헤더를 업데이트하지 않음 (기존 값 유지)
            
            # 헤더 업데이트 실행 (전역함수 보호 적용)
            if updates:
                logger.info(f"📝 {row}행 긴 대본 관련 헤더 업데이트: {len(updates)}개 셀")

                # 헤더 정보 가져오기
                headers = sheet.row_values(1)

                # 벌크 업데이트 + 전역함수 열 자동 보호
                bulk_update_with_formula_protection(
                    sheet=sheet,
                    updates=updates,
                    headers=headers,
                    sheet_type=SheetType.VIDEO_LIST
                )
                logger.info(f"✅ {row}행 긴 대본 관련 헤더 업데이트 완료 (전역함수 보호 적용)")
                
        except Exception as e:
            logger.error(f"❌ {row}행 긴 대본 관련 헤더 업데이트 실패: {e}")
            raise
    
    def check_and_handle_transcript_length(self, transcript_text: str, video_id: str, title: str, playlist_name: str = None, metadata: dict = None) -> dict:
        """대본 길이 확인 및 처리 (브라우저 자동화용)"""
        MAX_CELL_LENGTH = 50000
        
        if len(transcript_text) > MAX_CELL_LENGTH:
            logger.warning(f"⚠️ {video_id}: 대본 길이 초과 ({len(transcript_text)}자), Google Sheets 셀 제한 (50,000자) 초과")
            logger.info(f"🔄 {video_id}: 긴 대본을 구글 드라이브에 자동 저장합니다...")
            
            try:
                # 드라이브에 문서 저장
                docs_info, txt_info, thumbnail_info = self.create_drive_documents_for_long_transcript(
                    video_id, title or video_id, transcript_text, playlist_name, metadata, sheet_name
                )
                
                return {
                    'is_long': True,
                    'docs_info': docs_info,
                    'txt_info': txt_info,
                    'thumbnail_info': thumbnail_info,
                    'message': '길이 초과로 드라이브에 저장됨'
                }
            except Exception as e:
                logger.error(f"❌ {video_id}: 긴 대본 드라이브 저장 실패 - {e}")
                return {
                    'is_long': True,
                    'error': str(e),
                    'message': f'길이 초과, 드라이브 저장 실패: {str(e)}'
                }
        
        return {'is_long': False}
    
    def get_playlist_name_from_sheet(self, sheet_url: str, sheet_name: str, row: int) -> str:
        """시트의 특정 행에서 재생목록 이름 추출"""
        try:
            workbook = self.client.open_by_url(sheet_url)
            sheet = workbook.worksheet(sheet_name)
            
            # 재생목록 이름 열 찾기
            playlist_col = self.find_column_by_keyword(sheet, '재생목록 이름')
            if not playlist_col:
                logger.warning(f"⚠️ '재생목록 이름' 열을 찾을 수 없습니다. 기본값 사용")
                return "긴대본_기타"
            
            # 해당 행의 재생목록 이름 가져오기
            playlist_name = sheet.cell(row, playlist_col).value
            if not playlist_name or not playlist_name.strip():
                logger.warning(f"⚠️ {row}행의 재생목록 이름이 비어있습니다. 기본값 사용")
                return "긴대본_기타"
            
            logger.debug(f"📁 {row}행에서 재생목록 이름 추출: '{playlist_name.strip()}'")
            return playlist_name.strip()
            
        except Exception as e:
            logger.error(f"❌ {row}행 재생목록 이름 추출 실패: {e}")
            return "긴대본_기타"
    
    def process_docs_extraction(self, sheet_url: str, sheet_name: str, max_count: int = 10, stop_callback=None) -> Dict[str, int]:
        """구글 닥스 추출 전체 프로세스"""
        try:
            # 추출 모드 가져오기
            extraction_mode = getattr(self, '_extraction_mode', 'both')
            bulk_thumbnail = getattr(self, '_bulk_thumbnail', False)
            bulk_docs = getattr(self, '_bulk_docs', False)
            extract_to_end = getattr(self, '_extract_to_end', False)
            logger.info(f"🚀 구글 닥스 추출 프로세스 시작 (최대 {max_count}개, 모드: {extraction_mode}, 벌크 썸네일: {bulk_thumbnail}, 벌크 닥스: {bulk_docs})")
            
            # 1. 추출 데이터 수집
            extraction_data = self.get_docs_extraction_data(sheet_url, sheet_name, max_count, extraction_mode)
            
            if not extraction_data:
                logger.warning("조건에 맞는 데이터가 없습니다")
                return {'processed': 0, 'docs_success': 0, 'txt_success': 0, 'docs_error': 0, 'txt_error': 0}
            
            # 벌크 썸네일 처리 (shorts_thumbnail 모드이고 벌크 옵션이 활성화된 경우)
            if extraction_mode == "shorts_thumbnail" and bulk_thumbnail:
                return self.process_bulk_shorts_thumbnail(sheet_url, sheet_name, extraction_data, stop_callback)
            
            # 일반 벌크 처리 (벌크 닥스 옵션이 활성화된 경우)
            if bulk_docs:
                # 쇼츠인지 확인하여 배치 크기 결정
                is_shorts = '쇼츠' in sheet_name
                batch_size = 30 if is_shorts else 10
                logger.info(f"📦 벌크 닥스 모드 활성화: {len(extraction_data)}개 항목, 배치 크기 {batch_size}개 ({'쇼츠' if is_shorts else '일반'})")
                return self.process_bulk_docs_extraction(
                    sheet_url, sheet_name, extraction_data, batch_size, stop_callback
                )
            
            # 2. 각 데이터에 대해 1개씩 실시간으로 문서 생성
            docs_success_count = 0
            txt_success_count = 0
            docs_error_count = 0
            txt_error_count = 0
            thumbnail_results = []  # 썸네일 처리 결과 저장
            
            for idx, data in enumerate(extraction_data, 1):
                # ESC 키로 중단 확인
                if stop_callback and stop_callback():
                    if idx > 1:
                        first_row = extraction_data[0]['row_number']
                        last_completed_row = extraction_data[idx-2]['row_number']
                        logger.info(f"🛑 사용자 요청으로 작업 중단됨 ({idx-1}/{len(extraction_data)} 완료, 행 {first_row}-{last_completed_row})")
                    else:
                        logger.info(f"🛑 사용자 요청으로 작업 중단됨 (0/{len(extraction_data)} 완료)")
                    break
                
                # 재생목록 폴더 찾기/생성 (Google Drive용)
                save_location = getattr(self, '_save_location', 'drive')
                if save_location == 'drive':
                    # 폴더 ID 설정 (쇼츠 모드인 경우 쇼츠 폴더 사용)
                    if extraction_mode == "shorts_thumbnail":
                        # 쇼츠 모음 폴더 ID
                        base_folder_id = "1RwI2KSTAGfVrWugnSu8Ny5tlgls1XCox"
                        logger.info("📂 쇼츠용 썸네일 추출 모드: 쇼츠 모음 폴더 사용")
                    else:
                        # 재생목록별 대본 폴더 ID
                        base_folder_id = "1qJy-LUcKdnTw0wgMOj-o26wsgb-ZY8oA"
                    
                    # 메타데이터에서 채널명 추출 (시트 이름이 '영상 리스트'인 경우)
                    metadata = {}
                    if data.get('video_id'):
                        try:
                            metadata = self.get_video_metadata_from_sheet(sheet_url, sheet_name, data['row_number'], data['video_id'])
                        except Exception as meta_error:
                            logger.warning(f"⚠️ {data.get('video_id', 'Unknown')}: 메타데이터 추출 실패 - {meta_error}")
                    
                    # 시트 이름에 '영상 리스트'가 포함되어 있고 채널명이 있는 경우
                    if '영상 리스트' in sheet_name and metadata.get('channel_name'):
                        channel_name = metadata['channel_name']
                        logger.debug(f"📁 채널별 폴더 '{channel_name}' 찾기/생성")
                        channel_folder_id = self.find_or_create_folder(channel_name, base_folder_id)
                        
                        # 채널 폴더 내에 하위 폴더들 생성 (txt, 썸네일만)
                        txt_folder_id = self.find_or_create_folder("txt", channel_folder_id)  
                        thumbnail_folder_id = self.find_or_create_folder("썸네일", channel_folder_id)
                        
                        playlist_folder_id = channel_folder_id  # 닥스 파일은 채널 폴더에 직접 저장
                    else:
                        # 기존 로직 (재생목록명으로 폴더 생성)
                        logger.debug(f"📁 재생목록 폴더 '{data['playlist_name']}' 찾기/생성")
                        playlist_folder_id = self.find_or_create_folder(data['playlist_name'], base_folder_id)
                    
                    # 썸네일 다운로드 및 저장 (비디오 ID가 있는 경우만)
                    thumbnail_info = {'success': False}
                    if data.get('video_id'):
                        try:
                            thumbnail_info = self.download_and_save_thumbnail(data['video_id'], data['title'], playlist_folder_id)
                        except Exception as thumb_error:
                            logger.warning(f"⚠️ {data['video_id']}: 썸네일 다운로드 실패 - {thumb_error}")
                            thumbnail_info = {'success': False, 'error': str(thumb_error), 'youtube_thumbnail_url': self.get_youtube_thumbnail_url(data['video_id']) if data.get('video_id') else None}
                else:
                    metadata = {}
                    thumbnail_info = {'success': False}
                
                # 쇼츠용 썸네일만 추출 모드 처리
                if extraction_mode == "shorts_thumbnail":
                    try:
                        logger.info(f"🖼️ {idx}/{len(extraction_data)} (행{data['row_number']}): '{data['title'][:30]}...' 쇼츠용 썸네일만 다운로드 중...")
                        
                        # 썸네일만 다운로드하여 폴더에 저장 (Google Docs 생성하지 않음)
                        if thumbnail_info.get('success'):
                            # 시트 열기
                            workbook = self.client.open_by_url(sheet_url)
                            sheet = workbook.worksheet(sheet_name)
                            
                            # 썸네일 관련 열 찾기
                            thumbnail_status_col = self.find_column_by_keyword(sheet, '썸네일 여부')
                            thumbnail_path_col = self.find_column_by_keyword(sheet, '썸네일 경로')
                            thumbnail_image_col = self.find_column_by_keyword(sheet, '썸네일 이미지주소')
                            
                            updates = []
                            if thumbnail_status_col:
                                updates.append({'col': thumbnail_status_col, 'value': 'ㅇ'})
                            
                            # 썸네일 URL 업데이트 (일반 모드와 동일한 로직)
                            if thumbnail_image_col and thumbnail_info.get('youtube_thumbnail_url'):
                                updates.append({'col': thumbnail_image_col, 'value': thumbnail_info['youtube_thumbnail_url']})
                            
                            if thumbnail_path_col and thumbnail_info.get('drive_file_url'):
                                updates.append({'col': thumbnail_path_col, 'value': thumbnail_info['drive_file_url']})
                            
                            if updates:
                                self.update_sheet_multiple_columns(
                                    sheet_url=sheet_url,
                                    sheet_name=sheet_name,
                                    row_number=data['row_number'],
                                    updates=updates
                                )
                            
                            docs_success_count += 1
                            logger.info(f"✅ 쇼츠용 썸네일 다운로드 성공: '{data['title'][:30]}...'")
                        else:
                            docs_error_count += 1
                            logger.error(f"❌ 쇼츠용 썸네일 다운로드 실패: '{data['title'][:30]}...'")
                            
                    except Exception as e:
                        docs_error_count += 1
                        logger.error(f"❌ 쇼츠용 썸네일 모드 처리 실패: {e}")
                
                # 썸네일 모드 처리
                elif extraction_mode == "thumbnail":
                    try:
                        logger.info(f"🖼️ {idx}/{len(extraction_data)} (행{data['row_number']}): '{data['title'][:30]}...' 기존 Docs에 썸네일 추가 중...")
                        
                        # 기존 Docs에 썸네일 추가
                        thumbnail_update_success = self.update_existing_docs_with_thumbnail(
                            sheet_url, sheet_name, data, playlist_folder_id, thumbnail_info
                        )
                        
                        if thumbnail_update_success:
                            docs_success_count += 1
                            logger.info(f"✅ 썸네일 추가 성공: '{data['title'][:30]}...'")
                        else:
                            docs_error_count += 1
                            logger.error(f"❌ 썸네일 추가 실패: '{data['title'][:30]}...'")
                            
                    except Exception as e:
                        docs_error_count += 1
                        logger.error(f"❌ 썸네일 모드 처리 실패: {e}")
                
                # Docs 파일 생성 (썸네일 모드나 쇼츠 썸네일 모드가 아닌 경우)
                elif data['needs_docs'] and extraction_mode not in ["thumbnail", "shorts_thumbnail"]:
                    try:
                        if save_location == 'local':
                            logger.info(f"🔄 {idx}/{len(extraction_data)} (행{data['row_number']}): '{data['title'][:30]}...' 로컬 Docs 파일 생성 중...")
                            file_path = self.create_local_doc_file(
                                title=data['title'],
                                content=data['transcript_content'],
                                playlist_name=data['playlist_name'],
                                custom_path=getattr(self, '_custom_path', None),
                                file_format='docs'
                            )
                            docs_id = Path(file_path).name
                            docs_url = file_path
                        else:
                            logger.info(f"🔄 {idx}/{len(extraction_data)} (행{data['row_number']}): '{data['title'][:30]}...' Google Docs 문서 생성 중...")
                            
                            # 안전한 파일명 생성
                            safe_title = "".join(c for c in data['title'] if c.isalnum() or c in (' ', '-', '_')).rstrip()
                            safe_title = safe_title[:100] if safe_title else data.get('video_id', 'untitled')
                            
                            # 중복 파일 확인 (Google Docs)
                            logger.debug(f"📝 기존 Google Docs 확인: {safe_title}")
                            existing_docs = self.find_existing_file(safe_title, playlist_folder_id, 'application/vnd.google-apps.document')
                            
                            if existing_docs:
                                docs_id = existing_docs['id']
                                docs_url = f"https://docs.google.com/document/d/{docs_id}/edit"
                                logger.info(f"📁 기존 Google Docs 재사용: '{safe_title}' (ID: {docs_id}) - 새 업로드 없이 시트 업데이트만 진행")
                            else:
                                # 새 문서 생성
                                docs_id = self.create_google_doc(
                                    title=data['title'],
                                    content=data['transcript_content'],
                                    folder_id=playlist_folder_id,
                                    video_id=data.get('video_id'),
                                    metadata=metadata,
                                    thumbnail_info=thumbnail_info
                                )
                                docs_url = f"https://docs.google.com/document/d/{docs_id}/edit"
                        
                        # Docs 시트 업데이트
                        if data['docs_status_col']:
                            self.update_sheet_multiple_columns(
                                sheet_url=sheet_url,
                                sheet_name=sheet_name,
                                row_number=data['row_number'],
                                updates=[
                                    {'col': data['docs_status_col'], 'value': 'ㅇ'},
                                    {'col': data['docs_id_col'], 'value': docs_id} if data['docs_id_col'] else None,
                                    {'col': data['docs_path_col'], 'value': docs_url} if data['docs_path_col'] else None
                                ]
                            )
                        
                        docs_success_count += 1
                        logger.info(f"✅ Docs 파일 생성 완료: '{data['title'][:30]}...'")
                        
                    except Exception as e:
                        docs_error_count += 1
                        logger.error(f"❌ Docs 파일 생성 실패: {e}")
                
                # TXT 파일 생성 (썸네일 모드가 아닌 경우만)
                if data['needs_txt'] and extraction_mode != "thumbnail":
                    try:
                        if save_location == 'local':
                            logger.info(f"🔄 {idx}/{len(extraction_data)} (행{data['row_number']}): '{data['title'][:30]}...' 로컬 TXT 파일 생성 중...")
                            file_path = self.create_local_doc_file(
                                title=data['title'],
                                content=data['transcript_content'],
                                playlist_name=data['playlist_name'],
                                custom_path=getattr(self, '_custom_path', None),
                                file_format='txt'
                            )
                            txt_id = Path(file_path).name
                            txt_url = file_path
                        else:
                            logger.info(f"🔄 {idx}/{len(extraction_data)} (행{data['row_number']}): '{data['title'][:30]}...' Google Drive TXT 파일 생성 중...")
                            
                            # TXT 폴더 결정 (채널별 폴더가 있는 경우 해당 txt 폴더 사용, 아니면 기본 txt 하위 폴더)
                            if '영상 리스트' in sheet_name and metadata.get('channel_name'):
                                txt_folder_id = txt_folder_id  # 이미 위에서 채널의 txt 폴더로 설정됨
                            else:
                                # 기존 로직 (재생목록 폴더 내 txt 하위 폴더)
                                txt_folder_id = self.find_or_create_folder("txt", playlist_folder_id)
                            
                            # 안전한 파일명 생성 (.txt 확장자 포함)
                            safe_title = "".join(c for c in data['title'] if c.isalnum() or c in (' ', '-', '_')).rstrip()
                            safe_title = safe_title[:100] if safe_title else data.get('video_id', 'untitled')
                            txt_filename_with_ext = f"{safe_title}.txt"
                            
                            # 중복 파일 확인 (TXT) - .txt 확장자 포함하여 검색
                            logger.debug(f"📄 기존 TXT 파일 확인: {txt_filename_with_ext}")
                            existing_txt = self.find_existing_file(txt_filename_with_ext, txt_folder_id, 'text/plain')
                            
                            if existing_txt:
                                txt_id = existing_txt['id']
                                txt_url = f"https://drive.google.com/file/d/{txt_id}/view"
                                logger.info(f"📁 기존 TXT 파일 재사용: '{txt_filename_with_ext}' (ID: {txt_id}) - 새 업로드 없이 시트 업데이트만 진행")
                            else:
                                # 새 파일 생성
                                txt_id = self.create_drive_txt_file(
                                    title=data['title'],
                                    content=data['transcript_content'],
                                    folder_id=txt_folder_id,
                                    metadata=metadata
                                )
                                txt_url = f"https://drive.google.com/file/d/{txt_id}/view"
                        
                        # TXT 시트 업데이트
                        if data['txt_status_col']:
                            self.update_sheet_multiple_columns(
                                sheet_url=sheet_url,
                                sheet_name=sheet_name,
                                row_number=data['row_number'],
                                updates=[
                                    {'col': data['txt_status_col'], 'value': 'ㅇ'},
                                    {'col': data['txt_id_col'], 'value': txt_id} if data['txt_id_col'] else None,
                                    {'col': data['txt_path_col'], 'value': txt_url} if data['txt_path_col'] else None
                                ]
                            )
                        
                        txt_success_count += 1
                        logger.info(f"✅ TXT 파일 생성 완료: '{data['title'][:30]}...'")
                        
                    except Exception as e:
                        txt_error_count += 1
                        logger.error(f"❌ TXT 파일 생성 실패: {e}")
                
                # 썸네일 관련 헤더 업데이트 (구글 드라이브 저장 및 비디오 ID가 있는 경우만)
                if save_location == 'drive' and data.get('video_id') and thumbnail_info:
                    try:
                        # 썸네일 관련 헤더 열 찾기
                        workbook = self.client.open_by_url(sheet_url)
                        sheet = workbook.worksheet(sheet_name)
                        
                        thumbnail_status_col = self.find_column_by_keyword(sheet, '썸네일 여부')
                        thumbnail_image_col = self.find_column_by_keyword(sheet, '썸네일 이미지주소')
                        thumbnail_path_col = self.find_column_by_keyword(sheet, '썸네일 경로')
                        
                        # 썸네일 헤더 업데이트 준비
                        thumbnail_updates = []
                        
                        # 성공한 경우만 헤더 업데이트
                        if thumbnail_info.get('success'):
                            if thumbnail_status_col:
                                thumbnail_updates.append({'col': thumbnail_status_col, 'value': 'ㅇ'})
                            
                            if thumbnail_image_col and thumbnail_info.get('youtube_thumbnail_url'):
                                thumbnail_updates.append({'col': thumbnail_image_col, 'value': thumbnail_info['youtube_thumbnail_url']})
                            
                            if thumbnail_path_col and thumbnail_info.get('thumbnail_url'):
                                thumbnail_updates.append({'col': thumbnail_path_col, 'value': thumbnail_info['thumbnail_url']})
                        
                        # 썸네일 헤더 업데이트 실행
                        if thumbnail_updates:
                            self.update_sheet_multiple_columns(
                                sheet_url=sheet_url,
                                sheet_name=sheet_name,
                                row_number=data['row_number'],
                                updates=thumbnail_updates
                            )
                            logger.info(f"🖼️ {data['title'][:30]}...: 썸네일 헤더 업데이트 완료 ({len(thumbnail_updates)}개 열)")
                        
                    except Exception as thumb_header_error:
                        logger.warning(f"⚠️ 썸네일 헤더 업데이트 실패: {thumb_header_error}")
                
                # 썸네일 처리 결과 저장
                if data.get('video_id') and save_location == 'drive':
                    thumbnail_results.append(thumbnail_info.get('success', False))
                
                # 1초 대기 (API 사용량 제한 방지)
                import time
                time.sleep(1)
            
            # 썸네일 결과 집계
            thumbnail_success_count = sum(1 for result in thumbnail_results if result)
            thumbnail_error_count = sum(1 for result in thumbnail_results if not result)
            
            # 작업한 행 범위 계산
            if extraction_data:
                first_row = extraction_data[0]['row_number']
                last_row = extraction_data[-1]['row_number']
                logger.info(f"🎉 문서 추출 완료 (행 {first_row}-{last_row}) - Docs 성공: {docs_success_count}개, TXT 성공: {txt_success_count}개, Docs 실패: {docs_error_count}개, TXT 실패: {txt_error_count}개, 썸네일 성공: {thumbnail_success_count}개, 썸네일 실패: {thumbnail_error_count}개")
            else:
                logger.info(f"🎉 문서 추출 완료 - Docs 성공: {docs_success_count}개, TXT 성공: {txt_success_count}개, Docs 실패: {docs_error_count}개, TXT 실패: {txt_error_count}개, 썸네일 성공: {thumbnail_success_count}개, 썸네일 실패: {thumbnail_error_count}개")
            
            return {
                'processed': len(extraction_data),
                'docs_success': docs_success_count,
                'txt_success': txt_success_count,
                'docs_error': docs_error_count,
                'txt_error': txt_error_count,
                'thumbnail_success': thumbnail_success_count,
                'thumbnail_error': thumbnail_error_count,
                'stopped': stop_callback and stop_callback() if stop_callback else False
            }
            
        except Exception as e:
            logger.error(f"구글 닥스 추출 프로세스 실패: {e}")
            raise
    
    def update_sheet_multiple_columns(self, sheet_url: str, sheet_name: str, row_number: int, updates: List[Dict]):
        """시트의 여러 열을 한번에 업데이트"""
        try:
            workbook = self.client.open_by_url(sheet_url)
            sheet = workbook.worksheet(sheet_name)
            
            # 열 번호를 문자로 변환
            def column_number_to_letter(col_num):
                result = ""
                while col_num > 0:
                    col_num -= 1
                    result = chr(col_num % 26 + ord('A')) + result
                    col_num //= 26
                return result
            
            # 배치 업데이트를 위한 리스트
            batch_updates = []
            
            for update in updates:
                if update and update['col']:
                    col_letter = column_number_to_letter(update['col'])
                    cell_address = f'{col_letter}{row_number}'
                    batch_updates.append({
                        'range': cell_address,
                        'values': [[update['value']]]
                    })
                    logger.debug(f"📝 {row_number}행 {col_letter}열 업데이트: '{update['value']}' ({cell_address})")
            
            # 배치 업데이트 실행
            if batch_updates:
                sheet.batch_update(batch_updates)
                logger.info(f"✅ {row_number}행 {len(batch_updates)}개 열 업데이트 완료")
            
        except Exception as e:
            logger.error(f"시트 다중 열 업데이트 실패: {e}")
            raise
    
    def update_sheet_bulk_multiple_rows(self, sheet_url: str, sheet_name: str, row_updates: List[Dict]):
        """여러 행의 여러 열을 한번에 벌크 업데이트 (API 호출 최소화)"""
        try:
            workbook = self.client.open_by_url(sheet_url)
            sheet = workbook.worksheet(sheet_name)
            
            # 열 번호를 문자로 변환
            def column_number_to_letter(col_num):
                result = ""
                while col_num > 0:
                    col_num -= 1
                    result = chr(col_num % 26 + ord('A')) + result
                    col_num //= 26
                return result
            
            # 모든 업데이트를 하나의 배치로 합침
            all_batch_updates = []
            
            for row_data in row_updates:
                row_number = row_data['row_number']
                updates = row_data['updates']
                
                for update in updates:
                    if update and update['col']:
                        col_letter = column_number_to_letter(update['col'])
                        cell_address = f'{col_letter}{row_number}'
                        all_batch_updates.append({
                            'range': cell_address,
                            'values': [[update['value']]]
                        })
                        logger.debug(f"📝 벌크 업데이트 준비: {row_number}행 {col_letter}열 = '{update['value']}'")
            
            # 하나의 배치 업데이트로 모든 셀 업데이트
            if all_batch_updates:
                sheet.batch_update(all_batch_updates)
                logger.info(f"✅ 벌크 업데이트 완료: {len(row_updates)}행, 총 {len(all_batch_updates)}개 셀")
            
        except Exception as e:
            logger.error(f"시트 벌크 업데이트 실패: {e}")
            raise
    
    def process_bulk_shorts_thumbnail(self, sheet_url: str, sheet_name: str, extraction_data: List[Dict], stop_callback=None) -> Dict[str, int]:
        """쇼츠용 썸네일 50개 단위 순차 벌크 처리 (안정성 강화)"""
        try:
            logger.info(f"🚀 50개 단위 순차 벌크 썸네일 처리 시작: {len(extraction_data)}개 항목")
            
            # 썸네일 관련 열 정보를 한 번만 가져오기
            workbook = self.client.open_by_url(sheet_url)
            sheet = workbook.worksheet(sheet_name)
            
            thumbnail_status_col = self.find_column_by_keyword(sheet, '썸네일 여부')
            thumbnail_path_col = self.find_column_by_keyword(sheet, '썸네일 경로')
            thumbnail_image_col = self.find_column_by_keyword(sheet, '썸네일 이미지주소')
            
            # 쇼츠 모음 폴더 ID 설정
            base_folder_id = "1RwI2KSTAGfVrWugnSu8Ny5tlgls1XCox"  # 쇼츠 모음 폴더
            
            # 첫 번째 데이터로 폴더 구조 결정
            thumbnail_folder_id = base_folder_id
            if extraction_data:
                first_data = extraction_data[0]
                metadata = {}
                if first_data.get('video_id'):
                    try:
                        metadata = self.get_video_metadata_from_sheet(sheet_url, sheet_name, first_data['row_number'], first_data['video_id'])
                    except Exception:
                        pass
                
                # 채널별 폴더 또는 기본 폴더 결정
                if '영상 리스트' in sheet_name and metadata.get('channel_name'):
                    channel_name = metadata['channel_name']
                    channel_folder_id = self.find_or_create_folder(channel_name, base_folder_id)
                    thumbnail_folder_id = self.find_or_create_folder("썸네일", channel_folder_id)
                else:
                    # 기본 썸네일 폴더 사용
                    thumbnail_folder_id = self.find_or_create_folder("썸네일", base_folder_id)
            
            # 50개씩 배치로 나누기
            batch_size = 50
            total_batches = (len(extraction_data) + batch_size - 1) // batch_size
            
            total_success_count = 0
            total_error_count = 0
            
            for batch_idx in range(total_batches):
                start_idx = batch_idx * batch_size
                end_idx = min(start_idx + batch_size, len(extraction_data))
                current_batch = extraction_data[start_idx:end_idx]
                
                logger.info(f"📦 배치 {batch_idx + 1}/{total_batches}: {len(current_batch)}개 항목 처리 중...")
                
                # ESC 키로 중단 확인
                if stop_callback and stop_callback():
                    logger.info(f"🛑 사용자 요청으로 배치 처리 중단됨 ({batch_idx}/{total_batches} 배치 완료)")
                    break
                
                # 현재 배치 처리
                batch_result = self.process_single_batch_thumbnails(
                    current_batch, thumbnail_folder_id,
                    sheet_url, sheet_name,
                    thumbnail_status_col, thumbnail_path_col, thumbnail_image_col,
                    batch_idx + 1, total_batches,
                    stop_callback
                )
                
                total_success_count += batch_result['success_count']
                total_error_count += batch_result['error_count']
                
                logger.info(f"✅ 배치 {batch_idx + 1}/{total_batches} 완료: 성공 {batch_result['success_count']}개, 실패 {batch_result['error_count']}개")
                
                # 배치 간 잠시 대기 (API 안정성)
                if batch_idx < total_batches - 1:  # 마지막 배치가 아니면
                    import time
                    time.sleep(1)  # 1초 대기
            
            logger.info(f"🎉 50개 단위 순차 벌크 처리 완료: 총 성공 {total_success_count}개, 총 실패 {total_error_count}개")
            return {
                'processed': len(extraction_data),
                'docs_success': total_success_count,
                'txt_success': 0,
                'docs_error': total_error_count,
                'txt_error': 0
            }
            
        except Exception as e:
            logger.error(f"50개 단위 순차 벌크 처리 중 오류 발생: {e}")
            return {
                'processed': 0,
                'docs_success': 0,
                'txt_success': 0,
                'docs_error': len(extraction_data) if extraction_data else 0,
                'txt_error': 0
            }
    
    def process_single_batch_thumbnails(self, batch_data: List[Dict], thumbnail_folder_id: str,
                                      sheet_url: str, sheet_name: str,
                                      thumbnail_status_col: int, thumbnail_path_col: int, thumbnail_image_col: int,
                                      batch_num: int, total_batches: int, stop_callback=None) -> Dict[str, int]:
        """단일 배치(50개) 썸네일 처리"""
        try:
            logger.info(f"🔄 배치 {batch_num}/{total_batches} - 3단계 처리 시작: {len(batch_data)}개 항목")
            
            # 1단계: 이미지 다운로드
            logger.info(f"📥 배치 {batch_num} - 1단계: 이미지 다운로드 중...")
            thumbnail_data_list = []
            
            for idx, data in enumerate(batch_data, 1):
                if stop_callback and stop_callback():
                    logger.info(f"🛑 배치 {batch_num} 다운로드 중단 ({idx-1}/{len(batch_data)} 완료)")
                    break
                
                try:
                    # 안전한 파일명 생성
                    safe_title = "".join(c for c in data['title'] if c.isalnum() or c in (' ', '-', '_')).rstrip()
                    safe_title = safe_title[:100] if safe_title else data['video_id']
                    thumbnail_filename = f"{safe_title}.jpg"
                    
                    # 중복 파일 확인
                    existing_thumbnail = self.find_existing_file(thumbnail_filename, thumbnail_folder_id, 'image/jpeg')
                    if existing_thumbnail:
                        thumbnail_id = existing_thumbnail['id']
                        youtube_thumbnail_url = self.get_youtube_thumbnail_url(data['video_id'])
                        thumbnail_data_list.append({
                            'row_number': data['row_number'],
                            'success': True,
                            'thumbnail_id': thumbnail_id,
                            'drive_file_url': f"https://drive.google.com/file/d/{thumbnail_id}/view",
                            'youtube_thumbnail_url': youtube_thumbnail_url,
                            'filename': thumbnail_filename,
                            'title': data['title']
                        })
                        continue
                    
                    # YouTube에서 이미지 다운로드
                    youtube_thumbnail_url = self.get_youtube_thumbnail_url(data['video_id'])
                    
                    import requests
                    response = requests.get(youtube_thumbnail_url, timeout=15)  # 타임아웃 단축
                    response.raise_for_status()
                    
                    if response.status_code == 200 and len(response.content) > 1000:
                        thumbnail_data_list.append({
                            'row_number': data['row_number'],
                            'success': True,
                            'image_content': response.content,
                            'filename': thumbnail_filename,
                            'youtube_thumbnail_url': youtube_thumbnail_url,
                            'title': data['title']
                        })
                    else:
                        thumbnail_data_list.append({
                            'row_number': data['row_number'],
                            'success': False,
                            'error': '이미지 다운로드 실패',
                            'title': data['title']
                        })
                        
                except Exception as e:
                    logger.error(f"❌ 배치 {batch_num} 다운로드 실패 ({data['title'][:30]}): {e}")
                    thumbnail_data_list.append({
                        'row_number': data['row_number'],
                        'success': False,
                        'error': str(e),
                        'title': data['title']
                    })
            
            # 2단계: Drive 업로드
            upload_data = [t for t in thumbnail_data_list if t['success'] and 'image_content' in t]
            if upload_data:
                logger.info(f"☁️ 배치 {batch_num} - 2단계: {len(upload_data)}개 이미지 업로드 중...")
                upload_results = self.bulk_upload_thumbnails_to_drive(upload_data, thumbnail_folder_id)
                
                # 업로드 결과를 thumbnail_data_list에 반영
                for thumbnail_data in thumbnail_data_list:
                    if thumbnail_data['success'] and 'image_content' in thumbnail_data:
                        for upload_result in upload_results:
                            if upload_result['filename'] == thumbnail_data['filename']:
                                thumbnail_data.update(upload_result)
                                break
            
            # 3단계: 시트 업데이트
            successful_thumbnails = [t for t in thumbnail_data_list if t.get('success') and t.get('thumbnail_id')]
            if successful_thumbnails:
                logger.info(f"📝 배치 {batch_num} - 3단계: {len(successful_thumbnails)}개 시트 업데이트 중...")
                try:
                    self.bulk_update_sheet_thumbnails(
                        sheet_url, sheet_name,
                        successful_thumbnails,
                        thumbnail_status_col, thumbnail_path_col, thumbnail_image_col
                    )
                except Exception as sheet_error:
                    logger.error(f"❌ 배치 {batch_num} 시트 업데이트 실패: {sheet_error}")
            
            # 결과 집계
            success_count = len(successful_thumbnails)
            error_count = len(thumbnail_data_list) - success_count
            
            return {
                'success_count': success_count,
                'error_count': error_count
            }
            
        except Exception as e:
            logger.error(f"❌ 배치 {batch_num} 처리 중 오류: {e}")
            return {
                'success_count': 0,
                'error_count': len(batch_data)
            }
    
    def process_bulk_docs_extraction(self, sheet_url: str, sheet_name: str, extraction_data: List[Dict], 
                                   base_batch_size: int, stop_callback=None) -> Dict[str, int]:
        """구글 닥스/TXT 벌크 추출 처리 (대용량 대본 예외처리 포함)"""
        try:
            logger.info(f"🚀 구글 닥스/TXT 벌크 처리 시작: {len(extraction_data)}개 항목 (기본 배치 크기: {base_batch_size}개)")
            
            # 50개 이상인 경우 50개씩 분할
            if len(extraction_data) >= 50:
                max_batch_size = 50
                logger.info(f"📦 50개 이상 감지: 최대 배치 크기를 {max_batch_size}개로 제한")
            else:
                max_batch_size = base_batch_size
            
            # 대용량 대본 예외 처리: 40,000자 이상 대본 미리 분리
            large_transcripts = []
            normal_transcripts = []
            
            for data in extraction_data:
                transcript_content = data.get('transcript_content', '')
                if len(transcript_content) >= 40000:
                    large_transcripts.append(data)
                    logger.debug(f"🔍 대용량 대본 감지 ({len(transcript_content):,}자): {data.get('title', '')[:30]}...")
                else:
                    normal_transcripts.append(data)
            
            logger.info(f"📊 대본 분류: 일반 {len(normal_transcripts)}개, 대용량 {len(large_transcripts)}개")
            
            # 일반 대본들을 배치로 나누기
            batch_size = min(max_batch_size, base_batch_size)
            normal_batches = []
            for i in range(0, len(normal_transcripts), batch_size):
                normal_batches.append(normal_transcripts[i:i+batch_size])
            
            total_batches = len(normal_batches) + len(large_transcripts)  # 대용량 대본은 각각 개별 배치
            logger.info(f"📦 총 {total_batches}개 배치로 분할 (일반: {len(normal_batches)}개, 대용량: {len(large_transcripts)}개)")
            
            total_success_count = {'docs': 0, 'txt': 0}
            total_error_count = {'docs': 0, 'txt': 0}
            batch_idx = 0
            
            # 1. 일반 대본 배치 처리
            for batch_data in normal_batches:
                batch_idx += 1
                logger.info(f"📦 일반 배치 {batch_idx}/{len(normal_batches)}: {len(batch_data)}개 항목 처리 중...")
                
                if stop_callback and stop_callback():
                    logger.info(f"🛑 사용자 요청으로 배치 처리 중단됨 ({batch_idx-1}/{len(normal_batches)} 일반 배치 완료)")
                    break
                
                batch_result = self.process_single_docs_batch(
                    batch_data, sheet_url, sheet_name, batch_idx, total_batches, stop_callback
                )
                
                total_success_count['docs'] += batch_result['docs_success']
                total_success_count['txt'] += batch_result['txt_success']
                total_error_count['docs'] += batch_result['docs_error']
                total_error_count['txt'] += batch_result['txt_error']
                
                logger.info(f"✅ 일반 배치 {batch_idx} 완료: Docs {batch_result['docs_success']}개, TXT {batch_result['txt_success']}개 성공")
                
                # 배치 간 대기 (API 안정성)
                if batch_idx < total_batches:
                    import time
                    time.sleep(2)  # 2초 대기
            
            # 2. 대용량 대본 개별 처리
            for large_data in large_transcripts:
                batch_idx += 1
                if stop_callback and stop_callback():
                    logger.info(f"🛑 사용자 요청으로 대용량 대본 처리 중단됨")
                    break
                
                logger.info(f"📋 대용량 대본 {batch_idx-len(normal_batches)}/{len(large_transcripts)}: '{large_data.get('title', '')[:30]}...' 개별 처리 중...")
                
                batch_result = self.process_single_docs_batch(
                    [large_data], sheet_url, sheet_name, batch_idx, total_batches, stop_callback, is_large_transcript=True
                )
                
                total_success_count['docs'] += batch_result['docs_success']
                total_success_count['txt'] += batch_result['txt_success']
                total_error_count['docs'] += batch_result['docs_error']
                total_error_count['txt'] += batch_result['txt_error']
                
                logger.info(f"✅ 대용량 대본 처리 완료: Docs {batch_result['docs_success']}개, TXT {batch_result['txt_success']}개")
                
                # 대용량 대본 처리 후 긴 대기
                if batch_idx < total_batches:
                    import time
                    time.sleep(5)  # 5초 대기
            
            total_docs_success = total_success_count['docs']
            total_txt_success = total_success_count['txt']
            total_docs_error = total_error_count['docs']
            total_txt_error = total_error_count['txt']
            
            logger.info(f"🎉 구글 닥스/TXT 벌크 처리 완료: Docs 성공 {total_docs_success}개, TXT 성공 {total_txt_success}개, 총 실패 {total_docs_error + total_txt_error}개")
            return {
                'processed': len(extraction_data),
                'docs_success': total_docs_success,
                'txt_success': total_txt_success,
                'docs_error': total_docs_error,
                'txt_error': total_txt_error
            }
            
        except Exception as e:
            logger.error(f"구글 닥스/TXT 벌크 처리 중 오류 발생: {e}")
            return {
                'processed': 0,
                'docs_success': 0,
                'txt_success': 0,
                'docs_error': len(extraction_data) if extraction_data else 0,
                'txt_error': 0
            }
    
    def process_single_docs_batch(self, batch_data: List[Dict], sheet_url: str, sheet_name: str, 
                                batch_idx: int, total_batches: int, stop_callback=None, 
                                is_large_transcript: bool = False) -> Dict[str, int]:
        """단일 구글 닥스/TXT 배치 처리"""
        try:
            logger.info(f"📝 배치 {batch_idx}/{total_batches} 처리 시작: {len(batch_data)}개 항목 {'(대용량 대본)' if is_large_transcript else ''}")
            
            docs_success_count = 0
            txt_success_count = 0
            docs_error_count = 0
            txt_error_count = 0
            
            # 시트 정보 가져오기
            workbook = self.client.open_by_url(sheet_url)
            sheet = workbook.worksheet(sheet_name)
            
            # 필요한 열 정보 가져오기
            docs_status_col = self.find_column_by_keyword(sheet, '구글닥스여부')
            docs_path_col = self.find_column_by_keyword(sheet, '구글닥스경로')
            txt_status_col = self.find_column_by_keyword(sheet, 'TXT여부')
            txt_path_col = self.find_column_by_keyword(sheet, 'TXT경로')
            thumbnail_status_col = self.find_column_by_keyword(sheet, '썸네일 여부')
            thumbnail_path_col = self.find_column_by_keyword(sheet, '썸네일 경로')
            thumbnail_image_col = self.find_column_by_keyword(sheet, '썸네일 이미지주소')
            
            # 배치 내 모든 업데이트를 저장할 리스트
            batch_updates = []
            
            for data in batch_data:
                if stop_callback and stop_callback():
                    logger.info(f"🛑 사용자 요청으로 배치 내 항목 처리 중단")
                    break
                
                try:
                    row_number = data['row_number']
                    video_id = data.get('video_id', '')
                    title = data.get('title', '제목없음')
                    transcript_content = data.get('transcript_content', '')
                    channel_name = data.get('channel_name', '')
                    
                    logger.debug(f"📄 처리 중: {row_number}행 - '{title[:30]}...'")
                    
                    # 메타데이터 가져오기
                    try:
                        metadata = self.get_video_metadata_from_sheet(sheet_url, sheet_name, row_number, video_id)
                    except Exception as e:
                        logger.warning(f"⚠️ 메타데이터 가져오기 실패 (계속 진행): {e}")
                        metadata = {'channel_name': channel_name}
                    
                    # 폴더 결정
                    base_folder_id = "1RwI2KSTAGfVrWugnSu8Ny5tlgls1XCox"  # 쇼츠 모음 폴더
                    if '영상 리스트' in sheet_name and metadata.get('channel_name'):
                        # 채널명 폴더
                        channel_folder_id = self.find_or_create_folder(metadata['channel_name'], base_folder_id)
                        docs_folder_id = channel_folder_id
                        txt_folder_id = self.find_or_create_folder("TXT", channel_folder_id)
                        thumbnail_folder_id = self.find_or_create_folder("썸네일", channel_folder_id)
                    else:
                        # 기본 폴더
                        docs_folder_id = base_folder_id
                        txt_folder_id = self.find_or_create_folder("TXT", base_folder_id)
                        thumbnail_folder_id = self.find_or_create_folder("썸네일", base_folder_id)
                    
                    # 1. 구글 닥스 생성
                    try:
                        if is_large_transcript:
                            # 대용량 대본은 특별 처리 (기존 함수 사용)
                            result = self.create_drive_documents_for_long_transcript(
                                title, transcript_content, metadata.get('channel_name', ''), 
                                docs_folder_id, sheet_url, sheet_name, row_number
                            )
                            if result['docs_success']:
                                docs_success_count += 1
                                # 업데이트 정보 추가
                                if docs_status_col:
                                    batch_updates.append({
                                        'range': f'{self._column_number_to_letter(docs_status_col)}{row_number}',
                                        'values': [['ㅇ']]
                                    })
                                if docs_path_col and result.get('docs_url'):
                                    batch_updates.append({
                                        'range': f'{self._column_number_to_letter(docs_path_col)}{row_number}',
                                        'values': [[result['docs_url']]]
                                    })
                            else:
                                docs_error_count += 1
                        else:
                            # 일반 닥스 생성 (썸네일 포함)
                            # 썸네일 정보 준비
                            thumbnail_info = {'success': False}
                            if video_id:
                                try:
                                    thumbnail_url = f"https://img.youtube.com/vi/{video_id}/maxresdefault.jpg"
                                    thumbnail_info = self.download_and_upload_thumbnail(
                                        thumbnail_url, video_id, title, thumbnail_folder_id
                                    )
                                except Exception as thumb_error:
                                    logger.warning(f"⚠️ {video_id}: 썸네일 다운로드 실패 - {thumb_error}")
                                    thumbnail_info = {'success': False, 'youtube_thumbnail_url': thumbnail_url}
                            
                            # 구글닥스 생성 (일반 처리와 동일한 방식으로 썸네일 포함)
                            docs_id = self.create_google_doc(
                                title=title,
                                content=transcript_content,
                                folder_id=docs_folder_id,
                                video_id=video_id,
                                metadata=metadata,
                                thumbnail_info=thumbnail_info
                            )
                            docs_result = {
                                'success': True if docs_id else False,
                                'docs_url': f"https://docs.google.com/document/d/{docs_id}/edit" if docs_id else None,
                                'docs_id': docs_id
                            }
                            
                            if docs_result['success']:
                                docs_success_count += 1
                                # 업데이트 정보 추가
                                if docs_status_col:
                                    batch_updates.append({
                                        'range': f'{self._column_number_to_letter(docs_status_col)}{row_number}',
                                        'values': [['ㅇ']]
                                    })
                                if docs_path_col:
                                    batch_updates.append({
                                        'range': f'{self._column_number_to_letter(docs_path_col)}{row_number}',
                                        'values': [[docs_result['docs_url']]]
                                    })
                            else:
                                docs_error_count += 1
                                logger.error(f"❌ 구글닥스 생성 실패: {docs_result.get('error', '알 수 없는 오류')}")
                    except Exception as docs_error:
                        docs_error_count += 1
                        logger.error(f"❌ 구글닥스 생성 중 오류: {docs_error}")
                    
                    # 2. TXT 파일 생성
                    try:
                        txt_content = f"{title}\n\n{transcript_content}"
                        txt_filename = f"{title}.txt"
                        txt_result = self.create_txt_file_in_drive(txt_content, txt_filename, txt_folder_id)
                        
                        if txt_result['success']:
                            txt_success_count += 1
                            # 업데이트 정보 추가
                            if txt_status_col:
                                batch_updates.append({
                                    'range': f'{self._column_number_to_letter(txt_status_col)}{row_number}',
                                    'values': [['ㅇ']]
                                })
                            if txt_path_col:
                                batch_updates.append({
                                    'range': f'{self._column_number_to_letter(txt_path_col)}{row_number}',
                                    'values': [[txt_result['txt_url']]]
                                })
                        else:
                            txt_error_count += 1
                            logger.error(f"❌ TXT 파일 생성 실패: {txt_result.get('error', '알 수 없는 오류')}")
                    except Exception as txt_error:
                        txt_error_count += 1
                        logger.error(f"❌ TXT 파일 생성 중 오류: {txt_error}")
                    
                    # 3. 썸네일 시트 업데이트 (이미 구글닥스에 삽입되었으므로 시트 업데이트만)
                    if video_id and 'thumbnail_info' in locals() and thumbnail_info.get('success'):
                        try:
                            # 썸네일 시트 업데이트 정보 추가
                            if thumbnail_status_col:
                                batch_updates.append({
                                    'range': f'{self._column_number_to_letter(thumbnail_status_col)}{row_number}',
                                    'values': [['ㅇ']]
                                })
                            if thumbnail_path_col and thumbnail_info.get('drive_file_url'):
                                batch_updates.append({
                                    'range': f'{self._column_number_to_letter(thumbnail_path_col)}{row_number}',
                                    'values': [[thumbnail_info['drive_file_url']]]
                                })
                            if thumbnail_image_col and thumbnail_info.get('youtube_thumbnail_url'):
                                batch_updates.append({
                                    'range': f'{self._column_number_to_letter(thumbnail_image_col)}{row_number}',
                                    'values': [[thumbnail_info['youtube_thumbnail_url']]]
                                })
                        except Exception as thumbnail_error:
                            logger.warning(f"⚠️ 썸네일 시트 업데이트 실패 (계속 진행): {thumbnail_error}")
                    
                except Exception as item_error:
                    logger.error(f"❌ 항목 처리 실패 ({row_number}행): {item_error}")
                    docs_error_count += 1
            
            # 배치 업데이트 실행
            try:
                if batch_updates:
                    sheet.batch_update(batch_updates)
                    logger.info(f"✅ 배치 시트 업데이트 완료: {len(batch_updates)}개 셀")
            except Exception as update_error:
                logger.warning(f"⚠️ 배치 시트 업데이트 실패, 개별 업데이트 시도: {update_error}")
                # 개별 업데이트 시도
                success_count = 0
                for update in batch_updates:
                    try:
                        sheet.update(update['range'], update['values'])
                        success_count += 1
                    except Exception as individual_error:
                        logger.error(f"❌ 개별 업데이트 실패 ({update['range']}): {individual_error}")
                logger.info(f"✅ 개별 업데이트 완료: {success_count}/{len(batch_updates)}개 성공")
            
            return {
                'docs_success': docs_success_count,
                'txt_success': txt_success_count,
                'docs_error': docs_error_count,
                'txt_error': txt_error_count
            }
            
        except Exception as e:
            logger.error(f"배치 처리 중 오류 발생: {e}")
            return {
                'docs_success': 0,
                'txt_success': 0,
                'docs_error': len(batch_data),
                'txt_error': len(batch_data)
            }
    
    def _column_number_to_letter(self, col_num):
        """열 번호를 문자로 변환하는 헬퍼 함수"""
        if not col_num:
            return None
        result = ""
        while col_num > 0:
            col_num -= 1
            result = chr(col_num % 26 + ord('A')) + result
            col_num //= 26
        return result
    
    def bulk_upload_thumbnails_to_drive(self, thumbnail_data_list: List[Dict], folder_id: str) -> List[Dict]:
        """썸네일 고속 연속 업로드 (Batch API 문제 해결)"""
        try:
            from googleapiclient.http import MediaInMemoryUpload
            import time
            
            logger.info(f"☁️ 고속 연속 업로드로 {len(thumbnail_data_list)}개 이미지 처리 시작")
            
            upload_results = []
            
            # 배치 API 문제로 인해 고속 연속 업로드 사용
            # 하지만 API 제한을 피하기 위해 작은 지연 시간 추가
            for idx, thumbnail_info in enumerate(thumbnail_data_list, 1):
                max_retries = 3
                retry_count = 0
                success = False
                
                while retry_count < max_retries and not success:
                    try:
                        logger.debug(f"☁️ {idx}/{len(thumbnail_data_list)}: '{thumbnail_info['filename']}' 업로드 중... (시도 {retry_count + 1}/{max_retries})")
                        
                        file_metadata = {
                            'name': thumbnail_info['filename'],
                            'parents': [folder_id],
                            'mimeType': 'image/jpeg'
                        }
                        
                        media = MediaInMemoryUpload(
                            thumbnail_info['image_content'],
                            mimetype='image/jpeg'
                        )
                        
                        # 타임아웃이 있는 업로드 요청
                        import socket
                        original_timeout = socket.getdefaulttimeout()
                        try:
                            socket.setdefaulttimeout(30)  # 30초 타임아웃
                            thumbnail_file = self.drive_service.files().create(
                                body=file_metadata,
                                media_body=media
                            ).execute()
                        finally:
                            socket.setdefaulttimeout(original_timeout)  # 원래 타임아웃 복구
                        
                        thumbnail_id = thumbnail_file.get('id')
                        drive_file_url = f"https://drive.google.com/file/d/{thumbnail_id}/view"
                        
                        upload_results.append({
                            'filename': thumbnail_info['filename'],
                            'success': True,
                            'thumbnail_id': thumbnail_id,
                            'drive_file_url': drive_file_url
                        })
                        
                        logger.debug(f"✅ 업로드 성공: {thumbnail_id}")
                        success = True
                        
                    except Exception as upload_error:
                        retry_count += 1
                        error_msg = str(upload_error)
                        
                        if "timeout" in error_msg.lower() or "read operation timed out" in error_msg.lower():
                            if retry_count < max_retries:
                                wait_time = retry_count * 2  # 2, 4초 대기
                                logger.warning(f"⏰ 업로드 타임아웃 '{thumbnail_info['filename']}', {wait_time}초 후 재시도 ({retry_count}/{max_retries})")
                                time.sleep(wait_time)
                            else:
                                logger.error(f"❌ 업로드 최종 실패 (타임아웃) '{thumbnail_info['filename']}': {upload_error}")
                                upload_results.append({
                                    'filename': thumbnail_info['filename'],
                                    'success': False,
                                    'error': f"타임아웃 (재시도 {max_retries}회 실패): {error_msg}"
                                })
                        else:
                            logger.error(f"❌ 업로드 실패 '{thumbnail_info['filename']}': {upload_error}")
                            upload_results.append({
                                'filename': thumbnail_info['filename'],
                                'success': False,
                                'error': str(upload_error)
                            })
                            break  # 타임아웃이 아닌 다른 에러는 재시도하지 않음
                
                # API 제한 방지를 위한 극소 지연 (100ms)
                if idx % 10 == 0:  # 10개마다 약간의 지연
                    time.sleep(0.1)
            
            success_count = len([r for r in upload_results if r['success']])
            logger.info(f"☁️ 고속 연속 업로드 완료: 성공 {success_count}개, 실패 {len(upload_results) - success_count}개")
            return upload_results
            
        except Exception as e:
            logger.error(f"Drive 업로드 중 오류 발생: {e}")
            # 모든 항목을 실패로 처리
            return [
                {
                    'filename': thumbnail_info.get('filename', 'unknown'),
                    'success': False,
                    'error': str(e)
                }
                for thumbnail_info in thumbnail_data_list
            ]
    
    def bulk_update_sheet_thumbnails(self, sheet_url: str, sheet_name: str, thumbnail_data_list: List[Dict], 
                                   thumbnail_status_col: int, thumbnail_path_col: int, thumbnail_image_col: int):
        """시트 썸네일 정보 안정적 벌크 업데이트 (gspread 호환)"""
        try:
            logger.info(f"📝 시트 안정적 벌크 업데이트: {len(thumbnail_data_list)}개 행")
            
            # gspread를 사용한 안정적 업데이트
            workbook = self.client.open_by_url(sheet_url)
            sheet = workbook.worksheet(sheet_name)
            
            # 열 번호를 문자로 변환
            def column_number_to_letter(col_num):
                if not col_num:
                    return None
                result = ""
                while col_num > 0:
                    col_num -= 1
                    result = chr(col_num % 26 + ord('A')) + result
                    col_num //= 26
                return result
            
            # 업데이트할 데이터 준비
            batch_updates = []
            
            for thumbnail_info in thumbnail_data_list:
                row_num = thumbnail_info['row_number']
                
                # 썸네일 여부 업데이트
                if thumbnail_status_col:
                    col_letter = column_number_to_letter(thumbnail_status_col)
                    batch_updates.append({
                        'range': f'{col_letter}{row_num}',
                        'values': [['ㅇ']]
                    })
                
                # 썸네일 이미지주소 업데이트
                if thumbnail_image_col and thumbnail_info.get('youtube_thumbnail_url'):
                    col_letter = column_number_to_letter(thumbnail_image_col)
                    batch_updates.append({
                        'range': f'{col_letter}{row_num}',
                        'values': [[thumbnail_info['youtube_thumbnail_url']]]
                    })
                
                # 썸네일 경로 업데이트
                if thumbnail_path_col and thumbnail_info.get('drive_file_url'):
                    col_letter = column_number_to_letter(thumbnail_path_col)
                    batch_updates.append({
                        'range': f'{col_letter}{row_num}',
                        'values': [[thumbnail_info['drive_file_url']]]
                    })
            
            # gspread의 batch_update 사용 (전역함수 보호 적용)
            if batch_updates:
                try:
                    # 헤더 정보 가져오기
                    headers = sheet.row_values(1)

                    # 벌크 업데이트 + 전역함수 열 자동 보호
                    bulk_update_with_formula_protection(
                        sheet=sheet,
                        updates=batch_updates,
                        headers=headers,
                        sheet_type=SheetType.VIDEO_LIST
                    )
                    logger.info(f"✅ 시트 안정적 벌크 업데이트 완료: {len(batch_updates)}개 셀 (전역함수 보호 적용)")
                except Exception as gspread_error:
                    logger.warning(f"⚠️ gspread 벌크 업데이트 실패, 개별 업데이트로 대체: {gspread_error}")
                    
                    # 대체 방법: 개별 업데이트 (안정성 우선)
                    success_count = 0
                    for update in batch_updates:
                        try:
                            sheet.update(update['range'], update['values'])
                            success_count += 1
                        except Exception as individual_error:
                            logger.error(f"❌ 개별 업데이트 실패 ({update['range']}): {individual_error}")
                    
                    logger.info(f"✅ 개별 업데이트 완료: {success_count}/{len(batch_updates)}개 성공")
            else:
                logger.warning("업데이트할 데이터가 없습니다")
                
        except Exception as e:
            logger.error(f"시트 벌크 업데이트 실패: {e}")
            # 에러 발생해도 전체 프로세스는 계속 진행
            logger.warning("⚠️ 시트 업데이트 실패했지만 처리 계속 진행")
    
    def _group_consecutive_ranges(self, ranges: List[str], values: List[List]):
        """연속된 셀 범위를 그룹화하여 효율성 증대"""
        if not ranges:
            return []
        
        # 셀 주소를 파싱하여 정렬
        parsed_ranges = []
        for i, range_str in enumerate(ranges):
            col = ''.join(c for c in range_str if c.isalpha())
            row = int(''.join(c for c in range_str if c.isdigit()))
            parsed_ranges.append((col, row, values[i]))
        
        # 열별로 그룹화
        grouped = {}
        for col, row, value in parsed_ranges:
            if col not in grouped:
                grouped[col] = []
            grouped[col].append((row, value))
        
        # 각 열에서 연속된 행들을 범위로 만들기
        result = []
        for col, row_values in grouped.items():
            row_values.sort(key=lambda x: x[0])  # 행 번호로 정렬
            
            current_start = row_values[0][0]
            current_values = [row_values[0][1]]
            
            for i in range(1, len(row_values)):
                current_row, current_value = row_values[i]
                prev_row = row_values[i-1][0]
                
                if current_row == prev_row + 1:
                    # 연속된 행
                    current_values.append(current_value)
                else:
                    # 연속이 끊어짐 - 이전 범위 저장
                    if len(current_values) == 1:
                        range_str = f"{col}{current_start}"
                    else:
                        range_str = f"{col}{current_start}:{col}{current_start + len(current_values) - 1}"
                    result.append((range_str, current_values))
                    
                    # 새로운 범위 시작
                    current_start = current_row
                    current_values = [current_value]
            
            # 마지막 범위 저장
            if len(current_values) == 1:
                range_str = f"{col}{current_start}"
            else:
                range_str = f"{col}{current_start}:{col}{current_start + len(current_values) - 1}"
            result.append((range_str, current_values))
        
        return result
    
    def update_docs_status(self, sheet_url: str, sheet_name: str, row_number: int, docs_status_col: int, status: str):
        """시트의 구글닥스여부 열 업데이트 (호환성을 위한 기존 메서드)"""
        self.update_docs_complete_info(sheet_url, sheet_name, row_number, docs_status_col, None, None, status, None, None)
    
    def update_docs_complete_info(self, sheet_url: str, sheet_name: str, row_number: int, 
                                 docs_status_col: int, file_id_col: Optional[int], file_path_col: Optional[int],
                                 status: str, file_id: Optional[str] = None, file_url: Optional[str] = None):
        """구글 닥스 생성 완료 후 관련 열들을 한 번에 업데이트"""
        try:
            workbook = self.client.open_by_url(sheet_url)
            sheet = workbook.worksheet(sheet_name)
            
            # 열 번호를 문자로 변환
            def column_number_to_letter(col_num):
                result = ""
                while col_num > 0:
                    col_num -= 1
                    result = chr(col_num % 26 + ord('A')) + result
                    col_num //= 26
                return result
            
            # 배치 업데이트를 위한 리스트
            updates = []
            
            # 1. 구글닥스여부 열 업데이트
            if docs_status_col:
                col_letter = column_number_to_letter(docs_status_col)
                cell_address = f'{col_letter}{row_number}'
                updates.append({
                    'range': cell_address,
                    'values': [[status]]
                })
                logger.debug(f"📝 {row_number}행 구글닥스여부 업데이트: '{status}' ({cell_address})")
            
            # 2. 대본파일 ID 열 업데이트
            if file_id_col and file_id:
                col_letter = column_number_to_letter(file_id_col)
                cell_address = f'{col_letter}{row_number}'
                updates.append({
                    'range': cell_address,
                    'values': [[file_id]]
                })
                logger.debug(f"📝 {row_number}행 대본파일ID 업데이트: '{file_id}' ({cell_address})")
            
            # 3. 대본파일 경로 열 업데이트
            if file_path_col and file_url:
                col_letter = column_number_to_letter(file_path_col)
                cell_address = f'{col_letter}{row_number}'
                updates.append({
                    'range': cell_address,
                    'values': [[file_url]]
                })
                logger.debug(f"📝 {row_number}행 대본파일경로 업데이트: '{file_url[:50]}...' ({cell_address})")
            
            # 배치 업데이트 실행
            if updates:
                sheet.batch_update(updates)
                logger.info(f"✅ {row_number}행 {len(updates)}개 열 업데이트 완료")
            
        except Exception as e:
            logger.error(f"시트 정보 업데이트 실패 (행 {row_number}): {e}")
            raise
    
    def get_header_columns(self, sheet, use_cache=True, header_row_num=1):
        """헤더에서 필요한 열들의 인덱스를 찾아 반환 (캐싱 지원)"""
        try:
            # 캐시 키 생성 (시트 ID, 제목, 헤더 행 번호 조합)
            cache_key = f"{sheet.spreadsheet.id}_{sheet.title}_row{header_row_num}"

            # 캐시 확인
            if use_cache and hasattr(self, '_header_cache') and cache_key in self._header_cache:
                logger.debug(f"헤더 매핑 캐시 사용: {self._header_cache[cache_key]}")
                return self._header_cache[cache_key]

            # 캐시 딕셔너리 초기화 (없는 경우)
            if not hasattr(self, '_header_cache'):
                self._header_cache = {}

            # 헤더 행 가져오기
            header_row = sheet.row_values(header_row_num)

            column_mapping = {}
            target_columns = ['영상 ID', '제목', '영상 링크', '분야1', '분야2', '숏폼여부', '조회수', '구독자수',
                            '채널명', '조회수 대비 좋아요', '조회수 대비 댓글', '구독자 대비 조회수 배율',
                            '영상길이', '벤치마킹 채널여부', '영상 업로드날짜', '영상 업로드 날짜',
                            '대본유무', '후킹자막 유무', '후킹자막', '대본내용', '대본 텍스트수',
                            '카테고리 분류', '카테고리 ID', '디스크립션', '사용 해시태그', '썸네일 링크', '원본 행순서']

            for i, cell_value in enumerate(header_row, 1):  # 1-based indexing
                cell_str = str(cell_value).strip()

                for target in target_columns:
                    # 이미 매핑된 경우 스킵 (가장 빠른 열만 사용)
                    if target in column_mapping:
                        continue

                    # 조회수는 정확히 일치하는 경우만 매핑 (숫자 제외)
                    if target == '조회수':
                        # 넘버링 제거 (예: "1. 조회수", "2.조회수" 등)
                        clean_text = cell_str
                        # 앞에 숫자와 점/공백이 있으면 제거
                        import re
                        clean_text = re.sub(r'^\d+[\.\s]*', '', clean_text).strip()
                        if clean_text == '조회수':
                            column_mapping[target] = i
                            logger.debug(f"헤더 매핑: {target} -> 열 {i}")
                            break
                    # 채널명도 정확히 일치하는 경우만 매핑
                    elif target == '채널명':
                        clean_text = cell_str
                        import re
                        clean_text = re.sub(r'^\d+[\.\s]*', '', clean_text).strip()
                        if clean_text == '채널명':
                            column_mapping[target] = i
                            logger.debug(f"헤더 매핑: {target} -> 열 {i}")
                            break
                    # 나머지는 부분 일치
                    elif target in cell_str:
                        column_mapping[target] = i
                        logger.debug(f"헤더 매핑: {target} -> 열 {i}")
                        break

            logger.info(f"헤더 열 매핑 완료: {column_mapping}")

            # 캐시에 저장
            if use_cache:
                self._header_cache[cache_key] = column_mapping
                logger.debug(f"헤더 매핑을 캐시에 저장: {cache_key}")

            return column_mapping

        except Exception as e:
            logger.error(f"헤더 열 매핑 실패: {str(e)}")
            return {}
    
    def get_column_mapping(self, sheet):
        """get_header_columns의 별칭 메서드"""
        return self.get_header_columns(sheet)
    
    def get_column_unique_values(self, sheet, column_name, start_row=10):
        """특정 열의 고유값들을 반환 (start_row부터 마지막 데이터까지)"""
        try:
            column_mapping = self.get_header_columns(sheet)
            if column_name not in column_mapping:
                logger.warning(f"'{column_name}' 열을 찾을 수 없습니다.")
                return []
            
            col_index = column_mapping[column_name]
            
            # A열의 마지막 데이터 행 찾기
            last_row = len(sheet.col_values(1))
            
            if last_row < start_row:
                logger.info(f"'{column_name}' 열에 {start_row}행부터 시작할 데이터가 없습니다.")
                return []
            
            # 해당 열의 값들 가져오기 (start_row부터 last_row까지)
            column_values = sheet.col_values(col_index)[start_row-1:last_row]
            
            # 빈 값들 제거하고 고유값만 추출
            unique_values = list(set([str(val).strip() for val in column_values if val and str(val).strip()]))
            unique_values.sort()  # 정렬
            
            logger.debug(f"'{column_name}' 열의 고유값 {len(unique_values)}개 추출: {unique_values[:5]}{'...' if len(unique_values) > 5 else ''}")
            return unique_values
            
        except Exception as e:
            logger.error(f"'{column_name}' 열의 고유값 추출 실패: {str(e)}")
            return []
    
    def get_filtered_data(self, sheet, field1_filters=None, field2_filters=None, shortform_filters=None,
                         min_views=None, min_subscribers=None, channel_names=None,
                         min_like_ratio=None, min_duration=None, max_upload_days=None, max_video_upload_days=None,
                         benchmarking_only=False,
                         script_exists_only=False, hook_subtitle_exists_only=False,
                         has_transcript=None, has_hook=None,
                         start_row=10, count_only=False,
                         top_n=None, top_n_mode=None, ignore_field2=False, use_cache=True,
                         sort_column=None, sort_order=None, sort_limit=None):
        """조건에 맞는 데이터 가져오기 (실시간 카운트용 - 캐싱 지원)"""
        try:
            # has_transcript와 has_hook를 script_exists_only, hook_subtitle_exists_only에 매핑
            if has_transcript is not None:
                script_exists_only = has_transcript
            if has_hook is not None:
                hook_subtitle_exists_only = has_hook

            # 헤더 열 매핑 (캐시 사용)
            column_mapping = self.get_header_columns(sheet, use_cache=True)

            # 캐시 키 생성
            cache_key = f"{sheet.spreadsheet.id}_{sheet.title}_data"

            # 캐시된 데이터 확인 및 사용
            if use_cache and hasattr(self, '_data_cache') and cache_key in self._data_cache:
                logger.debug(f"시트 데이터 캐시 사용 (행 수: {len(self._data_cache[cache_key])})")
                all_data = self._data_cache[cache_key]
            else:
                # A열의 마지막 데이터 행 찾기
                last_row = len(sheet.col_values(1))
                if last_row < start_row:
                    return []

                # 모든 데이터 가져오기 (벌크 처리) - UNFORMATTED_VALUE로 실제 값 가져오기
                logger.info(f"시트 데이터를 새로 불러옵니다 (예상 행 수: {last_row})...")
                all_data = self.get_all_values_unformatted(sheet)

                # 캐시에 저장
                if use_cache:
                    if not hasattr(self, '_data_cache'):
                        self._data_cache = {}
                    self._data_cache[cache_key] = all_data
                    logger.info(f"시트 데이터를 캐시에 저장 (행 수: {len(all_data)})")

            # A열의 마지막 데이터 행 찾기 (캐시된 데이터 기준)
            last_row = len(all_data)
            if last_row < start_row:
                return []
            
            # 필터링할 데이터 (start_row부터)
            data_rows = all_data[start_row-1:last_row]

            # 헤더 행 길이 확인 (1행)
            header_row = all_data[0] if len(all_data) > 0 else []
            header_length = len(header_row)

            # 각 데이터 행의 길이를 헤더 길이에 맞춤 (빈 열 추가)
            normalized_data_rows = []
            for row in data_rows:
                if len(row) < header_length:
                    # 부족한 열을 빈 문자열로 채움
                    normalized_row = list(row) + [''] * (header_length - len(row))
                    normalized_data_rows.append(normalized_row)
                else:
                    normalized_data_rows.append(row)

            data_rows = normalized_data_rows

            filtered_rows = []

            # 디버깅용 카운터
            upload_date_filter_active = max_upload_days is not None
            upload_date_pass_count = 0
            upload_date_fail_count = 0
            video_upload_date_filter_active = max_video_upload_days is not None
            video_upload_date_pass_count = 0
            video_upload_date_fail_count = 0
            total_checked = 0

            # 업로드 날짜 필터 샘플 로깅 초기화
            if upload_date_filter_active:
                self._upload_date_filter_first_call = True
                self._upload_date_sample_logged = 0

            # 영상 업로드날짜 필터 샘플 로깅 초기화
            if video_upload_date_filter_active:
                self._video_upload_date_filter_first_call = True
                self._video_upload_date_sample_logged = 0

            for row_idx, row_data in enumerate(data_rows, start_row):
                if not any(row_data):  # 빈 행 스킵
                    continue

                total_checked += 1

                # 각 조건별로 검사
                field1_pass = self._check_field_filter(row_data, column_mapping, '분야1', field1_filters)
                field2_pass = self._check_field_filter(row_data, column_mapping, '분야2', field2_filters)
                shortform_pass = self._check_field_filter(row_data, column_mapping, '숏폼여부', shortform_filters)
                views_pass = self._check_numeric_filter(row_data, column_mapping, '조회수', min_views)
                subscribers_pass = self._check_numeric_filter(row_data, column_mapping, '구독자수', min_subscribers)
                channel_pass = self._check_channel_filter(row_data, column_mapping, channel_names)
                like_ratio_pass = self._check_percentage_filter(row_data, column_mapping, '조회수 대비 좋아요', min_like_ratio)
                duration_pass = self._check_duration_filter(row_data, column_mapping, min_duration)
                upload_date_pass = self._check_upload_date_filter(row_data, column_mapping, max_upload_days)
                video_upload_date_pass = self._check_video_upload_date_filter(row_data, column_mapping, max_video_upload_days)
                benchmarking_pass = self._check_benchmarking_filter(row_data, column_mapping, benchmarking_only)
                script_exists_pass = self._check_script_exists_filter(row_data, column_mapping, script_exists_only)
                hook_subtitle_exists_pass = self._check_hook_subtitle_exists_filter(row_data, column_mapping, hook_subtitle_exists_only)

                # 업로드 날짜 필터 디버깅
                if upload_date_filter_active:
                    if upload_date_pass:
                        upload_date_pass_count += 1
                    else:
                        upload_date_fail_count += 1

                # 영상 업로드날짜 필터 디버깅
                if video_upload_date_filter_active:
                    if video_upload_date_pass:
                        video_upload_date_pass_count += 1
                    else:
                        video_upload_date_fail_count += 1

                # 모든 조건을 만족하는 경우만 추가
                if (field1_pass and field2_pass and shortform_pass and
                    views_pass and subscribers_pass and channel_pass and like_ratio_pass and
                    duration_pass and upload_date_pass and video_upload_date_pass and benchmarking_pass and
                    script_exists_pass and hook_subtitle_exists_pass):
                    if count_only:
                        filtered_rows.append(None)  # 카운트만 필요한 경우
                    else:
                        filtered_rows.append(row_data)

            # 업로드 날짜 필터 결과 요약
            if upload_date_filter_active:
                logger.info(f"📅 업로드 날짜 필터 요약: 전체 {total_checked}개 행 중 통과 {upload_date_pass_count}개, 제외 {upload_date_fail_count}개")

            # 영상 업로드날짜 필터 결과 요약
            if video_upload_date_filter_active:
                logger.info(f"📅 영상 업로드날짜 필터 요약: 전체 {total_checked}개 행 중 통과 {video_upload_date_pass_count}개, 제외 {video_upload_date_fail_count}개")
            
            # 상위 N개 처리
            if top_n and top_n_mode and not count_only and filtered_rows:
                # None 값들 제거 (count_only 처리에서 추가될 수 있음)
                filtered_rows = [row for row in filtered_rows if row is not None]
                if filtered_rows:  # None이 아닌 행이 있는 경우에만 처리
                    filtered_rows = self._apply_top_n_filter(filtered_rows, column_mapping, top_n, top_n_mode, ignore_field2, field1_filters, field2_filters)

            # 상위 N개 정렬 추출 처리 (top_n이 없는 경우에도 적용)
            if sort_column and sort_order and sort_limit and not count_only and filtered_rows:
                logger.info(f"상위 N개 정렬 추출 적용: {sort_column} {sort_order} 상위 {sort_limit}개")
                filtered_rows = self._apply_sort_and_limit(filtered_rows, column_mapping, sort_column, sort_order, sort_limit)

            return filtered_rows
            
        except Exception as e:
            logger.error(f"필터링된 데이터 가져오기 실패: {str(e)}")
            return []
    
    def _apply_top_n_filter(self, filtered_rows, column_mapping, top_n, top_n_mode, ignore_field2=False, field1_filters=None, field2_filters=None):
        """상위 N개 필터 적용"""
        try:
            if '조회수' not in column_mapping:
                logger.warning("조회수 열을 찾을 수 없어 상위 N개 필터를 적용할 수 없습니다.")
                return filtered_rows
            
            views_col_idx = column_mapping['조회수'] - 1  # 0-based index
            top_n_rows = []
            
            # 행이 있는지 확인
            if not filtered_rows:
                return []
            
            if top_n_mode == 'channel':
                # 채널별 상위 N개
                if '채널명' not in column_mapping:
                    logger.warning("채널명 열을 찾을 수 없습니다.")
                    return filtered_rows
                
                channel_col_idx = column_mapping['채널명'] - 1
                channel_groups = {}
                
                # 채널별로 그룹화
                for row in filtered_rows:
                    if row is None or not row:  # None 또는 빈 행 건너뛰기
                        continue
                    if len(row) > max(channel_col_idx, views_col_idx):
                        channel = str(row[channel_col_idx]).strip()
                        if channel and channel not in channel_groups:
                            channel_groups[channel] = []
                        if channel:
                            channel_groups[channel].append(row)
                
                # 각 채널별로 조회수 기준 상위 N개 선택
                for channel, rows in channel_groups.items():
                    try:
                        # 유효한 행들만 필터링
                        valid_rows = []
                        for row in rows:
                            if row is not None and isinstance(row, (list, tuple)) and len(row) > views_col_idx:
                                valid_rows.append(row)
                        
                        if not valid_rows:
                            continue
                            
                        # 조회수 기준으로 정렬 - 각 행에 대해 안전하게 파싱
                        def get_sort_value(row):
                            try:
                                view_value = row[views_col_idx] if views_col_idx < len(row) else None
                                parsed_value = self._parse_number(view_value)
                                return parsed_value if parsed_value is not None else 0
                            except:
                                return 0
                        
                        # 각 행에 대해 (정렬값, 행) 튜플로 만들어서 정렬
                        row_with_values = [(get_sort_value(row), row) for row in valid_rows]
                        row_with_values.sort(key=lambda x: x[0], reverse=True)
                        
                        # 상위 N개 추출 (채널별은 중복 제거 없이)
                        for sort_value, row in row_with_values[:top_n]:
                            top_n_rows.append(row)
                            
                    except Exception as e:
                        logger.error(f"채널별 정렬 중 오류 ({channel}): {str(e)}")
                        continue
                    
            elif top_n_mode == 'field1':
                # 분야1별 상위 N개 - 분야2 무시 옵션 고려
                if '분야1' not in column_mapping:
                    logger.warning("분야1 열을 찾을 수 없습니다.")
                    return filtered_rows
                
                field1_col_idx = column_mapping['분야1'] - 1
                field1_groups = {}
                
                # 이미 필터링된 행들을 분야1별로 그룹화
                for row in filtered_rows:
                    if row is None or not row:  # None 또는 빈 행 건너뛰기
                        continue
                    if len(row) > max(field1_col_idx, views_col_idx):
                        field1_val = str(row[field1_col_idx]).strip()
                        if field1_val:
                            # 쉼표로 구분된 값들 처리
                            field1_vals = [val.strip() for val in field1_val.split(',') if val.strip()]
                            for f1_val in field1_vals:
                                if f1_val not in field1_groups:
                                    field1_groups[f1_val] = []
                                field1_groups[f1_val].append(row)
                
                # 분야2 무시 옵션 처리
                if ignore_field2:
                    # 분야1 전체에서 상위 N개 추출
                    all_filtered_rows = []
                    added_rows = set()
                    
                    # 모든 필터링된 행을 하나의 리스트로 합침
                    for rows in field1_groups.values():
                        for row in rows:
                            row_key = tuple(row)
                            if row_key not in added_rows:
                                all_filtered_rows.append(row)
                                added_rows.add(row_key)
                    
                    try:
                        valid_rows = [row for row in all_filtered_rows if row is not None and isinstance(row, (list, tuple)) and len(row) > views_col_idx]
                        if valid_rows:
                            def get_sort_value(row):
                                try:
                                    view_value = row[views_col_idx] if views_col_idx < len(row) else None
                                    parsed_value = self._parse_number(view_value)
                                    return parsed_value if parsed_value is not None else 0
                                except:
                                    return 0
                            
                            # 전체에서 상위 N개만 추출
                            row_with_values = [(get_sort_value(row), row) for row in valid_rows]
                            row_with_values.sort(key=lambda x: x[0], reverse=True)
                            
                            for sort_value, row in row_with_values[:top_n]:
                                top_n_rows.append(row)
                    
                    except Exception as e:
                        logger.error(f"분야1 전체 상위 N개 정렬 중 오류: {str(e)}")
                        top_n_rows = []
                        
                else:
                    # 분야1별 -> 분야2별 상위 N개 추출 
                    if '분야2' not in column_mapping:
                        logger.warning("분야2 열을 찾을 수 없습니다.")
                        return filtered_rows
                    
                    field2_col_idx = column_mapping['분야2'] - 1
                    
                    # 각 분야1별로 분야2별 상위 N개 추출
                    added_rows = set()
                    for field1_val, field1_rows in field1_groups.items():
                        # 현재 분야1 내에서 분야2별로 그룹화
                        field2_groups = {}
                        
                        for row in field1_rows:
                            if len(row) > field2_col_idx:
                                field2_val = str(row[field2_col_idx]).strip()
                                if field2_val:
                                    field2_vals = [val.strip() for val in field2_val.split(',') if val.strip()]
                                    for f2_val in field2_vals:
                                        if f2_val not in field2_groups:
                                            field2_groups[f2_val] = []
                                        field2_groups[f2_val].append(row)
                        
                        # 각 분야2별로 상위 N개 추출
                        for field2_val, field2_rows in field2_groups.items():
                            try:
                                valid_rows = [row for row in field2_rows if row is not None and isinstance(row, (list, tuple)) and len(row) > views_col_idx]
                                if not valid_rows:
                                    continue
                                
                                def get_sort_value(row):
                                    try:
                                        view_value = row[views_col_idx] if views_col_idx < len(row) else None
                                        parsed_value = self._parse_number(view_value)
                                        return parsed_value if parsed_value is not None else 0
                                    except:
                                        return 0
                                
                                row_with_values = [(get_sort_value(row), row) for row in valid_rows]
                                row_with_values.sort(key=lambda x: x[0], reverse=True)
                                
                                for sort_value, row in row_with_values[:top_n]:
                                    row_key = tuple(row)
                                    if row_key not in added_rows:
                                        top_n_rows.append(row)
                                        added_rows.add(row_key)
                            
                            except Exception as e:
                                logger.error(f"분야1별-분야2별 정렬 중 오류 ({field1_val}-{field2_val}): {str(e)}")
                                continue
                            
            elif top_n_mode == 'field2':
                # 분야2별 상위 N개
                if '분야2' not in column_mapping:
                    logger.warning("분야2 열을 찾을 수 없습니다.")
                    return filtered_rows
                
                field2_col_idx = column_mapping['분야2'] - 1
                field2_groups = {}
                
                # 이미 필터링된 행들을 분야2별로 그룹화
                for row in filtered_rows:
                    if row is None or not row:  # None 또는 빈 행 건너뛰기
                        continue
                    if len(row) > max(field2_col_idx, views_col_idx):
                        field2_val = str(row[field2_col_idx]).strip()
                        if field2_val:
                            # 쉼표로 구분된 값들 처리
                            field2_vals = [val.strip() for val in field2_val.split(',') if val.strip()]
                            for f2_val in field2_vals:
                                if f2_val not in field2_groups:
                                    field2_groups[f2_val] = []
                                field2_groups[f2_val].append(row)
                
                # 각 분야2별로 조회수 기준 상위 N개 선택 (중복 제거)
                added_rows = set()
                for field2_val, rows in field2_groups.items():
                    # 조회수로 정렬 (None 값 안전 처리)
                    try:
                        # 유효한 행들만 필터링
                        valid_rows = []
                        for row in rows:
                            if row is not None and isinstance(row, (list, tuple)) and len(row) > views_col_idx:
                                valid_rows.append(row)
                        
                        if not valid_rows:
                            continue
                            
                        # 조회수 기준으로 정렬 - 각 행에 대해 안전하게 파싱
                        def get_sort_value(row):
                            try:
                                view_value = row[views_col_idx] if views_col_idx < len(row) else None
                                parsed_value = self._parse_number(view_value)
                                return parsed_value if parsed_value is not None else 0
                            except:
                                return 0
                        
                        # 각 행에 대해 (정렬값, 행) 튜플로 만들어서 정렬
                        row_with_values = [(get_sort_value(row), row) for row in valid_rows]
                        row_with_values.sort(key=lambda x: x[0], reverse=True)
                        
                        # 상위 N개 추출
                        for sort_value, row in row_with_values[:top_n]:
                            row_key = tuple(row)
                            if row_key not in added_rows:
                                top_n_rows.append(row)
                                added_rows.add(row_key)
                    except Exception as e:
                        logger.error(f"분야2별 정렬 중 오류: {str(e)}")
                        continue

            # 상위 N개 정렬 추출이 활성화된 경우, 추가로 정렬 및 제한 적용
            if sort_column and sort_order and sort_limit:
                logger.info(f"상위 N개 정렬 추출 적용: {sort_column} {sort_order} 상위 {sort_limit}개")
                final_rows = self._apply_sort_and_limit(top_n_rows, column_mapping, sort_column, sort_order, sort_limit)
                return final_rows

            return top_n_rows

        except Exception as e:
            logger.error(f"상위 N개 필터 적용 실패: {str(e)}")
            # 상위 N개 정렬 추출이 활성화된 경우, 기본 filtered_rows에도 적용
            if sort_column and sort_order and sort_limit:
                logger.info(f"상위 N개 정렬 추출 적용 (fallback): {sort_column} {sort_order} 상위 {sort_limit}개")
                return self._apply_sort_and_limit(filtered_rows, column_mapping, sort_column, sort_order, sort_limit)
            return filtered_rows
    
    def _parse_number(self, value):
        """숫자 파싱 (조회수 등) - 항상 int 값 반환"""
        try:
            if value is None or value == '' or value == 'None':
                return 0
            # 문자열로 변환 후 불필요한 문자 제거
            str_value = str(value).replace(',', '').replace(' ', '').replace('None', '0').strip()
            if not str_value or str_value == '':
                return 0
            # 정수로 변환
            result = int(float(str_value))  # float를 거쳐서 '1.5k' 같은 경우도 처리
            return result
        except (ValueError, AttributeError, TypeError, OverflowError):
            return 0

    def _apply_sort_and_limit(self, rows, column_mapping, sort_column, sort_order, sort_limit):
        """정렬 및 제한 적용"""
        try:
            if not rows:
                return []

            # 정렬 기준 열 찾기
            if sort_column not in column_mapping:
                logger.warning(f"'{sort_column}' 열을 찾을 수 없어 정렬을 건너뜁니다.")
                return rows[:sort_limit] if sort_limit else rows

            col_idx = column_mapping[sort_column] - 1

            # 정렬 가능한 행만 필터링
            valid_rows = [row for row in rows if row is not None and isinstance(row, (list, tuple)) and len(row) > col_idx]

            if not valid_rows:
                return []

            # 정렬 함수 정의
            def get_sort_value(row):
                try:
                    value = row[col_idx] if col_idx < len(row) else None

                    # 조회수, 구독자수, 영상길이는 숫자로 파싱
                    if sort_column in ['조회수', '구독자수', '영상길이']:
                        if value is None or value == '':
                            return 0
                        parsed = self._parse_number(value)
                        return parsed if parsed is not None else 0

                    # 영상 업로드날짜는 날짜로 파싱
                    elif sort_column == '영상 업로드날짜':
                        from datetime import datetime
                        if value is not None and value != '':
                            try:
                                # 날짜 형식 파싱 (예: "2025-11-15" 또는 Excel 날짜 번호)
                                if isinstance(value, (int, float)):
                                    # Excel 날짜 번호인 경우
                                    from datetime import timedelta
                                    base_date = datetime(1899, 12, 30)
                                    return base_date + timedelta(days=value)
                                else:
                                    # 문자열 날짜인 경우
                                    return datetime.strptime(str(value), '%Y-%m-%d')
                            except:
                                return datetime.min
                        return datetime.min

                    # 기타는 문자열로
                    return str(value) if value is not None and value != '' else ''

                except Exception as e:
                    logger.debug(f"정렬 값 추출 실패: {str(e)}")
                    return 0 if sort_column in ['조회수', '구독자수', '영상길이'] else ''

            # 정렬
            reverse = (sort_order == '내림차순')
            sorted_rows = sorted(valid_rows, key=get_sort_value, reverse=reverse)

            # 제한 적용
            return sorted_rows[:sort_limit] if sort_limit else sorted_rows

        except Exception as e:
            logger.error(f"정렬 및 제한 적용 실패: {str(e)}")
            return rows[:sort_limit] if sort_limit else rows

    def extract_filtered_data(self, sheet,
                            field1_filters=None, field2_filters=None, shortform_filters=None,
                            min_views=None, min_subscribers=None, channel_names=None,
                            min_like_ratio=None, start_row=10):
        """조건에 맞는 데이터 추출 (캐싱 사용)"""
        try:
            # 헤더 열 매핑 (캐시 사용)
            column_mapping = self.get_header_columns(sheet, use_cache=True)

            # 캐시 키 생성
            cache_key = f"{sheet.spreadsheet.id}_{sheet.title}_data"

            # 캐시된 데이터 확인 및 사용
            if hasattr(self, '_data_cache') and cache_key in self._data_cache:
                logger.info("캐시된 시트 데이터를 사용합니다")
                all_data = self._data_cache[cache_key]
            else:
                # A열의 마지막 데이터 행 찾기
                last_row = len(sheet.col_values(1))
                if last_row < start_row:
                    logger.info("추출할 데이터가 없습니다.")
                    return []

                logger.info(f"조건부 추출 시작: {start_row}행 ~ {last_row}행 ({last_row - start_row + 1}개 행 검사)")

                # 모든 데이터 가져오기 (벌크 처리) - UNFORMATTED_VALUE로 실제 값 가져오기
                all_data = self.get_all_values_unformatted(sheet)

                # 캐시에 저장
                if not hasattr(self, '_data_cache'):
                    self._data_cache = {}
                self._data_cache[cache_key] = all_data
                logger.info(f"시트 데이터를 캐시에 저장 (행 수: {len(all_data)})")

            # A열의 마지막 데이터 행 찾기 (캐시된 데이터 기준)
            last_row = len(all_data)
            if last_row < start_row:
                logger.info("추출할 데이터가 없습니다.")
                return []
            
            # 필터링할 데이터 (start_row부터)
            data_rows = all_data[start_row-1:last_row]
            
            # 각 필터 조건별 통계
            total_rows = len(data_rows)
            filter_stats = {
                'total': total_rows,
                'field1_matched': 0,
                'field2_matched': 0, 
                'shortform_matched': 0,
                'views_matched': 0,
                'subscribers_matched': 0,
                'channel_matched': 0,
                'like_ratio_matched': 0,
                'final_matched': 0
            }
            
            filtered_rows = []
            
            for row_idx, row_data in enumerate(data_rows, start_row):
                if not any(row_data):  # 빈 행 스킵
                    continue
                
                # 각 조건별로 개별 검사 및 통계 수집
                field1_pass = self._check_field_filter(row_data, column_mapping, '분야1', field1_filters)
                field2_pass = self._check_field_filter(row_data, column_mapping, '분야2', field2_filters)  
                shortform_pass = self._check_field_filter(row_data, column_mapping, '숏폼여부', shortform_filters)
                views_pass = self._check_numeric_filter(row_data, column_mapping, '조회수', min_views)
                subscribers_pass = self._check_numeric_filter(row_data, column_mapping, '구독자수', min_subscribers)
                channel_pass = self._check_channel_filter(row_data, column_mapping, channel_names)
                like_ratio_pass = self._check_percentage_filter(row_data, column_mapping, '조회수 대비 좋아요', min_like_ratio)
                
                # 통계 업데이트
                if field1_pass: filter_stats['field1_matched'] += 1
                if field2_pass: filter_stats['field2_matched'] += 1
                if shortform_pass: filter_stats['shortform_matched'] += 1
                if views_pass: filter_stats['views_matched'] += 1
                if subscribers_pass: filter_stats['subscribers_matched'] += 1
                if channel_pass: filter_stats['channel_matched'] += 1  
                if like_ratio_pass: filter_stats['like_ratio_matched'] += 1
                
                # 모든 조건을 만족하는 경우만 추가
                if (field1_pass and field2_pass and shortform_pass and 
                    views_pass and subscribers_pass and channel_pass and like_ratio_pass):
                    filtered_rows.append(row_data)
                    filter_stats['final_matched'] += 1
            
            # 상세 로그 출력
            logger.info("=" * 60)
            logger.info("🔍 조건부 추출 결과 통계:")
            logger.info(f"📊 전체 검사 행수: {filter_stats['total']}행")
            
            if field1_filters:
                logger.info(f"📌 분야1 필터 ({field1_filters}): {filter_stats['field1_matched']}행 매칭")
            if field2_filters:
                logger.info(f"📌 분야2 필터 ({field2_filters}): {filter_stats['field2_matched']}행 매칭") 
            if shortform_filters:
                logger.info(f"📌 숏폼여부 필터 ({shortform_filters}): {filter_stats['shortform_matched']}행 매칭")
            if min_views:
                logger.info(f"📌 조회수 필터 ({min_views:,} 이상): {filter_stats['views_matched']}행 매칭")
            if min_subscribers:
                logger.info(f"📌 구독자수 필터 ({min_subscribers:,} 이상): {filter_stats['subscribers_matched']}행 매칭")
            if channel_names:
                logger.info(f"📌 채널명 필터 ({channel_names}): {filter_stats['channel_matched']}행 매칭")
            if min_like_ratio:
                logger.info(f"📌 좋아요 비율 필터 ({min_like_ratio}% 이상): {filter_stats['like_ratio_matched']}행 매칭")
                
            logger.info(f"🎯 최종 결과: {filter_stats['final_matched']}행이 모든 조건을 만족")
            logger.info("=" * 60)
            
            return filtered_rows
            
        except Exception as e:
            logger.error(f"데이터 추출 실패: {str(e)}")
            raise
    
    def _check_field_filter(self, row_data, column_mapping, field_name, filters):
        """필드 필터 조건 확인"""
        if not filters:  # 필터가 없으면 통과
            return True
        
        if field_name not in column_mapping:
            return False  # 해당 열이 없으면 필터 조건 불만족
            
        col_idx = column_mapping[field_name] - 1  # 0-based index
        if col_idx >= len(row_data):
            return False  # 데이터 범위를 벗어나면 필터 조건 불만족
            
        cell_value = str(row_data[col_idx]).strip()
        if not cell_value:
            return False  # 필터가 있는데 빈 값이면 조건 불만족
            
        # 정확한 값 매칭 (부분 문자열이 아닌 정확한 값 또는 쉼표로 구분된 값 중 하나)
        cell_values = [val.strip() for val in cell_value.split(',')]
        return any(filter_val.strip() in cell_values for filter_val in filters)
    
    def _check_numeric_filter(self, row_data, column_mapping, field_name, min_value):
        """숫자 필터 조건 확인"""
        if min_value is None:  # 필터가 없으면 통과
            return True
            
        if field_name not in column_mapping:
            return True
            
        col_idx = column_mapping[field_name] - 1
        if col_idx >= len(row_data):
            return True
            
        try:
            actual_value = self._parse_number(row_data[col_idx])
            if actual_value is None:
                return True  # 파싱 실패시 통과
            return actual_value >= min_value
        except:
            return True
    
    def _check_channel_filter(self, row_data, column_mapping, channel_names):
        """채널명 필터 조건 확인"""
        if not channel_names:
            return True
            
        if '채널명' not in column_mapping:
            return True
            
        col_idx = column_mapping['채널명'] - 1
        if col_idx >= len(row_data):
            return True
            
        cell_value = str(row_data[col_idx]).strip()
        if not cell_value:
            return True
            
        return any(channel_name.strip() in cell_value for channel_name in channel_names)
    
    def _check_percentage_filter(self, row_data, column_mapping, field_name, min_percentage):
        """퍼센트 필터 조건 확인"""
        if min_percentage is None:
            return True
            
        if field_name not in column_mapping:
            return True
            
        col_idx = column_mapping[field_name] - 1
        if col_idx >= len(row_data):
            return True
            
        try:
            actual_value = self._parse_percentage(row_data[col_idx])
            if actual_value is None:
                return True
            return actual_value >= min_percentage
        except:
            return True
    
    def _check_duration_filter(self, row_data, column_mapping, min_duration):
        """영상길이 필터 조건 확인"""
        if min_duration is None:
            return True

        if '영상길이' not in column_mapping:
            return True

        col_idx = column_mapping['영상길이'] - 1
        if col_idx >= len(row_data):
            return True

        try:
            duration_str = str(row_data[col_idx]).strip()
            actual_duration = self.parse_duration_to_seconds(duration_str)
            if actual_duration == 0:
                return True  # 파싱 실패시 통과
            return actual_duration >= min_duration
        except:
            return True

    def _check_upload_date_filter(self, row_data, column_mapping, max_upload_days):
        """수집날짜 경과일 필터 조건 확인"""
        if max_upload_days is None:
            return True

        # '영상 업로드날짜' 또는 '영상 업로드 날짜' 찾기
        upload_date_col = None

        # 먼저 column_mapping에서 찾기
        if '영상 업로드날짜' in column_mapping:
            upload_date_col = column_mapping['영상 업로드날짜']
            if hasattr(self, '_upload_date_filter_first_call') and self._upload_date_filter_first_call:
                logger.info(f"📅 업로드 날짜 필터: '영상 업로드날짜' 열 찾음 (열 {upload_date_col}), 조건={max_upload_days}일 이내")
                self._upload_date_filter_first_call = False
        elif '영상 업로드 날짜' in column_mapping:
            upload_date_col = column_mapping['영상 업로드 날짜']
            if hasattr(self, '_upload_date_filter_first_call') and self._upload_date_filter_first_call:
                logger.info(f"📅 업로드 날짜 필터: '영상 업로드 날짜' 열 찾음 (열 {upload_date_col}), 조건={max_upload_days}일 이내")
                self._upload_date_filter_first_call = False
        else:
            # 매핑에 없으면 동적으로 찾기
            for key in column_mapping.keys():
                if '업로드' in key and '날짜' in key and '이후' not in key:
                    upload_date_col = column_mapping[key]
                    if hasattr(self, '_upload_date_filter_first_call') and self._upload_date_filter_first_call:
                        logger.info(f"📅 업로드 날짜 필터: 동적 검색으로 '{key}' 열 찾음 (열 {upload_date_col}), 조건={max_upload_days}일 이내")
                        self._upload_date_filter_first_call = False
                    break

        if upload_date_col is None:
            if hasattr(self, '_upload_date_filter_first_call') and self._upload_date_filter_first_call:
                logger.warning("📅 업로드 날짜 필터: 업로드 날짜 열을 찾을 수 없음 - 모든 행 통과")
            return True

        col_idx = upload_date_col - 1
        if col_idx >= len(row_data):
            return True

        try:
            date_str = str(row_data[col_idx]).strip()

            upload_date = self.parse_upload_date(date_str)
            if upload_date is None:
                if hasattr(self, '_upload_date_sample_logged') and self._upload_date_sample_logged < 3:
                    logger.debug(f"📅 업로드 날짜 필터: 날짜 파싱 실패 ('{date_str}') - 통과")
                    self._upload_date_sample_logged += 1
                return True  # 파싱 실패시 통과

            # 오늘 날짜와 비교
            today = datetime.now()
            days_diff = (today - upload_date).days
            result = days_diff <= max_upload_days

            # 처음 3개 샘플만 상세 로그
            if hasattr(self, '_upload_date_sample_logged') and self._upload_date_sample_logged < 3:
                logger.info(f"📅 업로드 날짜 필터 샘플: 날짜={upload_date.strftime('%Y-%m-%d')}, 경과일={days_diff}일, 조건={max_upload_days}일 이내, 결과={'통과' if result else '제외'}")
                self._upload_date_sample_logged += 1

            return result
        except Exception as e:
            if hasattr(self, '_upload_date_sample_logged') and self._upload_date_sample_logged < 3:
                logger.error(f"📅 업로드 날짜 필터: 에러 발생 - {str(e)} - 통과")
                self._upload_date_sample_logged += 1
            return True

    def _check_video_upload_date_filter(self, row_data, column_mapping, max_video_upload_days):
        """영상 업로드날짜 필터 조건 확인"""
        if max_video_upload_days is None:
            return True

        # '영상 업로드날짜' 또는 '영상 업로드 날짜' 찾기
        upload_date_col = None

        # 먼저 column_mapping에서 찾기
        if '영상 업로드날짜' in column_mapping:
            upload_date_col = column_mapping['영상 업로드날짜']
            if hasattr(self, '_video_upload_date_filter_first_call') and self._video_upload_date_filter_first_call:
                logger.info(f"📅 영상 업로드날짜 필터: '영상 업로드날짜' 열 찾음 (열 {upload_date_col}), 조건={max_video_upload_days}일 이내")
                self._video_upload_date_filter_first_call = False
        elif '영상 업로드 날짜' in column_mapping:
            upload_date_col = column_mapping['영상 업로드 날짜']
            if hasattr(self, '_video_upload_date_filter_first_call') and self._video_upload_date_filter_first_call:
                logger.info(f"📅 영상 업로드날짜 필터: '영상 업로드 날짜' 열 찾음 (열 {upload_date_col}), 조건={max_video_upload_days}일 이내")
                self._video_upload_date_filter_first_call = False
        else:
            # 매핑에 없으면 동적으로 찾기
            for key in column_mapping.keys():
                if '영상' in key and '업로드' in key and '날짜' in key and '이후' not in key:
                    upload_date_col = column_mapping[key]
                    if hasattr(self, '_video_upload_date_filter_first_call') and self._video_upload_date_filter_first_call:
                        logger.info(f"📅 영상 업로드날짜 필터: 동적 검색으로 '{key}' 열 찾음 (열 {upload_date_col}), 조건={max_video_upload_days}일 이내")
                        self._video_upload_date_filter_first_call = False
                    break

        if upload_date_col is None:
            if hasattr(self, '_video_upload_date_filter_first_call') and self._video_upload_date_filter_first_call:
                logger.warning("📅 영상 업로드날짜 필터: 업로드 날짜 열을 찾을 수 없음 - 모든 행 통과")
            return True

        col_idx = upload_date_col - 1
        if col_idx >= len(row_data):
            return True

        try:
            date_str = str(row_data[col_idx]).strip()

            upload_date = self.parse_upload_date(date_str)
            if upload_date is None:
                if hasattr(self, '_video_upload_date_sample_logged') and self._video_upload_date_sample_logged < 3:
                    logger.debug(f"📅 영상 업로드날짜 필터: 날짜 파싱 실패 ('{date_str}') - 통과")
                    self._video_upload_date_sample_logged += 1
                return True  # 파싱 실패시 통과

            # 오늘 날짜와 비교
            today = datetime.now()
            days_diff = (today - upload_date).days
            result = days_diff <= max_video_upload_days

            # 처음 3개 샘플만 상세 로그
            if hasattr(self, '_video_upload_date_sample_logged') and self._video_upload_date_sample_logged < 3:
                logger.info(f"📅 영상 업로드날짜 필터 샘플: 날짜={upload_date.strftime('%Y-%m-%d')}, 경과일={days_diff}일, 조건={max_video_upload_days}일 이내, 결과={'통과' if result else '제외'}")
                self._video_upload_date_sample_logged += 1

            return result
        except Exception as e:
            if hasattr(self, '_video_upload_date_sample_logged') and self._video_upload_date_sample_logged < 3:
                logger.error(f"📅 영상 업로드날짜 필터: 에러 발생 - {str(e)} - 통과")
                self._video_upload_date_sample_logged += 1
            return True

    def _check_benchmarking_filter(self, row_data, column_mapping, benchmarking_only):
        """벤치마킹 채널여부 필터 조건 확인"""
        if not benchmarking_only:
            return True

        if '벤치마킹 채널여부' not in column_mapping:
            return True

        col_idx = column_mapping['벤치마킹 채널여부'] - 1
        if col_idx >= len(row_data):
            return False  # 체크박스가 활성화되었지만 열이 없으면 제외

        try:
            cell_value = str(row_data[col_idx]).strip()
            # 빈 값이 아니면 통과
            return bool(cell_value)
        except:
            return False

    def _check_script_exists_filter(self, row_data, column_mapping, script_exists_only):
        """대본유무 필터 조건 확인"""
        if not script_exists_only:
            return True

        if '대본유무' not in column_mapping:
            return True

        col_idx = column_mapping['대본유무'] - 1
        if col_idx >= len(row_data):
            return False

        try:
            cell_value = str(row_data[col_idx]).strip()
            # 빈 값이 아니면 통과
            return bool(cell_value)
        except:
            return False

    def _check_hook_subtitle_exists_filter(self, row_data, column_mapping, hook_subtitle_exists_only):
        """후킹자막 유무 필터 조건 확인"""
        if not hook_subtitle_exists_only:
            return True

        if '후킹자막 유무' not in column_mapping:
            return True

        col_idx = column_mapping['후킹자막 유무'] - 1
        if col_idx >= len(row_data):
            return False

        try:
            cell_value = str(row_data[col_idx]).strip()
            # 빈 값이 아니면 통과
            return bool(cell_value)
        except:
            return False

    def _parse_number(self, value):
        """문자열에서 숫자 추출 (쉼표 제거 등)"""
        if not value:
            return None

        try:
            # 쉼표, 공백 제거 후 숫자만 추출
            cleaned = re.sub(r'[,\s]', '', str(value))
            # 숫자 부분만 추출
            match = re.search(r'\d+', cleaned)
            if match:
                return int(match.group())
        except:
            pass
        
        return None
    
    def _parse_percentage(self, value):
        """문자열에서 퍼센트값 추출"""
        if not value:
            return None
        
        try:
            # % 기호 제거하고 숫자 추출
            cleaned = str(value).replace('%', '').strip()
            return float(cleaned)
        except:
            pass
        
        return None
    
    def _apply_date_format_to_column(self, sheet, column_index, start_row=1, end_row=None):
        """
        특정 열에 날짜 표시 형식(yyyy-mm-dd)을 적용

        Args:
            sheet: 대상 시트
            column_index: 열 인덱스 (1-based)
            start_row: 시작 행 (1-based, 기본값=1)
            end_row: 끝 행 (1-based, None이면 시트 전체)
        """
        try:
            sheet_id = sheet.id
            spreadsheet = sheet.spreadsheet

            # 끝 행이 지정되지 않으면 시트의 최대 행 사용
            if end_row is None:
                end_row = sheet.row_count

            # yyyy-mm-dd 형식 설정
            date_format_request = {
                'repeatCell': {
                    'range': {
                        'sheetId': sheet_id,
                        'startRowIndex': start_row - 1,  # 0-based
                        'endRowIndex': end_row,  # 0-based (exclusive)
                        'startColumnIndex': column_index - 1,  # 0-based
                        'endColumnIndex': column_index  # 0-based (exclusive)
                    },
                    'cell': {
                        'userEnteredFormat': {
                            'numberFormat': {
                                'type': 'DATE',
                                'pattern': 'yyyy-mm-dd'
                            }
                        }
                    },
                    'fields': 'userEnteredFormat.numberFormat'
                }
            }

            spreadsheet.batch_update({'requests': [date_format_request]})
            logger.debug(f"✅ {sheet.title} 시트의 {column_index}열에 날짜 형식(yyyy-mm-dd) 적용 완료")

        except Exception as e:
            logger.warning(f"날짜 형식 적용 실패 (열: {column_index}): {e}")

    def _copy_number_formats(self, source_sheet, target_sheet, target_start_row, num_rows):
        """원본 시트의 표시 형식을 대상 시트에 복사 (수집날짜는 yyyy-mm-dd로 강제)"""
        try:
            # 원본 시트의 표시 형식 정보 가져오기
            source_sheet_id = source_sheet.id
            target_sheet_id = target_sheet.id

            # 원본 시트의 10행 데이터 행의 표시 형식 가져오기
            spreadsheet = source_sheet.spreadsheet

            # Sheets API를 사용하여 원본 시트의 셀 형식 정보 가져오기
            result = spreadsheet.fetch_sheet_metadata({
                'includeGridData': True,
                'ranges': [f"'{source_sheet.title}'!A10:BH10"]  # 10행의 형식 정보
            })

            # 원본 시트 데이터 찾기
            source_formats = None
            for sheet_data in result.get('sheets', []):
                if sheet_data.get('properties', {}).get('sheetId') == source_sheet_id:
                    grid_data = sheet_data.get('data', [])
                    if grid_data and grid_data[0].get('rowData'):
                        row_data = grid_data[0]['rowData'][0]
                        if 'values' in row_data:
                            source_formats = row_data['values']
                    break

            if not source_formats:
                logger.warning("원본 시트의 표시 형식을 찾을 수 없습니다")
                return

            # 수집날짜 열 찾기 (1행 헤더에서)
            target_headers = target_sheet.row_values(1)
            collection_date_col = None
            for idx, header in enumerate(target_headers, 1):
                if '수집날짜' in str(header):
                    collection_date_col = idx
                    break

            # 대상 시트에 적용할 표시 형식 요청 생성
            requests = []

            # 각 열에 대해 표시 형식 복사
            for col_idx, cell_format in enumerate(source_formats):
                col_number = col_idx + 1  # 1-based

                # 수집날짜 열인 경우 yyyy-mm-dd 형식 강제 적용
                if collection_date_col and col_number == collection_date_col:
                    number_format = {
                        'type': 'DATE',
                        'pattern': 'yyyy-mm-dd'
                    }
                    logger.debug(f"수집날짜 열({col_number})에 yyyy-mm-dd 형식 강제 적용")
                elif 'userEnteredFormat' in cell_format:
                    user_format = cell_format['userEnteredFormat']
                    # numberFormat이 있는 경우만 복사
                    if 'numberFormat' not in user_format:
                        continue
                    number_format = user_format['numberFormat']
                else:
                    continue

                # 대상 시트의 해당 열 전체에 표시 형식 적용
                requests.append({
                    'repeatCell': {
                        'range': {
                            'sheetId': target_sheet_id,
                            'startRowIndex': target_start_row - 1,  # 0-based
                            'endRowIndex': target_start_row + num_rows - 1,
                            'startColumnIndex': col_idx,
                            'endColumnIndex': col_idx + 1
                        },
                        'cell': {
                            'userEnteredFormat': {
                                'numberFormat': number_format
                            }
                        },
                        'fields': 'userEnteredFormat.numberFormat'
                    }
                })

            # 일괄 적용
            if requests:
                spreadsheet.batch_update({'requests': requests})
                logger.info(f"✅ {len(requests)}개 열의 표시 형식 적용 완료")
            else:
                logger.info("적용할 표시 형식이 없습니다")

        except Exception as e:
            logger.error(f"표시 형식 복사 실패: {str(e)}")
            raise

    def _copy_cell_formats_for_updates(self, source_sheet, target_sheet, update_ranges):
        """
        업데이트된 셀들의 표시 형식을 원본에서 타겟으로 복사

        Args:
            source_sheet: 원본 시트
            target_sheet: 타겟 시트
            update_ranges: 업데이트된 범위 리스트 [{'range': 'A10', 'source_col': 1, 'source_row': 10}, ...]
        """
        try:
            if not update_ranges:
                return

            source_sheet_id = source_sheet.id
            target_sheet_id = target_sheet.id
            spreadsheet = source_sheet.spreadsheet

            # 원본 시트의 셀 형식 정보를 한 번에 가져오기 위해 범위 그룹화
            # 행별로 그룹화 (같은 행의 셀들을 한 번에 조회)
            rows_to_fetch = set()
            for item in update_ranges:
                if 'source_row' in item:
                    rows_to_fetch.add(item['source_row'])

            if not rows_to_fetch:
                return

            # 원본 시트의 형식 정보 가져오기 (필요한 행들만)
            min_row = min(rows_to_fetch)
            max_row = max(rows_to_fetch)

            result = spreadsheet.fetch_sheet_metadata({
                'includeGridData': True,
                'ranges': [f"'{source_sheet.title}'!A{min_row}:BH{max_row}"]
            })

            # 원본 시트 데이터 찾기
            source_formats_by_row = {}  # {row_num: [cell_formats]}
            for sheet_data in result.get('sheets', []):
                if sheet_data.get('properties', {}).get('sheetId') == source_sheet_id:
                    grid_data = sheet_data.get('data', [])
                    if grid_data and 'rowData' in grid_data[0]:
                        for idx, row_data in enumerate(grid_data[0]['rowData']):
                            row_num = min_row + idx
                            if 'values' in row_data:
                                source_formats_by_row[row_num] = row_data['values']
                    break

            if not source_formats_by_row:
                logger.warning("원본 시트의 표시 형식을 찾을 수 없습니다")
                return

            # 타겟 시트에 적용할 표시 형식 요청 생성
            requests = []

            for item in update_ranges:
                source_row = item.get('source_row')
                source_col = item.get('source_col')
                target_range = item.get('range')  # 예: 'Sheet1!A10'

                if not source_row or not source_col or not target_range:
                    continue

                # 원본 행의 형식 정보 가져오기
                if source_row not in source_formats_by_row:
                    continue

                source_row_formats = source_formats_by_row[source_row]

                # 원본 열의 형식 정보 (0-based index)
                if len(source_row_formats) <= source_col - 1:
                    continue

                cell_format = source_row_formats[source_col - 1]

                if 'userEnteredFormat' in cell_format:
                    user_format = cell_format['userEnteredFormat']

                    # numberFormat이 있는 경우만 복사
                    if 'numberFormat' in user_format:
                        number_format = user_format['numberFormat']

                        # 타겟 범위 파싱 (예: 'Sheet1!A10')
                        if '!' in target_range:
                            target_range = target_range.split('!')[-1]

                        # A1 표기법을 행/열 인덱스로 변환
                        col_letter = ''.join(filter(str.isalpha, target_range))
                        row_num = int(''.join(filter(str.isdigit, target_range)))

                        target_col_idx = self._col_letter_to_num(col_letter) - 1  # 0-based

                        requests.append({
                            'repeatCell': {
                                'range': {
                                    'sheetId': target_sheet_id,
                                    'startRowIndex': row_num - 1,
                                    'endRowIndex': row_num,
                                    'startColumnIndex': target_col_idx,
                                    'endColumnIndex': target_col_idx + 1
                                },
                                'cell': {
                                    'userEnteredFormat': {
                                        'numberFormat': number_format
                                    }
                                },
                                'fields': 'userEnteredFormat.numberFormat'
                            }
                        })

            # 일괄 적용
            if requests:
                spreadsheet.batch_update({'requests': requests})
                logger.info(f"✅ {len(requests)}개 셀의 표시 형식 적용 완료")

        except Exception as e:
            logger.error(f"셀 표시 형식 복사 실패: {str(e)}")
            # 형식 복사 실패해도 데이터는 업데이트되었으므로 에러를 던지지 않음
            logger.warning("표시 형식 복사는 실패했지만 데이터 업데이트는 완료되었습니다")

    def parse_video_duration_to_seconds(self, duration_str):
        """
        영상길이 텍스트를 초로 변환

        Args:
            duration_str: "56초", "1분", "1분 30초" 등의 텍스트

        Returns:
            int: 총 초 수, 파싱 실패 시 0
        """
        if not duration_str or not isinstance(duration_str, str):
            return 0

        try:
            total_seconds = 0
            duration_str = str(duration_str).strip()

            # 시간 파싱
            if '시간' in duration_str:
                import re
                hours_match = re.search(r'(\d+)\s*시간', duration_str)
                if hours_match:
                    total_seconds += int(hours_match.group(1)) * 3600

            # 분 파싱
            if '분' in duration_str:
                import re
                minutes_match = re.search(r'(\d+)\s*분', duration_str)
                if minutes_match:
                    total_seconds += int(minutes_match.group(1)) * 60

            # 초 파싱
            if '초' in duration_str:
                import re
                seconds_match = re.search(r'(\d+)\s*초', duration_str)
                if seconds_match:
                    total_seconds += int(seconds_match.group(1))

            return total_seconds
        except Exception as e:
            logger.warning(f"영상길이 파싱 실패: '{duration_str}' - {e}")
            return 0

    def seconds_to_duration_text(self, seconds):
        """
        초를 영상길이 텍스트로 변환

        Args:
            seconds: 총 초 수

        Returns:
            str: "1분 30초" 형식의 텍스트
        """
        if not seconds or seconds <= 0:
            return ""

        try:
            hours = seconds // 3600
            minutes = (seconds % 3600) // 60
            secs = seconds % 60

            parts = []
            if hours > 0:
                parts.append(f"{hours}시간")
            if minutes > 0:
                parts.append(f"{minutes}분")
            if secs > 0 or len(parts) == 0:
                parts.append(f"{secs}초")

            return " ".join(parts)
        except Exception as e:
            logger.warning(f"초를 텍스트로 변환 실패: {seconds} - {e}")
            return ""

    def parse_view_count(self, view_str):
        """
        조회수 문자열을 숫자로 변환

        Args:
            view_str: "1,234,567" 또는 "1234567" 형식의 조회수

        Returns:
            int: 조회수, 파싱 실패 시 0
        """
        if not view_str:
            return 0

        try:
            # 쉼표 제거 후 숫자로 변환
            view_str = str(view_str).replace(',', '').strip()
            return int(float(view_str))
        except (ValueError, TypeError):
            logger.warning(f"조회수 파싱 실패: '{view_str}'")
            return 0

    def parse_duration_to_seconds(self, duration_str):
        """
        영상길이 문자열을 초 단위로 변환

        Args:
            duration_str: "56초", "1분", "1분 30초", "1시간 5분" 등의 형식

        Returns:
            int: 초 단위 시간, 파싱 실패 시 0
        """
        if not duration_str:
            return 0

        try:
            duration_str = str(duration_str).strip()
            total_seconds = 0

            # 시간 파싱 (예: "1시간")
            hour_match = re.search(r'(\d+)\s*시간', duration_str)
            if hour_match:
                total_seconds += int(hour_match.group(1)) * 3600

            # 분 파싱 (예: "5분")
            minute_match = re.search(r'(\d+)\s*분', duration_str)
            if minute_match:
                total_seconds += int(minute_match.group(1)) * 60

            # 초 파싱 (예: "30초")
            second_match = re.search(r'(\d+)\s*초', duration_str)
            if second_match:
                total_seconds += int(second_match.group(1))

            return total_seconds
        except (ValueError, TypeError, AttributeError) as e:
            logger.warning(f"영상길이 파싱 실패: '{duration_str}' - {e}")
            return 0

    def parse_upload_date(self, date_str):
        """
        업로드 날짜 문자열을 datetime 객체로 변환

        Args:
            date_str: "2025-11-12 화요일  7:05:26", "2025. 4. 8 오후 11:25:15", 또는 시리얼 날짜 (45755.97587) 형식

        Returns:
            datetime: 파싱된 날짜, 실패 시 None
        """
        if not date_str:
            return None

        try:
            date_str = str(date_str).strip()

            # 1. 시리얼 날짜 형식 (예: 45755.97587)
            try:
                serial_date = float(date_str)
                if serial_date > 0:
                    # Excel/Google Sheets 시리얼 날짜 (1899-12-30 기준)
                    base_date = datetime(1899, 12, 30)
                    actual_date = base_date + timedelta(days=serial_date)
                    return actual_date
            except (ValueError, TypeError):
                pass

            # 2. "2025-11-12 화요일  7:05:26" 형식에서 날짜 부분만 추출
            date_match = re.search(r'(\d{4})-(\d{2})-(\d{2})', date_str)
            if date_match:
                year = int(date_match.group(1))
                month = int(date_match.group(2))
                day = int(date_match.group(3))
                return datetime(year, month, day)

            # 3. "2025. 4. 8" 형식
            date_match = re.search(r'(\d{4})\.\s*(\d{1,2})\.\s*(\d{1,2})', date_str)
            if date_match:
                year = int(date_match.group(1))
                month = int(date_match.group(2))
                day = int(date_match.group(3))
                return datetime(year, month, day)

            return None
        except (ValueError, TypeError, AttributeError) as e:
            logger.warning(f"업로드 날짜 파싱 실패: '{date_str}' - {e}")
            return None

    def format_number_with_comma(self, number):
        """
        숫자를 쉼표가 포함된 문자열로 변환

        Args:
            number: 숫자

        Returns:
            str: "1,234,567" 형식의 문자열
        """
        if not number or number <= 0:
            return "0"

        try:
            return f"{int(number):,}"
        except (ValueError, TypeError):
            return "0"

    def find_header_column(self, headers, target_header, allow_partial=True):
        """
        헤더 리스트에서 대상 헤더를 찾아 열 번호 반환 (1-based)

        매칭 우선순위:
        1. 넘버링 제거 후 완전 일치 (최우선)
        2. 부분 일치 (allow_partial=True인 경우만)

        Args:
            headers: 헤더 리스트 (1행 데이터)
            target_header: 찾을 헤더 이름 (넘버링 제외)
            allow_partial: 부분 일치 허용 여부 (기본값: True)

        Returns:
            int: 열 번호 (1-based), 찾지 못하면 None

        Examples:
            >>> headers = ['1. 채널 ID', '2. 가져왔는지 여부', '영상 업로드날짜', '영상 업로드 이후 수집날짜까지 기간']
            >>> find_header_column(headers, '영상 업로드날짜')
            3  # 완전 일치 우선
        """
        import re

        # 대상 헤더 정규화 (띄어쓰기 통일)
        target_normalized = ' '.join(target_header.split())

        # 1단계: 완전 일치 찾기
        for i, h in enumerate(headers, 1):
            # 넘버링 제거 및 정규화
            clean_h = re.sub(r'^\d+[\.\s]*', '', str(h)).strip()
            clean_h_normalized = ' '.join(clean_h.split())

            if clean_h_normalized == target_normalized:
                logger.debug(f"🎯 헤더 완전 일치: '{target_header}' → 열 {i} ('{h}')")
                return i

        # 2단계: 부분 일치 찾기 (allow_partial=True인 경우만)
        if allow_partial:
            for i, h in enumerate(headers, 1):
                clean_h = re.sub(r'^\d+[\.\s]*', '', str(h)).strip()
                clean_h_normalized = ' '.join(clean_h.split())

                # 대상이 헤더에 포함되거나, 헤더가 대상에 포함되는 경우
                if target_normalized in clean_h_normalized or clean_h_normalized in target_normalized:
                    logger.debug(f"📍 헤더 부분 일치: '{target_header}' → 열 {i} ('{h}')")
                    return i

        logger.debug(f"❌ 헤더를 찾을 수 없음: '{target_header}'")
        return None

    def find_multiple_headers(self, headers, target_headers_dict, allow_partial=True):
        """
        여러 헤더를 한 번에 찾아 딕셔너리로 반환

        Args:
            headers: 헤더 리스트 (1행 데이터)
            target_headers_dict: {키: 헤더이름} 딕셔너리
            allow_partial: 부분 일치 허용 여부

        Returns:
            dict: {키: 열번호(1-based)} 딕셔너리, 찾지 못한 키는 None

        Examples:
            >>> headers = ['1. 채널 ID', '2. 채널명', '3. 조회수']
            >>> targets = {'id': '채널 ID', 'name': '채널명', 'views': '조회수'}
            >>> find_multiple_headers(headers, targets)
            {'id': 1, 'name': 2, 'views': 3}
        """
        result = {}
        for key, target_header in target_headers_dict.items():
            result[key] = self.find_header_column(headers, target_header, allow_partial)
        return result

    def update_channel_list_from_video_sheet(self, spreadsheet_url, video_sheet_name, days_threshold=None, progress_callback=None):
        """
        영상 시트의 최신 데이터로 채널 리스트를 업데이트 (대규모 데이터 최적화)

        Args:
            spreadsheet_url: 스프레드시트 URL
            video_sheet_name: 영상 시트 이름 (예: "영상 리스트")
            days_threshold: 기간 필터 (일수). None이면 전체, 숫자면 해당 일수 이상 차이나는 채널만 업데이트
            progress_callback: 진행률 콜백 함수 (current, total, message)
        """
        try:
            logger.info(f"🔄 채널 리스트 업데이트 시작 (영상 시트: {video_sheet_name})")
            if days_threshold:
                logger.info(f"📅 기간 필터: {days_threshold}일 이상 차이나는 채널만 업데이트")

            # 스프레드시트 열기
            spreadsheet = self.client.open_by_url(spreadsheet_url)
            channel_sheet = spreadsheet.worksheet("채널 리스트")
            video_sheet = spreadsheet.worksheet(video_sheet_name)

            # 헤더 매핑 정의 (채널 리스트 헤더 -> 영상 시트 헤더)
            header_mapping = {
                # 채널 리스트 헤더: 영상 시트 헤더
                '구독자수': '구독자수',
                '채널전체 영상갯수': '영상갯수',
                '채널전체 조회수': '채널 전체 조회수',
                '영상당 평균 조회수(전투력)': '영상당 평균 조회수',
                '채널국가': '채널국가',
                '사용언어': '사용언어',
                '개설일': '채널 개설일',
            }

            # 평균 영상길이, 수집한 영상 평균 조회수, 최근 30개 영상 평균 조회수는 채널별로 계산하므로 별도 처리
            avg_duration_field = '평균 영상길이'
            collected_avg_views_field = '수집한 영상 평균 조회수'
            recent_30_avg_views_field = '최근 30개 영상 평균 조회수'

            # 전역함수 열 (9행에 함수가 있는 열 - 직접 업데이트 금지!)
            # 이 열들은 header_mapping에 포함하지 않으므로 자동으로 보호됨
            formula_columns = {
                '구독자 대비 조회수배율',  # 9행 함수: 채널 전체 조회수 / 구독자수
                '콘텐츠파워',              # 9행 함수: 복합 지표 계산
                '공정성과지수',            # 9행 함수: 복합 지표 계산
                '수집날짜 경과일'          # 9행 함수: 수집날짜로부터 경과한 일수
            }

            # 분야1, 분야2는 채널 리스트 → 영상 시트 단방향 업데이트
            category_fields = ['분야1', '분야2']

            # 헤더 열 인덱스 찾기
            channel_headers = channel_sheet.row_values(1)
            video_headers = video_sheet.row_values(1)

            logger.info(f"📋 채널 리스트 헤더: {len(channel_headers)}개")
            logger.info(f"📋 영상 시트 헤더: {len(video_headers)}개")

            # 채널 리스트와 영상 시트의 헤더 인덱스 매핑
            channel_col_map = {}
            video_col_map = {}
            category_col_map = {}  # 분야1, 분야2 매핑

            for ch_header, vid_header in header_mapping.items():
                # 채널 리스트 헤더 찾기 (넘버링 제거)
                for i, h in enumerate(channel_headers, 1):
                    clean_h = re.sub(r'^\d+[\.\s]*', '', str(h)).strip()
                    if clean_h == ch_header:
                        channel_col_map[ch_header] = i
                        break

                # 영상 시트 헤더 찾기 (넘버링 제거)
                for i, h in enumerate(video_headers, 1):
                    clean_h = re.sub(r'^\d+[\.\s]*', '', str(h)).strip()
                    if clean_h == vid_header:
                        video_col_map[vid_header] = i
                        break

            # 분야1, 분야2 헤더 찾기
            for category_field in category_fields:
                # 채널 리스트에서 찾기
                for i, h in enumerate(channel_headers, 1):
                    clean_h = re.sub(r'^\d+[\.\s]*', '', str(h)).strip()
                    if clean_h == category_field:
                        if 'channel' not in category_col_map:
                            category_col_map['channel'] = {}
                        category_col_map['channel'][category_field] = i
                        break

                # 영상 시트에서 찾기
                for i, h in enumerate(video_headers, 1):
                    clean_h = re.sub(r'^\d+[\.\s]*', '', str(h)).strip()
                    if clean_h == category_field:
                        if 'video' not in category_col_map:
                            category_col_map['video'] = {}
                        category_col_map['video'][category_field] = i
                        break

            # 평균 영상길이 헤더 찾기
            channel_avg_duration_col = None
            for i, h in enumerate(channel_headers, 1):
                clean_h = re.sub(r'^\d+[\.\s]*', '', str(h)).strip()
                if clean_h == avg_duration_field:
                    channel_avg_duration_col = i
                    break

            # 수집한 영상 평균 조회수 헤더 찾기
            channel_collected_avg_views_col = None
            for i, h in enumerate(channel_headers, 1):
                clean_h = re.sub(r'^\d+[\.\s]*', '', str(h)).strip()
                if clean_h == collected_avg_views_field:
                    channel_collected_avg_views_col = i
                    break

            # 최근 30개 영상 평균 조회수 헤더 찾기
            channel_recent_30_avg_views_col = None
            for i, h in enumerate(channel_headers, 1):
                clean_h = re.sub(r'^\d+[\.\s]*', '', str(h)).strip()
                if clean_h == recent_30_avg_views_field:
                    channel_recent_30_avg_views_col = i
                    break

            # 최근 30개 영상 중위 조회수 헤더 찾기
            recent_30_median_views_field = '최근 30개 영상 중위 조회수'
            channel_recent_30_median_views_col = None
            for i, h in enumerate(channel_headers, 1):
                clean_h = re.sub(r'^\d+[\.\s]*', '', str(h)).strip()
                if clean_h == recent_30_median_views_field:
                    channel_recent_30_median_views_col = i
                    break

            # 벤치마킹 채널여부 헤더 찾기 (채널 리스트와 영상 시트 모두)
            benchmarking_channel_field = '벤치마킹 채널여부'
            channel_benchmarking_col = None
            for i, h in enumerate(channel_headers, 1):
                clean_h = re.sub(r'^\d+[\.\s]*', '', str(h)).strip()
                if clean_h == benchmarking_channel_field:
                    channel_benchmarking_col = i
                    break

            video_benchmarking_col = None
            for i, h in enumerate(video_headers, 1):
                clean_h = re.sub(r'^\d+[\.\s]*', '', str(h)).strip()
                if clean_h == benchmarking_channel_field:
                    video_benchmarking_col = i
                    break

            # 영상길이 헤더 찾기 (영상 시트)
            video_duration_col = None
            for i, h in enumerate(video_headers, 1):
                clean_h = re.sub(r'^\d+[\.\s]*', '', str(h)).strip()
                if clean_h == '영상길이':
                    video_duration_col = i
                    break

            # 조회수 헤더 찾기 (영상 시트)
            video_views_col = None
            for i, h in enumerate(video_headers, 1):
                clean_h = re.sub(r'^\d+[\.\s]*', '', str(h)).strip()
                if clean_h == '조회수':
                    video_views_col = i
                    break

            logger.info(f"✅ 매핑된 채널 리스트 열: {channel_col_map}")
            logger.info(f"✅ 매핑된 영상 시트 열: {video_col_map}")
            logger.info(f"✅ 매핑된 분야 열: {category_col_map}")
            if channel_avg_duration_col:
                logger.info(f"✅ 채널 리스트 평균 영상길이 열: {channel_avg_duration_col}")
            if channel_collected_avg_views_col:
                logger.info(f"✅ 채널 리스트 수집한 영상 평균 조회수 열: {channel_collected_avg_views_col}")
            if channel_recent_30_avg_views_col:
                logger.info(f"✅ 채널 리스트 최근 30개 영상 평균 조회수 열: {channel_recent_30_avg_views_col}")
            if video_duration_col:
                logger.info(f"✅ 영상 시트 영상길이 열: {video_duration_col}")
            if video_views_col:
                logger.info(f"✅ 영상 시트 조회수 열: {video_views_col}")

            # 수집날짜 열 찾기
            channel_date_col = None
            video_date_col = None
            for i, h in enumerate(channel_headers, 1):
                if '수집날짜' in str(h):
                    channel_date_col = i
                    break
            for i, h in enumerate(video_headers, 1):
                if '수집날짜' in str(h):
                    video_date_col = i
                    break

            if not channel_date_col or not video_date_col:
                raise Exception("수집날짜 열을 찾을 수 없습니다")

            # 채널 ID 열 찾기
            channel_id_col = None
            for i, h in enumerate(channel_headers, 1):
                if '채널 ID' in str(h):
                    channel_id_col = i
                    break

            # 가져올 채널 열 찾기 (삭제된 채널 필터링용)
            fetch_channel_col = None
            for i, h in enumerate(channel_headers, 1):
                clean_h = re.sub(r'^\d+[\.\s]*', '', str(h)).strip()
                if clean_h == '가져올 채널':
                    fetch_channel_col = i
                    break

            video_channel_name_col = None
            video_channel_id_col = None
            video_upload_date_col = None
            video_upload_date_col_partial = None  # 부분 일치 백업

            for i, h in enumerate(video_headers, 1):
                clean_h = re.sub(r'^\d+[\.\s]*', '', str(h)).strip()
                if video_channel_name_col is None and clean_h == '채널명':
                    video_channel_name_col = i
                if video_channel_id_col is None and clean_h == '채널 ID':
                    video_channel_id_col = i

                # 업로드날짜 찾기 - 완전 일치 우선
                if video_upload_date_col is None:
                    if clean_h == '영상 업로드날짜' or clean_h == '영상 업로드 날짜':
                        video_upload_date_col = i
                        logger.info(f"🎯 완전 일치: '{clean_h}' 열을 업로드날짜로 선택 (열 {i})")
                    elif video_upload_date_col_partial is None and '업로드' in clean_h and '날짜' in clean_h:
                        video_upload_date_col_partial = i
                        logger.info(f"📍 부분 일치 발견: '{clean_h}' (열 {i})")

            # 완전 일치가 없으면 부분 일치 사용
            if video_upload_date_col is None and video_upload_date_col_partial is not None:
                video_upload_date_col = video_upload_date_col_partial
                logger.info(f"✅ 완전 일치 없음, 부분 일치 사용: 열 {video_upload_date_col}")

            logger.info(f"📌 채널 리스트 - 채널ID: {channel_id_col}열, 수집날짜: {channel_date_col}열")
            logger.info(f"📌 영상 시트 - 채널명: {video_channel_name_col}열, 채널ID: {video_channel_id_col}열, 수집날짜: {video_date_col}열, 업로드날짜: {video_upload_date_col}열")

            if video_upload_date_col is None:
                logger.warning(f"⚠️ 영상 시트에서 '업로드날짜' 열을 찾을 수 없습니다. '최근 30개 영상 평균 조회수' 계산이 불가능합니다.")
                logger.warning(f"   찾는 패턴: '영상 업로드날짜' 또는 '영상 업로드 날짜' (완전 일치)")
                logger.warning(f"   또는 '업로드'와 '날짜'를 모두 포함하는 헤더 (부분 일치)")
                # 헤더 출력 (넘버링 제거)
                clean_headers = [re.sub(r'^\d+[\.\s]*', '', str(h)).strip() for h in video_headers[:15]]
                logger.warning(f"   영상 시트 헤더 (처음 15개): {clean_headers}")

            # 채널 리스트 데이터 읽기 (10행부터)
            channel_data = self.get_all_values_unformatted(channel_sheet)
            video_data = self.get_all_values_unformatted(video_sheet)

            logger.info(f"📊 채널 리스트: {len(channel_data)}행, 영상 시트: {len(video_data)}행")

            # 진행률 초기화
            if progress_callback:
                progress_callback(0, 100, "영상 데이터 인덱싱 중...")

            # 대규모 데이터 최적화: 영상 데이터를 채널 ID별로 인덱싱
            from collections import defaultdict
            from datetime import datetime, timedelta

            # 날짜 변환 함수 (먼저 정의)
            def parse_date(date_value):
                """날짜 값을 yyyy-mm-dd 형식의 문자열로 변환"""
                if not date_value:
                    return None

                date_str = str(date_value).strip()

                # 이미 yyyy-mm-dd 형식인 경우
                if '-' in date_str and len(date_str) == 10:
                    return date_str

                # Excel 시리얼 날짜인 경우 (숫자)
                try:
                    serial_date = float(date_value)
                    # Excel 시리얼 날짜는 1899-12-30을 기준으로 계산
                    base_date = datetime(1899, 12, 30)
                    actual_date = base_date + timedelta(days=serial_date)
                    return actual_date.strftime('%Y-%m-%d')
                except (ValueError, TypeError):
                    # 숫자가 아닌 경우 원본 반환
                    return date_str

            # 날짜 차이 계산 함수
            def days_difference(date1_str, date2_str):
                """두 날짜 문자열(yyyy-mm-dd) 간의 일수 차이 계산"""
                try:
                    date1 = datetime.strptime(str(date1_str), '%Y-%m-%d')
                    date2 = datetime.strptime(str(date2_str), '%Y-%m-%d')
                    return abs((date2 - date1).days)
                except:
                    return 0

            video_by_channel = defaultdict(list)
            channel_durations = defaultdict(list)  # 채널별 영상길이 저장 (평균 계산용)
            channel_view_counts = defaultdict(list)  # 채널별 조회수 저장 (평균 계산용)
            channel_recent_videos = defaultdict(list)  # 채널별 (업로드날짜, 조회수) 튜플 저장 (최근 30개 평균용)

            for vid_row_idx, vid_row in enumerate(video_data[9:], 10):
                if len(vid_row) < max(video_channel_id_col or 0, video_date_col):
                    continue

                vid_channel_id = str(vid_row[video_channel_id_col - 1]).strip() if video_channel_id_col and len(vid_row) >= video_channel_id_col else ''
                vid_date_raw = vid_row[video_date_col - 1] if len(vid_row) >= video_date_col else None
                vid_date = parse_date(vid_date_raw)  # 날짜 파싱 함수 사용

                if vid_channel_id and vid_date:
                    video_by_channel[vid_channel_id].append((vid_row, vid_date))

                    # 영상길이 데이터 수집 (평균 계산용)
                    if video_duration_col and len(vid_row) >= video_duration_col:
                        duration_str = str(vid_row[video_duration_col - 1]).strip()
                        if duration_str:
                            duration_seconds = self.parse_video_duration_to_seconds(duration_str)
                            if duration_seconds > 0:
                                channel_durations[vid_channel_id].append(duration_seconds)

                    # 조회수 데이터 수집 (평균 계산용)
                    if video_views_col and len(vid_row) >= video_views_col:
                        views_str = str(vid_row[video_views_col - 1]).strip()
                        if views_str:
                            view_count = self.parse_view_count(views_str)
                            if view_count > 0:
                                channel_view_counts[vid_channel_id].append(view_count)

                                # 업로드날짜가 있으면 최근 30개 계산용 데이터도 수집
                                if video_upload_date_col and len(vid_row) >= video_upload_date_col:
                                    upload_date_raw = vid_row[video_upload_date_col - 1]
                                    upload_date = parse_date(upload_date_raw)
                                    if upload_date:
                                        channel_recent_videos[vid_channel_id].append((upload_date, view_count))

            logger.info(f"✅ {len(video_by_channel)}개 채널의 영상 데이터 인덱싱 완료")
            if video_duration_col:
                logger.info(f"✅ {len(channel_durations)}개 채널의 영상길이 데이터 수집 완료")
            if video_views_col:
                logger.info(f"✅ {len(channel_view_counts)}개 채널의 조회수 데이터 수집 완료")
            if video_upload_date_col and video_views_col:
                logger.info(f"✅ {len(channel_recent_videos)}개 채널의 최근 영상 데이터 수집 완료 (업로드날짜+조회수)")

            # 양방향 업데이트 준비
            channel_updates = []  # 채널 리스트 시트 업데이트
            video_updates = []    # 영상 시트 업데이트
            update_count = 0
            total_channels = len([row for row in channel_data[9:] if len(row) >= 2 and str(row[1]).strip()])
            processed_channels = 0
            skipped_by_threshold = 0
            latest_date_overall = None  # 전체 최신 수집날짜
            video_rows_to_update = {}  # 영상 시트에서 업데이트할 행들 {row_idx: channel_id}

            # 1단계: 채널 ID별 모든 영상 행 인덱싱
            video_rows_by_channel = defaultdict(list)
            for vid_row_idx, vid_row in enumerate(video_data[9:], 10):
                if len(vid_row) < max(video_channel_id_col or 0, video_date_col):
                    continue
                vid_channel_id = str(vid_row[video_channel_id_col - 1]).strip() if video_channel_id_col and len(vid_row) >= video_channel_id_col else ''
                if vid_channel_id:
                    video_rows_by_channel[vid_channel_id].append(vid_row_idx)

            # 2단계: 채널별 처리
            for ch_row_idx, ch_row in enumerate(channel_data[9:], 10):
                if len(ch_row) < 2:
                    continue

                # "가져왔는지 여부" 체크
                brought_status = str(ch_row[1]).strip()
                if not brought_status:
                    continue

                # 삭제된 채널 체크 (가져올 채널 값이 'x'인 경우 스킵)
                if self.is_deleted_channel(ch_row, fetch_channel_col):
                    logger.debug(f"⏭️ 행 {ch_row_idx} 스킵 (삭제된 채널: 가져올 채널='x')")
                    continue

                # 채널 ID 가져오기
                if len(ch_row) < channel_id_col:
                    continue
                channel_id = str(ch_row[channel_id_col - 1]).strip()
                if not channel_id:
                    continue

                processed_channels += 1

                # 진행률 업데이트
                if progress_callback and processed_channels % 5 == 0:
                    progress = int((processed_channels / total_channels) * 100)
                    progress_callback(progress, 100, f"채널 처리 중... ({processed_channels}/{total_channels})")

                # 채널 리스트의 수집날짜 (파싱)
                ch_date_raw = ch_row[channel_date_col - 1] if len(ch_row) >= channel_date_col else None
                ch_date = parse_date(ch_date_raw)

                # 해당 채널의 영상 데이터 가져오기 (인덱싱된 데이터 사용)
                if channel_id not in video_by_channel:
                    continue

                # 최신 영상 선택 (수집날짜 기준)
                matching_videos = video_by_channel[channel_id]
                latest_video = max(matching_videos, key=lambda x: x[1] if x[1] else '')
                latest_video_row, latest_video_date = latest_video

                # 전체 최신 날짜 추적
                if ch_date and latest_video_date:
                    newer_date = max(ch_date, latest_video_date)
                    if not latest_date_overall or newer_date > latest_date_overall:
                        latest_date_overall = newer_date

                # 기간 필터 적용
                if days_threshold and ch_date and latest_video_date:
                    diff_days = days_difference(ch_date, latest_video_date)
                    if diff_days < days_threshold:
                        skipped_by_threshold += 1
                        continue

                # 수집날짜 비교 - 양방향 동기화
                if ch_date and latest_video_date:
                    # Case 1: 영상 시트가 더 최신 → 채널 리스트 업데이트
                    if latest_video_date > ch_date:
                        # 매핑된 헤더 데이터 업데이트
                        for ch_header, vid_header in header_mapping.items():
                            if ch_header in channel_col_map and vid_header in video_col_map:
                                ch_col = channel_col_map[ch_header]
                                vid_col = video_col_map[vid_header]

                                if len(latest_video_row) >= vid_col:
                                    new_value = latest_video_row[vid_col - 1]
                                    channel_updates.append({
                                        'range': f'{self._col_num_to_letter(ch_col)}{ch_row_idx}',
                                        'values': [[new_value]]
                                    })

                        # 채널 리스트의 수집날짜도 업데이트
                        channel_updates.append({
                            'range': f'{self._col_num_to_letter(channel_date_col)}{ch_row_idx}',
                            'values': [[latest_video_date]]
                        })

                        # 평균 영상길이 업데이트 (영상 시트가 더 최신인 경우)
                        if channel_avg_duration_col and channel_id in channel_durations and len(channel_durations[channel_id]) > 0:
                            avg_duration_seconds = sum(channel_durations[channel_id]) // len(channel_durations[channel_id])
                            avg_duration_text = self.seconds_to_duration_text(avg_duration_seconds)
                            if avg_duration_text:
                                channel_updates.append({
                                    'range': f'{self._col_num_to_letter(channel_avg_duration_col)}{ch_row_idx}',
                                    'values': [[avg_duration_text]]
                                })
                                logger.info(f"📊 채널 '{channel_id}' 평균 영상길이: {avg_duration_text} ({len(channel_durations[channel_id])}개 영상 기준)")

                        # 수집한 영상 평균 조회수 업데이트 (영상 시트가 더 최신인 경우)
                        if channel_collected_avg_views_col and channel_id in channel_view_counts and len(channel_view_counts[channel_id]) > 0:
                            total_views = sum(channel_view_counts[channel_id])
                            avg_views = total_views // len(channel_view_counts[channel_id])
                            avg_views_text = self.format_number_with_comma(avg_views)
                            channel_updates.append({
                                'range': f'{self._col_num_to_letter(channel_collected_avg_views_col)}{ch_row_idx}',
                                'values': [[avg_views_text]]
                            })
                            logger.info(f"📊 채널 '{channel_id}' 수집한 영상 평균 조회수: {avg_views_text} (총 {self.format_number_with_comma(total_views)} / {len(channel_view_counts[channel_id])}개 영상)")

                        # 최근 30개 영상 평균 조회수 업데이트 (영상 시트가 더 최신인 경우)
                        if channel_recent_30_avg_views_col:
                            if channel_id in channel_recent_videos and len(channel_recent_videos[channel_id]) > 0:
                                # 업로드날짜 기준으로 내림차순 정렬 (최신순)
                                sorted_videos = sorted(channel_recent_videos[channel_id], key=lambda x: x[0], reverse=True)
                                # 최근 30개 선택 (또는 전체 영상이 30개 미만이면 전체)
                                recent_30 = sorted_videos[:30]
                                # 조회수 평균 계산
                                recent_30_views = [views for _, views in recent_30]
                                avg_recent_30_views = sum(recent_30_views) // len(recent_30_views)
                                avg_recent_30_text = self.format_number_with_comma(avg_recent_30_views)
                                channel_updates.append({
                                    'range': f'{self._col_num_to_letter(channel_recent_30_avg_views_col)}{ch_row_idx}',
                                    'values': [[avg_recent_30_text]]
                                })
                                logger.info(f"📊 채널 '{channel_id}' 최근 30개 영상 평균 조회수: {avg_recent_30_text} ({len(recent_30)}개 영상 기준)")
                            else:
                                logger.warning(f"⚠️ 채널 '{channel_id}' 최근 30개 영상 평균 조회수 업데이트 실패 - 업로드날짜 데이터 없음")
                        elif video_upload_date_col is None:
                            logger.warning(f"⚠️ 최근 30개 영상 평균 조회수 열은 있지만 영상 시트에 '업로드날짜' 열을 찾을 수 없음")

                        update_count += 1
                        logger.info(f"✅ 채널 '{channel_id}' → 채널 리스트 업데이트 (행: {ch_row_idx}, 날짜: {ch_date} → {latest_video_date})")

                    # Case 2: 채널 리스트가 더 최신 → 영상 시트의 모든 해당 채널 행 업데이트
                    elif ch_date > latest_video_date:
                        if channel_id in video_rows_by_channel:
                            for vid_row_idx in video_rows_by_channel[channel_id]:
                                # 매핑된 헤더 데이터 업데이트 (역방향)
                                for ch_header, vid_header in header_mapping.items():
                                    if ch_header in channel_col_map and vid_header in video_col_map:
                                        ch_col = channel_col_map[ch_header]
                                        vid_col = video_col_map[vid_header]

                                        if len(ch_row) >= ch_col:
                                            new_value = ch_row[ch_col - 1]
                                            video_updates.append({
                                                'sheet': video_sheet,
                                                'range': f'{self._col_num_to_letter(vid_col)}{vid_row_idx}',
                                                'values': [[new_value]]
                                            })

                                # 영상 시트의 수집날짜도 업데이트
                                video_updates.append({
                                    'sheet': video_sheet,
                                    'range': f'{self._col_num_to_letter(video_date_col)}{vid_row_idx}',
                                    'values': [[ch_date]]
                                })

                            # 평균 영상길이 업데이트 (채널 리스트가 더 최신인 경우)
                            if channel_avg_duration_col and channel_id in channel_durations and len(channel_durations[channel_id]) > 0:
                                avg_duration_seconds = sum(channel_durations[channel_id]) // len(channel_durations[channel_id])
                                avg_duration_text = self.seconds_to_duration_text(avg_duration_seconds)
                                if avg_duration_text:
                                    channel_updates.append({
                                        'range': f'{self._col_num_to_letter(channel_avg_duration_col)}{ch_row_idx}',
                                        'values': [[avg_duration_text]]
                                    })
                                    logger.info(f"📊 채널 '{channel_id}' 평균 영상길이: {avg_duration_text} ({len(channel_durations[channel_id])}개 영상 기준)")

                            # 수집한 영상 평균 조회수 업데이트 (채널 리스트가 더 최신인 경우)
                            if channel_collected_avg_views_col and channel_id in channel_view_counts and len(channel_view_counts[channel_id]) > 0:
                                total_views = sum(channel_view_counts[channel_id])
                                avg_views = total_views // len(channel_view_counts[channel_id])
                                avg_views_text = self.format_number_with_comma(avg_views)
                                channel_updates.append({
                                    'range': f'{self._col_num_to_letter(channel_collected_avg_views_col)}{ch_row_idx}',
                                    'values': [[avg_views_text]]
                                })
                                logger.info(f"📊 채널 '{channel_id}' 수집한 영상 평균 조회수: {avg_views_text} (총 {self.format_number_with_comma(total_views)} / {len(channel_view_counts[channel_id])}개 영상)")

                            # 최근 30개 영상 평균 조회수 업데이트 (채널 리스트가 더 최신인 경우)
                            if channel_recent_30_avg_views_col:
                                if channel_id in channel_recent_videos and len(channel_recent_videos[channel_id]) > 0:
                                    # 업로드날짜 기준으로 내림차순 정렬 (최신순)
                                    sorted_videos = sorted(channel_recent_videos[channel_id], key=lambda x: x[0], reverse=True)
                                    # 최근 30개 선택 (또는 전체 영상이 30개 미만이면 전체)
                                    recent_30 = sorted_videos[:30]
                                    # 조회수 평균 계산
                                    recent_30_views = [views for _, views in recent_30]
                                    avg_recent_30_views = sum(recent_30_views) // len(recent_30_views)
                                    avg_recent_30_text = self.format_number_with_comma(avg_recent_30_views)
                                    channel_updates.append({
                                        'range': f'{self._col_num_to_letter(channel_recent_30_avg_views_col)}{ch_row_idx}',
                                        'values': [[avg_recent_30_text]]
                                    })
                                    logger.info(f"📊 채널 '{channel_id}' 최근 30개 영상 평균 조회수: {avg_recent_30_text} ({len(recent_30)}개 영상 기준)")

                                    # 최근 30개 영상 중위 조회수 계산 (상위 20%, 하위 20% 제외)
                                    if channel_recent_30_median_views_col and len(recent_30_views) >= 5:
                                        sorted_views = sorted(recent_30_views)
                                        total_count = len(sorted_views)
                                        remove_count = int(total_count * 0.2)
                                        if remove_count > 0:
                                            trimmed_views = sorted_views[remove_count:-remove_count]
                                        else:
                                            trimmed_views = sorted_views
                                        median_views = sum(trimmed_views) // len(trimmed_views)
                                        median_views_text = self.format_number_with_comma(median_views)
                                        channel_updates.append({
                                            'range': f'{self._col_num_to_letter(channel_recent_30_median_views_col)}{ch_row_idx}',
                                            'values': [[median_views_text]]
                                        })
                                        logger.info(f"📊 채널 '{channel_id}' 최근 30개 영상 중위 조회수: {median_views_text} (상위/하위 20% 제외, {len(trimmed_views)}개 영상 기준)")
                                else:
                                    logger.warning(f"⚠️ 채널 '{channel_id}' 최근 30개 영상 평균 조회수 업데이트 실패 - 업로드날짜 데이터 없음")

                            update_count += 1
                            logger.info(f"✅ 채널 '{channel_id}' → 영상 시트 업데이트 ({len(video_rows_by_channel[channel_id])}개 행, 날짜: {latest_video_date} → {ch_date})")

                # 분야1, 분야2 처리 (채널 리스트 → 영상 시트 단방향)
                if 'channel' in category_col_map and 'video' in category_col_map:
                    if channel_id in video_rows_by_channel:
                        for category_field in category_fields:
                            if category_field in category_col_map['channel'] and category_field in category_col_map['video']:
                                ch_cat_col = category_col_map['channel'][category_field]
                                vid_cat_col = category_col_map['video'][category_field]

                                # 채널 리스트의 분야 값
                                ch_category_value = ch_row[ch_cat_col - 1] if len(ch_row) >= ch_cat_col else ''

                                # 영상 시트의 해당 채널 모든 행 업데이트
                                for vid_row_idx in video_rows_by_channel[channel_id]:
                                    vid_row = video_data[vid_row_idx - 1]  # 0-based index
                                    vid_category_value = vid_row[vid_cat_col - 1] if len(vid_row) >= vid_cat_col else ''

                                    # 값이 다른 경우에만 업데이트
                                    if str(ch_category_value).strip() != str(vid_category_value).strip():
                                        video_updates.append({
                                            'sheet': video_sheet,
                                            'range': f'{self._col_num_to_letter(vid_cat_col)}{vid_row_idx}',
                                            'values': [[ch_category_value]]
                                        })
                                        logger.info(f"📝 채널 '{channel_id}' {category_field} 업데이트: '{vid_category_value}' → '{ch_category_value}' (영상 행: {vid_row_idx})")

                # 벤치마킹 채널여부 처리 (채널 리스트 → 영상 시트 단방향)
                if channel_benchmarking_col and video_benchmarking_col:
                    if channel_id in video_rows_by_channel:
                        # 채널 리스트의 벤치마킹 채널여부 값 (빈 값도 정확하게 처리)
                        if len(ch_row) >= channel_benchmarking_col:
                            ch_benchmarking_value = str(ch_row[channel_benchmarking_col - 1]).strip()
                        else:
                            ch_benchmarking_value = ''

                        # 영상 시트의 해당 채널 모든 행 업데이트
                        for vid_row_idx in video_rows_by_channel[channel_id]:
                            vid_row = video_data[vid_row_idx - 1]  # 0-based index

                            if len(vid_row) >= video_benchmarking_col:
                                vid_benchmarking_value = str(vid_row[video_benchmarking_col - 1]).strip()
                            else:
                                vid_benchmarking_value = ''

                            # 값이 다른 경우에만 업데이트 (빈 값으로의 변경도 포함)
                            if ch_benchmarking_value != vid_benchmarking_value:
                                video_updates.append({
                                    'sheet': video_sheet,
                                    'range': f'{self._col_num_to_letter(video_benchmarking_col)}{vid_row_idx}',
                                    'values': [[ch_benchmarking_value]]
                                })
                                logger.info(f"📝 채널 '{channel_id}' 벤치마킹 채널여부 업데이트: '{vid_benchmarking_value}' → '{ch_benchmarking_value}' (영상 행: {vid_row_idx})")

                # 평균 영상길이 업데이트 (날짜가 동일한 경우)
                # 영상 시트 또는 채널 리스트가 업데이트되지 않았어도 평균 영상길이는 항상 계산
                if channel_avg_duration_col and channel_id in channel_durations and len(channel_durations[channel_id]) > 0:
                    # 현재 채널 리스트의 평균 영상길이 값
                    current_avg_duration = ch_row[channel_avg_duration_col - 1] if len(ch_row) >= channel_avg_duration_col else ''

                    # 새로 계산한 평균 영상길이
                    avg_duration_seconds = sum(channel_durations[channel_id]) // len(channel_durations[channel_id])
                    avg_duration_text = self.seconds_to_duration_text(avg_duration_seconds)

                    # 값이 다른 경우에만 업데이트
                    if avg_duration_text and str(current_avg_duration).strip() != avg_duration_text:
                        # 이미 업데이트 리스트에 없는 경우에만 추가 (중복 방지)
                        avg_duration_range = f'{self._col_num_to_letter(channel_avg_duration_col)}{ch_row_idx}'
                        if not any(update['range'] == avg_duration_range for update in channel_updates):
                            channel_updates.append({
                                'range': avg_duration_range,
                                'values': [[avg_duration_text]]
                            })
                            logger.info(f"📊 채널 '{channel_id}' 평균 영상길이 업데이트: '{current_avg_duration}' → '{avg_duration_text}' ({len(channel_durations[channel_id])}개 영상 기준)")

                # 수집한 영상 평균 조회수 업데이트 (날짜가 동일한 경우)
                # 영상 시트 또는 채널 리스트가 업데이트되지 않았어도 수집한 영상 평균 조회수는 항상 계산
                if channel_collected_avg_views_col and channel_id in channel_view_counts and len(channel_view_counts[channel_id]) > 0:
                    # 현재 채널 리스트의 수집한 영상 평균 조회수 값
                    current_avg_views = ch_row[channel_collected_avg_views_col - 1] if len(ch_row) >= channel_collected_avg_views_col else ''

                    # 새로 계산한 수집한 영상 평균 조회수
                    total_views = sum(channel_view_counts[channel_id])
                    avg_views = total_views // len(channel_view_counts[channel_id])
                    avg_views_text = self.format_number_with_comma(avg_views)

                    # 값이 다른 경우에만 업데이트
                    if avg_views_text and str(current_avg_views).replace(',', '').strip() != str(avg_views).strip():
                        # 이미 업데이트 리스트에 없는 경우에만 추가 (중복 방지)
                        avg_views_range = f'{self._col_num_to_letter(channel_collected_avg_views_col)}{ch_row_idx}'
                        if not any(update['range'] == avg_views_range for update in channel_updates):
                            channel_updates.append({
                                'range': avg_views_range,
                                'values': [[avg_views_text]]
                            })
                            logger.info(f"📊 채널 '{channel_id}' 수집한 영상 평균 조회수 업데이트: '{current_avg_views}' → '{avg_views_text}' (총 {self.format_number_with_comma(total_views)} / {len(channel_view_counts[channel_id])}개 영상)")

            # 9행 수집날짜 열만 최신 날짜로 업데이트 (양쪽 시트 모두, 헤더/함수는 유지)
            header_date_updated = {'channel': False, 'video': False}

            # 채널 리스트 9행 수집날짜 열 업데이트
            if latest_date_overall and len(channel_data) >= 9:
                header_row = channel_data[8]  # 9행 (인덱스 8)
                if len(header_row) >= channel_date_col:
                    current_header_date_raw = header_row[channel_date_col - 1]
                    current_header_date = parse_date(current_header_date_raw)
                    if not current_header_date or latest_date_overall > current_header_date:
                        channel_updates.append({
                            'range': f'{self._col_num_to_letter(channel_date_col)}9',
                            'values': [[latest_date_overall]]
                        })
                        header_date_updated['channel'] = True
                        logger.info(f"📅 채널 리스트 9행 수집날짜 업데이트: {current_header_date} → {latest_date_overall}")

            # 영상 시트 9행 수집날짜 열 업데이트
            if latest_date_overall and len(video_data) >= 9:
                header_row = video_data[8]  # 9행 (인덱스 8)
                if len(header_row) >= video_date_col:
                    current_header_date_raw = header_row[video_date_col - 1]
                    current_header_date = parse_date(current_header_date_raw)
                    if not current_header_date or latest_date_overall > current_header_date:
                        video_updates.append({
                            'sheet': video_sheet,
                            'range': f'{self._col_num_to_letter(video_date_col)}9',
                            'values': [[latest_date_overall]]
                        })
                        header_date_updated['video'] = True
                        logger.info(f"📅 영상 시트 9행 수집날짜 업데이트: {current_header_date} → {latest_date_overall}")

            # 일괄 업데이트
            total_updates = len(channel_updates) + len(video_updates)
            if total_updates > 0:
                if progress_callback:
                    progress_callback(90, 100, f"시트 업데이트 중... ({total_updates}개 셀)")

                logger.info(f"📝 {update_count}개 채널의 {total_updates}개 셀 업데이트 중...")
                logger.info(f"   - 채널 리스트: {len(channel_updates)}개 셀")
                logger.info(f"   - 영상 시트: {len(video_updates)}개 셀")

                # 일괄 업데이트 (batch_update 사용하여 API 호출 최소화)
                import time

                # 채널 리스트 업데이트
                if channel_updates:
                    logger.info(f"  채널 리스트 업데이트 시작... (일괄 업데이트)")
                    try:
                        # batch_update 사용 (한 번의 API 호출로 모든 셀 업데이트)
                        channel_sheet.batch_update(channel_updates, value_input_option='USER_ENTERED')
                        logger.info(f"    ✅ 채널 리스트 {len(channel_updates)}개 셀 업데이트 완료 (1회 API 호출)")
                    except Exception as e:
                        logger.error(f"    ❌ 채널 리스트 일괄 업데이트 실패: {e}")
                        raise

                # 영상 시트 업데이트
                if video_updates:
                    logger.info(f"  영상 시트 업데이트 시작... (일괄 업데이트)")

                    # 시트별로 그룹화
                    from collections import defaultdict
                    updates_by_sheet = defaultdict(list)
                    for update in video_updates:
                        sheet_obj = update['sheet']
                        updates_by_sheet[id(sheet_obj)].append({
                            'range': update['range'],
                            'values': update['values']
                        })

                    # 각 시트별로 batch_update 실행
                    for sheet_id, updates in updates_by_sheet.items():
                        # sheet 객체 찾기
                        sheet_obj = None
                        for update in video_updates:
                            if id(update['sheet']) == sheet_id:
                                sheet_obj = update['sheet']
                                break

                        if sheet_obj:
                            try:
                                sheet_obj.batch_update(updates, value_input_option='USER_ENTERED')
                                logger.info(f"    ✅ 영상 시트 {len(updates)}개 셀 업데이트 완료 (1회 API 호출)")
                            except Exception as e:
                                logger.error(f"    ❌ 영상 시트 일괄 업데이트 실패: {e}")
                                raise

                # 수집날짜 열에 표시 형식(yyyy-mm-dd) 적용
                logger.info(f"📅 수집날짜 열에 날짜 형식(yyyy-mm-dd) 적용 중...")
                try:
                    # 채널 리스트 수집날짜 열
                    if channel_date_col:
                        self._apply_date_format_to_column(channel_sheet, channel_date_col)
                        logger.info(f"  ✅ 채널 리스트 수집날짜 열({channel_date_col}) 형식 적용 완료")

                    # 영상 시트 수집날짜 열
                    if video_date_col:
                        self._apply_date_format_to_column(video_sheet, video_date_col)
                        logger.info(f"  ✅ 영상 시트 수집날짜 열({video_date_col}) 형식 적용 완료")
                except Exception as e:
                    logger.warning(f"⚠️ 수집날짜 형식 적용 중 오류 (데이터는 정상 업데이트됨): {e}")

                if progress_callback:
                    progress_callback(100, 100, "완료!")

                summary = f"🎉 양방향 동기화 완료: {update_count}개 채널"
                if header_date_updated['channel'] or header_date_updated['video']:
                    updated_sheets = []
                    if header_date_updated['channel']:
                        updated_sheets.append("채널 리스트")
                    if header_date_updated['video']:
                        updated_sheets.append("영상 시트")
                    summary += f" + 9행 헤더 날짜 ({', '.join(updated_sheets)})"
                if skipped_by_threshold > 0:
                    summary += f" (기간 필터로 {skipped_by_threshold}개 스킵)"

                logger.info(summary)
                return update_count
            else:
                logger.info(f"⚠️ 업데이트할 데이터가 없습니다 (기간 필터로 {skipped_by_threshold}개 스킵)")
                return 0

        except Exception as e:
            logger.error(f"❌ 채널 리스트 업데이트 실패: {str(e)}")
            raise

    def update_recent_30_avg_views_only(self, spreadsheet_url, video_sheet_name, progress_callback=None):
        """
        채널 리스트의 '최근 30개 영상 평균 조회수'와 '최근 30개 영상 중위 조회수' 열 업데이트

        Args:
            spreadsheet_url: 스프레드시트 URL
            video_sheet_name: 영상 시트 이름
            progress_callback: 진행률 콜백 함수

        Returns:
            str: 결과 메시지
        """
        try:
            logger.info(f"🔄 최근 30개 영상 평균 조회수 업데이트 시작 (영상 시트: {video_sheet_name})")

            # 스프레드시트 및 시트 가져오기
            spreadsheet = self.client.open_by_url(spreadsheet_url)
            channel_sheet = spreadsheet.worksheet('채널 리스트')
            video_sheet = spreadsheet.worksheet(video_sheet_name)

            recent_30_avg_views_field = '최근 30개 영상 평균 조회수'
            recent_30_median_views_field = '최근 30개 영상 중위 조회수'

            # 헤더 읽기
            channel_headers = channel_sheet.row_values(1)
            video_headers = video_sheet.row_values(1)

            logger.info(f"📋 채널 리스트 헤더: {len(channel_headers)}개")
            logger.info(f"📋 영상 시트 헤더: {len(video_headers)}개")

            # 채널 리스트에서 필요한 열 찾기 (완전 일치만)
            channel_recent_30_avg_views_col = self.find_header_column(channel_headers, recent_30_avg_views_field, allow_partial=False)
            channel_recent_30_median_views_col = self.find_header_column(channel_headers, recent_30_median_views_field, allow_partial=False)
            channel_id_col = self.find_header_column(channel_headers, '채널 ID', allow_partial=False)
            channel_benchmarking_col = self.find_header_column(channel_headers, '벤치마킹 채널여부', allow_partial=False)

            if not channel_recent_30_avg_views_col:
                raise Exception(f"채널 리스트에서 '{recent_30_avg_views_field}' 열을 찾을 수 없습니다.")
            if not channel_id_col:
                raise Exception(f"채널 리스트에서 '채널 ID' 열을 찾을 수 없습니다.")

            logger.info(f"✅ 채널 리스트 최근 30개 영상 평균 조회수 열: {channel_recent_30_avg_views_col}")
            if channel_recent_30_median_views_col:
                logger.info(f"✅ 채널 리스트 최근 30개 영상 중위 조회수 열: {channel_recent_30_median_views_col}")
            logger.info(f"✅ 채널 리스트 채널 ID 열: {channel_id_col}")
            if channel_benchmarking_col:
                logger.info(f"✅ 채널 리스트 벤치마킹 채널여부 열: {channel_benchmarking_col}")

            # 영상 시트에서 필요한 열 찾기 (완전 일치 우선, 없으면 부분 일치)
            video_channel_id_col = self.find_header_column(video_headers, '채널 ID', allow_partial=False)
            video_views_col = self.find_header_column(video_headers, '조회수', allow_partial=False)
            video_benchmarking_col = self.find_header_column(video_headers, '벤치마킹 채널여부', allow_partial=False)

            # 업로드날짜는 완전 일치 우선, 없으면 부분 일치 시도
            video_upload_date_col = self.find_header_column(video_headers, '영상 업로드날짜', allow_partial=False)
            if not video_upload_date_col:
                video_upload_date_col = self.find_header_column(video_headers, '영상 업로드 날짜', allow_partial=False)
            if not video_upload_date_col:
                # 부분 일치 시도 (최후의 수단)
                for i, h in enumerate(video_headers, 1):
                    clean_h = re.sub(r'^\d+[\.\s]*', '', str(h)).strip()
                    if '업로드' in clean_h and '날짜' in clean_h and '이후' not in clean_h:
                        video_upload_date_col = i
                        logger.info(f"📍 부분 일치: '{clean_h}' 열을 업로드날짜로 선택 (열 {i})")
                        break

            logger.info(f"📌 영상 시트 - 채널ID: {video_channel_id_col}열, 업로드날짜: {video_upload_date_col}열, 조회수: {video_views_col}열")

            if not video_channel_id_col or not video_upload_date_col or not video_views_col:
                missing = []
                if not video_channel_id_col:
                    missing.append("채널 ID")
                if not video_upload_date_col:
                    missing.append("영상 업로드날짜")
                if not video_views_col:
                    missing.append("조회수")
                raise Exception(f"영상 시트에서 필수 열을 찾을 수 없습니다: {', '.join(missing)}")

            # 데이터 읽기
            channel_data = self.get_all_values_unformatted(channel_sheet)
            video_data = self.get_all_values_unformatted(video_sheet)

            logger.info(f"📊 채널 리스트: {len(channel_data)}행, 영상 시트: {len(video_data)}행")

            if progress_callback:
                progress_callback(0, 100, "영상 데이터 수집 중...")

            # 날짜 변환 함수
            def parse_date(date_value):
                """날짜 값을 yyyy-mm-dd 형식의 문자열로 변환"""
                if not date_value:
                    return None

                date_str = str(date_value).strip()

                # 이미 yyyy-mm-dd 형식인 경우
                if '-' in date_str and len(date_str) == 10:
                    return date_str

                # Excel 시리얼 날짜인 경우 (숫자)
                try:
                    from datetime import datetime, timedelta
                    serial_date = float(date_value)
                    base_date = datetime(1899, 12, 30)
                    actual_date = base_date + timedelta(days=serial_date)
                    return actual_date.strftime('%Y-%m-%d')
                except (ValueError, TypeError):
                    return date_str

            # 채널별 (업로드날짜, 조회수) 수집 및 영상 행 인덱스 매핑
            from collections import defaultdict
            channel_recent_videos = defaultdict(list)
            video_rows_by_channel = defaultdict(list)  # 벤치마킹 채널여부 동기화를 위한 매핑

            for vid_row_idx, vid_row in enumerate(video_data[9:], 10):
                if len(vid_row) < max(video_channel_id_col, video_upload_date_col, video_views_col):
                    continue

                vid_channel_id = str(vid_row[video_channel_id_col - 1]).strip() if len(vid_row) >= video_channel_id_col else ''
                if not vid_channel_id:
                    continue

                # 영상 행 인덱스 매핑 (벤치마킹 채널여부 동기화용)
                video_rows_by_channel[vid_channel_id].append(vid_row_idx)

                upload_date_raw = vid_row[video_upload_date_col - 1] if len(vid_row) >= video_upload_date_col else None
                upload_date = parse_date(upload_date_raw)
                views_str = str(vid_row[video_views_col - 1]).strip() if len(vid_row) >= video_views_col else ''

                if upload_date and views_str:
                    view_count = self.parse_view_count(views_str)
                    if view_count > 0:
                        channel_recent_videos[vid_channel_id].append((upload_date, view_count))

            logger.info(f"✅ {len(channel_recent_videos)}개 채널의 최근 영상 데이터 수집 완료 (업로드날짜+조회수)")

            # 채널 리스트 및 영상 시트 업데이트
            channel_updates = []
            video_updates = []
            processed_channels = 0
            total_channels = len([row for row in channel_data[9:] if len(row) >= 2 and str(row[1]).strip()])

            for ch_row_idx, ch_row in enumerate(channel_data[9:], 10):
                if len(ch_row) < 2:
                    continue

                # "가져왔는지 여부" 체크
                brought_status = str(ch_row[1]).strip()
                if not brought_status:
                    continue

                # 채널 ID 가져오기
                channel_id = str(ch_row[channel_id_col - 1]).strip() if len(ch_row) >= channel_id_col else ''
                if not channel_id:
                    continue

                processed_channels += 1

                # 진행률 업데이트
                if progress_callback and processed_channels % 10 == 0:
                    progress_callback(processed_channels, total_channels, f"채널 {processed_channels}/{total_channels} 처리 중...")

                # 최근 30개 영상 평균 조회수 및 중위 조회수 계산
                if channel_id in channel_recent_videos and len(channel_recent_videos[channel_id]) > 0:
                    # 업로드날짜 기준으로 내림차순 정렬 (최신순)
                    sorted_videos = sorted(channel_recent_videos[channel_id], key=lambda x: x[0], reverse=True)
                    # 최근 30개 선택
                    recent_30 = sorted_videos[:30]
                    # 조회수 평균 계산
                    recent_30_views = [views for _, views in recent_30]
                    avg_recent_30_views = sum(recent_30_views) // len(recent_30_views)
                    avg_recent_30_text = self.format_number_with_comma(avg_recent_30_views)

                    # 평균 조회수 업데이트
                    channel_updates.append({
                        'range': f'{self._col_num_to_letter(channel_recent_30_avg_views_col)}{ch_row_idx}',
                        'values': [[avg_recent_30_text]]
                    })
                    logger.info(f"📊 채널 '{channel_id}' 최근 30개 영상 평균 조회수: {avg_recent_30_text} ({len(recent_30)}개 영상 기준)")

                    # 중위 조회수 계산 (상위 20%, 하위 20% 제외)
                    if channel_recent_30_median_views_col and len(recent_30_views) >= 5:
                        # 조회수 기준 정렬
                        sorted_views = sorted(recent_30_views)
                        total_count = len(sorted_views)

                        # 상위/하위 20% 제거 (소수점 내림)
                        remove_count = int(total_count * 0.2)
                        if remove_count > 0:
                            # 하위 20% 제거, 상위 20% 제거
                            trimmed_views = sorted_views[remove_count:-remove_count]
                        else:
                            trimmed_views = sorted_views

                        # 중위 조회수 계산 (trimmed mean)
                        if trimmed_views:
                            median_views = sum(trimmed_views) // len(trimmed_views)
                            median_views_text = self.format_number_with_comma(median_views)

                            channel_updates.append({
                                'range': f'{self._col_num_to_letter(channel_recent_30_median_views_col)}{ch_row_idx}',
                                'values': [[median_views_text]]
                            })
                            logger.info(f"📊 채널 '{channel_id}' 최근 30개 영상 중위 조회수: {median_views_text} (상위/하위 {remove_count}개씩 제외, {len(trimmed_views)}개 영상 기준)")

                # 벤치마킹 채널여부 처리 (채널 리스트 → 영상 시트 단방향)
                if channel_benchmarking_col and video_benchmarking_col:
                    if channel_id in video_rows_by_channel:
                        # 채널 리스트의 벤치마킹 채널여부 값 (빈 값도 정확하게 처리)
                        if len(ch_row) >= channel_benchmarking_col:
                            ch_benchmarking_value = str(ch_row[channel_benchmarking_col - 1]).strip()
                        else:
                            ch_benchmarking_value = ''

                        # 영상 시트의 해당 채널 모든 행 업데이트
                        for vid_row_idx in video_rows_by_channel[channel_id]:
                            vid_row = video_data[vid_row_idx - 10]  # video_data[9:]이므로 offset 10

                            if len(vid_row) >= video_benchmarking_col:
                                vid_benchmarking_value = str(vid_row[video_benchmarking_col - 1]).strip()
                            else:
                                vid_benchmarking_value = ''

                            # 값이 다른 경우에만 업데이트 (빈 값으로의 변경도 포함)
                            if ch_benchmarking_value != vid_benchmarking_value:
                                video_updates.append({
                                    'range': f'{self._col_num_to_letter(video_benchmarking_col)}{vid_row_idx}',
                                    'values': [[ch_benchmarking_value]]
                                })
                                logger.info(f"📝 채널 '{channel_id}' 벤치마킹 채널여부 업데이트: '{vid_benchmarking_value}' → '{ch_benchmarking_value}' (영상 행: {vid_row_idx})")

            # 배치 업데이트 실행
            if channel_updates or video_updates:
                total_updates = len(channel_updates) + len(video_updates)
                if progress_callback:
                    progress_callback(total_channels, total_channels, f"업데이트 적용 중... ({total_updates}개 셀)")

                if channel_updates:
                    channel_sheet.batch_update(channel_updates, value_input_option='USER_ENTERED')
                    logger.info(f"✅ 채널 리스트 {len(channel_updates)}개 셀 업데이트 완료")

                if video_updates:
                    video_sheet.batch_update(video_updates, value_input_option='USER_ENTERED')
                    logger.info(f"✅ 영상 시트 {len(video_updates)}개 셀 업데이트 완료 (벤치마킹 채널여부)")

                result_msg = f"✅ 최근 30개 영상 평균 조회수 업데이트 완료!\n\n" \
                           f"• 처리 채널: {processed_channels}개\n" \
                           f"• 채널 리스트 업데이트 셀: {len(channel_updates)}개\n" \
                           f"• 영상 시트 업데이트 셀: {len(video_updates)}개 (벤치마킹 채널여부)"
                logger.info(result_msg)
                return result_msg
            else:
                logger.info(f"⚠️ 업데이트할 데이터가 없습니다")
                return "⚠️ 업데이트할 데이터가 없습니다.\n업로드날짜 데이터가 있는 채널이 없습니다."

        except Exception as e:
            logger.error(f"❌ 최근 30개 영상 평균 조회수 업데이트 실패: {str(e)}")
            raise

    def update_video_sheet_categories_from_channel_list(self, spreadsheet_url, video_sheet_name, progress_callback=None):
        """
        영상 시트의 분야1/분야2를 채널 리스트 기준으로 업데이트 (채널명 기준 매칭)

        Args:
            spreadsheet_url: 스프레드시트 URL
            video_sheet_name: 영상 시트 이름
            progress_callback: 진행률 콜백 함수 (current, total, message)

        Returns:
            업데이트된 영상 수
        """
        try:
            logger.info(f"=== 분야1/분야2 업데이트 시작 ===")
            logger.info(f"영상 시트: {video_sheet_name}")

            if not self.client:
                self.authenticate()

            spreadsheet = self.client.open_by_url(spreadsheet_url)
            channel_sheet = spreadsheet.worksheet("채널 리스트")
            video_sheet = spreadsheet.worksheet(video_sheet_name)

            if progress_callback:
                progress_callback(0, 100, "헤더 정보 읽는 중...")

            # 헤더 읽기 (9행)
            channel_headers = channel_sheet.row_values(9)
            video_headers = video_sheet.row_values(9)

            # 채널 리스트에서 필요한 열 찾기
            channel_name_col = None
            channel_field1_col = None
            channel_field2_col = None
            channel_benchmarking_col = None

            for i, h in enumerate(channel_headers, 1):
                clean_h = re.sub(r'^\d+[\.\s]*', '', str(h)).strip()
                if clean_h == '채널명':
                    channel_name_col = i
                elif clean_h == '분야1':
                    channel_field1_col = i
                elif clean_h == '분야2':
                    channel_field2_col = i
                elif clean_h == '벤치마킹 채널여부':
                    channel_benchmarking_col = i

            # 영상 시트에서 필요한 열 찾기
            video_channel_name_col = None
            video_field1_col = None
            video_field2_col = None
            video_benchmarking_col = None

            for i, h in enumerate(video_headers, 1):
                clean_h = re.sub(r'^\d+[\.\s]*', '', str(h)).strip()
                if clean_h == '채널명':
                    video_channel_name_col = i
                elif clean_h == '분야1':
                    video_field1_col = i
                elif clean_h == '분야2':
                    video_field2_col = i
                elif clean_h == '벤치마킹 채널여부':
                    video_benchmarking_col = i

            if not all([channel_name_col, channel_field1_col, channel_field2_col,
                       video_channel_name_col, video_field1_col, video_field2_col]):
                missing = []
                if not channel_name_col or not video_channel_name_col:
                    missing.append("채널명")
                if not channel_field1_col or not video_field1_col:
                    missing.append("분야1")
                if not channel_field2_col or not video_field2_col:
                    missing.append("분야2")
                raise ValueError(f"필수 헤더가 없습니다: {', '.join(missing)}")

            logger.info(f"📌 채널 리스트 - 채널명: {channel_name_col}열, 분야1: {channel_field1_col}열, 분야2: {channel_field2_col}열")
            if channel_benchmarking_col:
                logger.info(f"📌 채널 리스트 - 벤치마킹 채널여부: {channel_benchmarking_col}열")
            logger.info(f"📌 영상 시트 - 채널명: {video_channel_name_col}열, 분야1: {video_field1_col}열, 분야2: {video_field2_col}열")
            if video_benchmarking_col:
                logger.info(f"📌 영상 시트 - 벤치마킹 채널여부: {video_benchmarking_col}열")

            # 데이터 읽기
            if progress_callback:
                progress_callback(10, 100, "데이터 읽는 중...")

            channel_data = self.get_all_values_unformatted(channel_sheet)
            video_data = self.get_all_values_unformatted(video_sheet)

            logger.info(f"📊 채널 리스트: {len(channel_data)}행, 영상 시트: {len(video_data)}행")

            # 채널명 → (분야1, 분야2, 벤치마킹 채널여부) 매핑 생성
            if progress_callback:
                progress_callback(20, 100, "채널 데이터 인덱싱 중...")

            channel_categories = {}  # {채널명: (분야1, 분야2, 벤치마킹 채널여부)}

            for ch_row in channel_data[9:]:  # 10행부터
                if len(ch_row) < 2:
                    continue

                # "가져왔는지 여부" 체크
                brought_status = str(ch_row[1]).strip()
                if not brought_status:
                    continue

                # 채널명 가져오기
                if len(ch_row) < channel_name_col:
                    continue
                channel_name = str(ch_row[channel_name_col - 1]).strip()
                if not channel_name:
                    continue

                # 분야1, 분야2, 벤치마킹 채널여부 가져오기
                field1 = str(ch_row[channel_field1_col - 1]).strip() if len(ch_row) >= channel_field1_col else ''
                field2 = str(ch_row[channel_field2_col - 1]).strip() if len(ch_row) >= channel_field2_col else ''
                benchmarking = str(ch_row[channel_benchmarking_col - 1]).strip() if channel_benchmarking_col and len(ch_row) >= channel_benchmarking_col else ''

                channel_categories[channel_name] = (field1, field2, benchmarking)

            logger.info(f"✅ {len(channel_categories)}개 채널의 분야 정보 인덱싱 완료")

            # 영상 시트 업데이트
            if progress_callback:
                progress_callback(30, 100, "영상 데이터 처리 중...")

            video_updates = []
            update_count = 0
            processed_rows = 0
            total_video_rows = len(video_data) - 9  # 10행부터 계산

            for vid_row_idx, vid_row in enumerate(video_data[9:], 10):
                if len(vid_row) < video_channel_name_col:
                    continue

                # 채널명 가져오기
                video_channel_name = str(vid_row[video_channel_name_col - 1]).strip()
                if not video_channel_name:
                    continue

                # 해당 채널이 채널 리스트에 있는지 확인
                if video_channel_name not in channel_categories:
                    continue

                # 채널 리스트의 분야1, 분야2, 벤치마킹 채널여부
                ch_field1, ch_field2, ch_benchmarking = channel_categories[video_channel_name]

                # 영상 시트의 현재 분야1, 분야2, 벤치마킹 채널여부
                vid_field1 = str(vid_row[video_field1_col - 1]).strip() if len(vid_row) >= video_field1_col else ''
                vid_field2 = str(vid_row[video_field2_col - 1]).strip() if len(vid_row) >= video_field2_col else ''
                vid_benchmarking = str(vid_row[video_benchmarking_col - 1]).strip() if video_benchmarking_col and len(vid_row) >= video_benchmarking_col else ''

                # 값이 다른 경우만 업데이트
                updated = False
                update_details = []

                if ch_field1 != vid_field1:
                    video_updates.append({
                        'range': f'{self._col_num_to_letter(video_field1_col)}{vid_row_idx}',
                        'values': [[ch_field1]]
                    })
                    updated = True
                    update_details.append(f"분야1: '{vid_field1}' → '{ch_field1}'")

                if ch_field2 != vid_field2:
                    video_updates.append({
                        'range': f'{self._col_num_to_letter(video_field2_col)}{vid_row_idx}',
                        'values': [[ch_field2]]
                    })
                    updated = True
                    update_details.append(f"분야2: '{vid_field2}' → '{ch_field2}'")

                if video_benchmarking_col and ch_benchmarking != vid_benchmarking:
                    video_updates.append({
                        'range': f'{self._col_num_to_letter(video_benchmarking_col)}{vid_row_idx}',
                        'values': [[ch_benchmarking]]
                    })
                    updated = True
                    update_details.append(f"벤치마킹 채널여부: '{vid_benchmarking}' → '{ch_benchmarking}'")

                if updated:
                    update_count += 1
                    logger.info(f"📝 영상 '{video_channel_name}' (행: {vid_row_idx}) - {', '.join(update_details)}")

                processed_rows += 1

                # 진행률 업데이트 (매 100행마다)
                if progress_callback and processed_rows % 100 == 0:
                    progress = 30 + int((processed_rows / total_video_rows) * 60)
                    progress_callback(progress, 100, f"영상 처리 중... ({processed_rows}/{total_video_rows})")

            # 일괄 업데이트
            if video_updates:
                if progress_callback:
                    progress_callback(90, 100, f"시트 업데이트 중... ({len(video_updates)}개 셀)")

                logger.info(f"📝 {len(video_updates)}개 셀 업데이트 중...")

                # batch_update API 사용 (한 번의 API 호출로 여러 셀 업데이트)
                import time

                # 데이터 준비: 범위별로 그룹화
                data_to_update = []
                for update in video_updates:
                    data_to_update.append({
                        'range': f"{video_sheet.title}!{update['range']}",
                        'values': update['values']
                    })

                # API 제한: 한 번에 최대 100개 범위까지 (안전하게 50개씩 처리)
                batch_size = 50
                total_batches = (len(data_to_update) + batch_size - 1) // batch_size

                for batch_idx in range(0, len(data_to_update), batch_size):
                    batch = data_to_update[batch_idx:batch_idx + batch_size]
                    current_batch_num = (batch_idx // batch_size) + 1

                    try:
                        # batch_update로 한 번에 여러 범위 업데이트 (1번의 API 호출)
                        video_sheet.spreadsheet.values_batch_update(
                            body={
                                'valueInputOption': 'USER_ENTERED',
                                'data': batch
                            }
                        )

                        logger.info(f"    진행: {min(batch_idx + batch_size, len(video_updates))}/{len(video_updates)} 셀 완료 (배치 {current_batch_num}/{total_batches})")

                        # 진행률 업데이트
                        if progress_callback:
                            progress = 90 + int((batch_idx / len(video_updates)) * 10)
                            progress_callback(progress, 100, f"시트 업데이트 중... ({min(batch_idx + batch_size, len(video_updates))}/{len(video_updates)})")

                        # 배치 간 대기 (API 제한 방지: 50개 범위 = 1 API 호출, 60회/분 제한이므로 1초 대기)
                        if batch_idx + batch_size < len(data_to_update):
                            time.sleep(1.2)  # 60회/분 = 1회/초, 여유있게 1.2초

                    except Exception as update_err:
                        error_msg = str(update_err)
                        logger.error(f"배치 업데이트 실패 (배치 {current_batch_num}/{total_batches}): {error_msg}")

                        # API 제한 에러인 경우 대기 후 재시도
                        if '429' in error_msg or 'quota' in error_msg.lower():
                            logger.warning("API 제한 도달, 60초 대기 후 재시도...")
                            time.sleep(60)  # 1분 대기

                            # 재시도
                            try:
                                video_sheet.spreadsheet.values_batch_update(
                                    body={
                                        'valueInputOption': 'USER_ENTERED',
                                        'data': batch
                                    }
                                )
                                logger.info(f"    재시도 성공: 배치 {current_batch_num}/{total_batches}")
                            except Exception as retry_err:
                                logger.error(f"재시도 실패: {str(retry_err)}")
                                raise
                        else:
                            raise

                if progress_callback:
                    progress_callback(100, 100, "완료!")

                logger.info(f"🎉 분야1/분야2 업데이트 완료: {update_count}개 영상")
                return update_count
            else:
                logger.info(f"⚠️ 업데이트할 데이터가 없습니다")
                return 0

        except Exception as e:
            logger.error(f"❌ 분야1/분야2 업데이트 실패: {str(e)}")
            raise

    def update_benchmarking_status_from_channel_list(self, spreadsheet_url, video_sheet_name, progress_callback=None):
        """
        영상 시트의 벤치마킹 채널여부를 채널 리스트 기준으로 업데이트 (채널명 기준 매칭)

        Args:
            spreadsheet_url: 스프레드시트 URL
            video_sheet_name: 영상 시트 이름
            progress_callback: 진행률 콜백 함수 (current, total, message)

        Returns:
            업데이트된 영상 수
        """
        try:
            logger.info(f"=== 벤치마킹 채널여부 업데이트 시작 ===")
            logger.info(f"영상 시트: {video_sheet_name}")

            if not self.client:
                self.authenticate()

            spreadsheet = self.client.open_by_url(spreadsheet_url)
            channel_sheet = spreadsheet.worksheet("채널 리스트")
            video_sheet = spreadsheet.worksheet(video_sheet_name)

            if progress_callback:
                progress_callback(0, 100, "헤더 정보 읽는 중...")

            # 헤더 읽기 (9행)
            channel_headers = channel_sheet.row_values(9)
            video_headers = video_sheet.row_values(9)

            # 채널 리스트에서 필요한 열 찾기
            channel_name_col = None
            channel_benchmarking_col = None

            for i, h in enumerate(channel_headers, 1):
                clean_h = re.sub(r'^\d+[\.\s]*', '', str(h)).strip()
                if clean_h == '채널명':
                    channel_name_col = i
                elif clean_h == '벤치마킹 채널여부':
                    channel_benchmarking_col = i

            # 영상 시트에서 필요한 열 찾기
            video_channel_name_col = None
            video_benchmarking_col = None

            for i, h in enumerate(video_headers, 1):
                clean_h = re.sub(r'^\d+[\.\s]*', '', str(h)).strip()
                if clean_h == '채널명':
                    video_channel_name_col = i
                elif clean_h == '벤치마킹 채널여부':
                    video_benchmarking_col = i

            if not all([channel_name_col, channel_benchmarking_col,
                       video_channel_name_col, video_benchmarking_col]):
                missing = []
                if not channel_name_col or not video_channel_name_col:
                    missing.append("채널명")
                if not channel_benchmarking_col or not video_benchmarking_col:
                    missing.append("벤치마킹 채널여부")
                raise ValueError(f"필수 헤더가 없습니다: {', '.join(missing)}")

            logger.info(f"📌 채널 리스트 - 채널명: {channel_name_col}열, 벤치마킹 채널여부: {channel_benchmarking_col}열")
            logger.info(f"📌 영상 시트 - 채널명: {video_channel_name_col}열, 벤치마킹 채널여부: {video_benchmarking_col}열")

            # 데이터 읽기
            if progress_callback:
                progress_callback(10, 100, "데이터 읽는 중...")

            channel_data = self.get_all_values_unformatted(channel_sheet)
            video_data = self.get_all_values_unformatted(video_sheet)

            logger.info(f"📊 채널 리스트: {len(channel_data)}행, 영상 시트: {len(video_data)}행")

            # 채널명 → 벤치마킹 채널여부 매핑 생성
            if progress_callback:
                progress_callback(20, 100, "채널 데이터 인덱싱 중...")

            channel_benchmarking_map = {}  # {채널명: 벤치마킹 채널여부}

            for ch_row in channel_data[9:]:  # 10행부터
                if len(ch_row) < 2:
                    continue

                # "가져왔는지 여부" 체크
                brought_status = str(ch_row[1]).strip()
                if not brought_status:
                    continue

                # 채널명 가져오기
                if len(ch_row) < channel_name_col:
                    continue
                channel_name = str(ch_row[channel_name_col - 1]).strip()
                if not channel_name:
                    continue

                # 벤치마킹 채널여부 가져오기
                benchmarking = str(ch_row[channel_benchmarking_col - 1]).strip() if len(ch_row) >= channel_benchmarking_col else ''

                channel_benchmarking_map[channel_name] = benchmarking

            logger.info(f"✅ {len(channel_benchmarking_map)}개 채널의 벤치마킹 정보 인덱싱 완료")

            # 영상 시트 업데이트
            if progress_callback:
                progress_callback(30, 100, "영상 데이터 처리 중...")

            video_updates = []
            update_count = 0
            processed_rows = 0
            total_video_rows = len(video_data) - 9  # 10행부터 계산

            for vid_row_idx, vid_row in enumerate(video_data[9:], 10):
                if len(vid_row) < video_channel_name_col:
                    continue

                # 채널명 가져오기
                video_channel_name = str(vid_row[video_channel_name_col - 1]).strip()
                if not video_channel_name:
                    continue

                # 해당 채널이 채널 리스트에 있는지 확인
                if video_channel_name not in channel_benchmarking_map:
                    continue

                # 채널 리스트의 벤치마킹 채널여부
                ch_benchmarking = channel_benchmarking_map[video_channel_name]

                # 영상 시트의 현재 벤치마킹 채널여부
                vid_benchmarking = str(vid_row[video_benchmarking_col - 1]).strip() if len(vid_row) >= video_benchmarking_col else ''

                # 값이 다른 경우만 업데이트
                if ch_benchmarking != vid_benchmarking:
                    video_updates.append({
                        'range': f'{self._col_num_to_letter(video_benchmarking_col)}{vid_row_idx}',
                        'values': [[ch_benchmarking]]
                    })
                    update_count += 1
                    logger.info(f"📝 영상 '{video_channel_name}' (행: {vid_row_idx}) - 벤치마킹 채널여부: '{vid_benchmarking}' → '{ch_benchmarking}'")

                processed_rows += 1

                # 진행률 업데이트 (매 100행마다)
                if progress_callback and processed_rows % 100 == 0:
                    progress = 30 + int((processed_rows / total_video_rows) * 60)
                    progress_callback(progress, 100, f"영상 처리 중... ({processed_rows}/{total_video_rows})")

            # 일괄 업데이트
            if video_updates:
                if progress_callback:
                    progress_callback(90, 100, f"시트 업데이트 중... ({len(video_updates)}개 셀)")

                logger.info(f"📝 {len(video_updates)}개 셀 업데이트 중...")

                # batch_update API 사용
                video_sheet.batch_update(video_updates, value_input_option='USER_ENTERED')

                logger.info(f"✅ 업데이트 완료: {len(video_updates)}개 셀")

                if progress_callback:
                    progress_callback(100, 100, "완료!")

                logger.info(f"🎉 벤치마킹 채널여부 업데이트 완료: {update_count}개 영상")
                return update_count
            else:
                logger.info(f"⚠️ 업데이트할 데이터가 없습니다")
                return 0

        except Exception as e:
            logger.error(f"❌ 벤치마킹 채널여부 업데이트 실패: {str(e)}")
            raise

    def classify_channels_to_field1_sheet(self, spreadsheet_url, field1_name, progress_callback=None):
        """
        채널 리스트에서 특정 분야1에 해당하는 채널들을 '{분야1}-채널' 시트로 분류
        시트가 없는 경우 채널 리스트를 복제하여 생성 (서식 유지)

        Args:
            spreadsheet_url: 스프레드시트 URL
            field1_name: 분야1 이름 (예: '해외영상')
            progress_callback: 진행률 콜백 함수 (current, total, message)

        Returns:
            (추가된 행 수, 업데이트된 행 수, 시트 생성 여부) 튜플
        """
        try:
            logger.info(f"=== 채널 분류 시작: '{field1_name}' ===")

            if not self.client:
                self.authenticate()

            spreadsheet = self.client.open_by_url(spreadsheet_url)

            # 채널 리스트 시트
            channel_list_sheet = spreadsheet.worksheet("채널 리스트")

            # 채널 리스트 헤더 먼저 읽기 (1행)
            channel_headers = channel_list_sheet.row_values(1)

            # 대상 시트 이름: '{분야1}-채널'
            target_sheet_name = f"{field1_name}-채널"

            # 대상 시트가 존재하는지 확인
            sheet_created = False
            try:
                target_sheet = spreadsheet.worksheet(target_sheet_name)
                logger.info(f"✅ 대상 시트 찾음: '{target_sheet_name}'")
            except Exception as e:
                # 시트가 없으면 채널 리스트를 복제하여 생성
                logger.info(f"⚠️ '{target_sheet_name}' 시트가 없습니다. 채널 리스트를 복제합니다.")

                if progress_callback:
                    progress_callback(0, 100, f"'{target_sheet_name}' 시트 생성 중...")

                try:
                    # 채널 리스트 시트 복제
                    target_sheet = channel_list_sheet.duplicate(new_sheet_name=target_sheet_name)
                    logger.info(f"✅ 시트 복제 완료: '{target_sheet_name}'")

                    # 10행 이후의 데이터 삭제
                    if progress_callback:
                        progress_callback(5, 100, "기존 데이터 지우는 중...")

                    # 시트의 전체 데이터 읽기
                    all_data = self.get_all_values_unformatted(target_sheet)

                    # 10행 이후 데이터가 있으면 내용만 지우기 (행은 유지, 서식 유지)
                    if len(all_data) > 9:
                        rows_to_clear = len(all_data) - 9
                        # 10행부터 마지막 행까지의 전체 범위를 빈 값으로 채우기
                        # A10:ZZ{마지막행} 범위를 빈 문자열로 채움
                        last_col_letter = self._col_num_to_letter(len(channel_headers))
                        clear_range = f'A10:{last_col_letter}{len(all_data)}'

                        # 범위의 모든 셀을 빈 값으로 업데이트
                        empty_values = [[''] * len(channel_headers) for _ in range(rows_to_clear)]
                        target_sheet.update(clear_range, empty_values, value_input_option='USER_ENTERED')

                        logger.info(f"✅ 10행 이후 {rows_to_clear}개 행의 데이터 지우기 완료 (행은 유지, 서식 유지)")

                    sheet_created = True
                    logger.info(f"✅ 새 시트 생성 완료: '{target_sheet_name}' (채널 리스트 복제 및 데이터 지우기)")

                except Exception as create_error:
                    logger.error(f"❌ 시트 생성 실패: {str(create_error)}")
                    raise ValueError(f"'{target_sheet_name}' 시트 생성 중 오류가 발생했습니다: {str(create_error)}")

            if progress_callback:
                progress_callback(10, 100, "헤더 정보 읽는 중...")

            # 대상 시트 헤더 읽기 (1행)
            target_headers = target_sheet.row_values(1)

            logger.info(f"📋 채널 리스트 헤더: {len(channel_headers)}개")
            logger.info(f"📋 대상 시트 헤더: {len(target_headers)}개")

            # 필요한 열 찾기
            # 채널 리스트에서
            channel_field1_col = None
            channel_channel_id_col = None
            fetch_channel_col = None

            for i, h in enumerate(channel_headers, 1):
                clean_h = re.sub(r'^\d+[\.\s]*', '', str(h)).strip()
                if clean_h == '분야1':
                    channel_field1_col = i
                elif clean_h == '채널 ID':
                    channel_channel_id_col = i
                elif clean_h == '가져올 채널':
                    fetch_channel_col = i

            # 대상 시트에서 '채널 ID' 열 찾기
            target_channel_id_col = None

            for i, h in enumerate(target_headers, 1):
                clean_h = re.sub(r'^\d+[\.\s]*', '', str(h)).strip()
                if clean_h == '채널 ID':
                    target_channel_id_col = i

            if not channel_field1_col:
                raise ValueError("채널 리스트에서 '분야1' 열을 찾을 수 없습니다.")
            if not channel_channel_id_col:
                raise ValueError("채널 리스트에서 '채널 ID' 열을 찾을 수 없습니다.")
            if not target_channel_id_col:
                raise ValueError(f"'{target_sheet_name}' 시트에서 '채널 ID' 열을 찾을 수 없습니다.")

            logger.info(f"✅ 채널 리스트 - 분야1: {channel_field1_col}열, 채널 ID: {channel_channel_id_col}열")
            logger.info(f"✅ 대상 시트 - 채널 ID: {target_channel_id_col}열")

            if progress_callback:
                progress_callback(10, 100, "데이터 읽는 중...")

            # 데이터 읽기
            channel_data = self.get_all_values_unformatted(channel_list_sheet)
            target_data = self.get_all_values_unformatted(target_sheet)

            logger.info(f"📊 채널 리스트: {len(channel_data)}행")
            logger.info(f"📊 대상 시트: {len(target_data)}행")

            # 채널 리스트에서 해당 분야1의 채널 ID 추출 (10행부터)
            matching_channel_ids = set()
            for row_idx, row in enumerate(channel_data[9:], start=10):
                if len(row) < max(channel_field1_col, channel_channel_id_col):
                    continue

                # 삭제된 채널 체크 (가져올 채널 값이 'x'인 경우 스킵)
                if self.is_deleted_channel(row, fetch_channel_col):
                    logger.debug(f"⏭️ 행 {row_idx} 스킵 (삭제된 채널: 가져올 채널='x')")
                    continue

                field1_value = str(row[channel_field1_col - 1]).strip()
                channel_id = str(row[channel_channel_id_col - 1]).strip()

                if field1_value == field1_name and channel_id:
                    matching_channel_ids.add(channel_id)

            logger.info(f"🎯 '{field1_name}' 분야1에 속하는 채널: {len(matching_channel_ids)}개")

            if not matching_channel_ids:
                logger.warning("⚠️ 해당 분야1에 속하는 채널이 없습니다.")
                return (0, 0, sheet_created)

            if progress_callback:
                progress_callback(30, 100, f"대상 시트의 기존 데이터 분석 중...")

            # 대상 시트의 기존 채널 ID로 매핑 생성 (10행부터)
            # {채널ID: 행번호}
            existing_channel_ids = {}
            for row_idx, row in enumerate(target_data[9:], start=10):
                if len(row) < target_channel_id_col:
                    continue

                channel_id = str(row[target_channel_id_col - 1]).strip()
                if channel_id:
                    existing_channel_ids[channel_id] = row_idx

            logger.info(f"📊 대상 시트의 기존 채널 ID: {len(existing_channel_ids)}개")

            # 매칭되는 채널의 모든 행을 채널 리스트에서 찾기
            # 여기서는 채널 리스트의 모든 열을 복사
            rows_to_process = []
            for row_idx, row in enumerate(channel_data[9:], start=10):
                if len(row) < channel_channel_id_col:
                    continue

                channel_id = str(row[channel_channel_id_col - 1]).strip()

                if channel_id in matching_channel_ids:
                    rows_to_process.append((row_idx, row))

            logger.info(f"📋 처리할 행: {len(rows_to_process)}개")

            if progress_callback:
                progress_callback(50, 100, f"데이터 분류 중... (0/{len(rows_to_process)})")

            # 업데이트할 행과 추가할 행 분리
            updates = []
            new_rows = []

            # 채널 리스트의 '가져왔는지 여부' 열 찾기
            channel_imported_col = None
            for i, h in enumerate(channel_headers, 1):
                clean_h = re.sub(r'^\d+[\.\s]*', '', str(h)).strip()
                if clean_h == '가져왔는지 여부':
                    channel_imported_col = i
                    break

            # 대상 시트에도 '가져왔는지 여부' 열이 있는지 확인
            target_imported_col = None
            for i, h in enumerate(target_headers, 1):
                clean_h = re.sub(r'^\d+[\.\s]*', '', str(h)).strip()
                if clean_h == '가져왔는지 여부':
                    target_imported_col = i
                    break

            processed_count = 0
            for row_idx, row in rows_to_process:
                # 채널 리스트의 '가져왔는지 여부' 열 확인 (빈 값이 아니어야 함)
                if channel_imported_col:
                    imported_value = str(row[channel_imported_col - 1]).strip() if len(row) >= channel_imported_col else ''
                    if not imported_value:  # 빈 값이면 스킵
                        logger.info(f"⏭️ 행 {row_idx} 스킵 (가져왔는지 여부 열이 비어있음)")
                        processed_count += 1
                        continue

                # 채널 ID 추출
                channel_id = str(row[channel_channel_id_col - 1]).strip() if len(row) >= channel_channel_id_col else ''

                if not channel_id:
                    logger.warning(f"⚠️ 행 {row_idx}: 채널 ID가 없음 - 스킵")
                    processed_count += 1
                    continue

                # 중복 체크 (채널 ID 기준)
                if channel_id in existing_channel_ids:
                    # 업데이트
                    target_row_idx = existing_channel_ids[channel_id]

                    # 전체 행 업데이트 (헤더 개수만큼)
                    update_values = []
                    for i in range(len(target_headers)):
                        if i < len(row):
                            update_values.append(row[i])
                        else:
                            update_values.append('')

                    updates.append({
                        'range': f'A{target_row_idx}:{self._col_num_to_letter(len(target_headers))}{target_row_idx}',
                        'values': [update_values]
                    })
                    logger.info(f"🔄 업데이트: 채널 ID '{channel_id}' (대상 시트 {target_row_idx}행)")
                else:
                    # 새 행 추가
                    new_row_values = []
                    for i in range(len(target_headers)):
                        if i < len(row):
                            new_row_values.append(row[i])
                        else:
                            new_row_values.append('')

                    new_rows.append(new_row_values)
                    logger.info(f"➕ 추가: 채널 ID '{channel_id}'")

                processed_count += 1
                if progress_callback and processed_count % 10 == 0:
                    progress = 50 + int((processed_count / len(rows_to_process)) * 40)
                    progress_callback(progress, 100, f"데이터 분류 중... ({processed_count}/{len(rows_to_process)})")

            logger.info(f"📝 업데이트할 행: {len(updates)}개")
            logger.info(f"📝 추가할 행: {len(new_rows)}개")

            # 시트 업데이트 실행
            if progress_callback:
                progress_callback(90, 100, "시트 업데이트 중...")

            # 업데이트 실행
            if updates:
                target_sheet.batch_update(updates, value_input_option='USER_ENTERED')
                logger.info(f"✅ {len(updates)}개 행 업데이트 완료")

            # 새 행 추가
            if new_rows:
                # 마지막 행 다음에 추가
                start_row = len(target_data) + 1
                target_sheet.append_rows(new_rows, value_input_option='USER_ENTERED')
                logger.info(f"✅ {len(new_rows)}개 행 추가 완료 (시작 행: {start_row})")

            if progress_callback:
                progress_callback(100, 100, "완료!")

            logger.info(f"🎉 채널 분류 완료: 업데이트 {len(updates)}개, 추가 {len(new_rows)}개")
            return (len(new_rows), len(updates), sheet_created)

        except Exception as e:
            logger.error(f"❌ 채널 분류 실패: {str(e)}")
            raise

    def get_field1_list_from_channel_list(self, spreadsheet_url):
        """
        채널 리스트 시트에서 '가져왔는지 여부'가 빈 행이 아닌 채널들의 분야1 목록과 개수 반환

        Args:
            spreadsheet_url: 스프레드시트 URL

        Returns:
            dict: {분야1: 채널수} 딕셔너리
        """
        try:
            logger.info("=== 채널 리스트에서 분야1 목록 추출 시작 ===")

            if not self.client:
                self.authenticate()

            spreadsheet = self.client.open_by_url(spreadsheet_url)
            channel_list_sheet = spreadsheet.worksheet("채널 리스트")

            # 헤더 읽기 (1행)
            channel_headers = channel_list_sheet.row_values(1)

            # 필요한 열 찾기
            field1_col = None
            imported_col = None
            fetch_channel_col = None

            for i, h in enumerate(channel_headers, 1):
                clean_h = re.sub(r'^\d+[\.\s]*', '', str(h)).strip()
                if clean_h == '분야1':
                    field1_col = i
                elif clean_h == '가져왔는지 여부':
                    imported_col = i
                elif clean_h == '가져올 채널':
                    fetch_channel_col = i

            if not field1_col:
                raise ValueError("채널 리스트에서 '분야1' 열을 찾을 수 없습니다.")

            logger.info(f"✅ 채널 리스트 - 분야1: {field1_col}열, 가져왔는지 여부: {imported_col}열, 가져올 채널: {fetch_channel_col}열")

            # 데이터 읽기
            channel_data = self.get_all_values_unformatted(channel_list_sheet)
            logger.info(f"📊 채널 리스트: {len(channel_data)}행")

            # 분야1별 채널 개수 집계
            field1_counts = {}

            for row_idx, row in enumerate(channel_data[9:], start=10):
                if len(row) < max(field1_col, imported_col or 0):
                    continue

                # '가져왔는지 여부' 체크 (열이 있는 경우에만)
                if imported_col:
                    imported_value = str(row[imported_col - 1]).strip() if len(row) >= imported_col else ''
                    if not imported_value:  # 빈 값이면 스킵
                        continue

                # 삭제된 채널 체크 (가져올 채널 값이 'x'인 경우 스킵)
                if self.is_deleted_channel(row, fetch_channel_col):
                    logger.debug(f"⏭️ 행 {row_idx} 스킵 (삭제된 채널: 가져올 채널='x')")
                    continue

                # 분야1 값
                field1_value = str(row[field1_col - 1]).strip() if len(row) >= field1_col else ''

                if field1_value:
                    field1_counts[field1_value] = field1_counts.get(field1_value, 0) + 1

            logger.info(f"✅ 분야1 목록 추출 완료: {len(field1_counts)}개 분야")
            for field1, count in sorted(field1_counts.items()):
                logger.info(f"   - {field1}: {count}개 채널")

            return field1_counts

        except Exception as e:
            logger.error(f"❌ 분야1 목록 추출 실패: {str(e)}")
            raise

    def delete_deleted_channels_videos_from_sheet(self, spreadsheet_url, video_sheet_name, progress_callback=None):
        """
        채널 리스트의 '가져올 채널'='x'인 채널의 모든 영상을 선택된 시트에서 행 삭제

        Args:
            spreadsheet_url: 스프레드시트 URL
            video_sheet_name: 영상 시트 이름
            progress_callback: 진행률 콜백 함수

        Returns:
            삭제된 행 수
        """
        try:
            logger.info("=== 삭제된 채널 영상 삭제 시작 ===")
            logger.info(f"영상 시트: {video_sheet_name}")

            if not self.client:
                self.authenticate()

            spreadsheet = self.client.open_by_url(spreadsheet_url)
            channel_sheet = spreadsheet.worksheet("채널 리스트")
            video_sheet = spreadsheet.worksheet(video_sheet_name)

            if progress_callback:
                progress_callback(0, 100, "채널 리스트에서 삭제된 채널 찾는 중...")

            # 채널 리스트 헤더 읽기
            channel_headers = channel_sheet.row_values(1)

            # 필요한 열 찾기
            channel_name_col = None
            fetch_channel_col = None

            for i, h in enumerate(channel_headers, 1):
                clean_h = re.sub(r'^\d+[\.\s]*', '', str(h)).strip()
                if clean_h == '채널명':
                    channel_name_col = i
                elif clean_h == '가져올 채널':
                    fetch_channel_col = i

            if not channel_name_col:
                raise ValueError("채널 리스트에서 '채널명' 열을 찾을 수 없습니다.")

            logger.info(f"✅ 채널 리스트 - 채널명: {channel_name_col}열, 가져올 채널: {fetch_channel_col}열")

            # 채널 리스트 데이터 읽기
            channel_data = self.get_all_values_unformatted(channel_sheet)
            logger.info(f"📊 채널 리스트: {len(channel_data)}행")

            # 삭제된 채널명 추출 (가져올 채널='x'인 채널)
            deleted_channel_names = set()
            for row_idx, row in enumerate(channel_data[9:], start=10):
                if len(row) < channel_name_col:
                    continue

                # 삭제된 채널 체크
                if self.is_deleted_channel(row, fetch_channel_col):
                    channel_name = str(row[channel_name_col - 1]).strip()
                    if channel_name:
                        deleted_channel_names.add(channel_name)
                        logger.info(f"🗑️  삭제된 채널: '{channel_name}' (행 {row_idx})")

            logger.info(f"📋 삭제된 채널: {len(deleted_channel_names)}개")

            if not deleted_channel_names:
                logger.info("⚠️ 삭제된 채널이 없습니다 (가져올 채널='x'인 채널 없음).")
                if progress_callback:
                    progress_callback(100, 100, "삭제된 채널이 없습니다.")
                return 0

            if progress_callback:
                progress_callback(30, 100, f"영상 시트에서 삭제 대상 영상 찾는 중... (삭제된 채널: {len(deleted_channel_names)}개)")

            # 영상 시트 헤더 읽기
            video_headers = video_sheet.row_values(1)

            # 채널명 열 찾기
            video_channel_name_col = None
            for i, h in enumerate(video_headers, 1):
                clean_h = re.sub(r'^\d+[\.\s]*', '', str(h)).strip()
                if clean_h == '채널명':
                    video_channel_name_col = i
                    break

            if not video_channel_name_col:
                raise ValueError(f"'{video_sheet_name}' 시트에서 '채널명' 열을 찾을 수 없습니다.")

            logger.info(f"✅ 영상 시트 - 채널명: {video_channel_name_col}열")

            # 영상 시트 데이터 읽기
            video_data = self.get_all_values_unformatted(video_sheet)
            logger.info(f"📊 영상 시트: {len(video_data)}행")

            # 삭제할 행 번호 수집 (10행부터)
            rows_to_delete = []
            for row_idx, row in enumerate(video_data[9:], start=10):
                if len(row) < video_channel_name_col:
                    continue

                channel_name = str(row[video_channel_name_col - 1]).strip()
                if channel_name in deleted_channel_names:
                    rows_to_delete.append(row_idx)

            logger.info(f"📋 삭제할 영상 행: {len(rows_to_delete)}개")

            if not rows_to_delete:
                logger.info("⚠️ 삭제할 영상이 없습니다.")
                if progress_callback:
                    progress_callback(100, 100, "삭제할 영상이 없습니다.")
                return 0

            if progress_callback:
                progress_callback(60, 100, f"영상 행 삭제 중... ({len(rows_to_delete)}개 행)")

            # 연속된 행들을 그룹화하여 배치 삭제 (API 호출 최소화)
            # 행을 정렬하고 연속된 범위를 찾음
            sorted_rows = sorted(rows_to_delete, reverse=True)  # 뒤에서부터 삭제

            # 연속된 행 범위 찾기
            ranges_to_delete = []
            if sorted_rows:
                range_start = sorted_rows[0]
                range_end = sorted_rows[0]

                for i in range(1, len(sorted_rows)):
                    if sorted_rows[i] == range_end - 1:
                        # 연속된 행
                        range_end = sorted_rows[i]
                    else:
                        # 범위 끝, 새 범위 시작
                        ranges_to_delete.append((range_end, range_start))
                        range_start = sorted_rows[i]
                        range_end = sorted_rows[i]

                # 마지막 범위 추가
                ranges_to_delete.append((range_end, range_start))

            logger.info(f"📋 연속된 범위로 그룹화: {len(ranges_to_delete)}개 범위")
            for start, end in ranges_to_delete[:5]:  # 처음 5개만 로깅
                if start == end:
                    logger.info(f"   - 행 {start}")
                else:
                    logger.info(f"   - 행 {start}~{end} ({end - start + 1}개)")
            if len(ranges_to_delete) > 5:
                logger.info(f"   ... 외 {len(ranges_to_delete) - 5}개 범위")

            # 배치로 행 삭제 실행
            deleted_count = 0
            total_ranges = len(ranges_to_delete)

            for idx, (start_row, end_row) in enumerate(ranges_to_delete, 1):
                try:
                    num_rows = end_row - start_row + 1
                    video_sheet.delete_rows(start_row, end_row)
                    deleted_count += num_rows

                    if num_rows == 1:
                        logger.info(f"✅ [{idx}/{total_ranges}] 행 {start_row} 삭제 완료")
                    else:
                        logger.info(f"✅ [{idx}/{total_ranges}] 행 {start_row}~{end_row} 삭제 완료 ({num_rows}개)")

                    # 진행률 업데이트
                    if progress_callback:
                        progress = 60 + int((idx / total_ranges) * 35)
                        progress_callback(progress, 100, f"영상 행 삭제 중... ({deleted_count}/{len(rows_to_delete)})")

                except Exception as e:
                    logger.error(f"❌ 행 {start_row}~{end_row} 삭제 실패: {str(e)}")

            logger.info(f"✅ 삭제된 채널 영상 삭제 완료: {deleted_count}개 행 삭제 ({total_ranges}개 API 호출)")

            if progress_callback:
                progress_callback(100, 100, f"완료: {deleted_count}개 행 삭제")

            return deleted_count

        except Exception as e:
            logger.error(f"❌ 삭제된 채널 영상 삭제 실패: {str(e)}")
            raise

    def _col_num_to_letter(self, n):
        """열 번호를 문자로 변환 (1 -> A, 27 -> AA)"""
        result = ""
        while n > 0:
            n -= 1
            result = chr(n % 26 + ord('A')) + result
            n //= 26
        return result

    def copy_transcript_data_between_sheets(self, spreadsheet_url, source_sheet_name, target_sheet_name, progress_callback=None):
        """
        영상 ID 기준으로 원본 시트에서 타겟 시트로 대본 관련 데이터 복사

        Args:
            spreadsheet_url: 스프레드시트 URL
            source_sheet_name: 원본 시트 이름
            target_sheet_name: 타겟 시트 이름
            progress_callback: 진행률 콜백 함수

        Returns:
            복사된 영상 수
        """
        try:
            logger.info(f"=== 대본 데이터 복사 시작 ===")
            logger.info(f"원본 시트: {source_sheet_name}")
            logger.info(f"타겟 시트: {target_sheet_name}")

            if not self.client:
                self.authenticate()

            spreadsheet = self.client.open_by_url(spreadsheet_url)
            source_sheet = spreadsheet.worksheet(source_sheet_name)
            target_sheet = spreadsheet.worksheet(target_sheet_name)

            if progress_callback:
                progress_callback(0, 100, "헤더 정보 읽는 중...")

            # 헤더 읽기 (9행)
            source_headers = source_sheet.row_values(9)
            target_headers = target_sheet.row_values(9)

            # 복사할 헤더 목록 (넘버링 제외)
            copy_fields = ['대본내용', '분석', '대본파일', '썸네일 여부', '썸네일 이미지주소', '썸네일 경로']

            # 원본 시트에서 필요한 열 찾기
            source_video_id_col = None
            source_field_cols = {}

            for i, h in enumerate(source_headers, 1):
                clean_h = re.sub(r'^\d+[\.\s]*', '', str(h)).strip()
                if clean_h == '영상 ID':
                    source_video_id_col = i
                elif clean_h in copy_fields:
                    source_field_cols[clean_h] = i

            # 타겟 시트에서 필요한 열 찾기
            target_video_id_col = None
            target_field_cols = {}

            for i, h in enumerate(target_headers, 1):
                clean_h = re.sub(r'^\d+[\.\s]*', '', str(h)).strip()
                if clean_h == '영상 ID':
                    target_video_id_col = i
                elif clean_h in copy_fields:
                    target_field_cols[clean_h] = i

            # 필수 헤더 확인
            if not source_video_id_col or not target_video_id_col:
                raise ValueError("영상 ID 열을 찾을 수 없습니다.")

            # 복사할 필드 중 양쪽 시트에 모두 있는 것만 선택
            valid_copy_fields = [field for field in copy_fields
                               if field in source_field_cols and field in target_field_cols]

            if not valid_copy_fields:
                raise ValueError("복사 가능한 헤더가 없습니다.")

            logger.info(f"📌 복사 대상 필드: {', '.join(valid_copy_fields)}")
            logger.info(f"📌 원본 시트 - 영상 ID: {source_video_id_col}열")
            logger.info(f"📌 타겟 시트 - 영상 ID: {target_video_id_col}열")

            # 데이터 읽기
            if progress_callback:
                progress_callback(10, 100, "데이터 읽는 중...")

            source_data = self.get_all_values_unformatted(source_sheet)
            target_data = self.get_all_values_unformatted(target_sheet)

            logger.info(f"📊 원본 시트: {len(source_data)}행, 타겟 시트: {len(target_data)}행")

            # 원본 데이터 인덱싱: {영상ID: (행 데이터, 행 번호)}
            if progress_callback:
                progress_callback(20, 100, "원본 데이터 인덱싱 중...")

            source_by_video_id = {}
            for source_row_idx, source_row in enumerate(source_data[9:], 10):  # 10행부터
                if len(source_row) < source_video_id_col:
                    continue

                video_id = str(source_row[source_video_id_col - 1]).strip()
                if not video_id:
                    continue

                source_by_video_id[video_id] = (source_row, source_row_idx)

            logger.info(f"✅ {len(source_by_video_id)}개 영상 ID 인덱싱 완료 (원본)")

            # 타겟 시트 처리
            if progress_callback:
                progress_callback(30, 100, "타겟 데이터 처리 중...")

            updates = []
            format_updates = []  # 표시 형식 복사용
            copy_count = 0
            processed_rows = 0
            total_target_rows = len(target_data) - 9

            for target_row_idx, target_row in enumerate(target_data[9:], 10):
                if len(target_row) < target_video_id_col:
                    continue

                # 타겟 시트의 영상 ID
                video_id = str(target_row[target_video_id_col - 1]).strip()
                if not video_id:
                    continue

                # 원본 시트에 해당 영상 ID가 있는지 확인
                if video_id not in source_by_video_id:
                    continue

                source_row, source_row_idx = source_by_video_id[video_id]

                # 각 필드 복사 여부 확인 및 업데이트 준비
                row_updated = False
                for field in valid_copy_fields:
                    source_col = source_field_cols[field]
                    target_col = target_field_cols[field]

                    # 원본 값 가져오기
                    source_value = str(source_row[source_col - 1]).strip() if len(source_row) >= source_col else ''

                    # 타겟 값 가져오기
                    target_value = str(target_row[target_col - 1]).strip() if len(target_row) >= target_col else ''

                    # 복사 조건: 원본에 값이 있고, 타겟이 비어있는 경우
                    if source_value and not target_value:
                        target_range = f'{target_sheet.title}!{self._col_num_to_letter(target_col)}{target_row_idx}'
                        updates.append({
                            'range': target_range,
                            'values': [[source_value]]
                        })
                        # 표시 형식 복사를 위한 정보 저장
                        format_updates.append({
                            'range': target_range,
                            'source_col': source_col,
                            'source_row': source_row_idx
                        })
                        row_updated = True
                        logger.info(f"📝 영상 ID '{video_id}' (행: {target_row_idx}) - {field}: 복사 예정")

                if row_updated:
                    copy_count += 1

                processed_rows += 1

                # 진행률 업데이트 (매 100행마다)
                if progress_callback and processed_rows % 100 == 0:
                    progress = 30 + int((processed_rows / total_target_rows) * 60)
                    progress_callback(progress, 100, f"타겟 처리 중... ({processed_rows}/{total_target_rows})")

            # 일괄 업데이트
            if updates:
                if progress_callback:
                    progress_callback(90, 100, f"시트 업데이트 중... ({len(updates)}개 셀)")

                logger.info(f"📝 {len(updates)}개 셀 업데이트 중...")

                # batch_update API 사용 (50개씩)
                import time
                batch_size = 50
                total_batches = (len(updates) + batch_size - 1) // batch_size

                for batch_idx in range(0, len(updates), batch_size):
                    batch = updates[batch_idx:batch_idx + batch_size]
                    current_batch_num = (batch_idx // batch_size) + 1

                    try:
                        target_sheet.spreadsheet.values_batch_update(
                            body={
                                'valueInputOption': 'USER_ENTERED',
                                'data': batch
                            }
                        )

                        logger.info(f"    진행: {min(batch_idx + batch_size, len(updates))}/{len(updates)} 셀 완료 (배치 {current_batch_num}/{total_batches})")

                        # 배치 간 대기
                        if batch_idx + batch_size < len(updates):
                            time.sleep(1.2)

                    except Exception as update_err:
                        error_msg = str(update_err)
                        logger.error(f"배치 업데이트 실패 (배치 {current_batch_num}/{total_batches}): {error_msg}")

                        if '429' in error_msg or 'quota' in error_msg.lower():
                            logger.warning("API 제한 도달, 60초 대기 후 재시도...")
                            time.sleep(60)
                            target_sheet.spreadsheet.values_batch_update(
                                body={
                                    'valueInputOption': 'USER_ENTERED',
                                    'data': batch
                                }
                            )
                            logger.info(f"    재시도 성공: 배치 {current_batch_num}/{total_batches}")
                        else:
                            raise

                # 표시 형식 복사
                if format_updates:
                    if progress_callback:
                        progress_callback(95, 100, f"표시 형식 복사 중... ({len(format_updates)}개 셀)")

                    logger.info(f"📝 {len(format_updates)}개 셀의 표시 형식 복사 중...")
                    self._copy_cell_formats_for_updates(source_sheet, target_sheet, format_updates)

                if progress_callback:
                    progress_callback(100, 100, "완료!")

                logger.info(f"🎉 대본 데이터 복사 완료: {copy_count}개 영상")
                return copy_count
            else:
                logger.info(f"⚠️ 복사할 데이터가 없습니다")
                return 0

        except Exception as e:
            logger.error(f"❌ 대본 데이터 복사 실패: {str(e)}")
            raise

    def add_new_videos_to_target_sheet(self, spreadsheet_url, source_sheet_name, target_sheet_name, progress_callback=None):
        """
        원본 시트에서 타겟 시트로 신규 영상 추가 (영상 ID 기준 중복 제거)

        Args:
            spreadsheet_url: 스프레드시트 URL
            source_sheet_name: 원본 시트 이름
            target_sheet_name: 타겟 시트 이름
            progress_callback: 진행률 콜백 함수

        Returns:
            추가된 영상 수
        """
        try:
            logger.info(f"=== 신규 영상 추가 시작 ===")
            logger.info(f"원본 시트: {source_sheet_name}")
            logger.info(f"타겟 시트: {target_sheet_name}")

            if not self.client:
                self.authenticate()

            spreadsheet = self.client.open_by_url(spreadsheet_url)
            source_sheet = spreadsheet.worksheet(source_sheet_name)
            target_sheet = spreadsheet.worksheet(target_sheet_name)

            if progress_callback:
                progress_callback(0, 100, "헤더 정보 읽는 중...")

            # 헤더 읽기 (9행)
            source_headers = source_sheet.row_values(9)
            target_headers = target_sheet.row_values(9)

            # 영상 ID 열 찾기
            source_video_id_col = None
            target_video_id_col = None

            for i, h in enumerate(source_headers, 1):
                clean_h = re.sub(r'^\d+[\.\s]*', '', str(h)).strip()
                if clean_h == '영상 ID':
                    source_video_id_col = i
                    break

            for i, h in enumerate(target_headers, 1):
                clean_h = re.sub(r'^\d+[\.\s]*', '', str(h)).strip()
                if clean_h == '영상 ID':
                    target_video_id_col = i
                    break

            if not source_video_id_col or not target_video_id_col:
                raise ValueError("영상 ID 열을 찾을 수 없습니다.")

            logger.info(f"📌 원본 시트 - 영상 ID: {source_video_id_col}열")
            logger.info(f"📌 타겟 시트 - 영상 ID: {target_video_id_col}열")

            # 데이터 읽기
            if progress_callback:
                progress_callback(10, 100, "데이터 읽는 중...")

            source_data = self.get_all_values_unformatted(source_sheet)
            target_data = self.get_all_values_unformatted(target_sheet)

            logger.info(f"📊 원본 시트: {len(source_data)}행, 타겟 시트: {len(target_data)}행")

            # 타겟 시트의 기존 영상 ID 수집
            if progress_callback:
                progress_callback(20, 100, "기존 영상 ID 수집 중...")

            existing_video_ids = set()
            for target_row in target_data[9:]:  # 10행부터
                if len(target_row) < target_video_id_col:
                    continue
                video_id = str(target_row[target_video_id_col - 1]).strip()
                if video_id:
                    existing_video_ids.add(video_id)

            logger.info(f"✅ 타겟 시트에 {len(existing_video_ids)}개 영상 ID 존재")

            # 신규 영상 찾기
            if progress_callback:
                progress_callback(40, 100, "신규 영상 찾는 중...")

            new_rows = []
            for source_row in source_data[9:]:  # 10행부터
                if len(source_row) < source_video_id_col:
                    continue
                video_id = str(source_row[source_video_id_col - 1]).strip()
                if not video_id:
                    continue

                # 타겟 시트에 없는 영상만 추가
                if video_id not in existing_video_ids:
                    new_rows.append(source_row)
                    logger.info(f"📝 신규 영상 발견: '{video_id}'")

            logger.info(f"📌 신규 영상: {len(new_rows)}개")

            # 신규 영상 추가
            if new_rows:
                if progress_callback:
                    progress_callback(60, 100, f"신규 영상 추가 중... ({len(new_rows)}개)")

                # 타겟 시트의 마지막 행 다음에 추가
                next_row = len(target_data) + 1

                # 행 추가 (append)
                logger.info(f"📝 {len(new_rows)}개 행 추가 중... (시작 행: {next_row})")
                target_sheet.append_rows(new_rows, value_input_option='USER_ENTERED')

                # 표시 형식 복사
                if progress_callback:
                    progress_callback(80, 100, f"표시 형식 복사 중... ({len(new_rows)}개 행)")

                logger.info(f"📝 {len(new_rows)}개 행의 표시 형식 복사 중...")
                self._copy_number_formats(source_sheet, target_sheet, next_row, len(new_rows))

                # 수집날짜 열에 표시 형식(yyyy-mm-dd) 명시적 적용
                try:
                    target_headers = target_sheet.row_values(1)
                    for idx, header in enumerate(target_headers, 1):
                        if '수집날짜' in str(header):
                            self._apply_date_format_to_column(target_sheet, idx, next_row, next_row + len(new_rows))
                            logger.debug(f"  ✅ 추가된 행의 수집날짜 열({idx}) 형식 적용 완료")
                            break
                except Exception as e:
                    logger.warning(f"⚠️ 수집날짜 형식 적용 중 오류: {e}")

                if progress_callback:
                    progress_callback(100, 100, "완료!")

                logger.info(f"🎉 신규 영상 추가 완료: {len(new_rows)}개")
                return len(new_rows)
            else:
                logger.info(f"⚠️ 추가할 신규 영상이 없습니다")
                return 0

        except Exception as e:
            logger.error(f"❌ 신규 영상 추가 실패: {str(e)}")
            raise

    def update_existing_videos_by_collect_date(self, spreadsheet_url, source_sheet_name, target_sheet_name, progress_callback=None):
        """
        수집날짜 기준으로 타겟 시트의 기존 영상 업데이트
        원본의 copy_fields가 비어있고 타겟에 값이 있으면 덮어쓰지 않음

        Args:
            spreadsheet_url: 스프레드시트 URL
            source_sheet_name: 원본 시트 이름
            target_sheet_name: 타겟 시트 이름
            progress_callback: 진행률 콜백 함수

        Returns:
            업데이트된 영상 수
        """
        try:
            logger.info(f"=== 기존 영상 업데이트 시작 ===")
            logger.info(f"원본 시트: {source_sheet_name}")
            logger.info(f"타겟 시트: {target_sheet_name}")

            if not self.client:
                self.authenticate()

            spreadsheet = self.client.open_by_url(spreadsheet_url)
            source_sheet = spreadsheet.worksheet(source_sheet_name)
            target_sheet = spreadsheet.worksheet(target_sheet_name)

            if progress_callback:
                progress_callback(0, 100, "헤더 정보 읽는 중...")

            # 헤더 읽기 (9행)
            source_headers = source_sheet.row_values(9)
            target_headers = target_sheet.row_values(9)

            # 보존할 필드 목록
            copy_fields = ['대본내용', '분석', '대본파일', '썸네일 여부', '썸네일 이미지주소', '썸네일 경로']

            # 원본 시트에서 필요한 열 찾기
            source_video_id_col = None
            source_collect_date_col = None
            source_field_cols = {}
            source_col_mapping = {}  # 모든 열 매핑

            for i, h in enumerate(source_headers, 1):
                clean_h = re.sub(r'^\d+[\.\s]*', '', str(h)).strip()
                source_col_mapping[i] = clean_h  # 모든 열 저장
                if clean_h == '영상 ID':
                    source_video_id_col = i
                elif clean_h == '수집날짜':
                    source_collect_date_col = i
                elif clean_h in copy_fields:
                    source_field_cols[clean_h] = i

            # 타겟 시트에서 필요한 열 찾기
            target_video_id_col = None
            target_collect_date_col = None
            target_field_cols = {}
            target_col_mapping = {}  # 모든 열 매핑

            for i, h in enumerate(target_headers, 1):
                clean_h = re.sub(r'^\d+[\.\s]*', '', str(h)).strip()
                target_col_mapping[i] = clean_h  # 모든 열 저장
                if clean_h == '영상 ID':
                    target_video_id_col = i
                elif clean_h == '수집날짜':
                    target_collect_date_col = i
                elif clean_h in copy_fields:
                    target_field_cols[clean_h] = i

            # 필수 헤더 확인
            if not source_video_id_col or not target_video_id_col:
                raise ValueError("영상 ID 열을 찾을 수 없습니다.")
            if not source_collect_date_col or not target_collect_date_col:
                raise ValueError("수집날짜 열을 찾을 수 없습니다.")

            logger.info(f"📌 원본 시트 - 영상 ID: {source_video_id_col}열, 수집날짜: {source_collect_date_col}열")
            logger.info(f"📌 타겟 시트 - 영상 ID: {target_video_id_col}열, 수집날짜: {target_collect_date_col}열")

            # 데이터 읽기
            if progress_callback:
                progress_callback(10, 100, "데이터 읽는 중...")

            source_data = self.get_all_values_unformatted(source_sheet)
            target_data = self.get_all_values_unformatted(target_sheet)

            logger.info(f"📊 원본 시트: {len(source_data)}행, 타겟 시트: {len(target_data)}행")

            # 원본 데이터 인덱싱: {영상ID: (행 데이터, 수집날짜, 행 번호)}
            if progress_callback:
                progress_callback(20, 100, "원본 데이터 인덱싱 중...")

            source_by_video_id = {}
            for source_row_idx, source_row in enumerate(source_data[9:], 10):  # 10행부터
                if len(source_row) < max(source_video_id_col, source_collect_date_col):
                    continue

                video_id = str(source_row[source_video_id_col - 1]).strip()
                collect_date = str(source_row[source_collect_date_col - 1]).strip()

                if not video_id or not collect_date:
                    continue

                source_by_video_id[video_id] = (source_row, collect_date, source_row_idx)

            logger.info(f"✅ {len(source_by_video_id)}개 영상 ID 인덱싱 완료 (원본)")

            # 타겟 시트 처리
            if progress_callback:
                progress_callback(40, 100, "타겟 데이터 처리 중...")

            updates = []
            format_updates = []  # 표시 형식 복사용
            update_count = 0
            processed_rows = 0
            total_target_rows = len(target_data) - 9

            for target_row_idx, target_row in enumerate(target_data[9:], 10):
                if len(target_row) < max(target_video_id_col, target_collect_date_col):
                    continue

                # 타겟 시트의 영상 ID와 수집날짜
                video_id = str(target_row[target_video_id_col - 1]).strip()
                target_date = str(target_row[target_collect_date_col - 1]).strip()

                if not video_id or not target_date:
                    continue

                # 원본 시트에 해당 영상 ID가 있는지 확인
                if video_id not in source_by_video_id:
                    continue

                source_row, source_date, source_row_idx = source_by_video_id[video_id]

                # 수집날짜 비교 (원본이 더 최신인 경우만 업데이트)
                if source_date <= target_date:
                    continue

                logger.info(f"📝 영상 ID '{video_id}' (행: {target_row_idx}) - 수집날짜 업데이트: {target_date} → {source_date}")

                # 모든 열 업데이트 (단, copy_fields는 조건부)
                row_updated = False
                for col_idx in range(1, len(source_headers) + 1):
                    if col_idx not in source_col_mapping:
                        continue

                    field_name = source_col_mapping[col_idx]

                    # 해당 열이 타겟에도 존재하는지 확인
                    target_col_idx = None
                    for t_idx, t_name in target_col_mapping.items():
                        if t_name == field_name:
                            target_col_idx = t_idx
                            break

                    if target_col_idx is None:
                        continue

                    # 원본 값과 타겟 값 가져오기
                    source_value = str(source_row[col_idx - 1]).strip() if len(source_row) >= col_idx else ''
                    target_value = str(target_row[target_col_idx - 1]).strip() if len(target_row) >= target_col_idx else ''

                    # copy_fields에 속한 필드는 조건부 업데이트
                    if field_name in copy_fields:
                        # 원본이 비어있고 타겟에 값이 있으면 덮어쓰지 않음 (보존)
                        if not source_value and target_value:
                            logger.info(f"  → {field_name}: 타겟 값 보존 (원본 비어있음)")
                            continue

                    # 값이 다른 경우만 업데이트
                    if source_value != target_value:
                        target_range = f'{target_sheet.title}!{self._col_num_to_letter(target_col_idx)}{target_row_idx}'
                        updates.append({
                            'range': target_range,
                            'values': [[source_value]]
                        })
                        # 표시 형식 복사를 위한 정보 저장
                        format_updates.append({
                            'range': target_range,
                            'source_col': col_idx,
                            'source_row': source_row_idx
                        })
                        row_updated = True

                if row_updated:
                    update_count += 1

                processed_rows += 1

                # 진행률 업데이트 (매 50행마다)
                if progress_callback and processed_rows % 50 == 0:
                    progress = 40 + int((processed_rows / total_target_rows) * 50)
                    progress_callback(progress, 100, f"타겟 처리 중... ({processed_rows}/{total_target_rows})")

            # 일괄 업데이트
            if updates:
                if progress_callback:
                    progress_callback(90, 100, f"시트 업데이트 중... ({len(updates)}개 셀)")

                logger.info(f"📝 {len(updates)}개 셀 업데이트 중...")

                # batch_update API 사용 (50개씩)
                import time
                batch_size = 50
                total_batches = (len(updates) + batch_size - 1) // batch_size

                for batch_idx in range(0, len(updates), batch_size):
                    batch = updates[batch_idx:batch_idx + batch_size]
                    current_batch_num = (batch_idx // batch_size) + 1

                    try:
                        target_sheet.spreadsheet.values_batch_update(
                            body={
                                'valueInputOption': 'USER_ENTERED',
                                'data': batch
                            }
                        )

                        logger.info(f"    진행: {min(batch_idx + batch_size, len(updates))}/{len(updates)} 셀 완료 (배치 {current_batch_num}/{total_batches})")

                        # 배치 간 대기
                        if batch_idx + batch_size < len(updates):
                            time.sleep(1.2)

                    except Exception as update_err:
                        error_msg = str(update_err)
                        logger.error(f"배치 업데이트 실패 (배치 {current_batch_num}/{total_batches}): {error_msg}")

                        if '429' in error_msg or 'quota' in error_msg.lower():
                            logger.warning("API 제한 도달, 60초 대기 후 재시도...")
                            time.sleep(60)
                            target_sheet.spreadsheet.values_batch_update(
                                body={
                                    'valueInputOption': 'USER_ENTERED',
                                    'data': batch
                                }
                            )
                            logger.info(f"    재시도 성공: 배치 {current_batch_num}/{total_batches}")
                        else:
                            raise

                # 표시 형식 복사
                if format_updates:
                    if progress_callback:
                        progress_callback(95, 100, f"표시 형식 복사 중... ({len(format_updates)}개 셀)")

                    logger.info(f"📝 {len(format_updates)}개 셀의 표시 형식 복사 중...")
                    self._copy_cell_formats_for_updates(source_sheet, target_sheet, format_updates)

                if progress_callback:
                    progress_callback(100, 100, "완료!")

                logger.info(f"🎉 기존 영상 업데이트 완료: {update_count}개")
                return update_count
            else:
                logger.info(f"⚠️ 업데이트할 영상이 없습니다")
                return 0

        except Exception as e:
            logger.error(f"❌ 기존 영상 업데이트 실패: {str(e)}")
            raise

    def paste_to_target_sheet(self, spreadsheet_url, filtered_data, target_sheet_name="조건 추출 영상", start_row=10, source_sheet_name=None):
        """조건에 맞는 데이터를 대상 시트에 붙여넣기"""

        if not filtered_data:
            logger.info("붙여넣을 데이터가 없습니다.")
            return True

        try:
            # 스프레드시트 열기
            spreadsheet = self.client.open_by_url(spreadsheet_url)

            # 원본 시트 결정
            source_sheet = None
            if source_sheet_name:
                source_sheet = spreadsheet.worksheet(source_sheet_name)
            else:
                # 첫 번째 시트가 "조건 추출 영상"이 아닌 시트 찾기
                for ws in spreadsheet.worksheets():
                    if ws.title != target_sheet_name:
                        source_sheet = ws
                        break
                else:
                    source_sheet = spreadsheet.worksheets()[0]

            # 대상 시트 가져오기 또는 생성
            try:
                target_worksheet = spreadsheet.worksheet(target_sheet_name)
                logger.info(f"기존 '{target_sheet_name}' 시트를 사용합니다.")
                is_new_sheet = False
            except gspread.WorksheetNotFound:
                logger.info(f"'{target_sheet_name}' 시트를 새로 생성합니다.")
                target_worksheet = spreadsheet.add_worksheet(
                    title=target_sheet_name,
                    rows=1000,
                    cols=60  # 60개 열로 확장 (헤더 참고 사항에 맞춤)
                )
                is_new_sheet = True

            # 1~9행 복사 (항상 실행하여 최신 헤더/전역함수 유지)
            try:
                logger.info(f"원본 시트 '{source_sheet.title}'에서 1~9행 전체 복사 중...")

                # Google Sheets API의 copyPaste를 사용하여 정확히 복사
                # 이 방법은 수식, 형식, 데이터를 모두 정확히 복사함
                requests = [{
                    'copyPaste': {
                        'source': {
                            'sheetId': source_sheet.id,
                            'startRowIndex': 0,  # 1행 (0-based)
                            'endRowIndex': 9,    # 9행까지
                            'startColumnIndex': 0,
                            'endColumnIndex': 60  # BH열 (60개 열)
                        },
                        'destination': {
                            'sheetId': target_worksheet.id,
                            'startRowIndex': 0,
                            'endRowIndex': 9,
                            'startColumnIndex': 0,
                            'endColumnIndex': 60
                        },
                        'pasteType': 'PASTE_NORMAL',  # 모든 것을 복사 (수식, 값, 형식)
                        'pasteOrientation': 'NORMAL'
                    }
                }]

                spreadsheet.batch_update({'requests': requests})
                logger.info(f"✅ 1~9행 전체 복사 완료 (copyPaste 사용)")

            except Exception as e:
                logger.warning(f"⚠️ copyPaste 실패, 대체 방법 시도: {str(e)}")
                # copyPaste 실패 시, FORMULA 모드로 복사
                try:
                    result = source_sheet.spreadsheet.values_get(
                        f"'{source_sheet.title}'!A1:BH9",
                        params={'valueRenderOption': 'FORMULA'}
                    )
                    rows_1_to_9 = result.get('values', [])

                    if rows_1_to_9:
                        target_worksheet.update(range_name='A1', values=rows_1_to_9, value_input_option='USER_ENTERED')
                        logger.info(f"✅ 1~9행 FORMULA 모드로 복사 완료: {len(rows_1_to_9)}행")
                except Exception as fallback_err:
                    logger.error(f"❌ 1~9행 복사 실패: {str(fallback_err)}")
            
            # A열의 마지막 데이터 행 찾기
            last_row_with_data = len([cell for cell in target_worksheet.col_values(1) if cell.strip()])
            
            # 새로운 데이터를 붙여넣을 시작 행
            paste_start_row = max(start_row, last_row_with_data + 1)
            
            logger.info(f"📝 '{target_sheet_name}' 시트의 {paste_start_row}행부터 {len(filtered_data)}개 행을 붙여넣습니다.")

            # 데이터 붙여넣기 (벌크 처리) - RAW 모드로 실제 값을 붙여넣기
            # filtered_data는 이미 UNFORMATTED_VALUE로 가져온 실제 값이므로 RAW로 붙여넣기
            range_name = f'A{paste_start_row}'
            target_worksheet.update(range_name, filtered_data, value_input_option='RAW')

            logger.info(f"✅ '{target_sheet_name}' 시트에 {len(filtered_data)}개 행 붙여넣기 완료 (시작 행: {paste_start_row})")

            # 원본 시트의 표시 형식을 대상 시트에 적용
            if source_sheet_name:
                try:
                    logger.info(f"📋 원본 시트 '{source_sheet_name}'의 표시 형식을 복사 중...")
                    source_sheet = spreadsheet.worksheet(source_sheet_name)
                    self._copy_number_formats(source_sheet, target_worksheet, paste_start_row, len(filtered_data))
                    logger.info(f"✅ 표시 형식 복사 완료")
                except Exception as e:
                    logger.warning(f"⚠️ 표시 형식 복사 중 오류 (데이터는 정상 복사됨): {str(e)}")

            return True
            
        except Exception as e:
            logger.error(f"데이터 붙여넣기 실패: {str(e)}")
            return False

    def copy_summary_sheet(self, sheet_url: str, sheet_name: str, row_count: int = 50, copy_all: bool = False) -> str:
        """선택된 시트의 요약 복제 생성 (특정 헤더만)"""
        try:
            workbook = self.client.open_by_url(sheet_url)
            source_sheet = workbook.worksheet(sheet_name)
            
            # 헤더 매칭을 위한 키워드 맵
            header_keywords = {
                '영상 ID': ['영상 ID', '영상ID', 'videoID', 'video_id', 'Video ID'],
                '영상 업로드 날짜': ['영상 업로드 날짜', '업로드 날짜', '업로드날짜', '날짜', 'upload_date'],
                '제목': ['제목', '영상제목', '영상 제목', 'title', '타이틀'],
                '채널명': ['채널명', '채널', 'channel', '채널이름'],
                '조회수': ['조회수', '조회', 'views', 'view_count'],
                '영상길이': ['영상길이', '길이', '영상 길이', 'duration', '재생시간'],
                '분야1': ['분야1', '카테고리1', '분야 1', 'category1'],
                '분야2': ['분야2', '카테고리2', '분야 2', 'category2'],
                '구독자수': ['구독자수', '구독자', 'subscribers', '구독자 수'],
                '조회수 대비 좋아요': ['조회수 대비 좋아요', '좋아요비율', '좋아요 비율', 'like_ratio'],
                '디스크립션': ['디스크립션', '설명', 'description', '영상설명'],
                '사용 해시태그': ['사용 해시태그', '해시태그', 'hashtag', '#태그'],
                '후킹자막': ['후킹자막', '후킹', '자막', 'hooking'],
                '대본내용': ['대본내용', '대본', 'transcript', '스크립트'],
                '대본유무': ['대본유무', '대본 유무', '대본여부', 'has_transcript'],
                '원본 행순서': ['원본 행순서', '행순서', '순서', 'row_order']
            }
            
            logger.info(f"📋 '{sheet_name}' 시트의 요약 복제 생성 중...")
            
            # 헤더 행 가져오기 (1행)
            header_row = source_sheet.row_values(1)
            if not header_row:
                raise Exception("헤더 행을 찾을 수 없습니다.")
            
            # 매칭되는 열 찾기
            matched_columns = {}
            for target_header, keywords in header_keywords.items():
                for i, cell_value in enumerate(header_row):
                    if cell_value and any(keyword in str(cell_value) for keyword in keywords):
                        matched_columns[target_header] = i + 1  # 1-based index
                        logger.info(f"📍 '{target_header}' -> {cell_value} (열 {i+1})")
                        break
            
            if not matched_columns:
                raise Exception("매칭되는 헤더를 찾을 수 없습니다.")
            
            # 복제 시트 이름 생성
            copy_name = self.generate_copy_name(workbook, sheet_name)
            
            # 새 시트 생성
            new_sheet = workbook.add_worksheet(title=copy_name, rows=1000, cols=len(matched_columns))
            
            # 헤더 행 복사 (원본 헤더명 그대로 사용)
            new_header = []
            column_indices = []
            for target_header in header_keywords.keys():
                if target_header in matched_columns:
                    col_idx = matched_columns[target_header] - 1  # 0-based index
                    original_header = header_row[col_idx] if col_idx < len(header_row) else target_header
                    new_header.append(original_header)
                    column_indices.append(matched_columns[target_header])
            
            new_sheet.update('A1', [new_header])
            
            # 원본 시트의 서식 복사
            try:
                logger.info("📋 원본 시트의 서식을 복사 중...")
                
                # 시트 서식 복사를 위한 요청 리스트
                format_requests = []
                
                # 헤더 행 서식 복사
                header_format_request = {
                    'repeatCell': {
                        'range': {
                            'sheetId': new_sheet.id,
                            'startRowIndex': 0,
                            'endRowIndex': 1,
                            'startColumnIndex': 0,
                            'endColumnIndex': len(column_indices)
                        },
                        'cell': {
                            'userEnteredFormat': {
                                'textFormat': {
                                    'bold': True,
                                    'fontSize': 10
                                },
                                'horizontalAlignment': 'CENTER',
                                'verticalAlignment': 'MIDDLE',
                                'wrapStrategy': 'CLIP',
                                'backgroundColor': {
                                    'red': 0.85,
                                    'green': 0.85,
                                    'blue': 0.85
                                }
                            }
                        },
                        'fields': 'userEnteredFormat'
                    }
                }
                format_requests.append(header_format_request)
                
                # 열 너비 설정
                for i, col_idx in enumerate(column_indices):
                    # 각 열별로 적절한 너비 설정
                    col_width = 120  # 기본값
                    
                    # 헤더명에 따른 열 너비 조정
                    header_name = new_header[i] if i < len(new_header) else ""
                    if any(keyword in header_name for keyword in ['제목', '디스크립션', '대본내용']):
                        col_width = 200
                    elif any(keyword in header_name for keyword in ['채널명', '해시태그']):
                        col_width = 150
                    elif any(keyword in header_name for keyword in ['ID', '날짜', '조회수']):
                        col_width = 100
                    
                    format_requests.append({
                        'updateDimensionProperties': {
                            'range': {
                                'sheetId': new_sheet.id,
                                'dimension': 'COLUMNS',
                                'startIndex': i,
                                'endIndex': i + 1
                            },
                            'properties': {
                                'pixelSize': col_width
                            },
                            'fields': 'pixelSize'
                        }
                    })
                
                # 헤더 행 높이 설정
                format_requests.append({
                    'updateDimensionProperties': {
                        'range': {
                            'sheetId': new_sheet.id,
                            'dimension': 'ROWS',
                            'startIndex': 0,
                            'endIndex': 1
                        },
                        'properties': {
                            'pixelSize': 35
                        },
                        'fields': 'pixelSize'
                    }
                })
                
                # 서식 적용
                workbook.batch_update({'requests': format_requests})
                logger.info("✅ 헤더 서식 설정 완료")
                        
            except Exception as e:
                logger.warning(f"서식 복사 실패: {e}")
            
            # 데이터 복사 범위 결정
            if copy_all:
                # A열 기준으로 마지막 데이터 행 찾기
                all_values = source_sheet.col_values(1)
                last_row = len([cell for cell in all_values if str(cell).strip()])
                end_row = last_row
                logger.info(f"📊 전체 복제 모드: 10행부터 {end_row}행까지 ({end_row-9}개 행)")
            else:
                end_row = min(10 + row_count - 1, 1000)
                logger.info(f"📊 선택 복제 모드: 10행부터 {end_row}행까지 ({row_count}개 행)")
            
            # 데이터 복사 (10행부터)
            if end_row >= 10:
                copy_data = []
                for row_num in range(10, end_row + 1):
                    source_row = source_sheet.row_values(row_num)
                    new_row = []
                    
                    for col_idx in column_indices:
                        if col_idx <= len(source_row):
                            new_row.append(source_row[col_idx - 1])
                        else:
                            new_row.append('')
                    
                    copy_data.append(new_row)
                
                if copy_data:
                    new_sheet.update('A2', copy_data)
                    logger.info(f"✅ {len(copy_data)}개 행 데이터 복사 완료")
                    
                    # 데이터 행 서식 설정
                    try:
                        logger.info("📋 데이터 행 서식 설정 중...")
                        
                        # 데이터 행 범위
                        data_end_row = 1 + len(copy_data)
                        data_end_col = len(column_indices) - 1
                        
                        # 데이터 행 서식 설정을 위한 요청
                        format_requests = []
                        
                        # 1. 행 높이 설정 (데이터 행만)
                        format_requests.append({
                            'updateDimensionProperties': {
                                'range': {
                                    'sheetId': new_sheet.id,
                                    'dimension': 'ROWS',
                                    'startIndex': 1,  # 2행부터 (0-based)
                                    'endIndex': data_end_row
                                },
                                'properties': {
                                    'pixelSize': 31
                                },
                                'fields': 'pixelSize'
                            }
                        })
                        
                        # 2. 데이터 행 텍스트 서식 설정 (볼드, 줄바꿈 자르기)
                        format_requests.append({
                            'repeatCell': {
                                'range': {
                                    'sheetId': new_sheet.id,
                                    'startRowIndex': 1,  # 2행부터
                                    'endRowIndex': data_end_row,
                                    'startColumnIndex': 0,
                                    'endColumnIndex': len(column_indices)
                                },
                                'cell': {
                                    'userEnteredFormat': {
                                        'textFormat': {
                                            'bold': True,
                                            'fontSize': 9
                                        },
                                        'wrapStrategy': 'CLIP',
                                        'verticalAlignment': 'MIDDLE',
                                        'horizontalAlignment': 'LEFT'
                                    }
                                },
                                'fields': 'userEnteredFormat(textFormat,wrapStrategy,verticalAlignment,horizontalAlignment)'
                            }
                        })
                        
                        # 서식 적용
                        if format_requests:
                            workbook.batch_update({'requests': format_requests})
                            logger.info("✅ 데이터 행 서식 설정 완료")
                            
                    except Exception as e:
                        logger.warning(f"데이터 행 서식 설정 실패: {e}")
            
            # 새 시트를 맨 뒤로 이동
            all_sheets = workbook.worksheets()
            workbook.reorder_worksheets([sheet for sheet in all_sheets if sheet.title != copy_name] + [new_sheet])
            
            logger.info(f"✅ 요약 복제 완료: '{copy_name}' ({len(matched_columns)}개 열, {end_row-9}개 행)")
            return copy_name
            
        except Exception as e:
            logger.error(f"시트 요약 복제 실패: {e}")
            raise
    
    def generate_copy_name(self, workbook, original_name: str) -> str:
        """복제 시트의 고유한 이름 생성"""
        existing_titles = [sheet.title for sheet in workbook.worksheets()]
        
        counter = 1
        while True:
            copy_name = f"{original_name}_추출{counter:02d}"
            if copy_name not in existing_titles:
                return copy_name
            counter += 1
            if counter > 99:  # 안전장치
                break
        
        # 99개를 초과하면 타임스탬프 추가
        timestamp = datetime.now().strftime("%m%d_%H%M")
        return f"{original_name}_추출_{timestamp}"

class TranscriptExtractorGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("YouTube Shorts 대본 추출기")
        # 화면 크기 가져오기
        screen_width = self.root.winfo_screenwidth()
        screen_height = self.root.winfo_screenheight()
        
        # 세로는 최대, 가로는 적절한 크기로 설정
        window_width = 1200
        window_height = screen_height - 80  # 태스크바 등을 고려한 여유 공간 (더 크게)
        
        self.root.geometry(f"{window_width}x{window_height}")
        self.root.state('normal')  # 전체화면이 아닌 최대화
        self.root.resizable(True, True)
        
        # 설정 - 동적 경로 사용
        SCRIPT_DIR = Path(__file__).parent
        # 공유 구글 자격증명 폴더 지원
        key_dir = SCRIPT_DIR / "google_service_key"
        if not (key_dir / "service-account-key.json").exists():
            parent_key_dir = SCRIPT_DIR / ".." / ".." / "google_service_key"
            if (parent_key_dir / "service-account-key.json").exists():
                key_dir = parent_key_dir
        self.CREDENTIALS_PATH = key_dir / "service-account-key.json"
        
        self.sheets_manager = GoogleSheetsManager(self.CREDENTIALS_PATH)
        self.is_running = False
        self.should_stop = False  # ESC 키로 중단 플래그
        self.current_sheet_url = self.sheets_manager.available_spreadsheets["쇼츠 스프레드시트"]
        
        # 조건부 영상 추출을 위한 변수들 초기화
        self.current_worksheet = None
        self.header_columns = {}

        # 디바운스를 위한 변수들
        self.debounce_timer = None
        self.debounce_delay = 2000  # 2초 (밀리초 단위)

        # 데이터 캐싱을 위한 변수들 (API 호출 최소화)
        self.cached_sheet_data = None
        self.cached_sheet_name = None

        # 첫 시트 로드 플래그 (자동 로드용)
        self._first_sheet_load = False

        self.setup_ui()
        self.setup_key_bindings()

    def auto_load_reference_sheet(self):
        """사용 레퍼런스 영상 시트 자동 로드"""
        try:
            # 스프레드시트 열기
            spreadsheet = self.sheets_manager.client.open_by_url(self.current_sheet_url)

            # "사용 레퍼런스 영상" 시트 찾기
            try:
                reference_sheet = spreadsheet.worksheet("사용 레퍼런스 영상")
                logger.info("📂 '사용 레퍼런스 영상' 시트 자동 로드 시작")

                # GUI가 완전히 로드된 후 실행되도록 after 사용
                self.root.after(100, lambda: self._load_reference_sheet_data(reference_sheet))

            except Exception as e:
                logger.info(f"'사용 레퍼런스 영상' 시트를 찾을 수 없습니다: {str(e)}")
                # 시트가 없으면 자동 로드하지 않음

        except Exception as e:
            logger.error(f"자동 로드 중 오류: {str(e)}")

    def _load_reference_sheet_data(self, worksheet):
        """사용 레퍼런스 영상 시트 데이터 로드"""
        try:
            self.current_worksheet = worksheet
            self.load_filter_data_manual()
        except Exception as e:
            logger.error(f"레퍼런스 시트 로드 실패: {str(e)}")

    def setup_ui(self):
        """UI 구성"""
        # 메인 캔버스와 스크롤바 생성
        self.main_canvas = tk.Canvas(self.root)
        self.scrollbar = ttk.Scrollbar(self.root, orient="vertical", command=self.main_canvas.yview)
        self.scrollable_frame = ttk.Frame(self.main_canvas)
        
        # 스크롤 영역 설정
        self.scrollable_frame.bind(
            "<Configure>",
            lambda e: self.main_canvas.configure(scrollregion=self.main_canvas.bbox("all"))
        )
        
        self.main_canvas.create_window((0, 0), window=self.scrollable_frame, anchor="nw")
        self.main_canvas.configure(yscrollcommand=self.scrollbar.set)
        
        # 캔버스와 스크롤바 배치
        self.main_canvas.pack(side="left", fill="both", expand=True, padx=(10, 0), pady=10)
        self.scrollbar.pack(side="right", fill="y", padx=(0, 10), pady=10)
        
        # 메인 프레임 (스크롤 가능한 영역 내부)
        main_frame = ttk.Frame(self.scrollable_frame, padding="10")
        main_frame.pack(fill="both", expand=True)
        
        # 마우스 휠 스크롤 바인딩
        def _on_mousewheel(event):
            self.main_canvas.yview_scroll(int(-1*(event.delta/120)), "units")
        
        def bind_to_mousewheel(widget):
            widget.bind("<MouseWheel>", _on_mousewheel)
            for child in widget.winfo_children():
                bind_to_mousewheel(child)
        
        # 모든 위젯에 마우스 휠 바인딩
        bind_to_mousewheel(self.scrollable_frame)
        self.main_canvas.bind("<MouseWheel>", _on_mousewheel)
        
        # 제목
        title_label = ttk.Label(main_frame, text="YouTube Shorts 대본 추출기", 
                               font=('Arial', 16, 'bold'))
        title_label.grid(row=0, column=0, columnspan=3, pady=(0, 20))
        
        # 공통 스프레드시트/시트 선택 영역
        common_frame = ttk.LabelFrame(main_frame, text="공통 설정", padding="10")
        common_frame.grid(row=1, column=0, columnspan=3, sticky=(tk.W, tk.E), pady=(0, 15))
        
        # 스프레드시트 선택
        ttk.Label(common_frame, text="스프레드시트:", font=('Arial', 10, 'bold')).grid(
            row=0, column=0, sticky=tk.W, pady=(0, 5))
        
        self.spreadsheet_var = tk.StringVar()
        self.spreadsheet_combo = ttk.Combobox(common_frame, textvariable=self.spreadsheet_var, 
                                             values=list(self.sheets_manager.available_spreadsheets.keys()),
                                             state="readonly", width=50)
        self.spreadsheet_combo.grid(row=1, column=0, columnspan=2, sticky=(tk.W, tk.E), pady=(0, 10))
        self.spreadsheet_combo.set("쇼츠 스프레드시트")
        self.spreadsheet_combo.bind('<<ComboboxSelected>>', self.on_spreadsheet_selected)
        
        # 시트 선택
        ttk.Label(common_frame, text="시트:", font=('Arial', 10, 'bold')).grid(
            row=2, column=0, sticky=tk.W, pady=(0, 5))
        
        self.sheet_var = tk.StringVar()
        self.sheet_combo = ttk.Combobox(common_frame, textvariable=self.sheet_var, 
                                       values=list(self.sheets_manager.available_sheets.keys()),
                                       state="readonly", width=50)
        self.sheet_combo.grid(row=3, column=0, columnspan=2, sticky=(tk.W, tk.E), pady=(0, 10))
        self.sheet_combo.set("쇼핑 레퍼런스 영상")
        self.sheet_combo.bind('<<ComboboxSelected>>', self.on_sheet_selected)
        
        # 시트 정보
        self.sheet_info_var = tk.StringVar(value="시트를 선택하고 '시트 정보 확인' 버튼을 클릭하세요.")
        self.sheet_info_label = ttk.Label(common_frame, textvariable=self.sheet_info_var, 
                                         font=('Arial', 9), foreground='blue')
        self.sheet_info_label.grid(row=4, column=0, sticky=(tk.W, tk.E), pady=(0, 5))
        
        self.info_button = ttk.Button(common_frame, text="시트 정보 확인", 
                                     command=self.check_sheet_info)
        self.info_button.grid(row=4, column=1, padx=(10, 0))
        
        common_frame.columnconfigure(0, weight=1)
        
        # 콘텐츠 영역 (좌우 분할)
        content_frame = ttk.Frame(main_frame)
        content_frame.grid(row=2, column=0, columnspan=3, sticky=(tk.W, tk.E, tk.N, tk.S))
        content_frame.columnconfigure(0, weight=1)
        content_frame.columnconfigure(1, weight=1)
        content_frame.rowconfigure(0, weight=1)
        
        # 좌측: 대본 추출
        self.setup_transcript_section(content_frame)
        
        # 우측: 조건 영상 추출
        self.setup_conditional_video_section(content_frame)
        
        # 하단: 진행 상황 및 로그 (전체 너비)
        self.setup_progress_section(main_frame)
        
        # 그리드 가중치 설정
        main_frame.columnconfigure(0, weight=1)
        main_frame.columnconfigure(1, weight=1)
        main_frame.columnconfigure(2, weight=1)
        main_frame.rowconfigure(2, weight=1)
        
        # 로그 핸들러 설정
        self.setup_logging()
        
        # 스프레드시트 초기화
        self.on_spreadsheet_selected()
        
    def setup_transcript_section(self, parent):
        """좌측 대본 추출 섹션 설정"""
        transcript_frame = ttk.LabelFrame(parent, text="대본 추출", padding="10")
        transcript_frame.grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S), padx=(0, 5))
        
        # 실행 모드 선택
        ttk.Label(transcript_frame, text="실행 모드:", font=('Arial', 10, 'bold')).grid(
            row=0, column=0, sticky=tk.W, pady=(0, 5))
        
        mode_frame = ttk.Frame(transcript_frame)
        mode_frame.grid(row=1, column=0, sticky=(tk.W, tk.E), pady=(0, 10))
        
        self.mode_var = tk.StringVar(value="B")
        ttk.Radiobutton(mode_frame, text="전체 추출 (10행부터 모든 영상)", 
                       variable=self.mode_var, value="A").grid(row=0, column=0, sticky=tk.W)
        ttk.Radiobutton(mode_frame, text="마지막 대본 데이터 이후 추출 시작", 
                       variable=self.mode_var, value="B").grid(row=1, column=0, sticky=tk.W)
        
        # 수집 설정
        collect_frame = ttk.Frame(mode_frame)
        collect_frame.grid(row=2, column=0, sticky=(tk.W, tk.E), pady=(10, 0))
        
        ttk.Label(collect_frame, text="대본 수집 갯수:").grid(row=0, column=0, sticky=tk.W)
        self.collect_count_var = tk.StringVar(value="20")
        collect_count_entry = ttk.Entry(collect_frame, textvariable=self.collect_count_var, width=10)
        collect_count_entry.grid(row=0, column=1, sticky=tk.W, padx=(5, 0))
        ttk.Label(collect_frame, text="개").grid(row=0, column=2, sticky=tk.W, padx=(3, 0))
        
        self.collect_all_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(collect_frame, text="마지막 데이터까지 수집", 
                       variable=self.collect_all_var).grid(row=1, column=0, columnspan=3, sticky=tk.W, pady=(5, 0))
        
        # 옵션 설정
        self.browser_automation_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(mode_frame, text="브라우저 자동화 사용 (권장)", 
                       variable=self.browser_automation_var).grid(row=3, column=0, sticky=tk.W, pady=(10, 0))
        
        self.include_timestamp_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(mode_frame, text="타임스탬프 포함 (쇼츠)", 
                       variable=self.include_timestamp_var).grid(row=4, column=0, sticky=tk.W, pady=(5, 0))
        
        self.extract_missing_transcripts_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(mode_frame, text="중간 누락 대본 추출 시도", 
                       variable=self.extract_missing_transcripts_var).grid(row=5, column=0, sticky=tk.W, pady=(5, 0))
        
        self.bulk_transcript_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(mode_frame, text="벌크 처리 (쇼츠: 30개씩, 일반: 10개씩)", 
                       variable=self.bulk_transcript_var).grid(row=6, column=0, sticky=tk.W, pady=(5, 0))
        
        # 버튼들
        button_frame = ttk.Frame(transcript_frame)
        button_frame.grid(row=2, column=0, pady=10, sticky=(tk.W, tk.E))
        button_frame.columnconfigure(0, weight=1)
        button_frame.columnconfigure(1, weight=1)
        
        self.test_button = ttk.Button(button_frame, text="연결 테스트", 
                                     command=self.test_connection)
        self.test_button.grid(row=0, column=0, padx=(0, 3), sticky=(tk.W, tk.E))
        
        self.backup_button = ttk.Button(button_frame, text="시트 백업", 
                                       command=self.backup_sheet)
        self.backup_button.grid(row=0, column=1, padx=(3, 0), sticky=(tk.W, tk.E))
        
        self.run_button = ttk.Button(transcript_frame, text="대본 추출 시작", 
                                    command=self.start_extraction, style='Accent.TButton')
        self.run_button.grid(row=3, column=0, pady=(5, 0), sticky=(tk.W, tk.E))
        
        # 구글 닥스 섹션
        docs_frame = ttk.LabelFrame(transcript_frame, text="구글 닥스 추출", padding="10")
        docs_frame.grid(row=4, column=0, sticky=(tk.W, tk.E), pady=(15, 0))
        
        # 닥스 추출 갯수 설정
        docs_count_frame = ttk.Frame(docs_frame)
        docs_count_frame.grid(row=0, column=0, sticky=(tk.W, tk.E), pady=(0, 10))
        
        ttk.Label(docs_count_frame, text="닥스 추출 갯수:").grid(row=0, column=0, sticky=tk.W)
        self.docs_count_var = tk.StringVar(value="10")
        docs_count_entry = ttk.Entry(docs_count_frame, textvariable=self.docs_count_var, width=10)
        docs_count_entry.grid(row=0, column=1, sticky=tk.W, padx=(5, 0))
        ttk.Label(docs_count_frame, text="개").grid(row=0, column=2, sticky=tk.W, padx=(3, 0))
        
        # 마지막 데이터까지 수집 체크박스
        self.extract_to_end_var = tk.BooleanVar(value=False)
        self.extract_to_end_check = ttk.Checkbutton(
            docs_count_frame, 
            text="마지막 데이터까지 수집", 
            variable=self.extract_to_end_var
        )
        self.extract_to_end_check.grid(row=1, column=0, columnspan=3, sticky=tk.W, pady=(5, 0))
        
        # 추출 모드 선택 옵션
        mode_frame = ttk.LabelFrame(docs_frame, text="추출 모드", padding="5")
        mode_frame.grid(row=1, column=0, sticky=(tk.W, tk.E), pady=(0, 10))
        
        self.extraction_mode_var = tk.StringVar(value="both")  # 기본값: 둘 다 저장
        
        # 둘 다 저장 라디오버튼
        self.both_radio = ttk.Radiobutton(
            mode_frame,
            text="Docs + TXT 동시 저장 (기본)",
            variable=self.extraction_mode_var,
            value="both",
            command=self.on_mode_change
        )
        self.both_radio.grid(row=0, column=0, sticky=tk.W, pady=(0, 5))
        
        # 누락 대본 추출 라디오버튼
        self.missing_radio = ttk.Radiobutton(
            mode_frame,
            text="누락 대본 추출 (Docs/TXT 중 비어있는 것만)",
            variable=self.extraction_mode_var,
            value="missing",
            command=self.on_mode_change
        )
        self.missing_radio.grid(row=1, column=0, sticky=tk.W)
        
        # 썸네일 추출 라디오버튼
        self.thumbnail_radio = ttk.Radiobutton(
            mode_frame,
            text="썸네일 추출 (기존 Docs에 썸네일 이미지 추가)",
            variable=self.extraction_mode_var,
            value="thumbnail",
            command=self.on_mode_change
        )
        self.thumbnail_radio.grid(row=2, column=0, sticky=tk.W)
        
        # 쇼츠용 썸네일만 추출 라디오버튼
        self.shorts_thumbnail_radio = ttk.Radiobutton(
            mode_frame,
            text="쇼츠용 썸네일만 추출 (대본여부 헤더열 기준)",
            variable=self.extraction_mode_var,
            value="shorts_thumbnail",
            command=self.on_mode_change
        )
        self.shorts_thumbnail_radio.grid(row=3, column=0, sticky=tk.W)
        
        # 일반 벌크 처리 체크박스 (모든 모드 공통)
        self.bulk_docs_var = tk.BooleanVar(value=False)
        self.bulk_docs_check = ttk.Checkbutton(
            mode_frame,
            text="벌크 처리 (50개 이상일 때 50개씩 순차 처리)",
            variable=self.bulk_docs_var
        )
        self.bulk_docs_check.grid(row=4, column=0, sticky=tk.W, pady=(5, 0))
        
        # 썸네일 전용 벌크 처리 체크박스
        self.bulk_thumbnail_var = tk.BooleanVar(value=False)
        self.bulk_thumbnail_check = ttk.Checkbutton(
            mode_frame,
            text="썸네일 50개 단위 순차 벌크 처리 (타임아웃 방지 + API 최적화)",
            variable=self.bulk_thumbnail_var
        )
        self.bulk_thumbnail_check.grid(row=5, column=0, sticky=tk.W, padx=(20, 0))
        
        # 저장 위치 옵션
        location_frame = ttk.LabelFrame(docs_frame, text="저장 위치", padding="5")
        location_frame.grid(row=2, column=0, sticky=(tk.W, tk.E), pady=(0, 10))
        
        self.save_location_var = tk.StringVar(value="drive")  # 기본값: Google Drive
        
        # Google Drive 라디오버튼
        self.drive_radio = ttk.Radiobutton(
            location_frame,
            text="Google Drive",
            variable=self.save_location_var,
            value="drive",
            command=self.on_location_change
        )
        self.drive_radio.grid(row=0, column=0, sticky=tk.W, padx=(0, 20))
        
        # 로컬 저장 라디오버튼
        self.local_radio = ttk.Radiobutton(
            location_frame,
            text="로컬 저장",
            variable=self.save_location_var,
            value="local",
            command=self.on_location_change
        )
        self.local_radio.grid(row=0, column=1, sticky=tk.W)
        
        # 경로 선택 프레임 (초기에는 숨김)
        self.path_frame = ttk.Frame(docs_frame)
        self.path_frame.grid(row=3, column=0, sticky=(tk.W, tk.E), pady=(0, 10))
        self.path_frame.grid_remove()  # 초기에는 숨김
        
        ttk.Label(self.path_frame, text="저장 경로:").grid(row=0, column=0, sticky=tk.W)
        
        # 기본 경로 설정
        script_dir = Path(__file__).parent
        default_path = script_dir / "output_docs"
        self.path_var = tk.StringVar(value=str(default_path))
        self.path_entry = ttk.Entry(self.path_frame, textvariable=self.path_var, width=40)
        self.path_entry.grid(row=0, column=1, sticky=(tk.W, tk.E), padx=(5, 5))
        
        ttk.Button(self.path_frame, text="찾아보기", 
                  command=self.browse_folder).grid(row=0, column=2, padx=(0, 0))
        
        self.path_frame.columnconfigure(1, weight=1)
        
        # 구글 닥스 추출 버튼
        self.docs_button = ttk.Button(docs_frame, text="구글닥스 추출", 
                                     command=self.start_docs_extraction)
        self.docs_button.grid(row=4, column=0, pady=(0, 0))
        
        # 선택 시트 요약 복제 섹션
        summary_frame = ttk.LabelFrame(transcript_frame, text="선택 시트 요약 복제", padding="10")
        summary_frame.grid(row=5, column=0, sticky=(tk.W, tk.E), pady=(15, 0))
        
        # 복제할 행 수 설정
        summary_count_frame = ttk.Frame(summary_frame)
        summary_count_frame.grid(row=0, column=0, sticky=(tk.W, tk.E), pady=(0, 10))
        
        ttk.Label(summary_count_frame, text="복제할 영상 수:").grid(row=0, column=0, sticky=tk.W)
        self.summary_count_var = tk.StringVar(value="50")
        summary_count_entry = ttk.Entry(summary_count_frame, textvariable=self.summary_count_var, width=10)
        summary_count_entry.grid(row=0, column=1, sticky=tk.W, padx=(5, 0))
        ttk.Label(summary_count_frame, text="개 (10행부터)").grid(row=0, column=2, sticky=tk.W, padx=(3, 0))
        
        # 전체 영상 복제 체크박스
        self.copy_all_videos_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(summary_frame, text="전체 영상(행) 복제 (A열 기준 마지막 데이터까지)", 
                       variable=self.copy_all_videos_var).grid(row=1, column=0, sticky=tk.W, pady=(0, 10))
        
        # 시트 복제 버튼
        self.summary_button = ttk.Button(summary_frame, text="시트 요약 복제",
                                       command=self.start_summary_copy)
        self.summary_button.grid(row=2, column=0, pady=(0, 0))

        # ========================================
        # 매칭 업데이트 섹션
        # ========================================
        matching_update_frame = ttk.LabelFrame(transcript_frame, text="매칭 업데이트", padding="10")
        matching_update_frame.grid(row=6, column=0, sticky=(tk.W, tk.E), pady=(15, 0))

        # 1. 채널 리스트 - 영상 업데이트 서브섹션
        channel_video_update_frame = ttk.LabelFrame(matching_update_frame, text="1. 채널 리스트 - 영상 업데이트", padding="5")
        channel_video_update_frame.grid(row=0, column=0, sticky=(tk.W, tk.E), pady=(0, 10))

        # 시트 선택
        sheet_select_frame = ttk.Frame(channel_video_update_frame)
        sheet_select_frame.grid(row=0, column=0, sticky=(tk.W, tk.E), pady=(0, 5))

        ttk.Label(sheet_select_frame, text="영상 시트:").grid(row=0, column=0, sticky=tk.W, padx=(0, 5))
        self.video_sheet_var = tk.StringVar()
        self.video_sheet_combo = ttk.Combobox(sheet_select_frame, textvariable=self.video_sheet_var,
                                               width=25, state='readonly')
        self.video_sheet_combo.grid(row=0, column=1, sticky=(tk.W, tk.E), padx=(0, 10))

        # 기간 필터 옵션
        filter_frame = ttk.Frame(channel_video_update_frame)
        filter_frame.grid(row=1, column=0, sticky=(tk.W, tk.E), pady=(5, 5))

        self.use_days_filter_var = tk.BooleanVar(value=False)
        self.days_filter_check = ttk.Checkbutton(
            filter_frame,
            text="기간 필터 사용 (N일 이상 차이나는 채널만 업데이트):",
            variable=self.use_days_filter_var,
            command=self.on_days_filter_change
        )
        self.days_filter_check.grid(row=0, column=0, sticky=tk.W, padx=(0, 5))

        self.days_threshold_var = tk.StringVar(value="30")
        self.days_threshold_entry = ttk.Entry(filter_frame, textvariable=self.days_threshold_var, width=8, state='disabled')
        self.days_threshold_entry.grid(row=0, column=1, sticky=tk.W, padx=(0, 3))
        ttk.Label(filter_frame, text="일").grid(row=0, column=2, sticky=tk.W)

        # 조건에 해당하는 채널 수 표시 레이블
        self.matching_channels_label = ttk.Label(filter_frame, text="", font=('Arial', 9), foreground='blue')
        self.matching_channels_label.grid(row=0, column=3, sticky=tk.W, padx=(10, 0))

        # 디바운스 타이머 변수
        self.channel_count_debounce_timer = None

        # 입력 변경 감지
        self.days_threshold_var.trace_add('write', self.on_channel_filter_change)
        self.use_days_filter_var.trace_add('write', self.on_channel_filter_change)

        # 진행률 표시
        progress_frame = ttk.Frame(channel_video_update_frame)
        progress_frame.grid(row=2, column=0, sticky=(tk.W, tk.E), pady=(5, 5))

        self.channel_update_progress = ttk.Progressbar(progress_frame, mode='determinate', length=300)
        self.channel_update_progress.grid(row=0, column=0, sticky=(tk.W, tk.E), padx=(0, 5))

        self.channel_update_status_label = ttk.Label(progress_frame, text="", font=('Arial', 8))
        self.channel_update_status_label.grid(row=0, column=1, sticky=tk.W)

        progress_frame.columnconfigure(0, weight=1)

        # 업데이트 버튼 프레임
        button_frame = ttk.Frame(channel_video_update_frame)
        button_frame.grid(row=3, column=0, pady=(5, 0))

        self.channel_video_update_button = ttk.Button(button_frame,
                                                      text="채널 리스트 업데이트",
                                                      command=self.update_channel_list_from_videos,
                                                      style='Accent.TButton')
        self.channel_video_update_button.grid(row=0, column=0, padx=(0, 5))

        # 벤치마킹 채널여부 업데이트 버튼
        self.update_benchmarking_status_button = ttk.Button(button_frame,
                                                           text="벤치마킹 채널여부 업데이트",
                                                           command=self.update_benchmarking_status)
        self.update_benchmarking_status_button.grid(row=0, column=1, padx=(0, 5))

        # 최근 30개 영상 평균 조회수 업데이트 버튼
        self.update_recent_30_views_button = ttk.Button(button_frame,
                                                        text="최근 30개 영상 평균 조회수 업데이트",
                                                        command=self.update_recent_30_avg_views)
        self.update_recent_30_views_button.grid(row=0, column=2, padx=(0, 5))

        # 선택 시트 분야1/분야2 업데이트 버튼
        self.update_video_sheet_category_button = ttk.Button(button_frame,
                                                             text="선택시트 분야1,분야2 업데이트",
                                                             command=self.update_video_sheet_categories)
        self.update_video_sheet_category_button.grid(row=0, column=3, padx=(0, 5))

        # 삭제된 채널 영상삭제 버튼
        self.delete_deleted_channels_videos_button = ttk.Button(button_frame,
                                                                text="삭제된 채널 영상삭제",
                                                                command=self.delete_deleted_channels_videos,
                                                                style='Danger.TButton')
        self.delete_deleted_channels_videos_button.grid(row=0, column=4)

        # 설명 레이블
        desc_label = ttk.Label(channel_video_update_frame,
                              text="[채널 리스트 업데이트] 채널 리스트 ↔ 영상 시트 양방향 동기화\n"
                                   "• 수집날짜가 최신인 쪽의 데이터로 상대방 업데이트\n"
                                   "• 업데이트 시 양쪽 시트의 수집날짜도 동일하게 갱신\n"
                                   "• 9행 수집날짜 열: 양쪽 시트 모두 최신 날짜로 자동 갱신 (헤더/함수는 유지)\n\n"
                                   "[벤치마킹 채널여부 업데이트] 채널 리스트 → 영상 시트 단방향 업데이트\n"
                                   "• 채널 리스트의 '벤치마킹 채널여부' 값을 영상 시트의 모든 해당 채널 영상에 동기화\n"
                                   "• 채널명이 일치하는 영상들만 업데이트 (값이 다른 경우만)\n\n"
                                   "[최근 30개 영상 평균 조회수 업데이트] 채널 리스트의 '최근 30개 영상 평균 조회수' 열만 업데이트\n"
                                   "• 영상 시트의 '영상 업로드날짜' 기준 최근 30개 영상의 조회수 평균 계산\n"
                                   "• 30개 미만인 경우 전체 영상 평균으로 계산\n\n"
                                   "[선택시트 분야1,분야2 업데이트] 채널 리스트 → 영상 시트 단방향 업데이트\n"
                                   "• 채널명이 일치하는 영상들의 분야1, 분야2를 채널 리스트 기준으로 업데이트\n"
                                   "• 값이 다른 경우만 업데이트 (대규모 데이터 최적화 적용)\n\n"
                                   "[삭제된 채널 영상삭제] 채널 리스트의 '가져올 채널'='x'인 채널의 모든 영상 행 삭제\n"
                                   "• 선택된 영상 시트에서 삭제된 채널의 영상들을 행 자체를 삭제 (복구 불가)",
                              font=('Arial', 8), foreground='gray')
        desc_label.grid(row=4, column=0, sticky=tk.W, pady=(5, 0))

        channel_video_update_frame.columnconfigure(0, weight=1)

        # 2. 대본 매칭 업데이트 서브섹션
        transcript_copy_frame = ttk.LabelFrame(matching_update_frame, text="2. 대본 매칭 업데이트", padding="5")
        transcript_copy_frame.grid(row=1, column=0, sticky=(tk.W, tk.E), pady=(10, 0))

        # 시트 선택 프레임
        sheets_select_frame = ttk.Frame(transcript_copy_frame)
        sheets_select_frame.grid(row=0, column=0, sticky=(tk.W, tk.E), pady=(0, 5))

        # 원본 시트 선택
        ttk.Label(sheets_select_frame, text="원본 시트:").grid(row=0, column=0, sticky=tk.W, padx=(0, 5))
        self.source_sheet_var = tk.StringVar()
        self.source_sheet_combo = ttk.Combobox(sheets_select_frame, textvariable=self.source_sheet_var,
                                               width=20, state='readonly')
        self.source_sheet_combo.grid(row=0, column=1, sticky=(tk.W, tk.E), padx=(0, 10))

        # 타겟 시트 선택
        ttk.Label(sheets_select_frame, text="타겟 시트:").grid(row=0, column=2, sticky=tk.W, padx=(0, 5))
        self.target_sheet_var = tk.StringVar()
        self.target_sheet_combo = ttk.Combobox(sheets_select_frame, textvariable=self.target_sheet_var,
                                               width=20, state='readonly')
        self.target_sheet_combo.grid(row=0, column=3, sticky=(tk.W, tk.E))

        sheets_select_frame.columnconfigure(1, weight=1)
        sheets_select_frame.columnconfigure(3, weight=1)

        # 진행률 표시
        progress_frame2 = ttk.Frame(transcript_copy_frame)
        progress_frame2.grid(row=1, column=0, sticky=(tk.W, tk.E), pady=(5, 5))

        self.transcript_copy_progress = ttk.Progressbar(progress_frame2, mode='determinate', length=300)
        self.transcript_copy_progress.grid(row=0, column=0, sticky=(tk.W, tk.E), padx=(0, 5))

        self.transcript_copy_status_label = ttk.Label(progress_frame2, text="", font=('Arial', 8))
        self.transcript_copy_status_label.grid(row=0, column=1, sticky=tk.W)

        progress_frame2.columnconfigure(0, weight=1)

        # 업데이트 버튼
        self.transcript_copy_button = ttk.Button(transcript_copy_frame,
                                                 text="타겟시트로 대본,썸네일경로 복사",
                                                 command=self.copy_transcript_to_target)
        self.transcript_copy_button.grid(row=2, column=0, pady=(5, 0))

        # 설명 레이블
        desc_label2 = ttk.Label(transcript_copy_frame,
                              text="영상 ID 기준으로 대본 관련 데이터 복사\n"
                                   "• 대본내용, 분석, 대본파일, 썸네일 여부, 썸네일 이미지주소, 썸네일 경로\n"
                                   "• 원본 시트에 값이 있고, 타겟 시트가 비어있는 경우만 복사",
                              font=('Arial', 8), foreground='gray')
        desc_label2.grid(row=3, column=0, sticky=tk.W, pady=(5, 0))

        # 3. 영상 추가 및 업데이트 서브섹션
        video_update_frame = ttk.LabelFrame(matching_update_frame, text="3. 영상 추가 및 업데이트", padding="5")
        video_update_frame.grid(row=2, column=0, sticky=(tk.W, tk.E), pady=(10, 0))

        # 시트 선택 프레임
        sheets_select_frame3 = ttk.Frame(video_update_frame)
        sheets_select_frame3.grid(row=0, column=0, sticky=(tk.W, tk.E), pady=(0, 5))

        # 원본 시트 선택
        ttk.Label(sheets_select_frame3, text="원본 시트:").grid(row=0, column=0, sticky=tk.W, padx=(0, 5))
        self.video_add_source_sheet_var = tk.StringVar()
        self.video_add_source_sheet_combo = ttk.Combobox(sheets_select_frame3, textvariable=self.video_add_source_sheet_var,
                                                         width=20, state='readonly')
        self.video_add_source_sheet_combo.grid(row=0, column=1, sticky=(tk.W, tk.E), padx=(0, 10))

        # 타겟 시트 선택
        ttk.Label(sheets_select_frame3, text="타겟 시트:").grid(row=0, column=2, sticky=tk.W, padx=(0, 5))
        self.video_add_target_sheet_var = tk.StringVar()
        self.video_add_target_sheet_combo = ttk.Combobox(sheets_select_frame3, textvariable=self.video_add_target_sheet_var,
                                                         width=20, state='readonly')
        self.video_add_target_sheet_combo.grid(row=0, column=3, sticky=(tk.W, tk.E))

        sheets_select_frame3.columnconfigure(1, weight=1)
        sheets_select_frame3.columnconfigure(3, weight=1)

        # 진행률 표시
        progress_frame3 = ttk.Frame(video_update_frame)
        progress_frame3.grid(row=1, column=0, sticky=(tk.W, tk.E), pady=(5, 5))

        self.video_add_progress = ttk.Progressbar(progress_frame3, mode='determinate', length=300)
        self.video_add_progress.grid(row=0, column=0, sticky=(tk.W, tk.E), padx=(0, 5))

        self.video_add_status_label = ttk.Label(progress_frame3, text="", font=('Arial', 8))
        self.video_add_status_label.grid(row=0, column=1, sticky=tk.W)

        progress_frame3.columnconfigure(0, weight=1)

        # 버튼들
        button_frame3 = ttk.Frame(video_update_frame)
        button_frame3.grid(row=2, column=0, pady=(5, 0))

        self.add_new_videos_button = ttk.Button(button_frame3,
                                                text="타겟시트에 신규영상 추가",
                                                command=self.add_new_videos_to_target)
        self.add_new_videos_button.grid(row=0, column=0, padx=(0, 5))

        self.update_videos_by_date_button = ttk.Button(button_frame3,
                                                       text="타겟시트 기존영상 수집날짜 최신기준 업데이트",
                                                       command=self.update_videos_by_collect_date)
        self.update_videos_by_date_button.grid(row=0, column=1, padx=(5, 0))

        # 설명 레이블
        desc_label3 = ttk.Label(video_update_frame,
                              text="영상 ID 기준으로 영상 데이터 관리\n"
                                   "• 신규영상 추가: 타겟 시트에 없는 영상 ID를 원본에서 복사\n"
                                   "• 기존영상 업데이트: 수집날짜가 더 최신인 경우 업데이트 (대본/썸네일 데이터는 조건부 보존)",
                              font=('Arial', 8), foreground='gray')
        desc_label3.grid(row=3, column=0, sticky=tk.W, pady=(5, 0))

        matching_update_frame.columnconfigure(0, weight=1)

        # 초기 상태 설정
        self.on_mode_change()  # 초기 썸네일 옵션 표시/숨김 설정

        transcript_frame.columnconfigure(0, weight=1)

    def setup_conditional_video_section(self, parent):
        """우측 조건 영상 추출 섹션 설정"""
        video_frame = ttk.LabelFrame(parent, text="조건 영상 추출", padding="10")
        video_frame.grid(row=0, column=1, sticky=(tk.W, tk.E, tk.N, tk.S), padx=(5, 0))
        
        # 현재 선택된 조건 표시 섹션 (읽기 전용)
        current_filter_frame = ttk.LabelFrame(video_frame, text="조건(필터) - 현재 선택된 항목", padding="5")
        current_filter_frame.grid(row=0, column=0, sticky=(tk.W, tk.E), pady=(0, 10))

        # 시트 이름 현재 선택
        ttk.Label(current_filter_frame, text="시트 이름:").grid(row=0, column=0, sticky=tk.W, pady=2)
        self.filter_sheet_name_var = tk.StringVar(value="시트를 불러오지 않음")
        self.filter_sheet_name_label = ttk.Label(current_filter_frame, textvariable=self.filter_sheet_name_var,
                                                 foreground='blue', font=('Arial', 9, 'bold'))
        self.filter_sheet_name_label.grid(row=0, column=1, sticky=(tk.W, tk.E), padx=(5, 0))

        # 데이터 행 개수 현재 선택
        ttk.Label(current_filter_frame, text="데이터 행 개수:").grid(row=1, column=0, sticky=tk.W, pady=2)
        self.filter_data_count_var = tk.StringVar(value="-")
        self.filter_data_count_label = ttk.Label(current_filter_frame, textvariable=self.filter_data_count_var,
                                                 foreground='green', font=('Arial', 9, 'bold'))
        self.filter_data_count_label.grid(row=1, column=1, sticky=(tk.W, tk.E), padx=(5, 0))

        # 분야1 현재 선택
        ttk.Label(current_filter_frame, text="분야1:").grid(row=2, column=0, sticky=tk.W, pady=2)
        self.field1_current_var = tk.StringVar(value="선택 안함")
        self.field1_current_label = ttk.Label(current_filter_frame, textvariable=self.field1_current_var,
                                             foreground='blue', font=('Arial', 9))
        self.field1_current_label.grid(row=2, column=1, sticky=(tk.W, tk.E), padx=(5, 0))

        # 분야2 현재 선택
        ttk.Label(current_filter_frame, text="분야2:").grid(row=3, column=0, sticky=tk.W, pady=2)
        self.field2_current_var = tk.StringVar(value="선택 안함")
        self.field2_current_label = ttk.Label(current_filter_frame, textvariable=self.field2_current_var,
                                             foreground='blue', font=('Arial', 9))
        self.field2_current_label.grid(row=3, column=1, sticky=(tk.W, tk.E), padx=(5, 0))

        # 숏폼여부 현재 선택
        ttk.Label(current_filter_frame, text="숏폼여부:").grid(row=4, column=0, sticky=tk.W, pady=2)
        self.shortform_current_var = tk.StringVar(value="선택 안함")
        self.shortform_current_label = ttk.Label(current_filter_frame, textvariable=self.shortform_current_var,
                                                foreground='blue', font=('Arial', 9))
        self.shortform_current_label.grid(row=4, column=1, sticky=(tk.W, tk.E), padx=(5, 0))

        current_filter_frame.columnconfigure(1, weight=1)
        
        # 필터 선택 섹션 (체크박스)
        select_filter_frame = ttk.LabelFrame(video_frame, text="필터 선택", padding="5")
        select_filter_frame.grid(row=1, column=0, sticky=(tk.W, tk.E), pady=(0, 10))
        
        # 시트 데이터 불러오기 버튼
        load_data_button = ttk.Button(select_filter_frame, text="시트 데이터 불러오기", 
                                     command=self.load_filter_data_manual)
        load_data_button.grid(row=0, column=0, pady=(0, 10), sticky=tk.W)
        
        # 분야1 체크박스
        field1_label = ttk.Label(select_filter_frame, text="분야1 선택:", font=('Arial', 9, 'bold'))
        field1_label.grid(row=1, column=0, sticky=tk.W, pady=(0, 3))
        
        # 분야1 체크박스를 위한 스크롤 가능한 프레임
        self.field1_container = ttk.Frame(select_filter_frame)
        self.field1_container.grid(row=2, column=0, sticky=(tk.W, tk.E), padx=(10, 0), pady=(0, 5))
        
        self.field1_canvas = tk.Canvas(self.field1_container, height=120, bg='white', relief='sunken', bd=1)
        self.field1_scrollbar = ttk.Scrollbar(self.field1_container, orient="vertical", command=self.field1_canvas.yview)
        self.field1_frame = ttk.Frame(self.field1_canvas)
        
        self.field1_frame.bind("<Configure>", lambda e: self.field1_canvas.configure(scrollregion=self.field1_canvas.bbox("all")))
        self.field1_canvas.create_window((0, 0), window=self.field1_frame, anchor="nw")
        self.field1_canvas.configure(yscrollcommand=self.field1_scrollbar.set)
        
        self.field1_canvas.pack(side="left", fill="both", expand=True)
        self.field1_scrollbar.pack(side="right", fill="y")
        
        # 분야2 체크박스 및 전체보기 옵션
        field2_header_frame = ttk.Frame(select_filter_frame)
        field2_header_frame.grid(row=3, column=0, sticky=(tk.W, tk.E), pady=(5, 3))
        
        field2_label = ttk.Label(field2_header_frame, text="분야2 선택:", font=('Arial', 9, 'bold'))
        field2_label.pack(side='left')
        
        # 전체보기 체크박스
        self.field2_show_all_var = tk.BooleanVar(value=False)
        field2_show_all_check = ttk.Checkbutton(field2_header_frame, text="전체보기", 
                                               variable=self.field2_show_all_var,
                                               command=self.on_field2_show_all_changed)
        field2_show_all_check.pack(side='right', padx=(5, 0))
        
        # 분야2 체크박스를 위한 스크롤 가능한 프레임
        self.field2_container = ttk.Frame(select_filter_frame)
        self.field2_container.grid(row=4, column=0, sticky=(tk.W, tk.E), padx=(10, 0), pady=(0, 5))
        
        self.field2_canvas = tk.Canvas(self.field2_container, height=120, bg='white', relief='sunken', bd=1)
        self.field2_scrollbar = ttk.Scrollbar(self.field2_container, orient="vertical", command=self.field2_canvas.yview)
        self.field2_frame = ttk.Frame(self.field2_canvas)
        
        self.field2_frame.bind("<Configure>", lambda e: self.field2_canvas.configure(scrollregion=self.field2_canvas.bbox("all")))
        self.field2_canvas.create_window((0, 0), window=self.field2_frame, anchor="nw")
        self.field2_canvas.configure(yscrollcommand=self.field2_scrollbar.set)
        
        self.field2_canvas.pack(side="left", fill="both", expand=True)
        self.field2_scrollbar.pack(side="right", fill="y")
        
        # 숏폼여부 체크박스
        shortform_label = ttk.Label(select_filter_frame, text="숏폼여부 선택:", font=('Arial', 9, 'bold'))
        shortform_label.grid(row=5, column=0, sticky=tk.W, pady=(5, 3))
        
        # 숏폼여부 체크박스를 위한 스크롤 가능한 프레임
        self.shortform_container = ttk.Frame(select_filter_frame)
        self.shortform_container.grid(row=6, column=0, sticky=(tk.W, tk.E), padx=(10, 0), pady=(0, 5))
        
        self.shortform_canvas = tk.Canvas(self.shortform_container, height=80, bg='white', relief='sunken', bd=1)
        self.shortform_scrollbar = ttk.Scrollbar(self.shortform_container, orient="vertical", command=self.shortform_canvas.yview)
        self.shortform_frame = ttk.Frame(self.shortform_canvas)
        
        self.shortform_frame.bind("<Configure>", lambda e: self.shortform_canvas.configure(scrollregion=self.shortform_canvas.bbox("all")))
        self.shortform_canvas.create_window((0, 0), window=self.shortform_frame, anchor="nw")
        self.shortform_canvas.configure(yscrollcommand=self.shortform_scrollbar.set)
        
        self.shortform_canvas.pack(side="left", fill="both", expand=True)
        self.shortform_scrollbar.pack(side="right", fill="y")
        
        select_filter_frame.columnconfigure(0, weight=1)
        
        # 초기 상태 표시
        ttk.Label(self.field1_frame, text="시트 데이터를 불러오려면 위의 버튼을 클릭하세요.", 
                 foreground='gray').pack(anchor='w', padx=5, pady=5)
        ttk.Label(self.field2_frame, text="시트 데이터를 불러오려면 위의 버튼을 클릭하세요.", 
                 foreground='gray').pack(anchor='w', padx=5, pady=5)
        ttk.Label(self.shortform_frame, text="시트 데이터를 불러오려면 위의 버튼을 클릭하세요.", 
                 foreground='gray').pack(anchor='w', padx=5, pady=5)
        
        # 수치 조건 섹션
        numeric_frame = ttk.LabelFrame(video_frame, text="수치 조건", padding="5")
        numeric_frame.grid(row=2, column=0, sticky=(tk.W, tk.E), pady=(0, 10))
        
        # 조회수
        ttk.Label(numeric_frame, text="조회수:").grid(row=0, column=0, sticky=tk.W, pady=2)
        self.views_var = tk.StringVar()
        self.views_var.trace_add('write', lambda *args: self.on_numeric_condition_change())
        ttk.Entry(numeric_frame, textvariable=self.views_var, width=15).grid(row=0, column=1, sticky=tk.W, padx=(5, 0))
        ttk.Label(numeric_frame, text="이상").grid(row=0, column=2, padx=(3, 0))

        # 구독자수
        ttk.Label(numeric_frame, text="구독자수:").grid(row=1, column=0, sticky=tk.W, pady=2)
        self.subscribers_var = tk.StringVar()
        self.subscribers_var.trace_add('write', lambda *args: self.on_numeric_condition_change())
        ttk.Entry(numeric_frame, textvariable=self.subscribers_var, width=15).grid(row=1, column=1, sticky=tk.W, padx=(5, 0))
        ttk.Label(numeric_frame, text="이상").grid(row=1, column=2, padx=(3, 0))

        # 채널명
        ttk.Label(numeric_frame, text="채널명:").grid(row=2, column=0, sticky=tk.W, pady=2)
        self.channel_names_var = tk.StringVar()
        self.channel_names_var.trace_add('write', lambda *args: self.on_numeric_condition_change())
        ttk.Entry(numeric_frame, textvariable=self.channel_names_var, width=15).grid(row=2, column=1, sticky=tk.W, padx=(5, 0))
        ttk.Label(numeric_frame, text="(콤마구분)").grid(row=2, column=2, padx=(3, 0))

        # 좋아요 비율
        ttk.Label(numeric_frame, text="좋아요 비율:").grid(row=3, column=0, sticky=tk.W, pady=2)
        self.like_ratio_var = tk.StringVar()
        self.like_ratio_var.trace_add('write', lambda *args: self.on_numeric_condition_change())
        ttk.Entry(numeric_frame, textvariable=self.like_ratio_var, width=15).grid(row=3, column=1, sticky=tk.W, padx=(5, 0))
        ttk.Label(numeric_frame, text="%이상").grid(row=3, column=2, padx=(3, 0))

        # 영상길이
        ttk.Label(numeric_frame, text="영상길이:").grid(row=4, column=0, sticky=tk.W, pady=2)
        self.video_duration_var = tk.StringVar()
        self.video_duration_var.trace_add('write', lambda *args: self.on_numeric_condition_change())
        ttk.Entry(numeric_frame, textvariable=self.video_duration_var, width=15).grid(row=4, column=1, sticky=tk.W, padx=(5, 0))
        ttk.Label(numeric_frame, text="초 이상").grid(row=4, column=2, padx=(3, 0))

        # 수집날짜 경과일
        ttk.Label(numeric_frame, text="수집날짜 경과일:").grid(row=5, column=0, sticky=tk.W, pady=2)
        self.upload_days_var = tk.StringVar()
        self.upload_days_var.trace_add('write', lambda *args: self.on_numeric_condition_change())
        ttk.Entry(numeric_frame, textvariable=self.upload_days_var, width=15).grid(row=5, column=1, sticky=tk.W, padx=(5, 0))
        ttk.Label(numeric_frame, text="일 이내").grid(row=5, column=2, padx=(3, 0))

        # 영상 업로드날짜
        ttk.Label(numeric_frame, text="영상 업로드날짜:").grid(row=6, column=0, sticky=tk.W, pady=2)
        self.video_upload_date_var = tk.StringVar()
        self.video_upload_date_var.trace_add('write', lambda *args: self.on_numeric_condition_change())
        ttk.Entry(numeric_frame, textvariable=self.video_upload_date_var, width=15).grid(row=6, column=1, sticky=tk.W, padx=(5, 0))
        ttk.Label(numeric_frame, text="일 이내").grid(row=6, column=2, padx=(3, 0))

        # 벤치마킹 채널여부
        self.benchmarking_var = tk.BooleanVar(value=False)
        self.benchmarking_var.trace_add('write', lambda *args: self.on_numeric_condition_change())
        ttk.Checkbutton(numeric_frame, text="벤치마킹 채널만",
                       variable=self.benchmarking_var).grid(row=7, column=0, columnspan=3, sticky=tk.W, pady=2)

        # 대본유무 체크박스
        self.script_exists_var = tk.BooleanVar(value=False)
        self.script_exists_var.trace_add('write', lambda *args: self.on_numeric_condition_change())
        ttk.Checkbutton(numeric_frame, text="대본유무",
                       variable=self.script_exists_var).grid(row=8, column=0, columnspan=3, sticky=tk.W, pady=2)

        # 후킹자막 유무 체크박스
        self.hook_subtitle_exists_var = tk.BooleanVar(value=False)
        self.hook_subtitle_exists_var.trace_add('write', lambda *args: self.on_numeric_condition_change())
        ttk.Checkbutton(numeric_frame, text="후킹자막 유무",
                       variable=self.hook_subtitle_exists_var).grid(row=9, column=0, columnspan=3, sticky=tk.W, pady=2)

        # 상위 N개 정렬 추출 서브섹션
        ttk.Separator(numeric_frame, orient='horizontal').grid(row=10, column=0, columnspan=3, sticky=(tk.W, tk.E), pady=(10, 5))

        # 상위 N개 정렬 추출 활성화 체크박스
        self.enable_sort_limit_var = tk.BooleanVar(value=False)
        self.enable_sort_limit_var.trace_add('write', lambda *args: self.on_sort_limit_toggle())
        ttk.Checkbutton(numeric_frame, text="상위 N개 정렬 추출 활성화",
                       variable=self.enable_sort_limit_var).grid(row=11, column=0, columnspan=3, sticky=tk.W, pady=2)

        # 정렬 기준 선택
        ttk.Label(numeric_frame, text="정렬 기준:").grid(row=12, column=0, sticky=tk.W, pady=2)
        self.sort_column_var = tk.StringVar(value="조회수")
        self.sort_column_combo = ttk.Combobox(numeric_frame, textvariable=self.sort_column_var,
                                              values=["조회수", "구독자수", "영상길이", "영상 업로드날짜"],
                                              width=12, state='disabled')
        self.sort_column_combo.grid(row=12, column=1, sticky=tk.W, padx=(5, 0))
        self.sort_column_combo.bind('<<ComboboxSelected>>', lambda e: self.on_numeric_condition_change())

        # 정렬 순서 선택
        self.sort_order_var = tk.StringVar(value="내림차순")
        self.sort_order_combo = ttk.Combobox(numeric_frame, textvariable=self.sort_order_var,
                                             values=["내림차순", "오름차순"],
                                             width=8, state='disabled')
        self.sort_order_combo.grid(row=12, column=2, sticky=tk.W, padx=(3, 0))
        self.sort_order_combo.bind('<<ComboboxSelected>>', lambda e: self.on_numeric_condition_change())

        # 추출 개수 입력
        ttk.Label(numeric_frame, text="추출 개수:").grid(row=13, column=0, sticky=tk.W, pady=2)
        self.sort_limit_var = tk.StringVar(value="100")
        self.sort_limit_var.trace_add('write', lambda *args: self.on_numeric_condition_change())
        self.sort_limit_entry = ttk.Entry(numeric_frame, textvariable=self.sort_limit_var, width=12, state='disabled')
        self.sort_limit_entry.grid(row=13, column=1, sticky=tk.W, padx=(5, 0))
        ttk.Label(numeric_frame, text="개").grid(row=13, column=2, sticky=tk.W, padx=(3, 0))

        # 채널별 상위 N개 추출 섹션
        top_n_frame = ttk.LabelFrame(video_frame, text="채널별 상위 N개 추출", padding="5")
        top_n_frame.grid(row=3, column=0, columnspan=1, sticky=(tk.W, tk.E), pady=(10, 10))
        
        # 상위 N개 추출 활성화 체크박스
        self.enable_top_n_var = tk.BooleanVar(value=False)
        enable_top_n_check = ttk.Checkbutton(top_n_frame, text="상위 N개 추출 활성화", 
                                           variable=self.enable_top_n_var,
                                           command=self.toggle_top_n_section)
        enable_top_n_check.grid(row=0, column=0, columnspan=3, sticky=tk.W, pady=(0, 10))
        
        # 상위 N개 입력
        self.top_n_label = ttk.Label(top_n_frame, text="상위 N개:")
        self.top_n_label.grid(row=1, column=0, sticky=tk.W, pady=2)
        self.top_n_var = tk.StringVar()
        self.top_n_var.trace_add('write', lambda *args: self.on_top_n_change())
        self.top_n_entry = ttk.Entry(top_n_frame, textvariable=self.top_n_var, width=10)
        self.top_n_entry.grid(row=1, column=1, sticky=tk.W, padx=(5, 0))
        self.top_n_count_label = ttk.Label(top_n_frame, text="개")
        self.top_n_count_label.grid(row=1, column=2, padx=(3, 0))
        
        # 채널별/분야별 선택 (라디오 버튼)
        self.top_n_mode_var = tk.StringVar(value="none")
        self.channel_radio = ttk.Radiobutton(top_n_frame, text="채널별", variable=self.top_n_mode_var, value="channel",
                                           command=self.on_top_n_change)
        self.channel_radio.grid(row=2, column=0, sticky=tk.W, pady=2)
        self.field1_radio = ttk.Radiobutton(top_n_frame, text="분야1별", variable=self.top_n_mode_var, value="field1",
                                          command=self.on_top_n_change)
        self.field1_radio.grid(row=2, column=1, sticky=tk.W, pady=2)
        self.field2_radio = ttk.Radiobutton(top_n_frame, text="분야2별", variable=self.top_n_mode_var, value="field2",
                                          command=self.on_top_n_change)
        self.field2_radio.grid(row=2, column=2, sticky=tk.W, pady=2)
        
        # 분야1별 선택 시 추가 옵션들
        self.field1_options_frame = ttk.Frame(top_n_frame)
        self.field1_options_frame.grid(row=3, column=0, columnspan=3, sticky=(tk.W, tk.E), pady=(5, 0))
        
        # 분야2 무시 체크박스
        self.ignore_field2_var = tk.BooleanVar(value=False)
        self.ignore_field2_check = ttk.Checkbutton(self.field1_options_frame, text="분야2 무시 (분야1 전체에서 상위N개 추출)", 
                                                  variable=self.ignore_field2_var, command=self.on_top_n_change)
        self.ignore_field2_check.pack(anchor='w', padx=10)
        
        # 초기에는 숨김 처리
        self.field1_options_frame.grid_remove()
        
        # 상위 N개 관련 위젯들 저장 (나중에 활성화/비활성화용)
        self.top_n_widgets = [
            self.top_n_label, self.top_n_entry, self.top_n_count_label,
            self.channel_radio, self.field1_radio, self.field2_radio
        ]
        
        # N개 추출 조건 해설
        self.top_n_description_var = tk.StringVar(value="상위 N개 추출이 비활성화되어 있습니다")
        top_n_desc_frame = ttk.LabelFrame(top_n_frame, text="추출 조건 해설", padding="3")
        top_n_desc_frame.grid(row=4, column=0, columnspan=3, sticky=(tk.W, tk.E), pady=(10, 0))
        top_n_desc_label = ttk.Label(top_n_desc_frame, textvariable=self.top_n_description_var, 
                                    font=('Arial', 8), foreground='darkgreen', wraplength=400)
        top_n_desc_label.pack(anchor='w', padx=5, pady=3)
        
        # 실시간 조건 부합 행 개수 표시
        self.matching_count_var = tk.StringVar(value="조건을 설정하고 데이터를 불러온 후 자동으로 업데이트됩니다")

        # 초기 상태는 비활성화 (matching_count_var 초기화 후에 호출)
        self.toggle_top_n_section()
        matching_count_label = ttk.Label(video_frame, textvariable=self.matching_count_var,
                                       font=('Arial', 10, 'bold'), foreground='blue')
        matching_count_label.grid(row=4, column=0, pady=(5, 5), sticky=tk.W)

        # 추출 대상 시트 선택
        target_sheet_frame = ttk.Frame(video_frame)
        target_sheet_frame.grid(row=5, column=0, pady=(0, 5), sticky=(tk.W, tk.E))

        ttk.Label(target_sheet_frame, text="추출 대상 시트:").pack(side=tk.LEFT, padx=(0, 5))

        self.target_sheet_var = tk.StringVar(value="조건 추출 영상")
        self.target_sheet_combo = ttk.Combobox(target_sheet_frame, textvariable=self.target_sheet_var,
                                               values=["조건 추출 영상"], state='readonly', width=30)
        self.target_sheet_combo.pack(side=tk.LEFT, padx=(0, 5))

        # 버튼들
        button_frame = ttk.Frame(video_frame)
        button_frame.grid(row=6, column=0, pady=(0, 0), sticky=(tk.W, tk.E))
        button_frame.columnconfigure(0, weight=1)
        button_frame.columnconfigure(1, weight=1)
        button_frame.columnconfigure(2, weight=1)
        button_frame.columnconfigure(3, weight=1)
        
        self.filter_reset_button = ttk.Button(button_frame, text="필터 초기화",
                                            command=self.reset_filters)
        self.filter_reset_button.grid(row=0, column=0, padx=(0, 2), sticky=(tk.W, tk.E))

        self.count_check_button = ttk.Button(button_frame, text="조건에 맞는 행(영상) 갯수 체크",
                                           command=self.check_matching_count)
        self.count_check_button.grid(row=0, column=1, padx=(2, 2), sticky=(tk.W, tk.E))

        self.extract_button = ttk.Button(button_frame, text="조건 추출",
                                       command=self.extract_conditional_videos,
                                       style='Accent.TButton')
        self.extract_button.grid(row=0, column=2, padx=(2, 2), sticky=(tk.W, tk.E))

        self.extract_new_sheet_button = ttk.Button(button_frame, text="새로운 시트에 추출",
                                                   command=self.extract_to_new_sheet)
        self.extract_new_sheet_button.grid(row=0, column=3, padx=(2, 0), sticky=(tk.W, tk.E))

        # AI참고용 데이터추출 버튼
        self.extract_ai_data_button = ttk.Button(button_frame, text="AI참고용 데이터추출",
                                                 command=self.extract_ai_reference_data,
                                                 style='Accent.TButton')
        self.extract_ai_data_button.grid(row=1, column=0, columnspan=4, padx=(0, 0), pady=(5, 0), sticky=(tk.W, tk.E))

        # AI참고용 데이터 복사 섹션
        copy_frame = ttk.LabelFrame(video_frame, text="AI참고용 데이터 복사", padding="10")
        copy_frame.grid(row=7, column=0, columnspan=1, sticky=(tk.W, tk.E), pady=(10, 0))

        # 설명 레이블
        copy_desc_label = ttk.Label(copy_frame,
                                    text="현재 시트를 외부 스프레드시트로 복사합니다.\n"
                                         "• 함수는 값으로 변환되어 복사됩니다\n"
                                         "• 표시형식은 원본을 따라갑니다 (행 높이 21 고정)\n"
                                         "• 동일한 이름의 시트가 있으면 덮어씁니다",
                                    font=('Arial', 8), foreground='darkblue', wraplength=450, justify='left')
        copy_desc_label.grid(row=0, column=0, columnspan=2, sticky=tk.W, pady=(0, 10))

        # 시트 선택 콤보박스
        ttk.Label(copy_frame, text="복사할 시트:").grid(row=1, column=0, sticky=tk.W, pady=5)
        self.copy_sheet_var = tk.StringVar()
        self.copy_sheet_combo = ttk.Combobox(copy_frame, textvariable=self.copy_sheet_var, width=30, state='readonly')
        self.copy_sheet_combo.grid(row=1, column=1, sticky=(tk.W, tk.E), padx=(5, 0), pady=5)

        # 영상 갯수 입력
        ttk.Label(copy_frame, text="복사할 영상 갯수:").grid(row=2, column=0, sticky=tk.W, pady=5)
        self.copy_video_count_var = tk.StringVar(value="100")
        copy_count_entry = ttk.Entry(copy_frame, textvariable=self.copy_video_count_var, width=15)
        copy_count_entry.grid(row=2, column=1, sticky=tk.W, padx=(5, 0), pady=5)

        # 9행을 헤더로 체크박스
        self.copy_header_row9_var = tk.BooleanVar(value=True)
        copy_header_check = ttk.Checkbutton(copy_frame, text="9행을 헤더로 (1-8행 삭제)",
                                           variable=self.copy_header_row9_var)
        copy_header_check.grid(row=3, column=0, columnspan=2, sticky=tk.W, pady=5)

        # AI참고용 시트에 복제 버튼
        self.copy_to_ai_button = ttk.Button(copy_frame, text="AI참고용 시트에 복제",
                                           command=self.copy_to_ai_spreadsheet,
                                           style='Accent.TButton')
        self.copy_to_ai_button.grid(row=4, column=0, columnspan=2, pady=(10, 5))

        # 결과 표시 레이블
        self.copy_result_var = tk.StringVar(value="")
        copy_result_label = ttk.Label(copy_frame, textvariable=self.copy_result_var,
                                     font=('Arial', 9), foreground='green', wraplength=450)
        copy_result_label.grid(row=5, column=0, columnspan=2, sticky=tk.W, pady=(5, 0))

        copy_frame.columnconfigure(1, weight=1)

        # 채널 분류 섹션
        classify_frame = ttk.LabelFrame(video_frame, text="채널 분류", padding="10")
        classify_frame.grid(row=8, column=0, columnspan=1, sticky=(tk.W, tk.E), pady=(10, 0))

        # 설명 레이블
        desc_label = ttk.Label(classify_frame,
                              text="채널 리스트 시트에서 분야1별로 채널을 '{분야1}-채널' 시트로 분류합니다.\n"
                                   "• 시트가 없으면 자동 생성 (채널 리스트 복제, 서식 유지)\n"
                                   "• '가져왔는지 여부'가 빈 행이 아닌 채널만 처리\n"
                                   "• 채널 ID 기준 중복 체크 (중복=업데이트, 신규=추가)",
                              font=('Arial', 8), foreground='darkblue', wraplength=450, justify='left')
        desc_label.grid(row=0, column=0, columnspan=3, sticky=tk.W, pady=(0, 10))

        # 새로고침 버튼
        refresh_button = ttk.Button(classify_frame, text="🔄 채널 리스트에서 분야1 목록 새로고침",
                                   command=self.refresh_classify_field1_list)
        refresh_button.grid(row=1, column=0, columnspan=3, sticky=tk.W, pady=(0, 10))

        # 분야1 체크박스 영역 (스크롤 가능)
        field1_classify_label = ttk.Label(classify_frame, text="분류할 분야1 선택:", font=('Arial', 9, 'bold'))
        field1_classify_label.grid(row=2, column=0, columnspan=3, sticky=tk.W, pady=(0, 3))

        # 전체 선택 체크박스
        select_all_frame = ttk.Frame(classify_frame)
        select_all_frame.grid(row=3, column=0, columnspan=3, sticky=tk.W, pady=(0, 5))

        self.classify_select_all_var = tk.BooleanVar(value=False)
        classify_select_all_check = ttk.Checkbutton(select_all_frame, text="전체 선택/해제",
                                                    variable=self.classify_select_all_var,
                                                    command=self.toggle_all_classify_field1)
        classify_select_all_check.pack(side='left')

        # 스크롤 가능한 체크박스 프레임
        self.classify_field1_container = ttk.Frame(classify_frame)
        self.classify_field1_container.grid(row=4, column=0, columnspan=3, sticky=(tk.W, tk.E), pady=(0, 10))

        self.classify_field1_canvas = tk.Canvas(self.classify_field1_container, height=150, bg='white', relief='sunken', bd=1)
        self.classify_field1_scrollbar = ttk.Scrollbar(self.classify_field1_container, orient="vertical",
                                                       command=self.classify_field1_canvas.yview)
        self.classify_field1_frame = ttk.Frame(self.classify_field1_canvas)

        self.classify_field1_frame.bind("<Configure>",
                                       lambda e: self.classify_field1_canvas.configure(
                                           scrollregion=self.classify_field1_canvas.bbox("all")))
        self.classify_field1_canvas.create_window((0, 0), window=self.classify_field1_frame, anchor="nw")
        self.classify_field1_canvas.configure(yscrollcommand=self.classify_field1_scrollbar.set)

        self.classify_field1_canvas.pack(side="left", fill="both", expand=True)
        self.classify_field1_scrollbar.pack(side="right", fill="y")

        # 초기 상태 표시
        ttk.Label(self.classify_field1_frame, text="'새로고침' 버튼을 클릭하여 분야1 목록을 불러오세요.",
                 foreground='gray').pack(anchor='w', padx=5, pady=5)

        # 분야1 체크박스 딕셔너리 초기화
        self.classify_field1_checkboxes = {}  # {field1_value: (BooleanVar, count)}

        # 채널 분류 실행 버튼
        self.classify_execute_button = ttk.Button(classify_frame, text="선택한 분야1 채널 분류 실행",
                                                 command=self.execute_classify_channels,
                                                 style='Accent.TButton')
        self.classify_execute_button.grid(row=5, column=0, columnspan=3, pady=(5, 5))

        # 결과 표시 레이블
        self.classify_result_var = tk.StringVar(value="")
        classify_result_label = ttk.Label(classify_frame, textvariable=self.classify_result_var,
                                         font=('Arial', 9), foreground='green', wraplength=450)
        classify_result_label.grid(row=6, column=0, columnspan=3, sticky=tk.W, pady=(5, 0))

        classify_frame.columnconfigure(0, weight=1)

        video_frame.columnconfigure(0, weight=1)
        
        # 필터 체크박스 초기화 (빈 딕셔너리로 시작)
        self.field1_checkboxes = {}
        self.field2_checkboxes = {}
        self.shortform_checkboxes = {}
        
        # 분야1-분야2 관계 데이터 저장
        self.field1_field2_mapping = {}  # {field1_value: [field2_values]}
        self.all_field2_values = []  # 전체 분야2 값들
        
        # 분야1 선택 순서 및 색상 관리
        self.field1_selection_order = []  # 분야1 선택 순서
        self.field1_colors = [
            '#FFE4E1',  # 연한 핑크 (MistyRose)
            '#E6F3FF',  # 연한 하늘색 (AliceBlue)
            '#F0FFF0',  # 연한 민트 (Honeydew)
            '#FFF8DC',  # 연한 노랑 (Cornsilk)
            '#F5F0FF'   # 연한 라벤더 (Ghost White with purple tint)
        ]
        self.field2_color_mapping = {}  # {field2_value: color}

    def setup_progress_section(self, parent):
        """하단 진행 상황 섹션 설정 (로그 제거)"""
        # 진행 상황만 간단히 표시
        progress_frame = ttk.Frame(parent)
        progress_frame.grid(row=3, column=0, columnspan=3, sticky=(tk.W, tk.E), pady=(15, 10))
        
        ttk.Label(progress_frame, text="진행 상황:", font=('Arial', 10, 'bold')).pack(anchor=tk.W)
        
        self.progress_var = tk.StringVar(value="대기 중...")
        self.progress_label = ttk.Label(progress_frame, textvariable=self.progress_var)
        self.progress_label.pack(anchor=tk.W, pady=(5, 5))
        
        self.progress_bar = ttk.Progressbar(progress_frame, mode='indeterminate')
        self.progress_bar.pack(fill=tk.X, pady=(0, 5))
        
    def setup_logging(self):
        """로그 설정 (GUI 텍스트 위젯 없이 파일과 콘솔 로그만)"""
        # GUI 로그가 제거되었으므로 파일과 콘솔 로그만 사용
        logger.info("GUI 로그 화면이 제거되었습니다. 로그는 파일과 콘솔에서 확인 가능합니다.")
    
    def setup_key_bindings(self):
        """키 바인딩 설정"""
        self.root.bind('<Escape>', self.on_escape_key)
        self.root.focus_set()  # 윈도우가 포커스를 받을 수 있도록 설정
    
    def on_escape_key(self, event):
        """ESC 키 눌렸을 때 처리"""
        if self.is_running:
            logger.info("🛑 ESC 키로 작업 중단 요청됨")
            self.should_stop = True
            self.update_progress("작업 중단 중...")
    
    def on_mode_change(self):
        """추출 모드 변경 처리"""
        mode = self.extraction_mode_var.get()
        
        # 썸네일 관련 모드일 때만 썸네일 벌크 처리 옵션 표시
        if mode in ["thumbnail", "shorts_thumbnail"]:
            self.bulk_thumbnail_check.grid()
        else:
            self.bulk_thumbnail_check.grid_remove()
    
    def on_location_change(self):
        """저장 위치 변경 처리"""
        if self.save_location_var.get() == "local":
            # 로컬 저장 선택 시 - 경로 선택 프레임 표시
            self.path_frame.grid()
            if not self.path_var.get():
                # 기본 경로 설정
                script_dir = Path(__file__).parent
                default_path = script_dir / "output_docs"
                self.path_var.set(str(default_path))
        else:
            # Google Drive 선택 시 - 경로 선택 프레임 숨김
            self.path_frame.grid_remove()
    
    def browse_folder(self):
        """폴더 선택 다이얼로그"""
        from tkinter import filedialog
        
        initial_dir = self.path_var.get() if self.path_var.get() else str(Path(__file__).parent)
        
        folder_path = filedialog.askdirectory(
            title="저장할 폴더를 선택하세요",
            initialdir=initial_dir
        )
        
        if folder_path:
            self.path_var.set(folder_path)
    
    def _get_save_location_text(self):
        """저장 위치 텍스트 반환"""
        if self.save_location_var.get() == "local":
            path = self.path_var.get() or "프로그램 폴더/output_docs"
            return f"{path} > 각 재생목록별 폴더"
        else:
            return "Google Drive > 각 재생목록별 폴더"
    
    def _get_save_format_text(self):
        """저장 형식 텍스트 반환"""
        location = "로컬 폴더" if self.save_location_var.get() == "local" else "Google Drive"
        file_type = "Google Docs 문서" if self.file_format_var.get() == "docs" else "텍스트 파일(.txt)"
        return f"{location}에 {file_type}로 저장되었습니다."
    
    def _get_additional_info_text(self):
        """추가 정보 텍스트 반환"""
        if self.save_location_var.get() == "local":
            return "💡 필요시 해당 폴더의 파일들을 Google Drive에 수동으로 업로드하세요."
        else:
            return "💡 Google Drive에서 파일을 확인할 수 있습니다."
    
    def update_progress(self, message):
        """진행 상황 업데이트"""
        self.progress_var.set(message)
        self.root.update_idletasks()
    
    def on_spreadsheet_selected(self, event=None):
        """스프레드시트가 선택되면 시트 목록 업데이트"""
        selected_spreadsheet = self.spreadsheet_var.get()
        if selected_spreadsheet:
            self.current_sheet_url = self.sheets_manager.available_spreadsheets[selected_spreadsheet]
            logger.info(f"선택된 스프레드시트: {selected_spreadsheet}")
            logger.info(f"URL: {self.current_sheet_url}")
            
            # 스프레드시트명에 '쇼츠'가 포함되어 있으면 타임스탬프 체크박스를 체크, 아니면 해제
            if '쇼츠' in selected_spreadsheet:
                self.include_timestamp_var.set(True)
                # 쇼츠용 썸네일만 추출 모드 자동 선택
                self.extraction_mode_var.set("shorts_thumbnail")
                logger.info("'쇼츠' 스프레드시트 선택: 타임스탬프 포함 체크, 쇼츠용 썸네일만 추출 모드 선택")
            else:
                self.include_timestamp_var.set(False)
                # 쇼츠가 아닌 경우 기본 모드로 설정
                if self.extraction_mode_var.get() == "shorts_thumbnail":
                    self.extraction_mode_var.set("both")
                logger.info("'쇼츠' 외 스프레드시트 선택: 타임스탬프 포함 해제")
            
            # 시트 목록을 동적으로 가져오기 위해 별도 스레드에서 실행
            thread = threading.Thread(target=self.load_sheet_list)
            thread.daemon = True
            thread.start()
    
    def load_sheet_list(self):
        """선택된 스프레드시트의 시트 목록 로드"""
        try:
            # 구글 시트 인증 (아직 인증되지 않은 경우)
            if not self.sheets_manager.client:
                self.sheets_manager.authenticate()
            
            # 스프레드시트 열기
            workbook = self.sheets_manager.client.open_by_url(self.current_sheet_url)
            sheet_names = [sheet.title for sheet in workbook.worksheets()]
            
            # GUI 업데이트
            self.root.after(0, lambda: self.update_sheet_list(sheet_names))
            
        except Exception as e:
            logger.error(f"시트 목록 로드 실패: {e}")
            # 기본 시트 목록 사용
            self.root.after(0, lambda: self.update_sheet_list(list(self.sheets_manager.available_sheets.keys())))
    
    def update_sheet_list(self, sheet_names):
        """시트 콤보박스 업데이트"""
        self.sheet_combo['values'] = sheet_names
        if sheet_names:
            # "사용 레퍼런스 영상" 시트가 있으면 우선 선택, 없으면 첫 번째 시트 선택
            if '사용 레퍼런스 영상' in sheet_names:
                self.sheet_combo.set('사용 레퍼런스 영상')
                logger.info("'사용 레퍼런스 영상' 시트를 기본 선택으로 설정")
            else:
                self.sheet_combo.set(sheet_names[0])  # 첫 번째 시트를 기본값으로 설정
        self.sheet_info_var.set("시트 목록이 업데이트되었습니다. '시트 정보 확인' 버튼을 클릭하세요.")

        # 매칭 업데이트용 영상 시트 콤보박스도 업데이트
        if hasattr(self, 'video_sheet_combo'):
            # "채널 리스트"를 제외한 시트만 선택 가능
            video_sheets = [name for name in sheet_names if '채널 리스트' not in name]
            self.video_sheet_combo['values'] = video_sheets
            if video_sheets:
                # "영상 리스트"가 있으면 우선 선택
                if any('영상 리스트' in name for name in video_sheets):
                    for name in video_sheets:
                        if '영상 리스트' in name:
                            self.video_sheet_combo.set(name)
                            break
                else:
                    self.video_sheet_combo.set(video_sheets[0])

        # 대본 매칭 업데이트용 원본/타겟 시트 콤보박스도 업데이트
        if hasattr(self, 'source_sheet_combo') and hasattr(self, 'target_sheet_combo'):
            # "채널 리스트"를 제외한 모든 시트
            video_sheets = [name for name in sheet_names if '채널 리스트' not in name]
            self.source_sheet_combo['values'] = video_sheets
            self.target_sheet_combo['values'] = video_sheets
            if video_sheets:
                # 기본값 설정: 원본='사용 레퍼런스 영상', 타겟='조건 추출 영상'
                source_default = next((name for name in video_sheets if '사용 레퍼런스 영상' in name),
                                     video_sheets[0] if len(video_sheets) > 0 else "")
                target_default = next((name for name in video_sheets if '조건 추출 영상' in name),
                                     video_sheets[1] if len(video_sheets) > 1 else "")
                self.source_sheet_combo.set(source_default)
                self.target_sheet_combo.set(target_default)

        # 영상 추가 및 업데이트용 원본/타겟 시트 콤보박스도 업데이트
        if hasattr(self, 'video_add_source_sheet_combo') and hasattr(self, 'video_add_target_sheet_combo'):
            # "채널 리스트"를 제외한 모든 시트
            video_sheets = [name for name in sheet_names if '채널 리스트' not in name]
            self.video_add_source_sheet_combo['values'] = video_sheets
            self.video_add_target_sheet_combo['values'] = video_sheets
            if video_sheets:
                # 기본값 설정: 원본='조건 추출 영상', 타겟='사용 레퍼런스 영상' (대본 매칭과 반대)
                source_default = next((name for name in video_sheets if '조건 추출 영상' in name),
                                     video_sheets[0] if len(video_sheets) > 0 else "")
                target_default = next((name for name in video_sheets if '사용 레퍼런스 영상' in name),
                                     video_sheets[1] if len(video_sheets) > 1 else "")
                self.video_add_source_sheet_combo.set(source_default)
                self.video_add_target_sheet_combo.set(target_default)

        # AI참고용 데이터 복사용 시트 콤보박스도 업데이트
        if hasattr(self, 'copy_sheet_combo'):
            self.copy_sheet_combo['values'] = sheet_names
            if sheet_names:
                # "AI참고용_01" 시트가 있으면 우선 선택
                ai_sheet = next((name for name in sheet_names if 'AI참고용' in name), None)
                if ai_sheet:
                    self.copy_sheet_combo.set(ai_sheet)
                else:
                    self.copy_sheet_combo.set(sheet_names[0])

        # 인증이 완료되고 시트 목록이 로드된 후, 사용 레퍼런스 영상 시트 자동 로드
        if hasattr(self, '_first_sheet_load') and not self._first_sheet_load:
            self._first_sheet_load = True
            # 사용 레퍼런스 영상 시트가 존재하는지 확인
            if '사용 레퍼런스 영상' in sheet_names:
                # 약간의 지연을 두고 자동 로드 실행
                self.root.after(200, self.auto_load_reference_sheet)
                logger.info("'사용 레퍼런스 영상' 시트 자동 로드 시작")
    
    def on_sheet_selected(self, event=None):
        """시트가 선택되면 정보 초기화"""
        selected_sheet = self.sheet_var.get()
        
        # '영상 리스트'가 포함된 시트 선택 시 특별 안내 메시지
        if selected_sheet and '영상 리스트' in selected_sheet:
            self.sheet_info_var.set(f"'{selected_sheet}' 시트가 선택되었습니다. 닥스 대본은 채널별 폴더로 분류되어 추출됩니다.")
        else:
            self.sheet_info_var.set("선택한 시트의 정보를 확인하려면 '시트 정보 확인' 버튼을 클릭하세요.")
        
        # 자동 필터 데이터 로드는 제거 (수동 버튼으로 대체)
        # 사용자가 '시트 데이터 불러오기' 버튼을 클릭해야 함
    
    def check_sheet_info(self):
        """선택한 시트의 정보 확인"""
        if self.is_running:
            messagebox.showwarning("경고", "작업 중에는 시트 정보를 확인할 수 없습니다.")
            return
            
        if not self.sheet_var.get():
            messagebox.showerror("오류", "시트를 선택해주세요.")
            return
        
        # 정보 확인 실행
        self.info_button.config(state='disabled', text='정보 확인 중...')
        self.update_progress("시트 정보 확인 중...")
        
        # 별도 스레드에서 실행
        thread = threading.Thread(target=self.run_sheet_info_check)
        thread.daemon = True
        thread.start()
    
    def run_sheet_info_check(self):
        """실제 시트 정보 확인 작업 수행"""
        try:
            # 구글 시트 인증 (아직 인증되지 않은 경우)
            if not self.sheets_manager.client:
                self.root.after(0, lambda: self.update_progress("구글 시트 인증 중..."))
                self.sheets_manager.authenticate()
            
            selected_sheet_name = self.sheet_var.get()
            
            # 모드 A와 B에 대한 정보 모두 가져오기
            try:
                # 모드 A 정보 (전체)
                video_ids_a, start_row_a, transcript_col_a, _ = self.sheets_manager.get_video_ids_from_sheet(
                    self.current_sheet_url, selected_sheet_name, 'A', None)
                
                # 모드 B 정보 (마지막 데이터 이후)
                video_ids_b, start_row_b, transcript_col_b, _ = self.sheets_manager.get_video_ids_from_sheet(
                    self.current_sheet_url, selected_sheet_name, 'B', None)
                
                # 시트 정보 업데이트
                info_text = f"""📊 '{selected_sheet_name}' 시트 정보:
• 전체 추출 모드: {len(video_ids_a)}개 영상 ({start_row_a}행부터)
• 마지막 데이터 이후 추출: {len(video_ids_b)}개 영상 ({start_row_b}행부터)
• 대본내용 열: {transcript_col_a}번째 열"""
                
                self.root.after(0, lambda: self.sheet_info_var.set(info_text))
                self.root.after(0, lambda: self.update_progress("시트 정보 확인 완료"))
                
            except Exception as e:
                error_msg = str(e)
                if "시트를 찾을 수 없습니다" in error_msg:
                    self.root.after(0, lambda: self.sheet_info_var.set(f"❌ '{selected_sheet_name}' 시트를 찾을 수 없습니다."))
                elif "API 사용량 한도 초과" in error_msg:
                    self.root.after(0, lambda: self.sheet_info_var.set("❌ API 사용량 한도 초과. 잠시 후 다시 시도하세요."))
                else:
                    self.root.after(0, lambda: self.sheet_info_var.set(f"❌ 시트 정보 확인 실패: {error_msg}"))
                self.root.after(0, lambda: self.update_progress("시트 정보 확인 실패"))
                
        except Exception as e:
            error_msg = str(e)
            logger.exception("시트 정보 확인 중 오류 발생:")
            self.root.after(0, lambda: self.sheet_info_var.set(f"❌ 오류 발생: {error_msg}"))
            self.root.after(0, lambda: self.update_progress("시트 정보 확인 오류"))
        finally:
            self.root.after(0, self.info_check_finished)
    
    def info_check_finished(self):
        """시트 정보 확인 완료 후 처리"""
        self.info_button.config(state='normal', text='시트 정보 확인')
    
    def backup_sheet(self):
        """시트 백업 실행"""
        if self.is_running:
            messagebox.showwarning("경고", "작업 중에는 백업을 실행할 수 없습니다.")
            return
            
        if not self.sheet_var.get():
            messagebox.showerror("오류", "백업할 시트를 선택해주세요.")
            return
        
        # 확인 대화상자
        selected_sheet = self.sheet_var.get()
        if not messagebox.askyesno("시트 백업", 
                                  f"'{selected_sheet}' 시트를 백업하시겠습니까?\n\n"
                                  f"백업 시트는 '{selected_sheet}_백업01' 형태로 생성되며\n"
                                  f"시트 목록 맨 뒤에 배치됩니다."):
            return
        
        # 백업 실행
        self.backup_button.config(state='disabled', text='백업 중...')
        self.update_progress("시트 백업 중...")
        
        # 별도 스레드에서 실행
        thread = threading.Thread(target=self.run_backup)
        thread.daemon = True
        thread.start()
    
    def run_backup(self):
        """실제 백업 작업 수행"""
        try:
            # 구글 시트 인증 (아직 인증되지 않은 경우)
            if not self.sheets_manager.client:
                self.root.after(0, lambda: self.update_progress("구글 시트 인증 중..."))
                self.sheets_manager.authenticate()
            
            selected_sheet = self.sheet_var.get()
            backup_name = self.sheets_manager.backup_sheet(self.current_sheet_url, selected_sheet)
            
            # 성공 메시지
            self.root.after(0, lambda: messagebox.showinfo("백업 완료", 
                                                          f"백업이 완료되었습니다!\n\n"
                                                          f"원본: {selected_sheet}\n"
                                                          f"백업: {backup_name}\n\n"
                                                          f"백업 시트는 시트 목록 맨 뒤에 배치되었습니다."))
            self.root.after(0, lambda: self.update_progress("백업 완료"))
            
        except Exception as e:
            error_msg = str(e)
            logger.exception("시트 백업 중 오류 발생:")
            self.root.after(0, lambda: messagebox.showerror("백업 실패", 
                                                           f"백업 중 오류가 발생했습니다:\n{error_msg}"))
            self.root.after(0, lambda: self.update_progress("백업 실패"))
        finally:
            self.root.after(0, self.backup_finished)
    
    def backup_finished(self):
        """백업 완료 후 처리"""
        self.backup_button.config(state='normal', text='시트 백업')
    
    def test_connection(self):
        """구글 시트 연결 테스트 실행"""
        if self.is_running:
            messagebox.showwarning("경고", "작업 중에는 연결 테스트를 실행할 수 없습니다.")
            return
        
        # 테스트 실행
        self.test_button.config(state='disabled', text='테스트 중...')
        self.update_progress("구글 시트 연결 테스트 중...")
        
        # 별도 스레드에서 실행
        thread = threading.Thread(target=self.run_connection_test)
        thread.daemon = True
        thread.start()
    
    def run_connection_test(self):
        """실제 연결 테스트 작업 수행"""
        try:
            # 연결 테스트 실행 (async 호출)
            test_result = asyncio.run(self.sheets_manager.test_connection(self.current_sheet_url))
            
            # 결과 메시지 생성
            if test_result['status'] == 'success':
                transcript_info = ""
                if 'transcript_success' in test_result:
                    if test_result['transcript_success']:
                        transcript_info = f"""
🎯 대본 추출 테스트: ✅ 성공
• 추출 세그먼트: {test_result['transcript_segments']}개
• 사용 언어: {test_result['transcript_language']}
• 성공 URL: {test_result['transcript_final_url'].split('/')[-1]}"""
                    else:
                        transcript_info = f"""
🎯 대본 추출 테스트: ❌ 실패
• 오류 내용: {test_result['transcript_error']}"""
                
                result_msg = f"""✅ 구글 시트 연결 테스트 성공!

📊 기본 테스트 결과:
• A2 셀 값 (영상 ID 갯수): {test_result['a2_value']}
• A10 셀 값 (첫번째 영상 ID): {test_result['a10_value']}{transcript_info}

{test_result['message']}"""
                
                self.root.after(0, lambda: messagebox.showinfo("연결 테스트 성공", result_msg))
                self.root.after(0, lambda: self.update_progress("연결 테스트 성공"))
            else:
                self.root.after(0, lambda: messagebox.showerror("연결 테스트 실패", test_result['message']))
                self.root.after(0, lambda: self.update_progress("연결 테스트 실패"))
                
        except Exception as e:
            error_msg = str(e)
            logger.exception("연결 테스트 중 오류 발생:")
            self.root.after(0, lambda: messagebox.showerror("연결 테스트 오류", 
                                                           f"연결 테스트 중 오류가 발생했습니다:\n{error_msg}"))
            self.root.after(0, lambda: self.update_progress("연결 테스트 오류"))
        finally:
            self.root.after(0, self.test_finished)
    
    def test_finished(self):
        """연결 테스트 완료 후 처리"""
        self.test_button.config(state='normal', text='연결 테스트')
    
    def start_extraction(self):
        """대본 추출 시작"""
        if self.is_running:
            return
            
        if not self.sheet_var.get():
            messagebox.showerror("오류", "시트를 선택해주세요.")
            return
            
        self.is_running = True
        self.run_button.config(state='disabled', text='추출 중...')
        self.progress_bar.start()
        
        # 별도 스레드에서 실행
        thread = threading.Thread(target=self.run_extraction)
        thread.daemon = True
        thread.start()
    
    def run_extraction(self):
        """실제 추출 작업 수행"""
        try:
            asyncio.run(self.extract_transcripts())
        except Exception as e:
            logger.exception("추출 작업 중 예상치 못한 오류 발생:")
            self.root.after(0, lambda: messagebox.showerror("오류", f"추출 중 오류가 발생했습니다:\n{str(e)}"))
        finally:
            self.root.after(0, self.extraction_finished)
    
    async def extract_transcripts(self):
        """비동기 대본 추출 (브라우저 자동화 + HTTP 폴백)"""
        try:
            # 구글 시트 인증
            self.root.after(0, lambda: self.update_progress("구글 시트 인증 중..."))
            self.sheets_manager.authenticate()
            
            selected_sheet_name = self.sheet_var.get()
            
            # 중간 누락 대본 추출 옵션 확인
            extract_missing = self.extract_missing_transcripts_var.get()
            if extract_missing:
                mode = 'C'  # 중간 누락 대본 추출 모드
                logger.info("🔍 중간 누락 대본 추출 모드 활성화")
            else:
                mode = self.mode_var.get()
            
            # GUI에서 설정 값 가져오기
            collect_all = self.collect_all_var.get()
            include_timestamp = self.include_timestamp_var.get()
            bulk_transcript = self.bulk_transcript_var.get()
            max_count = None
            
            logger.info(f"📋 타임스탬프 포함 설정: {'포함' if include_timestamp else '제외'}")
            logger.info(f"📦 벌크 처리 설정: {'활성화' if bulk_transcript else '비활성화'}")
            
            if not collect_all:
                # 마지막 데이터까지 수집이 체크되지 않은 경우에만 수집 갯수 제한 적용
                try:
                    collect_count_str = self.collect_count_var.get().strip()
                    if collect_count_str:
                        max_count = int(collect_count_str)
                        if max_count <= 0:
                            logger.error("수집 갯수는 1 이상의 양수여야 합니다.")
                            self.root.after(0, lambda: messagebox.showerror("입력 오류", "수집 갯수는 1 이상의 양수를 입력해주세요."))
                            return
                        logger.info(f"📊 수집 갯수 제한: 최대 {max_count}개")
                    else:
                        logger.warning("수집 갯수가 입력되지 않았습니다. 기본값 20개를 사용합니다.")
                        max_count = 20
                except ValueError:
                    logger.error("수집 갯수는 숫자만 입력할 수 있습니다.")
                    self.root.after(0, lambda: messagebox.showerror("입력 오류", "수집 갯수는 숫자만 입력해주세요."))
                    return
            else:
                logger.info("📊 마지막 데이터까지 모든 영상 수집")
            
            # 비디오 ID 가져오기
            self.root.after(0, lambda: self.update_progress("비디오 ID 목록 가져오는 중..."))
            video_ids, start_row, transcript_col, row_mapping = self.sheets_manager.get_video_ids_from_sheet(
                self.current_sheet_url, selected_sheet_name, mode, max_count)
            
            if not video_ids:
                logger.error("처리할 비디오 ID를 찾을 수 없습니다.")
                return
                
            logger.info(f"📋 {start_row}행부터 총 {len(video_ids)}개 비디오 처리 예정")
            
            # 벌크 처리 배치 크기 결정
            if bulk_transcript:
                # 스프레드시트 이름에 따라 배치 크기 결정
                is_shorts = '쇼츠' in selected_sheet_name
                batch_size = 30 if is_shorts else 10
                logger.info(f"📦 벌크 처리 활성화: {len(video_ids)}개 항목을 {batch_size}개씩 배치 처리 ({'쇼츠' if is_shorts else '일반'} 모드)")
            else:
                # 일반 처리는 기존 배치 크기 사용
                batch_size = 50  # 브라우저의 기본 배치 크기
            
            # 브라우저 자동화 또는 HTTP 방식으로 자막 추출
            results = None
            elapsed_time = 0
            use_browser = self.browser_automation_var.get()
            
            if use_browser:
                try:
                    # Main_Extract.py의 브라우저 자동화 기능 사용
                    from Main_Extract import BrowserTranscriptExtractor, TranscriptConfig as BrowserConfig
                    
                    browser_config = BrowserConfig(
                        target_language=None,
                        max_concurrent=2,
                        retry_attempts=1,
                        delay_between_requests=1.5,
                        use_browser_automation=True,
                        headless=True,  # GUI에서는 헤드리스 모드 사용
                        use_user_profile=False
                    )
                    
                    self.root.after(0, lambda: self.update_progress("브라우저 자동화 모드로 자막 추출 중..."))
                    logger.info("🌐 브라우저 자동화 모드로 자막 추출 시도")
                    
                    with BrowserTranscriptExtractor(browser_config) as browser_extractor:
                        start_time = time.time()
                        
                        # 브라우저 자동화로 실시간 시트 업데이트와 함께 추출 (동적 배치 크기 사용)
                        browser_results = browser_extractor.process_videos_batch(
                            video_ids,
                            sheets_manager=self.sheets_manager,
                            sheet_url=self.current_sheet_url,
                            sheet_name=selected_sheet_name,
                            start_row=start_row,
                            transcript_col=transcript_col,
                            include_timestamp=include_timestamp,
                            batch_size=batch_size  # 벌크 처리 시 동적 배치 크기 사용
                        )
                        
                        elapsed_time = time.time() - start_time
                        
                        # 브라우저 결과를 GUI용 형식으로 변환
                        results = []
                        for br in browser_results:
                            gui_result = VideoData(
                                video_id=br.video_id,
                                title=br.title,
                                transcript=br.transcript,
                                language=br.language,
                                error=br.error
                            )
                            results.append(gui_result)
                        
                        logger.info("✅ 브라우저 자동화 모드 완료 (실시간 시트 업데이트 포함)")
                    
                except Exception as browser_error:
                    logger.warning(f"⚠️  브라우저 자동화 실패: {browser_error}")
                    logger.info("🔄 HTTP 방식으로 폴백 시도")
                    use_browser = False  # 폴백
            
            if not use_browser:
                # HTTP 방식 사용
                config = TranscriptConfig(
                    target_language=None,
                    max_concurrent=2,
                    retry_attempts=1,
                    delay_between_requests=1.5
                )
                
                self.root.after(0, lambda: self.update_progress("HTTP 방식으로 자막 추출 중..."))
                
                async with MainYouTubeShortsTranscriptExtractor(config) as extractor:
                    start_time = time.time()
                    
                    results = await extractor.process_videos_batch(
                        video_ids, 
                        batch_size=batch_size,  # 벌크 처리 시 동적 배치 크기 사용
                        progress_callback=lambda msg: self.root.after(0, lambda: self.update_progress(msg))
                    )
                    
                    elapsed_time = time.time() - start_time
                    
                    # HTTP 방식에서는 별도로 시트 업데이트
                    self.root.after(0, lambda: self.update_progress("구글 시트 업데이트 중..."))
                    self.sheets_manager.update_sheet_with_transcripts(
                        self.current_sheet_url,
                        selected_sheet_name,
                        results,
                        start_row,
                        transcript_col,
                        include_timestamp,
                        row_mapping
                    )
            
            # 새로운 윈도우 알림창 스타일 결과 표시
            if results:
                success_count = sum(1 for r in results if not r.error)
                error_count = len(results) - success_count
                end_row = start_row + len(results) - 1
                
                # 모드 텍스트 생성
                mode_text_map = {
                    'A': '전체 추출 모드',
                    'B': '마지막 데이터 이후 추출',
                    'C': '중간 누락 대본 추출 시도'
                }
                mode_text = mode_text_map.get(mode, f'모드 {mode}')
                
                # 결과 데이터 구성
                result_data = {
                    'success_count': success_count,
                    'error_count': error_count,
                    'elapsed_time': elapsed_time,
                    'start_row': start_row,
                    'end_row': end_row,
                    'mode_text': mode_text
                }
                
                self.root.after(0, lambda: self.show_common_result_notification("대본 추출", result_data))
                
        except Exception as e:
            logger.exception("대본 추출 중 오류 발생:")
            raise
    
    def show_result_summary(self, start_row: int, end_row: int, success_count: int, 
                           error_count: int, elapsed_time: float, mode: str):
        """결과 집계 창 표시"""
        result_window = tk.Toplevel(self.root)
        result_window.title("추출 결과")
        result_window.geometry("500x400")
        result_window.transient(self.root)
        result_window.grab_set()
        
        # 결과 텍스트
        text_frame = ttk.Frame(result_window, padding="20")
        text_frame.pack(fill=tk.BOTH, expand=True)
        
        result_text = tk.Text(text_frame, wrap=tk.WORD, font=('Arial', 10))
        scrollbar = ttk.Scrollbar(text_frame, orient=tk.VERTICAL, command=result_text.yview)
        result_text.configure(yscrollcommand=scrollbar.set)
        
        result_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        
        # 결과 내용
        mode_text = "전체 추출" if mode == "A" else "마지막 대본 데이터 이후 추출"
        success_rate = (success_count/(success_count + error_count)*100) if (success_count + error_count) > 0 else 0
        
        result_content = f"""
{'='*50}
📊 대본 추출 결과 집계
{'='*50}

🎯 실행 모드: {mode_text}
📍 처리 범위: {start_row}행 ~ {end_row}행  
📊 총 처리된 영상: {success_count + error_count}개
✅ 성공한 추출: {success_count}개
❌ 실패한 추출: {error_count}개
⏱️  총 소요시간: {elapsed_time:.1f}초
🎯 성공률: {success_rate:.1f}%

완료 시간: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
{'='*50}
        """
        
        result_text.insert(tk.END, result_content)
        result_text.config(state=tk.DISABLED)
        
        # 확인 버튼
        ttk.Button(result_window, text="확인", 
                  command=result_window.destroy).pack(pady=10)
        
        self.update_progress("추출 완료!")
    
    def extraction_finished(self):
        """추출 완료 후 처리"""
        self.is_running = False
        self.run_button.config(state='normal', text='대본 추출 시작')
        self.progress_bar.stop()
    
    def start_docs_extraction(self):
        """구글 닥스 추출 시작"""
        if self.is_running:
            messagebox.showwarning("경고", "다른 작업이 실행 중입니다.")
            return
            
        if not self.sheet_var.get():
            messagebox.showerror("오류", "시트를 선택해주세요.")
            return
        
        # 갯수 유효성 검사
        if self.extract_to_end_var.get():
            # 마지막 데이터까지 수집 체크박스가 체크된 경우
            docs_count = 999999  # 매우 큰 수로 설정하여 모든 데이터 수집
            logger.info("마지막 데이터까지 수집 모드 활성화")
        else:
            try:
                docs_count = int(self.docs_count_var.get().strip())
                if docs_count <= 0:
                    raise ValueError("갯수는 1 이상이어야 합니다")
            except ValueError as e:
                messagebox.showerror("입력 오류", f"닥스 추출 갯수를 올바르게 입력해주세요.\n({e})")
                return
        
        # 추출 모드에 따른 확인 메시지
        save_location = "로컬 폴더" if self.save_location_var.get() == "local" else "Google Drive"
        extraction_mode = self.extraction_mode_var.get()
        
        if extraction_mode == "both":
            mode_text = "Docs + TXT 동시 저장"
            condition_text = "구글 닥스 여부가 비어있는 항목"
        elif extraction_mode == "missing":
            mode_text = "누락 대본 추출 (Docs/TXT 중 비어있는 것만)"
            condition_text = "구글 닥스 여부나 대본txt 여부 중 하나라도 비어있는 항목"
        elif extraction_mode == "shorts_thumbnail":
            mode_text = "쇼츠용 썸네일만 추출"
            condition_text = "대본여부가 'ㅇ'이고 영상ID가 있는 항목"
        else:  # thumbnail
            mode_text = "썸네일 추출 (기존 Docs에 썸네일 이미지 추가)"
            condition_text = "Docs와 TXT가 있고 썸네일이 비어있는 항목"
        
        # 확인 팝업 제거
        if False and messagebox.askyesno("문서 추출 확인", 
                                  f"선택한 시트에서 조건에 맞는 최대 {docs_count}개 항목을\n"
                                  f"{save_location}에 {mode_text}로 추출하시겠습니까?\n\n"
                                  f"* 대본내용이 있고\n"
                                  f"* 대본유무가 'ㅇ'이고\n"
                                  f"* {condition_text}만 처리됩니다."):
            return
            
        self.is_running = True
        self.should_stop = False  # 중단 플래그 초기화
        self.docs_button.config(state='disabled', text='닥스 추출 중... (ESC: 중단)')
        self.progress_bar.start()
        
        # 별도 스레드에서 실행
        thread = threading.Thread(target=self.run_docs_extraction, args=(docs_count,))
        thread.daemon = True
        thread.start()
    
    def run_docs_extraction(self, docs_count: int):
        """실제 구글 닥스 추출 작업 수행"""
        try:
            self.root.after(0, lambda: self.update_progress("구글 시트 인증 중..."))
            
            # 구글 시트 인증 (아직 인증되지 않은 경우)
            if not self.sheets_manager.client:
                self.sheets_manager.authenticate()
            
            # Google Drive 저장인 경우에만 OAuth 인증 수행
            if self.save_location_var.get() == "drive":
                self.root.after(0, lambda: self.update_progress("OAuth 인증 중..."))
                if not self.sheets_manager.oauth_credentials or not self.sheets_manager.oauth_credentials.valid:
                    self.sheets_manager.authenticate_oauth()
            
            selected_sheet_name = self.sheet_var.get()
            
            self.root.after(0, lambda: self.update_progress("문서 추출 중..."))
            
            # 사용자 선택사항 저장
            self.sheets_manager._save_location = self.save_location_var.get()
            self.sheets_manager._extraction_mode = self.extraction_mode_var.get()
            self.sheets_manager._custom_path = self.path_var.get() if self.save_location_var.get() == "local" else None
            self.sheets_manager._bulk_thumbnail = self.bulk_thumbnail_var.get()
            self.sheets_manager._bulk_docs = self.bulk_docs_var.get()
            self.sheets_manager._extract_to_end = self.extract_to_end_var.get()
            
            # 구글 닥스 추출 실행 (중단 플래그 전달)
            result = self.sheets_manager.process_docs_extraction(
                self.current_sheet_url,
                selected_sheet_name,
                docs_count,
                stop_callback=lambda: self.should_stop
            )
            
            # 새로운 윈도우 알림창 스타일 결과 표시
            self.root.after(0, lambda: self.show_docs_result_new_style(result))
            self.root.after(0, lambda: self.update_progress("구글 닥스 추출 완료"))
            
        except Exception as e:
            logger.exception("구글 닥스 추출 중 오류 발생:")
            self.root.after(0, lambda: messagebox.showerror("구글 닥스 추출 오류", 
                                                           f"구글 닥스 추출 중 오류가 발생했습니다:\n{str(e)}"))
            self.root.after(0, lambda: self.update_progress("구글 닥스 추출 실패"))
        finally:
            self.root.after(0, self.docs_extraction_finished)
    
    def show_common_result_notification(self, task_type: str, result: Dict[str, int]):
        """공용 작업 완료 알림 팝업 (구글 닥스 추출, 대본 추출 공통 사용)"""
        try:
            logger.info(f"🎉 {task_type} 새로운 윈도우 알림창 스타일 결과 팝업 표시 시작")
            # 알림음 재생
            self.play_notification_sound()
            
            # 구글 닥스 추출 결과 계산
            if task_type == "구글 닥스 추출":
                total_success = result.get('docs_success', 0) + result.get('txt_success', 0)
                total_error = result.get('docs_error', 0) + result.get('txt_error', 0)
                success_rate = (total_success/(total_success + total_error)*100) if (total_success + total_error) > 0 else 0
                status_icon = "🛑" if result.get('stopped', False) else "🎉"
                status_text = "중단됨" if result.get('stopped', False) else "완료"
                processed_count = result.get('processed', 0)
                
                message = f"📊 처리 항목: {processed_count}개\n✅ 성공: {total_success}개 | ❌ 실패: {total_error}개\n🎯 성공률: {success_rate:.1f}%"
                
            # 대본 추출 결과 계산
            elif task_type == "대본 추출":
                success_count = result.get('success_count', 0)
                error_count = result.get('error_count', 0)
                total_processed = success_count + error_count
                success_rate = (success_count/total_processed*100) if total_processed > 0 else 0
                status_icon = "🎉"
                status_text = "완료"
                
                message = f"📊 처리 항목: {total_processed}개\n✅ 성공: {success_count}개 | ❌ 실패: {error_count}개\n🎯 성공률: {success_rate:.1f}%"
                if result.get('elapsed_time'):
                    message += f"\n⏱️ 소요 시간: {result['elapsed_time']:.1f}초"
            
            # 윈도우 알림창 스타일 팝업 생성
            self.show_windows_notification_popup(
                title=f"{task_type} {status_text}",
                message=message,
                icon=status_icon,
                result_details=result,
                task_type=task_type
            )
            logger.info(f"🎉 {task_type} 새로운 윈도우 알림창 스타일 결과 팝업 표시 완료")
            
        except Exception as e:
            logger.exception(f"❌ {task_type} 새로운 결과 팝업 표시 실패: {e}")
            # 실패 시 기본 메시지박스 표시
            import tkinter.messagebox as messagebox
            if task_type == "구글 닥스 추출":
                messagebox.showinfo("구글 닥스 추출 완료", 
                                   f"처리된 항목: {result.get('processed', 0)}개\n"
                                   f"성공: {result.get('docs_success', 0) + result.get('txt_success', 0)}개")
            elif task_type == "대본 추출":
                messagebox.showinfo("대본 추출 완료",
                                   f"성공: {result.get('success_count', 0)}개\n"
                                   f"실패: {result.get('error_count', 0)}개")
    
    def show_docs_result_new_style(self, result: Dict[str, int]):
        """구글 닥스 추출 결과를 새로운 윈도우 알림창 스타일로 표시"""
        self.show_common_result_notification("구글 닥스 추출", result)
    
    def play_notification_sound(self):
        """알림음 재생"""
        try:
            import winsound
            # Windows 시스템 알림음 재생
            winsound.PlaySound("SystemAsterisk", winsound.SND_ALIAS | winsound.SND_ASYNC)
            logger.debug("🔔 시스템 알림음 재생 성공")
        except ImportError:
            # winsound가 없는 경우 기본 벨 소리
            try:
                print('\a')  # 시스템 벨 소리
                logger.debug("🔔 기본 벨 소리 재생")
            except:
                logger.warning("🔕 알림음 재생 불가")
        except Exception as e:
            logger.warning(f"🔕 알림음 재생 실패: {e}")
    
    def show_windows_notification_popup(self, title: str, message: str, icon: str = "ℹ️", result_details: Dict = None, task_type: str = "구글 닥스 추출"):
        """Windows 알림창 스타일의 팝업 창 표시"""
        # 화면 크기 가져오기
        screen_width = self.root.winfo_screenwidth()
        screen_height = self.root.winfo_screenheight()
        
        # 팝업 창 생성
        popup = tk.Toplevel(self.root)
        popup.title("")
        popup.configure(bg='#2d2d30')  # 어두운 배경색
        
        # 창 크기 및 위치 설정 (우측 하단에 표시)
        popup_width = 400
        popup_height = 200
        x = screen_width - popup_width - 20
        y = screen_height - popup_height - 80  # 작업 표시줄 고려
        
        popup.geometry(f"{popup_width}x{popup_height}+{x}+{y}")
        popup.overrideredirect(True)  # 타이틀 바 제거
        popup.attributes("-topmost", True)  # 최상위 표시
        popup.attributes("-alpha", 0.95)  # 약간 투명
        
        # 메인 프레임 (어두운 배경)
        main_frame = tk.Frame(popup, bg='#2d2d30', relief='solid', bd=1)
        main_frame.pack(fill='both', expand=True, padx=2, pady=2)
        
        # 헤더 프레임 (제목 영역)
        header_frame = tk.Frame(main_frame, bg='#0078d4', height=40)  # Windows 파란색
        header_frame.pack(fill='x', padx=0, pady=0)
        header_frame.pack_propagate(False)
        
        # 아이콘과 제목
        title_frame = tk.Frame(header_frame, bg='#0078d4')
        title_frame.pack(expand=True, fill='both')
        
        icon_label = tk.Label(title_frame, text=icon, font=('Segoe UI', 14), 
                             bg='#0078d4', fg='white')
        icon_label.pack(side='left', padx=(10, 5), pady=8)
        
        title_label = tk.Label(title_frame, text=title, font=('Segoe UI', 11, 'bold'), 
                              bg='#0078d4', fg='white')
        title_label.pack(side='left', pady=8)
        
        # 닫기 버튼
        close_btn = tk.Label(title_frame, text="✕", font=('Segoe UI', 12), 
                            bg='#0078d4', fg='white', cursor='hand2')
        close_btn.pack(side='right', padx=(5, 10), pady=8)
        close_btn.bind("<Button-1>", lambda e: popup.destroy())
        close_btn.bind("<Enter>", lambda e: close_btn.config(bg='#e81123'))  # 빨간색 호버
        close_btn.bind("<Leave>", lambda e: close_btn.config(bg='#0078d4'))
        
        # 내용 프레임
        content_frame = tk.Frame(main_frame, bg='#2d2d30')
        content_frame.pack(fill='both', expand=True, padx=15, pady=10)
        
        # 메시지 텍스트
        message_label = tk.Label(content_frame, text=message, 
                                font=('Segoe UI', 10), bg='#2d2d30', fg='white',
                                justify='left', wraplength=350)
        message_label.pack(anchor='w')
        
        # 버튼 프레임
        button_frame = tk.Frame(main_frame, bg='#2d2d30')
        button_frame.pack(fill='x', padx=15, pady=(0, 15))
        
        # 상세보기 버튼
        def show_details():
            popup.destroy()
            if task_type == "구글 닥스 추출":
                self.show_detailed_result(result_details or {})
            elif task_type == "대본 추출":
                self.show_transcript_detailed_result(result_details or {})
        
        detail_btn = tk.Button(button_frame, text="상세보기", 
                              font=('Segoe UI', 9), bg='#0078d4', fg='white',
                              border=0, padx=15, pady=5, cursor='hand2',
                              command=show_details)
        detail_btn.pack(side='left')
        
        # 확인 버튼
        ok_btn = tk.Button(button_frame, text="확인", 
                          font=('Segoe UI', 9, 'bold'), bg='#0078d4', fg='white',
                          border=0, padx=20, pady=5, cursor='hand2',
                          command=popup.destroy)
        ok_btn.pack(side='right')
        
        # 호버 효과
        for btn in [detail_btn, ok_btn]:
            btn.bind("<Enter>", lambda e, b=btn: b.config(bg='#106ebe'))
            btn.bind("<Leave>", lambda e, b=btn: b.config(bg='#0078d4'))
        
        # ESC 키로 닫기
        popup.bind('<Escape>', lambda e: popup.destroy())
        popup.focus_set()
        
        # 자동 닫기 (10초 후)
        popup.after(10000, lambda: popup.destroy() if popup.winfo_exists() else None)
        
        # 슬라이드 인 애니메이션
        self.animate_slide_in(popup, x, y, popup_width, popup_height)
    
    def animate_slide_in(self, window, target_x, target_y, width, height):
        """팝업 창 슬라이드 인 애니메이션"""
        start_x = target_x + width
        steps = 15
        step_size = width // steps
        
        def slide_step(step):
            if step < steps and window.winfo_exists():
                current_x = start_x - (step * step_size)
                window.geometry(f"{width}x{height}+{current_x}+{target_y}")
                window.after(20, lambda: slide_step(step + 1))
            elif window.winfo_exists():
                window.geometry(f"{width}x{height}+{target_x}+{target_y}")
        
        slide_step(0)
    
    def show_detailed_result(self, result: Dict[str, int]):
        """상세 결과 창 표시"""
        result_window = tk.Toplevel(self.root)
        result_window.title("구글 닥스 추출 상세 결과")
        result_window.geometry("500x400")
        result_window.transient(self.root)
        result_window.grab_set()
        
        # 결과 텍스트
        text_frame = ttk.Frame(result_window, padding="20")
        text_frame.pack(fill=tk.BOTH, expand=True)
        
        result_text = tk.Text(text_frame, wrap=tk.WORD, font=('Consolas', 10))
        scrollbar = ttk.Scrollbar(text_frame, orient=tk.VERTICAL, command=result_text.yview)
        result_text.configure(yscrollcommand=scrollbar.set)
        
        result_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        
        # 결과 내용
        total_success = result.get('docs_success', 0) + result.get('txt_success', 0)
        total_error = result.get('docs_error', 0) + result.get('txt_error', 0)
        success_rate = (total_success/(total_success + total_error)*100) if (total_success + total_error) > 0 else 0
        status_icon = "🛑" if result.get('stopped', False) else "🎉"
        status_text = "중단됨" if result.get('stopped', False) else "완료됨"
        
        result_content = f"""
{'='*50}
📄 구글 닥스 추출 상세 결과
{'='*50}

{status_icon} 작업 상태: {status_text}
📊 처리된 항목: {result['processed']}개

📝 Google Docs 결과:
   ✅ 성공: {result.get('docs_success', 0)}개
   ❌ 실패: {result.get('docs_error', 0)}개

📄 TXT 파일 결과:
   ✅ 성공: {result.get('txt_success', 0)}개  
   ❌ 실패: {result.get('txt_error', 0)}개

🖼️ 썸네일 결과:
   ✅ 성공: {result.get('thumbnail_success', 0)}개
   ❌ 실패: {result.get('thumbnail_error', 0)}개

🎯 전체 성공률: {success_rate:.1f}%

📁 파일 저장 위치: 
   {self._get_save_location_text()}

📝 채널별 폴더 구조 (영상 리스트 시트):
   📁 채널별 대본 (영상 리스트 시트)/
       📁 [채널명]/
           📄 Google Docs 파일들 (직접 저장)
           📁 txt/
           📁 썸네일/

{self._get_additional_info_text()}

{status_text} 시간: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
{'='*50}
        """
        
        result_text.insert(tk.END, result_content)
        result_text.config(state=tk.DISABLED)
        
        # 버튼 프레임
        button_frame = ttk.Frame(result_window)
        button_frame.pack(pady=15)
        
        # 폴더 열기 버튼 (로컬 저장인 경우만)
        if self.save_location_var.get() == "local":
            def open_output_folder():
                try:
                    if self.path_var.get():
                        output_dir = Path(self.path_var.get())
                    else:
                        script_dir = Path(__file__).parent
                        output_dir = script_dir / "output_docs"
                    import subprocess
                    subprocess.run(['explorer', str(output_dir)], check=True)
                except Exception as e:
                    logger.error(f"폴더 열기 실패: {e}")
            
            ttk.Button(button_frame, text="출력 폴더 열기", 
                      command=open_output_folder).pack(side=tk.LEFT, padx=(0, 10))
        
        # 확인 버튼
        ttk.Button(button_frame, text="확인", 
                  command=result_window.destroy).pack(side=tk.LEFT)
    
    def show_transcript_detailed_result(self, result: Dict[str, int]):
        """대본 추출 상세 결과 창 표시"""
        result_window = tk.Toplevel(self.root)
        result_window.title("대본 추출 상세 결과")
        result_window.geometry("500x400")
        result_window.transient(self.root)
        result_window.grab_set()
        
        # 결과 텍스트
        text_frame = ttk.Frame(result_window, padding="20")
        text_frame.pack(fill=tk.BOTH, expand=True)
        
        result_text = tk.Text(text_frame, wrap=tk.WORD, font=('Consolas', 10))
        scrollbar = ttk.Scrollbar(text_frame, orient=tk.VERTICAL, command=result_text.yview)
        result_text.configure(yscrollcommand=scrollbar.set)
        
        result_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        
        # 결과 내용
        success_count = result.get('success_count', 0)
        error_count = result.get('error_count', 0)
        total_processed = success_count + error_count
        success_rate = (success_count/total_processed*100) if total_processed > 0 else 0
        
        result_content = f"""
{'='*50}
📄 대본 추출 상세 결과
{'='*50}

🎉 작업 상태: 완료됨
📊 처리된 항목: {total_processed}개

📝 추출 결과:
   ✅ 성공: {success_count}개
   ❌ 실패: {error_count}개

🎯 전체 성공률: {success_rate:.1f}%

📁 추출 범위:
   시작 행: {result.get('start_row', 'N/A')}행
   종료 행: {result.get('end_row', 'N/A')}행

⏱️ 소요 시간: {result.get('elapsed_time', 0):.1f}초

📋 추출 모드: {result.get('mode_text', 'N/A')}

완료 시간: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
{'='*50}
        """
        
        result_text.insert(tk.END, result_content)
        result_text.config(state=tk.DISABLED)
        
        # 버튼 프레임
        button_frame = ttk.Frame(result_window)
        button_frame.pack(pady=15)
        
        # 확인 버튼
        ttk.Button(button_frame, text="확인", 
                  command=result_window.destroy).pack()
    
    def docs_extraction_finished(self):
        """구글 닥스 추출 완료 후 처리"""
        self.is_running = False
        self.docs_button.config(state='normal', text='구글닥스 추출')
        self.progress_bar.stop()
    
    def on_days_filter_change(self):
        """기간 필터 체크박스 변경 시 입력창 활성화/비활성화"""
        if self.use_days_filter_var.get():
            self.days_threshold_entry.config(state='normal')
        else:
            self.days_threshold_entry.config(state='disabled')

    def on_channel_filter_change(self, *args):
        """채널 필터 조건 변경 시 채널 수 업데이트 (2초 디바운스)"""
        # 기존 타이머 취소
        if self.channel_count_debounce_timer is not None:
            self.root.after_cancel(self.channel_count_debounce_timer)

        # 2초 후 업데이트
        self.channel_count_debounce_timer = self.root.after(2000, self._update_matching_channel_count)

    def _update_matching_channel_count(self):
        """조건에 맞는 채널 수 계산 및 표시"""
        try:
            # 스프레드시트 URL 확인
            if not hasattr(self, 'current_sheet_url') or not self.current_sheet_url:
                self.matching_channels_label.config(text="")
                return

            url = self.current_sheet_url

            # 영상 시트 선택 확인
            video_sheet_name = self.video_sheet_var.get()
            if not video_sheet_name:
                self.matching_channels_label.config(text="")
                return

            # 기간 필터 사용 여부 확인
            if not self.use_days_filter_var.get():
                self.matching_channels_label.config(text="(전체 채널 업데이트)")
                return

            # 기간 필터 값 검증
            try:
                days_threshold = int(self.days_threshold_var.get())
                if days_threshold <= 0:
                    self.matching_channels_label.config(text="")
                    return
            except:
                self.matching_channels_label.config(text="")
                return

            # 시트 데이터 읽기 (백그라운드에서)
            from datetime import datetime

            # 구글 시트 인증 확인
            if not self.sheets_manager.client:
                self.sheets_manager.authenticate()

            spreadsheet = self.sheets_manager.client.open_by_url(url)
            channel_sheet = spreadsheet.worksheet("채널 리스트")
            video_sheet = spreadsheet.worksheet(video_sheet_name)

            # 헤더 읽기
            channel_headers = channel_sheet.row_values(9)
            video_headers = video_sheet.row_values(9)

            # 수집날짜 열 찾기
            channel_date_col = None
            video_date_col = None

            for i, h in enumerate(channel_headers, 1):
                if '수집날짜' in str(h):
                    channel_date_col = i
                    break

            for i, h in enumerate(video_headers, 1):
                if '수집날짜' in str(h):
                    video_date_col = i
                    break

            if not channel_date_col or not video_date_col:
                self.matching_channels_label.config(text="(수집날짜 열 없음)")
                return

            # 데이터 읽기
            channel_data = self.sheets_manager.get_all_values_unformatted(channel_sheet)
            video_data = self.sheets_manager.get_all_values_unformatted(video_sheet)

            # 채널 ID 열 찾기
            channel_id_col = None
            video_channel_id_col = None

            for i, h in enumerate(channel_headers, 1):
                clean_h = re.sub(r'^\d+[\.\s]*', '', str(h)).strip()
                if clean_h == '채널 ID':
                    channel_id_col = i
                    break

            for i, h in enumerate(video_headers, 1):
                clean_h = re.sub(r'^\d+[\.\s]*', '', str(h)).strip()
                if clean_h == '채널 ID':
                    video_channel_id_col = i
                    break

            if not channel_id_col or not video_channel_id_col:
                self.matching_channels_label.config(text="(채널 ID 열 없음)")
                return

            # 영상 데이터 인덱싱
            from collections import defaultdict
            video_by_channel = defaultdict(list)

            for vid_row in video_data[9:]:  # 10행부터
                if len(vid_row) < max(video_channel_id_col, video_date_col):
                    continue

                vid_channel_id = str(vid_row[video_channel_id_col - 1]).strip() if len(vid_row) >= video_channel_id_col else ''
                vid_date_raw = vid_row[video_date_col - 1] if len(vid_row) >= video_date_col else None
                vid_date = str(vid_date_raw).strip() if vid_date_raw else None

                if vid_channel_id and vid_date:
                    video_by_channel[vid_channel_id].append(vid_date)

            # 조건에 맞는 채널 수 계산
            matching_count = 0
            total_count = 0

            for ch_row in channel_data[9:]:  # 10행부터
                if len(ch_row) < 2:
                    continue

                # "가져왔는지 여부" 체크
                brought_status = str(ch_row[1]).strip()
                if not brought_status:
                    continue

                # 채널 ID 가져오기
                if len(ch_row) < channel_id_col:
                    continue
                channel_id = str(ch_row[channel_id_col - 1]).strip()
                if not channel_id:
                    continue

                total_count += 1

                # 해당 채널의 영상이 있는지 확인
                if channel_id not in video_by_channel:
                    continue

                # 채널 리스트의 수집날짜
                ch_date_raw = ch_row[channel_date_col - 1] if len(ch_row) >= channel_date_col else None
                ch_date = str(ch_date_raw).strip() if ch_date_raw else None

                if not ch_date:
                    continue

                # 영상 시트의 최신 수집날짜
                matching_videos = video_by_channel[channel_id]
                latest_video_date = max(matching_videos, key=lambda x: str(x).strip())

                # 날짜 차이 계산
                try:
                    date1 = datetime.strptime(str(ch_date), '%Y-%m-%d')
                    date2 = datetime.strptime(str(latest_video_date), '%Y-%m-%d')
                    days_diff = abs((date2 - date1).days)

                    if days_diff >= days_threshold:
                        matching_count += 1
                except:
                    continue

            # 결과 표시
            self.matching_channels_label.config(text=f"(조건 해당: {matching_count}/{total_count}개 채널)")

        except Exception as e:
            logger.error(f"채널 수 계산 오류: {str(e)}")
            self.matching_channels_label.config(text="(계산 오류)")

    def update_channel_list_from_videos(self):
        """채널 리스트를 영상 시트로 업데이트"""
        if self.is_running:
            messagebox.showwarning("경고", "다른 작업이 실행 중입니다.")
            return

        if not self.video_sheet_var.get():
            messagebox.showerror("오류", "영상 시트를 선택해주세요.")
            return

        # 기간 필터 값 검증
        days_threshold = None
        if self.use_days_filter_var.get():
            try:
                days_threshold = int(self.days_threshold_var.get())
                if days_threshold <= 0:
                    raise ValueError("1 이상의 숫자를 입력해주세요.")
            except ValueError as e:
                messagebox.showerror("오류", f"기간 필터 값이 올바르지 않습니다:\n{e}")
                return

        # 확인 대화상자
        video_sheet = self.video_sheet_var.get()
        filter_msg = f"\n• 기간 필터: {days_threshold}일 이상 차이나는 채널만 업데이트" if days_threshold else ""

        if not messagebox.askyesno("채널-영상 양방향 동기화",
                                  f"채널 리스트 ↔ '{video_sheet}' 양방향 동기화를 시작하시겠습니까?\n\n"
                                  f"[기본 규칙]\n"
                                  f"• 채널 리스트의 '가져왔는지 여부'가 입력된 채널만 처리됩니다.\n"
                                  f"• 수집날짜가 최신인 쪽의 데이터로 상대방을 업데이트합니다.\n"
                                  f"• 업데이트 시 양쪽 시트의 수집날짜도 동일하게 갱신됩니다.\n\n"
                                  f"[업데이트 항목]\n"
                                  f"• 구독자수, 영상갯수, 평균 조회수, 채널국가, 사용언어, 개설일\n\n"
                                  f"[특별 처리]\n"
                                  f"• 분야1, 분야2: 채널 리스트 → 영상 시트 단방향 (값이 다른 경우만)\n"
                                  f"• 9행 수집날짜 열: 양쪽 시트 모두 최신 날짜로 갱신 (헤더/함수 유지){filter_msg}"):
            return

        # 업데이트 실행
        self.is_running = True
        self.channel_video_update_button.config(state='disabled', text='업데이트 중...')
        self.channel_update_progress['value'] = 0
        self.channel_update_status_label.config(text="시작...")
        self.update_progress("채널 리스트 업데이트 중...")
        self.progress_bar.start()

        # 별도 스레드에서 실행
        thread = threading.Thread(target=self._run_channel_list_update, args=(video_sheet, days_threshold))
        thread.daemon = True
        thread.start()

    def _update_channel_progress(self, current, total, message):
        """채널 업데이트 진행률 콜백"""
        def update():
            self.channel_update_progress['value'] = current
            self.channel_update_progress['maximum'] = total
            self.channel_update_status_label.config(text=message)
        self.root.after(0, update)

    def _run_channel_list_update(self, video_sheet_name, days_threshold):
        """실제 채널 리스트 업데이트 작업 수행"""
        try:
            self.root.after(0, lambda: self.update_progress("채널 리스트 업데이트 시작..."))

            # 업데이트 실행
            update_count = self.sheets_manager.update_channel_list_from_video_sheet(
                self.current_sheet_url,
                video_sheet_name,
                days_threshold=days_threshold,
                progress_callback=self._update_channel_progress
            )

            # 완료 메시지
            filter_info = f" (기간 필터: {days_threshold}일 이상)" if days_threshold else ""
            self.root.after(0, lambda: self.update_progress(
                f"✅ 양방향 동기화 완료: {update_count}개 채널 업데이트됨{filter_info}"))
            self.root.after(0, lambda: messagebox.showinfo(
                "완료",
                f"채널 리스트 ↔ 영상 시트 양방향 동기화가 완료되었습니다.\n\n"
                f"• 업데이트된 채널: {update_count}개{filter_info}\n"
                f"• 수집날짜가 최신인 쪽의 데이터로 상대방 갱신\n"
                f"• 양쪽 시트의 수집날짜 동기화 완료\n"
                f"• 분야1/분야2 단방향 업데이트 완료\n"
                f"• 9행 수집날짜 열 갱신 완료 (헤더/함수 유지)"))

        except Exception as e:
            error_msg = str(e)
            logger.error(f"채널 리스트 업데이트 실패: {error_msg}")
            self.root.after(0, lambda: self.update_progress(f"❌ 업데이트 실패: {error_msg}"))
            self.root.after(0, lambda: messagebox.showerror("오류", f"채널 리스트 업데이트 중 오류가 발생했습니다:\n{error_msg}"))

        finally:
            self.is_running = False
            self.root.after(0, lambda: self.channel_video_update_button.config(state='normal', text='채널 리스트 업데이트'))
            self.root.after(0, lambda: self.channel_update_status_label.config(text=""))
            self.root.after(0, lambda: self.progress_bar.stop())

    def update_recent_30_avg_views(self):
        """최근 30개 영상 평균 조회수만 업데이트"""
        if self.is_running:
            messagebox.showwarning("경고", "다른 작업이 실행 중입니다.")
            return

        if not self.video_sheet_var.get():
            messagebox.showerror("오류", "영상 시트를 선택해주세요.")
            return

        # 확인 대화상자
        video_sheet = self.video_sheet_var.get()

        if not messagebox.askyesno("최근 30개 영상 평균 조회수 업데이트",
                                  f"채널 리스트의 '최근 30개 영상 평균 조회수' 열을 업데이트하시겠습니까?\n\n"
                                  f"[처리 대상]\n"
                                  f"• 채널 리스트의 '가져왔는지 여부'가 입력된 모든 채널\n"
                                  f"• '{video_sheet}' 시트의 '영상 업로드날짜'와 '조회수' 데이터 사용\n\n"
                                  f"[계산 방식]\n"
                                  f"• 업로드날짜 기준 최근 30개 영상의 조회수 평균\n"
                                  f"• 30개 미만인 경우 전체 영상 평균으로 계산\n\n"
                                  f"[주의사항]\n"
                                  f"• 영상 시트에 '영상 업로드날짜' 열이 없으면 계산 불가"):
            return

        # 업데이트 실행
        self.is_running = True
        self.update_recent_30_views_button.config(state='disabled', text='업데이트 중...')
        self.channel_update_progress['value'] = 0
        self.channel_update_status_label.config(text="시작...")
        self.update_progress("최근 30개 영상 평균 조회수 업데이트 중...")
        self.progress_bar.start()

        # 별도 스레드에서 실행
        thread = threading.Thread(target=self._run_recent_30_views_update, args=(video_sheet,))
        thread.daemon = True
        thread.start()

    def _run_recent_30_views_update(self, video_sheet_name):
        """최근 30개 영상 평균 조회수 업데이트 작업 수행"""
        try:
            self.root.after(0, lambda: self.update_progress("최근 30개 영상 평균 조회수 업데이트 시작..."))

            # 업데이트 실행
            result_msg = self.sheets_manager.update_recent_30_avg_views_only(
                self.current_sheet_url,
                video_sheet_name,
                progress_callback=self._update_channel_progress
            )

            # 성공 메시지
            self.root.after(0, lambda: self.update_progress(result_msg))
            self.root.after(0, lambda: messagebox.showinfo("완료", result_msg))

        except Exception as e:
            error_msg = str(e)
            logger.error(f"최근 30개 영상 평균 조회수 업데이트 실패: {error_msg}")
            self.root.after(0, lambda: self.update_progress(f"❌ 업데이트 실패: {error_msg}"))
            self.root.after(0, lambda: messagebox.showerror("오류", f"최근 30개 영상 평균 조회수 업데이트 중 오류가 발생했습니다:\n{error_msg}"))

        finally:
            self.is_running = False
            self.root.after(0, lambda: self.update_recent_30_views_button.config(state='normal', text='최근 30개 영상 평균 조회수 업데이트'))
            self.root.after(0, lambda: self.channel_update_status_label.config(text=""))
            self.root.after(0, lambda: self.progress_bar.stop())

    def update_video_sheet_categories(self):
        """선택 시트의 분야1/분야2를 채널 리스트 기준으로 업데이트"""
        if self.is_running:
            messagebox.showwarning("경고", "다른 작업이 실행 중입니다.")
            return

        if not self.video_sheet_var.get():
            messagebox.showerror("오류", "영상 시트를 선택해주세요.")
            return

        # 확인 대화상자
        video_sheet = self.video_sheet_var.get()

        if not messagebox.askyesno("분야1/분야2 업데이트",
                                  f"'{video_sheet}' 시트의 분야1, 분야2를 채널 리스트 기준으로 업데이트하시겠습니까?\n\n"
                                  f"[처리 대상]\n"
                                  f"• 채널 리스트의 '가져왔는지 여부'가 입력된 채널만 처리\n"
                                  f"• 채널명이 일치하는 모든 영상 행\n\n"
                                  f"[업데이트 조건]\n"
                                  f"• 분야1, 분야2 값이 다른 경우만 업데이트\n"
                                  f"• 채널 리스트의 분야1, 분야2가 우선"):
            return

        # 업데이트 실행
        self.is_running = True
        self.update_video_sheet_category_button.config(state='disabled', text='업데이트 중...')
        self.channel_update_progress['value'] = 0
        self.channel_update_status_label.config(text="시작...")
        self.update_progress("분야1/분야2 업데이트 중...")
        self.progress_bar.start()

        # 별도 스레드에서 실행
        thread = threading.Thread(target=self._run_video_sheet_category_update, args=(video_sheet,))
        thread.daemon = True
        thread.start()

    def update_benchmarking_status(self):
        """선택 시트의 벤치마킹 채널여부를 채널 리스트 기준으로 업데이트"""
        if self.is_running:
            messagebox.showwarning("경고", "다른 작업이 실행 중입니다.")
            return

        if not self.video_sheet_var.get():
            messagebox.showerror("오류", "영상 시트를 선택해주세요.")
            return

        # 확인 대화상자
        video_sheet = self.video_sheet_var.get()

        if not messagebox.askyesno("벤치마킹 채널여부 업데이트",
                                  f"'{video_sheet}' 시트의 벤치마킹 채널여부를 채널 리스트 기준으로 업데이트하시겠습니까?\n\n"
                                  f"[처리 대상]\n"
                                  f"• 채널 리스트의 '가져왔는지 여부'가 입력된 채널만 처리\n"
                                  f"• 채널명이 일치하는 모든 영상 행\n\n"
                                  f"[업데이트 조건]\n"
                                  f"• 벤치마킹 채널여부 값이 다른 경우만 업데이트\n"
                                  f"• 채널 리스트의 벤치마킹 채널여부가 우선"):
            return

        # 업데이트 실행
        self.is_running = True
        self.update_benchmarking_status_button.config(state='disabled', text='업데이트 중...')
        self.channel_update_progress['value'] = 0
        self.channel_update_status_label.config(text="시작...")
        self.update_progress("벤치마킹 채널여부 업데이트 중...")
        self.progress_bar.start()

        # 별도 스레드에서 실행
        thread = threading.Thread(target=self._run_benchmarking_status_update, args=(video_sheet,))
        thread.daemon = True
        thread.start()

    def delete_deleted_channels_videos(self):
        """삭제된 채널의 영상을 선택된 시트에서 행 삭제"""
        if self.is_running:
            messagebox.showwarning("경고", "다른 작업이 실행 중입니다.")
            return

        if not self.video_sheet_var.get():
            messagebox.showerror("오류", "영상 시트를 선택해주세요.")
            return

        # 확인 대화상자
        video_sheet = self.video_sheet_var.get()

        if not messagebox.askyesno("삭제된 채널 영상 삭제",
                                  f"⚠️ 경고: 이 작업은 복구할 수 없습니다!\n\n"
                                  f"'{video_sheet}' 시트에서 삭제된 채널의 모든 영상을 행 자체를 삭제하시겠습니까?\n\n"
                                  f"[삭제 대상]\n"
                                  f"• 채널 리스트의 '가져올 채널'='x'인 채널의 모든 영상\n"
                                  f"• 채널명이 일치하는 모든 영상 행이 완전히 삭제됩니다\n\n"
                                  f"[주의사항]\n"
                                  f"• 행이 완전히 삭제되므로 복구가 불가능합니다\n"
                                  f"• 삭제 전 반드시 백업을 권장합니다",
                                  icon='warning'):
            return

        # 한 번 더 확인
        if not messagebox.askyesno("최종 확인",
                                  "정말로 삭제하시겠습니까?\n\n"
                                  "이 작업은 되돌릴 수 없습니다.",
                                  icon='warning'):
            return

        # 삭제 실행
        self.is_running = True
        self.delete_deleted_channels_videos_button.config(state='disabled', text='삭제 중...')
        self.channel_update_progress['value'] = 0
        self.channel_update_status_label.config(text="시작...")
        self.update_progress("삭제된 채널 영상 삭제 중...")
        self.progress_bar.start()

        # 별도 스레드에서 실행
        thread = threading.Thread(target=self._run_delete_deleted_channels_videos, args=(video_sheet,))
        thread.daemon = True
        thread.start()

    def _run_delete_deleted_channels_videos(self, video_sheet_name):
        """실제 삭제된 채널 영상 삭제 작업 수행"""
        try:
            self.root.after(0, lambda: self.update_progress("삭제된 채널 영상 삭제 시작..."))

            # 삭제 실행
            deleted_count = self.sheets_manager.delete_deleted_channels_videos_from_sheet(
                self.current_sheet_url,
                video_sheet_name,
                progress_callback=self._update_channel_progress
            )

            # 완료 메시지
            self.root.after(0, lambda: self.update_progress(
                f"✅ 삭제 완료: {deleted_count}개 행 삭제됨"))
            self.root.after(0, lambda: messagebox.showinfo(
                "완료",
                f"삭제된 채널의 영상 삭제가 완료되었습니다.\n\n"
                f"• 삭제된 행: {deleted_count}개\n"
                f"• 시트: '{video_sheet_name}'"))

        except Exception as e:
            error_msg = str(e)
            logger.error(f"삭제된 채널 영상 삭제 실패: {error_msg}")
            self.root.after(0, lambda: self.update_progress(f"❌ 삭제 실패: {error_msg}"))
            self.root.after(0, lambda: messagebox.showerror("오류", f"삭제된 채널 영상 삭제 중 오류가 발생했습니다:\n{error_msg}"))

        finally:
            self.is_running = False
            self.root.after(0, lambda: self.delete_deleted_channels_videos_button.config(state='normal', text='삭제된 채널 영상삭제'))
            self.root.after(0, lambda: self.channel_update_status_label.config(text=""))
            self.root.after(0, lambda: self.progress_bar.stop())

    def copy_transcript_to_target(self):
        """원본 시트에서 타겟 시트로 대본 관련 데이터 복사"""
        if self.is_running:
            messagebox.showwarning("경고", "다른 작업이 실행 중입니다.")
            return

        source_sheet = self.source_sheet_var.get()
        target_sheet = self.target_sheet_var.get()

        if not source_sheet or not target_sheet:
            messagebox.showerror("오류", "원본 시트와 타겟 시트를 모두 선택해주세요.")
            return

        if source_sheet == target_sheet:
            messagebox.showerror("오류", "원본 시트와 타겟 시트가 같을 수 없습니다.")
            return

        # 확인 대화상자
        if not messagebox.askyesno("대본 데이터 복사",
                                  f"'{source_sheet}' → '{target_sheet}' 대본 데이터 복사를 시작하시겠습니까?\n\n"
                                  f"[복사 대상 헤더]\n"
                                  f"• 대본내용, 분석, 대본파일\n"
                                  f"• 썸네일 여부, 썸네일 이미지주소, 썸네일 경로\n\n"
                                  f"[복사 조건]\n"
                                  f"• 영상 ID가 일치하는 행만 처리\n"
                                  f"• 원본에 값이 있고, 타겟이 비어있는 경우만 복사"):
            return

        # 업데이트 실행
        self.is_running = True
        self.transcript_copy_button.config(state='disabled', text='복사 중...')
        self.transcript_copy_progress['value'] = 0
        self.transcript_copy_status_label.config(text="시작...")
        self.update_progress("대본 데이터 복사 중...")
        self.progress_bar.start()

        # 별도 스레드에서 실행
        thread = threading.Thread(target=self._run_transcript_copy, args=(source_sheet, target_sheet))
        thread.daemon = True
        thread.start()

    def _update_transcript_copy_progress(self, current, total, message):
        """대본 복사 진행률 콜백"""
        def update():
            self.transcript_copy_progress['value'] = current
            self.transcript_copy_progress['maximum'] = total
            self.transcript_copy_status_label.config(text=message)
        self.root.after(0, update)

    def _run_transcript_copy(self, source_sheet_name, target_sheet_name):
        """실제 대본 데이터 복사 작업 수행"""
        try:
            self.root.after(0, lambda: self.update_progress("대본 데이터 복사 시작..."))

            # 복사 실행
            update_count = self.sheets_manager.copy_transcript_data_between_sheets(
                self.current_sheet_url,
                source_sheet_name,
                target_sheet_name,
                progress_callback=self._update_transcript_copy_progress
            )

            # 완료 메시지
            self.root.after(0, lambda: self.update_progress("✅ 대본 데이터 복사 완료!"))
            self.root.after(0, lambda: messagebox.showinfo(
                "완료",
                f"'{source_sheet_name}' → '{target_sheet_name}' 대본 데이터 복사가 완료되었습니다.\n\n"
                f"• 복사된 영상: {update_count}개\n"
                f"• 대본내용, 분석, 대본파일, 썸네일 정보 복사 완료"))

        except Exception as e:
            error_msg = str(e)
            logger.error(f"대본 데이터 복사 실패: {error_msg}")
            self.root.after(0, lambda: self.update_progress(f"❌ 복사 실패: {error_msg}"))
            self.root.after(0, lambda: messagebox.showerror("오류", f"대본 데이터 복사 중 오류가 발생했습니다:\n{error_msg}"))

        finally:
            self.is_running = False
            self.root.after(0, lambda: self.transcript_copy_button.config(state='normal', text='타겟시트로 대본,썸네일경로 복사'))
            self.root.after(0, lambda: self.transcript_copy_status_label.config(text=""))
            self.root.after(0, lambda: self.progress_bar.stop())

    def add_new_videos_to_target(self):
        """원본 시트에서 타겟 시트로 신규 영상 추가"""
        if self.is_running:
            messagebox.showwarning("경고", "다른 작업이 실행 중입니다.")
            return

        source_sheet = self.video_add_source_sheet_var.get()
        target_sheet = self.video_add_target_sheet_var.get()

        if not source_sheet or not target_sheet:
            messagebox.showerror("오류", "원본 시트와 타겟 시트를 모두 선택해주세요.")
            return

        if source_sheet == target_sheet:
            messagebox.showerror("오류", "원본 시트와 타겟 시트가 같을 수 없습니다.")
            return

        # 확인 대화상자
        if not messagebox.askyesno("신규 영상 추가",
                                  f"'{source_sheet}' → '{target_sheet}' 신규 영상 추가를 시작하시겠습니까?\n\n"
                                  f"[작업 내용]\n"
                                  f"• 타겟 시트에 없는 영상 ID를 원본에서 찾아 추가\n"
                                  f"• 중복되지 않은 영상만 추가됩니다"):
            return

        # 업데이트 실행
        self.is_running = True
        self.add_new_videos_button.config(state='disabled', text='추가 중...')
        self.video_add_progress['value'] = 0
        self.video_add_status_label.config(text="시작...")
        self.update_progress("신규 영상 추가 중...")
        self.progress_bar.start()

        # 별도 스레드에서 실행
        thread = threading.Thread(target=self._run_add_new_videos, args=(source_sheet, target_sheet))
        thread.daemon = True
        thread.start()

    def update_videos_by_collect_date(self):
        """수집날짜 기준으로 타겟 시트의 기존 영상 업데이트"""
        if self.is_running:
            messagebox.showwarning("경고", "다른 작업이 실행 중입니다.")
            return

        source_sheet = self.video_add_source_sheet_var.get()
        target_sheet = self.video_add_target_sheet_var.get()

        if not source_sheet or not target_sheet:
            messagebox.showerror("오류", "원본 시트와 타겟 시트를 모두 선택해주세요.")
            return

        if source_sheet == target_sheet:
            messagebox.showerror("오류", "원본 시트와 타겟 시트가 같을 수 없습니다.")
            return

        # 확인 대화상자
        if not messagebox.askyesno("기존 영상 업데이트",
                                  f"'{source_sheet}' → '{target_sheet}' 기존 영상 업데이트를 시작하시겠습니까?\n\n"
                                  f"[작업 내용]\n"
                                  f"• 영상 ID가 일치하는 행 중 수집날짜가 더 최신인 것만 업데이트\n"
                                  f"• 대본/썸네일 데이터는 조건부 보존 (원본이 비어있으면 타겟 유지)"):
            return

        # 업데이트 실행
        self.is_running = True
        self.update_videos_by_date_button.config(state='disabled', text='업데이트 중...')
        self.video_add_progress['value'] = 0
        self.video_add_status_label.config(text="시작...")
        self.update_progress("기존 영상 업데이트 중...")
        self.progress_bar.start()

        # 별도 스레드에서 실행
        thread = threading.Thread(target=self._run_update_videos_by_date, args=(source_sheet, target_sheet))
        thread.daemon = True
        thread.start()

    def _update_video_add_progress(self, current, total, message):
        """영상 추가/업데이트 진행률 콜백"""
        def update():
            self.video_add_progress['value'] = current
            self.video_add_progress['maximum'] = total
            self.video_add_status_label.config(text=message)
        self.root.after(0, update)

    def _run_add_new_videos(self, source_sheet_name, target_sheet_name):
        """실제 신규 영상 추가 작업 수행"""
        try:
            self.root.after(0, lambda: self.update_progress("신규 영상 추가 시작..."))

            # 신규 영상 추가 실행
            add_count = self.sheets_manager.add_new_videos_to_target_sheet(
                self.current_sheet_url,
                source_sheet_name,
                target_sheet_name,
                progress_callback=self._update_video_add_progress
            )

            # 완료 메시지
            self.root.after(0, lambda: self.update_progress("✅ 신규 영상 추가 완료!"))
            self.root.after(0, lambda: messagebox.showinfo(
                "완료",
                f"'{source_sheet_name}' → '{target_sheet_name}' 신규 영상 추가가 완료되었습니다.\n\n"
                f"• 추가된 영상: {add_count}개\n"
                f"• 타겟 시트에 중복되지 않는 영상만 추가됨"))

        except Exception as e:
            error_msg = str(e)
            logger.error(f"신규 영상 추가 실패: {error_msg}")
            self.root.after(0, lambda: self.update_progress(f"❌ 추가 실패: {error_msg}"))
            self.root.after(0, lambda: messagebox.showerror("오류", f"신규 영상 추가 중 오류가 발생했습니다:\n{error_msg}"))

        finally:
            self.is_running = False
            self.root.after(0, lambda: self.add_new_videos_button.config(state='normal', text='타겟시트에 신규영상 추가'))
            self.root.after(0, lambda: self.video_add_status_label.config(text=""))
            self.root.after(0, lambda: self.progress_bar.stop())

    def _run_update_videos_by_date(self, source_sheet_name, target_sheet_name):
        """실제 기존 영상 업데이트 작업 수행"""
        try:
            self.root.after(0, lambda: self.update_progress("기존 영상 업데이트 시작..."))

            # 기존 영상 업데이트 실행
            update_count = self.sheets_manager.update_existing_videos_by_collect_date(
                self.current_sheet_url,
                source_sheet_name,
                target_sheet_name,
                progress_callback=self._update_video_add_progress
            )

            # 완료 메시지
            self.root.after(0, lambda: self.update_progress("✅ 기존 영상 업데이트 완료!"))
            self.root.after(0, lambda: messagebox.showinfo(
                "완료",
                f"'{source_sheet_name}' → '{target_sheet_name}' 기존 영상 업데이트가 완료되었습니다.\n\n"
                f"• 업데이트된 영상: {update_count}개\n"
                f"• 수집날짜가 더 최신인 영상만 업데이트\n"
                f"• 대본/썸네일 데이터 조건부 보존"))

        except Exception as e:
            error_msg = str(e)
            logger.error(f"기존 영상 업데이트 실패: {error_msg}")
            self.root.after(0, lambda: self.update_progress(f"❌ 업데이트 실패: {error_msg}"))
            self.root.after(0, lambda: messagebox.showerror("오류", f"기존 영상 업데이트 중 오류가 발생했습니다:\n{error_msg}"))

        finally:
            self.is_running = False
            self.root.after(0, lambda: self.update_videos_by_date_button.config(state='normal', text='타겟시트 기존영상 수집날짜 최신기준 업데이트'))
            self.root.after(0, lambda: self.video_add_status_label.config(text=""))
            self.root.after(0, lambda: self.progress_bar.stop())

    def _run_video_sheet_category_update(self, video_sheet_name):
        """실제 분야1/분야2 업데이트 작업 수행"""
        try:
            self.root.after(0, lambda: self.update_progress("분야1/분야2 업데이트 시작..."))

            # 업데이트 실행
            update_count = self.sheets_manager.update_video_sheet_categories_from_channel_list(
                self.current_sheet_url,
                video_sheet_name,
                progress_callback=self._update_channel_progress
            )

            # 완료 메시지
            self.root.after(0, lambda: self.update_progress("✅ 분야1/분야2 업데이트 완료!"))
            self.root.after(0, lambda: messagebox.showinfo(
                "완료",
                f"'{video_sheet_name}' 시트의 분야1/분야2 업데이트가 완료되었습니다.\n\n"
                f"• 업데이트된 영상: {update_count}개\n"
                f"• 채널 리스트 기준으로 분야1/분야2 갱신 완료"))

        except Exception as e:
            error_msg = str(e)
            logger.error(f"분야1/분야2 업데이트 실패: {error_msg}")
            self.root.after(0, lambda: self.update_progress(f"❌ 업데이트 실패: {error_msg}"))
            self.root.after(0, lambda: messagebox.showerror("오류", f"분야1/분야2 업데이트 중 오류가 발생했습니다:\n{error_msg}"))

        finally:
            self.is_running = False
            self.root.after(0, lambda: self.update_video_sheet_category_button.config(state='normal', text='선택시트 분야1,분야2 업데이트'))
            self.root.after(0, lambda: self.channel_update_status_label.config(text=""))
            self.root.after(0, lambda: self.progress_bar.stop())

    def _run_benchmarking_status_update(self, video_sheet_name):
        """실제 벤치마킹 채널여부 업데이트 작업 수행"""
        try:
            self.root.after(0, lambda: self.update_progress("벤치마킹 채널여부 업데이트 시작..."))

            # 업데이트 실행
            update_count = self.sheets_manager.update_benchmarking_status_from_channel_list(
                self.current_sheet_url,
                video_sheet_name,
                progress_callback=self._update_channel_progress
            )

            # 완료 메시지
            self.root.after(0, lambda: self.update_progress("✅ 벤치마킹 채널여부 업데이트 완료!"))
            self.root.after(0, lambda: messagebox.showinfo(
                "완료",
                f"'{video_sheet_name}' 시트의 벤치마킹 채널여부 업데이트가 완료되었습니다.\n\n"
                f"• 업데이트된 영상: {update_count}개\n"
                f"• 채널 리스트 기준으로 벤치마킹 채널여부 갱신 완료"))

        except Exception as e:
            error_msg = str(e)
            logger.error(f"벤치마킹 채널여부 업데이트 실패: {error_msg}")
            self.root.after(0, lambda: self.update_progress(f"❌ 업데이트 실패: {error_msg}"))
            self.root.after(0, lambda: messagebox.showerror("오류", f"벤치마킹 채널여부 업데이트 중 오류가 발생했습니다:\n{error_msg}"))

        finally:
            self.is_running = False
            self.root.after(0, lambda: self.update_benchmarking_status_button.config(state='normal', text='벤치마킹 채널여부 업데이트'))
            self.root.after(0, lambda: self.channel_update_status_label.config(text=""))
            self.root.after(0, lambda: self.progress_bar.stop())

    def start_summary_copy(self):
        """시트 요약 복제 시작"""
        if self.is_running:
            messagebox.showwarning("경고", "다른 작업이 실행 중입니다.")
            return

        if not self.sheet_var.get():
            messagebox.showerror("오류", "시트를 선택해주세요.")
            return
        
        # 복제할 행 수 확인
        try:
            if not self.copy_all_videos_var.get():
                row_count = int(self.summary_count_var.get())
                if row_count <= 0:
                    raise ValueError("1 이상의 숫자를 입력해주세요.")
            else:
                row_count = 0  # 전체 복제 시 사용하지 않음
        except ValueError as e:
            messagebox.showerror("오류", f"올바른 숫자를 입력해주세요:\n{e}")
            return
        
        # 확인 대화상자
        selected_sheet = self.sheet_var.get()
        copy_mode = "전체 영상(A열 기준 마지막 데이터까지)" if self.copy_all_videos_var.get() else f"{row_count}개 영상(10행부터)"
        
        if not messagebox.askyesno("시트 요약 복제", 
                                  f"'{selected_sheet}' 시트를 복제하시겠습니까?\n\n"
                                  f"복제 범위: {copy_mode}\n"
                                  f"복제될 시트: '{selected_sheet}_추출01' 형태\n\n"
                                  f"특정 헤더(16개 열)만 복제됩니다."):
            return
        
        # 복제 실행
        self.is_running = True
        self.summary_button.config(state='disabled', text='복제 중...')
        self.update_progress("시트 요약 복제 중...")
        self.progress_bar.start()
        
        # 별도 스레드에서 실행
        thread = threading.Thread(target=self.run_summary_copy, args=(row_count,))
        thread.daemon = True
        thread.start()
    
    def run_summary_copy(self, row_count: int):
        """실제 시트 요약 복제 작업 수행"""
        try:
            self.root.after(0, lambda: self.update_progress("구글 시트 인증 중..."))
            
            # 구글 시트 인증 (아직 인증되지 않은 경우)
            if not self.sheets_manager.client:
                self.sheets_manager.authenticate()
            
            selected_sheet_name = self.sheet_var.get()
            copy_all = self.copy_all_videos_var.get()
            
            self.root.after(0, lambda: self.update_progress("시트 복제 중..."))
            
            # 시트 복제 실행
            copy_name = self.sheets_manager.copy_summary_sheet(
                self.current_sheet_url,
                selected_sheet_name,
                row_count,
                copy_all
            )
            
            # 성공 메시지
            copy_mode = "전체 영상" if copy_all else f"{row_count}개 영상"
            self.root.after(0, lambda: messagebox.showinfo("복제 완료", 
                                                          f"시트 복제가 완료되었습니다!\n\n"
                                                          f"원본: {selected_sheet_name}\n"
                                                          f"복제본: {copy_name}\n"
                                                          f"복제 범위: {copy_mode}\n\n"
                                                          f"복제된 시트는 시트 목록 맨 뒤에 배치되었습니다."))
            self.root.after(0, lambda: self.update_progress("시트 복제 완료"))
            
        except Exception as e:
            error_msg = str(e)
            logger.exception("시트 복제 중 오류 발생:")
            self.root.after(0, lambda: messagebox.showerror("복제 실패", 
                                                           f"시트 복제 중 오류가 발생했습니다:\n{error_msg}"))
            self.root.after(0, lambda: self.update_progress("시트 복제 실패"))
        finally:
            self.root.after(0, self.summary_copy_finished)
    
    def summary_copy_finished(self):
        """시트 복제 완료 후 처리"""
        self.is_running = False
        self.summary_button.config(state='normal', text='시트 요약 복제')
        self.progress_bar.stop()
    
    def load_filter_data(self):
        """시트가 변경될 때 필터 데이터 로드"""
        try:
            if not self.current_sheet_url or not hasattr(self, 'sheet_var') or not self.sheet_var.get():
                return
            
            logger.info(f"🔄 필터 데이터 로드 시작: {self.sheet_var.get()} 시트")
            
            # 현재 워크시트 가져오기
            try:
                spreadsheet = self.sheets_manager.client.open_by_url(self.current_sheet_url)
                self.current_worksheet = spreadsheet.worksheet(self.sheet_var.get())
                
                # 각 필터 필드의 고유값 가져오기
                field1_values = self.sheets_manager.get_column_unique_values(self.current_worksheet, '분야1')
                field2_values = self.sheets_manager.get_column_unique_values(self.current_worksheet, '분야2') 
                shortform_values = self.sheets_manager.get_column_unique_values(self.current_worksheet, '숏폼여부')
                
                # 체크박스 업데이트
                self.update_filter_checkboxes('field1', field1_values, self.field1_frame, self.field1_checkboxes)
                self.update_filter_checkboxes('field2', field2_values, self.field2_frame, self.field2_checkboxes)
                self.update_filter_checkboxes('shortform', shortform_values, self.shortform_frame, self.shortform_checkboxes)
                
                logger.info("✅ 필터 데이터 로드 완료")
                
            except Exception as e:
                logger.error(f"워크시트 접근 실패: {str(e)}")
                self.current_worksheet = None
            
        except Exception as e:
            logger.error(f"필터 데이터 로드 중 오류: {str(e)}")
    
    def load_filter_data_manual(self):
        """시트 데이터 불러오기 버튼 클릭시 호출되는 메서드"""
        try:
            if not self.current_sheet_url or not hasattr(self, 'sheet_var') or not self.sheet_var.get():
                messagebox.showwarning("경고", "먼저 스프레드시트와 시트를 선택해주세요.")
                return
            
            logger.info(f"🔄 수동 필터 데이터 로드 시작: {self.sheet_var.get()} 시트")

            # 새 시트를 로드하므로 캐시 초기화 (API 호출 최소화를 위해 캐시 클리어)
            if hasattr(self.sheets_manager, '_header_cache'):
                self.sheets_manager._header_cache = {}
                logger.info("헤더 캐시 초기화")
            if hasattr(self.sheets_manager, '_data_cache'):
                self.sheets_manager._data_cache = {}
                logger.info("데이터 캐시 초기화")

            # 현재 워크시트 가져오기
            try:
                spreadsheet = self.sheets_manager.client.open_by_url(self.current_sheet_url)
                self.current_worksheet = spreadsheet.worksheet(self.sheet_var.get())

                # 시트 이름 업데이트
                sheet_name = self.sheet_var.get()
                self.filter_sheet_name_var.set(sheet_name)

                # A열의 데이터 개수 계산 (10행부터 마지막까지)
                all_data = self.current_worksheet.get_all_values()
                data_count = 0
                if len(all_data) > 9:  # 10행 이상 데이터가 있는 경우
                    for row in all_data[9:]:  # 10행부터 시작 (인덱스 9)
                        if row and len(row) > 0 and row[0].strip():  # A열에 데이터가 있는 경우
                            data_count += 1

                self.filter_data_count_var.set(f"{data_count}개")
                logger.info(f"📊 시트 '{sheet_name}' 데이터 행 개수: {data_count}개")

                # 각 필터 필드의 고유값 가져오기
                field1_values = self.sheets_manager.get_column_unique_values(self.current_worksheet, '분야1')
                field2_values = self.sheets_manager.get_column_unique_values(self.current_worksheet, '분야2')
                shortform_values = self.sheets_manager.get_column_unique_values(self.current_worksheet, '숏폼여부')

                # 분야1-분야2 관계 분석
                self.analyze_field1_field2_relationship()

                # 전체 분야2 값들 저장
                self.all_field2_values = field2_values if field2_values else []

                # 체크박스 업데이트
                self.create_filter_checkboxes('field1', field1_values, self.field1_frame, self.field1_checkboxes)
                self.create_filter_checkboxes('field2', field2_values, self.field2_frame, self.field2_checkboxes)
                self.create_filter_checkboxes('shortform', shortform_values, self.shortform_frame, self.shortform_checkboxes)

                logger.info("✅ 수동 필터 데이터 로드 완료")
                messagebox.showinfo("완료", f"시트 '{self.sheet_var.get()}'의 필터 데이터를 성공적으로 불러왔습니다.\n데이터 행 개수: {data_count}개")
                
            except Exception as e:
                logger.error(f"워크시트 접근 실패: {str(e)}")
                messagebox.showerror("오류", f"워크시트 접근 실패: {str(e)}")
                self.current_worksheet = None
            
        except Exception as e:
            logger.error(f"수동 필터 데이터 로드 중 오류: {str(e)}")
            messagebox.showerror("오류", f"데이터 로드 중 오류가 발생했습니다: {str(e)}")
    
    def analyze_field1_field2_relationship(self):
        """분야1과 분야2의 관계를 분석하여 매핑 데이터 생성"""
        if not hasattr(self, 'current_worksheet') or not self.current_worksheet:
            return
        
        try:
            # 헤더 매핑 가져오기
            column_mapping = self.sheets_manager.get_header_columns(self.current_worksheet)
            if '분야1' not in column_mapping or '분야2' not in column_mapping:
                return
            
            # 모든 데이터 가져오기 (이 경우 헤더 분석용이므로 formatted value 사용 가능)
            all_data = self.current_worksheet.get_all_values()

            # 분야1-분야2 매핑 초기화
            self.field1_field2_mapping = {}
            
            field1_col_idx = column_mapping['분야1'] - 1
            field2_col_idx = column_mapping['분야2'] - 1
            
            # 데이터 행 분석 (10행부터)
            for row_idx, row_data in enumerate(all_data[9:], 10):  # 10행부터 시작
                if len(row_data) > max(field1_col_idx, field2_col_idx):
                    field1_value = str(row_data[field1_col_idx]).strip()
                    field2_value = str(row_data[field2_col_idx]).strip()
                    
                    if field1_value and field2_value:
                        # 쉼표로 구분된 값들 처리
                        field1_values = [val.strip() for val in field1_value.split(',') if val.strip()]
                        field2_values = [val.strip() for val in field2_value.split(',') if val.strip()]
                        
                        for f1 in field1_values:
                            if f1 not in self.field1_field2_mapping:
                                self.field1_field2_mapping[f1] = set()
                            for f2 in field2_values:
                                self.field1_field2_mapping[f1].add(f2)
            
            # Set을 list로 변환
            for key in self.field1_field2_mapping:
                self.field1_field2_mapping[key] = list(self.field1_field2_mapping[key])
                
            logger.info(f"📊 분야1-분야2 관계 분석 완료: {len(self.field1_field2_mapping)}개 분야1 항목")
                
        except Exception as e:
            logger.error(f"분야1-분야2 관계 분석 실패: {str(e)}")
            self.field1_field2_mapping = {}
    
    def on_field2_show_all_changed(self):
        """분야2 전체보기 체크박스 변경 처리"""
        self.update_field2_filters()
        self.update_real_time_count()
    
    def update_field2_filters(self):
        """분야1 선택에 따른 분야2 필터 업데이트"""
        if not hasattr(self, 'field1_checkboxes') or not self.field1_checkboxes:
            return
        
        # 전체보기가 체크되어 있으면 모든 분야2 값 표시
        if hasattr(self, 'field2_show_all_var') and self.field2_show_all_var.get():
            filtered_field2_values = self.all_field2_values
        else:
            # 선택된 분야1 값들 가져오기
            selected_field1 = self.get_selected_filter_values(self.field1_checkboxes)
            
            if not selected_field1:
                # 아무것도 선택되지 않으면 전체 분야2 값 표시
                filtered_field2_values = self.all_field2_values
            else:
                # 선택된 분야1에 해당하는 분야2 값들만 표시
                filtered_field2_values = set()
                for field1_val in selected_field1:
                    if field1_val in self.field1_field2_mapping:
                        filtered_field2_values.update(self.field1_field2_mapping[field1_val])
                filtered_field2_values = sorted(list(filtered_field2_values))
        
        # 색상 매핑 업데이트 후 분야2 체크박스 업데이트
        self.update_field2_color_mapping()
        self.create_filter_checkboxes('field2', filtered_field2_values, self.field2_frame, self.field2_checkboxes)
    
    def on_field1_changed(self, filter_type):
        """분야1 선택 변경 시 처리"""
        # 무한 루프 방지를 위한 플래그 체크
        if hasattr(self, '_updating_field1') and self._updating_field1:
            return
        
        self._updating_field1 = True
        try:
            self.update_current_filter_display(filter_type)
            self.update_field1_selection_order()
            self.update_field1_display()  # 분야1 순서 번호 표시 업데이트
            self.update_field2_filters()
            self.update_top_n_description()  # 상위 N개 해설 업데이트
            self.update_real_time_count()
        finally:
            self._updating_field1 = False
    
    def on_filter_changed(self, filter_type):
        """일반 필터 선택 변경 시 처리"""
        self.update_current_filter_display(filter_type)
        self.update_top_n_description()  # 상위 N개 해설 업데이트
        self.update_real_time_count()
    
    def update_field1_selection_order(self):
        """분야1 선택 순서 업데이트 및 색상 매핑"""
        if not hasattr(self, 'field1_checkboxes') or not self.field1_checkboxes:
            return
        
        # 현재 선택된 분야1 값들
        selected_field1 = self.get_selected_filter_values(self.field1_checkboxes)
        
        # 새로 선택된 항목들을 순서에 추가
        for field1_val in selected_field1:
            if field1_val not in self.field1_selection_order:
                self.field1_selection_order.append(field1_val)
        
        # 선택 해제된 항목들을 순서에서 제거
        self.field1_selection_order = [val for val in self.field1_selection_order if val in selected_field1]
        
        # 분야2 색상 매핑 업데이트
        self.update_field2_color_mapping()
    
    def update_field2_color_mapping(self):
        """분야2 색상 매핑 업데이트"""
        self.field2_color_mapping.clear()
        
        for i, field1_val in enumerate(self.field1_selection_order):
            if field1_val in self.field1_field2_mapping:
                color = self.field1_colors[i % len(self.field1_colors)]  # 5개 색상 순환
                for field2_val in self.field1_field2_mapping[field1_val]:
                    if field2_val not in self.field2_color_mapping:
                        self.field2_color_mapping[field2_val] = color
    
    def update_field1_display(self):
        """분야1 체크박스 표시 업데이트 (순서 번호 포함)"""
        if not hasattr(self, 'field1_checkboxes') or not hasattr(self, 'all_field2_values'):
            return
        
        # 현재 분야1 값들 가져오기
        if hasattr(self, 'current_worksheet') and self.current_worksheet:
            try:
                field1_values = self.sheets_manager.get_column_unique_values(self.current_worksheet, '분야1')
                # 현재 선택 상태 저장
                current_selections = {}
                for value, var in self.field1_checkboxes.items():
                    current_selections[value] = var.get()
                
                # 분야1 체크박스 재생성
                self.create_filter_checkboxes('field1', field1_values, self.field1_frame, self.field1_checkboxes)
                
                # 선택 상태 복원
                for value, selected in current_selections.items():
                    if value in self.field1_checkboxes:
                        self.field1_checkboxes[value].set(selected)
                        
            except Exception as e:
                logger.error(f"분야1 표시 업데이트 실패: {str(e)}")
    
    def toggle_top_n_section(self):
        """상위 N개 섹션 활성화/비활성화"""
        enabled = self.enable_top_n_var.get()
        state = 'normal' if enabled else 'disabled'
        
        for widget in self.top_n_widgets:
            widget.config(state=state)
            
        if not enabled:
            # 비활성화시 값들 초기화
            self.top_n_var.set("")
            self.top_n_mode_var.set("none")
            self.top_n_description_var.set("상위 N개 추출이 비활성화되어 있습니다")
        else:
            # 활성화시 초기 해설
            self.update_top_n_description()
            
        # 실시간 카운트 업데이트 (matching_count_var가 존재할 때만)
        if hasattr(self, 'matching_count_var'):
            self.update_real_time_count()
    
    def on_top_n_change(self):
        """상위 N개 설정 변경 시 처리"""
        # 분야1별 선택 시 추가 옵션 표시/숨김
        if hasattr(self, 'field1_options_frame') and hasattr(self, 'top_n_mode_var'):
            if self.top_n_mode_var.get() == 'field1':
                self.field1_options_frame.grid()
            else:
                self.field1_options_frame.grid_remove()
                # 분야1별이 아니면 분야2 무시 체크 해제
                if hasattr(self, 'ignore_field2_var'):
                    self.ignore_field2_var.set(False)
        
        self.update_top_n_description()
        if hasattr(self, 'matching_count_var'):
            self.update_real_time_count()

    def on_sort_limit_toggle(self):
        """상위 N개 정렬 추출 활성화/비활성화 토글"""
        enabled = self.enable_sort_limit_var.get()

        # 위젯 활성화/비활성화
        state = 'readonly' if enabled else 'disabled'
        entry_state = 'normal' if enabled else 'disabled'

        self.sort_column_combo.config(state=state)
        self.sort_order_combo.config(state=state)
        self.sort_limit_entry.config(state=entry_state)

        # 조건 변경 트리거
        self.on_numeric_condition_change()

    def on_numeric_condition_change(self):
        """수치 조건 변경 시 처리 (2초 디바운스 적용)"""
        # 기존 타이머가 있으면 취소
        if self.debounce_timer is not None:
            self.root.after_cancel(self.debounce_timer)

        # 상위 N개 조건 해설은 즉시 업데이트 (API 호출 없음)
        self.update_top_n_description()

        # 실시간 카운트는 2초 후에 업데이트 (디바운스)
        self.debounce_timer = self.root.after(
            self.debounce_delay,
            self._debounced_update_real_time_count
        )

    def _debounced_update_real_time_count(self):
        """디바운스된 실시간 카운트 업데이트"""
        self.debounce_timer = None
        self.update_real_time_count()

    def update_top_n_description(self):
        """상위 N개 추출 조건 해설 업데이트"""
        try:
            if not (hasattr(self, 'enable_top_n_var') and self.enable_top_n_var.get()):
                self.top_n_description_var.set("상위 N개 추출이 비활성화되어 있습니다")
                return
            
            # 현재 설정된 값들 가져오기
            field1_filters = []
            field2_filters = []
            shortform_filters = []
            
            if hasattr(self, 'field1_checkboxes'):
                field1_filters = self.get_selected_filter_values(self.field1_checkboxes)
            if hasattr(self, 'field2_checkboxes'):
                field2_filters = self.get_selected_filter_values(self.field2_checkboxes)
            if hasattr(self, 'shortform_checkboxes'):
                shortform_filters = self.get_selected_filter_values(self.shortform_checkboxes)
                
            top_n = self.top_n_var.get().strip()
            top_n_mode = self.top_n_mode_var.get()
            
            # 수치 조건들
            views_condition = self.views_var.get().strip() if hasattr(self, 'views_var') else ""
            subscribers_condition = self.subscribers_var.get().strip() if hasattr(self, 'subscribers_var') else ""
            channel_condition = self.channel_names_var.get().strip() if hasattr(self, 'channel_names_var') else ""
            
            if not top_n or top_n_mode == 'none':
                self.top_n_description_var.set("상위 N개와 추출 모드를 설정해주세요")
                return
            
            # 해설 생성
            description = f"📋 추출 조건: "
            
            # 기본 필터 조건들 설명
            base_conditions = []
            if field1_filters:
                base_conditions.append(f"분야1={', '.join(field1_filters)}")
            if field2_filters:
                base_conditions.append(f"분야2={', '.join(field2_filters)}")
            if shortform_filters:
                base_conditions.append(f"숏폼여부={', '.join(shortform_filters)}")
            if views_condition:
                base_conditions.append(f"조회수 {views_condition}이상")
            if subscribers_condition:
                base_conditions.append(f"구독자 {subscribers_condition}이상")
            if channel_condition:
                base_conditions.append(f"채널명 포함({channel_condition})")
            
            if base_conditions:
                description += " + ".join(base_conditions) + " + "
            
            # 상위 N개 모드별 설명
            mode_descriptions = {}
            if top_n_mode == 'channel':
                mode_descriptions['channel'] = f"각 채널마다 조회수 상위 {top_n}개씩 추출"
            elif top_n_mode == 'field1':
                # 분야2 무시 체크 여부 확인
                ignore_field2 = False
                if hasattr(self, 'ignore_field2_var'):
                    ignore_field2 = self.ignore_field2_var.get()
                
                if ignore_field2:
                    mode_descriptions['field1'] = f"각 분야1별 전체 행에서 조회수 상위 {top_n}개씩 추출 (분야2 무시)"
                else:
                    mode_descriptions['field1'] = f"각 분야1 내에서 분야2별로 상위 {top_n}개씩 추출"
            elif top_n_mode == 'field2':
                mode_descriptions['field2'] = f"각 분야2마다 조회수 상위 {top_n}개씩 추출"
            
            if top_n_mode in mode_descriptions:
                description += mode_descriptions[top_n_mode]
            
            # 구체적인 예시 추가
            if top_n_mode == 'channel' and not base_conditions:
                description += f" 🔍 예시: A채널 상위{top_n}개 + B채널 상위{top_n}개 + ..."
            elif top_n_mode == 'field1':
                # 분야2 무시 옵션 확인
                ignore_field2 = False
                if hasattr(self, 'ignore_field2_var'):
                    ignore_field2 = self.ignore_field2_var.get()
                
                if ignore_field2:
                    if field1_filters:
                        description += f" 🔍 예시: {field1_filters[0]} 전체에서 상위{top_n}개 (분야2 무시)"
                    else:
                        description += f" 🔍 예시: 게임 전체에서 상위{top_n}개 + 엔터 전체에서 상위{top_n}개 + ... (분야2 무시)"
                else:
                    if field1_filters:
                        description += f" 🔍 예시: {field1_filters[0]} 내에서 분야2별로 상위{top_n}개"
                    else:
                        description += f" 🔍 예시: 게임-바이럴 상위{top_n}개 + 게임-시니어 상위{top_n}개 + 엔터-바이럴 상위{top_n}개 + ..."
            elif top_n_mode == 'field2':
                if field2_filters:
                    description += f" 🔍 예시: {field2_filters[0]}에서만 상위{top_n}개"
                else:
                    description += f" 🔍 예시: 바이럴 상위{top_n}개 + 시니어 상위{top_n}개 + ..."
            
            self.top_n_description_var.set(description)
            
        except Exception as e:
            logger.error(f"상위 N개 해설 업데이트 실패: {str(e)}")
            self.top_n_description_var.set("해설을 생성할 수 없습니다")
    
    def update_real_time_count(self):
        """실시간으로 조건에 부합하는 행 개수 업데이트"""
        try:
            if not hasattr(self, 'current_worksheet') or not self.current_worksheet:
                self.matching_count_var.set("먼저 시트 데이터를 불러와주세요")
                return
            
            # 현재 선택된 조건들 가져오기
            field1_filters = self.get_selected_filter_values(self.field1_checkboxes)
            field2_filters = self.get_selected_filter_values(self.field2_checkboxes)
            shortform_filters = self.get_selected_filter_values(self.shortform_checkboxes)
            
            # 수치 조건 파싱
            min_views = None
            min_subscribers = None
            channel_names = []
            min_like_ratio = None
            min_duration = None
            max_upload_days = None
            benchmarking_only = False

            if hasattr(self, 'views_var') and self.views_var.get().strip():
                try:
                    min_views = int(self.views_var.get().strip().replace(',', ''))
                except ValueError:
                    pass

            if hasattr(self, 'subscribers_var') and self.subscribers_var.get().strip():
                try:
                    min_subscribers = int(self.subscribers_var.get().strip().replace(',', ''))
                except ValueError:
                    pass

            if hasattr(self, 'channel_names_var') and self.channel_names_var.get().strip():
                channel_names = [name.strip() for name in self.channel_names_var.get().split(',') if name.strip()]

            if hasattr(self, 'like_ratio_var') and self.like_ratio_var.get().strip():
                try:
                    min_like_ratio = float(self.like_ratio_var.get().strip())
                except ValueError:
                    pass

            if hasattr(self, 'video_duration_var') and self.video_duration_var.get().strip():
                try:
                    min_duration = int(self.video_duration_var.get().strip())
                except ValueError:
                    pass

            if hasattr(self, 'upload_days_var') and self.upload_days_var.get().strip():
                try:
                    max_upload_days = int(self.upload_days_var.get().strip())
                except ValueError:
                    pass

            max_video_upload_days = None
            if hasattr(self, 'video_upload_date_var') and self.video_upload_date_var.get().strip():
                try:
                    max_video_upload_days = int(self.video_upload_date_var.get().strip())
                except ValueError:
                    pass

            if hasattr(self, 'benchmarking_var'):
                benchmarking_only = self.benchmarking_var.get()

            script_exists_only = False
            if hasattr(self, 'script_exists_var'):
                script_exists_only = self.script_exists_var.get()

            hook_subtitle_exists_only = False
            if hasattr(self, 'hook_subtitle_exists_var'):
                hook_subtitle_exists_only = self.hook_subtitle_exists_var.get()

            # 상위 N개 정렬 추출 조건 파싱
            sort_limit_enabled = False
            sort_column = None
            sort_order = None
            sort_limit = None

            if hasattr(self, 'enable_sort_limit_var') and self.enable_sort_limit_var.get():
                sort_limit_enabled = True
                if hasattr(self, 'sort_column_var'):
                    sort_column = self.sort_column_var.get()
                if hasattr(self, 'sort_order_var'):
                    sort_order = self.sort_order_var.get()
                if hasattr(self, 'sort_limit_var') and self.sort_limit_var.get().strip():
                    try:
                        sort_limit = int(self.sort_limit_var.get().strip())
                    except ValueError:
                        pass

            # 상위 N개 조건 파싱 (활성화 상태일 때만)
            top_n = None
            top_n_mode = None
            if (hasattr(self, 'enable_top_n_var') and self.enable_top_n_var.get() and
                hasattr(self, 'top_n_var') and self.top_n_var.get().strip()):
                try:
                    top_n = int(self.top_n_var.get().strip())
                except ValueError:
                    pass

            if (hasattr(self, 'enable_top_n_var') and self.enable_top_n_var.get() and
                hasattr(self, 'top_n_mode_var') and self.top_n_mode_var.get() != 'none'):
                top_n_mode = self.top_n_mode_var.get()

            # 조건이 없으면
            has_conditions = (field1_filters or field2_filters or shortform_filters or
                            min_views is not None or min_subscribers is not None or
                            channel_names or min_like_ratio is not None or
                            min_duration is not None or max_upload_days is not None or
                            benchmarking_only or script_exists_only or hook_subtitle_exists_only or
                            (top_n and top_n_mode) or sort_limit_enabled)

            if not has_conditions:
                self.matching_count_var.set("조건을 설정해주세요")
                return
            
            # 백그라운드에서 카운트 계산
            import threading
            def count_thread():
                try:
                    if top_n and top_n_mode:
                        # 상위 N개 조건이 있을 때는 실제 데이터를 가져와서 카운트
                        # ignore_field2 옵션 가져오기
                        ignore_field2 = False
                        if hasattr(self, 'ignore_field2_var') and top_n_mode == 'field1':
                            ignore_field2 = self.ignore_field2_var.get()

                        filtered_data = self.sheets_manager.get_filtered_data(
                            self.current_worksheet,
                            field1_filters, field2_filters, shortform_filters,
                            min_views, min_subscribers, channel_names, min_like_ratio,
                            min_duration, max_upload_days, max_video_upload_days, benchmarking_only,
                            script_exists_only, hook_subtitle_exists_only,
                            count_only=False, top_n=top_n, top_n_mode=top_n_mode, ignore_field2=ignore_field2,
                            sort_column=sort_column if sort_limit_enabled else None,
                            sort_order=sort_order if sort_limit_enabled else None,
                            sort_limit=sort_limit if sort_limit_enabled else None
                        )
                        count = len(filtered_data) if filtered_data else 0
                        mode_text = {"channel": "채널별", "field1": "분야1별", "field2": "분야2별"}.get(top_n_mode, "")

                        # 상위 N개 정렬 추출 조건 표시
                        if sort_limit_enabled and sort_limit:
                            # 날짜 관련 정렬인 경우 최신/오래된 순 표시
                            if '날짜' in sort_column:
                                date_order_text = "최신순" if sort_order == "내림차순" else "오래된 순"
                                sort_text = f", {sort_column} {date_order_text} 상위 {sort_limit}개"
                            else:
                                sort_text = f", {sort_column} {sort_order} 상위 {sort_limit}개"
                            self.root.after(0, lambda: self.matching_count_var.set(f"조건에 부합하는 영상 ({mode_text} 상위 {top_n}개{sort_text}): {count}개"))
                        else:
                            self.root.after(0, lambda: self.matching_count_var.set(f"조건에 부합하는 영상 ({mode_text} 상위 {top_n}개): {count}개"))
                    else:
                        # 정렬이 활성화된 경우, 먼저 전체 조건 부합 개수를 구함
                        if sort_limit_enabled and sort_limit:
                            # 정렬 적용한 개수 구하기 (실제 데이터 필요)
                            sorted_filtered_data = self.sheets_manager.get_filtered_data(
                                self.current_worksheet,
                                field1_filters, field2_filters, shortform_filters,
                                min_views, min_subscribers, channel_names, min_like_ratio,
                                min_duration, max_upload_days, max_video_upload_days, benchmarking_only,
                                script_exists_only, hook_subtitle_exists_only,
                                count_only=False,
                                sort_column=sort_column,
                                sort_order=sort_order,
                                sort_limit=sort_limit
                            )
                            sorted_count = len(sorted_filtered_data) if sorted_filtered_data else 0

                            # 전체 개수는 count_only로 빠르게 구하기
                            total_filtered_data = self.sheets_manager.get_filtered_data(
                                self.current_worksheet,
                                field1_filters, field2_filters, shortform_filters,
                                min_views, min_subscribers, channel_names, min_like_ratio,
                                min_duration, max_upload_days, max_video_upload_days, benchmarking_only,
                                script_exists_only, hook_subtitle_exists_only,
                                count_only=True
                            )
                            total_count = len(total_filtered_data) if total_filtered_data else 0

                            # 날짜 관련 정렬인 경우 최신/오래된 순 표시
                            if '날짜' in sort_column:
                                date_order_text = "최신순" if sort_order == "내림차순" else "오래된 순"
                                self.root.after(0, lambda tc=total_count, sc=sorted_count, col=sort_column, order=date_order_text, lim=sort_limit:
                                    self.matching_count_var.set(f"조건 부합 영상: {tc}개 → {col} {order} 상위 {lim}개: {sc}개"))
                            else:
                                self.root.after(0, lambda tc=total_count, sc=sorted_count, col=sort_column, order=sort_order, lim=sort_limit:
                                    self.matching_count_var.set(f"조건 부합 영상: {tc}개 → {col} {order} 상위 {lim}개: {sc}개"))
                        else:
                            # 일반 필터링 카운트 (정렬 없음)
                            filtered_data = self.sheets_manager.get_filtered_data(
                                self.current_worksheet,
                                field1_filters, field2_filters, shortform_filters,
                                min_views, min_subscribers, channel_names, min_like_ratio,
                                min_duration, max_upload_days, max_video_upload_days, benchmarking_only,
                                script_exists_only, hook_subtitle_exists_only,
                                count_only=False
                            )
                            count = len(filtered_data) if filtered_data else 0
                            self.root.after(0, lambda c=count: self.matching_count_var.set(f"현재 조건에 부합하는 영상: {c}개"))
                except Exception as e:
                    logger.error(f"실시간 카운트 계산 실패: {str(e)}")
                    self.root.after(0, lambda: self.matching_count_var.set("카운트 계산 중 오류 발생"))
            
            thread = threading.Thread(target=count_thread, daemon=True)
            thread.start()
            
        except Exception as e:
            logger.error(f"실시간 카운트 업데이트 실패: {str(e)}")
            self.matching_count_var.set("카운트 계산 중 오류 발생")
    
    def create_filter_checkboxes(self, filter_type, values, parent_frame, checkboxes_dict):
        """필터 체크박스 생성 (2열 배치)"""
        # 기존 체크박스 제거
        for widget in parent_frame.winfo_children():
            widget.destroy()
        checkboxes_dict.clear()
        
        if not values:
            ttk.Label(parent_frame, text="(해당 열에 데이터가 없습니다)", foreground='gray').pack(anchor='w', padx=5, pady=5)
            return
        
        logger.info(f"📋 {filter_type} 필터: {len(values)}개 항목 로드")
        
        # 2열로 배치하기 위한 프레임 생성
        columns_frame = ttk.Frame(parent_frame)
        columns_frame.pack(fill='both', expand=True, padx=5, pady=3)
        
        # 왼쪽과 오른쪽 프레임
        left_frame = ttk.Frame(columns_frame)
        left_frame.pack(side='left', fill='both', expand=True)
        
        right_frame = ttk.Frame(columns_frame) 
        right_frame.pack(side='right', fill='both', expand=True)
        
        # 체크박스 생성 (2열로 분배)
        for i, value in enumerate(values):
            if value and str(value).strip():
                var = tk.BooleanVar()
                checkboxes_dict[value] = var
                # 체크박스 변경시 현재 선택 상태 업데이트 및 분야2 필터링
                if filter_type == 'field1':
                    var.trace_add('write', lambda *args, ft=filter_type: self.on_field1_changed(ft))
                else:
                    var.trace_add('write', lambda *args, ft=filter_type: self.on_filter_changed(ft))
                
                # 짝수는 왼쪽, 홀수는 오른쪽에 배치
                target_frame = left_frame if i % 2 == 0 else right_frame
                
                # 분야1의 경우 선택 순서 표시, 분야2의 경우 색상 배경 적용
                if filter_type == 'field1' and hasattr(self, 'field1_selection_order'):
                    # 선택 순서 확인
                    order_text = str(value)
                    current_selected = self.get_selected_filter_values(self.field1_checkboxes)
                    if str(value) in current_selected and str(value) in self.field1_selection_order:
                        order_num = self.field1_selection_order.index(str(value)) + 1
                        color_index = (order_num - 1) % len(self.field1_colors)
                        order_text = f"[{order_num}] {value}"
                        # 순서 표시를 위한 색상 프레임
                        order_frame = tk.Frame(target_frame, bg=self.field1_colors[color_index], relief='ridge', bd=1)
                        order_frame.pack(anchor='w', padx=2, pady=1, fill='x')
                        checkbox = ttk.Checkbutton(order_frame, text=order_text, variable=var)
                        checkbox.pack(anchor='w', padx=3, pady=2)
                    else:
                        checkbox = ttk.Checkbutton(target_frame, text=order_text, variable=var)
                        checkbox.pack(anchor='w', padx=2, pady=1)
                elif filter_type == 'field2' and hasattr(self, 'field2_color_mapping') and str(value) in self.field2_color_mapping:
                    # 색상 배경을 위한 프레임 생성
                    color_frame = tk.Frame(target_frame, bg=self.field2_color_mapping[str(value)], relief='ridge', bd=1)
                    color_frame.pack(anchor='w', padx=2, pady=1, fill='x')
                    checkbox = ttk.Checkbutton(color_frame, text=str(value), variable=var)
                    checkbox.pack(anchor='w', padx=3, pady=2)
                else:
                    checkbox = ttk.Checkbutton(target_frame, text=str(value), variable=var)
                    checkbox.pack(anchor='w', padx=2, pady=1)
        
        # 스크롤 영역 업데이트
        parent_frame.update_idletasks()
    
    def update_filter_checkboxes(self, filter_type, values, parent_frame, checkboxes_dict):
        """필터 체크박스 업데이트"""
        # 기존 체크박스 제거
        for widget in parent_frame.winfo_children():
            widget.destroy()
        checkboxes_dict.clear()
        
        if not values:
            ttk.Label(parent_frame, text="(데이터 없음)", foreground='gray').pack(anchor='w')
            return
        
        # 스크롤 가능한 프레임 생성 (값이 많을 경우를 위해)
        if len(values) > 8:
            canvas = tk.Canvas(parent_frame, height=120)
            scrollbar = ttk.Scrollbar(parent_frame, orient="vertical", command=canvas.yview)
            scrollable_frame = ttk.Frame(canvas)
            
            scrollable_frame.bind(
                "<Configure>",
                lambda e: canvas.configure(scrollregion=canvas.bbox("all"))
            )
            
            canvas.create_window((0, 0), window=scrollable_frame, anchor="nw")
            canvas.configure(yscrollcommand=scrollbar.set)
            
            canvas.pack(side="left", fill="both", expand=True)
            scrollbar.pack(side="right", fill="y")
            container = scrollable_frame
        else:
            container = parent_frame
        
        # 체크박스 생성 (변경 감지 콜백 포함)
        for value in values:
            if value and str(value).strip():
                var = tk.BooleanVar()
                checkboxes_dict[value] = var
                # 체크박스 변경시 현재 선택 상태 업데이트 및 분야2 필터링
                if filter_type == 'field1':
                    var.trace_add('write', lambda *args, ft=filter_type: self.on_field1_changed(ft))
                else:
                    var.trace_add('write', lambda *args, ft=filter_type: self.on_filter_changed(ft))
                ttk.Checkbutton(container, text=str(value), variable=var).pack(anchor='w', padx=5, pady=1)
    
    def get_selected_filter_values(self, checkboxes_dict):
        """선택된 필터값들 반환"""
        selected = []
        for value, var in checkboxes_dict.items():
            if var.get():
                selected.append(value)
        return selected
    
    def update_current_filter_display(self, filter_type):
        """현재 선택된 필터를 표시 영역에 업데이트 (개수 포함)"""
        try:
            if filter_type == 'field1':
                selected = self.get_selected_filter_values(self.field1_checkboxes)
                if selected:
                    display_text = f"({len(selected)}개 선택) {', '.join(selected)}"
                else:
                    display_text = '선택 안함'
                self.field1_current_var.set(display_text)
                
            elif filter_type == 'field2':
                selected = self.get_selected_filter_values(self.field2_checkboxes)
                if selected:
                    display_text = f"({len(selected)}개 선택) {', '.join(selected)}"
                else:
                    display_text = '선택 안함'
                self.field2_current_var.set(display_text)
                
            elif filter_type == 'shortform':
                selected = self.get_selected_filter_values(self.shortform_checkboxes)
                if selected:
                    display_text = f"({len(selected)}개 선택) {', '.join(selected)}"
                else:
                    display_text = '선택 안함'
                self.shortform_current_var.set(display_text)
                
        except Exception as e:
            logger.debug(f"필터 표시 업데이트 중 오류: {str(e)}")
    
    def reset_filters(self):
        """모든 필터 초기화"""
        try:
            # 체크박스 초기화
            for checkboxes_dict in [self.field1_checkboxes, self.field2_checkboxes, self.shortform_checkboxes]:
                for var in checkboxes_dict.values():
                    var.set(False)
            
            # 현재 선택된 항목 표시 초기화
            self.field1_current_var.set('선택 안함')
            self.field2_current_var.set('선택 안함')
            self.shortform_current_var.set('선택 안함')
            
            # 입력란 초기화
            self.views_var.set('')
            self.subscribers_var.set('')
            self.channel_names_var.set('')
            self.like_ratio_var.set('')
            if hasattr(self, 'video_duration_var'):
                self.video_duration_var.set('')
            if hasattr(self, 'upload_days_var'):
                self.upload_days_var.set('')
            if hasattr(self, 'benchmarking_var'):
                self.benchmarking_var.set(False)
            
            logger.info("모든 필터가 초기화되었습니다.")
        except Exception as e:
            logger.error(f"필터 초기화 중 오류: {str(e)}")
    
    def check_matching_count(self):
        """조건에 맞는 행(영상) 개수를 미리 체크"""
        if self.is_running:
            messagebox.showwarning("경고", "다른 작업이 실행 중입니다.")
            return
        
        try:
            # 현재 워크시트 확인
            if not self.current_worksheet:
                messagebox.showwarning("경고", "먼저 '시트 데이터 불러오기' 버튼을 클릭하여 데이터를 로드해주세요.")
                return
            
            # 선택된 필터값들 가져오기
            field1_filters = self.get_selected_filter_values(self.field1_checkboxes)
            field2_filters = self.get_selected_filter_values(self.field2_checkboxes)
            shortform_filters = self.get_selected_filter_values(self.shortform_checkboxes)

            # 수치 조건 파싱
            min_views = None
            min_subscribers = None
            channel_names = []
            min_like_ratio = None
            min_duration = None
            max_upload_days = None
            benchmarking_only = False

            if self.views_var.get().strip():
                try:
                    min_views = int(self.views_var.get().strip().replace(',', ''))
                except ValueError:
                    messagebox.showerror("오류", "조회수는 숫자로 입력해주세요.")
                    return

            if self.subscribers_var.get().strip():
                try:
                    min_subscribers = int(self.subscribers_var.get().strip().replace(',', ''))
                except ValueError:
                    messagebox.showerror("오류", "구독자수는 숫자로 입력해주세요.")
                    return

            if self.channel_names_var.get().strip():
                channel_names = [name.strip() for name in self.channel_names_var.get().split(',') if name.strip()]

            if self.like_ratio_var.get().strip():
                try:
                    min_like_ratio = float(self.like_ratio_var.get().strip())
                except ValueError:
                    messagebox.showerror("오류", "좋아요 비율은 숫자로 입력해주세요.")
                    return

            # 디버깅: 속성 존재 확인
            logger.debug(f"video_duration_var 존재: {hasattr(self, 'video_duration_var')}")
            if hasattr(self, 'video_duration_var'):
                logger.debug(f"video_duration_var 값: '{self.video_duration_var.get()}'")

            logger.debug(f"upload_days_var 존재: {hasattr(self, 'upload_days_var')}")
            if hasattr(self, 'upload_days_var'):
                logger.debug(f"upload_days_var 값: '{self.upload_days_var.get()}'")

            logger.debug(f"benchmarking_var 존재: {hasattr(self, 'benchmarking_var')}")
            if hasattr(self, 'benchmarking_var'):
                logger.debug(f"benchmarking_var 값: {self.benchmarking_var.get()}")

            if hasattr(self, 'video_duration_var') and self.video_duration_var.get().strip():
                try:
                    min_duration = int(self.video_duration_var.get().strip())
                    logger.info(f"✅ 영상길이 조건 파싱: {min_duration}초")
                except ValueError:
                    messagebox.showerror("오류", "영상길이는 숫자(초)로 입력해주세요.")
                    return

            if hasattr(self, 'upload_days_var') and self.upload_days_var.get().strip():
                try:
                    max_upload_days = int(self.upload_days_var.get().strip())
                    logger.info(f"✅ 수집날짜 경과일 조건 파싱: {max_upload_days}일")
                except ValueError:
                    messagebox.showerror("오류", "수집날짜 경과일은 숫자로 입력해주세요.")
                    return

            max_video_upload_days = None
            if hasattr(self, 'video_upload_date_var') and self.video_upload_date_var.get().strip():
                try:
                    max_video_upload_days = int(self.video_upload_date_var.get().strip())
                    logger.info(f"✅ 영상 업로드날짜 조건 파싱: {max_video_upload_days}일")
                except ValueError:
                    messagebox.showerror("오류", "영상 업로드날짜는 숫자로 입력해주세요.")
                    return

            if hasattr(self, 'benchmarking_var'):
                benchmarking_only = self.benchmarking_var.get()
                logger.info(f"✅ 벤치마킹 채널만 조건 파싱: {benchmarking_only}")

            script_exists_only = False
            if hasattr(self, 'script_exists_var'):
                script_exists_only = self.script_exists_var.get()
                logger.info(f"✅ 대본유무 조건 파싱: {script_exists_only}")

            hook_subtitle_exists_only = False
            if hasattr(self, 'hook_subtitle_exists_var'):
                hook_subtitle_exists_only = self.hook_subtitle_exists_var.get()
                logger.info(f"✅ 후킹자막 유무 조건 파싱: {hook_subtitle_exists_only}")

            logger.info("🔍 조건에 맞는 행 개수 체크 시작")

            # 진행 표시
            self.progress_var.set("조건에 맞는 행 개수를 확인 중...")
            self.progress_bar.start()
            self.count_check_button.config(state='disabled', text='체크 중...')

            # 비동기로 실행 (UI 블로킹 방지)
            threading.Thread(target=self._run_count_check,
                           args=(field1_filters, field2_filters, shortform_filters,
                                min_views, min_subscribers, channel_names, min_like_ratio,
                                min_duration, max_upload_days, max_video_upload_days, benchmarking_only, script_exists_only, hook_subtitle_exists_only),
                           daemon=True).start()
            
        except Exception as e:
            logger.error(f"행 개수 체크 시작 중 오류: {str(e)}")
            messagebox.showerror("오류", f"실행 중 오류가 발생했습니다: {str(e)}")
    
    def _run_count_check(self, field1_filters, field2_filters, shortform_filters,
                        min_views, min_subscribers, channel_names, min_like_ratio,
                        min_duration, max_upload_days, max_video_upload_days, benchmarking_only, script_exists_only, hook_subtitle_exists_only):
        """조건에 맞는 행 개수 체크 실제 실행 (백그라운드)"""
        try:
            logger.info("📋 적용할 조건:")
            logger.info(f"   - 분야1: {field1_filters if field1_filters else '조건 없음'}")
            logger.info(f"   - 분야2: {field2_filters if field2_filters else '조건 없음'}")
            logger.info(f"   - 숏폼여부: {shortform_filters if shortform_filters else '조건 없음'}")
            logger.info(f"   - 조회수: {f'{min_views:,} 이상' if min_views else '조건 없음'}")
            logger.info(f"   - 구독자수: {f'{min_subscribers:,} 이상' if min_subscribers else '조건 없음'}")
            logger.info(f"   - 채널명: {channel_names if channel_names else '조건 없음'}")
            logger.info(f"   - 좋아요 비율: {f'{min_like_ratio}% 이상' if min_like_ratio else '조건 없음'}")
            logger.info(f"   - 영상길이: {f'{min_duration}초 이상' if min_duration else '조건 없음'}")
            logger.info(f"   - 수집날짜 경과일: {f'{max_upload_days}일 이내' if max_upload_days else '조건 없음'}")
            logger.info(f"   - 영상 업로드날짜: {f'{max_video_upload_days}일 이내' if max_video_upload_days else '조건 없음'}")
            logger.info(f"   - 벤치마킹 채널만: {'예' if benchmarking_only else '아니오'}")
            logger.info(f"   - 대본유무: {'예' if script_exists_only else '아니오'}")
            logger.info(f"   - 후킹자막 유무: {'예' if hook_subtitle_exists_only else '아니오'}")
            
            # 상위 N개 조건 파싱 (활성화 상태일 때만)
            top_n = None
            top_n_mode = None
            if (hasattr(self, 'enable_top_n_var') and self.enable_top_n_var.get() and
                hasattr(self, 'top_n_var') and self.top_n_var.get().strip()):
                try:
                    top_n = int(self.top_n_var.get().strip())
                except ValueError:
                    pass
            if (hasattr(self, 'enable_top_n_var') and self.enable_top_n_var.get() and
                hasattr(self, 'top_n_mode_var') and self.top_n_mode_var.get() != 'none'):
                top_n_mode = self.top_n_mode_var.get()
                
            # 상위 N개 조건 로그 출력
            if top_n and top_n_mode:
                mode_text = {"channel": "채널별", "field1": "분야1별", "field2": "분야2별"}.get(top_n_mode, "")
                logger.info(f"   - 상위 N개: {mode_text} 상위 {top_n}개")

            # ignore_field2 옵션 가져오기
            ignore_field2 = False
            if hasattr(self, 'ignore_field2_var') and top_n_mode == 'field1':
                ignore_field2 = self.ignore_field2_var.get()

            # 상위 N개 정렬 추출 조건 파싱
            sort_column = None
            sort_order = None
            sort_limit = None
            sort_limit_enabled = False

            if hasattr(self, 'enable_sort_limit_var') and self.enable_sort_limit_var.get():
                sort_limit_enabled = True
                if hasattr(self, 'sort_column_var'):
                    sort_column = self.sort_column_var.get()
                if hasattr(self, 'sort_order_var'):
                    sort_order = self.sort_order_var.get()
                if hasattr(self, 'sort_limit_var') and self.sort_limit_var.get().strip():
                    try:
                        sort_limit = int(self.sort_limit_var.get().strip())
                        logger.info(f"   - 정렬 추출: {sort_column} {sort_order} 상위 {sort_limit}개")
                    except ValueError:
                        pass

            # 정렬이 활성화된 경우, 전체 조건 부합 개수와 정렬 후 개수를 각각 구함
            if sort_limit_enabled and sort_limit:
                # 정렬 적용한 데이터 구하기 (실제 데이터 필요)
                sorted_filtered_data = self.sheets_manager.get_filtered_data(
                    sheet=self.current_worksheet,
                    field1_filters=field1_filters if field1_filters else None,
                    field2_filters=field2_filters if field2_filters else None,
                    shortform_filters=shortform_filters if shortform_filters else None,
                    min_views=min_views,
                    min_subscribers=min_subscribers,
                    channel_names=channel_names if channel_names else None,
                    min_like_ratio=min_like_ratio,
                    min_duration=min_duration,
                    max_upload_days=max_upload_days,
                    max_video_upload_days=max_video_upload_days,
                    benchmarking_only=benchmarking_only,
                    script_exists_only=script_exists_only,
                    hook_subtitle_exists_only=hook_subtitle_exists_only,
                    count_only=False,
                    top_n=top_n,
                    top_n_mode=top_n_mode,
                    ignore_field2=ignore_field2,
                    sort_column=sort_column,
                    sort_order=sort_order,
                    sort_limit=sort_limit
                )
                sorted_count = len(sorted_filtered_data)

                # 전체 개수는 정렬 전 데이터를 count_only로 빠르게 구하기
                total_filtered_data = self.sheets_manager.get_filtered_data(
                    sheet=self.current_worksheet,
                    field1_filters=field1_filters if field1_filters else None,
                    field2_filters=field2_filters if field2_filters else None,
                    shortform_filters=shortform_filters if shortform_filters else None,
                    min_views=min_views,
                    min_subscribers=min_subscribers,
                    channel_names=channel_names if channel_names else None,
                    min_like_ratio=min_like_ratio,
                    min_duration=min_duration,
                    max_upload_days=max_upload_days,
                    max_video_upload_days=max_video_upload_days,
                    benchmarking_only=benchmarking_only,
                    script_exists_only=script_exists_only,
                    hook_subtitle_exists_only=hook_subtitle_exists_only,
                    count_only=True,  # 카운트만 하므로 빠름
                    top_n=top_n,
                    top_n_mode=top_n_mode,
                    ignore_field2=ignore_field2
                )
                total_count = len(total_filtered_data)

                self.progress_var.set(f"체크 완료: 조건 부합 {total_count}개 → 정렬 후 {sorted_count}개")
                logger.info(f"🎯 조건에 맞는 행 개수: {total_count}개 → 정렬 후: {sorted_count}개")

                # 날짜 관련 정렬인 경우 최신/오래된 순 표시
                if '날짜' in sort_column:
                    date_order_text = "최신순" if sort_order == "내림차순" else "오래된 순"
                    sort_text = f"{sort_column} {date_order_text}"
                else:
                    sort_text = f"{sort_column} {sort_order}"

                # 결과 메시지 표시
                if sorted_count == 0:
                    messagebox.showinfo("체크 결과", f"조건에 맞는 영상: {total_count}개\n정렬 후 ({sort_text} 상위 {sort_limit}개): 0개")
                else:
                    messagebox.showinfo("체크 결과",
                                      f"조건에 맞는 영상: {total_count}개\n"
                                      f"정렬 후 ({sort_text} 상위 {sort_limit}개): {sorted_count}개\n\n"
                                      f"'조건 추출' 버튼을 클릭하면 이 {sorted_count}개의 영상이\n"
                                      f"'조건 추출 영상' 시트에 추가됩니다.")
            else:
                # 정렬 없이 일반 필터링
                filtered_data = self.sheets_manager.get_filtered_data(
                    sheet=self.current_worksheet,
                    field1_filters=field1_filters if field1_filters else None,
                    field2_filters=field2_filters if field2_filters else None,
                    shortform_filters=shortform_filters if shortform_filters else None,
                    min_views=min_views,
                    min_subscribers=min_subscribers,
                    channel_names=channel_names if channel_names else None,
                    min_like_ratio=min_like_ratio,
                    min_duration=min_duration,
                    max_upload_days=max_upload_days,
                    max_video_upload_days=max_video_upload_days,
                    benchmarking_only=benchmarking_only,
                    script_exists_only=script_exists_only,
                    hook_subtitle_exists_only=hook_subtitle_exists_only,
                    count_only=False if (top_n and top_n_mode) else True,
                    top_n=top_n,
                    top_n_mode=top_n_mode,
                    ignore_field2=ignore_field2
                )

                count = len(filtered_data)

                self.progress_var.set(f"체크 완료: 조건에 맞는 {count}개의 영상이 발견되었습니다.")
                logger.info(f"🎯 조건에 맞는 행 개수: {count}개")

                # 결과 메시지 표시
                if count == 0:
                    messagebox.showinfo("체크 결과", "조건에 맞는 영상이 없습니다.")
                else:
                    messagebox.showinfo("체크 결과",
                                      f"조건에 맞는 영상: {count}개\n\n"
                                      f"'조건 추출' 버튼을 클릭하면 이 {count}개의 영상이\n"
                                      f"'조건 추출 영상' 시트에 추가됩니다.")
        
        except Exception as e:
            logger.error(f"❌ 행 개수 체크 중 오류: {str(e)}")
            self.progress_var.set(f"체크 실패: {str(e)}")
            messagebox.showerror("오류", f"체크 중 오류가 발생했습니다: {str(e)}")
        
        finally:
            self.count_check_button.config(state='normal', text='조건에 맞는 행(영상) 갯수 체크')
            self.progress_bar.stop()
    
    def extract_conditional_videos(self):
        """조건에 맞는 영상 추출 실행"""
        if self.is_running:
            messagebox.showwarning("경고", "이미 실행 중입니다.")
            return
        
        try:
            # 선택된 필터값들 가져오기
            field1_filters = self.get_selected_filter_values(self.field1_checkboxes)
            field2_filters = self.get_selected_filter_values(self.field2_checkboxes)
            shortform_filters = self.get_selected_filter_values(self.shortform_checkboxes)

            # 수치 조건 파싱
            min_views = None
            min_subscribers = None
            channel_names = []
            min_like_ratio = None
            min_duration = None
            max_upload_days = None
            benchmarking_only = False

            if self.views_var.get().strip():
                try:
                    min_views = int(self.views_var.get().strip().replace(',', ''))
                except ValueError:
                    messagebox.showerror("오류", "조회수는 숫자로 입력해주세요.")
                    return

            if self.subscribers_var.get().strip():
                try:
                    min_subscribers = int(self.subscribers_var.get().strip().replace(',', ''))
                except ValueError:
                    messagebox.showerror("오류", "구독자수는 숫자로 입력해주세요.")
                    return

            if self.channel_names_var.get().strip():
                channel_names = [name.strip() for name in self.channel_names_var.get().split(',') if name.strip()]

            if self.like_ratio_var.get().strip():
                try:
                    min_like_ratio = float(self.like_ratio_var.get().strip())
                except ValueError:
                    messagebox.showerror("오류", "좋아요 비율은 숫자로 입력해주세요.")
                    return

            if hasattr(self, 'video_duration_var') and self.video_duration_var.get().strip():
                try:
                    min_duration = int(self.video_duration_var.get().strip())
                except ValueError:
                    messagebox.showerror("오류", "영상길이는 숫자(초)로 입력해주세요.")
                    return

            if hasattr(self, 'upload_days_var') and self.upload_days_var.get().strip():
                try:
                    max_upload_days = int(self.upload_days_var.get().strip())
                except ValueError:
                    messagebox.showerror("오류", "수집날짜 경과일은 숫자로 입력해주세요.")
                    return

            max_video_upload_days = None
            if hasattr(self, 'video_upload_date_var') and self.video_upload_date_var.get().strip():
                try:
                    max_video_upload_days = int(self.video_upload_date_var.get().strip())
                except ValueError:
                    messagebox.showerror("오류", "영상 업로드날짜는 숫자로 입력해주세요.")
                    return

            if hasattr(self, 'benchmarking_var'):
                benchmarking_only = self.benchmarking_var.get()

            script_exists_only = False
            if hasattr(self, 'script_exists_var'):
                script_exists_only = self.script_exists_var.get()

            hook_subtitle_exists_only = False
            if hasattr(self, 'hook_subtitle_exists_var'):
                hook_subtitle_exists_only = self.hook_subtitle_exists_var.get()

            # 조건 확인
            has_conditions = (field1_filters or field2_filters or shortform_filters or
                            min_views is not None or min_subscribers is not None or
                            channel_names or min_like_ratio is not None or
                            min_duration is not None or max_upload_days is not None or
                            benchmarking_only or script_exists_only or hook_subtitle_exists_only)

            if not has_conditions:
                result = messagebox.askyesno("확인", "조건이 설정되지 않았습니다. 모든 데이터를 추출하시겠습니까?")
                if not result:
                    return

            # 비동기 실행
            threading.Thread(target=self._run_conditional_extraction,
                           args=(field1_filters, field2_filters, shortform_filters,
                                min_views, min_subscribers, channel_names, min_like_ratio,
                                min_duration, max_upload_days, benchmarking_only, script_exists_only, hook_subtitle_exists_only),
                           daemon=True).start()
            
        except Exception as e:
            logger.error(f"조건부 추출 시작 중 오류: {str(e)}")
            messagebox.showerror("오류", f"실행 중 오류가 발생했습니다: {str(e)}")

    def extract_to_new_sheet(self):
        """새로운 시트를 생성하여 조건에 맞는 영상 추출"""
        if self.is_running:
            messagebox.showwarning("경고", "이미 실행 중입니다.")
            return

        try:
            # 현재 워크시트와 스프레드시트 확인
            if not self.current_worksheet:
                messagebox.showwarning("경고", "먼저 '시트 데이터 불러오기' 버튼을 클릭하여 데이터를 로드해주세요.")
                return

            if not hasattr(self, 'current_sheet_url') or not self.current_sheet_url:
                messagebox.showwarning("경고", "스프레드시트가 선택되지 않았습니다.")
                return

            # 비동기로 실행
            threading.Thread(target=self._run_extract_to_new_sheet, daemon=True).start()

        except Exception as e:
            logger.error(f"새 시트 추출 시작 중 오류: {str(e)}")
            messagebox.showerror("오류", f"실행 중 오류가 발생했습니다: {str(e)}")

    def _run_extract_to_new_sheet(self):
        """새 시트 추출 실제 실행 (백그라운드)"""
        try:
            self.is_running = True
            self.extract_new_sheet_button.config(state='disabled', text='추출 중...')
            self.progress_bar.start()
            self.progress_var.set("새로운 시트를 생성하고 데이터를 추출 중...")

            logger.info("🎯 새 시트에 조건부 추출 시작")

            # 1. 스프레드시트 열기
            spreadsheet = self.sheets_manager.client.open_by_url(self.current_sheet_url)

            # 2. "조건 추출 영상" 시트 찾기
            source_sheet_name = "조건 추출 영상"
            source_sheet = None
            try:
                source_sheet = spreadsheet.worksheet(source_sheet_name)
            except:
                messagebox.showerror("오류", f"'{source_sheet_name}' 시트를 찾을 수 없습니다.")
                return

            # 3. 새 시트 이름 결정 (넘버링)
            existing_sheets = [sheet.title for sheet in spreadsheet.worksheets()]
            new_sheet_number = 2
            while f"조건 추출 영상_{new_sheet_number:02d}" in existing_sheets:
                new_sheet_number += 1
            new_sheet_name = f"조건 추출 영상_{new_sheet_number:02d}"

            logger.info(f"📝 새 시트 이름: {new_sheet_name}")

            # 4. 시트 복제
            self.progress_var.set(f"'{source_sheet_name}' 시트를 복제 중...")
            new_sheet = source_sheet.duplicate(new_sheet_name=new_sheet_name)
            logger.info(f"✅ 시트 복제 완료: {new_sheet_name}")

            # 5. 10행부터 데이터 삭제
            self.progress_var.set("기존 데이터 제거 중...")
            all_values = new_sheet.get_all_values()
            if len(all_values) > 9:  # 10행 이상 있으면
                rows_to_delete = len(all_values) - 9
                new_sheet.delete_rows(10, 10 + rows_to_delete - 1)
                logger.info(f"✅ 10행부터 {rows_to_delete}개 행 삭제 완료")

            # 6. 조건에 맞는 데이터 추출
            self.progress_var.set("조건에 맞는 영상 데이터를 가져오는 중...")

            # 현재 조건들 가져오기
            field1_filters = self.get_selected_filter_values(self.field1_checkboxes)
            field2_filters = self.get_selected_filter_values(self.field2_checkboxes)
            shortform_filters = self.get_selected_filter_values(self.shortform_checkboxes)

            # 수치 조건 파싱
            min_views = None
            min_subscribers = None
            channel_names = []
            min_like_ratio = None
            min_duration = None
            max_upload_days = None
            benchmarking_only = False

            if hasattr(self, 'views_var') and self.views_var.get().strip():
                min_views = int(self.views_var.get().strip().replace(',', ''))

            if hasattr(self, 'subscribers_var') and self.subscribers_var.get().strip():
                min_subscribers = int(self.subscribers_var.get().strip().replace(',', ''))

            if hasattr(self, 'channel_names_var') and self.channel_names_var.get().strip():
                channel_names = [name.strip() for name in self.channel_names_var.get().split(',') if name.strip()]

            if hasattr(self, 'like_ratio_var') and self.like_ratio_var.get().strip():
                min_like_ratio = float(self.like_ratio_var.get().strip())

            if hasattr(self, 'video_duration_var') and self.video_duration_var.get().strip():
                min_duration = int(self.video_duration_var.get().strip())

            if hasattr(self, 'upload_days_var') and self.upload_days_var.get().strip():
                max_upload_days = int(self.upload_days_var.get().strip())

            if hasattr(self, 'benchmarking_var'):
                benchmarking_only = self.benchmarking_var.get()

            script_exists_only = False
            if hasattr(self, 'script_exists_var'):
                script_exists_only = self.script_exists_var.get()

            hook_subtitle_exists_only = False
            if hasattr(self, 'hook_subtitle_exists_var'):
                hook_subtitle_exists_only = self.hook_subtitle_exists_var.get()

            # 상위 N개 조건
            top_n = None
            top_n_mode = None
            if (hasattr(self, 'enable_top_n_var') and self.enable_top_n_var.get() and
                hasattr(self, 'top_n_var') and self.top_n_var.get().strip()):
                try:
                    top_n = int(self.top_n_var.get().strip())
                except ValueError:
                    pass
            if (hasattr(self, 'enable_top_n_var') and self.enable_top_n_var.get() and
                hasattr(self, 'top_n_mode_var') and self.top_n_mode_var.get() != 'none'):
                top_n_mode = self.top_n_mode_var.get()

            ignore_field2 = False
            if hasattr(self, 'ignore_field2_var') and top_n_mode == 'field1':
                ignore_field2 = self.ignore_field2_var.get()

            # 데이터 필터링
            filtered_data = self.sheets_manager.get_filtered_data(
                sheet=self.current_worksheet,
                field1_filters=field1_filters if field1_filters else None,
                field2_filters=field2_filters if field2_filters else None,
                shortform_filters=shortform_filters if shortform_filters else None,
                min_views=min_views,
                min_subscribers=min_subscribers,
                channel_names=channel_names if channel_names else None,
                min_like_ratio=min_like_ratio,
                min_duration=min_duration,
                max_upload_days=max_upload_days,
                max_video_upload_days=max_video_upload_days,
                benchmarking_only=benchmarking_only,
                script_exists_only=script_exists_only,
                hook_subtitle_exists_only=hook_subtitle_exists_only,
                count_only=False,
                top_n=top_n,
                top_n_mode=top_n_mode,
                ignore_field2=ignore_field2
            )

            if not filtered_data:
                messagebox.showinfo("결과", "조건에 맞는 영상이 없습니다.")
                return

            # 7. 필터링된 데이터를 새 시트에 추가
            self.progress_var.set(f"{len(filtered_data)}개의 영상 데이터를 추가하는 중...")
            new_sheet.append_rows(filtered_data, value_input_option='USER_ENTERED')

            logger.info(f"✅ {len(filtered_data)}개의 영상 데이터가 '{new_sheet_name}' 시트에 추가되었습니다")

            # 8. 콤보박스에 새 시트 추가
            current_values = list(self.target_sheet_combo['values'])
            if new_sheet_name not in current_values:
                current_values.append(new_sheet_name)
                self.target_sheet_combo['values'] = current_values
                self.target_sheet_var.set(new_sheet_name)

            self.progress_var.set(f"완료: {len(filtered_data)}개의 영상이 '{new_sheet_name}' 시트에 추가되었습니다")
            messagebox.showinfo("추출 완료",
                              f"조건에 맞는 {len(filtered_data)}개의 영상이\n"
                              f"'{new_sheet_name}' 시트에 추가되었습니다.")

        except Exception as e:
            logger.error(f"❌ 새 시트 추출 중 오류: {str(e)}")
            self.progress_var.set(f"추출 실패: {str(e)}")
            messagebox.showerror("오류", f"추출 중 오류가 발생했습니다:\n{str(e)}")

        finally:
            self.is_running = False
            self.extract_new_sheet_button.config(state='normal', text='새로운 시트에 추출')
            self.progress_bar.stop()

    def extract_ai_reference_data(self):
        """AI참고용 특정 열만 추출하여 추출 대상 시트에 복사"""
        if self.is_running:
            messagebox.showwarning("경고", "이미 실행 중입니다.")
            return

        try:
            # 현재 워크시트와 스프레드시트 확인
            if not self.current_worksheet:
                messagebox.showwarning("경고", "먼저 '시트 데이터 불러오기' 버튼을 클릭하여 데이터를 로드해주세요.")
                return

            if not hasattr(self, 'current_sheet_url') or not self.current_sheet_url:
                messagebox.showwarning("경고", "스프레드시트가 선택되지 않았습니다.")
                return

            # 추출 대상 시트 확인
            if not hasattr(self, 'target_sheet_var') or not self.target_sheet_var.get():
                messagebox.showwarning("경고", "추출 대상 시트를 선택해주세요.")
                return

            # 비동기로 실행
            threading.Thread(target=self._run_extract_ai_reference_data, daemon=True).start()

        except Exception as e:
            logger.error(f"AI참고용 데이터 추출 시작 중 오류: {str(e)}")
            messagebox.showerror("오류", f"실행 중 오류가 발생했습니다: {str(e)}")

    def _run_extract_ai_reference_data(self):
        """AI참고용 데이터 추출 실제 실행 (백그라운드)"""
        try:
            self.is_running = True
            self.extract_ai_data_button.config(state='disabled', text='추출 중...')
            self.progress_bar.start()
            self.progress_var.set("AI참고용 데이터를 추출 중...")

            logger.info("🎯 AI참고용 데이터 추출 시작")

            # 필요한 열 이름 정의
            # 대상 시트에 추가할 열 목록 (원본 시트에 있는 열만 추출됨)
            required_columns = [
                '영상 ID', '영상 업로드날짜', '제목', '채널명', '조회수', '벤치마킹 채널여부', '영상길이',
                '분야1', '분야2', '구독자수', '구독자 대비 조회수 배율', '조회수 대비 좋아요', '조회수 대비 댓글',
                '카테고리 ID', '디스크립션', '후킹자막',
                '대본내용', '대본 텍스트수'
            ]

            # 1. 조건에 맞는 데이터 필터링
            field1_filters = self.get_selected_filter_values(self.field1_checkboxes)
            field2_filters = self.get_selected_filter_values(self.field2_checkboxes)
            shortform_filters = self.get_selected_filter_values(self.shortform_checkboxes)

            # 수치 조건 파싱
            min_views = None
            min_subscribers = None
            channel_names = []
            min_like_ratio = None
            min_duration = None
            max_upload_days = None
            benchmarking_only = False

            if hasattr(self, 'views_var') and self.views_var.get().strip():
                min_views = int(self.views_var.get().strip().replace(',', ''))

            if hasattr(self, 'subscribers_var') and self.subscribers_var.get().strip():
                min_subscribers = int(self.subscribers_var.get().strip().replace(',', ''))

            if hasattr(self, 'channel_names_var') and self.channel_names_var.get().strip():
                channel_names = [name.strip() for name in self.channel_names_var.get().split(',') if name.strip()]

            if hasattr(self, 'like_ratio_var') and self.like_ratio_var.get().strip():
                min_like_ratio = float(self.like_ratio_var.get().strip())

            if hasattr(self, 'video_duration_var') and self.video_duration_var.get().strip():
                min_duration = int(self.video_duration_var.get().strip())

            if hasattr(self, 'upload_days_var') and self.upload_days_var.get().strip():
                max_upload_days = int(self.upload_days_var.get().strip())

            if hasattr(self, 'benchmarking_var'):
                benchmarking_only = self.benchmarking_var.get()

            # 대본유무 조건
            has_transcript = None
            if hasattr(self, 'script_exists_var') and self.script_exists_var.get():
                has_transcript = True

            # 후킹자막 유무 조건
            has_hook = None
            if hasattr(self, 'hook_subtitle_exists_var') and self.hook_subtitle_exists_var.get():
                has_hook = True

            # 상위 N개 조건
            top_n = None
            top_n_mode = None
            if (hasattr(self, 'enable_top_n_var') and self.enable_top_n_var.get() and
                hasattr(self, 'top_n_var') and self.top_n_var.get().strip()):
                try:
                    top_n = int(self.top_n_var.get().strip())
                except ValueError:
                    pass
            if (hasattr(self, 'enable_top_n_var') and self.enable_top_n_var.get() and
                hasattr(self, 'top_n_mode_var') and self.top_n_mode_var.get() != 'none'):
                top_n_mode = self.top_n_mode_var.get()

            ignore_field2 = False
            if hasattr(self, 'ignore_field2_var') and top_n_mode == 'field1':
                ignore_field2 = self.ignore_field2_var.get()

            # 상위 N개 정렬 추출 조건 파싱
            sort_column = None
            sort_order = None
            sort_limit = None

            if hasattr(self, 'enable_sort_limit_var') and self.enable_sort_limit_var.get():
                if hasattr(self, 'sort_column_var'):
                    sort_column = self.sort_column_var.get()
                if hasattr(self, 'sort_order_var'):
                    sort_order = self.sort_order_var.get()
                if hasattr(self, 'sort_limit_var') and self.sort_limit_var.get().strip():
                    try:
                        sort_limit = int(self.sort_limit_var.get().strip())
                    except ValueError:
                        pass

            # 데이터 필터링
            self.progress_var.set("조건에 맞는 영상 데이터를 필터링 중...")
            filtered_data = self.sheets_manager.get_filtered_data(
                sheet=self.current_worksheet,
                field1_filters=field1_filters if field1_filters else None,
                field2_filters=field2_filters if field2_filters else None,
                shortform_filters=shortform_filters if shortform_filters else None,
                min_views=min_views,
                min_subscribers=min_subscribers,
                channel_names=channel_names if channel_names else None,
                min_like_ratio=min_like_ratio,
                min_duration=min_duration,
                max_upload_days=max_upload_days,
                benchmarking_only=benchmarking_only,
                has_transcript=has_transcript,
                has_hook=has_hook,
                count_only=False,
                top_n=top_n,
                top_n_mode=top_n_mode,
                ignore_field2=ignore_field2,
                sort_column=sort_column,
                sort_order=sort_order,
                sort_limit=sort_limit
            )

            if not filtered_data:
                messagebox.showinfo("결과", "조건에 맞는 영상이 없습니다.")
                return

            logger.info(f"✅ 조건에 맞는 영상 {len(filtered_data)}개 필터링 완료")

            # 2. 스프레드시트와 대상 시트 열기
            self.progress_var.set("대상 시트를 준비하는 중...")
            spreadsheet = self.sheets_manager.client.open_by_url(self.current_sheet_url)
            target_sheet_name = self.target_sheet_var.get()

            # 대상 시트 찾기 또는 생성
            try:
                target_sheet = spreadsheet.worksheet(target_sheet_name)
            except:
                messagebox.showerror("오류", f"'{target_sheet_name}' 시트를 찾을 수 없습니다.")
                return

            # 3. 헤더 매핑 가져오기 (원본 시트 - 9행을 헤더로 사용)
            source_column_mapping = self.sheets_manager.get_header_columns(self.current_worksheet, header_row_num=9)

            # 원본 시트의 모든 헤더 텍스트 출력 (디버깅용)
            source_all_headers = self.current_worksheet.row_values(9)
            logger.info(f"📋 원본 시트 전체 헤더 ({len(source_all_headers)}개): {source_all_headers}")

            # 4. 대상 시트의 헤더 확인 및 필요시 추가
            self.progress_var.set("대상 시트 헤더를 확인하는 중...")
            target_header_row = target_sheet.row_values(9)  # 9행이 헤더

            # 넘버링 제거 함수
            import re
            def clean_header(header_text):
                return re.sub(r'^\d+[\.\s]*', '', str(header_text)).strip()

            # 대상 시트의 헤더를 정리하여 매핑 (빈 열 제외)
            target_headers_cleaned = {}
            for idx, h in enumerate(target_header_row):
                cleaned = clean_header(h)
                if cleaned:  # 빈 문자열이 아닌 경우만 매핑
                    target_headers_cleaned[cleaned] = idx + 1

            # 필요한 열이 없으면 추가 (원본 시트에도 있는 경우만)
            headers_to_add = []
            for col_name in required_columns:
                # 대상 시트에 없고, 원본 시트에는 있는 경우만 추가
                if col_name not in target_headers_cleaned and col_name in source_column_mapping:
                    headers_to_add.append(col_name)

            if headers_to_add:
                logger.info(f"대상 시트에 없는 헤더 추가: {headers_to_add}")
                # 9행 끝에 헤더 추가 (빈 열이 아닌 마지막 열 다음에)
                last_non_empty_col = max(target_headers_cleaned.values()) if target_headers_cleaned else 0
                new_col_count = last_non_empty_col + len(headers_to_add)

                # 시트의 열 개수 확인 및 필요시 확장
                current_col_count = target_sheet.col_count
                if new_col_count > current_col_count:
                    logger.info(f"시트 열 개수 확장: {current_col_count} -> {new_col_count}")
                    target_sheet.resize(rows=target_sheet.row_count, cols=new_col_count)

                for idx, header in enumerate(headers_to_add):
                    target_sheet.update_cell(9, last_non_empty_col + idx + 1, header)

                # 헤더 매핑 갱신
                target_header_row = target_sheet.row_values(9)
                target_headers_cleaned = {}
                for idx, h in enumerate(target_header_row):
                    cleaned = clean_header(h)
                    if cleaned:  # 빈 문자열이 아닌 경우만 매핑
                        target_headers_cleaned[cleaned] = idx + 1

            # 5. 필터링된 데이터에서 필요한 열만 추출
            self.progress_var.set(f"{len(filtered_data)}개의 영상 데이터를 추출하는 중...")

            # 원본 시트와 대상 시트의 헤더 매칭 로그
            logger.info(f"📋 원본 시트 헤더 매핑: {source_column_mapping}")
            logger.info(f"📋 대상 시트 헤더 매핑: {target_headers_cleaned}")

            # 필요한 열이 원본 시트에 있는지 확인
            missing_in_source = [col for col in required_columns if col not in source_column_mapping]
            if missing_in_source:
                logger.warning(f"⚠️ 원본 시트에 없는 필수 열: {missing_in_source}")

            extracted_rows = []
            for row_idx, row_data in enumerate(filtered_data):
                extracted_row_dict = {}

                # 첫 번째 행에서 row_data 길이 확인
                if row_idx == 0:
                    category_id_col = source_column_mapping.get('카테고리 ID', 'N/A')
                    description_col = source_column_mapping.get('디스크립션', 'N/A')
                    logger.info(f"📏 필터링된 데이터 행 길이: {len(row_data)}열, 필요한 열: 카테고리ID({category_id_col}), 디스크립션({description_col})")

                for col_name in required_columns:
                    if col_name in source_column_mapping:
                        col_idx = source_column_mapping[col_name] - 1  # 0-based
                        if col_idx < len(row_data):
                            extracted_row_dict[col_name] = row_data[col_idx]
                        else:
                            extracted_row_dict[col_name] = ''
                            # 카테고리 ID와 디스크립션이 누락되면 경고
                            if row_idx < 3 and col_name in ['카테고리 ID', '디스크립션']:
                                logger.warning(f"⚠️ 행 {row_idx + 1}: '{col_name}' 데이터 누락 (필요 열={col_idx + 1}, 실제 길이={len(row_data)})")
                    else:
                        extracted_row_dict[col_name] = ''

                # 원본 행순서 추가 (정렬 순서 유지용, 1부터 시작)
                extracted_row_dict['원본 행순서'] = row_idx + 1

                # 첫 3개 행만 상세 로그 출력
                if row_idx < 3:
                    category_val = extracted_row_dict.get('카테고리 ID', 'N/A')
                    description_val = extracted_row_dict.get('디스크립션', 'N/A')
                    logger.debug(f"📄 행 {row_idx + 1} 데이터: 영상ID={extracted_row_dict.get('영상 ID', 'N/A')[:20]}, 제목={extracted_row_dict.get('제목', 'N/A')[:30]}, "
                               f"카테고리ID={str(category_val)[:30] if category_val else '(빈값)'}, "
                               f"디스크립션={str(description_val)[:50] if description_val else '(빈값)'}")

                extracted_rows.append(extracted_row_dict)

            logger.info(f"✅ {len(extracted_rows)}개 행 데이터 추출 완료")

            # 6. 대상 시트에서 영상 ID 기준으로 기존 데이터 확인 (10행부터)
            self.progress_var.set("기존 데이터와 비교 중...")

            # 영상 ID 열의 인덱스 찾기
            video_id_col_name = '영상 ID'
            if video_id_col_name not in target_headers_cleaned:
                messagebox.showerror("오류", "대상 시트에 '영상 ID' 열을 찾을 수 없습니다.")
                return

            video_id_col_idx = target_headers_cleaned[video_id_col_name]

            # 대상 시트의 모든 데이터 가져오기 (10행부터)
            all_target_data = target_sheet.get_all_values()
            existing_video_ids = {}

            # 10행부터 데이터가 있는지 확인 (all_target_data는 1행부터 시작하므로 인덱스 9 = 10행)
            last_data_row = 9  # 마지막으로 데이터가 있는 행 번호 (0-based)
            if len(all_target_data) > 9:  # 10행 이상이면
                for row_idx in range(9, len(all_target_data)):  # 10행부터 (0-based로 9부터)
                    row = all_target_data[row_idx]
                    if len(row) > video_id_col_idx - 1:  # 영상 ID 열이 존재하는지 확인
                        video_id = str(row[video_id_col_idx - 1]).strip()
                        if video_id and video_id != '':  # 빈 값이 아닌 경우만
                            existing_video_ids[video_id] = row_idx + 1  # 1-based 행 번호
                            last_data_row = row_idx  # 마지막 데이터 행 업데이트

            logger.info(f"기존 영상 ID {len(existing_video_ids)}개 확인됨")
            if len(existing_video_ids) > 0:
                logger.debug(f"기존 영상 ID 샘플 (최대 5개): {list(existing_video_ids.keys())[:5]}")
                logger.debug(f"마지막 데이터 행: {last_data_row + 1}")

            # 7. 영상 ID 기준으로 업데이트 또는 추가 (벌크 처리)
            # 영상 ID가 있으면 중복 체크 후 업데이트/추가, 없으면 무조건 새로 추가
            self.progress_var.set("데이터를 업데이트 중...")

            update_count = 0
            insert_count = 0

            # 다음 빈 행 번호 찾기 (영상 ID가 있는 마지막 행 + 1, 최소 10행)
            if len(existing_video_ids) > 0:
                # 기존 데이터가 있으면 마지막 데이터 다음 행부터
                next_empty_row = last_data_row + 2  # 1-based 행 번호
            else:
                # 기존 데이터가 없으면 10행부터 시작
                next_empty_row = 10
            logger.info(f"새 데이터 추가 시작 행: {next_empty_row}")

            # 벌크 처리를 위한 데이터 수집
            rows_to_insert = []  # 새로 추가할 행들
            rows_to_update = []  # 업데이트할 행들

            for row_idx, extracted_row_dict in enumerate(extracted_rows):
                video_id = str(extracted_row_dict.get('영상 ID', '')).strip()

                # 행 데이터 생성 (대상 시트의 열 순서대로)
                row_to_write = [''] * len(target_header_row)
                for col_name, value in extracted_row_dict.items():
                    # 첫 3개 행에서 카테고리 ID, 디스크립션 매핑 추적
                    if row_idx < 3 and col_name in ['카테고리 ID', '디스크립션']:
                        in_target = col_name in target_headers_cleaned
                        target_col_idx = target_headers_cleaned.get(col_name, 'N/A')
                        logger.debug(f"🔄 행 {row_idx + 1}: '{col_name}' 매핑, 값='{str(value)[:50] if value else '(빈값)'}', 타겟헤더존재={in_target}, 타겟열번호={target_col_idx}")

                    if col_name in target_headers_cleaned:
                        col_idx = target_headers_cleaned[col_name] - 1  # 0-based
                        row_to_write[col_idx] = value

                # 첫 3개 행만 상세 로그 출력
                if row_idx < 3:
                    video_id_display = video_id[:20] if video_id else "없음"
                    category_id_idx = target_headers_cleaned.get('카테고리 ID', 0) - 1 if '카테고리 ID' in target_headers_cleaned else -1
                    category_id_val = row_to_write[category_id_idx] if category_id_idx >= 0 and category_id_idx < len(row_to_write) else "N/A"
                    description_idx = target_headers_cleaned.get('디스크립션', 0) - 1 if '디스크립션' in target_headers_cleaned else -1
                    description_val = row_to_write[description_idx] if description_idx >= 0 and description_idx < len(row_to_write) else "N/A"
                    logger.debug(f"📝 행 {row_idx + 1}: 영상ID={video_id_display}, 대상 시트 행 데이터 길이={len(row_to_write)}, 카테고리ID값='{str(category_id_val)[:30] if category_id_val else '(빈값)'}', 디스크립션값='{str(description_val)[:50] if description_val else '(빈값)'}'")

                # 영상 ID가 있으면 중복 체크, 없으면 무조건 추가
                if video_id and video_id in existing_video_ids:
                    # 업데이트 대상
                    target_row_num = existing_video_ids[video_id]
                    rows_to_update.append((target_row_num, row_to_write, video_id))
                else:
                    # 신규 추가 대상
                    rows_to_insert.append((row_to_write, video_id))

            # 벌크 업데이트 처리 (기존 영상 업데이트)
            if rows_to_update:
                logger.info(f"🔄 {len(rows_to_update)}개 영상 업데이트 시작...")
                update_row_numbers = []  # 업데이트된 행 번호 추적

                # batch_update를 위한 데이터 준비
                update_data = []
                for target_row_num, row_data, vid in rows_to_update:
                    update_data.append({
                        'range': f'A{target_row_num}',
                        'values': [row_data]
                    })
                    update_row_numbers.append(target_row_num)
                    if update_count < 3:
                        logger.info(f"🔄 영상 ID {vid[:20]}... 업데이트 예정 (행 {target_row_num})")
                    update_count += 1

                # 모든 업데이트를 한 번에 처리 (API 호출 1회)
                target_sheet.batch_update(update_data, value_input_option='USER_ENTERED')
                logger.info(f"✅ 업데이트 완료: {update_count}개")

                # 표시형식 복사 (업데이트된 행들에 대해 - 헤더 매칭 방식)
                if update_row_numbers:
                    try:
                        logger.info(f"📋 업데이트된 행 표시형식 복사 중...")
                        # 연속된 행 범위로 묶어서 처리
                        update_row_numbers.sort()

                        from sheet_config import normalize_header

                        # 원본 시트 헤더 가져오기
                        source_header_row = self.current_worksheet.row_values(1)

                        # 헤더 매칭: 원본 열 → 대상 열 매핑
                        column_mapping = {}  # {원본_열_인덱스: 대상_열_인덱스}
                        for src_idx, src_header in enumerate(source_header_row):
                            normalized_src = normalize_header(src_header)
                            for tgt_idx, tgt_header in enumerate(target_header_row):
                                normalized_tgt = normalize_header(tgt_header)
                                if normalized_src == normalized_tgt:
                                    column_mapping[src_idx] = tgt_idx
                                    break

                        # 각 업데이트된 행에 대해 매칭된 열별로 표시형식 복사
                        requests = []
                        for row_num in update_row_numbers:
                            for src_col_idx, tgt_col_idx in column_mapping.items():
                                requests.append({
                                    'copyPaste': {
                                        'source': {
                                            'sheetId': self.current_worksheet.id,
                                            'startRowIndex': 9,  # 10행 (0-based)
                                            'endRowIndex': 10,   # 10행만
                                            'startColumnIndex': src_col_idx,
                                            'endColumnIndex': src_col_idx + 1
                                        },
                                        'destination': {
                                            'sheetId': target_sheet.id,
                                            'startRowIndex': row_num - 1,  # 0-based
                                            'endRowIndex': row_num,        # 0-based
                                            'startColumnIndex': tgt_col_idx,
                                            'endColumnIndex': tgt_col_idx + 1
                                        },
                                        'pasteType': 'PASTE_FORMAT',
                                        'pasteOrientation': 'NORMAL'
                                    }
                                })

                        spreadsheet.batch_update({'requests': requests})
                        logger.info(f"✅ 업데이트 행 표시형식 복사 완료 (헤더 매칭: {len(column_mapping)}개 열)")
                    except Exception as e:
                        logger.warning(f"⚠️ 업데이트 행 표시형식 복사 중 오류: {e}")

                    # 업데이트된 행의 전역함수 열 데이터 제거
                    try:
                        logger.info(f"📋 업데이트 행 전역함수 열 데이터 제거 중...")

                        from sheet_config import VIDEO_LIST_FORMULA_COLUMNS, normalize_header

                        # 전역함수 열 찾기
                        formula_col_indices = []
                        for idx, header in enumerate(target_header_row):
                            normalized_header = normalize_header(header)
                            for formula_col in VIDEO_LIST_FORMULA_COLUMNS:
                                if normalize_header(formula_col) == normalized_header:
                                    formula_col_indices.append(idx)
                                    break

                        if formula_col_indices:
                            clear_requests = []
                            for row_num in update_row_numbers:
                                for col_idx in formula_col_indices:
                                    clear_requests.append({
                                        'updateCells': {
                                            'range': {
                                                'sheetId': target_sheet.id,
                                                'startRowIndex': row_num - 1,
                                                'endRowIndex': row_num,
                                                'startColumnIndex': col_idx,
                                                'endColumnIndex': col_idx + 1
                                            },
                                            'fields': 'userEnteredValue'
                                        }
                                    })

                            spreadsheet.batch_update({'requests': clear_requests})
                            logger.info(f"✅ 업데이트 행 전역함수 열 데이터 제거 완료")

                    except Exception as e:
                        logger.warning(f"⚠️ 업데이트 행 전역함수 열 제거 중 오류: {e}")

            # 벌크 삽입 처리 (신규 영상 추가) - 한 번에 업데이트
            if rows_to_insert:
                logger.info(f"➕ {len(rows_to_insert)}개 영상 신규 추가 시작...")

                # 모든 행을 한 번에 업데이트 (API 호출 1회로 줄임)
                all_data = [row_data for row_data, _ in rows_to_insert]

                # 시작 행과 끝 행 계산
                start_row = next_empty_row
                end_row = start_row + len(all_data) - 1

                # 범위 업데이트 (A열부터 전체 행)
                # 데이터의 열 개수에 맞춰 범위 지정
                range_notation = f'A{start_row}:{end_row}'
                target_sheet.update(values=all_data, range_name=range_notation, value_input_option='USER_ENTERED')

                # 로그 출력
                for idx, (_, vid) in enumerate(rows_to_insert):
                    insert_count += 1
                    if insert_count <= 3:
                        row_num = start_row + idx
                        if vid:
                            logger.info(f"➕ 영상 ID {vid[:20]}... 신규 추가 (행 {row_num})")
                        else:
                            logger.info(f"➕ 행 {idx + 1}: 영상 ID 없이 신규 추가 (행 {row_num})")

                logger.info(f"✅ 신규 추가 완료: {insert_count}개")

                # 표시형식 복사 (헤더 매칭하여 열별로 복사)
                try:
                    logger.info(f"📋 표시형식 복사 중... (원본 시트 10행 → 대상 시트 {start_row}~{end_row}행, 헤더 매칭)")

                    from sheet_config import normalize_header

                    # 원본 시트 헤더 가져오기
                    source_header_row = self.current_worksheet.row_values(1)

                    # 헤더 매칭: 원본 열 → 대상 열 매핑
                    column_mapping = {}  # {원본_열_인덱스: 대상_열_인덱스}
                    for src_idx, src_header in enumerate(source_header_row):
                        normalized_src = normalize_header(src_header)
                        for tgt_idx, tgt_header in enumerate(target_header_row):
                            normalized_tgt = normalize_header(tgt_header)
                            if normalized_src == normalized_tgt:
                                column_mapping[src_idx] = tgt_idx
                                break

                    logger.info(f"헤더 매칭: {len(column_mapping)}개 열 매칭됨")

                    # 각 매칭된 열에 대해 표시형식 복사
                    format_requests = []
                    for src_col_idx, tgt_col_idx in column_mapping.items():
                        format_requests.append({
                            'copyPaste': {
                                'source': {
                                    'sheetId': self.current_worksheet.id,
                                    'startRowIndex': 9,  # 10행 (0-based)
                                    'endRowIndex': 10,   # 10행만
                                    'startColumnIndex': src_col_idx,
                                    'endColumnIndex': src_col_idx + 1
                                },
                                'destination': {
                                    'sheetId': target_sheet.id,
                                    'startRowIndex': start_row - 1,  # 0-based
                                    'endRowIndex': end_row,          # 0-based
                                    'startColumnIndex': tgt_col_idx,
                                    'endColumnIndex': tgt_col_idx + 1
                                },
                                'pasteType': 'PASTE_FORMAT',  # 형식만 복사
                                'pasteOrientation': 'NORMAL'
                            }
                        })

                    if format_requests:
                        spreadsheet.batch_update({'requests': format_requests})
                        logger.info(f"✅ 표시형식 복사 완료 ({len(format_requests)}개 열)")
                    else:
                        logger.warning("매칭된 열이 없어 표시형식 복사 스킵")

                except Exception as e:
                    logger.warning(f"⚠️ 표시형식 복사 중 오류 (데이터는 정상 추가됨): {e}")

                # 전역함수 열 데이터 제거 (#REF! 오류 방지)
                try:
                    logger.info(f"📋 전역함수 열 데이터 제거 중... ({start_row}~{end_row}행)")

                    # sheet_config에서 전역함수 열 목록 가져오기
                    from sheet_config import VIDEO_LIST_FORMULA_COLUMNS, normalize_header

                    # 대상 시트의 헤더에서 전역함수 열 찾기
                    formula_col_indices = []
                    for idx, header in enumerate(target_header_row):
                        normalized_header = normalize_header(header)
                        for formula_col in VIDEO_LIST_FORMULA_COLUMNS:
                            if normalize_header(formula_col) == normalized_header:
                                formula_col_indices.append(idx)
                                break

                    if formula_col_indices:
                        logger.info(f"전역함수 열 {len(formula_col_indices)}개 발견: 인덱스 {formula_col_indices}")

                        # 각 전역함수 열의 데이터를 빈 값으로 교체
                        clear_requests = []
                        for col_idx in formula_col_indices:
                            clear_requests.append({
                                'updateCells': {
                                    'range': {
                                        'sheetId': target_sheet.id,
                                        'startRowIndex': start_row - 1,  # 0-based
                                        'endRowIndex': end_row,          # 0-based
                                        'startColumnIndex': col_idx,
                                        'endColumnIndex': col_idx + 1
                                    },
                                    'fields': 'userEnteredValue'
                                }
                            })

                        spreadsheet.batch_update({'requests': clear_requests})
                        logger.info(f"✅ 전역함수 열 데이터 제거 완료 (9행 전역함수가 자동 계산)")
                    else:
                        logger.info("전역함수 열이 없음 - 제거 작업 스킵")

                except Exception as e:
                    logger.warning(f"⚠️ 전역함수 열 제거 중 오류: {e}")

            # 원본 시트의 정렬 순서를 따라가도록 '원본 행순서' 열로 정렬
            try:
                from sheet_config import normalize_header

                # '원본 행순서' 열 찾기
                sort_col_name = '원본 행순서'
                sort_col_idx = None
                for idx, header in enumerate(target_header_row):
                    if normalize_header(header) == normalize_header(sort_col_name):
                        sort_col_idx = idx
                        break

                if sort_col_idx is not None:
                    logger.info(f"📊 원본 시트 정렬 순서로 정렬 중... (열: {sort_col_name}, 인덱스: {sort_col_idx})")

                    # 데이터 범위 정렬 (10행부터 마지막 데이터 행까지)
                    # 먼저 현재 마지막 데이터 행 찾기
                    current_data = target_sheet.get_all_values()
                    last_row_with_data = 9  # 기본값: 10행 직전
                    for row_idx in range(9, len(current_data)):  # 10행부터
                        row = current_data[row_idx]
                        # 행에 데이터가 있는지 확인 (빈 행이 아닌지)
                        if any(str(cell).strip() for cell in row):
                            last_row_with_data = row_idx + 1  # 1-based

                    if last_row_with_data >= 10:  # 데이터가 있으면
                        sort_request = {
                            'sortRange': {
                                'range': {
                                    'sheetId': target_sheet.id,
                                    'startRowIndex': 9,  # 10행부터 (0-based)
                                    'endRowIndex': last_row_with_data,  # 마지막 데이터 행까지 (0-based)
                                    'startColumnIndex': 0,
                                    'endColumnIndex': len(target_header_row)
                                },
                                'sortSpecs': [
                                    {
                                        'dimensionIndex': sort_col_idx,  # 정렬 기준 열
                                        'sortOrder': 'ASCENDING'  # 오름차순 (1, 2, 3, ...)
                                    }
                                ]
                            }
                        }
                        spreadsheet.batch_update({'requests': [sort_request]})
                        logger.info(f"✅ 정렬 완료: {sort_col_name} 열 기준 오름차순 (10행~{last_row_with_data}행)")
                    else:
                        logger.info("정렬할 데이터가 없음 (10행 미만)")
                else:
                    logger.warning(f"⚠️ '{sort_col_name}' 열을 찾을 수 없어 정렬 스킵")

            except Exception as e:
                logger.warning(f"⚠️ 정렬 중 오류 (데이터는 정상 추가됨): {e}")

            logger.info(f"✅ AI참고용 데이터 추출 완료: 업데이트 {update_count}개, 신규 추가 {insert_count}개")

            self.progress_var.set(f"완료: 업데이트 {update_count}개, 신규 추가 {insert_count}개")
            messagebox.showinfo("추출 완료",
                              f"AI참고용 데이터 추출이 완료되었습니다.\n\n"
                              f"업데이트: {update_count}개\n"
                              f"신규 추가: {insert_count}개\n"
                              f"대상 시트: '{target_sheet_name}'")

        except Exception as e:
            logger.error(f"❌ AI참고용 데이터 추출 중 오류: {str(e)}")
            self.progress_var.set(f"추출 실패: {str(e)}")
            messagebox.showerror("오류", f"추출 중 오류가 발생했습니다:\n{str(e)}")

        finally:
            self.is_running = False
            self.extract_ai_data_button.config(state='normal', text='AI참고용 데이터추출')
            self.progress_bar.stop()

    def _run_conditional_extraction(self, field1_filters, field2_filters, shortform_filters,
                                  min_views, min_subscribers, channel_names, min_like_ratio,
                                  min_duration, max_upload_days, benchmarking_only, script_exists_only, hook_subtitle_exists_only):
        """조건부 추출 실제 실행 (백그라운드)"""
        try:
            self.is_running = True
            self.extract_button.config(state='disabled', text='추출 중...')
            self.progress_bar.start()
            self.progress_var.set("조건에 맞는 영상 데이터를 추출 중...")

            # 현재 워크시트 확인
            if not self.current_worksheet:
                logger.warning("워크시트가 설정되지 않았습니다.")
                messagebox.showwarning("경고", "먼저 '시트 데이터 불러오기' 버튼을 클릭하여 데이터를 로드해주세요.")
                return

            logger.info("🎯 조건부 추출 시작")
            logger.info(f"📋 적용 조건:")
            logger.info(f"   - 분야1: {field1_filters if field1_filters else '조건 없음'}")
            logger.info(f"   - 분야2: {field2_filters if field2_filters else '조건 없음'}")  
            logger.info(f"   - 숏폼여부: {shortform_filters if shortform_filters else '조건 없음'}")
            logger.info(f"   - 조회수: {f'{min_views:,} 이상' if min_views else '조건 없음'}")
            logger.info(f"   - 구독자수: {f'{min_subscribers:,} 이상' if min_subscribers else '조건 없음'}")
            logger.info(f"   - 채널명: {channel_names if channel_names else '조건 없음'}")
            logger.info(f"   - 좋아요 비율: {f'{min_like_ratio}% 이상' if min_like_ratio else '조건 없음'}")
            logger.info(f"   - 영상길이: {f'{min_duration}초 이상' if min_duration else '조건 없음'}")
            logger.info(f"   - 수집날짜 경과일: {f'{max_upload_days}일 이내' if max_upload_days else '조건 없음'}")
            logger.info(f"   - 영상 업로드날짜: {f'{max_video_upload_days}일 이내' if max_video_upload_days else '조건 없음'}")
            logger.info(f"   - 벤치마킹 채널만: {'예' if benchmarking_only else '아니오'}")
            logger.info(f"   - 대본유무: {'예' if script_exists_only else '아니오'}")
            logger.info(f"   - 후킹자막 유무: {'예' if hook_subtitle_exists_only else '아니오'}")

            # 상위 N개 조건 파싱 (활성화 상태일 때만)
            top_n = None
            top_n_mode = None
            if (hasattr(self, 'enable_top_n_var') and self.enable_top_n_var.get() and
                hasattr(self, 'top_n_var') and self.top_n_var.get().strip()):
                try:
                    top_n = int(self.top_n_var.get().strip())
                except ValueError:
                    pass
            if (hasattr(self, 'enable_top_n_var') and self.enable_top_n_var.get() and
                hasattr(self, 'top_n_mode_var') and self.top_n_mode_var.get() != 'none'):
                top_n_mode = self.top_n_mode_var.get()
                
            # 상위 N개 조건 로그 출력
            if top_n and top_n_mode:
                mode_text = {"channel": "채널별", "field1": "분야1별", "field2": "분야2별"}.get(top_n_mode, "")
                logger.info(f"   - 상위 N개: {mode_text} 상위 {top_n}개")
            
            # ignore_field2 옵션 가져오기
            ignore_field2 = False
            if hasattr(self, 'ignore_field2_var') and top_n_mode == 'field1':
                ignore_field2 = self.ignore_field2_var.get()
            
            # 데이터 추출
            filtered_data = self.sheets_manager.get_filtered_data(
                sheet=self.current_worksheet,
                field1_filters=field1_filters if field1_filters else None,
                field2_filters=field2_filters if field2_filters else None,
                shortform_filters=shortform_filters if shortform_filters else None,
                min_views=min_views,
                min_subscribers=min_subscribers,
                channel_names=channel_names if channel_names else None,
                min_like_ratio=min_like_ratio,
                min_duration=min_duration,
                max_upload_days=max_upload_days,
                max_video_upload_days=max_video_upload_days,
                benchmarking_only=benchmarking_only,
                script_exists_only=script_exists_only,
                hook_subtitle_exists_only=hook_subtitle_exists_only,
                count_only=False,
                top_n=top_n,
                top_n_mode=top_n_mode,
                ignore_field2=ignore_field2
            )
            
            if not filtered_data:
                self.progress_var.set("조건에 맞는 데이터가 없습니다.")
                logger.info("❌ 조건에 맞는 데이터가 없습니다.")
                messagebox.showinfo("결과", "조건에 맞는 데이터가 없습니다.")
            else:
                # 대상 시트에 붙여넣기 (원본 시트 이름 전달)
                success = self.sheets_manager.paste_to_target_sheet(
                    self.current_sheet_url,
                    filtered_data,
                    source_sheet_name=self.current_worksheet.title
                )
                
                if success:
                    self.progress_var.set(f"추출 완료: {len(filtered_data)}개 영상이 '조건 추출 영상' 시트에 추가되었습니다.")
                    logger.info(f"🎉 조건부 추출 완료: {len(filtered_data)}개 영상이 성공적으로 추출되어 저장되었습니다.")
                    messagebox.showinfo("완료", f"{len(filtered_data)}개의 영상이 '조건 추출 영상' 시트에 추가되었습니다.")
                else:
                    self.progress_var.set("데이터 붙여넣기 실패")
                    messagebox.showerror("오류", "데이터 붙여넣기 중 오류가 발생했습니다.")
        
        except Exception as e:
            logger.error(f"❌ 조건부 추출 중 오류: {str(e)}")
            self.progress_var.set(f"오류: {str(e)}")
            messagebox.showerror("오류", f"추출 중 오류가 발생했습니다: {str(e)}")
        
        finally:
            self.is_running = False
            self.extract_button.config(state='normal', text='조건 추출')
            self.progress_bar.stop()

    def refresh_classify_field1_list(self):
        """채널 리스트에서 분야1 목록 새로고침"""
        try:
            logger.info("🔄 채널 리스트에서 분야1 목록 새로고침 시작")

            # 스프레드시트 확인
            if not self.current_sheet_url:
                messagebox.showerror("오류", "먼저 스프레드시트를 선택해주세요.")
                return

            # 진행률 표시
            self.update_progress("채널 리스트에서 분야1 목록 불러오는 중...")
            self.progress_bar.start()

            # 분야1 목록 가져오기
            field1_counts = self.sheets_manager.get_field1_list_from_channel_list(self.current_sheet_url)

            # 체크박스 프레임 초기화
            for widget in self.classify_field1_frame.winfo_children():
                widget.destroy()

            self.classify_field1_checkboxes.clear()

            if not field1_counts:
                ttk.Label(self.classify_field1_frame, text="분야1 데이터가 없습니다.",
                         foreground='gray').pack(anchor='w', padx=5, pady=5)
                self.progress_bar.stop()
                return

            # 분야1별로 체크박스 생성 (정렬)
            for field1_value in sorted(field1_counts.keys()):
                count = field1_counts[field1_value]
                var = tk.BooleanVar(value=False)

                checkbox = ttk.Checkbutton(self.classify_field1_frame,
                                          text=f"{field1_value} ({count}개 채널)",
                                          variable=var)
                checkbox.pack(anchor='w', padx=5, pady=2)

                self.classify_field1_checkboxes[field1_value] = (var, count)

            logger.info(f"✅ 분야1 체크박스 생성 완료: {len(field1_counts)}개")
            self.update_progress(f"✅ 분야1 목록 불러오기 완료: {len(field1_counts)}개")
            self.progress_bar.stop()

            messagebox.showinfo("완료", f"{len(field1_counts)}개 분야1 목록을 불러왔습니다.")

        except Exception as e:
            logger.error(f"❌ 분야1 목록 새로고침 실패: {str(e)}")
            self.progress_bar.stop()
            messagebox.showerror("오류", f"분야1 목록을 불러오는 중 오류가 발생했습니다:\n{str(e)}")

    def toggle_all_classify_field1(self):
        """전체 선택/해제"""
        select_all = self.classify_select_all_var.get()

        for field1_value, (var, count) in self.classify_field1_checkboxes.items():
            var.set(select_all)

        logger.info(f"{'✅ 전체 선택' if select_all else '❌ 전체 해제'}: {len(self.classify_field1_checkboxes)}개")

    def execute_classify_channels(self):
        """체크된 분야1들에 대해 채널 분류 실행"""
        if self.is_running:
            messagebox.showwarning("경고", "다른 작업이 실행 중입니다.")
            return

        # 체크된 분야1 목록 추출
        selected_field1s = []
        for field1_value, (var, count) in self.classify_field1_checkboxes.items():
            if var.get():
                selected_field1s.append((field1_value, count))

        if not selected_field1s:
            messagebox.showerror("오류", "분류할 분야1을 하나 이상 선택해주세요.")
            return

        # 확인 대화상자
        field1_list_str = "\n".join([f"  • {field1} ({count}개 채널)" for field1, count in selected_field1s])
        if not messagebox.askyesno("채널 분류",
                                  f"선택한 {len(selected_field1s)}개 분야1에 대해 채널 분류를 실행하시겠습니까?\n\n"
                                  f"{field1_list_str}\n\n"
                                  f"[처리 내용]\n"
                                  f"• 각 분야1별로 '{{분야1}}-채널' 시트로 분류\n"
                                  f"• 시트가 없으면 자동 생성 (채널 리스트 복제, 서식 유지)\n"
                                  f"• '가져왔는지 여부'가 빈 행이 아닌 채널만 처리\n"
                                  f"• 채널 ID 기준 중복 체크 (중복=업데이트, 신규=추가)"):
            return

        # 분류 실행
        self.is_running = True
        self.classify_execute_button.config(state='disabled', text='분류 중...')
        self.classify_result_var.set("처리 중...")
        self.update_progress(f"채널 분류 중: {len(selected_field1s)}개 분야")
        self.progress_bar.start()

        # 별도 스레드에서 실행
        thread = threading.Thread(target=self._run_batch_channel_classification,
                                 args=([f1 for f1, _ in selected_field1s],))
        thread.daemon = True
        thread.start()

    def _classify_progress(self, current, total, message):
        """채널 분류 진행률 콜백"""
        def update():
            self.progress_var.set(message)
        self.root.after(0, update)

    def _run_batch_channel_classification(self, field1_list):
        """체크된 분야1들에 대해 순차적으로 채널 분류 실행"""
        try:
            total_results = {
                'success': [],
                'failed': [],
                'total_added': 0,
                'total_updated': 0,
                'sheets_created': []
            }

            for idx, field1_name in enumerate(field1_list, 1):
                try:
                    self.root.after(0, lambda i=idx, f=field1_name: self.update_progress(
                        f"[{i}/{len(field1_list)}] 채널 분류 중: {f}"))

                    logger.info(f"[{idx}/{len(field1_list)}] 채널 분류 시작: {field1_name}")

                    # 분류 실행
                    added_count, updated_count, sheet_created = self.sheets_manager.classify_channels_to_field1_sheet(
                        self.current_sheet_url,
                        field1_name,
                        progress_callback=self._classify_progress
                    )

                    total_results['success'].append(field1_name)
                    total_results['total_added'] += added_count
                    total_results['total_updated'] += updated_count

                    if sheet_created:
                        total_results['sheets_created'].append(field1_name)

                    logger.info(f"✅ [{idx}/{len(field1_list)}] '{field1_name}' 완료: 추가 {added_count}개, 업데이트 {updated_count}개")

                except Exception as e:
                    logger.error(f"❌ [{idx}/{len(field1_list)}] '{field1_name}' 실패: {str(e)}")
                    total_results['failed'].append((field1_name, str(e)))

            # 완료 메시지 생성
            result_msg = f"✅ 성공: {len(total_results['success'])}개"
            if total_results['failed']:
                result_msg += f" | ❌ 실패: {len(total_results['failed'])}개"
            result_msg += f"\n총 추가: {total_results['total_added']}개 | 총 업데이트: {total_results['total_updated']}개"

            self.root.after(0, lambda: self.classify_result_var.set(result_msg))
            self.root.after(0, lambda: self.update_progress("✅ 채널 분류 완료"))

            # 상세 메시지
            complete_msg = f"채널 분류가 완료되었습니다.\n\n"
            complete_msg += f"✅ 성공: {len(total_results['success'])}개 분야1\n"
            if total_results['failed']:
                complete_msg += f"❌ 실패: {len(total_results['failed'])}개 분야1\n"
            complete_msg += f"\n총 추가된 행: {total_results['total_added']}개\n"
            complete_msg += f"총 업데이트된 행: {total_results['total_updated']}개\n"

            if total_results['sheets_created']:
                complete_msg += f"\n✨ 새로 생성된 시트:\n"
                for field1 in total_results['sheets_created']:
                    complete_msg += f"  • {field1}-채널\n"

            if total_results['failed']:
                complete_msg += f"\n⚠️ 실패한 분야1:\n"
                for field1, error in total_results['failed']:
                    complete_msg += f"  • {field1}: {error[:50]}...\n"

            self.root.after(0, lambda: messagebox.showinfo("완료", complete_msg))

        except Exception as e:
            logger.error(f"❌ 채널 분류 실행 실패: {str(e)}")
            self.root.after(0, lambda: self.classify_result_var.set(f"❌ 실패: {str(e)}"))
            self.root.after(0, lambda: self.update_progress(f"채널 분류 실패: {str(e)}"))
            self.root.after(0, lambda: messagebox.showerror(
                "오류",
                f"채널 분류 중 오류가 발생했습니다:\n{str(e)}"))

        finally:
            self.is_running = False
            self.root.after(0, lambda: self.classify_execute_button.config(state='normal', text='선택한 분야1 채널 분류 실행'))
            self.root.after(0, lambda: self.progress_bar.stop())
    def copy_to_ai_spreadsheet(self):
        """선택한 시트를 AI참고용 스프레드시트로 복제"""
        # 시트 선택 확인
        source_sheet_name = self.copy_sheet_var.get()
        if not source_sheet_name:
            messagebox.showwarning("경고", "복사할 시트를 선택하세요.")
            return

        # 확인 메시지
        header_option = "9행을 헤더로 (1-8행 삭제)" if self.copy_header_row9_var.get() else "전체 행 복사"
        if not messagebox.askyesno("AI참고용 시트 복제",
                                  f"'{source_sheet_name}' 시트를 AI참고용 스프레드시트로 복제하시겠습니까?\n\n"
                                  f"옵션: {header_option}\n"
                                  f"• 함수는 값으로 변환됩니다\n"
                                  f"• 표시형식은 유지됩니다\n"
                                  f"• 동일한 이름의 시트가 있으면 덮어씁니다"):
            return

        # 백그라운드에서 실행
        thread = threading.Thread(target=self._run_copy_to_ai_spreadsheet,
                                 args=(source_sheet_name,))
        thread.daemon = True
        thread.start()

    def _run_copy_to_ai_spreadsheet(self, source_sheet_name):
        """AI참고용 스프레드시트로 복제 실제 실행 (백그라운드)"""
        try:
            self.copy_to_ai_button.config(state='disabled', text='복제 중...')
            self.progress_bar.start()
            self.progress_var.set("시트를 복제하는 중...")

            logger.info(f"🎯 AI참고용 스프레드시트로 시트 복제 시작: {source_sheet_name}")

            # 1. 원본 스프레드시트와 시트 가져오기
            source_spreadsheet = self.sheets_manager.client.open_by_url(self.current_sheet_url)
            source_sheet = source_spreadsheet.worksheet(source_sheet_name)

            # 2. 대상 스프레드시트 열기
            target_spreadsheet_url = "https://docs.google.com/spreadsheets/d/1QjIbjkxwq6LI1ZxreQ9K3jExXRQYSdaX2cJ5GbhPSCQ/edit?usp=sharing"
            target_spreadsheet = self.sheets_manager.client.open_by_url(target_spreadsheet_url)

            logger.info(f"대상 스프레드시트: {target_spreadsheet.title}")

            # 3. 행 범위 계산
            start_row = 9 if self.copy_header_row9_var.get() else 1

            # 영상 갯수 제한
            try:
                video_count = int(self.copy_video_count_var.get())
                if video_count > 0:
                    end_row = start_row + video_count
                else:
                    end_row = source_sheet.row_count
            except ValueError:
                logger.warning(f"잘못된 영상 갯수 입력: {self.copy_video_count_var.get()}, 전체 복사")
                end_row = source_sheet.row_count

            logger.info(f"복사 범위: {start_row}행 ~ {end_row}행")

            # 4. 원본 시트에서 데이터와 서식 가져오기
            self.progress_var.set("데이터 및 서식을 읽는 중...")

            # 데이터 가져오기 (수식 포함)
            range_name = f"'{source_sheet_name}'!A{start_row}:{self._get_column_letter(source_sheet.col_count)}{end_row}"
            data_response = source_spreadsheet.values_get(range_name, params={'valueRenderOption': 'FORMULA'})
            all_values = data_response.get('values', [])

            if not all_values:
                raise Exception("복사할 데이터가 없습니다")

            logger.info(f"데이터 읽기 완료: {len(all_values)}행 x {len(all_values[0]) if all_values else 0}열")

            # 서식 가져오기 (Google Sheets API 직접 호출)
            try:
                # Google Sheets API v4 서비스 객체 생성
                # sheets_manager의 client에서 credentials 가져오기
                credentials = self.sheets_manager.client.auth
                service = build('sheets', 'v4', credentials=credentials)

                # 원본 시트의 ID 찾기
                source_sheet_id = None
                for sheet in source_spreadsheet.worksheets():
                    if sheet.title == source_sheet_name:
                        source_sheet_id = sheet.id
                        break

                # spreadsheet.id와 range를 사용하여 서식 정보 가져오기 (셀 서식 + 열/행 메타데이터)
                data_with_format = service.spreadsheets().get(
                    spreadsheetId=source_spreadsheet.id,
                    ranges=[range_name],
                    fields='sheets(properties,data(rowData(values(userEnteredFormat,userEnteredValue)),columnMetadata,rowMetadata))'
                ).execute()
            except Exception as e:
                logger.warning(f"서식 정보 가져오기 실패, 기본 서식만 적용: {str(e)}")
                data_with_format = None
                source_sheet_id = None

            # 5. 대상 시트 확인 및 생성/재사용
            self.progress_var.set("대상 시트를 준비하는 중...")
            rows_to_copy = len(all_values)
            cols_count = max(len(row) for row in all_values) if all_values else 1

            # 대상 스프레드시트에 동일한 이름의 시트가 있는지 확인
            target_sheet = None
            sheet_exists = False
            try:
                target_sheet = target_spreadsheet.worksheet(source_sheet_name)
                sheet_exists = True
                logger.info(f"기존 시트 '{source_sheet_name}' 발견 - 내용을 덮어씁니다")

                # 기존 시트의 크기 조정 (필요한 경우)
                if target_sheet.row_count < rows_to_copy or target_sheet.col_count < cols_count:
                    target_sheet.resize(rows=max(rows_to_copy, target_sheet.row_count),
                                       cols=max(cols_count, target_sheet.col_count))
                    logger.info(f"시트 크기 조정: {rows_to_copy}행 x {cols_count}열")

                # 기존 내용 삭제 (전체 시트 클리어)
                target_sheet.clear()
                logger.info(f"기존 시트 내용 삭제 완료")
            except:
                logger.info(f"새로운 시트 '{source_sheet_name}' 생성")
                target_sheet = target_spreadsheet.add_worksheet(
                    title=source_sheet_name,
                    rows=rows_to_copy,
                    cols=cols_count
                )

            # 7. 데이터 쓰기
            self.progress_var.set(f"{rows_to_copy}개 행을 복사하는 중...")
            end_col_letter = self._get_column_letter(cols_count)
            target_range = f'A1:{end_col_letter}{rows_to_copy}'
            target_sheet.update(target_range, all_values, value_input_option='USER_ENTERED')
            logger.info(f"데이터 복사 완료: {target_range}")

            # 8. 서식 복사
            self.progress_var.set("서식을 복사하는 중...")
            try:
                requests = []

                # 8-1. 원본 시트의 셀 서식 가져오기
                if data_with_format:
                    source_sheet_data = data_with_format.get('sheets', [{}])[0].get('data', [{}])[0]
                    row_data_list = source_sheet_data.get('rowData', [])

                    if row_data_list:
                        # 각 셀의 서식을 batchUpdate로 적용
                        for row_idx, row_data in enumerate(row_data_list):
                            cell_data_list = row_data.get('values', [])
                            for col_idx, cell_data in enumerate(cell_data_list):
                                user_format = cell_data.get('userEnteredFormat')
                                if user_format:
                                    # 셀 서식 업데이트 요청
                                    requests.append({
                                        'repeatCell': {
                                            'range': {
                                                'sheetId': target_sheet.id,
                                                'startRowIndex': row_idx,
                                                'endRowIndex': row_idx + 1,
                                                'startColumnIndex': col_idx,
                                                'endColumnIndex': col_idx + 1
                                            },
                                            'cell': {
                                                'userEnteredFormat': user_format
                                            },
                                            'fields': 'userEnteredFormat'
                                        }
                                    })

                                    # 배치 크기 제한 (한번에 너무 많은 요청 방지)
                                    if len(requests) >= 1000:
                                        target_spreadsheet.batch_update({'requests': requests})
                                        requests = []
                                        logger.info(f"서식 복사 진행 중... ({row_idx + 1}/{len(row_data_list)} 행)")

                # 8-2. 열 너비 복사
                if data_with_format:
                    source_sheet_data = data_with_format.get('sheets', [{}])[0].get('data', [{}])[0]
                    column_metadata = source_sheet_data.get('columnMetadata', [])

                    if column_metadata:
                        logger.info(f"열 메타데이터 개수: {len(column_metadata)}")
                        for col_idx, col_meta in enumerate(column_metadata):
                            # 열 인덱스는 행과 무관하게 0부터 시작
                            if col_idx < cols_count and 'pixelSize' in col_meta:
                                pixel_size = col_meta['pixelSize']
                                logger.debug(f"열 {col_idx} 너비: {pixel_size}px")
                                requests.append({
                                    'updateDimensionProperties': {
                                        'range': {
                                            'sheetId': target_sheet.id,
                                            'dimension': 'COLUMNS',
                                            'startIndex': col_idx,
                                            'endIndex': col_idx + 1
                                        },
                                        'properties': {
                                            'pixelSize': pixel_size
                                        },
                                        'fields': 'pixelSize'
                                    }
                                })
                        logger.info(f"열 너비 복사: {len([r for r in requests if 'updateDimensionProperties' in r and r['updateDimensionProperties']['range']['dimension'] == 'COLUMNS'])}개 열")
                    else:
                        logger.warning("열 메타데이터를 찾을 수 없습니다")

                # 8-3. 행 높이를 31로 고정
                for row_idx in range(rows_to_copy):
                    requests.append({
                        'updateDimensionProperties': {
                            'range': {
                                'sheetId': target_sheet.id,
                                'dimension': 'ROWS',
                                'startIndex': row_idx,
                                'endIndex': row_idx + 1
                            },
                            'properties': {
                                'pixelSize': 31
                            },
                            'fields': 'pixelSize'
                        }
                    })

                # 남은 요청 실행
                if requests:
                    target_spreadsheet.batch_update({'requests': requests})
                    logger.info(f"서식 복사 완료: 폰트, 색상, 정렬, 행 높이(31), 열 너비")

            except Exception as e:
                logger.warning(f"서식 복사 중 오류 (데이터는 정상 복사됨): {str(e)}")

            logger.info(f"✅ AI참고용 스프레드시트로 시트 복제 완료: {source_sheet_name}")
            self.copy_result_var.set(f"✅ '{source_sheet_name}' 시트 복제 완료")
            self.progress_var.set(f"완료: {source_sheet_name} 시트 복제 완료")
            messagebox.showinfo("복제 완료",
                              f"'{source_sheet_name}' 시트가 AI참고용 스프레드시트로 복제되었습니다.\n\n"
                              f"복사된 행: {rows_to_copy}개")

        except Exception as e:
            logger.error(f"❌ AI참고용 스프레드시트로 시트 복제 중 오류: {str(e)}")
            self.copy_result_var.set(f"❌ 복제 실패: {str(e)}")
            self.progress_var.set(f"복제 실패: {str(e)}")
            messagebox.showerror("오류", f"시트 복제 중 오류가 발생했습니다:\n{str(e)}")

        finally:
            self.copy_to_ai_button.config(state='normal', text='AI참고용 시트에 복제')
            self.progress_bar.stop()

    def _get_column_letter(self, col_num):
        """열 번호를 A, B, C, ... Z, AA, AB, ... 형식으로 변환"""
        result = ""
        while col_num > 0:
            col_num -= 1
            result = chr(col_num % 26 + ord('A')) + result
            col_num //= 26
        return result


def main():
    root = tk.Tk()
    
    # 스타일 설정
    style = ttk.Style()
    if "vista" in style.theme_names():
        style.theme_use("vista")
    elif "clam" in style.theme_names():
        style.theme_use("clam")
    
    app = TranscriptExtractorGUI(root)
    
    # GUI 종료 시 프로그램도 완전히 종료되도록 설정
    def on_closing():
        try:
            root.destroy()
        except:
            pass
        import sys
        sys.exit(0)
    
    root.protocol("WM_DELETE_WINDOW", on_closing)
    root.mainloop()

if __name__ == "__main__":
    # Windows 콘솔 인코딩 설정
    import sys
    if sys.platform == "win32":
        try:
            import os
            os.system("chcp 65001 > nul")
        except:
            pass
    
    # 필요한 패키지 안내 (콘솔 출력 제거)
    required_packages = [
        "aiohttp",
        "gspread", 
        "google-auth",
        "google-auth-oauthlib",
        "google-auth-httplib2",
        "brotli"  # Brotli 압축 지원용
    ]
    
    # GUI 실행
    main()