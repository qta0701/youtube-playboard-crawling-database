import os
import sys
from datetime import datetime

# sys.path에 외부프로그램 경로 추가
current_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.abspath(os.path.join(current_dir, ".."))
sys.path.append(project_root)

# 숏폼-유튜브검색기 Main_Search 로드
shorts_search_dir = os.path.join(project_root, "외부프로그램", "숏폼-유튜브검색기")
sys.path.append(shorts_search_dir)

from Main_Search import YouTubeSearchAPI

def test_search():
    print("=== 숏폼 유튜브 검색기 테스트 시작 ===")
    youtube_api = YouTubeSearchAPI()

    # 1. 인증 시도
    secret_dir = os.path.join(project_root, "google_service_key")
    client_secret_file = None
    for file in os.listdir(secret_dir):
        if file.startswith("client_secret") and file.endswith(".json"):
            client_secret_file = os.path.join(secret_dir, file)
            break

    if client_secret_file:
        print(f"인증 파일 감지: {client_secret_file}")
        youtube_api.authenticate_oauth(client_secret_file)
        print("OAuth 인증 성공!")
    else:
        print("인증 파일을 찾을 수 없습니다.")
        return

    # 2. 캐시를 비운 채로 국가 필터 포함(only_country=True, 한국) 테스트
    print("\n--- [Test 1] 국가 필터(only_country=True, 한국) 검색 테스트 ---")
    results, stats = youtube_api.search_videos(
        keyword="Must have item",
        max_results=5,
        order="relevance",
        search_country="한국",
        exclude_country=False,
        only_country=True,
        db_cache={}
    )
    print(f"검색 결과 수: {len(results)}")
    print(f"API 통계: {stats}")
    for idx, r in enumerate(results[:3]):
        print(f"[{idx+1}] ID: {r['영상 ID']}, 제목: {r['제목'][:30]}, 채널명: {r['채널명']}, 채널국가: {r['채널국가']}")

    # 3. 캐시 적용 테스트 (동일 영상 ID에 대해 캐시 주입 시 API 스킵 검증)
    print("\n--- [Test 2] 당일 수집 캐시 스킵(Quota 절약) 테스트 ---")
    db_cache = {}
    if results:
        target_video = results[0]
        v_id = target_video['영상 ID']
        # 더미 캐시 데이터 제작 (오늘 수집한 형태)
        db_cache[v_id] = {
            '영상 ID': v_id,
            '영상 업로드날짜': target_video['영상 업로드날짜'],
            '수집날짜': datetime.now().strftime('%Y-%m-%d'),
            '검색 키워드': 'Must have item',
            '영상 링크': target_video['영상 링크'],
            '제목': '[캐시 대체됨] ' + target_video['제목'],
            '채널명': target_video['채널명'],
            '조회수': target_video['조회수'],
            '영상길이': target_video['영상길이'],
            '좋아요 수': target_video['좋아요 수'],
            '댓글수': target_video['댓글수'],
            '구독자수': target_video['구독자수'],
            '구독자 대비 조회수 배율': target_video['구독자 대비 조회수 배율'],
            '조회수 대비 좋아요': target_video['조회수 대비 좋아요'],
            '조회수 대비 댓글': target_video['조회수 대비 댓글'],
            '33. 채널명': target_video['33. 채널명'],
            '채널국가': target_video['채널국가'],
            '채널 ID': target_video['채널 ID'],
            '채널링크': target_video['채널링크'],
            '채널 디스크립션': target_video['채널 디스크립션'],
            '채널 핸들': target_video['채널 핸들'],
            '썸네일 링크': target_video['썸네일 링크'],
            '영상갯수': target_video['영상갯수'],
            '채널 전체 조회수': target_video['채널 전체 조회수'],
            '영상당 평균 조회수': target_video['영상당 평균 조회수'],
            '채널 개설일': target_video['채널 개설일'],
            '카테고리 ID': target_video['카테고리 ID'],
            '디스크립션': target_video['디스크립션'],
            '디스크립션 텍스트 수': target_video['디스크립션 텍스트 수'],
            '해시태그 유무': target_video['해시태그 유무'],
            '썸네일 이미지주소': target_video['썸네일 이미지주소'],
        }

    # 캐시와 함께 다시 검색 수행
    results_cached, stats_cached = youtube_api.search_videos(
        keyword="Must have item",
        max_results=5,
        order="relevance",
        search_country="한국",
        exclude_country=False,
        only_country=True,
        db_cache=db_cache
    )
    print(f"캐시 적용 후 검색 결과 수: {len(results_cached)}")
    print(f"캐시 적용 후 API 통계: {stats_cached}")
    
    # 캐시 대치가 일어났는지 검증
    cache_hit = False
    for r in results_cached:
        if "[캐시 대체됨]" in r['제목']:
            cache_hit = True
            print(f"✓ 캐시 대치 확인 성공! 비디오 ID: {r['영상 ID']}")
            break
            
    if cache_hit:
        print("▶ 성공: 중복 수집 시 API 호출을 스킵하고 캐시 정보를 활용하여 쿼터를 아꼈습니다.")
    else:
        print("▶ 실패: 캐시 정보가 적용되지 않았습니다.")

if __name__ == "__main__":
    test_search()
