import pandas as pd

path = r"c:\program1\개인자료\AI쇼츠_회사\youtube-project\플레이보드-크롤링\output\2026_06_14\Shorts\shorts_batch_게임_한국_일간_조회수 순위_2026_06_14.csv"
df = pd.read_csv(path)
print("CSV 행 수:", len(df))
print("유니크 Video ID 수:", df['Video ID'].nunique())
print("Video ID sample:")
print(df['Video ID'].head(10))
print("Video ID null count:", df['Video ID'].isnull().sum())
print("Video ID value counts (top 5):")
print(df['Video ID'].value_counts().head(5))
