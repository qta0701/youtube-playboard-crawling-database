import asyncio
import aiohttp
import json
import re
import time
import random
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass
from urllib.parse import urlparse, parse_qs
import logging
from pathlib import Path

# YouTube Transcript API (공식 라이브러리 - 2025 InnerTube API 지원)
try:
    from youtube_transcript_api import YouTubeTranscriptApi
    from youtube_transcript_api._errors import TranscriptsDisabled, NoTranscriptFound, VideoUnavailable
    YOUTUBE_TRANSCRIPT_API_AVAILABLE = True
except ImportError:
    YOUTUBE_TRANSCRIPT_API_AVAILABLE = False
    # logger는 나중에 정의되므로 여기서는 warning 출력 안 함

# 브라우저 자동화 관련 import
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, WebDriverException
from webdriver_manager.chrome import ChromeDriverManager
import threading
import keyboard
import signal
import sys

# Google Sheets API
import gspread
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaInMemoryUpload
import io

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

# 콘솔 핸들러 (DEBUG 이상으로 변경)
console_handler = logging.StreamHandler()
console_handler.setLevel(logging.DEBUG)
console_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))

# 핸들러 추가
logger.addHandler(file_handler)
logger.addHandler(console_handler)

# youtube-transcript-api 라이브러리 체크
if not YOUTUBE_TRANSCRIPT_API_AVAILABLE:
    logger.warning("=" * 80)
    logger.warning("⚠️  youtube-transcript-api 라이브러리를 찾을 수 없습니다")
    logger.warning("   YouTube가 2025년부터 PoToken(Proof of Origin Token)을 요구합니다")
    logger.warning("   기존 HTTP 방식은 빈 응답을 반환할 수 있습니다")
    logger.warning("")
    logger.warning("📌 해결 방법: 다음 명령어를 실행하세요")
    logger.warning("   pip install youtube-transcript-api")
    logger.warning("=" * 80)
else:
    logger.info("✅ youtube-transcript-api 라이브러리가 로드되었습니다 (InnerTube API 지원)")

# 전역 중단 제어 변수
class InterruptController:
    def __init__(self):
        self.should_stop = False
        self.esc_count = 0
        self.processed_count = 0
        self.success_count = 0
        self.error_count = 0
        
    def reset_stats(self):
        self.processed_count = 0
        self.success_count = 0
        self.error_count = 0

interrupt_controller = InterruptController()

@dataclass
class TranscriptConfig:
    """자막 추출 설정"""
    target_language: Optional[str] = None  # None이면 자동 감지, 'ko', 'en', 'ja' 등
    max_concurrent: int = 5  # 동시 처리 수
    retry_attempts: int = 1
    delay_between_requests: float = 1.0  # 요청 간 지연시간 (초)
    use_browser_automation: bool = True  # 브라우저 자동화 사용 여부
    headless: bool = True  # 헤드리스 모드 사용 여부
    use_user_profile: bool = False  # 사용자 Chrome 프로필 사용 여부

@dataclass
class VideoData:
    """비디오 데이터"""
    video_id: str
    title: str = ""
    transcript: List[Tuple[str, str]] = None  # [(timestamp, text), ...]
    language: str = ""
    error: str = ""

class BrowserTranscriptExtractor:
    """브라우저 자동화를 통한 자막 추출기"""
    
    def __init__(self, config: TranscriptConfig):
        self.config = config
        self.driver = None
        self._lock = threading.Lock()
        
    def __enter__(self):
        """컨텍스트 매니저 진입"""
        self.start_browser()
        return self
        
    def __exit__(self, exc_type, exc_val, exc_tb):
        """컨텍스트 매니저 종료"""
        self.close_browser()
    
    def start_browser(self):
        """브라우저 시작"""
        if self.driver:
            return
            
        try:
            logger.info("🚀 브라우저 자동화 시작...")
            
            # Chrome 옵션 설정
            chrome_options = Options()
            
            if self.config.headless:
                chrome_options.add_argument("--headless=new")
                logger.info("📱 헤드리스 모드로 실행")
            else:
                logger.info("🖥️  GUI 모드로 실행")
            
            # 성능 최적화 옵션
            chrome_options.add_argument("--no-sandbox")
            chrome_options.add_argument("--disable-dev-shm-usage")
            chrome_options.add_argument("--disable-blink-features=AutomationControlled")
            chrome_options.add_experimental_option("excludeSwitches", ["enable-automation"])
            chrome_options.add_experimental_option('useAutomationExtension', False)
            
            # 리소스 절약을 위한 설정 (JavaScript는 자막 추출에 필요하므로 활성화)
            chrome_options.add_argument("--disable-images")
            chrome_options.add_argument("--disable-plugins")
            chrome_options.add_argument("--disable-extensions")
            # 광고 차단
            chrome_options.add_argument("--disable-popup-blocking")
            chrome_options.add_argument("--disable-background-timer-throttling")
            # 음량 50% 설정 (브라우저에서 자동 재생 소리 제한)
            chrome_options.add_argument("--autoplay-policy=user-gesture-required")
            chrome_options.add_argument("--disable-background-media-suspend")
            
            # 사용자 프로필 사용 설정
            if self.config.use_user_profile:
                import os
                user_data_dir = os.path.expanduser("~\\AppData\\Local\\Google\\Chrome\\User Data")
                if os.path.exists(user_data_dir):
                    chrome_options.add_argument(f"--user-data-dir={user_data_dir}")
                    chrome_options.add_argument("--profile-directory=Default")
                    logger.info("👤 사용자 Chrome 프로필 사용")
                else:
                    logger.warning("⚠️  사용자 Chrome 프로필을 찾을 수 없음, 임시 프로필 사용")
            
            # WebDriver 생성 (최신 ChromeDriver 자동 다운로드)
            logger.info("📥 ChromeDriver 확인 및 업데이트 중...")
            chrome_service = webdriver.chrome.service.Service(
                ChromeDriverManager().install()
            )
            
            self.driver = webdriver.Chrome(
                service=chrome_service,
                options=chrome_options
            )
            
            # 브라우저 설정
            self.driver.set_page_load_timeout(30)
            self.driver.implicitly_wait(10)
            
            # User-Agent 설정
            self.driver.execute_cdp_cmd('Network.setUserAgentOverride', {
                "userAgent": 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36'
            })
            
            logger.info("✅ 브라우저 자동화 초기화 완료")
            
        except Exception as e:
            logger.exception("❌ 브라우저 시작 실패:")
            raise Exception(f"브라우저 초기화 실패: {str(e)}")
    
    def close_browser(self):
        """브라우저 종료"""
        if self.driver:
            try:
                self.driver.quit()
                logger.info("🔚 브라우저 종료 완료")
            except Exception as e:
                logger.warning(f"⚠️  브라우저 종료 중 오류: {e}")
            finally:
                self.driver = None
    
    def extract_transcript_from_video(self, video_id: str) -> VideoData:
        """단일 비디오에서 자막 추출"""
        video_data = VideoData(video_id=video_id)
        
        if not self.driver:
            video_data.error = "브라우저가 초기화되지 않음"
            return video_data
        
        # 일반 YouTube URL만 사용 (Shorts URL은 실패가 많아서 제외)
        url = f"https://www.youtube.com/watch?v={video_id}"
        
        try:
            logger.info(f"🔍 {video_id}: 일반 YouTube URL로 시도")
            
            # 페이지 로드
            self.driver.get(url)
            
            # 페이지 로드 대기
            WebDriverWait(self.driver, 15).until(
                EC.presence_of_element_located((By.TAG_NAME, "body"))
            )
            
            # 잠시 대기 (JavaScript 실행 완료 대기)
            time.sleep(2)
            
            # JavaScript로 자막 추출 시도
            result = self._extract_transcript_with_javascript(video_id)
            
            if result['success']:
                video_data.title = result['title']
                video_data.transcript = result['transcript']
                video_data.language = result.get('language', 'unknown')
                logger.info(f"✅ {video_id}: 일반 YouTube에서 {len(result['transcript'])}개 세그먼트 추출 성공")
                return video_data
            else:
                video_data.error = result['error']
                logger.warning(f"⚠️  {video_id}: 일반 YouTube에서 자막 추출 실패 - {result['error']}")
                    
        except TimeoutException:
            video_data.error = "페이지 로드 타임아웃"
            logger.warning(f"⏰ {video_id}: 페이지 로드 타임아웃")
        except Exception as e:
            video_data.error = str(e)
            logger.warning(f"❌ {video_id}: 처리 중 오류 - {e}")
        
        return video_data
    
    def _extract_transcript_with_javascript(self, video_id: str) -> dict:
        """JavaScript를 사용하여 자막 추출 (확장프로그램과 동일한 로직)"""
        try:
            # 확장프로그램의 JavaScript 코드를 그대로 사용
            js_script = """
            function extractJsonFromHtml(html, key) {
                const regexes = [
                    new RegExp(`window\\\\["${key}"\\\\]\\\\s*=\\\\s*({[\\\\s\\\\S]+?})\\\\s*;`),
                    new RegExp(`var ${key}\\\\s*=\\\\s*({[\\\\s\\\\S]+?})\\\\s*;`),
                    new RegExp(`${key}\\\\s*=\\\\s*({[\\\\s\\\\S]+?})\\\\s*;`)
                ];

                for (const regex of regexes) {
                    const match = html.match(regex);
                    if (match && match[1]) {
                        try {
                            return JSON.parse(match[1]);
                        } catch (err) {
                            console.warn(`⚠️ Failed to parse ${key}:`, err.message);
                        }
                    }
                }
                return null;
            }

            function msToTimestamp(ms) {
                const totalSec = Math.floor(ms / 1000);
                const min = Math.floor(totalSec / 60);
                const sec = totalSec % 60;
                return `${min}:${sec.toString().padStart(2, "0")}`;
            }

            function getShortsSegmentData(event) {
                const timestamp = msToTimestamp(event.tStartMs);
                const text = (event.segs || []).map(seg => seg.utf8).join(" ").replace(/\\n/g, " ");
                return [timestamp, text];
            }

            function getSegmentData(item) {
                const seg = item?.transcriptSegmentRenderer;
                if (!seg) return ["", ""];
                const timestamp = seg.startTimeText?.simpleText || "";
                const text = seg.snippet?.runs?.map(r => r.text).join(" ") || "";
                return [timestamp, text];
            }

            async function getTranscriptItems(ytData, dataKey) {
                if (dataKey === "ytInitialPlayerResponse") {
                    const baseUrl = ytData?.captions?.playerCaptionsTracklistRenderer?.captionTracks?.[0]?.baseUrl;
                    if (!baseUrl) throw new Error("Transcript not available for this video.");
                    const captionUrl = baseUrl + "&fmt=json3";
                    try {
                        const response = await fetch(captionUrl);
                        if (!response.ok) throw new Error(`Fetch failed with status: ${response.status}`);
                        const json = await response.json();
                        return json.events || [];
                    } catch (e) {
                        console.error("Error fetching or parsing transcript from baseUrl:", e);
                        throw new Error("Transcript not available for this video.");
                    }
                }

                const continuationParams = ytData.engagementPanels?.find(p =>
                    p.engagementPanelSectionListRenderer?.content?.continuationItemRenderer?.continuationEndpoint?.getTranscriptEndpoint
                )?.engagementPanelSectionListRenderer?.content?.continuationItemRenderer?.continuationEndpoint?.getTranscriptEndpoint?.params;

                if (!continuationParams) throw new Error("Transcript not available for this video");

                const hl = ytData.topbar?.desktopTopbarRenderer?.searchbox?.fusionSearchboxRenderer?.config?.webSearchboxConfig?.requestLanguage || "en";
                const clientData = ytData.responseContext?.serviceTrackingParams?.[0]?.params;
                const visitorData = ytData.responseContext?.webResponseContextExtensionData?.ytConfigData?.visitorData;

                const body = {
                    context: {
                        client: {
                            hl,
                            visitorData,
                            clientName: clientData?.[0]?.value,
                            clientVersion: clientData?.[1]?.value
                        },
                        request: { useSsl: true }
                    },
                    params: continuationParams
                };

                const res = await fetch("https://www.youtube.com/youtubei/v1/get_transcript?prettyPrint=false", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify(body)
                });

                const json = await res.json();
                return json.actions?.[0]?.updateEngagementPanelAction?.content?.transcriptRenderer
                    ?.content?.transcriptSearchPanelRenderer?.body?.transcriptSegmentListRenderer?.initialSegments || [];
            }

            async function resolveYouTubeData(videoUrl, initialType) {
                const dataKey = "ytInitialData";
                const html = document.documentElement.outerHTML;
                let ytData = extractJsonFromHtml(html, dataKey);

                let title = ytData?.videoDetails?.title || 
                          ytData?.playerOverlays?.playerOverlayRenderer?.videoDetails?.playerOverlayVideoDetailsRenderer?.title?.simpleText ||
                          "제목 없음";

                const panels = ytData?.engagementPanels || [];
                const hasTranscriptPanel = panels.some(p =>
                    p.engagementPanelSectionListRenderer?.content?.continuationItemRenderer?.continuationEndpoint?.getTranscriptEndpoint
                );
            
                if (!hasTranscriptPanel) {
                    const fallbackData = extractJsonFromHtml(html, "ytInitialPlayerResponse");
                    return {
                        title: title || fallbackData?.videoDetails?.title || "제목 없음",
                        ytData: fallbackData,
                        dataKey: "ytInitialPlayerResponse",
                        resolvedType: "shorts"
                    };
                }

                return {
                    title,
                    ytData,
                    dataKey,
                    resolvedType: "regular"
                };
            }

            function createTranscriptArray(items, type) {
                return type === "regular"
                    ? items.map(item => getSegmentData(item))
                    : items.filter(e => e.segs).map(e => getShortsSegmentData(e));
            }

            // 메인 실행 함수
            async function extractTranscript() {
                try {
                    const videoUrl = window.location.href;
                    const { title, ytData, dataKey, resolvedType } = await resolveYouTubeData(videoUrl, "regular");
                    const segments = await getTranscriptItems(ytData, dataKey);
                    if (!segments.length) return { success: false, error: "자막 세그먼트가 없음", title, transcript: [] };
                    const transcript = createTranscriptArray(segments, resolvedType);
                    return { success: true, title, transcript, language: "unknown" };
                } catch (error) {
                    return { success: false, error: error.message, title: "제목 없음", transcript: [] };
                }
            }
            """
            
            # JavaScript 실행 (함수 즉시 실행)
            full_script = f"""
            {js_script}
            return extractTranscript();
            """

            # Promise를 동기적으로 처리하기 위해 async script 사용
            promise_script = f"""
            {js_script}
            const callback = arguments[arguments.length - 1];
            extractTranscript().then(callback).catch(err => callback({{success: false, error: err.message}}));
            """

            try:
                result = self.driver.execute_async_script(promise_script)
            except Exception as e:
                # async script 실패 시 동기 방식으로 재시도
                result = self.driver.execute_script(full_script)

            return result
            
        except Exception as e:
            logger.exception(f"{video_id}: JavaScript 실행 실패:")
            return {
                'success': False,
                'error': f'JavaScript 실행 실패: {str(e)}',
                'title': '제목 없음',
                'transcript': []
            }
    
    def process_videos_batch(self, video_ids: List[str], sheets_manager=None, sheet_url="", sheet_name="", start_row=0, transcript_col=0, duration_col=None, status_col=None, include_timestamp=True, batch_size=None) -> List[VideoData]:
        """비디오 배치 처리 (실시간 시트 업데이트)"""
        global interrupt_controller
        results = []
        
        interrupt_controller.reset_stats()
        
        for i, video_id in enumerate(video_ids, 1):
            # ESC 키 중단 체크
            if interrupt_controller.should_stop:
                logger.warning(f"⚠️  사용자 요청으로 처리 중단됨 ({i-1}/{len(video_ids)} 처리 완료)")
                break
                
            logger.info(f"🔄 {i}/{len(video_ids)}: {video_id} 처리 중...")
            interrupt_controller.processed_count = i
            
            try:
                result = self.extract_transcript_from_video(video_id)
                results.append(result)
                
                # 통계 업데이트
                if result.error:
                    interrupt_controller.error_count += 1
                    logger.warning(f"❌ {video_id}: {result.error}")
                else:
                    interrupt_controller.success_count += 1
                    logger.info(f"🎬 {video_id}: {len(result.transcript)}개 세그먼트 추출 성공 (제목: {result.title[:30]}...)")
                    
                    # 실시간 시트 업데이트 (성공한 경우만)
                    if sheets_manager and result.transcript:
                        try:
                            current_row = start_row + i - 1
                            if include_timestamp:
                                transcript_text = '\n'.join([f"({ts}) {text}" for ts, text in result.transcript])
                            else:
                                transcript_text = '\n'.join([text for ts, text in result.transcript])

                            # 타임스탬프를 제외한 순수 텍스트만 추출
                            text_only = '\n'.join([text for ts, text in result.transcript])

                            # [음악], [박수] 등 특수 표기 제거
                            import re
                            text_cleaned = re.sub(r'\[.*?\]', '', text_only)

                            # 세그먼트(줄) 개수 계산 (빈 줄과 특수 표기만 있는 줄 제외)
                            segments = [line.strip() for line in text_cleaned.split('\n') if line.strip()]
                            segment_count = len(segments)

                            # 개별 셀 업데이트
                            workbook = sheets_manager.client.open_by_url(sheet_url)
                            sheet = workbook.worksheet(sheet_name)
                            
                            # 열 번호를 올바른 열 문자로 변환 (52열 = AZ열)
                            def column_number_to_letter(col_num):
                                result = ""
                                while col_num > 0:
                                    col_num -= 1
                                    result = chr(col_num % 26 + ord('A')) + result
                                    col_num //= 26
                                return result

                            # 영상 길이 확인 (duration_col이 있는 경우)
                            video_duration_text = ""
                            is_long_video = False
                            if duration_col:
                                try:
                                    video_duration_text = sheet.cell(current_row, duration_col).value or ""
                                    # 영상 길이가 3분 이상인지 확인
                                    is_long_video = self._is_video_longer_than_3min(video_duration_text)
                                    if is_long_video:
                                        logger.info(f"📏 {video_id}: 영상 길이 3분 이상 ({video_duration_text}) - 드라이브 저장 대상")
                                except Exception as duration_error:
                                    logger.warning(f"⚠️ {video_id}: 영상 길이 확인 실패 - {duration_error}")

                            # 대본 텍스트 길이 확인
                            text_length = len(transcript_text)
                            is_long_text = text_length > 10000

                            # 드라이브 저장 조건:
                            # 1. 영상 길이가 3분 이상 OR
                            # 2. 대본 텍스트가 1만자 초과 OR
                            # 3. 대본 길이가 40,000자 초과 (구글 시트 단일 셀 제한)
                            should_upload_to_drive = is_long_video or is_long_text or text_length > 40000

                            if should_upload_to_drive:
                                # 드라이브 저장 이유 로그
                                reasons = []
                                if is_long_video:
                                    reasons.append(f"영상 길이 3분 이상 ({video_duration_text})")
                                if is_long_text:
                                    reasons.append(f"대본 1만자 초과 ({text_length:,}자)")
                                if text_length > 40000:
                                    reasons.append(f"구글 시트 제한 초과 ({text_length:,}자)")

                                reason_str = " & ".join(reasons)
                                logger.info(f"📊 {video_id}: 드라이브 저장 조건 충족 - {reason_str}")

                                # Google Drive에 업로드
                                docs_url, txt_url = None, None

                                logger.info(f"🔧 {video_id}: 드라이브 저장 시도 중...")
                                try:
                                    # GUI_Extract 모듈 임포트 및 OAuth 인증 사용
                                    from GUI_Extract import GoogleSheetsManager as GuiSheetsManager
                                    
                                    # OAuth 인증을 위한 GUI SheetsManager 인스턴스 생성
                                    gui_sheets = GuiSheetsManager(sheets_manager.credentials_path)
                                    gui_sheets.authenticate()  # 일반 gspread client 설정
                                    gui_sheets.authenticate_oauth()  # OAuth 인증
                                    
                                    # Google Docs 추출 로직 사용
                                    try:
                                        playlist_name = gui_sheets.get_playlist_name_from_sheet(sheet_url, sheet_name, current_row)
                                    except Exception as playlist_error:
                                        logger.warning(f"⚠️ {video_id}: 재생목록 이름 추출 실패 - {playlist_error}")
                                        playlist_name = "기타"  # 기본값 사용
                                    
                                    # 시트에서 메타데이터 추출
                                    try:
                                        metadata = gui_sheets.get_video_metadata_from_sheet(sheet_url, sheet_name, current_row, video_id)
                                    except Exception as meta_error:
                                        logger.warning(f"⚠️ {video_id}: 메타데이터 추출 실패 - {meta_error}")
                                        metadata = {}
                                    
                                    docs_info, txt_info, thumbnail_info = gui_sheets.create_drive_documents_for_long_transcript(
                                        video_id, result.title, transcript_text, playlist_name, metadata
                                    )
                                    
                                    # 딕셔너리에서 URL 추출
                                    docs_url = docs_info.get('url') if docs_info else None
                                    txt_url = txt_info.get('url') if txt_info else None
                                    docs_id = docs_info.get('id') if docs_info else None
                                    txt_id = txt_info.get('id') if txt_info else None
                                    
                                    logger.info(f"✅ {video_id}: OAuth 기반 드라이브 저장 완료")
                                    
                                except Exception as oauth_error:
                                    logger.error(f"❌ {video_id}: OAuth 기반 드라이브 저장 실패 - {oauth_error}")
                                    docs_url, txt_url = None, None
                                    
                                    # 드라이브 저장 실패시 로컬 저장으로 대체
                                    logger.info(f"📁 {video_id}: 로컬 저장으로 대체")
                                    try:
                                        # 재생목록 이름으로 폴더 생성 (Main_Extract에서는 기본값 사용)
                                        playlist_name = "기타"  # Main_Extract에서는 간단히 기본값 사용
                                        
                                        from pathlib import Path
                                        import re
                                        
                                        # 파일명 안전하게 정리
                                        safe_playlist = re.sub(r'[<>:"/\\|?*]', '_', playlist_name)
                                        safe_title = re.sub(r'[<>:"/\\|?*]', '_', result.title[:50])
                                        
                                        local_dir = Path(__file__).parent / "대용량대본" / safe_playlist
                                        local_dir.mkdir(parents=True, exist_ok=True)
                                        local_file_path = local_dir / f"{current_row}_{safe_title}.txt"
                                        
                                        with open(local_file_path, 'w', encoding='utf-8') as f:
                                            f.write(transcript_text)
                                        
                                        txt_url = f"로컬 파일: {local_file_path}"
                                        logger.info(f"📁 {video_id}: 로컬 파일 저장 성공 - {local_file_path}")
                                    except Exception as local_error:
                                        logger.error(f"❌ {video_id}: 로컬 파일 저장도 실패 - {local_error}")
                                        txt_url = None
                                
                                # 드라이브 저장 결과에 따른 시트 업데이트
                                if docs_url or txt_url:
                                    # URL 정리 함수 (edit, view 제거)
                                    def clean_url(url):
                                        if not url:
                                            return url
                                        # /edit, /view 등 제거하여 순수 경로만 남김
                                        import re
                                        clean = re.sub(r'/(edit|view)(\?.*)?$', '', url)
                                        return clean
                                    
                                    # URL에서 파일 ID 추출 함수
                                    def extract_file_id(url):
                                        if not url:
                                            return ""
                                        import re
                                        # Google Docs/Drive URL에서 파일 ID 추출
                                        match = re.search(r'/d/([a-zA-Z0-9-_]+)', url)
                                        return match.group(1) if match else ""
                                    
                                    # 1. 대본내용 셀 업데이트
                                    col_letter = column_number_to_letter(transcript_col)
                                    cell_address = f'{col_letter}{current_row}'
                                    sheet.update(cell_address, [["대본 길이 초과로 드라이브 업로드 처리완료"]])
                                    logger.debug(f"✅ {video_id}: 대본내용 셀 업데이트 완료")
                                    
                                    # 2. 관련 헤더 찾기 및 업데이트
                                    all_values = sheet.get_all_values()
                                    header_row = all_values[0] if all_values else []
                                    
                                    # 헤더 행 디버그 출력 (55-60열 주변)
                                    logger.info(f"📋 {video_id}: 헤더 55-60열: {header_row[54:60] if len(header_row) > 60 else header_row[54:]}")
                                    
                                    # Google Docs/TXT 관련 헤더 찾기는 GUI_Extract.py에서 처리하므로 제거
                                    
                                    # 3. Google Docs 관련 업데이트는 GUI_Extract.py에서 처리하므로 제거
                                    
                                    # 4. TXT 관련 업데이트도 GUI_Extract.py에서 처리하므로 제거

                                    # 5. 대본유무는 전역함수 열이므로 직접 업데이트하지 않음
                                    # 9행의 전역함수 배열이 자동으로 계산: =ARRAYFORMULA(IF(LEN(AZ10:AZ)>0, "ㅇ", "x"))
                                    logger.debug(f"✅ {video_id}: 대본유무는 전역함수가 자동 계산합니다 (행 {current_row})")

                                    logger.info(f"✅ {video_id}: 드라이브 업로드 완료 및 모든 관련 헤더 업데이트 완료")
                                else:
                                    logger.error(f"❌ {video_id}: 드라이브 저장 실패 - 일반 시트 업데이트로 대체")
                                    # 드라이브 저장 실패시 일반 시트 업데이트 시도
                                    col_letter = column_number_to_letter(transcript_col)
                                    cell_address = f'{col_letter}{current_row}'
                                    try:
                                        sheet.update(cell_address, [[transcript_text]])
                                        logger.info(f"✅ {video_id}: 일반 시트 업데이트 완료 (드라이브 실패 대체)")
                                    except Exception as fallback_error:
                                        error_msg = str(fallback_error)
                                        if 'more than the maximum of 50000 characters' in error_msg:
                                            logger.error(f"❌ {video_id}: 구글 시트 문자 제한 초과 (50,000자) - {len(transcript_text):,}자")
                                        else:
                                            logger.error(f"❌ {video_id}: 일반 시트 업데이트 실패 - {fallback_error}")
                            elif segment_count <= 6:
                                # 6개 세그먼트 이하인 경우: 대본내용 셀에 'x' 저장
                                logger.info(f"⚠️ {video_id}: 대본 내용이 6개 세그먼트 이하 ({segment_count}개) - 대본내용 셀에 'x' 저장")
                                col_letter = column_number_to_letter(transcript_col)
                                cell_address = f'{col_letter}{current_row}'

                                try:
                                    sheet.update(cell_address, [['x']])
                                    logger.info(f"✅ {video_id}: 대본내용 셀에 'x' 저장 완료")
                                except Exception as update_error:
                                    logger.error(f"❌ {video_id}: 대본내용 'x' 저장 실패 - {update_error}")
                            else:
                                # 40,000자 이하이고 6개 세그먼트 초과인 경우 일반 시트 업데이트
                                logger.info(f"📝 {video_id}: 일반 시트 업데이트 ({len(transcript_text):,}자)")
                                col_letter = column_number_to_letter(transcript_col)
                                cell_address = f'{col_letter}{current_row}'

                                try:
                                    sheet.update(cell_address, [[transcript_text]])
                                except Exception as update_error:
                                    error_msg = str(update_error)
                                    if 'more than the maximum of 50000 characters' in error_msg:
                                        logger.error(f"❌ {video_id}: 구글 시트 문자 제한 초과 (50,000자) - {len(transcript_text):,}자")
                                        logger.error(f"❌ {video_id}: 40,000자 기준이었으나 실제로는 50,000자 제한에 걸림")
                                    else:
                                        logger.error(f"❌ {video_id}: 일반 시트 업데이트 실패 - {update_error}")
                                    continue  # 다음 영상 처리
                                
                                # 업데이트 확인 (실제로 데이터가 들어갔는지 검증)
                                time.sleep(1)  # API 반영 대기
                                updated_value = sheet.cell(current_row, transcript_col).value
                                
                                if updated_value and len(updated_value) > 10:  # 최소 10자 이상이면 성공으로 간주
                                    logger.info(f"✅ {video_id}: 시트 {current_row}행 ({cell_address}) 업데이트 완료 및 검증됨")

                                    # 대본유무는 전역함수 열이므로 직접 업데이트하지 않음
                                    # 9행의 전역함수 배열이 자동으로 계산
                                    logger.debug(f"✅ {video_id}: 대본유무는 전역함수가 자동 계산합니다 (행 {current_row})")
                                else:
                                    logger.warning(f"⚠️  {video_id}: 시트 업데이트 후 검증 실패 - 값: '{updated_value[:50] if updated_value else 'None'}'")
                                    # 재시도
                                    logger.info(f"🔄 {video_id}: 시트 업데이트 재시도...")
                                    sheet.update(cell_address, [[transcript_text]])
                                    time.sleep(1)
                                    updated_value = sheet.cell(current_row, transcript_col).value
                                    if updated_value and len(updated_value) > 10:
                                        logger.info(f"✅ {video_id}: 재시도 후 시트 업데이트 성공")

                                        # 대본유무는 전역함수 열이므로 직접 업데이트하지 않음
                                        # 9행의 전역함수 배열이 자동으로 계산
                                        logger.debug(f"✅ {video_id}: 대본유무는 전역함수가 자동 계산합니다 (행 {current_row})")
                                    else:
                                        logger.error(f"❌ {video_id}: 재시도 후에도 시트 업데이트 실패")
                        except Exception as update_error:
                            logger.error(f"⚠️  {video_id}: 시트 업데이트 실패 - {update_error}")
                            logger.debug(f"⚠️  업데이트 세부정보 - 행: {current_row}, 열: {transcript_col}")
                    elif sheets_manager and not result.transcript:
                        # 자막 데이터가 없는 경우 'x' 표시
                        try:
                            col_letter = column_number_to_letter(transcript_col)
                            cell_address = f'{col_letter}{current_row}'
                            sheet.update(cell_address, [['x']])
                            logger.info(f"📝 {video_id}: 자막 데이터 없음 - 대본내용 셀에 'x' 표시 완료 (행 {current_row})")
                        except Exception as update_error:
                            logger.error(f"❌ {video_id}: 대본내용 'x' 표시 실패 - {update_error}")
                    elif not sheets_manager:
                        logger.debug(f"📝 {video_id}: sheets_manager가 없어 시트 업데이트 건너뜀")
                
                # 진행 상황 출력
                logger.info(f"📊 진행률: {i}/{len(video_ids)} ({(i/len(video_ids)*100):.1f}%) | 성공: {interrupt_controller.success_count}개 | 실패: {interrupt_controller.error_count}개")
                
                # 다음 요청 전 잠시 대기 (최대 2초 이하)
                if i < len(video_ids) and not interrupt_controller.should_stop:
                    delay = random.uniform(0.8, 2.0)
                    logger.debug(f"⏳ {delay:.1f}초 대기...")
                    time.sleep(delay)
                    
            except Exception as e:
                logger.exception(f"❌ {video_id} 처리 중 예외:")
                results.append(VideoData(video_id=video_id, error=str(e)))
                interrupt_controller.error_count += 1
        
        # 최종 집계
        final_success = sum(1 for r in results if not r.error)
        final_error = len(results) - final_success
        
        logger.info(f"📊 배치 처리 완료: 성공 {final_success}개, 실패 {final_error}개")
        
        # 중단된 경우 집계 로그 저장
        if interrupt_controller.should_stop:
            self._save_interrupt_summary(len(video_ids), final_success, final_error)
        
        return results

    def _is_video_longer_than_3min(self, duration_text: str) -> bool:
        """영상 길이가 3분 이상인지 판별"""
        try:
            if not duration_text:
                return False

            duration_text = str(duration_text).strip()
            logger.debug(f"영상 길이 판별 (3분 기준): '{duration_text}'")

            # 시간 형식 파싱 (예: "3분 27초", "1시간 02분 53초", "5분", "1시간 30분")
            import re

            # 시간, 분 추출
            hour_match = re.search(r'(\d+)시간', duration_text)
            minute_match = re.search(r'(\d+)분', duration_text)

            hours = int(hour_match.group(1)) if hour_match else 0
            minutes = int(minute_match.group(1)) if minute_match else 0

            total_minutes = hours * 60 + minutes

            logger.debug(f"영상 길이 파싱 결과: {hours}시간 {minutes}분 (총 {total_minutes}분)")

            # 3분 이상 확인
            return total_minutes >= 3
        except Exception as e:
            logger.warning(f"영상 길이 판별 실패: {duration_text} - {e}")
            return False

    def _save_interrupt_summary(self, total_videos, success_count, error_count):
        """중단 시 집계 로그 저장"""
        summary = f"""
==========================================
처리 중단 집계 (ESC 키로 중단됨)
==========================================
총 예정 영상: {total_videos}개
처리 완료: {success_count + error_count}개
성공: {success_count}개
실패: {error_count}개
미처리: {total_videos - (success_count + error_count)}개
성공률: {(success_count/(success_count + error_count)*100):.1f}% (처리된 영상 기준)
중단 시간: {time.strftime('%Y-%m-%d %H:%M:%S')}
==========================================
        """
        logger.info(summary)
        print(summary)

class MainYouTubeShortsTranscriptExtractor:
    """YouTube Shorts 자막 추출기"""
    
    def __init__(self, config: TranscriptConfig):
        self.config = config
        self.session: Optional[aiohttp.ClientSession] = None
        
    async def __aenter__(self):
        """비동기 컨텍스트 매니저 진입"""
        # Brotli 지원을 위한 커넥터 설정
        connector = aiohttp.TCPConnector(
            limit=100,
            limit_per_host=30,
            ttl_dns_cache=300,
            use_dns_cache=True,
        )
        
        # 완전한 브라우저 시뮬레이션 헤더
        browser_headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7',
            'Accept-Language': 'ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7',
            'Accept-Encoding': 'gzip, deflate, br, zstd',
            'DNT': '1',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1',
            'Sec-Fetch-Dest': 'document',
            'Sec-Fetch-Mode': 'navigate',
            'Sec-Fetch-Site': 'none',
            'Sec-Fetch-User': '?1',
            'Cache-Control': 'max-age=0',
            'sec-ch-ua': '"Google Chrome";v="131", "Chromium";v="131", "Not_A Brand";v="24"',
            'sec-ch-ua-mobile': '?0',
            'sec-ch-ua-platform': '"Windows"',
        }
        
        self.session = aiohttp.ClientSession(
            connector=connector,
            timeout=aiohttp.ClientTimeout(total=30),
            headers=browser_headers
        )
        return self
        
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """비동기 컨텍스트 매니저 종료"""
        if self.session:
            await self.session.close()

    def extract_video_id(self, url_or_id: str) -> Optional[str]:
        """URL 또는 ID에서 비디오 ID 추출"""
        if not url_or_id:
            return None
            
        # 이미 ID인 경우
        if len(url_or_id) == 11 and url_or_id.isalnum():
            return url_or_id
            
        # URL인 경우
        if 'youtube.com/shorts/' in url_or_id:
            return url_or_id.split('/shorts/')[1].split('?')[0]
        elif 'youtu.be/' in url_or_id:
            return url_or_id.split('youtu.be/')[1].split('?')[0]
        elif 'youtube.com/watch' in url_or_id:
            parsed = urlparse(url_or_id)
            return parse_qs(parsed.query).get('v', [None])[0]
            
        return None

    def extract_json_from_html(self, html: str, key: str) -> Optional[Dict]:
        """HTML에서 JSON 데이터 추출"""
        patterns = [
            rf'window\["{key}"\]\s*=\s*({{[\s\S]+?}})\s*;',
            rf'var {key}\s*=\s*({{[\s\S]+?}})\s*;',
            rf'{key}\s*=\s*({{[\s\S]+?}})\s*;'
        ]
        
        for pattern in patterns:
            match = re.search(pattern, html)
            if match:
                try:
                    return json.loads(match.group(1))
                except json.JSONDecodeError as e:
                    logger.warning(f"JSON 파싱 실패 {key}: {e}")
                    continue
        return None

    def ms_to_timestamp(self, ms: int) -> str:
        """밀리초를 타임스탬프로 변환"""
        total_sec = ms // 1000
        minutes = total_sec // 60
        seconds = total_sec % 60
        return f"{minutes}:{seconds:02d}"

    def get_best_caption_track(self, caption_tracks: List[Dict], target_lang: Optional[str] = None) -> Optional[Dict]:
        """최적의 자막 트랙 선택"""
        if not caption_tracks:
            return None
            
        # 타겟 언어가 지정된 경우
        if target_lang:
            for track in caption_tracks:
                if track.get('languageCode', '').startswith(target_lang):
                    return track
                    
        # 자동 생성된 자막 중에서 선택
        auto_tracks = [t for t in caption_tracks if t.get('kind') == 'asr']
        if auto_tracks:
            return auto_tracks[0]
            
        # 첫 번째 자막 반환
        return caption_tracks[0]

    async def extract_transcript_from_shorts(self, video_id: str) -> VideoData:
        """쇼츠에서 자막 추출 (폴백 지원)"""
        video_data = VideoData(video_id=video_id)
        
        # 1단계: 쿠키 설정을 위한 YouTube 메인 페이지 사전 방문
        try:
            logger.debug(f"{video_id}: 쿠키 설정을 위한 YouTube 메인 페이지 방문")
            async with self.session.get("https://www.youtube.com") as main_response:
                if main_response.status == 200:
                    logger.debug(f"{video_id}: YouTube 메인 페이지 방문 성공, 쿠키 설정 완료")
                    # 랜덤 지연 (봇 탐지 회피)
                    delay = random.uniform(1.0, 2.0)
                    await asyncio.sleep(delay)
                else:
                    logger.warning(f"{video_id}: YouTube 메인 페이지 방문 실패 - HTTP {main_response.status}")
        except Exception as e:
            logger.warning(f"{video_id}: YouTube 메인 페이지 방문 중 오류 - {e}")
        
        # 시도할 URL 목록 (다중 전략)
        urls_to_try = [
            f"https://www.youtube.com/shorts/{video_id}",
            f"https://www.youtube.com/watch?v={video_id}",
            f"https://m.youtube.com/watch?v={video_id}"  # 모바일 버전 추가
        ]
        
        last_error = None
        
        for attempt, url in enumerate(urls_to_try, 1):
            try:
                url_type = "Shorts" if "/shorts/" in url else "일반 YouTube"
                logger.debug(f"{video_id}: {url_type} URL 시도 ({attempt}/{len(urls_to_try)}): {url}")
                
                # YouTube 페이지 요청
                async with self.session.get(url) as response:
                    if response.status != 200:
                        last_error = f"HTTP {response.status}"
                        logger.debug(f"{video_id}: {url_type} URL 실패 - HTTP {response.status}")
                        continue
                        
                    html = await response.text()
                
                # 성공한 경우 자막 추출 진행
                logger.info(f"✅ {video_id}: {url_type} URL로 페이지 접근 성공")
                return await self._extract_transcript_from_html(video_data, html)
                
            except Exception as e:
                last_error = str(e)
                error_msg = str(e).lower()
                
                # Brotli 인코딩 오류 체크
                if "brotli" in error_msg or "content-encoding" in error_msg:
                    logger.warning(f"⚠️ {video_id}: {url_type} URL에서 Brotli 인코딩 오류, 다음 URL 시도")
                    continue
                # 기타 일시적 오류들
                elif any(keyword in error_msg for keyword in ["timeout", "connection", "network"]):
                    logger.warning(f"⚠️ {video_id}: {url_type} URL에서 네트워크 오류, 다음 URL 시도")
                    continue
                else:
                    logger.debug(f"{video_id}: {url_type} URL 실패 - {e}")
                    continue
        
        # 모든 URL 시도 실패
        video_data.error = f"모든 URL 형식 실패: {last_error}"
        logger.error(f"❌ {video_id}: 모든 URL 형식 시도 실패")
        return video_data
        
    async def _extract_transcript_from_html(self, video_data: VideoData, html: str) -> VideoData:
        """HTML에서 자막 추출 (확장프로그램 로직 적용)"""
        try:
            # 확장프로그램과 동일한 방식으로 데이터 추출 시도
            transcript_result = await self._resolve_youtube_data_and_extract(html, video_data.video_id)
            
            if transcript_result['success']:
                video_data.title = transcript_result['title']
                video_data.transcript = transcript_result['transcript']
                video_data.language = transcript_result.get('language', 'unknown')
                logger.info(f"✅ {video_data.video_id}: {len(transcript_result['transcript'])}개 자막 세그먼트 추출 완료")
            else:
                video_data.error = transcript_result['error']
                logger.warning(f"❌ {video_data.video_id}: {transcript_result['error']}")
            
        except Exception as e:
            video_data.error = str(e)
            logger.exception(f"❌ {video_data.video_id} HTML 자막 추출 실패:")
            
        return video_data

    async def _resolve_youtube_data_and_extract(self, html: str, video_id: str) -> dict:
        """확장프로그램의 resolveYouTubeData와 동일한 로직"""
        # 1. ytInitialData 먼저 시도 (일반 YouTube 방식)
        yt_data = self.extract_json_from_html(html, 'ytInitialData')

        # 제목 추출
        title = "제목 없음"
        if yt_data:
            video_details = yt_data.get('videoDetails', {})
            if video_details:
                title = video_details.get('title', '')

            # 추가 제목 소스 확인 (확장프로그램과 동일)
            if not title:
                player_overlays = yt_data.get('playerOverlays', {}).get('playerOverlayRenderer', {})
                video_details_renderer = player_overlays.get('videoDetails', {}).get('playerOverlayVideoDetailsRenderer', {})
                title_obj = video_details_renderer.get('title', {})
                title = title_obj.get('simpleText', title)

        # 0. engagement panel 방식 우선 시도 (가장 성공률이 높음)
        if yt_data:
            panels = yt_data.get('engagementPanels', [])
            has_transcript_panel = any(
                p.get('engagementPanelSectionListRenderer', {})
                .get('content', {})
                .get('continuationItemRenderer', {})
                .get('continuationEndpoint', {})
                .get('getTranscriptEndpoint')
                for p in panels
            )

            if has_transcript_panel:
                logger.debug(f"{video_id}: engagement panel 방식으로 자막 추출 시도 (우선)")
                transcript_result = await self._extract_via_engagement_panel(yt_data, video_id)
                if transcript_result['success']:
                    transcript_result['title'] = title or transcript_result.get('title', '제목 없음')
                    logger.info(f"✅ {video_id}: engagement panel 방식으로 자막 추출 성공!")
                    return transcript_result

        # 1. youtube-transcript-api 시도 (2025 InnerTube API 지원)
        if YOUTUBE_TRANSCRIPT_API_AVAILABLE:
            logger.info(f"{video_id}: youtube-transcript-api 시도 (InnerTube API)")
            yt_api_result = await self._extract_via_youtube_transcript_api(video_id, title)
            if yt_api_result['success']:
                logger.info(f"✅ {video_id}: youtube-transcript-api로 자막 추출 성공!")
                return yt_api_result
            else:
                logger.debug(f"{video_id}: youtube-transcript-api 실패, 다음 방식 시도")

        # 2. Innertube API 직접 호출 시도 (2025년 방식)
        try:
            from innertube_extractor import InnertubeTranscriptExtractor

            logger.info(f"{video_id}: Innertube API 직접 호출 시도")
            loop = asyncio.get_event_loop()
            innertube_extractor = InnertubeTranscriptExtractor()

            innertube_result = await loop.run_in_executor(
                None,
                innertube_extractor.extract_transcript,
                video_id
            )

            if innertube_result['success']:
                logger.info(f"✅ {video_id}: Innertube API로 자막 추출 성공!")
                return {
                    'success': True,
                    'transcript': innertube_result['transcript'],
                    'title': title,
                    'language': innertube_result['language']
                }
            else:
                logger.debug(f"{video_id}: Innertube API 실패 - {innertube_result['error']}")

        except ImportError:
            logger.debug(f"{video_id}: innertube_extractor 모듈 없음, 다음 방식 시도")
        except Exception as e:
            logger.warning(f"{video_id}: Innertube API 예외 - {str(e)}")

        # 3. ytInitialPlayerResponse 방식 시도 (Shorts 방식)
        logger.debug(f"{video_id}: ytInitialPlayerResponse 방식으로 자막 추출 시도")
        player_data = self.extract_json_from_html(html, 'ytInitialPlayerResponse')
        if not player_data:
            return {
                'success': False,
                'error': 'ytInitialPlayerResponse를 찾을 수 없음',
                'title': title,
                'transcript': []
            }
        
        # 제목 업데이트 
        if not title or title == "제목 없음":
            title = player_data.get('videoDetails', {}).get('title', '제목 없음')
        
        # 자막 트랙 찾기
        captions = player_data.get('captions', {})
        caption_tracks = captions.get('playerCaptionsTracklistRenderer', {}).get('captionTracks', [])
        
        if not caption_tracks:
            return {
                'success': False,
                'error': '자막을 사용할 수 없음',
                'title': title,
                'transcript': []
            }
        
        # 최적의 자막 트랙 선택
        selected_track = self.get_best_caption_track(caption_tracks, self.config.target_language)
        if not selected_track:
            return {
                'success': False,
                'error': '적절한 자막 트랙을 찾을 수 없음',
                'title': title,
                'transcript': []
            }
        
        # baseUrl 방식으로 자막 추출
        return await self._extract_via_base_url(selected_track, video_id, title)

    async def _extract_via_engagement_panel(self, yt_data: dict, video_id: str) -> dict:
        """engagement panel을 통한 자막 추출 (일반 YouTube)"""
        try:
            # continuation params 추출
            panels = yt_data.get('engagementPanels', [])
            continuation_params = None
            
            for panel in panels:
                endpoint = (panel.get('engagementPanelSectionListRenderer', {})
                          .get('content', {})
                          .get('continuationItemRenderer', {})
                          .get('continuationEndpoint', {})
                          .get('getTranscriptEndpoint', {}))
                if endpoint:
                    continuation_params = endpoint.get('params')
                    break
            
            if not continuation_params:
                return {
                    'success': False,
                    'error': 'continuation params를 찾을 수 없음',
                    'transcript': []
                }
            
            # 클라이언트 정보 추출
            hl = (yt_data.get('topbar', {})
                 .get('desktopTopbarRenderer', {})
                 .get('searchbox', {})
                 .get('fusionSearchboxRenderer', {})
                 .get('config', {})
                 .get('webSearchboxConfig', {})
                 .get('requestLanguage', 'en'))
            
            client_data = yt_data.get('responseContext', {}).get('serviceTrackingParams', [])
            visitor_data = (yt_data.get('responseContext', {})
                          .get('webResponseContextExtensionData', {})
                          .get('ytConfigData', {})
                          .get('visitorData'))
            
            client_name = client_version = None
            if client_data and len(client_data) > 0:
                params = client_data[0].get('params', [])
                if len(params) >= 2:
                    client_name = params[0].get('value')
                    client_version = params[1].get('value')
            
            # API 요청 body 구성
            body = {
                'context': {
                    'client': {
                        'hl': hl,
                        'visitorData': visitor_data,
                        'clientName': client_name,
                        'clientVersion': client_version
                    },
                    'request': {'useSsl': True}
                },
                'params': continuation_params
            }
            
            # YouTube internal API 호출
            api_url = "https://www.youtube.com/youtubei/v1/get_transcript?prettyPrint=false"
            headers = {'Content-Type': 'application/json'}
            
            async with self.session.post(api_url, json=body, headers=headers) as response:
                if response.status != 200:
                    return {
                        'success': False,
                        'error': f'transcript API 호출 실패: HTTP {response.status}',
                        'transcript': []
                    }
                
                data = await response.json()
            
            # transcript 데이터 추출
            segments = (data.get('actions', [{}])[0]
                       .get('updateEngagementPanelAction', {})
                       .get('content', {})
                       .get('transcriptRenderer', {})
                       .get('content', {})
                       .get('transcriptSearchPanelRenderer', {})
                       .get('body', {})
                       .get('transcriptSegmentListRenderer', {})
                       .get('initialSegments', []))
            
            if not segments:
                return {
                    'success': False,
                    'error': 'transcript segments를 찾을 수 없음',
                    'transcript': []
                }
            
            # transcript 파싱 (일반 YouTube 방식)
            transcript = []
            for item in segments:
                seg = item.get('transcriptSegmentRenderer')
                if not seg:
                    continue
                
                timestamp = seg.get('startTimeText', {}).get('simpleText', '')
                runs = seg.get('snippet', {}).get('runs', [])
                text = ' '.join(run.get('text', '') for run in runs).strip()
                
                if timestamp and text:
                    transcript.append((timestamp, text))
            
            logger.debug(f"{video_id}: engagement panel 방식으로 {len(transcript)}개 세그먼트 추출")
            return {
                'success': True,
                'transcript': transcript,
                'language': hl
            }
            
        except Exception as e:
            logger.debug(f"{video_id}: engagement panel 방식 실패 - {e}")
            return {
                'success': False,
                'error': f'engagement panel 방식 실패: {str(e)}',
                'transcript': []
            }

    async def _extract_via_youtube_transcript_api(self, video_id: str, title: str) -> dict:
        """youtube-transcript-api 라이브러리를 사용한 자막 추출 (2025 InnerTube API 지원)"""
        if not YOUTUBE_TRANSCRIPT_API_AVAILABLE:
            return {
                'success': False,
                'error': 'youtube-transcript-api 라이브러리를 사용할 수 없음',
                'title': title,
                'transcript': []
            }

        try:
            logger.info(f"{video_id}: youtube-transcript-api 라이브러리로 자막 추출 시도 (v1.2.2 API)")

            # 비동기 컨텍스트에서 동기 함수 실행
            loop = asyncio.get_event_loop()

            # youtube-transcript-api v1.2.2는 인스턴스 생성 후 fetch() 메서드 사용
            ytt_api = YouTubeTranscriptApi()
            transcript_data = None

            # 한국어 자막 시도
            try:
                logger.debug(f"{video_id}: 한국어 자막 다운로드 시도...")
                transcript_data = await loop.run_in_executor(
                    None,
                    lambda: ytt_api.fetch(video_id, languages=['ko', 'kr'])
                )
                logger.info(f"{video_id}: 한국어 자막 다운로드 성공 - {len(transcript_data)}개 세그먼트")
            except NoTranscriptFound:
                # 한국어 자막 없으면 영어 시도
                logger.debug(f"{video_id}: 한국어 자막 없음, 영어 자막 시도...")
                try:
                    transcript_data = await loop.run_in_executor(
                        None,
                        lambda: ytt_api.fetch(video_id, languages=['en'])
                    )
                    logger.info(f"{video_id}: 영어 자막 다운로드 성공 - {len(transcript_data)}개 세그먼트")
                except NoTranscriptFound:
                    # 모든 언어 자막 시도
                    logger.debug(f"{video_id}: 영어 자막 없음, 모든 언어 자막 시도...")
                    transcript_data = await loop.run_in_executor(
                        None,
                        lambda: ytt_api.fetch(video_id)
                    )
                    logger.info(f"{video_id}: 자막 다운로드 성공 - {len(transcript_data)}개 세그먼트")

            # 자막 데이터를 내부 포맷으로 변환
            formatted_transcript = []
            for entry in transcript_data:
                timestamp = self.ms_to_timestamp(int(entry['start'] * 1000))
                text = entry['text'].replace('\n', ' ').strip()
                if text:
                    formatted_transcript.append((timestamp, text))

            logger.info(f"🎉 {video_id}: youtube-transcript-api로 {len(formatted_transcript)}개 세그먼트 추출 성공!")

            return {
                'success': True,
                'transcript': formatted_transcript,
                'title': title,
                'language': 'ko'  # v1.2.2에서는 언어 코드를 직접 반환하지 않음
            }

        except TranscriptsDisabled:
            logger.warning(f"{video_id}: 이 영상은 자막이 비활성화되어 있습니다")
            return {
                'success': False,
                'error': '자막이 비활성화됨',
                'title': title,
                'transcript': []
            }
        except NoTranscriptFound:
            logger.warning(f"{video_id}: 사용 가능한 자막을 찾을 수 없습니다")
            return {
                'success': False,
                'error': '사용 가능한 자막 없음',
                'title': title,
                'transcript': []
            }
        except VideoUnavailable:
            logger.warning(f"{video_id}: 영상을 사용할 수 없습니다")
            return {
                'success': False,
                'error': '영상을 사용할 수 없음',
                'title': title,
                'transcript': []
            }
        except Exception as e:
            logger.warning(f"{video_id}: youtube-transcript-api 실패 - {str(e)}")
            return {
                'success': False,
                'error': f'youtube-transcript-api 실패: {str(e)}',
                'title': title,
                'transcript': []
            }

    async def _extract_via_base_url(self, selected_track: dict, video_id: str, title: str) -> dict:
        """baseUrl을 통한 자막 추출 (Shorts 방식)"""
        try:
            # 자막 트랙 정보 상세 로깅
            track_info = {
                'languageCode': selected_track.get('languageCode', 'unknown'),
                'kind': selected_track.get('kind', 'unknown'),
                'name': selected_track.get('name', {}).get('simpleText', 'unnamed')
            }
            logger.info(f"{video_id}: 선택된 자막 트랙 - {track_info}")
            
            # 여러 포맷과 최적화된 파라미터로 시도
            formats_to_try = [
                ("json3", "&fmt=json3&xoaf=5&xosf=1&hl=ko"),
                ("srv3", "&fmt=srv3&xoaf=5&xosf=1&hl=ko"), 
                ("ttml", "&fmt=ttml&xoaf=5&xosf=1&hl=ko"),
                ("vtt", "&fmt=vtt&xoaf=5&xosf=1&hl=ko")
            ]
            
            base_url = selected_track['baseUrl']
            logger.info(f"{video_id}: 기본 자막 URL - {base_url}")
            
            for fmt_name, fmt_param in formats_to_try:
                caption_url = base_url + fmt_param
                logger.info(f"{video_id}: {fmt_name} 포맷으로 자막 요청 시도 - {caption_url}")
                
                # timedtext API 전용 헤더 (완전한 브라우저 시뮬레이션)
                headers = {
                    'Accept': 'application/json, text/plain, */*',
                    'Accept-Language': 'ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7',
                    'Accept-Encoding': 'gzip, deflate, br, zstd',
                    'Referer': f'https://www.youtube.com/watch?v={video_id}',
                    'Origin': 'https://www.youtube.com',
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
                    'X-Requested-With': 'XMLHttpRequest',
                    'Sec-Fetch-Dest': 'empty',
                    'Sec-Fetch-Mode': 'cors',
                    'Sec-Fetch-Site': 'same-origin',
                    'sec-ch-ua': '"Google Chrome";v="131", "Chromium";v="131", "Not_A Brand";v="24"',
                    'sec-ch-ua-mobile': '?0',
                    'sec-ch-ua-platform': '"Windows"',
                    'DNT': '1',
                    'Connection': 'keep-alive'
                }
                
                # 랜덤 지연 (봇 탐지 회피)
                delay = random.uniform(0.5, 1.5)
                await asyncio.sleep(delay)
                
                async with self.session.get(caption_url, headers=headers) as caption_response:
                    # 응답 상세 정보 로깅
                    response_headers = dict(caption_response.headers)
                    logger.info(f"{video_id}: {fmt_name} 응답 - 상태: {caption_response.status}, Content-Type: {response_headers.get('content-type', 'N/A')}")
                    logger.debug(f"{video_id}: {fmt_name} 응답 헤더 - {response_headers}")
                    
                    if caption_response.status != 200:
                        status_code = caption_response.status
                        logger.warning(f"{video_id}: {fmt_name} 요청 실패 - HTTP {status_code}")
                        
                        # 403 Forbidden의 경우 추가 지연 후 재시도
                        if status_code == 403:
                            logger.info(f"{video_id}: {fmt_name} 403 에러로 인한 추가 지연 적용")
                            retry_delay = random.uniform(2.0, 4.0)
                            await asyncio.sleep(retry_delay)
                        # 429 Too Many Requests의 경우 더 긴 지연
                        elif status_code == 429:
                            logger.warning(f"{video_id}: {fmt_name} 요청 한도 초과로 인한 긴 지연 적용")
                            retry_delay = random.uniform(5.0, 10.0)
                            await asyncio.sleep(retry_delay)
                        
                        continue
                    
                    # 응답 내용 분석
                    content_type = caption_response.headers.get('content-type', '').lower()
                    content_length = caption_response.headers.get('content-length', 'unknown')
                    logger.info(f"{video_id}: {fmt_name} 응답 크기 - {content_length} 바이트")
                    
                    # 응답 내용 읽기
                    try:
                        response_text = await caption_response.text()
                        response_size = len(response_text)
                        logger.info(f"{video_id}: {fmt_name} 실제 응답 크기 - {response_size} 바이트")
                        
                        if response_size == 0:
                            logger.warning(f"{video_id}: {fmt_name} 완전히 빈 응답 - PoToken 필요 가능성")
                            logger.warning(f"{video_id}: YouTube가 2025년부터 PoToken(Proof of Origin Token)을 요구합니다")
                            if not YOUTUBE_TRANSCRIPT_API_AVAILABLE:
                                logger.warning(f"{video_id}: youtube-transcript-api 라이브러리 설치 권장: pip install youtube-transcript-api")
                            continue

                        # 응답 내용 미리보기
                        preview = response_text[:500].replace('\n', '\\n').replace('\r', '\\r')
                        logger.info(f"{video_id}: {fmt_name} 응답 미리보기 (500자) - {preview}")

                        # JSON 포맷 처리
                        if fmt_name == 'json3':
                            if 'application/json' not in content_type:
                                logger.warning(f"{video_id}: {fmt_name} Content-Type이 JSON이 아님 - {content_type}")
                                logger.debug(f"{video_id}: {fmt_name} 전체 응답 헤더 - {response_headers}")

                                # HTML 내용에 오류 키워드 확인
                                response_lower = response_text.lower()
                                if 'error' in response_lower or 'bot' in response_lower or 'denied' in response_lower:
                                    logger.warning(f"{video_id}: {fmt_name} 응답에 오류 키워드 발견 (error/bot/denied)")
                                    logger.debug(f"{video_id}: {fmt_name} 응답 전체 내용: {response_text}")

                                if response_text.strip():
                                    logger.warning(f"{video_id}: {fmt_name} 하지만 응답 내용이 있음, JSON 파싱 시도")
                                else:
                                    logger.error(f"{video_id}: {fmt_name} 빈 응답 확인됨 (HTML Content-Type)")
                                    continue
                            
                            try:
                                caption_data = await caption_response.json()
                                logger.info(f"{video_id}: {fmt_name} JSON 파싱 성공")
                                
                                # JSON 구조 분석
                                events = caption_data.get('events', [])
                                logger.info(f"{video_id}: {fmt_name} events 배열 길이 - {len(events)}")
                                
                                if len(events) == 0:
                                    logger.warning(f"{video_id}: {fmt_name} events 배열이 비어있음")
                                    logger.debug(f"{video_id}: {fmt_name} JSON 구조 - {list(caption_data.keys())}")
                                    continue
                                
                                # 자막 파싱 시도
                                transcript = self._parse_json3_caption(events, video_id)
                                if transcript:
                                    logger.info(f"🎉 {video_id}: {fmt_name} 포맷으로 {len(transcript)}개 세그먼트 추출 성공!")
                                    logger.info(f"✅ {video_id}: 사용된 전략 - {fmt_name} 포맷, {selected_track.get('languageCode', 'unknown')} 언어")
                                    return {
                                        'success': True,
                                        'transcript': transcript,
                                        'title': title,
                                        'language': selected_track.get('languageCode', 'unknown')
                                    }
                                else:
                                    logger.warning(f"{video_id}: {fmt_name} 파싱 결과 빈 자막")
                                    continue
                                    
                            except Exception as json_error:
                                logger.warning(f"{video_id}: {fmt_name} JSON 파싱 실패 - {json_error}")
                                continue
                        
                        # 다른 포맷들도 처리 가능하도록 확장
                        elif fmt_name in ['srv3', 'ttml', 'vtt']:
                            logger.info(f"{video_id}: {fmt_name} 포맷 파싱은 아직 구현되지 않음, 다음 포맷 시도")
                            continue
                            
                    except Exception as e:
                        logger.error(f"{video_id}: {fmt_name} 응답 읽기 실패 - {e}")
                        continue
            
            # 모든 포맷 실패
            return {
                'success': False,
                'error': f'모든 자막 포맷({[f[0] for f in formats_to_try]}) 시도 실패',
                'title': title,
                'transcript': []
            }
            
        except Exception as e:
            logger.exception(f"{video_id}: baseUrl 방식 전체 실패:")
            return {
                'success': False,
                'error': f'baseUrl 방식 실패: {str(e)}',
                'title': title,
                'transcript': []
            }

    def _parse_json3_caption(self, events: list, video_id: str) -> list:
        """JSON3 포맷 자막 파싱"""
        transcript = []
        
        for event in events:
            if 'segs' not in event:
                continue
            
            timestamp = self.ms_to_timestamp(event.get('tStartMs', 0))
            text_parts = []
            
            for seg in event['segs']:
                if 'utf8' in seg:
                    text_parts.append(seg['utf8'])
            
            if text_parts:
                text = ' '.join(text_parts).replace('\n', ' ').strip()
                if text:
                    transcript.append((timestamp, text))
        
        logger.debug(f"{video_id}: JSON3 파싱 완료 - {len(transcript)}개 세그먼트")
        return transcript

    async def process_video_with_retry(self, video_id: str) -> VideoData:
        """재시도 로직을 포함한 비디오 처리"""
        for attempt in range(self.config.retry_attempts):
            try:
                result = await self.extract_transcript_from_shorts(video_id)
                if not result.error:
                    return result
                    
                if attempt < self.config.retry_attempts - 1:
                    wait_time = (attempt + 1) * 2
                    logger.warning(f"🔄 {video_id}: 재시도 {attempt + 1}/{self.config.retry_attempts} (대기: {wait_time}초)")
                    await asyncio.sleep(wait_time)
                else:
                    return result
                    
            except Exception as e:
                if attempt < self.config.retry_attempts - 1:
                    wait_time = (attempt + 1) * 2
                    logger.warning(f"🔄 {video_id}: 재시도 {attempt + 1}/{self.config.retry_attempts}")
                    logger.exception(f"재시도 원인 ({video_id}):")
                    await asyncio.sleep(wait_time)
                else:
                    logger.exception(f"❌ {video_id} 최종 실패:")
                    return VideoData(video_id=video_id, error=str(e))
                    
        return VideoData(video_id=video_id, error="최대 재시도 횟수 초과")

    async def process_videos_batch(self, video_ids: List[str], batch_size: int = 50, progress_callback=None) -> List[VideoData]:
        """비디오 배치 처리 (50개 단위)"""
        all_results = []
        
        for i in range(0, len(video_ids), batch_size):
            batch = video_ids[i:i + batch_size]
            current_batch = i//batch_size + 1
            total_batches = (len(video_ids) + batch_size - 1) // batch_size
            
            batch_msg = f"🔄 배치 {current_batch}: {i+1}-{min(i+batch_size, len(video_ids))}번째 영상 처리 중..."
            logger.info(batch_msg)
            if progress_callback:
                progress_callback(f"배치 {current_batch}/{total_batches}: {i+1}-{min(i+batch_size, len(video_ids))}번째 영상 처리 중...")
            
            semaphore = asyncio.Semaphore(self.config.max_concurrent)
            
            async def process_single(video_id: str) -> VideoData:
                async with semaphore:
                    result = await self.process_video_with_retry(video_id)
                    await asyncio.sleep(self.config.delay_between_requests)
                    return result
                    
            tasks = [process_single(vid) for vid in batch if vid]
            batch_results = await asyncio.gather(*tasks, return_exceptions=True)
            
            processed_results = []
            for idx, result in enumerate(batch_results):
                if isinstance(result, Exception):
                    processed_results.append(VideoData(video_id=batch[idx], error=str(result)))
                else:
                    processed_results.append(result)
                    # 개별 영상 성공 시 progress_callback 호출
                    if not result.error and result.transcript and progress_callback:
                        segment_count = len(result.transcript)
                        progress_callback(f"🎬 {result.video_id}: {segment_count}개 세그먼트 추출 완료")
            
            all_results.extend(processed_results)
            
            success_count = sum(1 for r in processed_results if not r.error)
            error_count = len(processed_results) - success_count
            logger.info(f"✅ 배치 {current_batch} 완료: 성공 {success_count}개, 실패 {error_count}개")
            
            if progress_callback:
                progress_callback(f"배치 {current_batch} 완료: 성공 {success_count}개, 실패 {error_count}개")
            
        return all_results

class GoogleSheetsManager:
    """구글 시트 관리"""

    def __init__(self, credentials_path: str):
        self.credentials_path = credentials_path
        self.client = None
        self.available_sheets = {
            '1': '사용 레퍼런스 영상',
            '2': '쇼핑 레퍼런스 영상',
            '3': '유튜브 재생목록',
            '4': '채널 리스트',
            '5': '영상 리스트'
        }

        # 영상 리스트 시트의 전역함수 헤더 (9행에 함수가 있는 열, 10행부터 데이터 삭제 필요)
        self.video_sheet_global_functions = [
            '숏폼여부', '영상 업로드 이후 수집날짜까지 기간', '일평균 조회수',
            '조회수 100만 이상', '조회수 500만 이상', '조회수 1,000만 이상',
            '구독자 대비 조회수 몇 배 이상', '좋아요 3%이상',
            '채널개설 이후 수집일까지 경과일', '카테고리 분류', '사용 해시태그',
            '후킹자막 유무', '대본유무', '대본 텍스트수'
        ]

        # 채널 리스트 시트의 전역함수 헤더 (9행에 함수가 있는 열, 10행부터 데이터 삭제 필요)
        self.channel_sheet_global_functions = [
            '수집날짜 경과일', '채널전체 조회수(변환)', '영상당 평균 조회수(전투력)',
            '조회수 100만이상 비율', '조회수 500만이상 비율', '조회수 1,000만이상 비율',
            '구독자 대비 조회수배율(최근30개)', '공정성과지수(최근30개)',
            '영상당 구독자수', '구독자1명 당 조회수', '개설 이후 수집날짜까지 기간'
        ]
        
    def authenticate(self):
        """구글 시트 인증"""
        try:
            logger.debug(f"서비스 계정 키 파일 경로: {self.credentials_path}")
            
            scope = [
                'https://spreadsheets.google.com/feeds',
                'https://www.googleapis.com/auth/drive'
            ]
            
            credentials = Credentials.from_service_account_file(
                self.credentials_path, scopes=scope
            )
            
            logger.debug(f"서비스 계정 이메일: {credentials.service_account_email}")
            
            self.client = gspread.authorize(credentials)
            self.drive_service = build('drive', 'v3', credentials=credentials)
            logger.info("✅ 구글 시트 및 드라이브 인증 성공")
            
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
        
    def select_sheet(self, sheet_url: str) -> str:
        """사용할 시트 선택"""
        print("\n사용 가능한 시트:")
        for key, name in self.available_sheets.items():
            print(f"{key}. {name}")

        while True:
            sheet_choice = input("\n사용할 시트 번호를 선택하세요 (1-5): ").strip()
            if sheet_choice in self.available_sheets:
                selected_sheet = self.available_sheets[sheet_choice]
                print(f"'{selected_sheet}' 시트가 선택되었습니다.")
                print("마지막 대본 데이터 이후부터 추출을 시작합니다.")
                return selected_sheet
            else:
                print("잘못된 선택입니다. 1-5 중에서 선택해주세요.")

    def _normalize_header_text(self, text: str) -> str:
        """헤더 텍스트 정규화: 넘버링 제거, 공백 정리"""
        import re
        if not text:
            return ""

        # 넘버링 패턴 제거 (예: "1. ", "2.", "10. " 등)
        normalized = re.sub(r'^\d+\.\s*', '', str(text))

        # 앞뒤 공백 제거
        normalized = normalized.strip()

        return normalized

    def _get_sheet_type(self, sheet_name: str) -> str:
        """시트 타입 결정"""
        if sheet_name == '재생목록ID':
            return 'playlist'
        elif '채널' in sheet_name or sheet_name == '채널 리스트':
            return 'channel'
        else:
            # 채널 리스트, 재생목록ID를 제외한 모든 시트는 영상 리스트 시트
            return 'video'

    def find_header_column(self, sheet, sheet_name: str, header_name: str,
                          required: bool = True, search_rows: Optional[List[int]] = None) -> Optional[int]:
        """
        헤더 열 찾기 (통합 함수)

        Args:
            sheet: 구글 시트 워크시트 객체
            sheet_name: 시트 이름
            header_name: 찾을 헤더 이름 (넘버링 제외)
            required: 필수 헤더 여부 (False면 못 찾아도 None 반환)
            search_rows: 검색할 행 목록 (기본값: [1행, 9행])

        Returns:
            열 번호 (1-based) 또는 None
        """
        try:
            if search_rows is None:
                search_rows = [0, 8]  # 1행과 9행 (0-based 인덱스)

            logger.debug(f"'{sheet_name}' 시트에서 '{header_name}' 열 찾기 시작")
            all_values = sheet.get_all_values()
            logger.debug(f"시트 데이터 읽기 완료: {len(all_values)}행")

            # 정규화된 헤더 이름
            normalized_header = self._normalize_header_text(header_name)

            # 단계 1: 완전일치 검색 (우선순위 높음)
            for row_idx in search_rows:
                if row_idx >= len(all_values):
                    continue

                row = all_values[row_idx]
                for col_idx, cell in enumerate(row):
                    normalized_cell = self._normalize_header_text(cell)

                    # 완전일치
                    if normalized_cell == normalized_header:
                        logger.debug(f"'{header_name}' 열 발견 (완전일치): {row_idx+1}행 {col_idx+1}열 (원본: '{cell}')")
                        return col_idx + 1

            # 단계 2: 부분일치 검색 (가장 빠른 열 반환)
            for row_idx in search_rows:
                if row_idx >= len(all_values):
                    continue

                row = all_values[row_idx]
                for col_idx, cell in enumerate(row):
                    normalized_cell = self._normalize_header_text(cell)

                    # 부분일치 (공백 무시)
                    if normalized_header.replace(' ', '') in normalized_cell.replace(' ', ''):
                        logger.debug(f"'{header_name}' 열 발견 (부분일치): {row_idx+1}행 {col_idx+1}열 (원본: '{cell}')")
                        return col_idx + 1

            # 헤더를 찾지 못한 경우
            if all_values:
                logger.debug(f"1행 헤더: {all_values[0]}")
                if len(all_values) > 8:
                    logger.debug(f"9행 헤더: {all_values[8]}")

            if required:
                raise ValueError(f"'{sheet_name}' 시트에서 '{header_name}' 열을 찾을 수 없습니다.")
            else:
                logger.warning(f"'{sheet_name}' 시트에서 '{header_name}' 열을 찾을 수 없습니다.")
                return None

        except Exception as e:
            if required:
                logger.exception(f"'{sheet_name}' 시트 '{header_name}' 열 찾기 실패:")
                raise ValueError(f"시트 구조 분석 실패: {e}")
            else:
                logger.warning(f"'{sheet_name}' 시트 '{header_name}' 열 찾기 실패: {e}")
                return None
    
    def find_video_id_column(self, sheet, sheet_name: str) -> int:
        """'영상 ID'가 포함된 열 찾기"""
        col = self.find_header_column(sheet, sheet_name, '영상 ID', required=True)
        if col is None:
            raise ValueError(f"'{sheet_name}' 시트에서 '영상 ID' 열을 찾을 수 없습니다.")
        return col
    
    def find_transcript_column(self, sheet, sheet_name: str) -> int:
        """'대본내용'이 포함된 열 찾기"""
        col = self.find_header_column(sheet, sheet_name, '대본내용', required=True)
        if col is None:
            raise ValueError(f"'{sheet_name}' 시트에서 '대본내용' 열을 찾을 수 없습니다.")
        return col
    
    def find_video_duration_column(self, sheet, sheet_name: str) -> Optional[int]:
        """'영상길이'가 포함된 열 찾기"""
        return self.find_header_column(sheet, sheet_name, '영상길이', required=False)
    
    def find_transcript_status_column(self, sheet, sheet_name: str) -> Optional[int]:
        """'대본유무'가 포함된 열 찾기"""
        return self.find_header_column(sheet, sheet_name, '대본유무', required=False)
    
    def is_long_video(self, duration_text: str) -> bool:
        """영상 길이가 40분 이상인지 판별"""
        try:
            if not duration_text:
                return False

            duration_text = str(duration_text).strip()
            logger.debug(f"영상 길이 판별: '{duration_text}'")

            # 시간 형식 파싱 (예: "40분 53초", "1시간 02분 53초", "45분", "1시간 30분")
            import re

            # 시간, 분, 초 추출
            hour_match = re.search(r'(\d+)시간', duration_text)
            minute_match = re.search(r'(\d+)분', duration_text)

            hours = int(hour_match.group(1)) if hour_match else 0
            minutes = int(minute_match.group(1)) if minute_match else 0

            total_minutes = hours * 60 + minutes

            logger.debug(f"영상 길이 파싱 결과: {hours}시간 {minutes}분 (총 {total_minutes}분)")

            return total_minutes >= 40
        except Exception as e:
            logger.warning(f"영상 길이 판별 실패: {duration_text} - {e}")
            return False

    def get_global_function_columns(self, sheet, sheet_name: str) -> List[int]:
        """
        전역함수가 있는 열 찾기

        Args:
            sheet: 구글 시트 워크시트 객체
            sheet_name: 시트 이름

        Returns:
            전역함수 열 번호 리스트 (1-based)
        """
        try:
            # 시트 타입 결정
            sheet_type = self._get_sheet_type(sheet_name)

            # 재생목록ID 시트는 전역함수 없음
            if sheet_type == 'playlist':
                return []

            # 시트 타입에 따른 전역함수 목록 선택
            if sheet_type == 'channel':
                global_functions = self.channel_sheet_global_functions
            else:  # video
                global_functions = self.video_sheet_global_functions

            logger.debug(f"'{sheet_name}' 시트에서 전역함수 열 찾기 시작 ({len(global_functions)}개)")

            # 각 전역함수 헤더에 대해 열 찾기
            global_function_cols = []
            for header_name in global_functions:
                col = self.find_header_column(sheet, sheet_name, header_name, required=False)
                if col is not None:
                    global_function_cols.append(col)
                    logger.debug(f"전역함수 열 발견: '{header_name}' -> {col}열")

            logger.info(f"'{sheet_name}' 시트에서 {len(global_function_cols)}개의 전역함수 열 발견")
            return global_function_cols

        except Exception as e:
            logger.warning(f"'{sheet_name}' 시트에서 전역함수 열 찾기 실패: {e}")
            return []

    def clear_global_function_data(self, sheet, sheet_name: str, global_function_cols: List[int]):
        """
        전역함수 열의 10행부터 데이터 삭제 (1~9행 보존)

        Args:
            sheet: 구글 시트 워크시트 객체
            sheet_name: 시트 이름
            global_function_cols: 전역함수 열 번호 리스트 (1-based)
        """
        try:
            if not global_function_cols:
                logger.debug(f"'{sheet_name}' 시트에 전역함수 열이 없어 데이터 삭제 건너뜀")
                return

            logger.info(f"'{sheet_name}' 시트의 전역함수 열 {len(global_function_cols)}개에서 10행부터 데이터 삭제 시작")

            # 시트의 총 행 수 확인
            all_values = sheet.get_all_values()
            total_rows = len(all_values)

            if total_rows < 10:
                logger.debug(f"'{sheet_name}' 시트의 총 행 수가 10행 미만이므로 삭제할 데이터 없음")
                return

            # 10행부터 마지막 행까지 삭제할 범위 생성
            updates = []
            for col in global_function_cols:
                col_letter = self._col_number_to_letter(col)
                # 10행부터 끝까지 빈 값으로 업데이트
                range_to_clear = f'{col_letter}10:{col_letter}{total_rows}'
                updates.append({
                    'range': range_to_clear,
                    'values': [[''] for _ in range(total_rows - 9)]  # 10행부터이므로 총 (total_rows - 9)개
                })
                logger.debug(f"전역함수 열 {col}({col_letter}) 범위 {range_to_clear} 삭제 예약")

            # 배치 업데이트
            if updates:
                sheet.batch_update(updates)
                logger.info(f"✅ '{sheet_name}' 시트의 전역함수 열 {len(global_function_cols)}개에서 10행부터 데이터 삭제 완료")

        except Exception as e:
            logger.error(f"❌ '{sheet_name}' 시트의 전역함수 열 데이터 삭제 실패: {e}")

    def _col_number_to_letter(self, col_num: int) -> str:
        """열 번호를 열 문자로 변환 (1 -> A, 27 -> AA)"""
        result = ""
        while col_num > 0:
            col_num -= 1
            result = chr(ord('A') + col_num % 26) + result
            col_num //= 26
        return result
    
    def upload_transcript_to_drive(self, video_id: str, title: str, transcript_text: str) -> tuple:
        """대용량 대본을 Google Drive에 업로드 (닥스 및 txt 형식)"""
        try:
            # 폴더명: 날짜_대용량대본
            from datetime import datetime
            folder_name = f"{datetime.now().strftime('%Y%m%d')}_대용량대본"
            
            # 폴더 찾기 또는 생성
            folder_id = self._get_or_create_folder(folder_name)
            
            # 파일명 정리 (특수문자 제거)
            import re
            clean_title = re.sub(r'[<>:"/\\|?*]', '_', title[:50])  # 50자로 제한
            
            docs_url = None
            txt_url = None
            
            # 1. Google Docs 업로드
            try:
                docs_file_metadata = {
                    'name': f"{video_id}_{clean_title}.docs",
                    'parents': [folder_id],
                    'mimeType': 'application/vnd.google-apps.document'
                }
                
                # HTML 형태로 변환하여 업로드 (줄바꿈 처리)
                html_content = transcript_text.replace('\n', '<br>')
                media = MediaInMemoryUpload(
                    html_content.encode('utf-8'), 
                    mimetype='text/html',
                    resumable=True
                )
                
                docs_file = self.drive_service.files().create(
                    body=docs_file_metadata,
                    media_body=media,
                    fields='id, webViewLink'
                ).execute()
                
                docs_url = docs_file.get('webViewLink')
                logger.info(f"📄 {video_id}: Google Docs 업로드 성공 - {docs_url}")
                
            except Exception as e:
                logger.warning(f"⚠️ {video_id}: Google Docs 업로드 실패 - {e}")
            
            # 2. TXT 파일 업로드
            try:
                txt_file_metadata = {
                    'name': f"{video_id}_{clean_title}.txt",
                    'parents': [folder_id]
                }
                
                media = MediaInMemoryUpload(
                    transcript_text.encode('utf-8'), 
                    mimetype='text/plain',
                    resumable=True
                )
                
                txt_file = self.drive_service.files().create(
                    body=txt_file_metadata,
                    media_body=media,
                    fields='id, webViewLink'
                ).execute()
                
                txt_url = txt_file.get('webViewLink')
                logger.info(f"📝 {video_id}: TXT 파일 업로드 성공 - {txt_url}")
                
            except Exception as e:
                logger.warning(f"⚠️ {video_id}: TXT 파일 업로드 실패 - {e}")
            
            return docs_url, txt_url
            
        except Exception as e:
            logger.error(f"❌ {video_id}: Drive 업로드 실패 - {e}")
            return None, None
    
    def _get_or_create_folder(self, folder_name: str) -> str:
        """폴더 찾기 또는 생성"""
        try:
            # 기존 폴더 찾기
            query = f"name='{folder_name}' and mimeType='application/vnd.google-apps.folder'"
            results = self.drive_service.files().list(
                q=query,
                fields='files(id, name)'
            ).execute()
            
            folders = results.get('files', [])
            
            if folders:
                folder_id = folders[0]['id']
                logger.debug(f"📁 기존 폴더 사용: {folder_name} ({folder_id})")
                return folder_id
            
            # 새 폴더 생성
            folder_metadata = {
                'name': folder_name,
                'mimeType': 'application/vnd.google-apps.folder'
            }
            
            folder = self.drive_service.files().create(
                body=folder_metadata,
                fields='id'
            ).execute()
            
            folder_id = folder.get('id')
            logger.info(f"📁 새 폴더 생성: {folder_name} ({folder_id})")
            return folder_id
            
        except Exception as e:
            logger.error(f"❌ 폴더 생성 실패: {folder_name} - {e}")
            # 루트 디렉토리 사용
            return 'root'
            
    def find_last_transcript_row(self, sheet, transcript_col: int) -> int:
        """대본내용 열에서 마지막 데이터가 있는 행 찾기"""
        transcript_values = sheet.col_values(transcript_col)
        last_row = 0
        for i in range(len(transcript_values) - 1, -1, -1):
            if transcript_values[i].strip():
                last_row = i + 1
                break
        return last_row
    
    def get_video_ids_from_sheet(self, sheet_url: str, sheet_name: str, mode: str = 'A') -> Tuple[List[str], int, int, int, int]:
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
            duration_col = self.find_video_duration_column(sheet, sheet_name)
            status_col = self.find_transcript_status_column(sheet, sheet_name)
            
            logger.debug(f"컬럼 위치 - 영상ID: {video_id_col}, 대본내용: {transcript_col}, 영상길이: {duration_col}, 대본유무: {status_col}")
            
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
            
            if mode == 'A':
                start_row = 10
                filtered_ids = [vid.strip() for vid in video_ids[9:] if vid.strip()]
                logger.debug(f"모드 A: 10행부터 {len(filtered_ids)}개 영상 ID 필터링")
            else:
                last_transcript_row = self.find_last_transcript_row(sheet, transcript_col)
                start_row = last_transcript_row + 1
                filtered_ids = [vid.strip() for vid in video_ids[start_row-1:] if vid.strip()]
                logger.debug(f"모드 B: {start_row}행부터 {len(filtered_ids)}개 영상 ID 필터링")
            
            logger.info(f"비디오 ID 추출 완료: {len(filtered_ids)}개 ({start_row}행부터)")
            return filtered_ids, start_row, transcript_col, duration_col, status_col
            
        except Exception as e:
            if "API 사용량 한도 초과" in str(e) or "시트 접근 권한이 없습니다" in str(e) or "시트를 찾을 수 없습니다" in str(e):
                raise
            else:
                logger.exception(f"'{sheet_name}' 시트에서 비디오 ID 가져오기 실패:")
                raise
        
    def update_sheet_with_transcripts(self, sheet_url: str, sheet_name: str, video_data_list: List[VideoData],
                                    start_row: int, transcript_col: int):
        """
        시트에 자막 데이터 업데이트 및 전역함수 열 처리

        Args:
            sheet_url: 구글 시트 URL
            sheet_name: 시트 이름
            video_data_list: 비디오 데이터 리스트
            start_row: 시작 행 번호
            transcript_col: 대본내용 열 번호
        """
        workbook = self.client.open_by_url(sheet_url)
        sheet = workbook.worksheet(sheet_name)

        # 1. 전역함수 열 찾기 및 데이터 삭제 (10행부터)
        logger.info(f"'{sheet_name}' 시트의 전역함수 열 처리 시작")
        global_function_cols = self.get_global_function_columns(sheet, sheet_name)
        if global_function_cols:
            self.clear_global_function_data(sheet, sheet_name, global_function_cols)

        # 2. 대본 데이터 업데이트
        updates = []

        for i, video_data in enumerate(video_data_list):
            row = start_row + i

            if video_data.transcript:
                transcript_text = '\n'.join([f"({ts}) {text}" for ts, text in video_data.transcript])
                col_letter = self._col_number_to_letter(transcript_col)
                updates.append({
                    'range': f'{col_letter}{row}',
                    'values': [[transcript_text]]
                })
            elif video_data.error:
                col_letter = self._col_number_to_letter(transcript_col)
                updates.append({
                    'range': f'{col_letter}{row}',
                    'values': [[f"에러: {video_data.error}"]]
                })

        if updates:
            sheet.batch_update(updates)
            logger.info(f"✅ {len(updates)}개 셀 업데이트 완료")
    
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
            
            # 1단계: 인증 테스트
            logger.info("1단계: 구글 서비스 계정 인증 테스트")
            if not self.client:
                self.authenticate()
            logger.info("✅ 구글 서비스 계정 인증 성공")
            
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
                    logger.error("   1. 구글 시트 우상단 '공유' 버튼 클릭")
                    logger.error("   2. 서비스 계정 이메일 입력 후 '편집자' 권한 부여")
                    raise Exception("시트 접근 권한이 없습니다")
                elif "429" in str(e) or "quota" in error_msg:
                    logger.error("❌ API 사용량 한도를 초과했습니다")
                    logger.error("🔧 해결방법: 잠시 후 다시 시도하거나 요청 간격을 늘리세요")
                    raise Exception("API 사용량 한도 초과")
                else:
                    raise
            
            # 3단계: '쇼핑 레퍼런스 영상' 시트 접근 테스트
            logger.info("3단계: '쇼핑 레퍼런스 영상' 워크시트 접근 테스트")
            try:
                sheet = workbook.worksheet('쇼핑 레퍼런스 영상')
                logger.info("✅ '쇼핑 레퍼런스 영상' 워크시트 접근 성공")
            except Exception as e:
                if "not found" in str(e).lower():
                    logger.error("❌ '쇼핑 레퍼런스 영상' 시트를 찾을 수 없습니다")
                    logger.error("🔧 해결방법: 시트 이름을 확인하거나 해당 시트가 존재하는지 확인하세요")
                    available_sheets = [ws.title for ws in workbook.worksheets()]
                    logger.error(f"   사용 가능한 시트: {available_sheets}")
                    raise Exception("'쇼핑 레퍼런스 영상' 시트를 찾을 수 없습니다")
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
                    logger.info(f"✅ 대본 추출 테스트 성공: {transcript_test_result['transcript_segments']}개 세그먼트")
                    logger.info("🎉 모든 연결 및 대본 추출 테스트 완료!")
                    result['message'] = f"모든 테스트가 성공적으로 완료되었습니다. 대본 추출: {transcript_test_result['transcript_segments']}개 세그먼트"
                else:
                    logger.warning(f"⚠️ 대본 추출 테스트 실패: {transcript_test_result['transcript_error']}")
                    logger.info("🎉 구글 시트 연결 테스트는 완료되었으나 대본 추출에 문제가 있습니다.")
                    result['message'] = f"구글 시트 연결은 성공했으나 대본 추출 실패: {transcript_test_result['transcript_error']}"
            else:
                logger.warning("⚠️ A10 셀이 비어있어 대본 추출 테스트를 건너뜁니다.")
                result['message'] = "구글 시트 연결 테스트는 완료되었으나 테스트할 영상 ID가 없습니다."
            
        except Exception as e:
            logger.exception("❌ 구글 시트 연결 테스트 실패:")
            result['status'] = 'failed'
            result['message'] = str(e)
            
        return result
    
    async def _test_transcript_extraction(self, video_id: str) -> Dict[str, any]:
        """대본 추출 테스트 (읽기 전용)"""
        test_result = {
            'transcript_success': False,
            'transcript_segments': 0,
            'transcript_error': '',
            'transcript_language': '',
            'transcript_urls_tried': [],
            'transcript_final_url': ''
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
                        
                        # 확장프로그램과 동일한 로직으로 자막 추출
                        logger.info(f"6-2-{attempt}N단계: 확장프로그램 로직으로 자막 추출 시도")
                        transcript_result = await extractor._resolve_youtube_data_and_extract(html, video_id)
                        
                        if transcript_result['success']:
                            transcript_count = len(transcript_result['transcript'])
                            logger.info(f"6-2-{attempt}O단계: ✅ {transcript_count}개 자막 세그먼트 추출 성공")
                            
                            # 성공
                            test_result['transcript_success'] = True
                            test_result['transcript_segments'] = transcript_count
                            test_result['transcript_final_url'] = url
                            test_result['transcript_language'] = transcript_result.get('language', 'unknown')
                            logger.info(f"6-2-{attempt}P단계: ✅ {url_type} URL로 대본 추출 테스트 성공!")
                            return test_result
                        else:
                            last_error = transcript_result['error']
                            logger.warning(f"6-2-{attempt}Q단계: 자막 추출 실패 - {last_error}")
                            continue
                        
                    except Exception as e:
                        last_error = str(e)
                        logger.error(f"6-2-{attempt}ERROR단계: {url_type} URL 테스트 실패 - {e}")
                        logger.exception(f"상세 오류 ({video_id}):")
                        continue
                
                # 모든 URL 실패
                test_result['transcript_error'] = f"모든 URL 형식 실패: {last_error}"
                logger.error(f"6-3단계: ❌ 모든 URL 테스트 실패 - {last_error}")
                
        except Exception as e:
            test_result['transcript_error'] = f"대본 추출 테스트 중 예외 발생: {str(e)}"
            logger.exception(f"6-ERROR단계: 대본 추출 테스트 예외:")
            
        return test_result

def setup_interrupt_handler():
    """ESC 키 중단 핸들러 설정"""
    global interrupt_controller
    
    def on_esc_press():
        interrupt_controller.esc_count += 1
        if interrupt_controller.esc_count == 1:
            print(f"\n⚠️  ESC 키가 눌렸습니다. 종료하시겠습니까? (한 번 더 ESC를 누르면 중단됩니다)")
            logger.warning("⚠️  첫 번째 ESC 키 감지 - 한 번 더 누르면 중단됩니다")
            # 3초 후 ESC 카운트 리셋
            threading.Timer(3.0, lambda: setattr(interrupt_controller, 'esc_count', 0)).start()
        elif interrupt_controller.esc_count >= 2:
            print(f"\n🛑 처리를 중단합니다...")
            logger.warning("🛑 두 번째 ESC 키 감지 - 처리 중단")
            interrupt_controller.should_stop = True
    
    try:
        keyboard.on_press_key('esc', lambda _: on_esc_press())
        logger.info("⌨️  ESC 키 중단 기능 활성화됨 (ESC 두 번으로 중단)")
        print("💡 ESC 키를 두 번 누르면 처리를 중단할 수 있습니다.")
    except Exception as e:
        logger.warning(f"⚠️  키보드 후킹 실패 (관리자 권한 필요): {e}")
        print("⚠️  ESC 키 중단 기능을 사용할 수 없습니다. (관리자 권한으로 실행 필요)")

def show_result_summary(start_row: int, end_row: int, success_count: int, error_count: int, elapsed_time: float, mode: str):
    """결과 집계 창 표시"""
    print("\n" + "="*60)
    print("🎉 대본 추출 결과 집계")
    print("="*60)
    print(f"실행 모드: {'전체 추출' if mode == 'A' else '마지막 대본 데이터 이후 추출'}")
    print(f"처리 범위: {start_row}행 ~ {end_row}행")
    print(f"총 처리된 영상: {success_count + error_count}개")
    print(f"✅ 성공한 추출: {success_count}개")
    print(f"❌ 실패한 추출: {error_count}개")
    print(f"⏱️  총 소요시간: {elapsed_time:.1f}초")
    print(f"📊 성공률: {(success_count/(success_count + error_count)*100):.1f}%" if (success_count + error_count) > 0 else "0%")
    print("="*60)

async def main():
    """메인 실행 함수"""
    config = TranscriptConfig(
        target_language=None,
        max_concurrent=2,      # 동시 처리 수 감소  
        retry_attempts=1,
        delay_between_requests=3.0,  # 요청 간 지연 증가
        use_browser_automation=True,  # 브라우저 자동화 사용
        headless=True,  # 헤드리스 모드 (빠른 실행)
        use_user_profile=False  # 필요시 True로 변경
    )
    
    # 환경 설정 (작업환경이 바뀌어도 자동으로 인식되는 상대 경로)
    SCRIPT_DIR = Path(__file__).parent
    # 공유 구글 자격증명 폴더 지원
    key_dir = SCRIPT_DIR / "google_service_key"
    if not (key_dir / "service-account-key.json").exists():
        parent_key_dir = SCRIPT_DIR / ".." / ".." / "google_service_key"
        if (parent_key_dir / "service-account-key.json").exists():
            key_dir = parent_key_dir
    CREDENTIALS_PATH = key_dir / "service-account-key.json"
    SHEET_URL = "https://docs.google.com/spreadsheets/d/1o-Vm5eMfH6Mm1gCnsv-SuRqtVMYTBDnWThBxjwCOptY/edit?gid=495259183#gid=495259183"
    
    try:
        sheets_manager = GoogleSheetsManager(CREDENTIALS_PATH)
        
        # 자동으로 인증하고 진행 (테스트 건너뛰기)
        logger.info("🔑 구글 시트 인증 중...")
        sheets_manager.authenticate()
        
        # 기본적으로 '쇼핑 레퍼런스 영상' 시트 사용
        selected_sheet_name = '쇼핑 레퍼런스 영상'
        logger.info(f"📋 '{selected_sheet_name}' 시트 선택됨")
        
        logger.info(f"📊 '{selected_sheet_name}' 시트에서 비디오 ID 목록 가져오는 중...")
        video_ids, start_row, transcript_col, duration_col, status_col = sheets_manager.get_video_ids_from_sheet(SHEET_URL, selected_sheet_name, 'B')
        
        if not video_ids:
            logger.error("❌ 처리할 비디오 ID를 찾을 수 없습니다.")
            return
            
        logger.info(f"📋 {start_row}행부터 총 {len(video_ids)}개 비디오 처리 예정")
        
        # 브라우저 자동화 시도 후 실패 시 HTTP 방식으로 폴백
        results = None
        elapsed_time = 0
        
        if config.use_browser_automation:
            try:
                logger.info("🌐 브라우저 자동화 모드로 자막 추출 시도")
                
                # 시트 권한 테스트
                logger.info("🔐 시트 쓰기 권한 테스트 중...")
                try:
                    test_workbook = sheets_manager.client.open_by_url(SHEET_URL)
                    test_sheet = test_workbook.worksheet(selected_sheet_name)
                    
                    # 빈 셀에 테스트 데이터 쓰기 후 즉시 삭제
                    test_value = f"권한테스트_{int(time.time())}"
                    test_sheet.update('A1', [[test_value]])
                    time.sleep(1)
                    read_back = test_sheet.cell(1, 1).value
                    if read_back == test_value:
                        logger.info("✅ 시트 쓰기 권한 확인됨")
                        # 테스트 데이터 삭제
                        test_sheet.update('A1', [['']])
                    else:
                        logger.error("❌ 시트 쓰기 권한 테스트 실패 - 데이터 불일치")
                        raise Exception("시트 쓰기 권한 없음")
                except Exception as perm_error:
                    logger.error(f"❌ 시트 쓰기 권한 오류: {perm_error}")
                    logger.error("🔧 해결방법: 서비스 계정을 시트의 '편집자'로 공유했는지 확인하세요")
                    logger.error(f"🔧 서비스 계정 이메일: {sheets_manager.client.auth.service_account_email}")
                    return
                
                # ESC 키 중단 핸들러 설정
                setup_interrupt_handler()
                
                with BrowserTranscriptExtractor(config) as browser_extractor:
                    logger.info("🚀 브라우저 자동화 자막 추출 시작...")
                    start_time = time.time()
                    
                    results = browser_extractor.process_videos_batch(
                        video_ids, 
                        sheets_manager=sheets_manager, 
                        sheet_url=SHEET_URL, 
                        sheet_name=selected_sheet_name, 
                        start_row=start_row, 
                        transcript_col=transcript_col,
                        duration_col=duration_col,
                        status_col=status_col,
                        include_timestamp=True
                    )
                    
                    elapsed_time = time.time() - start_time
                    logger.info("✅ 브라우저 자동화 모드 완료")
            except Exception as e:
                logger.warning(f"⚠️  브라우저 자동화 실패: {e}")
                logger.info("🔄 HTTP 요청 모드로 폴백 시도...")
                config.use_browser_automation = False
        
        if not config.use_browser_automation or results is None:
            logger.info("🔗 HTTP 요청 모드로 자막 추출")
            async with MainYouTubeShortsTranscriptExtractor(config) as extractor:
                logger.info("🚀 HTTP 자막 추출 시작...")
                start_time = time.time()
                
                results = await extractor.process_videos_batch(video_ids, batch_size=50)
                
                elapsed_time = time.time() - start_time
        
        success_count = sum(1 for r in results if not r.error)
        error_count = len(results) - success_count
        
        logger.info("📝 구글 시트 업데이트 중...")
        sheets_manager.update_sheet_with_transcripts(
            SHEET_URL,
            selected_sheet_name,
            results,
            start_row,
            transcript_col
        )
        
        end_row = start_row + len(results) - 1
        show_result_summary(start_row, end_row, success_count, error_count, elapsed_time, 'B')
            
    except Exception as e:
        logger.exception("❌ 메인 실행 중 예상치 못한 오류 발생:")

if __name__ == "__main__":
    # 필요한 패키지 설치 안내
    required_packages = [
        "aiohttp",
        "gspread",
        "google-auth",
        "google-auth-oauthlib",
        "google-auth-httplib2",
        "brotli"  # Brotli 압축 지원용
    ]
    
    print("필요한 패키지:")
    print("pip install " + " ".join(required_packages))
    print()
    
    # 실행
    asyncio.run(main())