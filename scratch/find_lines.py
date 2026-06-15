with open('app.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()

start_idx = None
end_idx = None

for idx, line in enumerate(lines):
    if "if start_btn:" in line and "crawl_result" not in line: # if start_btn: 라인
        start_idx = idx
        print(f"Start index candidate: {idx} (Line {idx+1}): {line.strip()}")
    # st.rerun()이 있는 finally 블록의 끝을 찾음
    if idx > 900 and "logger.removeHandler(streamlit_handler)" in line:
        # 그 다음 st.rerun()과 finally 블록의 끝을 찾기 위해 몇 줄 더 탐색
        for j in range(idx, idx+10):
            if "st.rerun()" in lines[j]:
                end_idx = j
                print(f"End index candidate: {j} (Line {j+1}): {lines[j].strip()}")
                break

print(f"\n최종 감지된 범위: Line {start_idx+1} ~ Line {end_idx+1}")
