#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
환경 체크 스크립트
- 상대 경로 기반 작업 디렉토리 확인
- 필요한 파일들 존재 여부 확인
- Python 버전 및 패키지 확인
"""

import sys
import os
from pathlib import Path

def check_environment():
    """환경 설정 체크"""
    print("=" * 50)
    print("환경 체크 시작")
    print("=" * 50)
    
    # 1. 현재 스크립트 디렉토리
    script_dir = Path(__file__).parent
    print(f"[폴더] 스크립트 디렉토리: {script_dir}")
    print(f"[폴더] 절대 경로: {script_dir.resolve()}")
    
    # 2. 필수 파일들 체크
    required_files = [
        "Main_Extract.py",
        "GUI_Extract.py", 
        "requirements.txt"
    ]
    
    print("\n[확인] 필수 파일 체크:")
    missing_files = []
    for file_path in required_files:
        full_path = script_dir / file_path
        if full_path.exists():
            print(f"[OK] {file_path}")
        else:
            print(f"[X] {file_path} (누락)")
            missing_files.append(file_path)
            
    # 서비스 계정 키 체크 (로컬 및 공유 루트)
    service_key_rel = "google_service_key/service-account-key.json"
    full_service_key_path = script_dir / service_key_rel
    parent_service_key_path = script_dir / ".." / ".." / service_key_rel
    if full_service_key_path.exists():
        print(f"[OK] {service_key_rel} (로컬)")
    elif parent_service_key_path.exists():
        print(f"[OK] {service_key_rel} (공유 루트)")
    else:
        print(f"[X] {service_key_rel} (누락)")
        missing_files.append(service_key_rel)
    
    # 3. 로그 디렉토리 체크/생성
    log_dir = script_dir / "logs"
    if log_dir.exists():
        print(f"[OK] logs 디렉토리 존재")
    else:
        log_dir.mkdir(exist_ok=True)
        print(f"[생성] logs 디렉토리 생성됨")
    
    # 4. Python 버전 체크
    print(f"\n[Python] 버전: {sys.version}")
    print(f"[Python] 실행 경로: {sys.executable}")
    
    # 5. 필수 패키지 체크
    required_packages = [
        "aiohttp",
        "gspread", 
        "google.auth",
        "selenium",
        "keyboard"
    ]
    
    print("\n[패키지] 체크:")
    missing_packages = []
    for package in required_packages:
        try:
            __import__(package)
            print(f"[OK] {package}")
        except ImportError:
            print(f"[X] {package} (미설치)")
            missing_packages.append(package)
    
    # 6. 결과 요약
    print("\n" + "=" * 50)
    if not missing_files and not missing_packages:
        print("[성공] 모든 환경 체크 통과!")
        print("[성공] 어떤 디렉토리에서든 실행 가능합니다.")
    else:
        print("[경고] 일부 문제가 발견되었습니다:")
        if missing_files:
            print(f"   - 누락된 파일: {', '.join(missing_files)}")
        if missing_packages:
            print(f"   - 미설치 패키지: {', '.join(missing_packages)}")
            print("   - 해결방법: pip install -r requirements.txt")
    
    print("=" * 50)
    return len(missing_files) == 0 and len(missing_packages) == 0

if __name__ == "__main__":
    success = check_environment()
    # Batch file에서 호출될 때는 자동으로 종료
    # 직접 실행할 때만 pause
    if sys.stdin.isatty():
        input("\nPress Enter to continue...")
    sys.exit(0 if success else 1)