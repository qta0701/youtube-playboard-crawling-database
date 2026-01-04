# 서버 재시작 가이드

**최종 업데이트:** 2025-12-10

---

## ⚠️ 중요: 코드 변경 후 서버 재시작 필수

Python 코드를 수정한 후에는 **반드시 서버 프로세스를 완전히 종료하고 재시작**해야 합니다.

### 문제 상황

코드 수정 후 서버가 여전히 이전 코드를 실행하는 경우:
- Python 모듈 캐싱으로 인해 변경사항이 반영되지 않음
- 여러 서버 프로세스가 동시에 실행 중일 가능성

---

## 🔄 올바른 서버 재시작 방법

### 1. 현재 실행 중인 서버 프로세스 확인

**Windows PowerShell:**
```powershell
Get-Process python | Where-Object {$_.MainWindowTitle -like "*dashboard*"}
```

**Windows 명령 프롬프트:**
```cmd
tasklist | findstr python
```

### 2. 모든 Python 프로세스 종료

**방법 1: Task Manager 사용**
1. `Ctrl + Shift + Esc` → Task Manager 열기
2. `Details` 탭 → `python.exe` 모든 항목 찾기
3. 우클릭 → `End task`

**방법 2: 명령줄 사용**
```powershell
# PowerShell (관리자 권한 필요)
Get-Process python | Stop-Process -Force

# 또는 특정 포트(5001) 사용 프로세스만 종료
netstat -ano | findstr :5001
taskkill /PID <PID번호> /F
```

### 3. 서버 재시작

**START_DASHBOARD.bat 실행:**
```cmd
cd "Y:\AI쇼츠\Shorts Transcripts Python\플레이보드 크롤링"
START_DASHBOARD.bat
```

**또는 직접 실행:**
```cmd
cd "Y:\AI쇼츠\Shorts Transcripts Python\플레이보드 크롤링"
python dashboard_app.py
```

### 4. 서버 정상 시작 확인

**로그 파일 확인:**
```powershell
# 최신 로그 파일 내용 확인
Get-Content "logs\log_START_DASHBOARD_*.log" -Tail 20
```

**예상 로그:**
```
[2025-12-10 XX:XX:XX] INFO - ============================================================
[2025-12-10 XX:XX:XX] INFO - YouTube DB Dashboard Starting...
[2025-12-10 XX:XX:XX] INFO - Port: 5001
[2025-12-10 XX:XX:XX] INFO - Database: output/db/youtube_data.db
[2025-12-10 XX:XX:XX] INFO - ============================================================
```

**브라우저 접속:**
```
http://localhost:5001
```

---

## 🐛 트러블슈팅

### 문제 1: "Address already in use" 에러

**증상:**
```
OSError: [WinError 10048] Only one usage of each socket address is normally permitted
```

**해결:**
1. 포트 5001을 사용 중인 프로세스 찾기:
```cmd
netstat -ano | findstr :5001
```

2. 해당 PID 프로세스 종료:
```cmd
taskkill /PID <PID번호> /F
```

### 문제 2: 코드 변경사항이 반영되지 않음

**확인 사항:**
1. 파일이 저장되었는지 확인 (VS Code: `Ctrl + S`)
2. 서버 프로세스가 완전히 종료되었는지 확인
3. 새로운 로그 파일이 생성되었는지 확인

**강제 재시작:**
```powershell
# 모든 Python 프로세스 강제 종료
Get-Process python | Stop-Process -Force

# 잠시 대기 (모듈 언로드)
Start-Sleep -Seconds 2

# 서버 재시작
cd "Y:\AI쇼츠\Shorts Transcripts Python\플레이보드 크롤링"
python dashboard_app.py
```

### 문제 3: 여러 서버가 동시 실행 중

**증상:**
- 새 로그 파일은 생성되지만 내용이 적음
- 이전 로그 파일에 새로운 요청 기록이 추가됨

**해결:**
1. **모든** Python 프로세스 종료
2. 포트 5001 사용 프로세스 확인 및 종료
3. 서버 재시작
4. 브라우저 캐시 삭제 후 새로고침 (`Ctrl + Shift + R`)

---

## ✅ 재시작 후 확인 체크리스트

- [ ] 새로운 로그 파일(`log_START_DASHBOARD_YYYYMMDD_HHMMSS.log`) 생성됨
- [ ] 로그에 "YouTube DB Dashboard Starting..." 메시지 있음
- [ ] 브라우저에서 `http://localhost:5001` 접속 가능
- [ ] 코드 변경사항이 로그에 반영됨 (예: 새로운 로그 메시지 확인)
- [ ] API 요청 시 새 로그 파일에 기록됨

---

## 📝 개선 #36, #37 적용 확인 방법

### 확인 1: 로깅 형식 변경

**변경 전:**
```
[Low-Cost Recovery] Retrieving channel ID via video: xxx
```

**변경 후:**
```
[Step 2: Low-Cost] Attempting recovery - channel_name: 'xxx'
[Low-Cost] Searching DB for videos with channel_name: 'xxx'
```

### 확인 2: High-Cost 복구 시도

채널 1개 선택 → High-Cost 체크박스 활성화 → 동기화 실행

**로그에서 확인:**
```
[Step 1: Zero-Cost] ✗ URL parsing failed
[Step 2: Low-Cost] Attempting recovery
[Low-Cost] ✗ No video data found in DB
[Step 3: High-Cost] Attempting search recovery
[High-Cost] Calling search.list API
```

### 확인 3: 개선 #37 (N/A 처리)

**로그에서 확인:**
```
[Batch Sync] Invalid channel_id detected: 'N/A', searching for actual ID...
[Batch Sync] ✓ Found actual channel_id from videos_rank: 'UCxxxxxx'
```

---

## 🚨 긴급 상황: 서버가 응답하지 않을 때

```powershell
# 1. 모든 관련 프로세스 강제 종료
taskkill /F /IM python.exe

# 2. 포트 확인 및 정리
netstat -ano | findstr :5001
# PID 확인 후
taskkill /PID <PID> /F

# 3. 잠시 대기
timeout /t 3

# 4. 서버 재시작
cd "Y:\AI쇼츠\Shorts Transcripts Python\플레이보드 크롤링"
START_DASHBOARD.bat
```

---

**참고**: 개발 중에는 코드 변경 후 항상 서버를 재시작하는 습관을 들이는 것이 좋습니다.
