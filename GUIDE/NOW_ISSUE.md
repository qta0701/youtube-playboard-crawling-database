# 현재 이슈 및 해결 상태

**최종 업데이트:** 2025-12-12 (13차 업데이트 - PLAN.md 전면 UI 개편 완료)

---

## 📋 문서 개요

이 문서는 프로젝트의 **현재 발생한 에러**, **해결된 이슈**, **알려진 제한사항**을 기록합니다.
과거 이슈는 별도 백업 파일(`NOW_ISSUE_backup_*.md`)에 보관되며, 본 문서는 현재 상태만 반영합니다.

---

## 🎉 최근 해결 완료 이슈

### ✅ [2025-12-12] PLAN.md 전면 UI 개편 완료 (Phase 1-5)

**목적**: 대시보드 UI/UX 전면 개편 - 스프레드시트 스타일 통일, 데이터 완전성 확보, 채널 관리 단순화

**Phase 1: 공통 UI 모듈 구현 (Spreadsheet Table System)**
- **CSS 추가** - [db_dashboard.html:541-555](../templates/db_dashboard.html#L541-L555):
  - `.top-scroll-wrapper`: 상단 스크롤바 래퍼
  - `.top-scroll-inner`: 더미 스크롤 영역
  - Sticky headers, 가로 스크롤, 컬럼 리사이징 지원
- **HTML 구조 변경**:
  - 크롤링 데이터 테이블: [db_dashboard.html:1240-1247](../templates/db_dashboard.html#L1240-L1247)
  - API 데이터 테이블: [db_dashboard.html:1660-1666](../templates/db_dashboard.html#L1660-L1666)
  - 채널 관리 테이블: [db_dashboard.html:1475-1481](../templates/db_dashboard.html#L1475-L1481)
- **JavaScript 동기화 함수**:
  - `syncTopScroll(tableId)` 범용화 - [db_dashboard.html:4764-4791](../templates/db_dashboard.html#L4764-L4791)
  - 모든 테이블 렌더링 후 자동 호출

**Phase 2: 크롤링 데이터 탭 개편**
- **Backend 로깅 강화** - [dashboard_app.py:1275-1280](../dashboard_app.py#L1275-L1280):
  - 첫 번째 row의 컬럼명 로깅으로 스키마 검증
- **Frontend 디버깅 추가** - [db_dashboard.html:3111-3118](../templates/db_dashboard.html#L3111-L3118):
  - `console.table(results[0])` 로 데이터 구조 확인
  - 데이터 타입, 총 row 수 로그 출력

**Phase 3: API 데이터 탭 개편**
- **Backend Deep Data 추가** - [dashboard_app.py:540-567](../dashboard_app.py#L540-L567):
  - `days_since_upload`: 업로드 후 경과 일수 계산
  - `view_sub_ratio`: 조회수/구독자수 비율 계산
  - `channel_subscriber_count`: 채널 구독자수 추가
- **Frontend 디버깅 추가** - [db_dashboard.html:3610-3617](../templates/db_dashboard.html#L3610-L3617):
  - API 비디오 데이터 구조 console.table 로 확인

**Phase 4: 채널 관리 탭 기능 축소**
- **UI 제거**:
  - Low-Cost/High-Cost 복구 설명 패널 제거 - [db_dashboard.html:1389](../templates/db_dashboard.html#L1389)
  - 동기화 옵션 패널 (Quota estimate) 제거
  - 동기화 관련 버튼 제거 (체크 채널 동기화, 미동기화 전체 동기화, 오래된 채널 동기화)
- **Stats 간소화** - [db_dashboard.html:1327-1337](../templates/db_dashboard.html#L1327-L1337):
  - 6개 카드 → 2개 카드 (전체 채널, 추출된 채널)
  - Sync 관련 stat 제거 (동기화율, Low-Cost 복구 가능, 미동기화, 업데이트 필요)
- **유지된 기능**:
  - 재생목록 기반 채널 추출
  - 크롤링 데이터 채널 추출
  - 채널 삭제 기능

**Phase 5: 디버깅 및 에러 처리 강화**
- Backend: 컬럼명 로깅으로 스키마 불일치 조기 발견
- Frontend: console.table로 데이터 구조 시각적 확인
- 모든 render 함수에 디버깅 그룹 추가

**효과**:
- ✅ 모든 탭의 테이블 UI 통일 (스프레드시트 스타일)
- ✅ DB 스키마의 모든 컬럼 표시 가능
- ✅ 채널 관리 탭 복잡도 대폭 감소
- ✅ 디버깅 용이성 향상

---

### ✅ [2025-12-12] PLAN.md Phase 3 - 스프레드시트형 테이블 뷰 CSS 추가 (이전 버전)

**목적**: 모든 데이터를 가로 스크롤 가능한 스프레드시트 형태로 표시

**추가된 CSS** - [db_dashboard.html:471-539](../templates/db_dashboard.html#L471-L539):
- `.spreadsheet-container`: 가로 스크롤 컨테이너
- `.spreadsheet-table`: 자동 너비 확장 테이블
- `.cell-with-thumb`: 썸네일 + 텍스트 통합 셀
- `.cell-thumb-img`: 영상 썸네일 (80x45px)
- `.cell-profile-img`: 채널 프로필 (40x40px)

**효과**:
- DB의 모든 컬럼 정보 표시 가능
- 가로 스크롤로 더 많은 데이터 확인
- 썸네일과 텍스트를 하나의 셀에 통합하여 가독성 향상

---

### ✅ [2025-12-12] API 탭 동기화된 채널 섹션 제거

**변경 사항**:
1. HTML 섹션 제거 - `syncedChannelsCarousel` div 제거
2. JavaScript 함수 제거:
   - `loadSyncedChannelCards()` 함수 삭제
   - `renderChannelCards()` 함수 삭제
3. 탭 전환 시 호출 제거 - [db_dashboard.html:1967-1969](../templates/db_dashboard.html#L1967-L1969)

**목적**:
- 불필요한 UI 복잡도 감소
- 채널 관리 탭과 기능 중복 제거
- 페이지 로드 성능 향상

---

### ✅ [2025-12-12] PLAN.md Phase 1 - API 데이터 탭 채널 카드 UX 개선

**문제**: 더블클릭 기능이 직관적이지 않아 사용자가 채널별 영상 필터링 기능을 인지하기 어려움

**해결**:

1. **`renderApiChannels()` 함수 개선** - [db_dashboard.html:3345-3376](../templates/db_dashboard.html#L3345-L3376)
   - 더블클릭 이벤트 제거
   - 명시적인 버튼 2개 추가: "쇼츠", "비디오"
   ```javascript
   <div class="card-actions" style="margin-top:10px; display:flex; gap:5px;">
       <button onclick="filterByChannel(channelId, title, 'shorts')">쇼츠</button>
       <button onclick="filterByChannel(channelId, title, 'videos')">비디오</button>
   </div>
   ```

2. **`filterByChannel()` 함수 개선** - [db_dashboard.html:3593-3618](../templates/db_dashboard.html#L3593-L3618)
   - 타입 파라미터 추가 (shorts/videos)
   - 지정된 타입 탭으로 자동 전환

**효과**:
- 숨겨진 기능 발견성 향상
- 버튼 클릭만으로 쇼츠/비디오 영상 즉시 필터링 가능
- 사용자 경험 개선

---

### ✅ [2025-12-12] PLAN.md Phase 2 - '동기화' → '추출 완료' 개념 재정의

**목적**: API Quota 사용 여부와 무관하게 Channel ID가 확보된 상태를 '추출 완료'로 표시

**Backend 수정**:

1. **`/api/channel_manager/list` 엔드포인트** - [dashboard_app.py:2149-2170](../dashboard_app.py#L2149-L2170)
   ```python
   CASE
       WHEN (ac.channel_id IS NOT NULL AND ac.channel_id != 'N/A')
            OR ac.last_synced_at IS NOT NULL
       THEN 1 ELSE 0
   END as is_extracted
   ```

2. **`/api/channels/synced_list` 엔드포인트** - [dashboard_app.py:470-499](../dashboard_app.py#L470-L499)
   - WHERE 조건 변경: `sync_status = 'synced'` → `(channel_id IS NOT NULL AND channel_id != 'N/A') OR last_synced_at IS NOT NULL`

**효과**:
- 채널 ID가 있으면 즉시 '추출 완료'로 인식
- 동기화 여부와 무관하게 데이터 유효성 기준으로 상태 표시
- 사용자 혼란 감소

---

### ✅ [2025-12-12] PLAN.md Phase 2 (이전) - API 데이터 탭 채널 테이블 뷰 구현

**변경 전**: 채널 탭에서 테이블 뷰 미지원 (토스트 알림만 표시)
**변경 후**: 채널 탭에서도 테이블 뷰 완전 지원

**구현 내용**:

1. **`renderApiChannelsTable()` 함수 추가** - [db_dashboard.html:3393-3456](../templates/db_dashboard.html#L3393-L3456)
   - 채널 전용 테이블 헤더 (번호, 채널 정보, 구독자 수, 영상 수, 총 조회수, 동기화 상태, 액션)
   - DocumentFragment 사용으로 DOM 렌더링 최적화
   - "📺 보기" 버튼으로 Channel Viewer 연결

2. **뷰 모드 전환 로직 수정** - [db_dashboard.html:3488-3491](../templates/db_dashboard.html#L3488-L3491)
   - 채널 탭 제한 로직 제거
   - 모든 서브탭에서 테이블/그리드 뷰 모두 지원

3. **`loadApiData()` 수정** - [db_dashboard.html:3332-3346](../templates/db_dashboard.html#L3332-L3346)
   ```javascript
   if (currentApiSubtype === 'channels') {
       if (apiViewMode === 'table') {
           renderApiChannelsTable(data.channels);  // 새로 추가
       } else {
           renderApiChannels(data.channels);
       }
   }
   ```

**효과**:
- 채널 목록을 테이블 형식으로 더 많은 정보 확인 가능
- 정렬된 데이터 보기 용이
- 채널별 액션 버튼 제공

---

### ✅ [2025-12-12] PLAN.md Phase 3 - 채널 동기화 상태 갱신 버그 수정 (Critical)

**증상**:
- 채널 관리 탭에서 채널 동기화 후 테이블이 업데이트되지 않음
- 동기화 완료해도 "미동기화" 상태로 계속 표시됨

**원인 분석**:
- **sync_status 값 불일치**: 백엔드에서 `sync_status = 'success'`로 저장
- **조회 조건 불일치**: 조회 시 `sync_status = 'synced'`로 필터링
- **캐시 문제**: 브라우저가 이전 응답을 캐시하여 새 데이터 미반영

**수정 내용**:

1. **sync_status 값 통일** - [dashboard_app.py:2581, 2596](../dashboard_app.py#L2581):
```python
# Before (잘못된 값)
sync_status = 'success'

# After (올바른 값)
sync_status = 'synced'
```

2. **Cache-Control 헤더 추가** - [dashboard_app.py:104-108](../dashboard_app.py#L104-L108):
```python
# PLAN.md Phase 3: 채널 관리 API에 Cache-Control 헤더 추가
if request.path.startswith('/api/channel_manager/'):
    response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '0'
```

3. **동기화 완료 후 1초 딜레이** - [db_dashboard.html:5582-5588](../templates/db_dashboard.html#L5582-L5588):
```javascript
// PLAN.md Phase 3: 1초 딜레이 후 목록 새로고침 (DB Commit 대기)
setTimeout(async () => {
    debugLog('ACTION', '동기화 완료 후 자동 새로고침 시작');
    await loadChannelManagerStatus();
    await loadChannelManagerList();
    debugLog('SUCCESS', '동기화 후 목록 자동 갱신 완료');
}, 1000);
```

**효과**:
- 채널 동기화 후 즉시 테이블에 "동기화됨" 상태로 표시
- 동기화 현황 통계에 정확한 수치 반영
- 브라우저 캐시로 인한 이전 데이터 표시 방지
- DB 커밋 완료 후 안정적인 데이터 갱신

---

### ✅ [2025-12-12] 디버그 로깅 시스템 개선

**변경 사항**:

1. **메모리 최적화** - 최대 로그 수 1000 → 500개로 축소
2. **VERBOSE 모드 추가** - 상세 로그와 핵심 로그 분리
   ```javascript
   let DEBUG_VERBOSE = false;  // true면 상세 로그 출력

   function debugLog(category, message, data = null, verbose = false) {
       if (verbose && !DEBUG_VERBOSE) return;
       // ...
   }

   // 콘솔에서 토글: toggleDebugVerbose()
   ```

3. **새로운 카테고리 추가**:
   - `API_START`, `API_END` - API 호출 시작/완료
   - `WARN` - 경고
   - `CHANNEL_VIEWER` - Channel Viewer 관련
   - `VIEW_MODE` - 뷰 모드 전환
   - `BUTTON` - 버튼 클릭 이벤트

4. **Channel Viewer 함수에 상세 로깅 추가**:
   - `openChannelViewer()` - 채널 뷰어 열기 로그
   - `closeChannelViewer()` - 채널 뷰어 닫기 로그
   - `loadChannelViewerHeader()` - 헤더 로드 시작/완료 로그

5. **새로고침 버튼 로깅 강화**:
   ```javascript
   async function refreshChannelManagerList() {
       debugLog('BUTTON', '새로고침 버튼 클릭');
       const startTime = Date.now();
       // ...
       const elapsed = Date.now() - startTime;
       debugLog('SUCCESS', `채널 목록 새로고침 완료`, { elapsedMs: elapsed });
   }
   ```

**효과**:
- 개발자 콘솔에서 `toggleDebugVerbose()` 호출로 상세 로그 토글 가능
- 핵심 이벤트만 기본 출력으로 로그 가독성 향상
- API 호출 시간 측정으로 성능 분석 가능

---

### ✅ [2025-12-11] 대시보드 무한 로딩 및 탭 미작동 버그 수정 (Critical)

**증상**:
- `http://localhost:5001/` 접속 시 로딩 스피너가 무한 반복
- 탭 클릭이 작동하지 않음
- 서버 로그에 API 요청이 전혀 기록되지 않음

**원인 분석**:
- 서버 로그에서 API 요청이 없는 것은 **클라이언트 측 JavaScript 오류**를 의미
- [db_dashboard.html:3491](../templates/db_dashboard.html#L3491) - `try:` (Python 문법)가 JavaScript 코드에 삽입됨
- JavaScript 구문 오류로 인해 전체 스크립트 실행이 중단됨

**수정 내용**:

1. **JavaScript 문법 오류 수정** - [db_dashboard.html:3491](../templates/db_dashboard.html#L3491)
   ```javascript
   // Before (잘못된 Python 문법)
   try:
       const res = await fetch(...);

   // After (올바른 JavaScript 문법)
   try {
       const res = await fetch(...);
   ```

2. **DOMContentLoaded 안전 초기화 구현** - [db_dashboard.html:1727-1767](../templates/db_dashboard.html#L1727-L1767)
   ```javascript
   document.addEventListener('DOMContentLoaded', async () => {
       try {
           // 1. 탭 이벤트 리스너 먼저 설정 (데이터 로딩 실패해도 탭 전환 가능)
           setupTabs();

           // 2. Safety Timeout: 8초 후 스피너 강제 제거
           setTimeout(() => {
               // 로딩 지연 시 경고 메시지 표시
           }, 8000);

           // 3. 데이터 로드 (병렬, 개별 에러 핸들링)
           await Promise.all([
               loadStats().catch(e => console.error('[Init] loadStats 에러:', e)),
               loadQuota().catch(e => console.error('[Init] loadQuota 에러:', e)),
               // ... 기타 로드 함수들
           ]);

       } catch (criticalError) {
           console.error('[CRITICAL] 대시보드 초기화 오류:', criticalError);
           showToast('대시보드 초기화 중 오류가 발생했습니다', 'error');
       }
   });
   ```

3. **Backend Request Timing Middleware 추가** - [dashboard_app.py:83-103](../dashboard_app.py#L83-L103)
   ```python
   @app.before_request
   def start_timer():
       g.start = time.time()

   @app.after_request
   def log_request(response):
       if hasattr(g, 'start'):
           diff = time.time() - g.start
           if diff > 1.0:
               logger.warning(f"SLOW REQUEST: {request.path} took {diff:.4f}s")
       return response
   ```

**효과**:
- 탭 전환이 데이터 로딩과 무관하게 즉시 작동
- 개별 API 호출 실패가 전체 초기화를 중단하지 않음
- 8초 Safety Timeout으로 무한 로딩 방지
- 느린 API 요청 자동 감지 및 로깅

---

### ✅ [2025-12-11] API Data Tab UX 개선 - Channel Viewer 탭 추가 및 더블클릭 → 버튼 변경

**문제**:
- 채널 카드에서 더블클릭으로 채널 영상 필터링하는 기능이 직관적이지 않음
- 사용자가 숨겨진 기능을 인지하기 어려움

**해결 (PLAN.md 2.1)**:

1. **채널 카드 UI 개선** - [db_dashboard.html:3487-3514](../templates/db_dashboard.html#L3487-L3514)
   - 더블클릭 이벤트 제거
   - "📺 채널 영상 보기" 버튼 추가 (카드 하단에 명시적 버튼)
   ```html
   <button onclick="openChannelViewer('${ch.channel_id}', '${escapedTitle}')"
           style="width:100%;padding:8px 12px;background:linear-gradient(135deg, #667eea, #764ba2);...">
       📺 채널 영상 보기
   </button>
   ```

2. **Channel Viewer 전용 탭 생성** - [db_dashboard.html:1618-1711](../templates/db_dashboard.html#L1618-L1711)
   - 별도의 "채널별 영상" 탭 추가 (클릭 시 표시됨)
   - 채널 헤더: 프로필 이미지, 채널명, 구독자 수, 수집된 영상 수
   - 필터/정렬 옵션: 타입(전체/쇼츠/비디오), 정렬(최신순/조회수순/좋아요순)
   - 그리드/테이블 뷰 모드 전환
   - "← API 데이터로 돌아가기" 버튼

3. **JavaScript 함수 추가** - [db_dashboard.html:3625-3855](../templates/db_dashboard.html#L3625-L3855)
   - `openChannelViewer(channelId, channelName)`: 채널 뷰어 열기
   - `closeChannelViewer()`: API 데이터 탭으로 복귀
   - `loadChannelViewerHeader()`: 채널 정보 로드
   - `loadChannelViewerVideos()`: 채널 영상 로드
   - `renderCvGridView()`, `renderCvTableView()`: 뷰 렌더링
   - `setCvViewMode()`: 뷰 모드 전환
   - `loadChannelViewerPage()`: 페이지네이션

**효과**:
- 명시적인 버튼으로 기능 발견성 향상
- 전용 탭에서 채널별 영상 분석에 집중 가능
- 뒤로가기 버튼으로 쉽게 API 데이터 탭 복귀

---

### ✅ [2025-12-11] 채널 매니저 - 목록 새로고침 버튼 및 자동 갱신 개선

**문제**:
- 채널 추출 후 테이블이 자동으로 갱신되지 않는 경우 발생
- 사용자가 목록을 수동으로 새로고침할 방법이 없음

**해결 (PLAN.md 2.2)**:

1. **새로고침 버튼 추가** - [db_dashboard.html:1275-1278](../templates/db_dashboard.html#L1275-L1278)
   ```html
   <button class="btn btn-sm btn-secondary" onclick="refreshChannelManagerList()" id="btnRefreshCMList" title="채널 목록 새로고침" style="background:#2196f3;color:#fff;">
       <i class="fas fa-sync-alt" id="refreshCMIcon"></i> 새로고침
   </button>
   ```

2. **새로고침 함수** - [db_dashboard.html:4975-4999](../templates/db_dashboard.html#L4975-L4999)
   - 스핀 애니메이션 추가 (fa-spin)
   - 채널 목록 및 통계 동시 갱신
   - 성공 시 토스트 메시지 표시

3. **채널 추출 후 자동 갱신 딜레이** - [db_dashboard.html:6107-6113](../templates/db_dashboard.html#L6107-L6113)
   - DB 커밋 대기를 위한 500ms 딜레이 추가
   ```javascript
   setTimeout(async () => {
       await loadChannelManagerStatus();
       await loadChannelManagerList();
   }, 500);
   ```

**효과**:
- 언제든 수동으로 목록 갱신 가능
- 채널 추출 후 안정적인 자동 갱신
- 스핀 애니메이션으로 갱신 진행 상태 표시

---

### ✅ [2025-12-11] 채널 동기화 - /api/channel_manager/sync/batch 404 에러 수정

**문제**:
```
[ERROR] batch_sync API HTTP 오류
  Data: {
  "status": 404,
  "statusText": "NOT FOUND"
}
```
- 채널 관리 탭에서 채널 동기화 실행 시 404 에러 발생
- 프론트엔드에서 잘못된 API 경로 호출

**원인**:
- 프론트엔드: `/api/channel_manager/batch_sync` 호출
- 백엔드 실제 경로: `/api/channel_manager/sync/batch`

**해결 (2025-12-11)**:
1. [templates/db_dashboard.html](../templates/db_dashboard.html) - 모든 `/api/channel_manager/batch_sync`를 `/api/channel_manager/sync/batch`로 수정
2. [dashboard_app.py:2606-2637](../dashboard_app.py#L2606-L2637) - SSE 진행 상태 엔드포인트 추가 (`/api/channel_manager/sync/batch_progress`)

**추가된 SSE 엔드포인트**:
```python
@app.route('/api/channel_manager/sync/batch_progress')
def api_channel_manager_sync_batch_progress():
    """SSE endpoint for batch sync progress updates"""
    def generate():
        while True:
            if batch_sync_progress['is_running']:
                data = {...}  # 진행 상황 데이터
                yield f"data: {json.dumps(data)}\n\n"
                time.sleep(0.5)
            else:
                yield f"data: {{\"status\": \"completed\"}}\n\n"
                break
    return Response(generate(), mimetype='text/event-stream')
```

---

### ✅ [2025-12-11] 채널 관리 탭 - /api/channel_manager/status 404 에러 수정

**문제**: 채널 관리 탭 초기화 시 동기화 현황 통계 로드 실패

**해결**: [dashboard_app.py:2136-2214](../dashboard_app.py#L2136-L2214) - `/api/channel_manager/status` API 엔드포인트 신규 추가

---

## 🚀 적용된 개선사항

### ✅ [2025-12-11] 크롤링 데이터 테이블 - 영상 썸네일 표시 기능 추가

**추가된 기능**:
- 비디오 랭킹, 쇼츠 랭킹 테이블의 제목 컬럼에 영상 썸네일 이미지 표시
- 썸네일 클릭 시 YouTube 영상 새 탭에서 열기

**크롤러 개선** - [modules/crawler_selenium.py:657-695](../modules/crawler_selenium.py#L657-L695):
```python
# 4-1. div.thumb의 background-image에서 추출 (Playboard 방식)
thumb_div = row.select_one('div.thumb')
if thumb_div:
    bg_url = thumb_div.get('data-background-image')
    if not bg_url:
        # style 속성에서 background-image 추출
        style = thumb_div.get('style', '')
        bg_match = re.search(r'background-image:\s*url\(["\']?([^"\'()]+)["\']?\)', style)
        if bg_match:
            bg_url = bg_match.group(1)

# 4-2. img 태그에서 추출 (fallback)
# 4-3. video_id로 YouTube 썸네일 URL 생성 (최종 fallback)
if thumbnail == 'N/A' and video_id:
    thumbnail = f'https://img.youtube.com/vi/{video_id}/mqdefault.jpg'
```

**프론트엔드 UI** - [templates/db_dashboard.html:2631-2646](../templates/db_dashboard.html#L2631-L2646):
```javascript
// 제목 + 썸네일 병합 셀
const titleCell = `
    <div style="display:flex; align-items:center; gap:10px;">
        <img src="${thumbUrl}"
             style="width:80px; height:45px; border-radius:4px; object-fit:cover; cursor:pointer;"
             onclick="window.open('https://youtube.com/watch?v=${r.video_id}', '_blank')"
             loading="lazy">
        <a href="...">제목</a>
    </div>
`;
```

**썸네일 추출 우선순위**:
1. `div.thumb`의 `data-background-image` 속성
2. `div.thumb`의 `style` 속성 내 `background-image` URL
3. `img` 태그의 `data-src`, `data-original`, `src` 속성
4. `video_id`로 YouTube 썸네일 URL 자동 생성 (`https://img.youtube.com/vi/{video_id}/mqdefault.jpg`)

---

### ✅ [2025-12-11] UI/UX 개선 - 채널 관리 탭 편의 기능

1. **통계 새로고침 버튼** - 동기화 현황 수동 새로고침
2. **통계 카드 클릭 필터링** - 상태별 채널 필터링

---

### ✅ [2025-12-11] 디버그 로그 시스템 강화

1. **HTTP 상태 체크** - `res.ok` 확인 후 처리
2. **JSON 파싱 예외 처리** - try-catch로 파싱 실패 감지
3. **batch_sync API 로그** - 요청/응답/SSE 에러 핸들링

---

## 📊 채널 관리 API 엔드포인트

| Endpoint | Method | 기능 |
|----------|--------|------|
| `/api/channel_manager/status` | GET | 동기화 현황 통계 조회 |
| `/api/channel_manager/list` | GET | 채널 목록 조회 |
| `/api/channel_manager/sync/batch` | POST | 채널 일괄 동기화 |
| `/api/channel_manager/sync/batch_progress` | GET (SSE) | 동기화 진행 상황 스트리밍 |
| `/api/channel_manager/delete_all` | POST | 전체 채널 삭제 |

---

## 📊 로그 시스템 정보

### 로그 파일 구조
- **경로**: `logs/` 폴더
- **파일명 형식**:
  - Dashboard: `log_START_DASHBOARD_YYYYMMDD_HHMMSS.log`
  - Crawler: `log_YYYYMMDD_HHMMSS.log`
  - Browser Debug: `debug_log_YYYY-MM-DDTHH-MM-SS-MMMZ.txt`

### 로그 레벨
| 레벨 | 용도 | 출력 위치 |
|------|------|----------|
| **DEBUG** | 상세 로그 | 파일만 |
| **INFO** | 일반 정보 | 콘솔 + 파일 |
| **WARNING** | 경고 | 콘솔 + 파일 |
| **ERROR** | 에러 | 콘솔 + 파일 |

---

## 🔧 현재 활성 이슈

현재 활성화된 이슈 없음. 모든 주요 기능 정상 작동 중.

---

## ⚠️ 알려진 제한사항

### 1. YouTube API Quota 제한
- **일일 한도**: 10,000 units
- **주요 비용**: channels.list (1), videos.list (1), search.list (100)

### 2. Selenium 크롤링 제한
- 로그인 없이 ~22개, 로그인 후 최대 200개 수집 가능

### 3. 패키지 의존성
- Flask, google-api-python-client, youtube-transcript-api, selenium, webdriver-manager
- **자동 설치**: START_DASHBOARD.bat에서 자동 체크 및 설치

---

## 📖 참고 문서

- [NOW_LOGIC.md](NOW_LOGIC.md) - 시스템 로직 및 구조
- [NOW_DB_SCHEMA.md](NOW_DB_SCHEMA.md) - 데이터베이스 스키마
- [SERVER_RESTART_GUIDE.md](SERVER_RESTART_GUIDE.md) - 서버 재시작 가이드

---

## 🔧 최신 적용 사항 (2025-12-11 - PLAN.md 4차 적용: API Data Tab 성능 & UX 개선)

### ✅ 1. UI 명칭 변경 (PLAN.md 2.1.A)
**변경 사항**: 뷰 모드 버튼 텍스트 "그리드" → "썸네일"로 변경

**구현**:
- [db_dashboard.html:1563](../templates/db_dashboard.html#L1563) - 버튼 텍스트 수정
  ```html
  <i class="fas fa-th-large"></i> 썸네일
  ```

**효과**:
- 사용자에게 더 명확한 의미 전달 (썸네일 중심의 카드 뷰)

### ✅ 2. 동기화된 채널 카드 Carousel (PLAN.md 2.1.A)
**목적**: API Data 탭에서 동기화된 채널을 가로 스크롤 카드 형태로 시각화하고 빠른 필터링 지원

**구현**:
- [dashboard_app.py:440-479](../dashboard_app.py#L440-L479) - `/api/channels/synced_list` 엔드포인트 추가
  ```python
  @app.route('/api/channels/synced_list')
  def api_channels_synced_list():
      # 동기화된 채널만 가져오기 (sync_status = 'synced')
      # 수집된 영상 수 포함
  ```

- [db_dashboard.html:1549-1560](../templates/db_dashboard.html#L1549-L1560) - 채널 카드 Carousel UI 추가
  - 가로 스크롤 가능한 컨테이너
  - 동기화된 채널 수 표시

- [db_dashboard.html:3412-3469](../templates/db_dashboard.html#L3412-L3469) - 채널 카드 로딩 & 렌더링 함수
  ```javascript
  async function loadSyncedChannelCards()  // API 호출
  function renderChannelCards(channels)    // 카드 렌더링
  ```

- [db_dashboard.html:1755](../templates/db_dashboard.html#L1755) - API 탭 진입 시 자동 로드
  ```javascript
  if (currentTab === 'api') {
      loadApiData();
      loadSyncedChannelCards();  // 채널 카드 로드
  }
  ```

**카드 디자인**:
- 채널 썸네일 (50px 원형)
- 채널명, 구독자 수
- 수집된 영상 개수 표시
- "이동 →" 버튼 (YouTube 채널 페이지로 외부 링크)
- **클릭 시**: `filterByChannel()` 호출하여 해당 채널의 영상만 필터링

**효과**:
- 동기화된 채널을 한눈에 확인 가능
- 카드 클릭 한 번으로 특정 채널의 영상 필터링
- 빠른 채널 간 전환 및 탐색

### ✅ 3. 테이블 뷰 렌더링 최적화 (PLAN.md 2.1.B)
**목적**: 대량 데이터 렌더링 시 성능 개선 (DOM 조작 비용 감소)

**구현**:
- [db_dashboard.html:3272-3314](../templates/db_dashboard.html#L3272-L3314) - `renderApiVideosTable()` 함수 수정
  ```javascript
  // Before: innerHTML로 모든 행을 한 번에 추가 (비효율)
  tbody.innerHTML = videos.map(...).join('');

  // After: DocumentFragment 사용 (PLAN.md 2.1.B)
  const fragment = document.createDocumentFragment();
  videos.forEach(v => {
      const tr = document.createElement('tr');
      tr.innerHTML = `...`;
      fragment.appendChild(tr);
  });
  tbody.appendChild(fragment);  // 한 번에 DOM에 추가
  ```

**효과**:
- **Reflow 최소화**: DOM에 한 번만 접근하여 성능 향상
- 수백 개 행 렌더링 시 체감 속도 개선
- 브라우저 부하 감소

### ✅ 4. 채널 카드 필터링 로직 강화 (PLAN.md 2.1.C)
**목적**: 채널 카드 클릭 시 해당 채널의 영상만 즉시 필터링

**구현**:
- [db_dashboard.html:3444](../templates/db_dashboard.html#L3444) - 채널 카드에 클릭 이벤트 연결
  ```javascript
  <div class="channel-card" onclick="filterByChannel('${ch.channel_id}', '${ch.title}')">
  ```

- 기존 `filterByChannel()` 함수 활용 (이전 세션에서 구현됨)
  - 채널 ID로 영상 필터링
  - 쇼츠 탭으로 자동 전환
  - 필터 상태 UI 표시

**효과**:
- 채널 카드 → 영상 리스트 필터링까지 원클릭
- 직관적인 UX
- 채널별 콘텐츠 분석 편의성 증대

---

## 🔧 이전 적용 사항 (2025-12-11 - PLAN.md 3차 적용: API Data Tab 개선)

### ✅ 1. API Data Tab - View Mode Toggle (PLAN.md 2.1)
**목적**: 사용자가 영상 데이터를 그리드 또는 테이블 형식으로 볼 수 있도록 선택 가능

**구현**:
- [db_dashboard.html:1538-1584](../templates/db_dashboard.html#L1538-L1584) - UI 컨트롤 추가
  - 그리드/테이블 전환 버튼 추가
  - 테이블 뷰 컨테이너 생성 (썸네일, 제목, 채널, 조회수, 좋아요, 게시일, 타입 컬럼)
  - 그리드 뷰 컨테이너 분리
- [db_dashboard.html:1694-1695](../templates/db_dashboard.html#L1694-L1695) - 상태 변수 추가
  ```javascript
  let apiViewMode = 'grid';  // 기본값: 그리드
  ```
- [db_dashboard.html:3179-3247](../templates/db_dashboard.html#L3179-L3247) - View Mode 함수 구현
  - `setApiViewMode(mode)` - 뷰 모드 전환 및 UI 업데이트
  - `renderApiVideosTable(videos)` - 테이블 형식 렌더링 함수
- [db_dashboard.html:3088-3145](../templates/db_dashboard.html#L3088-L3145) - `loadApiData()` 수정
  - 뷰 모드에 따라 적절한 렌더링 함수 호출
  - 로딩/에러 메시지도 뷰 모드에 맞게 표시

**효과**:
- 사용자 선호도에 따라 그리드 또는 테이블 뷰 선택 가능
- 테이블 뷰에서 더 많은 정보를 한눈에 확인 가능
- 채널 탭은 항상 그리드 뷰 유지

### ✅ 2. API Channel Filtering - 채널별 영상 필터링 (PLAN.md 2.2)
**목적**: 특정 채널의 영상만 필터링하여 보기

**구현**:
- [db_dashboard.html:1461-1470](../templates/db_dashboard.html#L1461-L1470) - 채널 필터 UI 섹션 추가
  - 선택한 채널 이름 표시
  - 필터 해제 버튼
- [db_dashboard.html:3164-3187](../templates/db_dashboard.html#L3164-L3187) - 채널 카드에 더블클릭 기능 추가
  ```javascript
  ondblclick="event.stopPropagation(); filterByChannel('${ch.channel_id}', '${ch.title}')"
  ```
  - 클릭: 채널 상세 정보 모달
  - 더블클릭: 해당 채널 영상 필터링
- [db_dashboard.html:3326-3368](../templates/db_dashboard.html#L3326-L3368) - 필터링 함수 구현
  - `filterByChannel(channelId, channelName)` - 채널 필터 활성화, 쇼츠 탭 자동 전환
  - `clearChannelFilter()` - 채널 필터 해제
- [db_dashboard.html:3110](../templates/db_dashboard.html#L3110) - API 요청에 채널 ID 파라미터 추가
  ```javascript
  if (apiFilters.channelId) url += `&channel_id=${apiFilters.channelId}`;
  ```
- [dashboard_app.py:447, 494-496](../dashboard_app.py#L447) - 백엔드에서 이미 지원 중 ✅

**효과**:
- 채널 카드 더블클릭으로 해당 채널의 쇼츠만 즉시 확인 가능
- 필터 상태를 명확하게 UI에 표시
- 편리한 채널별 콘텐츠 분석 가능

---

## 🔧 이전 적용 사항 (2025-12-11 - PLAN.md 2차 적용: 디버깅 강화)

### ✅ 1. 채널 매니저 - 동기화 후 자동 갱신 개선
**문제**: 채널 동기화 완료 후 테이블이 자동으로 갱신되지 않거나 캐시된 데이터가 표시됨

**해결**:
- [db_dashboard.html:4309](../templates/db_dashboard.html#L4309) - 캐시 방지 타임스탬프 추가
  ```javascript
  let url = `/api/channel_manager/list?...&_=${Date.now()}`;
  ```
- 브라우저 캐시를 우회하여 항상 최신 데이터를 가져옴

### ✅ 2. 프론트엔드 디버그 로깅 표준화 (PLAN.md 3.1)
**목적**: 각 탭의 테이블 렌더링 상태를 쉽게 디버그

**구현**:
- [db_dashboard.html:2624-2718](../templates/db_dashboard.html#L2624-L2718) - 크롤링 데이터 탭 렌더링 로그
  ```javascript
  console.group('🎬 Crawl Data Table Render');
  console.log('Sample Data (First Row)'); // 썸네일 URL 확인
  console.groupEnd();
  ```
- [db_dashboard.html:4373-4522](../templates/db_dashboard.html#L4373-L4522) - 채널 관리 탭 렌더링 로그
  ```javascript
  console.group('📺 Channel Manager Table Render');
  console.log('Sample Channel Data'); // discovery_video_id 확인
  console.groupEnd();
  ```

**효과**:
- 렌더링 시작/종료 시간 추적
- 첫 번째 행 데이터 샘플 자동 출력
- 썸네일 URL, 채널 프로필 URL 등 핵심 필드 값 확인 가능

### ✅ 3. 백엔드 로그 최적화 (PLAN.md 3.2)
**목적**: API 응답 데이터의 실제 내용 확인

**구현**:
- [dashboard_app.py:1276-1284](../dashboard_app.py#L1276-L1284) - `/api/crawl_data` 엔드포인트
  ```python
  logger.debug(f"[Sample Row] video_id={...}, thumbnail_url={...}, channel_profile_url={...}")
  ```
- [dashboard_app.py:2134-2141](../dashboard_app.py#L2134-L2141) - `/api/channel_manager/list` 엔드포인트
  ```python
  logger.debug(f"[Sample Channel] channel_id={...}, discovery_video_id={...}")
  ```

**효과**:
- 첫 번째 행의 실제 데이터 값 로그 출력
- 썸네일 URL이 올바르게 전달되는지 확인 가능
- discovery_video_id가 정상적으로 저장/조회되는지 추적

---

## 📝 이전 적용 사항 (2025-12-11 - 1차 PLAN.md 전면 적용)

## ✅ 1. Critical Bug Fixes
- ✅ [dashboard_app.py](../dashboard_app.py#L10) `import random` 누락 수정 (PLAN.md 1.1)
- ✅ [youtube_manager.py:716-725](../modules/youtube_manager.py#L716-L725) `self.db` 속성 에러 수정 - DatabaseHandler 로컬 인스턴스 사용 (PLAN.md 1.2)

## ✅ 2. UI/UX 개선 - 썸네일 분리 표시
- ✅ [dashboard_app.py](../dashboard_app.py#L1024-L1144) `/api/crawl_data` 쿼리 개선 - api_channels 테이블 LEFT JOIN 추가 (PLAN.md 2.1)
  - 비디오 랭킹/쇼츠 랭킹 테이블에서 `channel_profile_url` 컬럼 제공
- ✅ [db_dashboard.html:2624-2679](../templates/db_dashboard.html#L2624-L2679) 썸네일 배치 수정 (PLAN.md 2.2)
  - **제목 컬럼**: 비디오 썸네일 표시 (`thumbnail_url` 또는 `https://img.youtube.com/vi/{video_id}/mqdefault.jpg`)
  - **채널 컬럼**: 채널 프로필 이미지 + 채널명 + 구독자수 표시 (`channel_profile_url` from api_channels JOIN)

## ✅ 3. Playlist-Driven Channel Extraction 강화
- ✅ [database.py:345-347](../modules/database.py#L345-L347) `api_channels` 테이블 컬럼 추가 (PLAN.md 3.2)
  - `discovery_video_id TEXT` - 채널 발견에 사용된 영상 ID
  - `discovery_video_url TEXT` - 채널 발견에 사용된 영상 URL
- ✅ [database.py:1622-1670](../modules/database.py#L1622-L1670) `upsert_channel_from_playlist()` 메서드 업데이트 (PLAN.md 3.2)
  - discovery_video_id, discovery_video_url 파라미터 추가
- ✅ [youtube_manager.py:987-1139](../modules/youtube_manager.py#L987-L1139) `extract_channels_from_playlist_robust()` 메서드 구현 (PLAN.md 3.3)
  - **Strategy A (Fast)**: `videoOwnerChannelId` 활용
  - **Strategy B (Robust Fallback)**: video_id → videos.list API → channelId
  - 누락된 채널 정보 videos.list로 보완 (Cost: +1 quota per 50 videos)
- ✅ [db_dashboard.html:1422-1425](../templates/db_dashboard.html#L1422-L1425) 채널 목록 UI 업데이트 (PLAN.md 3.4)
  - "출처 영상 (ID)" 컬럼 추가
  - discovery_video_id → YouTube 링크로 표시

## ✅ 4. Bot Detection Avoidance
- ✅ [utils.py:421-463](../modules/utils.py#L421-L463) `get_random_headers()` 함수 추가 (PLAN.md 4.1)
  - 8가지 User-Agent 랜덤 순환 (Chrome, Firefox, Safari, Edge)
  - Accept-Language, Referer 등 헤더 완비
- ✅ [dashboard_app.py:2556-2559](../dashboard_app.py#L2556-L2559) batch_sync 딜레이 범위 조정 (PLAN.md 4.2)
  - 기존: `random.uniform(1.0, 3.0)`
  - 변경: `random.uniform(1.5, 3.5)` - 불규칙한 간격으로 봇 탐지 회피

---

**이전 변경사항**:
- /api/channel_manager/sync/batch 404 에러 수정 (API 경로 통일)
- SSE 진행 상태 엔드포인트 추가
- 크롤링 데이터 테이블 영상 썸네일 표시 기능 추가
