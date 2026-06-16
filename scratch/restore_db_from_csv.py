import os
import re
import sqlite3
import pandas as pd
from datetime import datetime

# 프로젝트 루트 추가
import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from modules.database import DatabaseHandler

DB_PATH = 'output/db/youtube_data.db'
OUTPUT_DIR = 'output'

def restore_database():
    print("=== 크롤링 데이터베이스 CSV 복원 시작 ===")
    
    # 1. DB 연결 및 테이블 DROP
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    print("기존 랭킹 테이블 DROP 중 (UNIQUE 제약조건 갱신용)...")
    cursor.execute("DROP TABLE IF EXISTS shorts_rank")
    cursor.execute("DROP TABLE IF EXISTS videos_rank")
    cursor.execute("DROP TABLE IF EXISTS channels_rank")
    conn.commit()
    conn.close()
    
    # DatabaseHandler 호출하여 테이블 재생성 (새로운 UNIQUE 제약조건 적용됨)
    db_handler = DatabaseHandler(DB_PATH)
    
    # 2. output 디렉토리 순회
    if not os.path.exists(OUTPUT_DIR):
        print(f"오류: {OUTPUT_DIR} 디렉토리가 존재하지 않습니다.")
        return
        
    date_folders = [f for f in os.listdir(OUTPUT_DIR) if re.match(r'^\d{4}_\d{2}_\d{2}$', f)]
    print(f"발견된 날짜 폴더: {date_folders}")
    
    total_files_processed = 0
    total_rows_inserted = 0
    
    for date_folder in date_folders:
        date_path = os.path.join(OUTPUT_DIR, date_folder)
        for type_folder in ['Shorts', 'Video', 'Channel']:
            type_path = os.path.join(date_path, type_folder)
            if not os.path.exists(type_path):
                continue
                
            csv_files = [f for f in os.listdir(type_path) if f.endswith('.csv')]
            for csv_file in csv_files:
                csv_filepath = os.path.join(type_path, csv_file)
                print(f"\n파일 처리 중: {csv_file}")
                
                parts = csv_file.replace('.csv', '').split('_')
                if len(parts) < 6:
                    print(f"스킵: 파일명 형식이 너무 짧음 -> {csv_file}")
                    continue
                
                # target_type 결정
                target_type_str = parts[0].lower()
                if 'shorts' in target_type_str:
                    target_type = 'shorts'
                elif 'channel' in target_type_str:
                    target_type = 'channel'
                else:
                    target_type = 'video'
                    
                # 뒤에서부터 고정 매핑
                criteria = parts[-4]
                period = parts[-5]
                country = parts[-6]
                
                # category 결정
                is_batch = parts[1] == 'batch'
                if is_batch:
                    category = "batch_" + "_".join(parts[2:-6])
                else:
                    category = parts[1] # 'ALL'
                
                try:
                    df = pd.read_csv(csv_filepath)
                    if df.empty:
                        print("빈 파일 스킵")
                        continue
                        
                    # 폴더명(date_folder) 기준 날짜를 YYYY-MM-DD 형식으로 변환하여 Ranking Date 강제 보정
                    correct_date = date_folder.replace('_', '-')
                    df['Ranking Date'] = correct_date
                    if 'ranking_date' in df.columns:
                        df['ranking_date'] = correct_date
                        
                    # Criteria 컬럼이 없으면 강제로 넣어줌
                    if 'Criteria' not in df.columns and 'criteria' not in df.columns:
                        df['Criteria'] = criteria
                        
                    # DB에 데이터 삽입
                    inserted = db_handler.insert_dataframe(df, category, country, period, target_type)
                    total_files_processed += 1
                    total_rows_inserted += inserted
                    print(f"삽입 완료: {inserted}개 행 (대상: {target_type}, 카테고리: {category}, 기준: {criteria})")
                except Exception as e:
                    print(f"에러 발생 ({csv_file}): {e}")
                    
    print("\n=======================================")
    print("✓ 복원 완료!")
    print(f"총 처리된 파일 수: {total_files_processed}개")
    print(f"총 삽입된 행 수  : {total_rows_inserted}개")
    print("=======================================")

if __name__ == '__main__':
    restore_database()
