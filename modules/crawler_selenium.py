import os
import re
import time
import random
import threading
from datetime import datetime
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager
from bs4 import BeautifulSoup
import pandas as pd
from config import Config
from logger_config import setup_logger, log_exception
from modules.utils import (
    parse_korean_number_string, 
    play_sound, 
    clean_text, 
    get_chrome_profile_path, 
    show_notification, 
    play_notification_sound
)

# selenium-stealth 임포트
from selenium_stealth import stealth

logger = setup_logger('crawler')


# play_completion_sound는 utils.py의 play_sound()로 대체됨 (PLAN.md 4.1)


class PlayboardCrawler:
    """
    개선된 Playboard 크롤러
    - 동적 URL 지원
    - 향상된 데이터 추출 (Video ID, Channel ID, 순위 증감)
    - webdriver-manager로 자동 ChromeDriver 설치
    """
    # 병렬 기동 시 ChromeDriver 다운로드 및 기동 동시성 제어를 위한 스레드 락
    _driver_init_lock = threading.Lock()

    def __init__(self, headless=False, worker_id=1):
        self.headless = headless
        self.worker_id = worker_id
        self.driver = None
        self.stop_requested = False
        self.resume_requested = False

    def random_delay(self, min_val=0.5, max_val=1.5):
        """
        사람과 유사한 동작 모사를 위해 임의의 딜레이를 클릭/입력/동작 사이에 적용합니다.
        """
        delay = random.uniform(min_val, max_val)
        logger.debug(f"[Delay] Sleeping for {delay:.2f} seconds to simulate human behavior...")
        time.sleep(delay)

    @staticmethod
    def parse_numeric_field(text):
        """
        텍스트에서 숫자 추출 및 변환 (K, M, B 단위 처리)

        Args:
            text (str): 변환할 텍스트 ("1.2M", "350K", "1,234", "N/A" 등)

        Returns:
            str: 정제된 문자열 (숫자가 아니면 'N/A')

        Examples:
            "1.2M" -> "1.2M"
            "350K views" -> "350K"
            "#kpop music" -> "N/A"
            "1,234,567" -> "1,234,567"
        """
        if not text or text == 'N/A':
            return 'N/A'

        # 숫자와 관련된 패턴만 추출 (K, M, B 단위 포함)
        pattern = r'([\d,\.]+[KMB]?)'
        match = re.search(pattern, text)

        if match:
            return match.group(1)

        # 순수 숫자만 있는 경우
        if re.match(r'^[\d,\.]+$', text.strip()):
            return text.strip()

        return 'N/A'

    @staticmethod
    def log_parsing_failure(row_html, idx, error):
        """
        파싱 실패 시 HTML 구조 로깅 및 스냅샷 저장 (PLAN.md 6.0)

        Args:
            row_html: BeautifulSoup row 객체
            idx (int): 행 인덱스
            error (Exception): 발생한 에러
        """
        logger.warning(f"Parsing failed for row #{idx}: {error}")
        logger.debug(f"Failed row HTML snippet (first 500 chars): {str(row_html)[:500]}")

        # HTML 스냅샷 저장
        try:
            error_dir = 'logs/error_html'
            os.makedirs(error_dir, exist_ok=True)

            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            error_file = os.path.join(error_dir, f'error_row_{idx}_{timestamp}.html')

            with open(error_file, 'w', encoding='utf-8') as f:
                f.write(str(row_html))

            logger.info(f"Error HTML snapshot saved: {error_file}")
        except Exception as e:
            logger.error(f"Failed to save error HTML snapshot: {e}")

    def _init_driver(self):
        """Chrome WebDriver 초기화 (초기화 락, 랜덤 디버깅 포트, 백그라운드 최적화)"""
        if self.driver is not None:
            try:
                # 드라이버 세션 확인
                _ = self.driver.current_url
                logger.info("Existing Chrome session is active. Reusing driver.")
                return
            except Exception:
                logger.info("Existing Chrome session is invalid. Quit and re-initialize.")
                try:
                    self.driver.quit()
                except:
                    pass
                self.driver = None

        # [원칙] 네이버/플레이보드 보안 감지 회피 및 디버깅을 용이하게 하기 위해 GUI 모드(headless=False) 실행을 우선합니다.
        profile_path = get_chrome_profile_path()
        logger.info(f"Using Chrome profile path: {profile_path}")

        chrome_options = Options()
        if self.headless:
            chrome_options.add_argument('--headless')
            
        # 병렬 기동 시 포트 충돌 방지를 위해 Remote Debugging Port를 랜덤(9300~9500)하게 할당
        debug_port = random.randint(9300, 9500)
        chrome_options.add_argument(f"--remote-debugging-port={debug_port}")
        logger.info(f"Allocated Chrome remote debugging port: {debug_port}")

        # Gpu 및 UI 렌더링 관련 불필요한 시스템 에러 로그 및 개발자 도구 수신 포트 정보 콘솔 출력 방지
        chrome_options.add_argument('--log-level=3')
        chrome_options.add_argument('--silent')
        chrome_options.add_experimental_option('excludeSwitches', ['enable-logging'])

        chrome_options.add_argument('--no-sandbox')
        chrome_options.add_argument('--disable-dev-shm-usage')
        chrome_options.add_argument('--disable-blink-features=AutomationControlled')
        chrome_options.add_argument('--disable-gpu')
        chrome_options.add_argument('--window-size=1920,1080')
        chrome_options.add_argument(f"--user-data-dir={profile_path}")

        # 백그라운드 구동 스로틀링 완전 비활성화 (최소화 상태에서도 속도 지연 방지)
        chrome_options.add_argument('--disable-background-timer-throttling')
        chrome_options.add_argument('--disable-backgrounding-occluded-windows')
        chrome_options.add_argument('--disable-renderer-backgrounding')
        chrome_options.add_argument('--disable-features=CalculatePageVisibility')
        chrome_options.add_argument('--disable-features=IntensiveWakeUpThrottling')
        chrome_options.add_argument('--disable-features=ThrottleDelayableQueueOnCpuUsage')
        chrome_options.add_argument('--disable-infobars')  # "자동화된 소프트웨어..." 바 제거
        logger.debug("Background optimization options enabled")

        # 자동화 탐지 방지
        chrome_options.add_experimental_option("excludeSwitches", ["enable-automation"])
        chrome_options.add_experimental_option('useAutomationExtension', False)

        driver_initialized = False

        # 초기화 락 (Driver Lock)을 통해 ChromeDriverManager 설치 및 기동 프로세스 동시성 보호
        with PlayboardCrawler._driver_init_lock:
            # 1차 시도: webdriver-manager 사용 (버전 매칭이 가장 완벽하여 최우선)
            if not driver_initialized:
                try:
                    logger.info("Attempting to download and install ChromeDriver with webdriver-manager...")
                    service = Service(ChromeDriverManager().install())
                    self.driver = webdriver.Chrome(service=service, options=chrome_options)
                    logger.info("✓ ChromeDriver initialized with webdriver-manager")
                    driver_initialized = True
                except Exception as e:
                    logger.warning(f"[Driver Init] Fallback ChromeDriver installation via webdriver-manager failed: {e}")

            # 2차 시도: 시스템 PATH의 ChromeDriver 사용
            if not driver_initialized:
                try:
                    logger.debug("Attempting to use ChromeDriver from system PATH...")
                    self.driver = webdriver.Chrome(options=chrome_options)
                    logger.info("✓ ChromeDriver initialized from system PATH")
                    driver_initialized = True
                except Exception as e:
                    logger.warning(f"[Driver Init] System PATH ChromeDriver not available: {e}")

            # 3차 시도: 프로젝트 내 chromedriver.exe 확인
            if not driver_initialized:
                try:
                    local_driver_path = "chromedriver.exe"
                    if os.path.exists(local_driver_path):
                        logger.debug(f"Attempting to use local ChromeDriver: {local_driver_path}")
                        service = Service(local_driver_path)
                        self.driver = webdriver.Chrome(service=service, options=chrome_options)
                        logger.info("✓ ChromeDriver initialized from project directory")
                        driver_initialized = True
                except Exception as e:
                    logger.warning(f"[Driver Init] Local ChromeDriver in project directory failed: {e}")

            # 모든 시도 실패
            if not driver_initialized:
                error_msg = "ChromeDriver initialization failed. Please install ChromeDriver manually."
                logger.error(error_msg)
                raise RuntimeError(error_msg)

            # 봇 탐지 우회 설정 (selenium-stealth 적용)
            try:
                stealth(
                    self.driver,
                    languages=["ko-KR", "ko"],
                    vendor="Google Inc.",
                    platform="Win32",
                    webgl_vendor="Intel Inc.",
                    renderer="Intel Iris OpenGL Engine",
                    fix_hairline=True,
                )
                logger.info("✓ selenium-stealth successfully applied for bot bypass")
            except Exception as e:
                logger.warning(f"Failed to apply selenium-stealth: {e}")

        # 추가 WebDriver 마스킹 스크립트 실행
        try:
            self.driver.execute_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
            
            # 창 크기 기본 최대화 (최소화 상태에서도 기본 레이아웃 안정을 위해 처리하되 강제 팝업 포커싱은 제거)
            self.driver.maximize_window()
            logger.debug("ChromeDriver configured successfully (maximized)")
        except Exception as e:
            logger.warning(f"Failed to configure ChromeDriver properties: {e}")

    def _check_login_wall(self):
        """
        [PLAN.md Phase 2.2] 로그인 월(Login Wall) 정밀 감지

        "로그인하여 더 보기" 버튼이나 팝업이 있는지 확인하여
        실제 로그인 제한인지, 스크롤 트리거 문제인지 판단

        Returns:
            bool: 로그인 월이 감지되면 True
        """
        try:
            # Playboard 로그인 월 감지 (다양한 패턴)
            login_wall_indicators = [
                "//button[contains(text(), 'Sign in')]",
                "//button[contains(text(), 'Login')]",
                "//button[contains(text(), '로그인')]",
                "//div[contains(text(), '로그인하여 더 보기')]",
                "//div[contains(text(), 'Sign in to see more')]",
                "//div[contains(text(), 'Login to continue')]",
                "//a[contains(text(), 'Sign up')]",
                "//div[contains(@class, 'login-wall')]",
                "//div[contains(@class, 'auth-required')]",
            ]

            for xpath in login_wall_indicators:
                try:
                    element = self.driver.find_element(By.XPATH, xpath)
                    if element.is_displayed():
                        logger.debug(f"[Scroll Debug] Login wall element found: {xpath}")
                        return True
                except:
                    continue

            logger.debug("[Scroll Debug] No login wall elements found")
            return False
        except Exception as e:
            logger.debug(f"[Scroll Debug] Login wall check error: {e}")
            return False

    def _human_like_scroll(self):
        """
        사람처럼 스크롤하는 로직 (봇 탐지 회피 강화)
        - 불규칙한 스크롤 양
        - 가끔 위로 올리기 (읽는 척)
        - 랜덤 대기 시간
        """
        try:
            # 1. 스크롤 높이의 70~90% 랜덤하게 내림
            scroll_amount = random.randint(300, 700)
            self.driver.execute_script(f"window.scrollBy(0, {scroll_amount});")

            # 2. 아주 짧은 대기 (시각적 인식 시간)
            self.random_delay(0.5, 1.2)

            # 3. 가끔(30% 확률) 살짝 위로 올림 (읽는 척)
            if random.random() < 0.3:
                up_scroll = random.randint(-200, -50)
                self.driver.execute_script(f"window.scrollBy(0, {up_scroll});")
                self.random_delay(0.5, 0.8)
                logger.debug("Human-like behavior: scrolled up briefly")
        except Exception as e:
            logger.debug(f"Human scroll error: {e}")

    def _scroll_to_load_items(self, target_count=100, max_attempts=None):
        """
        무한 스크롤로 아이템 로딩 (Optimized JavaScript Scrolling)
        - div.current 순위 텍스트 최댓값을 분석하여 target_count 도달 여부 판정
        - 비로그인 모드 수집 도중 로그인 팝업(login wall) 발견 시 사용자 알림 및 조기 중단
        """
        if max_attempts is None:
            max_attempts = Config.MAX_SCROLL_ATTEMPTS

        logger.info(f"Starting optimized scroll for {target_count} items...")
        items_loaded = 0
        attempts = 0
        no_change_count = 0

        from selenium.webdriver.support.ui import WebDriverWait
        from selenium.common.exceptions import TimeoutException

        while attempts < max_attempts:
            # 1. 프로세스 중단 요청 확인
            if self.stop_requested:
                logger.warning("🛑 [System] 스크롤 루프 도중 프로세스 중단 요청이 감지되었습니다. 스크롤을 즉시 중단합니다.")
                break

            # 2. 로그인 벽 감지 (로그인 팝업이 떴는지 모니터링)
            if self._check_login_wall():
                logger.warning("⚠ [Scroll] 로그인 팝업(Login Wall)이 감지되었습니다. 크롤링을 조기 중단하고 사용자에게 알림을 발송합니다.")
                try:
                    play_notification_sound()
                    show_notification(
                        f"[워커 {self.worker_id}번 브라우저] 로그인 요구 팝업 감지",
                        "크롤링 중 로그인 팝업이 감지되었습니다. 수집이 조기 중단되며 수집 완료 처리됩니다."
                    )
                except Exception as alert_err:
                    logger.warning(f"Failed to trigger login wall notification: {alert_err}")
                break

            # 백그라운드 상태(최소화 등) 모니터링
            try:
                is_hidden = self.driver.execute_script("return document.hidden;")
                if is_hidden:
                    logger.debug("[System] Browser is running in background (minimized/occluded)")
            except Exception as e:
                logger.debug(f"[System] Visibility check failed: {e}")

            # Human-like Scrolling (봇 탐지 회피 강화)
            self._human_like_scroll()

            # [PLAN.md Phase 1.3.B] Element-Based Stepped Scrolling (Pure JavaScript)
            # 마지막 요소를 찾아서 화면 중앙에 위치시켜 Lazy Loading 트리거
            try:
                rows = self.driver.find_elements(By.CSS_SELECTOR, "tr.chart__row")
                
                # div.current 순위 텍스트 직접 감지하여 최대 순위 도출
                current_count = 0
                try:
                    rank_elements = self.driver.find_elements(By.CSS_SELECTOR, "div.current")
                    ranks = []
                    for el in rank_elements:
                        txt = el.text.strip()
                        if txt.isdigit():
                            ranks.append(int(txt))
                    if ranks:
                        current_count = max(ranks)
                        logger.debug(f"[Scroll] Current max rank by elements: {current_count}")
                    else:
                        current_count = len(rows)
                except Exception as rank_err:
                    logger.debug(f"Failed to parse ranks via element text: {rank_err}")
                    current_count = len(rows)

                # 목표 달성 시 즉시 종료
                if current_count >= target_count:
                    logger.info(f"Target reached: Rank {current_count} matches or exceeds target {target_count}")
                    return current_count

                if rows and len(rows) > 0:
                    last_row = rows[-1]
                    # 마지막 요소를 화면 중앙으로 스크롤 (Lazy Loading 트리거)
                    try:
                        self.driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", last_row)
                    except:
                        pass

                    # Lazy Loading 트리거를 위해 약간 더 내림
                    self.driver.execute_script("window.scrollBy(0, 300);")
                else:
                    # Fallback: 기존 방식
                    self.driver.execute_script("window.scrollBy(0, 500);")
            except Exception as e:
                logger.warning(f"[Scroll] Element-based scroll failed (elements might not be loaded yet): {e}, using viewport fallback")
                self.driver.execute_script("window.scrollBy(0, 500);")

            # [PLAN.md Phase 1.3.B] 고정 대기 제거 -> 동적 대기
            # 이전 개수보다 늘어날 때까지 최대 3초 대기 (늘어나면 즉시 탈출)
            try:
                WebDriverWait(self.driver, 3).until(
                    lambda d: len(d.find_elements(By.CSS_SELECTOR, "tr.chart__row")) > len(rows)
                )
                # 아이템이 늘어났으면 바로 다음 루프로 (속도 향상)
                new_count = len(self.driver.find_elements(By.CSS_SELECTOR, "tr.chart__row"))
                logger.info(f"[Scroll] Loaded rows count: {len(rows)} -> {new_count}")
                items_loaded = current_count
                no_change_count = 0
                attempts += 1
                continue
            except TimeoutException:
                # 3초 동안 안 늘어나면 Wiggle 시도
                pass

            # 현재 로드된 실제 순위 최댓값 확인
            soup = BeautifulSoup(self.driver.page_source, 'html.parser')
            current_ranks = []
            
            # td.rank 내 div.current 또는 일반 div.current 파싱
            rank_divs = soup.select('td.rank div.current')
            if not rank_divs:
                rank_divs = soup.select('div.current')
                
            for d in rank_divs:
                text_val = d.get_text(strip=True)
                if text_val.isdigit():
                    current_ranks.append(int(text_val))
                    
            if current_ranks:
                new_items_loaded = max(current_ranks)
                logger.info(f"Scroll {attempts + 1}: Max rank loaded: {new_items_loaded}/{target_count}")
            else:
                # Fallback: 행 개수 기준 카운팅
                items = soup.select('table.sheet tbody tr.chart__row')
                if not items:
                    items = soup.select('tbody tr')
                new_items_loaded = len(items)
                logger.info(f"Scroll {attempts + 1}: Items loaded (rows count fallback): {new_items_loaded}/{target_count}")

            # 목표 달성 확인
            if new_items_loaded >= target_count:
                logger.info(f"Target reached: {new_items_loaded} items")
                break

            # 이전 수집 상태와 변화 없음 감지
            if new_items_loaded == items_loaded:
                no_change_count += 1
                if new_items_loaded >= 20 and new_items_loaded <= 25 and no_change_count >= 2:
                    login_wall_detected = self._check_login_wall()
                    if login_wall_detected:
                        logger.warning("=" * 80)
                        logger.warning("⚠ LOGIN WALL DETECTED: '로그인하여 더 보기' 버튼 또는 팝업 발견")
                        logger.warning("크롤링 중 로그인 팝업이 감지되었습니다. 수집이 조기 중단되며 수집 완료 처리됩니다.")
                        logger.warning("=" * 80)
                        try:
                            play_notification_sound()
                            show_notification(
                                f"[워커 {self.worker_id}번 브라우저] 로그인 요구 팝업 감지",
                                "비로그인 상태로 크롤링 중 로그인 팝업이 감지되었습니다. 계속 수집하려면 로그인 모드를 활성화하세요."
                            )
                        except Exception as alert_err:
                            logger.warning(f"Failed to trigger login wall notification: {alert_err}")
                        break

                # [PLAN.md Phase 1.3.B] Wiggle Scrolling (JavaScript 사용)
                # 3회 연속 변화 없음 시 (동일 개수 4회 반복) Wiggle 및 브라우저 강제 포커싱을 통해 백그라운드 스로틀링 해제
                if no_change_count >= 3:
                    logger.info(f"[Scroll] Wiggle attempt at {new_items_loaded} items (no_change: {no_change_count})...")
                    self.driver.execute_script("window.scrollBy(0, -200);")
                    self.random_delay(0.1, 0.3)
                    self.driver.execute_script("window.scrollBy(0, 200);")
                    self.random_delay(0.4, 0.7)

                    logger.info(f"[Scroll] {no_change_count}회 연속 로딩 정체 감지. 백그라운드 지연 해제를 위해 브라우저 강제 포커싱 시도...")
                    try:
                        self.driver.switch_to.window(self.driver.current_window_handle)
                        self.driver.execute_script("window.focus();")
                        logger.info("✓ [Scroll] 브라우저 창을 포커싱 완료했습니다.")
                    except Exception as focus_err:
                        logger.debug(f"Failed to focus browser window: {focus_err}")

                # [PLAN.md Phase 1.3.B] 10회 연속 변화 없음 시 중단
                if no_change_count >= 10:
                    logger.warning(f"⚠ [Crawl Warning] No more items loading after 10 attempts. Stopping at {new_items_loaded} items")
                    logger.warning(f"목표 수량인 {target_count}개에 미치지 못하고 스크롤이 정지되었습니다.")
                    if new_items_loaded <= 25:
                        logger.warning("Note: Playboard may require user login (login_mode=True) for more than ~20 items")
                    break
            else:
                items_loaded = new_items_loaded
                no_change_count = 0

            attempts += 1
            # 최소 안정화 시간
            self.random_delay(0.4, 0.8)

        return new_items_loaded if new_items_loaded > 0 else items_loaded

    def _check_login_status_quick(self):
        """빠른 로그인 여부 체크 (수동 재개 시 사용)"""
        try:
            soup = BeautifulSoup(self.driver.page_source, 'html.parser')
            rows = soup.select('table.sheet tbody tr.chart__row')
            if not rows:
                rows = soup.select('tbody tr')
            return len(rows) > 30
        except:
            return False

    def _check_login_status_by_element(self):
        """
        사용자가 제시한 구체적인 요소를 기반으로 로그인 성공 여부를 정밀 판정합니다.
        Returns:
            bool: 로그인에 성공한 상태이면 True, 로그인이 필요한 상태이면 False
        """
        try:
            # 1. 미로그인 상태 마커 요소(로그인 유도 버튼)를 최우선으로 검사
            # 해당 요소들이 하나라도 노출되고 있으면 무조건 비로그인 상태(False)로 간주합니다.
            signin_selectors = [
                "div.menu__item--signin",
                "a[href*='/account/signin']",
                "a[data-interaction-type='clickSignin']"
            ]
            for sel in signin_selectors:
                elements = self.driver.find_elements(By.CSS_SELECTOR, sel)
                for el in elements:
                    if el.is_displayed():
                        logger.debug(f"[Login Check] Visible sign-in element found: {sel}. User is NOT logged in.")
                        return False

            # XPath를 이용해 텍스트가 '로그인'인 링크도 검사
            login_buttons = self.driver.find_elements(By.XPATH, "//a[contains(@href, '/account/signin') and contains(text(), '로그인')]")
            for btn in login_buttons:
                if btn.is_displayed():
                    logger.debug("[Login Check] Visible login button found via XPath. User is NOT logged in.")
                    return False

            # 2. 로그인 성공 표시 요소가 존재하는지 검사
            signed_indicators = [
                "div.menu.menu--signed",
                "div.menu__item.menu__picture"
            ]
            # 비로그인 상태에서도 오인되기 쉬운 단순 'div.profile-image' 대신 명확한 세션 서브셋만 우선 검사
            for sel in signed_indicators:
                elements = self.driver.find_elements(By.CSS_SELECTOR, sel)
                if elements:
                    logger.debug(f"[Login Check] Found login success indicator element: {sel}")
                    return True

            # profile-image도 확인하되, 미로그인 요소를 통과한 뒤에만 보조적으로 검사
            profile_images = self.driver.find_elements(By.CSS_SELECTOR, "div.profile-image")
            if profile_images:
                logger.debug("[Login Check] Found profile-image element (fallback check). Assuming logged in.")
                return True

            # 기본적으로 미로그인 마커 요소가 보이지 않으면 로그인 완료로 판단
            logger.debug("[Login Check] No login elements visible. Assuming logged in.")
            return True
        except Exception as e:
            logger.warning(f"Error checking login status by element: {e}")
            return self._check_login_status_quick()

    def _wait_for_login(self, max_wait_time=3600, check_interval=5.0):
        """
        로그인 대기 및 완료 감지 (수동 재개 및 프로세스 중단 연동)
        - 자동 진행 타임아웃 대신 사용자가 수동 재개를 클릭할 때까지 무한 대기 (최대 1시간 지정)
        - 플레이보드 로그인 페이지로 바로 이동하여 직접 로그인 대기
        - 실제 로그인 상태 검증 요소를 정밀 판별하여 대기
        """
        logger.info("=" * 60)
        logger.info("[로그인 모드] 로그인 여부를 검증하고 있습니다...")
        logger.info("=" * 60)

        # 1. 사전 상태 검사: 이미 로그인된 상태이면 대기하지 않고 진행
        if self._check_login_status_by_element():
            logger.info("✓ [로그인 모드] 이미 로그인된 세션이 확인되었습니다. 크롤링을 즉시 시작합니다.")
            return True

        original_url = self.driver.current_url
        logger.warning("⚠ [로그인 모드] 로그인 정보가 확인되지 않았습니다. 사용자 로그인이 필요합니다.")
        logger.info("[로그인 안내] 플레이보드 로그인 페이지로 전환합니다. 브라우저에서 로그인을 완료한 후 대시보드에서 '수동 재개' 버튼을 클릭해 주세요.")

        # 플레이보드 로그인 페이지 리다이렉트
        try:
            login_url = "https://playboard.co/account/signin"
            logger.info(f"이동 중: {login_url}")
            self.driver.get(login_url)
            time.sleep(2)
        except Exception as redirect_err:
            logger.warning(f"Failed to redirect to Playboard signin page: {redirect_err}")

        # OS 알림음 재생 및 알림 메시지 노출 (Speech Off.wav 탑재)
        try:
            play_notification_sound()
            show_notification(
                f"[워커 {self.worker_id}번 브라우저] 로그인 대기 요구",
                "구글 로그인 세션이 필요합니다. 브라우저에서 로그인을 완료한 후 대시보드에서 '수동 재개'를 눌러주세요."
            )
        except Exception as alert_err:
            logger.warning(f"Failed to trigger login notification: {alert_err}")

        start_time = time.time()

        while time.time() - start_time < max_wait_time:
            # 1. 중단 플래그 검사
            if self.stop_requested:
                logger.warning("🛑 [System] 로그인 대기 중 프로세스 중단 요청이 감지되어 대기를 종료합니다.")
                return False

            # 2. 수동 재개 플래그 검사
            if self.resume_requested:
                logger.info("⏯️ [System] 사용자 수동 재개 요청 감지! 플레이보드 페이지로 복귀하여 로그인 상태를 확인합니다.")
                self.resume_requested = False  # 플래그 초기화
                
                try:
                    # 원래 페이지로 돌아가기
                    self.driver.get(original_url)
                    time.sleep(3)
                except Exception as restore_err:
                    logger.error(f"Failed to restore original url {original_url}: {restore_err}")
                
                # 플레이보드에서 로그인 성공 여부 검사
                if self._check_login_status_by_element():
                    logger.info("✓ [System] 수동 재개 성공: 로그인이 확인되었습니다.")
                    self.driver.execute_script("window.scrollTo(0, 0);")
                    time.sleep(1)
                    return True
                else:
                    logger.warning("⚠ [System] 수동 재개 실패: 아직 로그인이 완료되지 않았거나 세션 로드가 되지 않았습니다. 로그인 페이지로 재이동합니다.")
                    try:
                        self.driver.get("https://playboard.co/account/signin")
                        time.sleep(2)
                    except:
                        pass

            elapsed = int(time.time() - start_time)
            remaining = max_wait_time - elapsed

            logger.info(f"로그인 확인 중... (경과: {elapsed}초, 남은 시간: {remaining}초 / '수동 재개' 가능)")

            # check_interval 대기 시, 0.5초 간격으로 쪼개서 중단 요청을 빠르게 폴링
            for _ in range(int(check_interval / 0.5)):
                if self.stop_requested:
                    return False
                time.sleep(0.5)

        logger.warning(f"로그인 대기 시간 초과 ({max_wait_time}초)")
        logger.warning("비로그인 상태로 진행합니다. (약 20개만 수집 가능)")
        try:
            self.driver.get(original_url)
            time.sleep(2)
        except:
            pass
        return False

    def _extract_channel_id(self, href):
        """URL에서 Channel ID 추출"""
        if not href:
            return 'N/A'

        try:
            if '/en/channel/' in href:
                return href.split('/en/channel/')[-1].split('?')[0]
            elif '/channel/' in href:
                return href.split('/channel/')[-1].split('?')[0]
        except Exception as e:
            logger.error(f"Error extracting channel ID from {href}: {e}")

        return 'N/A'

    def crawl(self, url, target_type='shorts', login_mode=False, target_count=None, country='한국', period='일간', ranking_date=None, ranking_criteria='조회수 순위', start_rank=0, keep_open=False, category='전체'):
        """
        통합 크롤링 메서드

        Args:
            url (str): 크롤링할 URL
            target_type (str): 'shorts', 'video', 'channel'
            login_mode (bool): 로그인 모드 활성화
            target_count (int): 수집할 아이템 수
            country (str): 국가 정보 (PLAN.md 3.5 - 메타 데이터)
            period (str): 기간 구분 (일간, 주간, 월간)
            ranking_date (str): 랭킹 기준 날짜 (YYYY-MM-DD 형식)

        Returns:
            pd.DataFrame: 크롤링된 데이터
        """
        # ranking_date가 없으면 오늘 날짜 사용
        if ranking_date is None:
            ranking_date = datetime.now().strftime('%Y-%m-%d')
        if target_count is None:
            target_count = Config.MAX_ITEMS_WITH_LOGIN if login_mode else Config.MAX_ITEMS_NO_LOGIN

        try:
            self._init_driver()
            logger.info(f"Starting crawl: {url}")
            logger.info(f"Target type: {target_type}, Login: {login_mode}, Count: {target_count}")

            # 비로그인 대용량 수집 정보 안내
            if target_count > 40 and not login_mode:
                logger.info("=" * 80)
                logger.info(f"💡 [안내] 비로그인 모드로 {target_count}개 수집을 시도합니다.")
                logger.info("수집 도중 로그인 유도 팝업이 노출될 경우 수집이 조기 중단될 수 있습니다.")
                logger.info("=" * 80)

            if self.stop_requested:
                logger.warning("🛑 [System] 크롤링 기동 직전 중단 요청이 감지되었습니다.")
                return pd.DataFrame()

            self.driver.get(url)
            
            for _ in range(6):
                if self.stop_requested:
                    return pd.DataFrame()
                time.sleep(0.5)

            if login_mode:
                self._wait_for_login()
                if self.stop_requested:
                    return pd.DataFrame()

            # 탭 클릭을 통한 수집 기준 변경 (조회수 순위, 좋아요 순위, 댓글 순위)
            # 채널 랭킹은 탭 목록이 다르거나 없을 수 있으므로 video/shorts 일 때만 동작
            if target_type != 'channel' and ranking_criteria:
                logger.info(f"[Criteria] 수집 기준 변경 시도: {ranking_criteria}")
                try:
                    # 앞뒤 공백이 존재할 수 있으므로 contains 매칭 활용
                    xpath = f"//li[contains(@class, 'item')]//span[contains(text(), '{ranking_criteria}')]"
                    tab_element = WebDriverWait(self.driver, 10).until(
                        EC.element_to_be_clickable((By.XPATH, xpath))
                    )
                    self.driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", tab_element)
                    self.random_delay(0.5, 1.0)
                    self.driver.execute_script("arguments[0].click();", tab_element)
                    logger.info(f"✓ [Criteria] 수집 기준을 '{ranking_criteria}'(으)로 전환 완료했습니다.")
                    
                    # 탭 전환 후 데이터 갱신을 위해 대기
                    self.random_delay(2.0, 3.0)
                except Exception as tab_err:
                    logger.warning(f"⚠ [Criteria] 수집 기준 탭 '{ranking_criteria}' 전환 실패 (기본 설정으로 구동될 수 있음): {tab_err}")

            # 스크롤하여 데이터 로드
            items_loaded = self._scroll_to_load_items(target_count)
            if self.stop_requested:
                return pd.DataFrame()

            # 페이지 소스 파싱
            soup = BeautifulSoup(self.driver.page_source, 'html.parser')

            # 재시도 로직 (PLAN.md 6.2): 요소를 찾지 못하면 1회 새로고침
            retry_attempted = False
            rows = soup.select('table.sheet tbody tr.chart__row')
            if not rows and not retry_attempted:
                logger.warning("⚠ [Warning] No elements ('tr.chart__row') found on page. Attempting page refresh to reload...")
                self.driver.refresh()
                time.sleep(3)
                items_loaded = self._scroll_to_load_items(target_count)
                soup = BeautifulSoup(self.driver.page_source, 'html.parser')
                rows = soup.select('table.sheet tbody tr.chart__row')
                retry_attempted = True
                logger.info("Page refresh and scroll reload completed")
                
            if not rows:
                logger.error("❌ [Error] Failed to find table elements ('tr.chart__row') even after page refresh!")
                logger.error("보안 로그인 만료 또는 네트워크 접속 차단, 혹은 플레이보드 레이아웃 구조 변경 가능성이 있습니다.")

            # 타겟 타입에 따라 다른 파싱 메서드 호출
            if target_type == 'channel':
                data = self._parse_channels(soup, target_count, country, period, ranking_date, start_rank, category, ranking_criteria)
            else:  # shorts or video
                data = self._parse_videos(soup, target_count, target_type, country, period, ranking_date, start_rank, category, ranking_criteria)

            df = pd.DataFrame(data)

            # 수집 요약 로그 (Video ID 제거)
            logger.info("=" * 80)
            logger.info("📊 CRAWLING SUMMARY")
            logger.info("=" * 80)
            logger.info(f"  Target Count     : {target_count}")
            logger.info(f"  Items Collected  : {len(df)}")
            logger.info(f"  Items Loaded     : {items_loaded}")

            # 데이터 품질 체크 (필수 필드 기준)
            if target_type != 'channel':
                valid_items = sum(1 for item in data if item.get('Video Title') != 'N/A' and item.get('Views', 0) > 0)
                data_quality = (valid_items / len(data) * 100) if data else 0
                logger.info(f"  Valid Items      : {valid_items} (Title + Views 존재)")
                logger.info(f"  Data Quality     : {data_quality:.1f}%")

            # 중단 사유 판단
            if len(df) < target_count:
                if items_loaded <= 40 and not login_mode:
                    logger.info(f"  Stop Reason      : Login Wall Detected (non-login mode)")
                else:
                    logger.info(f"  Stop Reason      : No More Items (Scroll Stuck)")
            else:
                logger.info(f"  Stop Reason      : Target Reached")

            logger.info("=" * 80)

            # 완료 알림음 재생 (PLAN.md 4.1)
            play_sound()

            return df

        except Exception as e:
            logger.error(f"Error in crawl: {e}", exc_info=True)
            # 에러 발생 시에도 알림음 재생
            play_sound()
            raise
        finally:
            if not keep_open:
                if self.driver:
                    try:
                        self.driver.quit()
                    except:
                        pass
                    self.driver = None

    def close(self):
        """WebDriver 세션 종료"""
        if self.driver:
            try:
                self.driver.quit()
                logger.info("Chrome WebDriver closed successfully.")
                try:
                    play_sound()
                    show_notification(
                        "유튜브 대시보드 크롤러 완료",
                        "모든 크롤링 수집 프로세스가 완료되어 브라우저가 안전하게 종료되었습니다."
                    )
                except Exception as notify_err:
                    logger.debug(f"Failed to send completion notification: {notify_err}")
            except Exception as e:
                logger.warning(f"Failed to close Chrome WebDriver: {e}")
            finally:
                self.driver = None

    def _parse_videos(self, soup, target_count, video_type='shorts', country='한국', period='일간', ranking_date=None, start_rank=0, category='전체', ranking_criteria='조회수 순위'):
        """
        쇼츠/영상 데이터 파싱 (광고 섹션 클릭 방지 및 오류 수정 버전)
        """
        if ranking_date is None:
            ranking_date = datetime.now().strftime('%Y-%m-%d')
        data = []

        # [수정 1] 광고(.chart__row--ad)를 CSS Selector 단계에서 원천적으로 제외
        # 이렇게 하면 광고 행은 아예 rows 리스트에 들어오지 않습니다.
        rows = soup.select('table.sheet tbody tr.chart__row:not(.chart__row--ad)')
        
        if not rows:
            # Fallback: 기존 방식에서도 광고 클래스 제외 시도
            rows = [r for r in soup.find_all('tr', class_='rank-item') if 'chart__row--ad' not in r.get('class', [])]
        if not rows:
            rows = soup.select('tbody tr')

        logger.info(f"Found {len(rows)} valid rows (ads excluded) for parsing")

        collected_count = 0
        
        # [수정 2] target_count만큼만 반복 (광고가 이미 제외되었으므로 순수 데이터만 처리)
        for idx, row in enumerate(rows[start_rank:target_count], start_rank + 1):
            try:
                # [수정 3] 삭제된 로직: self.driver.execute_script(...)
                # 이유: 'row' 변수는 BeautifulSoup의 Tag 객체입니다. 
                # Selenium 드라이버는 이를 인식할 수 없어 'JSON serializable' 오류를 뿜고, 
                # 이로 인해 크롤링이 불안정해져 엉뚱한 곳(광고)을 클릭하게 됩니다.
                # 파싱 단계에서는 브라우저 조작(스크롤)을 하지 않는 것이 원칙입니다.

                # 진행률 로깅
                if idx % 10 == 0:
                    logger.info(f"Progress: Parsing {idx}/{len(rows)} items...")
                else:
                    logger.debug(f"Parsing row {idx}")

                # 1. 순위 (td.rank .current)
                rank = 'N/A'
                rank_current = row.select_one('td.rank .current')
                if rank_current:
                    rank = rank_current.text.strip()
                else:
                    rank_elem = row.find('td', class_='rank')
                    rank = rank_elem.text.strip() if rank_elem else str(idx)

                # 2. 순위 변화 (td.rank .fluc)
                rank_change = '0'
                fluc_elem = row.select_one('td.rank .fluc')
                if fluc_elem:
                    fluc_classes = fluc_elem.get('class', [])
                    fluc_text = fluc_elem.text.strip()

                    if 'new' in fluc_classes:
                        rank_change = 'NEW'
                    elif 'up' in fluc_classes:
                        rank_change = f'+{fluc_text}' if fluc_text and fluc_text.isdigit() else '+1'
                    elif 'down' in fluc_classes:
                        rank_change = f'-{fluc_text}' if fluc_text and fluc_text.isdigit() else '-1'
                    else:
                        rank_change = fluc_text if fluc_text else '0'

                # [버그 수정 및 기능 개선] Video ID 정밀 추출
                video_id = 'N/A'
                video_link = row.find('a', href=lambda x: x and ('watch?v=' in x or '/video/' in x or '/shorts/' in x or '/videos/' in x))
                if video_link:
                    href = video_link.get('href', '')
                    from modules.utils import extract_video_id_from_url
                    video_id = extract_video_id_from_url(href)
                    if not video_id:
                        match = re.search(r'([a-zA-Z0-9_-]{11})', href)
                        if match:
                            video_id = match.group(1)
                    if not video_id:
                        video_id = 'N/A'

                # 3. 제목 (td.title .title__label h3) - 해시태그 제외
                video_title = 'N/A'
                title_elem = row.select_one('td.title .title__label h3')
                if title_elem:
                    video_title = title_elem.get_text(strip=True)
                    video_title = re.sub(r'#\S+', '', video_title).strip()
                elif video_link:
                    video_title = video_link.text.strip()
                    video_title = re.sub(r'#\S+', '', video_title).strip()

                # 4. 썸네일 (Lazy Loading 데이터 우선 확보)
                thumbnail = 'N/A'

                # 4-1. div.thumb의 background-image에서 추출 (Playboard 방식)
                thumb_div = row.select_one('div.thumb')
                if thumb_div:
                    # data-background-image 속성 확인
                    bg_url = thumb_div.get('data-background-image')
                    if not bg_url:
                        # style 속성에서 background-image 추출
                        style = thumb_div.get('style', '')
                        bg_match = re.search(r'background-image:\s*url\(["\']?([^"\'()]+)["\']?\)', style)
                        if bg_match:
                            bg_url = bg_match.group(1)

                    if bg_url and bg_url != 'N/A':
                        # URL 정규화 (//로 시작하면 https: 추가)
                        if bg_url.startswith('//'):
                            bg_url = 'https:' + bg_url
                        thumbnail = bg_url
                        logger.debug(f"[Rank #{rank}] Thumbnail from div.thumb: {thumbnail[:50]}...")

                # 4-2. img 태그에서 추출 (fallback)
                if thumbnail == 'N/A':
                    img_elem = row.find('img')
                    if img_elem:
                        # data-src나 data-original에 실제 고화질 주소가 숨어있음
                        thumbnail = img_elem.get('data-src') or img_elem.get('data-original') or img_elem.get('src', 'N/A')

                        if thumbnail and 'data:image' in thumbnail:
                            logger.debug(f"[Rank #{rank}] Base64 dummy thumbnail detected, setting to N/A")
                            thumbnail = 'N/A'
                        elif thumbnail and thumbnail.startswith('//'):
                            thumbnail = 'https:' + thumbnail

                # 4-3. video_id로 YouTube 썸네일 URL 생성 (최종 fallback)
                if thumbnail == 'N/A' and video_id and video_id != 'N/A':
                    thumbnail = f'https://img.youtube.com/vi/{video_id}/mqdefault.jpg'
                    logger.debug(f"[Rank #{rank}] Thumbnail generated from video_id: {thumbnail}")

                # 5. 태그 수집
                tags = []
                tag_elems = row.select('td.title ul.ttags li a')
                for tag_elem in tag_elems:
                    tag_text = tag_elem.text.strip()
                    if tag_text:
                        tags.append(tag_text)
                tags_str = ','.join(tags) if tags else ''

                # 6. 조회수
                views = 0
                score_elem = row.select_one('td.score .fluc-label')
                if score_elem:
                    views_text = score_elem.text.strip()
                    views = parse_korean_number_string(views_text)
                else:
                    views_elem = row.find('td', class_='views')
                    if views_elem:
                        views_text = views_elem.text.strip()
                        views = parse_korean_number_string(views_text)

                # 7. 업로드 날짜
                upload_date = 'N/A'
                date_elem = row.select_one('td.title .title__date')
                if date_elem:
                    upload_date = date_elem.text.strip()
                else:
                    date_pattern = r'(\d{4}[.-]\d{2}[.-]\d{2})'
                    date_match = re.search(date_pattern, row.text)
                    if date_match:
                        upload_date = date_match.group(1)

                # 8. 채널명
                channel_name = 'N/A'
                channel_elem = row.select_one('td.channel .name')
                if channel_elem:
                    channel_name = channel_elem.text.strip()
                else:
                    channel_elem = row.select_one('td.title .title__channel')
                    if channel_elem:
                        channel_name = channel_elem.text.strip()
                    else:
                        channel_elem = row.find('a', href=lambda x: x and '/en/channel/' in x)
                        if channel_elem:
                            channel_name = channel_elem.text.strip()

                # 9. 구독자 수
                subscriber_count = ''
                subs_elem = row.select_one('td.channel span.subs__count')
                if subs_elem:
                    subscriber_count = subs_elem.text.strip()

                # 상위 4개 샘플 로깅
                if idx <= 4:
                    logger.debug(f"[Sample #{idx}] Rank: {rank} | Title: {video_title[:30]}... | Channel: {channel_name}")

                # 수집 기준(Criteria)에 따른 동적 메트릭 컬럼 이름 결정
                metric_col = 'Views'
                if ranking_criteria == '좋아요 순위':
                    metric_col = 'Likes'
                elif ranking_criteria == '댓글 순위':
                    metric_col = 'Comments'

                # 데이터 적재
                item_dict = {
                    'Rank': rank,
                    'Rank Change': rank_change,
                    'Video Title': video_title,
                    'Video ID': video_id,
                    'Thumbnail': thumbnail,
                    'Channel Name': channel_name,
                    'Subscribers': subscriber_count,
                    'Upload Date': upload_date,
                    'Tags': tags_str,
                    'Country': country,
                    'Category': category,
                    'Criteria': ranking_criteria,
                    'Period': period,
                    'Ranking Date': ranking_date,
                    'Type': video_type
                }
                item_dict[metric_col] = views
                data.append(item_dict)

                collected_count += 1
                if collected_count % 10 == 0:
                    logger.info(f"Progress: {collected_count}/{target_count} items collected")

            except Exception as e:
                self.log_parsing_failure(row, idx, e)
                continue

        # 요약 정보 출력
        if data:
            logger.info("=" * 50)
            logger.info(f"=== {video_type.upper()} Crawling Summary (First 4 Items) ===")
            for item in data[:4]:
                logger.info(f"#{item['Rank']} ({item['Rank Change']}) | {item['Video Title'][:30]}... | {item['Views']:,} views")
            logger.info("=" * 50)

        return data

    def _parse_channels(self, soup, target_count, country='한국', period='일간', ranking_date=None, start_rank=0, category='전체', ranking_criteria='조회수 순위'):
        """
        채널 데이터 파싱 (PLAN.md 3.2.B - nth-child 기반 정밀 파싱)

        HTML 구조 기반 (Popular 차트):
        - Row: table.sheet--popular tbody tr.chart__row
        - Channel Name: td.name .name__label h3
        - Views (조회수): tr 내부의 4번째 td (td.score:nth-child(4))
        - Likes (좋아요): tr 내부의 5번째 td (td.score:nth-child(5))
        - 단위 변환: "23.8만" -> 238000
        """
        if ranking_date is None:
            ranking_date = datetime.now().strftime('%Y-%m-%d')
        data = []

        # 정확한 CSS Selector로 Row 추출
        rows = soup.select('table.sheet--popular tbody tr.chart__row')
        if not rows:
            # Fallback: 기존 방식
            rows = soup.find_all('tr', class_='rank-item')
        if not rows:
            rows = soup.select('tbody tr')

        logger.info(f"Found {len(rows)} channel rows for parsing")

        collected_count = 0
        for idx, row in enumerate(rows[start_rank:target_count], start_rank + 1):
            try:
                # 광고 필터링
                if 'chart__row--ad' in row.get('class', []):
                    logger.debug(f"Skipping ad row #{idx}")
                    continue

                # 1. 순위 (td.rank .current)
                rank = 'N/A'
                rank_current = row.select_one('td.rank .current')
                if rank_current:
                    rank = rank_current.text.strip()
                else:
                    rank_elem = row.find('td', class_='rank')
                    rank = rank_elem.text.strip() if rank_elem else str(idx)

                # 2. 순위 변화
                rank_change = '0'
                fluc_elem = row.select_one('td.rank .fluc')
                if fluc_elem:
                    fluc_classes = fluc_elem.get('class', [])
                    fluc_text = fluc_elem.text.strip()

                    if 'new' in fluc_classes:
                        rank_change = 'NEW'
                    elif 'up' in fluc_classes:
                        rank_change = f'+{fluc_text}' if fluc_text and fluc_text.isdigit() else '+1'
                    elif 'down' in fluc_classes:
                        rank_change = f'-{fluc_text}' if fluc_text and fluc_text.isdigit() else '-1'
                    else:
                        rank_change = fluc_text if fluc_text else '0'

                # 3. 채널명 (td.name .name__label h3)
                channel_name = 'N/A'
                name_elem = row.select_one('td.name .name__label h3')
                if name_elem:
                    channel_name = name_elem.text.strip()
                else:
                    # Fallback
                    channel_link = row.find('a', href=lambda x: x and '/en/channel/' in x)
                    if channel_link:
                        channel_name = channel_link.text.strip()

                # 4. Channel ID 및 Channel URL 추출 (Zero-Cost ID Extraction용)
                channel_id = 'N/A'
                channel_url = ''
                channel_link = row.find('a', href=lambda x: x and '/en/channel/' in x)
                if channel_link and 'href' in channel_link.attrs:
                    href = channel_link['href']
                    channel_id = self._extract_channel_id(href)
                    # 채널 URL 저장 (YouTube 링크로 변환 가능)
                    if href.startswith('/'):
                        channel_url = f"https://playboard.co{href}"
                    else:
                        channel_url = href

                # 5. 프로필 이미지
                profile_image = 'N/A'
                img_elem = row.find('img')
                if img_elem and 'src' in img_elem.attrs:
                    profile_image = img_elem['src']

                # 6. 태그 수집
                tags = []
                tag_elems = row.select('td.name ul.ttags li a')
                for tag_elem in tag_elems:
                    tag_text = tag_elem.text.strip()
                    if tag_text:
                        tags.append(tag_text)
                tags_str = ','.join(tags) if tags else ''

                # 7. 조회수 (4번째 td - nth-child(4))
                total_views = 0
                views_elem = row.select_one('td.score:nth-child(4)')
                if views_elem:
                    views_text = views_elem.text.strip()
                    total_views = parse_korean_number_string(views_text)
                else:
                    # Fallback: class로 찾기
                    all_tds = row.find_all('td')
                    if len(all_tds) > 3:
                        views_text = all_tds[3].text.strip()
                        total_views = parse_korean_number_string(views_text)

                # 8. Score 2 (5번째 td - nth-child(5))
                score_2 = 0
                score2_elem = row.select_one('td.score:nth-child(5)')
                if score2_elem:
                    score2_text = score2_elem.text.strip()
                    score_2 = parse_korean_number_string(score2_text)
                else:
                    # Fallback
                    all_tds = row.find_all('td')
                    if len(all_tds) > 4:
                        score2_text = all_tds[4].text.strip()
                        score_2 = parse_korean_number_string(score2_text)

                # 9. Video Count (PLAN.md 3.2.B)
                video_count = 0
                video_count_elem = row.select_one('td.videos')
                if video_count_elem:
                    count_text = video_count_elem.text.strip()
                    video_count = parse_korean_number_string(count_text)

                # 상위 4개 데이터 샘플 로깅
                if idx <= 4:
                    logger.debug(f"[Channel Sample #{idx}] Rank: {rank} | Name: {channel_name} | Score1: {total_views} | Score2: {score_2}")

                data.append({
                    'Rank': rank,
                    'Rank Change': rank_change,
                    'Channel Name': channel_name,
                    'Channel ID': channel_id,
                    'Channel URL': channel_url,  # Zero-Cost ID Extraction용
                    'Profile Image': profile_image,
                    'Score 1': total_views,
                    'Score 2': score_2,
                    'Video Count': video_count,
                    'Tags': tags_str,
                    'Country': country,  # PLAN.md 3.5 - 메타 데이터 추가
                    'Category': category,
                    'Criteria': ranking_criteria,
                    'Period': period,  # 일간/주간/월간 구분
                    'Ranking Date': ranking_date,  # 랭킹 기준 날짜
                    'Type': 'channel'
                })

                collected_count += 1

                # 진행률 로그 (PLAN.md 6.1 - 10개마다)
                if collected_count % 10 == 0:
                    logger.info(f"Progress: {collected_count}/{target_count} items collected")

            except Exception as e:
                # HTML 스냅샷 저장 및 로깅
                self.log_parsing_failure(row, idx, e)
                continue

        # 크롤링 완료 후 상위 4개 요약
        if data:
            logger.info("=" * 50)
            logger.info("=== CHANNEL Crawling Summary (First 4 Items) ===")
            for item in data[:4]:
                logger.info(f"#{item['Rank']} ({item['Rank Change']}) | {item['Channel Name']} | Score1: {item['Score 1']:,} | Score2: {item['Score 2']:,}")
            logger.info("=" * 50)

        return data

    # ========== 채널 랭킹 전용 메서드 ==========

    @staticmethod
    def build_channel_ranking_url(ranking_type='popular', category='all', country='kr', period='daily'):
        """
        채널 랭킹 URL 생성

        Args:
            ranking_type: 'popular' (인기순), 'growth' (구독자 급상승), 'viewed' (조회수)
            category: 카테고리 slug (all, animals, music, gaming 등)
            country: 국가 코드 (kr, us, jp, global)
            period: 기간 (daily, weekly, monthly)

        Returns:
            str: 완성된 URL

        Examples:
            - 인기순위 전체: most-popular-all-channels-in-south-korea-daily
            - 구독자 급상승 동물: most-growth-animals-channels-in-south-korea-weekly
        """
        # Config에서 매핑 가져오기
        type_slug = Config.CHANNEL_RANKING_TYPES.get(ranking_type, 'most-popular')
        country_slug = Config.CHANNEL_COUNTRIES.get(country, 'south-korea')
        period_slug = Config.CHANNEL_PERIODS.get(period, 'daily')

        # 구독자 급상승은 일간 불가 - 주간으로 자동 변경
        if ranking_type == 'growth' and period == 'daily':
            period_slug = 'weekly'
            logger.warning(f"구독자 급상승 순위는 일간을 지원하지 않습니다. 주간으로 변경됩니다.")

        # URL 생성
        url = f"https://playboard.co/youtube-ranking/{type_slug}-{category}-channels-in-{country_slug}-{period_slug}"

        logger.info(f"Built channel ranking URL: {url}")
        return url

    def crawl_channel_ranking(self, ranking_type='popular', category='all', country='kr',
                               period='weekly', login_mode=False, target_count=100,
                               ranking_date=None):
        """
        채널 랭킹 크롤링 메인 메서드

        Args:
            ranking_type: 'popular' (인기순), 'growth' (구독자 급상승), 'viewed' (조회수)
            category: 카테고리 slug
            country: 국가 코드
            period: 기간 (daily, weekly, monthly) - growth는 weekly/monthly만 지원
            login_mode: 로그인 모드 여부
            target_count: 목표 수집 개수
            ranking_date: 랭킹 기준 날짜 (YYYY-MM-DD)

        Returns:
            pd.DataFrame: 채널 랭킹 데이터
        """
        if ranking_date is None:
            ranking_date = datetime.now().strftime('%Y-%m-%d')

        # 구독자 급상승은 일간 불가
        if ranking_type == 'growth' and period == 'daily':
            period = 'weekly'
            logger.warning("구독자 급상승 순위는 일간을 지원하지 않습니다. 주간으로 변경됩니다.")

        url = self.build_channel_ranking_url(ranking_type, category, country, period)

        # 기간 한글명
        period_ko = {'daily': '일간', 'weekly': '주간', 'monthly': '월간'}.get(period, period)
        country_ko = {'kr': '한국', 'us': '미국', 'jp': '일본', 'global': '전세계'}.get(country, country)

        logger.info(f"=== 채널 랭킹 크롤링 시작 ===")
        logger.info(f"  기준: {ranking_type} | 카테고리: {category} | 국가: {country_ko} | 기간: {period_ko}")
        logger.info(f"  URL: {url}")

        try:
            self._init_driver()
            self.driver.get(url)

            # 로그인 모드 처리
            if login_mode:
                if not self._wait_for_login():
                    logger.warning("로그인 대기 시간 초과. 비로그인 모드로 진행합니다.")

            # 스크롤하여 데이터 로드
            self._scroll_to_load_items(target_count)

            # 페이지 소스 파싱
            soup = BeautifulSoup(self.driver.page_source, 'html.parser')

            # ranking_type에 따라 다른 파서 사용
            if ranking_type == 'growth':
                data = self._parse_channel_growth(soup, target_count, country_ko, period_ko, ranking_date, category)
            else:  # popular, viewed
                data = self._parse_channel_popular(soup, target_count, country_ko, period_ko, ranking_date, category)

            df = pd.DataFrame(data)
            logger.info(f"채널 랭킹 크롤링 완료: {len(df)}개 항목")

            play_sound()
            return df

        except Exception as e:
            logger.error(f"채널 랭킹 크롤링 오류: {e}", exc_info=True)
            play_sound()
            raise
        finally:
            if self.driver:
                self.driver.quit()

    def _parse_channel_growth(self, soup, target_count, country, period, ranking_date, category):
        """
        구독자 급상승 순위 파싱

        테이블 구조:
        - 순위, 채널명, 전체 구독자, 신규 구독자(증가폭%), 영상수

        Returns:
            list: 채널 데이터 딕셔너리 리스트
        """
        data = []

        rows = soup.select('table.sheet tbody tr')
        if not rows:
            rows = soup.select('tbody tr')

        logger.info(f"[구독자 급상승] Found {len(rows)} rows for parsing")

        collected_count = 0
        for idx, row in enumerate(rows[:target_count + 10], 1):  # 광고 여유분
            try:
                # 광고 필터링
                row_classes = row.get('class', [])
                if any('ad' in c.lower() for c in row_classes):
                    continue

                # 1. 순위
                rank = 'N/A'
                rank_elem = row.select_one('td.rank .current')
                if rank_elem:
                    rank = rank_elem.text.strip()
                else:
                    rank_td = row.select_one('td.rank')
                    if rank_td:
                        rank = rank_td.text.strip().split()[0] if rank_td.text.strip() else str(idx)

                # 2. 순위 변화
                rank_change = '0'
                fluc_elem = row.select_one('td.rank .fluc')
                if fluc_elem:
                    fluc_classes = fluc_elem.get('class', [])
                    fluc_text = fluc_elem.text.strip()
                    if 'new' in fluc_classes:
                        rank_change = 'NEW'
                    elif 'up' in fluc_classes:
                        rank_change = f'+{fluc_text}' if fluc_text.isdigit() else '+1'
                    elif 'down' in fluc_classes:
                        rank_change = f'-{fluc_text}' if fluc_text.isdigit() else '-1'

                # 3. 채널명
                channel_name = 'N/A'
                name_elem = row.select_one('td.name h3')
                if name_elem:
                    channel_name = name_elem.text.strip()
                else:
                    name_elem = row.select_one('td.name .name__label')
                    if name_elem:
                        channel_name = name_elem.text.strip()

                if channel_name == 'N/A':
                    continue  # 유효하지 않은 행 스킵

                # 4. 프로필 이미지
                profile_image = 'N/A'
                img_elem = row.select_one('td.logo img')
                if img_elem and img_elem.get('src'):
                    profile_image = img_elem['src']

                # 5. 태그 수집
                tags = []
                tag_elems = row.select('td.name ul.ttags li a')
                for tag_elem in tag_elems:
                    tag_text = tag_elem.text.strip()
                    if tag_text:
                        tags.append(tag_text)
                tags_str = ','.join(tags) if tags else ''

                # 6. 전체 구독자 수 (4번째 td)
                total_subscribers = 0
                all_tds = row.select('td.score')
                if len(all_tds) >= 1:
                    subs_text = all_tds[0].text.strip()
                    total_subscribers = parse_korean_number_string(subs_text)

                # 7. 신규 구독자 수 및 증가폭 (5번째 td)
                new_subscribers = 0
                growth_rate = ''
                if len(all_tds) >= 2:
                    growth_td = all_tds[1]
                    growth_text = growth_td.text.strip()

                    # "18,801명 (26.3%)" 형식 파싱
                    # 숫자 부분 추출
                    num_match = re.search(r'([\d,]+)(?:명|만)?', growth_text)
                    if num_match:
                        new_subscribers = parse_korean_number_string(num_match.group(0))

                    # 증가폭(%) 추출
                    rate_match = re.search(r'\(([\d.]+%)\)', growth_text)
                    if rate_match:
                        growth_rate = rate_match.group(1)

                # 8. 영상 수
                video_count = 0
                video_elem = row.select_one('td.videos')
                if video_elem:
                    video_count = parse_korean_number_string(video_elem.text.strip())

                # 상위 4개 샘플 로깅
                if collected_count < 4:
                    logger.debug(f"[Growth Sample #{collected_count+1}] #{rank} | {channel_name} | 전체: {total_subscribers:,} | 신규: {new_subscribers:,} ({growth_rate})")

                data.append({
                    'Rank': rank,
                    'Rank Change': rank_change,
                    'Channel Name': channel_name,
                    'Profile Image': profile_image,
                    'Total Subscribers': total_subscribers,
                    'New Subscribers': new_subscribers,
                    'Growth Rate': growth_rate,
                    'Video Count': video_count,
                    'Tags': tags_str,
                    'Country': country,
                    'Period': period,
                    'Ranking Date': ranking_date,
                    'Category': category,
                    'Ranking Type': '구독자 급상승',
                    'Type': 'channel_ranking'
                })

                collected_count += 1
                if collected_count >= target_count:
                    break

                if collected_count % 10 == 0:
                    logger.info(f"Progress: {collected_count}/{target_count} items collected")

            except Exception as e:
                self.log_parsing_failure(row, idx, e)
                continue

        # 요약 로깅
        if data:
            logger.info("=" * 50)
            logger.info("=== 구독자 급상승 순위 요약 (상위 4개) ===")
            for item in data[:4]:
                logger.info(f"#{item['Rank']} | {item['Channel Name']} | 전체: {item['Total Subscribers']:,} | 신규: {item['New Subscribers']:,} ({item['Growth Rate']})")
            logger.info("=" * 50)

        return data

    def _parse_channel_popular(self, soup, target_count, country, period, ranking_date, category):
        """
        인기 순위 / 조회수 순위 파싱

        테이블 구조:
        - 순위, 채널명, 조회수, 좋아요, 영상수

        Returns:
            list: 채널 데이터 딕셔너리 리스트
        """
        data = []

        rows = soup.select('table.sheet tbody tr')
        if not rows:
            rows = soup.select('tbody tr')

        logger.info(f"[인기 순위] Found {len(rows)} rows for parsing")

        collected_count = 0
        for idx, row in enumerate(rows[:target_count + 10], 1):
            try:
                # 광고 필터링
                row_classes = row.get('class', [])
                if any('ad' in c.lower() for c in row_classes):
                    continue

                # 1. 순위
                rank = 'N/A'
                rank_elem = row.select_one('td.rank .current')
                if rank_elem:
                    rank = rank_elem.text.strip()
                else:
                    rank_td = row.select_one('td.rank')
                    if rank_td:
                        rank = rank_td.text.strip().split()[0] if rank_td.text.strip() else str(idx)

                # 2. 순위 변화
                rank_change = '0'
                fluc_elem = row.select_one('td.rank .fluc')
                if fluc_elem:
                    fluc_classes = fluc_elem.get('class', [])
                    fluc_text = fluc_elem.text.strip()
                    if 'new' in fluc_classes:
                        rank_change = 'NEW'
                    elif 'up' in fluc_classes:
                        rank_change = f'+{fluc_text}' if fluc_text.isdigit() else '+1'
                    elif 'down' in fluc_classes:
                        rank_change = f'-{fluc_text}' if fluc_text.isdigit() else '-1'

                # 3. 채널명
                channel_name = 'N/A'
                name_elem = row.select_one('td.name h3')
                if name_elem:
                    channel_name = name_elem.text.strip()
                else:
                    name_elem = row.select_one('td.name .name__label')
                    if name_elem:
                        channel_name = name_elem.text.strip()

                if channel_name == 'N/A':
                    continue

                # 4. 프로필 이미지
                profile_image = 'N/A'
                img_elem = row.select_one('td.logo img')
                if img_elem and img_elem.get('src'):
                    profile_image = img_elem['src']

                # 5. 태그 수집
                tags = []
                tag_elems = row.select('td.name ul.ttags li a')
                for tag_elem in tag_elems:
                    tag_text = tag_elem.text.strip()
                    if tag_text:
                        tags.append(tag_text)
                tags_str = ','.join(tags) if tags else ''

                # 6. 조회수 (4번째 td)
                views = 0
                all_tds = row.select('td.score')
                if len(all_tds) >= 1:
                    views_text = all_tds[0].text.strip()
                    views = parse_korean_number_string(views_text)

                # 7. 좋아요 (5번째 td)
                likes = 0
                if len(all_tds) >= 2:
                    likes_text = all_tds[1].text.strip()
                    likes = parse_korean_number_string(likes_text)

                # 8. 영상 수
                video_count = 0
                video_elem = row.select_one('td.videos')
                if video_elem:
                    video_count = parse_korean_number_string(video_elem.text.strip())

                # 상위 4개 샘플 로깅
                if collected_count < 4:
                    logger.debug(f"[Popular Sample #{collected_count+1}] #{rank} | {channel_name} | 조회수: {views:,} | 좋아요: {likes:,}")

                data.append({
                    'Rank': rank,
                    'Rank Change': rank_change,
                    'Channel Name': channel_name,
                    'Profile Image': profile_image,
                    'Views': views,
                    'Likes': likes,
                    'Video Count': video_count,
                    'Tags': tags_str,
                    'Country': country,
                    'Period': period,
                    'Ranking Date': ranking_date,
                    'Category': category,
                    'Ranking Type': '인기순위',
                    'Type': 'channel_ranking'
                })

                collected_count += 1
                if collected_count >= target_count:
                    break

                if collected_count % 10 == 0:
                    logger.info(f"Progress: {collected_count}/{target_count} items collected")

            except Exception as e:
                self.log_parsing_failure(row, idx, e)
                continue

        # 요약 로깅
        if data:
            logger.info("=" * 50)
            logger.info("=== 인기 순위 요약 (상위 4개) ===")
            for item in data[:4]:
                logger.info(f"#{item['Rank']} | {item['Channel Name']} | 조회수: {item['Views']:,} | 좋아요: {item['Likes']:,}")
            logger.info("=" * 50)

        return data
