"""
PyInstaller를 사용하여 실행파일 생성하는 스크립트
"""
import os
import sys
import subprocess
from pathlib import Path

def build_executable():
    """실행파일 빌드"""
    
    # 현재 스크립트 경로
    current_dir = Path(__file__).parent
    gui_script = current_dir / "GUI_Extract.py"
    
    # PyInstaller 명령어 구성 (오탐 방지 옵션 추가)
    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--onedir",  # 폴더 형태로 생성 (오탐 가능성 낮음)
        "--windowed",  # 콘솔 창 숨기기 (GUI 앱용)
        "--name", "YouTube_Shorts_Transcript_Extractor",  # 실행파일 이름
        "--distpath", "dist_safe",  # 안전한 폴더명 사용
        "--workpath", "build_temp",  # 임시 폴더명
        "--noconfirm",  # 기존 파일 덮어쓰기
        "--clean",  # 빌드 전 정리
        # 오탐 방지를 위한 옵션들
        "--noupx",  # UPX 압축 비활성화 (백신 오탐 원인)
        "--debug=noarchive",  # 아카이브 디버그 정보 비활성화
        # 필수 모듈 포함
        "--hidden-import", "gspread",
        "--hidden-import", "google.auth",
        "--hidden-import", "google.oauth2.service_account", 
        "--hidden-import", "aiohttp",
        "--hidden-import", "tkinter",
        "--hidden-import", "asyncio",
        "--hidden-import", "logging",
        "--hidden-import", "json",
        "--hidden-import", "threading",
        str(gui_script)
    ]
    
    print("🔨 실행파일 빌드를 시작합니다...")
    print("📦 이 과정은 몇 분 정도 소요될 수 있습니다.")
    
    try:
        # PyInstaller 실행
        result = subprocess.run(cmd, cwd=current_dir, capture_output=True, text=True)
        
        if result.returncode == 0:
            print("Build completed successfully!")
            print(f"Executable location: {current_dir}/dist_safe/YouTube_Shorts_Transcript_Extractor/")
            print("\nHow to use:")
            print("1. Copy entire 'YouTube_Shorts_Transcript_Extractor' folder from dist_safe")
            print("2. Copy 'Google Service Account JSON Key' folder as well")
            print("3. Double-click YouTube_Shorts_Transcript_Extractor.exe to run")
            print("\nNote:")
            print("- You must copy the entire folder for it to work properly")
            print("- Created as folder format (not single exe) to prevent antivirus false positives")
        else:
            print("❌ 빌드 중 오류가 발생했습니다:")
            print(result.stderr)
            
    except FileNotFoundError:
        print("❌ PyInstaller가 설치되지 않았습니다.")
        print("📦 다음 명령어로 설치해주세요:")
        print("pip install pyinstaller")
    except Exception as e:
        print(f"❌ 예상치 못한 오류가 발생했습니다: {e}")

def install_requirements():
    """필수 패키지 설치"""
    requirements_file = Path(__file__).parent / "requirements.txt"
    
    if requirements_file.exists():
        print("📦 필수 패키지를 설치합니다...")
        try:
            subprocess.run([sys.executable, "-m", "pip", "install", "-r", str(requirements_file)], check=True)
            print("✅ 패키지 설치 완료!")
        except subprocess.CalledProcessError as e:
            print(f"❌ 패키지 설치 실패: {e}")
            return False
    
    # PyInstaller 설치
    print("🔨 PyInstaller를 설치합니다...")
    try:
        subprocess.run([sys.executable, "-m", "pip", "install", "pyinstaller"], check=True)
        print("✅ PyInstaller 설치 완료!")
    except subprocess.CalledProcessError as e:
        print(f"❌ PyInstaller 설치 실패: {e}")
        return False
        
    return True

if __name__ == "__main__":
    print("🚀 YouTube Shorts 대본추출기 실행파일 빌드")
    print("="*50)
    
    # 패키지 설치
    if install_requirements():
        print("\n" + "="*50)
        # 실행파일 빌드
        build_executable()
    
    input("\n🔚 아무 키나 눌러서 종료...")