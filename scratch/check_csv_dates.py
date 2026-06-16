import pandas as pd

path = r"c:\program1\개인자료\AI쇼츠_회사\youtube-project\플레이보드-크롤링\output\2026_06_14\Shorts\shorts_batch_게임_한국_일간_조회수 순위_2026_06_14.csv"
df = pd.read_csv(path)
print("=== Ranking Date 분포 ===")
print(df['Ranking Date'].value_counts())
print("\n=== 상위 10행의 Ranking Date ===")
print(df[['Rank', 'Video ID', 'Ranking Date']].head(10))
