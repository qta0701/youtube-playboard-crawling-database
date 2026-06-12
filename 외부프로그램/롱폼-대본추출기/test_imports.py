#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
패키지 import 테스트 스크립트
"""

import sys
print(f"Python 버전: {sys.version}")
print(f"Python 실행 경로: {sys.executable}")
print()

packages = [
    'aiohttp',
    'gspread', 
    'google.auth',
    'google.oauth2',
    'json',
    'pathlib',
    'logging'
]

print("패키지 import 테스트:")
print("-" * 40)

for package in packages:
    try:
        __import__(package)
        print(f"[OK] {package} - 정상")
    except ImportError as e:
        print(f"[FAIL] {package} - 실패: {e}")

print("-" * 40)
print("테스트 완료!")