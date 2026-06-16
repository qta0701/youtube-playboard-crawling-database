import os
import sys
import shutil

# 프로젝트 루트 폴더를 sys.path에 추가
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from modules.utils import generate_safe_filepath
from modules.crawler_runner import find_existing_batch_file_runner
from config import Config

def test_country_folder_generation():
    print("Testing generate_safe_filepath with country filters...")
    
    # 1. 미국(해외) 선택 시
    target_type = "shorts"
    category = "all"
    country = "미국"
    period = "일간"
    criteria = "조회수 순위"
    ranking_date = "2026-06-16"
    
    filepath_us, filename_us = generate_safe_filepath(
        base_dir=Config.OUTPUT_DIR,
        target_type=target_type,
        category=category,
        country=country,
        period=period,
        criteria=criteria,
        ranking_date=ranking_date
    )
    
    print(f"US Filepath: {filepath_us}")
    # 경로상에 '미국' 서브폴더가 포함되어 있어야 함
    expected_us_subdir = os.path.join(Config.OUTPUT_DIR, "2026_06_16", "Shorts", "미국")
    assert expected_us_subdir in filepath_us
    print("✓ US Folder generation test passed!")
    
    # 2. 한국 선택 시 (기존 경로 유지)
    country_kr = "한국"
    filepath_kr, filename_kr = generate_safe_filepath(
        base_dir=Config.OUTPUT_DIR,
        target_type=target_type,
        category=category,
        country=country_kr,
        period=period,
        criteria=criteria,
        ranking_date=ranking_date
    )
    
    print(f"KR Filepath: {filepath_kr}")
    # 경로상에 '한국' 서브폴더가 포함되지 않고 기존처럼 바로 Shorts 아래에 있어야 함
    expected_kr_subdir = os.path.join(Config.OUTPUT_DIR, "2026_06_16", "Shorts")
    assert os.path.normpath(os.path.dirname(filepath_kr)) == os.path.normpath(expected_kr_subdir)
    print("✓ KR Folder (Legacy Path) test passed!")
    
    # 3. find_existing_batch_file_runner 탐색 검증
    # 임시 테스트용 가짜 파일 생성
    os.makedirs(expected_us_subdir, exist_ok=True)
    test_file_path = os.path.join(expected_us_subdir, filename_us)
    with open(test_file_path, 'w', encoding='utf-8') as f:
        f.write("rank,title\n1,US Video")
        
    print(f"Created temp test file at: {test_file_path}")
    
    # 검색 실행
    found_path = find_existing_batch_file_runner(
        target_type=target_type,
        category=category,
        country=country,
        period=period,
        criteria=criteria,
        ranking_date=ranking_date
    )
    
    print(f"Found Path: {found_path}")
    assert found_path is not None
    assert os.path.normpath(found_path) == os.path.normpath(test_file_path)
    print("✓ find_existing_batch_file_runner search test passed!")
    
    # 가짜 파일 삭제 클린업
    if os.path.exists(test_file_path):
        os.remove(test_file_path)
    # 빈 폴더들 클린업
    try:
        os.rmdir(expected_us_subdir)
        os.rmdir(os.path.join(Config.OUTPUT_DIR, "2026_06_16", "Shorts"))
        os.rmdir(os.path.join(Config.OUTPUT_DIR, "2026_06_16"))
    except Exception:
        pass
        
    print("\nAll tests completed successfully!")

if __name__ == '__main__':
    test_country_folder_generation()
