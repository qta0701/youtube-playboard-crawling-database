# -*- coding: utf-8 -*-
"""
외부 프로그램 독립 격리 로더 (external_loader.py)
롱폼/숏폼 외부 프로젝트의 파일 경로를 기반으로 모듈 간의 충돌 없이
독립적으로 메모리에 로드하기 위한 유틸리티입니다.
"""
import os
import sys
import importlib.util
from typing import Any

# 네임스페이스 충돌이 예상되는 모듈 이름 목록
CONFLICT_MODULES = ["sheet_config", "sheet_utils", "Main_Extract", "Main_Search", "GUI_Extract", "GUI_Interface"]

def load_isolated_module(module_name: str, file_path: str, project_dir: str) -> Any:
    """
    지정된 프로젝트 경로 내의 Python 파일을 격리된 네임스페이스로 로드합니다.
    내부에서 충돌 가능성이 있는 모듈들은 sys.modules 샌드박싱을 적용합니다.

    Args:
        module_name: 로드될 모듈의 고유 이름 (예: 'long_extract_gui')
        file_path: 모듈 파일의 절대 경로
        project_dir: 해당 모듈이 속한 프로젝트 루트 디렉토리 경로
    """
    project_dir = os.path.abspath(project_dir)
    file_path = os.path.abspath(file_path)

    # 1. sys.path 샌드박싱 (해당 프로젝트 경로를 최우선 순위로 지정)
    original_path = sys.path.copy()
    if project_dir in sys.path:
        sys.path.remove(project_dir)
    sys.path.insert(0, project_dir)

    # 2. sys.modules 샌드박싱 (충돌 대상 모듈 임시 제거 및 백업)
    backed_up_modules = {}
    for mod in CONFLICT_MODULES:
        if mod in sys.modules:
            backed_up_modules[mod] = sys.modules.pop(mod)

    try:
        # 3. 대상 모듈 로드
        spec = importlib.util.spec_from_file_location(module_name, file_path)
        if spec is None or spec.loader is None:
            raise ImportError(f"모듈 명세(spec)를 생성할 수 없습니다: {file_path}")
        
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
        
        # 4. 모듈 로드 과정에서 해당 프로젝트 내부의 sheet_config 등이 임포트되었을 것이므로,
        # 격리 모듈 객체 내부에 이들을 명시적으로 보관하여 참조할 수 있도록 바인딩합니다.
        for mod in CONFLICT_MODULES:
            if mod in sys.modules:
                setattr(module, f"_{mod}", sys.modules[mod])
                
        return module

    finally:
        # 5. sys.modules 복원 (백업했던 기존 모듈 복구 및 샌드박스에서 로드된 충돌 모듈 제거)
        for mod in CONFLICT_MODULES:
            if mod in sys.modules:
                sys.modules.pop(mod)
        for mod, mod_obj in backed_up_modules.items():
            sys.modules[mod] = mod_obj

        # 6. sys.path 복원
        sys.path = original_path
