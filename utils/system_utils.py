# -*- coding: utf-8 -*-
"""
시스템 경로 관련 유틸리티 모듈 (system_utils.py)
타 PC의 절대 경로를 현재 실행 중인 PC의 프로젝트 루트 경로에 맞추어 유연하게 치환 및 복원합니다.
"""
import os
import re

def resolve_meta_path(path: str) -> str:
    """
    타 PC 환경의 절대 경로가 메타데이터나 설정에 포함되어 있을 경우,
    현재 PC 환경의 프로젝트 루트 경로와 결합하여 정상 동작할 수 있도록
    동적으로 경로를 치환하고 정규화합니다. (rules.md 규칙 준수)

    Args:
        path (str): 변환할 대상 파일/디렉토리 경로

    Returns:
        str: 현재 PC 환경에 맞추어 변환된 절대 경로
    """
    if not path:
        return path

    # 1. 윈도우/리눅스 등 OS 경로 표준화
    normalized_path = os.path.normpath(path)

    # 2. 현재 PC 환경의 프로젝트 루트 디렉토리 계산
    # 이 파일은 프로젝트루트/utils/system_utils.py 에 위치하므로 부모의 부모가 루트가 됨
    current_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    # 3. 만약 이미 상대 경로라면 현재 루트 경로와 안전하게 결합
    if not os.path.isabs(normalized_path):
        # 상대 경로에 드라이브 문자나 불완전한 윈도우 기호가 들어갔을 경우 보정
        clean_rel = normalized_path.lstrip('\\/ ')
        # 혹시 'google_service_key/' 나 'output/' 형태의 경로 구조를 보존하며 결합
        return os.path.abspath(os.path.join(current_root, clean_rel))

    # 4. 절대 경로인 경우:
    # 경로를 분할하여 프로젝트 폴더 이름 기준 또는 표준 서브폴더 기준으로 하위 상대경로 획득
    project_folder_name = "youtube-playboard-crawling-database"
    parts = re.split(r'[\\/]', normalized_path)
    lower_parts = [p.lower() for p in parts]

    # (A) 프로젝트 최상위 폴더명이 경로에 들어있는 경우
    if project_folder_name in lower_parts:
        idx = lower_parts.index(project_folder_name)
        # 프로젝트 폴더명 다음부터의 세그먼트들을 떼어냄
        relative_subpath = os.path.join(*parts[idx + 1:])
        return os.path.abspath(os.path.join(current_root, relative_subpath))

    # (B) 프로젝트 명은 없으나 알려진 표준 서브폴더가 포함되어 있는 경우
    standard_folders = ['output', 'logs', 'google_service_key', 'modules', '외부프로그램', 'utils', 'venv']
    for folder in standard_folders:
        if folder in lower_parts:
            idx = lower_parts.index(folder)
            relative_subpath = os.path.join(*parts[idx:])
            return os.path.abspath(os.path.join(current_root, relative_subpath))

    # (C) 모든 규칙에 매칭되지 않는 쌩 절대 경로인 경우 (예: C:\Users\alma\secret.json 등)
    # 파일명만 추출해서 프로젝트 루트 내부에 실재하는 파일인지 스캔
    file_name = os.path.basename(normalized_path)
    if file_name:
        for root_dir, dirs, files in os.walk(current_root):
            # 검색 성능을 위해 venv 및 .git은 스킵
            if 'venv' in dirs:
                dirs.remove('venv')
            if '.git' in dirs:
                dirs.remove('.git')
            if file_name in files:
                return os.path.abspath(os.path.join(root_dir, file_name))

    # 최종 폴백: 실재 검색도 실패하면 현재 루트와 파일명을 직접 붙임
    return os.path.abspath(os.path.join(current_root, file_name))
