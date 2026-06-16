import os
import pandas as pd

target_dir = r"c:\program1\개인자료\AI쇼츠_회사\youtube-project\플레이보드-크롤링\output\2026_06_14\Shorts"

files = [
    "shorts_batch_게임_한국_일간_조회수 순위_2026_06_14.csv",
    "shorts_batch_과학기술_한국_일간_조회수 순위_2026_06_14.csv",
    "shorts_batch_게임_한국_일간_댓글 순위_2026_06_14.csv"
]

for f in files:
    path = os.path.join(target_dir, f)
    if os.path.exists(path):
        df = pd.read_csv(path)
        print(f"{f} -> 행 개수: {len(df)}")
        if not df.empty:
            print("컬럼 목록:", list(df.columns))
            print("첫 3행:")
            print(df.head(3)[['Rank', 'Video Title', 'Channel Name', 'Views']])
    else:
        print(f"{f} 파일이 존재하지 않습니다.")
