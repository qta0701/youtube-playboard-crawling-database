import os
from datetime import datetime
from modules.utils import generate_safe_filepath
from config import Config
from utils.system_utils import resolve_meta_path

def test_filepath_generation():
    print("Testing generate_safe_filepath...")
    
    # Test parameters
    target_type = "test_target"
    category = "test_category"
    country = "KR"
    period = "daily"
    
    # Expected folder name
    today_str = datetime.now().strftime('%Y_%m_%d')
    expected_dir = os.path.join(Config.OUTPUT_DIR, today_str)
    
    # Run function
    filepath, filename = generate_safe_filepath(
        Config.OUTPUT_DIR, target_type, category, country, period
    )
    
    print(f"Generated filepath: {filepath}")
    print(f"Generated filename: {filename}")
    
    # Verify directory existence
    if os.path.exists(expected_dir):
        print(f"SUCCESS: Directory {expected_dir} exists.")
    else:
        print(f"FAILURE: Directory {expected_dir} NOT found.")
        
    # Verify file path structure
    if expected_dir in filepath:
        print(f"SUCCESS: Filepath lies within the date directory.")
    else:
        print(f"FAILURE: Filepath {filepath} is NOT in {expected_dir}.")

def test_resolve_meta_path():
    print("\nTesting resolve_meta_path...")
    
    # Test case 1: 타 PC의 절대 경로 (프로젝트 폴더명 매칭)
    foreign_path = r"C:\Users\old_user\youtube-playboard-crawling-database\google_service_key\service-account-key.json"
    resolved_path = resolve_meta_path(foreign_path)
    print(f"Foreign Path: {foreign_path}")
    print(f"Resolved Path: {resolved_path}")
    
    current_root = os.path.dirname(os.path.abspath(__file__))
    expected_path = os.path.abspath(os.path.join(current_root, "google_service_key", "service-account-key.json"))
    
    if os.path.normpath(resolved_path) == os.path.normpath(expected_path):
        print("SUCCESS: Path resolved correctly using project folder name.")
    else:
        print(f"FAILURE: Expected {expected_path}, got {resolved_path}")

    # Test case 2: 상대 경로 입력
    relative_path = "output/db/youtube_data.db"
    resolved_rel = resolve_meta_path(relative_path)
    print(f"Relative Path: {relative_path}")
    print(f"Resolved Path: {resolved_rel}")
    expected_rel = os.path.abspath(os.path.join(current_root, relative_path))
    if os.path.normpath(resolved_rel) == os.path.normpath(expected_rel):
        print("SUCCESS: Relative path resolved correctly.")
    else:
        print(f"FAILURE: Expected {expected_rel}, got {resolved_rel}")

    # Test case 3: 프로젝트 명은 없지만 알려진 하위 폴더 포함 절대 경로
    foreign_path_2 = r"D:\some_user\AppData\Local\Temp\output\settings.json"
    resolved_path_2 = resolve_meta_path(foreign_path_2)
    print(f"Foreign Path 2: {foreign_path_2}")
    print(f"Resolved Path 2: {resolved_path_2}")
    expected_path_2 = os.path.abspath(os.path.join(current_root, "output", "settings.json"))
    if os.path.normpath(resolved_path_2) == os.path.normpath(expected_path_2):
        print("SUCCESS: Standard subdirectory mapping worked.")
    else:
        print(f"FAILURE: Expected {expected_path_2}, got {resolved_path_2}")

def test_load_settings_resolution():
    print("\nTesting load_settings with resolve_meta_path integration...")
    import json
    import shutil
    
    # 임시 settings.json 작성
    temp_settings_file = 'output/settings.json'
    os.makedirs(os.path.dirname(temp_settings_file), exist_ok=True)
    
    backup_exists = os.path.exists(temp_settings_file)
    if backup_exists:
        shutil.copy(temp_settings_file, temp_settings_file + '.bak')
        
    test_data = {
        "ext_trans_creds": r"C:\Users\alma\youtube-playboard-crawling-database\google_service_key\service-account-key.json",
        "some_random_path": r"D:\old_dir\youtube-playboard-crawling-database\logs\app.log",
        "normal_value": "hello_world"
    }
    
    with open(temp_settings_file, 'w', encoding='utf-8') as f:
        json.dump(test_data, f)
        
    try:
        from app import load_settings
        settings = load_settings()
        
        current_root = os.path.dirname(os.path.abspath(__file__))
        expected_creds = os.path.abspath(os.path.join(current_root, "google_service_key", "service-account-key.json"))
        expected_path = os.path.abspath(os.path.join(current_root, "logs", "app.log"))
        
        print(f"Loaded creds: {settings.get('ext_trans_creds')}")
        print(f"Loaded path: {settings.get('some_random_path')}")
        
        if os.path.normpath(settings.get('ext_trans_creds')) == os.path.normpath(expected_creds):
            print("SUCCESS: ext_trans_creds resolved correctly through load_settings().")
        else:
            print("FAILURE: ext_trans_creds not resolved.")
            
        if os.path.normpath(settings.get('some_random_path')) == os.path.normpath(expected_path):
            print("SUCCESS: some_random_path resolved correctly through load_settings().")
        else:
            print("FAILURE: some_random_path not resolved.")
            
    finally:
        # 복원
        if backup_exists:
            shutil.move(temp_settings_file + '.bak', temp_settings_file)
        elif os.path.exists(temp_settings_file):
            os.remove(temp_settings_file)

if __name__ == "__main__":
    test_filepath_generation()
    test_resolve_meta_path()
    test_load_settings_resolution()

