import pandas as pd

def test_dedup_channel():
    # 1. 채널 데이터 테스트
    data = [
        {'Channel Name': 'A', 'Score 1': 100},
        {'Channel Name': 'B', 'Score 1': 200},
        {'Channel Name': 'A', 'Score 1': 150}, # 중복 (keep='last' 이므로 이것이 남아야 함)
    ]
    df = pd.DataFrame(data)
    target_type = 'channel'
    
    if target_type == 'channel':
        df = df.drop_duplicates(subset=['Channel Name'], keep='last')
    else:
        df = df.drop_duplicates(keep='last')
        
    print("=== Channel Dedup Result ===")
    print(df)
    assert len(df) == 2
    assert df.iloc[0]['Score 1'] == 200
    assert df.iloc[1]['Score 1'] == 150
    print("Channel Dedup Success!\n")

def test_dedup_video():
    # 2. 비디오 데이터 테스트 (Video ID가 있는 경우)
    data = [
        {'Video Title': 'V1', 'Channel Name': 'Ch1', 'Video ID': 'id1'},
        {'Video Title': 'V2', 'Channel Name': 'Ch2', 'Video ID': 'N/A'},
        {'Video Title': 'V3', 'Channel Name': 'Ch3', 'Video ID': 'N/A'},
        {'Video Title': 'V1_new', 'Channel Name': 'Ch1', 'Video ID': 'id1'}, # 중복 ID (id1이 남아야 함)
        {'Video Title': 'V2', 'Channel Name': 'Ch2', 'Video ID': 'N/A'}, # 중복 Title+Channel (N/A인 것 중 중복)
    ]
    df = pd.DataFrame(data)
    target_type = 'shorts'
    
    if target_type == 'channel':
        df = df.drop_duplicates(subset=['Channel Name'], keep='last')
    else:
        if 'Video ID' in df.columns:
            df_valid_id = df[df['Video ID'] != 'N/A']
            df_invalid_id = df[df['Video ID'] == 'N/A']
            
            df_valid_id = df_valid_id.drop_duplicates(subset=['Video ID'], keep='last')
            df_invalid_id = df_invalid_id.drop_duplicates(subset=['Video Title', 'Channel Name'], keep='last')
            
            df = pd.concat([df_valid_id, df_invalid_id], ignore_index=True)
        else:
            df = df.drop_duplicates(subset=['Video Title', 'Channel Name'], keep='last')
            
    print("=== Video Dedup Result ===")
    print(df)
    assert len(df) == 3
    # id1 중 유효한 것 중 뒤에 나온 V1_new가 남아야 함
    assert 'V1_new' in df['Video Title'].values
    assert 'V1' not in df['Video Title'].values
    # N/A 중 Ch2가 중복되므로 뒤에 나온 것이 남고, Ch3은 그대로 유지되어야 함
    assert len(df[df['Video ID'] == 'N/A']) == 2
    print("Video Dedup Success!\n")

if __name__ == '__main__':
    test_dedup_channel()
    test_dedup_video()
