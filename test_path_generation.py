import os
from datetime import datetime
from modules.utils import generate_safe_filepath
from config import Config

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

if __name__ == "__main__":
    test_filepath_generation()
