import os
from datetime import datetime
from flask import Flask, render_template, request, jsonify, send_file, session
from werkzeug.utils import secure_filename
import pandas as pd
from modules.crawler_selenium import PlayboardCrawler
from modules.youtube_handler import YouTubeTranscriptExtractor
from modules.database import DatabaseHandler
from modules.utils import sanitize_filename, generate_safe_filepath
from config import Config
from config_mappings import (
    build_url,
    get_country_list,
    get_category_list,
    get_period_list,
    CATEGORIES
)
from logger_config import setup_logger, log_exception, log_function_call

# 상세 로깅 설정
logger = setup_logger('app')

app = Flask(__name__)
app.secret_key = 'your-secret-key-change-this-in-production'

# 디렉토리 생성
if not os.path.exists(Config.OUTPUT_DIR):
    os.makedirs(Config.OUTPUT_DIR)

if not os.path.exists(Config.TRANSCRIPTS_DIR):
    os.makedirs(Config.TRANSCRIPTS_DIR)

# DB 핸들러 초기화
db_handler = DatabaseHandler()
logger.info("Database handler initialized")

status_logs = []
latest_crawl_data = []  # 최근 크롤링 결과 저장


def add_log(message):
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    log_entry = f"[{timestamp}] {message}"
    status_logs.append(log_entry)
    logger.info(message)
    if len(status_logs) > 100:
        status_logs.pop(0)


@app.route('/')
def index():
    """메인 대시보드 (원페이지 구성)"""
    countries = get_country_list()
    categories = get_category_list()
    periods = get_period_list()

    # 최근 크롤링 데이터 전달
    global latest_crawl_data
    crawl_data = latest_crawl_data if latest_crawl_data else []

    return render_template('index.html',
                         countries=countries,
                         categories=categories,
                         periods=periods,
                         crawl_data=crawl_data)


@app.route('/dashboard')
def dashboard():
    """통계 및 시각화 대시보드 (Chart.js)"""
    return render_template('dashboard.html')


@app.route('/api/build_url', methods=['POST'])
def api_build_url():
    """동적 URL 생성 API"""
    try:
        data = request.json
        target_type = data.get('target_type')
        category = data.get('category')
        country = data.get('country')
        period = data.get('period')
        timestamp = data.get('timestamp')  # Unix timestamp (optional)

        url = build_url(target_type, category, country, period, timestamp)

        return jsonify({
            'status': 'success',
            'url': url
        })

    except Exception as e:
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 400


@app.route('/crawl', methods=['POST'])
def crawl():
    """크롤링 실행"""
    try:
        data = request.json
        target_type = data.get('target_type', 'shorts')
        category = data.get('category', '전체')
        country = data.get('country', '한국')
        period = data.get('period', '일간')
        login_mode = data.get('login_mode', False)
        specific_date = data.get('specific_date')  # YYYY-MM-DD format

        status_logs.clear()
        add_log(f"크롤링 시작: {target_type} / {category} / {country} / {period}")

        # Unix timestamp 변환 (특정 날짜 선택 시)
        timestamp = None
        if specific_date:
            try:
                dt = datetime.strptime(specific_date, '%Y-%m-%d')
                timestamp = int(dt.timestamp())
                add_log(f"특정 날짜 선택: {specific_date} (Timestamp: {timestamp})")
            except ValueError:
                add_log("날짜 형식 오류. 기본 기간 사용")

        # URL 생성
        url = build_url(target_type, category, country, period, timestamp)
        add_log(f"생성된 URL: {url}")

        # 크롤링 실행
        target_count = Config.MAX_ITEMS_WITH_LOGIN if login_mode else Config.MAX_ITEMS_NO_LOGIN
        crawler = PlayboardCrawler(headless=Config.CHROME_HEADLESS)

        # 랭킹 기준 날짜 결정 (특정 날짜 또는 오늘)
        ranking_date = specific_date if specific_date else datetime.now().strftime('%Y-%m-%d')

        df = crawler.crawl(
            url=url,
            target_type=target_type,
            login_mode=login_mode,
            target_count=target_count,
            country=country,  # PLAN.md 3.5 - 메타 데이터 전달
            period=period,  # 일간/주간/월간 구분
            ranking_date=ranking_date  # 랭킹 기준 날짜
        )

        # 안전한 파일 경로 생성 (파일명 정제 적용)
        filepath, filename = generate_safe_filepath(
            base_dir=Config.OUTPUT_DIR,
            target_type=target_type,
            category=category,
            country=country,
            period=period,
            extension='csv'
        )

        # CSV 파일 저장 (컬럼 순서: Period, Ranking Date, Type, Country, Rank, ...)
        csv_columns = ['Period', 'Ranking Date', 'Type', 'Country', 'Rank', 'Rank Change',
                       'Video Title', 'Views', 'Upload Date', 'Tags',
                       'Channel Name', 'Subscribers', 'Thumbnail']
        csv_df = df[[col for col in csv_columns if col in df.columns]]
        csv_df.to_csv(filepath, index=False, encoding='utf-8-sig')
        add_log(f"CSV 저장 완료: {filename}")

        # DB 저장 (이중 저장)
        try:
            db_count = db_handler.insert_dataframe(df, category, country, period, target_type)
            db_handler.log_crawl_history(target_type, category, country, period, len(df), success=True)
            add_log(f"DB 저장 완료: {db_count}개 레코드")
        except Exception as db_error:
            logger.error(f"DB 저장 실패: {db_error}", exc_info=True)
            add_log(f"경고: DB 저장 실패 (CSV는 저장됨)")

        # 세션 및 전역 변수에 저장
        session['last_crawl_file'] = filepath
        global latest_crawl_data
        latest_crawl_data = df.to_dict('records')

        # 데이터 품질 체크 (N/A 개수 카운트)
        na_count = sum(1 for row in latest_crawl_data if row.get('Views') == 'N/A' or row.get('Video ID') == 'N/A')
        success_count = len(df) - na_count

        add_log(f"크롤링 완료. 총 {len(df)}개 항목 (성공: {success_count}, 불완전: {na_count}) 저장: {filename}")

        return jsonify({
            'status': 'success',
            'message': f'{len(df)}개 항목 수집 완료',
            'filename': filename,
            'data': latest_crawl_data,
            'summary': {
                'total': len(df),
                'success': success_count,
                'incomplete': na_count,
                'success_rate': f"{(success_count / len(df) * 100):.1f}%" if len(df) > 0 else "0%"
            }
        })

    except Exception as e:
        error_msg = f"크롤링 오류: {str(e)}"
        add_log(error_msg)
        logger.error(error_msg, exc_info=True)
        return jsonify({
            'status': 'error',
            'message': error_msg
        }), 500


@app.route('/crawl_channel_ranking', methods=['POST'])
def crawl_channel_ranking():
    """채널 랭킹 크롤링 실행"""
    try:
        data = request.json
        ranking_type = data.get('ranking_type', 'popular')  # popular, growth, viewed
        category = data.get('category', 'all')
        country = data.get('country', 'kr')
        period = data.get('period', 'weekly')
        login_mode = data.get('login_mode', False)
        ranking_date = data.get('ranking_date')  # YYYY-MM-DD format (선택사항)

        # 구독자 급상승은 일간 불가
        if ranking_type == 'growth' and period == 'daily':
            period = 'weekly'
            add_log("구독자 급상승 순위는 일간을 지원하지 않습니다. 주간으로 변경됩니다.")

        # 한글명 매핑
        ranking_type_ko = {'popular': '인기순위', 'growth': '구독자 급상승', 'viewed': '조회수 순위'}
        category_ko = Config.CHANNEL_CATEGORIES_KO.get(category, category)
        country_ko = {'kr': '한국', 'us': '미국', 'jp': '일본', 'global': '전세계'}.get(country, country)
        period_ko = {'daily': '일간', 'weekly': '주간', 'monthly': '월간'}.get(period, period)

        status_logs.clear()
        add_log(f"채널 랭킹 크롤링 시작: {ranking_type_ko.get(ranking_type, ranking_type)} / {category_ko} / {country_ko} / {period_ko}")

        # 크롤링 실행
        target_count = Config.MAX_ITEMS_WITH_LOGIN if login_mode else Config.MAX_ITEMS_NO_LOGIN
        crawler = PlayboardCrawler(headless=Config.CHROME_HEADLESS)

        # 랭킹 기준 날짜 결정
        if not ranking_date:
            ranking_date = datetime.now().strftime('%Y-%m-%d')

        df = crawler.crawl_channel_ranking(
            ranking_type=ranking_type,
            category=category,
            country=country,
            period=period,
            login_mode=login_mode,
            target_count=target_count,
            ranking_date=ranking_date
        )

        # 안전한 파일 경로 생성
        safe_category = f"channel_{ranking_type}_{category}"
        filepath, filename = generate_safe_filepath(
            base_dir=Config.OUTPUT_DIR,
            target_type='channel_ranking',
            category=safe_category,
            country=country_ko,
            period=period_ko,
            extension='csv'
        )

        # CSV 파일 저장
        if ranking_type == 'growth':
            csv_columns = ['Period', 'Ranking Date', 'Ranking Type', 'Category', 'Country',
                           'Rank', 'Rank Change', 'Channel Name', 'Profile Image',
                           'Total Subscribers', 'New Subscribers', 'Growth Rate',
                           'Video Count', 'Tags', 'Type']
        else:  # popular, viewed
            csv_columns = ['Period', 'Ranking Date', 'Ranking Type', 'Category', 'Country',
                           'Rank', 'Rank Change', 'Channel Name', 'Profile Image',
                           'Views', 'Likes', 'Video Count', 'Tags', 'Type']

        csv_df = df[[col for col in csv_columns if col in df.columns]]
        csv_df.to_csv(filepath, index=False, encoding='utf-8-sig')
        add_log(f"CSV 저장 완료: {filename}")

        # DB 저장 (채널 랭킹도 DB에 저장)
        try:
            db_count = db_handler.insert_dataframe(df, category_ko, country_ko, period_ko, 'channel')
            db_handler.log_crawl_history('channel', category_ko, country_ko, period_ko, len(df), success=True)
            add_log(f"DB 저장 완료: {db_count}개 레코드")
        except Exception as db_error:
            logger.error(f"DB 저장 실패: {db_error}", exc_info=True)
            add_log(f"경고: DB 저장 실패 (CSV는 저장됨)")

        # 세션 및 전역 변수에 저장
        global latest_crawl_data
        latest_crawl_data = df.to_dict('records')

        add_log(f"채널 랭킹 크롤링 완료. 총 {len(df)}개 항목 저장: {filename}")

        return jsonify({
            'status': 'success',
            'message': f'채널 랭킹 {len(df)}개 항목 수집 완료',
            'filename': filename,
            'data': latest_crawl_data,
            'summary': {
                'total': len(df),
                'ranking_type': ranking_type_ko.get(ranking_type, ranking_type),
                'category': category_ko,
                'country': country_ko,
                'period': period_ko
            }
        })

    except Exception as e:
        error_msg = f"채널 랭킹 크롤링 오류: {str(e)}"
        add_log(error_msg)
        logger.error(error_msg, exc_info=True)
        log_exception(logger, e, "채널 랭킹 크롤링")
        return jsonify({
            'status': 'error',
            'message': error_msg
        }), 500


@app.route('/crawl_all_categories', methods=['POST'])
def crawl_all_categories():
    """전체 카테고리 크롤링 (쇼츠/영상) - 전체 제외한 개별 카테고리 수집"""
    try:
        data = request.json
        target_type = data.get('target_type', 'shorts')
        country = data.get('country', '한국')
        period = data.get('period', '일간')
        login_mode = data.get('login_mode', False)
        specific_date = data.get('specific_date')

        # '전체'를 제외한 카테고리 목록
        all_categories = [cat for cat in CATEGORIES if cat != '전체']

        status_logs.clear()
        add_log(f"전체 카테고리 크롤링 시작: {target_type} / {len(all_categories)}개 카테고리 / {country} / {period}")

        ranking_date = specific_date if specific_date else datetime.now().strftime('%Y-%m-%d')
        target_count = Config.MAX_ITEMS_WITH_LOGIN if login_mode else Config.MAX_ITEMS_NO_LOGIN

        all_data = []
        success_count = 0
        fail_count = 0

        for category in all_categories:
            try:
                add_log(f"카테고리 수집 중: {category}")

                timestamp = None
                if specific_date:
                    dt = datetime.strptime(specific_date, '%Y-%m-%d')
                    timestamp = int(dt.timestamp())

                url = build_url(target_type, category, country, period, timestamp)
                crawler = PlayboardCrawler(headless=Config.CHROME_HEADLESS)

                df = crawler.crawl(
                    url=url,
                    target_type=target_type,
                    login_mode=login_mode,
                    target_count=target_count,
                    country=country,
                    period=period,
                    ranking_date=ranking_date
                )

                if len(df) > 0:
                    all_data.extend(df.to_dict('records'))
                    success_count += 1
                    add_log(f"✓ '{category}' 완료: {len(df)}개 항목")
                else:
                    fail_count += 1
                    add_log(f"✗ '{category}' 실패: 데이터 없음")

            except Exception as e:
                fail_count += 1
                add_log(f"✗ '{category}' 오류: {str(e)}")
                logger.error(f"Category {category} error: {e}", exc_info=True)

        # 통합 CSV 파일 저장 (파일명에 ALL 포함)
        if all_data:
            combined_df = pd.DataFrame(all_data)

            filepath, filename = generate_safe_filepath(
                base_dir=Config.OUTPUT_DIR,
                target_type=target_type,
                category='ALL',
                country=country,
                period=period,
                extension='csv'
            )

            csv_columns = ['Period', 'Ranking Date', 'Type', 'Country', 'Rank', 'Rank Change',
                           'Video Title', 'Views', 'Upload Date', 'Tags',
                           'Channel Name', 'Subscribers', 'Thumbnail']
            csv_df = combined_df[[col for col in csv_columns if col in combined_df.columns]]
            csv_df.to_csv(filepath, index=False, encoding='utf-8-sig')
            add_log(f"통합 CSV 저장 완료: {filename}")

        add_log(f"전체 카테고리 크롤링 완료: 성공 {success_count}개, 실패 {fail_count}개, 총 {len(all_data)}개 항목")

        global latest_crawl_data
        latest_crawl_data = all_data

        return jsonify({
            'status': 'success',
            'message': f'전체 카테고리 크롤링 완료: {len(all_data)}개 항목 수집',
            'data': all_data,
            'summary': {
                'total': len(all_data),
                'categories': len(all_categories),
                'success_count': success_count,
                'fail_count': fail_count
            }
        })

    except Exception as e:
        error_msg = f"전체 카테고리 크롤링 오류: {str(e)}"
        add_log(error_msg)
        logger.error(error_msg, exc_info=True)
        return jsonify({
            'status': 'error',
            'message': error_msg
        }), 500


@app.route('/crawl_channel_ranking_all', methods=['POST'])
def crawl_channel_ranking_all():
    """전체 카테고리 채널 랭킹 크롤링 - 전체 제외한 개별 카테고리 수집"""
    try:
        data = request.json
        ranking_type = data.get('ranking_type', 'popular')
        country = data.get('country', 'kr')
        period = data.get('period', 'weekly')
        login_mode = data.get('login_mode', False)
        ranking_date = data.get('ranking_date')

        # 구독자 급상승은 일간 불가
        if ranking_type == 'growth' and period == 'daily':
            period = 'weekly'

        # '전체'를 제외한 채널 카테고리 목록
        all_categories = [cat for cat in Config.CHANNEL_CATEGORIES.keys() if cat != 'all']

        ranking_type_ko = {'popular': '인기순위', 'growth': '구독자 급상승', 'viewed': '조회수 순위'}
        country_ko = {'kr': '한국', 'us': '미국', 'jp': '일본', 'global': '전세계'}.get(country, country)
        period_ko = {'daily': '일간', 'weekly': '주간', 'monthly': '월간'}.get(period, period)

        status_logs.clear()
        add_log(f"채널 랭킹 전체 카테고리 크롤링 시작: {ranking_type_ko.get(ranking_type)} / {len(all_categories)}개 카테고리 / {country_ko} / {period_ko}")

        if not ranking_date:
            ranking_date = datetime.now().strftime('%Y-%m-%d')

        target_count = Config.MAX_ITEMS_WITH_LOGIN if login_mode else Config.MAX_ITEMS_NO_LOGIN

        all_data = []
        success_count = 0
        fail_count = 0

        for category in all_categories:
            try:
                category_ko = Config.CHANNEL_CATEGORIES_KO.get(category, category)
                add_log(f"카테고리 수집 중: {category_ko}")

                crawler = PlayboardCrawler(headless=Config.CHROME_HEADLESS)
                df = crawler.crawl_channel_ranking(
                    ranking_type=ranking_type,
                    category=category,
                    country=country,
                    period=period,
                    login_mode=login_mode,
                    target_count=target_count,
                    ranking_date=ranking_date
                )

                if len(df) > 0:
                    all_data.extend(df.to_dict('records'))
                    success_count += 1
                    add_log(f"✓ '{category_ko}' 완료: {len(df)}개 항목")
                else:
                    fail_count += 1
                    add_log(f"✗ '{category_ko}' 실패: 데이터 없음")

            except Exception as e:
                fail_count += 1
                add_log(f"✗ '{category_ko}' 오류: {str(e)}")
                logger.error(f"Channel category {category} error: {e}", exc_info=True)

        # 통합 CSV 파일 저장 (파일명에 ALL 포함)
        if all_data:
            combined_df = pd.DataFrame(all_data)

            safe_category = f"channel_{ranking_type}_ALL"
            filepath, filename = generate_safe_filepath(
                base_dir=Config.OUTPUT_DIR,
                target_type='channel_ranking',
                category=safe_category,
                country=country_ko,
                period=period_ko,
                extension='csv'
            )

            if ranking_type == 'growth':
                csv_columns = ['Period', 'Ranking Date', 'Ranking Type', 'Category', 'Country',
                               'Rank', 'Rank Change', 'Channel Name', 'Profile Image',
                               'Total Subscribers', 'New Subscribers', 'Growth Rate',
                               'Video Count', 'Tags', 'Type']
            else:
                csv_columns = ['Period', 'Ranking Date', 'Ranking Type', 'Category', 'Country',
                               'Rank', 'Rank Change', 'Channel Name', 'Profile Image',
                               'Views', 'Likes', 'Video Count', 'Tags', 'Type']

            csv_df = combined_df[[col for col in csv_columns if col in combined_df.columns]]
            csv_df.to_csv(filepath, index=False, encoding='utf-8-sig')
            add_log(f"통합 CSV 저장 완료: {filename}")

        add_log(f"채널 랭킹 전체 카테고리 크롤링 완료: 성공 {success_count}개, 실패 {fail_count}개, 총 {len(all_data)}개 항목")

        global latest_crawl_data
        latest_crawl_data = all_data

        return jsonify({
            'status': 'success',
            'message': f'채널 랭킹 전체 카테고리 크롤링 완료: {len(all_data)}개 항목 수집',
            'data': all_data,
            'summary': {
                'total': len(all_data),
                'categories': len(all_categories),
                'success_count': success_count,
                'fail_count': fail_count,
                'ranking_type': ranking_type_ko.get(ranking_type, ranking_type)
            }
        })

    except Exception as e:
        error_msg = f"채널 랭킹 전체 카테고리 크롤링 오류: {str(e)}"
        add_log(error_msg)
        logger.error(error_msg, exc_info=True)
        return jsonify({
            'status': 'error',
            'message': error_msg
        }), 500


@app.route('/extract_transcripts', methods=['POST'])
def extract_transcripts():
    """자막 추출"""
    try:
        data = request.json
        video_ids = data.get('video_ids', [])

        if not video_ids:
            csv_file = session.get('last_crawl_file')
            if csv_file and os.path.exists(csv_file):
                df = pd.read_csv(csv_file)
                if 'Video ID' in df.columns:
                    video_ids = df['Video ID'].dropna().tolist()
                    video_ids = [vid for vid in video_ids if vid != 'N/A']

        if not video_ids:
            return jsonify({
                'status': 'error',
                'message': '비디오 ID가 없습니다'
            }), 400

        status_logs.clear()
        add_log(f"{len(video_ids)}개 영상의 자막 추출 시작")

        extractor = YouTubeTranscriptExtractor()
        results = extractor.extract_transcripts_batch(video_ids, save_to_file=True)

        success_count = sum(1 for r in results if r['status'] == 'success')
        add_log(f"자막 추출 완료: {success_count}/{len(video_ids)} 성공")

        return jsonify({
            'status': 'success',
            'message': f'{success_count}/{len(video_ids)}개 자막 추출 완료',
            'results': results
        })

    except Exception as e:
        error_msg = f"자막 추출 오류: {str(e)}"
        add_log(error_msg)
        logger.error(error_msg, exc_info=True)
        return jsonify({
            'status': 'error',
            'message': error_msg
        }), 500


@app.route('/upload_csv', methods=['POST'])
def upload_csv():
    """CSV 파일 업로드"""
    try:
        if 'file' not in request.files:
            return jsonify({'status': 'error', 'message': '파일이 없습니다'}), 400

        file = request.files['file']
        if file.filename == '':
            return jsonify({'status': 'error', 'message': '파일이 선택되지 않았습니다'}), 400

        if file and file.filename.endswith('.csv'):
            filename = secure_filename(file.filename)
            filepath = os.path.join(Config.OUTPUT_DIR, filename)
            file.save(filepath)

            df = pd.read_csv(filepath)
            video_ids = []

            if 'Video ID' in df.columns:
                video_ids = df['Video ID'].dropna().tolist()
                video_ids = [vid for vid in video_ids if vid != 'N/A']

            session['last_crawl_file'] = filepath

            return jsonify({
                'status': 'success',
                'message': f'파일 업로드 완료. {len(video_ids)}개 비디오 ID 발견',
                'video_ids': video_ids
            })

    except Exception as e:
        error_msg = f"파일 업로드 오류: {str(e)}"
        logger.error(error_msg, exc_info=True)
        return jsonify({
            'status': 'error',
            'message': error_msg
        }), 500


@app.route('/status')
def get_status():
    """실시간 상태 로그"""
    return jsonify({
        'logs': status_logs[-20:]
    })


@app.route('/results')
def results():
    """결과 페이지"""
    csv_files = [f for f in os.listdir(Config.OUTPUT_DIR) if f.endswith('.csv')]
    csv_files.sort(reverse=True)

    data = None
    if csv_files:
        latest_file = os.path.join(Config.OUTPUT_DIR, csv_files[0])
        df = pd.read_csv(latest_file)
        data = df.to_dict('records')

    return render_template('results.html', data=data, csv_files=csv_files)


@app.route('/download/<filename>')
def download_file(filename):
    """파일 다운로드"""
    try:
        filepath = os.path.join(Config.OUTPUT_DIR, secure_filename(filename))
        if os.path.exists(filepath):
            return send_file(filepath, as_attachment=True)
        else:
            return jsonify({'status': 'error', 'message': '파일을 찾을 수 없습니다'}), 404
    except Exception as e:
        logger.error(f"다운로드 오류: {e}")
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/view_data/<filename>')
def view_data(filename):
    """데이터 조회"""
    try:
        filepath = os.path.join(Config.OUTPUT_DIR, secure_filename(filename))
        if os.path.exists(filepath):
            df = pd.read_csv(filepath)
            data = df.to_dict('records')
            return jsonify({'status': 'success', 'data': data})
        else:
            return jsonify({'status': 'error', 'message': '파일을 찾을 수 없습니다'}), 404
    except Exception as e:
        logger.error(f"데이터 조회 오류: {e}")
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/get_options')
def get_options():
    """드롭다운 옵션 조회 API"""
    return jsonify({
        'countries': get_country_list(),
        'categories': get_category_list(),
        'periods': get_period_list()
    })


@app.route('/batch_crawl', methods=['POST'])
def batch_crawl():
    """
    일괄 크롤링 API
    선택한 타겟의 모든 카테고리를 자동으로 크롤링
    """
    try:
        data = request.json
        target_type = data.get('target_type', 'shorts')
        country = data.get('country', '한국')
        period = data.get('period', '일간')
        login_mode = data.get('login_mode', False)

        log_function_call(logger, 'batch_crawl',
                         target_type=target_type,
                         country=country,
                         period=period,
                         login_mode=login_mode)

        status_logs.clear()
        add_log(f"일괄 크롤링 시작: {target_type} / {country} / {period}")
        add_log(f"총 {len(CATEGORIES)}개 카테고리 크롤링 예정")

        results = []
        success_count = 0
        fail_count = 0

        for category in CATEGORIES.keys():
            try:
                add_log(f"카테고리 '{category}' 크롤링 중...")
                logger.info(f"Batch crawl - Processing category: {category}")

                # URL 생성
                url = build_url(target_type, category, country, period)

                # 크롤링 실행
                target_count = Config.MAX_ITEMS_WITH_LOGIN if login_mode else Config.MAX_ITEMS_NO_LOGIN
                crawler = PlayboardCrawler(headless=Config.CHROME_HEADLESS)

                # 오늘 날짜를 랭킹 기준으로 사용
                ranking_date = datetime.now().strftime('%Y-%m-%d')

                df = crawler.crawl(
                    url=url,
                    target_type=target_type,
                    login_mode=login_mode,  # 로그인 모드 파라미터 전달 (100개 이상 수집 시 필요)
                    target_count=target_count,
                    country=country,  # PLAN.md 3.5 - 메타 데이터 전달
                    period=period,  # 일간/주간/월간 구분
                    ranking_date=ranking_date  # 랭킹 기준 날짜
                )

                # 안전한 파일 경로 생성
                safe_category = f"batch_{category}"
                filepath, filename = generate_safe_filepath(
                    base_dir=Config.OUTPUT_DIR,
                    target_type=target_type,
                    category=safe_category,
                    country=country,
                    period=period,
                    extension='csv'
                )

                # CSV 저장 (컬럼 순서: Period, Ranking Date, Type, Country, Rank, ...)
                csv_columns = ['Period', 'Ranking Date', 'Type', 'Country', 'Rank', 'Rank Change',
                               'Video Title', 'Views', 'Upload Date', 'Tags',
                               'Channel Name', 'Subscribers', 'Thumbnail']
                csv_df = df[[col for col in csv_columns if col in df.columns]]
                csv_df.to_csv(filepath, index=False, encoding='utf-8-sig')

                # DB 저장
                try:
                    db_handler.insert_dataframe(df, category, country, period, target_type)
                    db_handler.log_crawl_history(target_type, category, country, period, len(df), success=True)
                except Exception as db_error:
                    logger.error(f"DB 저장 실패 (카테고리: {category}): {db_error}")

                results.append({
                    'category': category,
                    'status': 'success',
                    'count': len(df),
                    'filename': filename
                })

                success_count += 1
                add_log(f"✓ '{category}' 완료: {len(df)}개 항목")
                logger.info(f"Batch crawl - Success: {category}, Items: {len(df)}")

            except Exception as e:
                fail_count += 1
                error_msg = f"✗ '{category}' 실패: {str(e)}"
                add_log(error_msg)
                logger.error(f"Batch crawl - Failed: {category}", exc_info=True)
                log_exception(logger, e, f"Batch crawl category: {category}")

                # DB에 실패 기록
                try:
                    db_handler.log_crawl_history(target_type, category, country, period, 0, success=False, error_message=str(e))
                except:
                    pass

                results.append({
                    'category': category,
                    'status': 'failed',
                    'error': str(e)
                })

        add_log(f"일괄 크롤링 완료: 성공 {success_count}개, 실패 {fail_count}개")
        logger.info(f"Batch crawl completed - Success: {success_count}, Failed: {fail_count}")

        return jsonify({
            'status': 'success',
            'message': f'일괄 크롤링 완료: {success_count}/{len(CATEGORIES)} 성공',
            'results': results,
            'success_count': success_count,
            'fail_count': fail_count
        })

    except Exception as e:
        error_msg = f"일괄 크롤링 오류: {str(e)}"
        add_log(error_msg)
        logger.error(error_msg, exc_info=True)
        log_exception(logger, e, "Batch crawl")
        return jsonify({
            'status': 'error',
            'message': error_msg
        }), 500


@app.route('/api/stats', methods=['GET'])
def api_stats():
    """대시보드 통계 데이터 API (3개 테이블 통합)"""
    try:
        stats = db_handler.get_statistics()

        # 오늘 수집된 데이터 카운트
        from datetime import date
        today = date.today().isoformat()

        return jsonify({
            'status': 'success',
            'data': {
                'total_shorts': stats.get('total_shorts', 0),
                'total_videos': stats.get('total_videos', 0),
                'total_channels': stats.get('total_channels', 0),
                'total_items': stats.get('total_items', 0),
                'total_crawls': stats.get('total_crawls', 0),
                'shorts_by_category': stats.get('shorts_by_category', []),
                'videos_by_category': stats.get('videos_by_category', []),
                'channels_by_category': stats.get('channels_by_category', []),
                'today_date': today
            }
        })

    except Exception as e:
        logger.error(f"Stats API error: {e}", exc_info=True)
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500


@app.route('/api/history', methods=['GET'])
def api_history():
    """크롤링 히스토리 API (차트용 데이터)"""
    try:
        limit = request.args.get('limit', 50, type=int)
        history = db_handler.get_crawl_history(limit=limit)

        # 날짜별 집계
        from collections import defaultdict
        daily_stats = defaultdict(int)

        for record in history:
            crawled_date = record['crawled_at'][:10]  # YYYY-MM-DD만 추출
            daily_stats[crawled_date] += record.get('item_count', 0)

        # 차트용 데이터 포맷
        chart_data = {
            'labels': list(daily_stats.keys())[-7:],  # 최근 7일
            'data': [daily_stats[date] for date in list(daily_stats.keys())[-7:]]
        }

        return jsonify({
            'status': 'success',
            'chart_data': chart_data,
            'recent_history': history[:10]  # 최근 10개 이력
        })

    except Exception as e:
        logger.error(f"History API error: {e}", exc_info=True)
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500


@app.route('/api/search', methods=['GET'])
def api_search():
    """데이터 검색 API"""
    try:
        keyword = request.args.get('keyword', '')
        category = request.args.get('category', '')
        page = request.args.get('page', 1, type=int)
        per_page = request.args.get('per_page', 50, type=int)

        # 카테고리별 조회
        if category:
            videos = db_handler.get_videos_by_category(category, limit=per_page * 2)
        else:
            videos = db_handler.get_recent_videos(limit=per_page * 2)

        # 키워드 필터링 (title 또는 channel_name에서 검색)
        if keyword:
            videos = [
                v for v in videos
                if keyword.lower() in v.get('title', '').lower() or
                   keyword.lower() in v.get('channel_name', '').lower()
            ]

        # 페이지네이션
        start_idx = (page - 1) * per_page
        end_idx = start_idx + per_page
        paginated_videos = videos[start_idx:end_idx]

        return jsonify({
            'status': 'success',
            'data': paginated_videos,
            'total': len(videos),
            'page': page,
            'per_page': per_page,
            'total_pages': (len(videos) + per_page - 1) // per_page
        })

    except Exception as e:
        logger.error(f"Search API error: {e}", exc_info=True)
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500


# ========== 수집 현황 대시보드 API ==========

@app.route('/api/collection_status', methods=['GET'])
def api_collection_status():
    """
    수집 현황 API - 카테고리별 수집 여부 체크박스 현황

    Query params:
        period_type: 'daily', 'weekly', 'monthly', 'custom'
        start_date: YYYY-MM-DD (custom일 때)
        end_date: YYYY-MM-DD (custom일 때)
    """
    try:
        period_type = request.args.get('period_type', 'daily')
        start_date = request.args.get('start_date')
        end_date = request.args.get('end_date')

        status = db_handler.get_collection_status(period_type, start_date, end_date)
        summary = db_handler.get_collection_summary(period_type, start_date, end_date)

        return jsonify({
            'status': 'success',
            'collection_status': status,
            'summary': summary,
            'period_type': period_type
        })

    except Exception as e:
        logger.error(f"Collection status API error: {e}", exc_info=True)
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500


@app.route('/api/collection_history', methods=['GET'])
def api_collection_history():
    """
    수집 이력 API - 기간별 상세 이력 조회

    Query params:
        period_type: 'daily', 'weekly', 'monthly', 'custom'
        start_date: YYYY-MM-DD (custom일 때)
        end_date: YYYY-MM-DD (custom일 때)
    """
    try:
        period_type = request.args.get('period_type', 'daily')
        start_date = request.args.get('start_date')
        end_date = request.args.get('end_date')

        history = db_handler.get_crawl_history_by_period(period_type, start_date, end_date)

        return jsonify({
            'status': 'success',
            'history': history,
            'total': len(history),
            'period_type': period_type
        })

    except Exception as e:
        logger.error(f"Collection history API error: {e}", exc_info=True)
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500


@app.route('/api/db_search', methods=['GET'])
def api_db_search():
    """
    데이터베이스 검색 API - 쇼츠/영상/채널 테이블에서 검색

    Query params:
        table: 'shorts', 'videos', 'channels'
        keyword: 검색어 (제목 또는 채널명)
        category: 카테고리 필터
        country: 국가 필터
        period: 기간 필터
        limit: 결과 개수 (기본 100)
    """
    try:
        table = request.args.get('table', 'shorts')
        keyword = request.args.get('keyword', '')
        category = request.args.get('category', '')
        country = request.args.get('country', '')
        period = request.args.get('period', '')
        limit = request.args.get('limit', 100, type=int)

        cursor = db_handler.conn.cursor()

        # 테이블 선택
        table_map = {
            'shorts': 'shorts_rank',
            'videos': 'videos_rank',
            'channels': 'channels_rank'
        }
        table_name = table_map.get(table, 'shorts_rank')

        # 동적 WHERE 조건 생성
        conditions = []
        params = []

        if keyword:
            if table == 'channels':
                conditions.append("channel_name LIKE ?")
            else:
                conditions.append("(title LIKE ? OR channel_name LIKE ?)")
                params.append(f"%{keyword}%")
            params.append(f"%{keyword}%")

        if category:
            conditions.append("category = ?")
            params.append(category)

        if country:
            conditions.append("country = ?")
            params.append(country)

        if period:
            conditions.append("period = ?")
            params.append(period)

        # SQL 쿼리 생성
        where_clause = " AND ".join(conditions) if conditions else "1=1"
        query = f'''
            SELECT * FROM {table_name}
            WHERE {where_clause}
            ORDER BY crawled_at DESC, rank ASC
            LIMIT ?
        '''
        params.append(limit)

        cursor.execute(query, params)
        rows = cursor.fetchall()
        data = [dict(row) for row in rows]

        return jsonify({
            'status': 'success',
            'data': data,
            'total': len(data),
            'table': table
        })

    except Exception as e:
        logger.error(f"DB Search API error: {e}", exc_info=True)
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500


if __name__ == '__main__':
    logger.info("Starting Flask application...")
    app.run(debug=True, host='0.0.0.0', port=5000)
