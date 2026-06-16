with open(r"c:\program1\개인자료\AI쇼츠_회사\youtube-project\플레이보드-크롤링\app.py", "r", encoding="utf-8") as f:
    lines = f.readlines()

for i, line in enumerate(lines, 1):
    if 'replace("batch_"' in line or "replace('batch_'" in line:
        print(f"Line {i}: {line.strip()}")
