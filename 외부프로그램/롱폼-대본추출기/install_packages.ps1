# PowerShell 스크립트 - 패키지 설치
# UTF-8 지원으로 한글 표시 문제 없음

Write-Host "=" * 50 -ForegroundColor Cyan
Write-Host "YouTube Shorts 대본 추출기 - 패키지 설치" -ForegroundColor Yellow
Write-Host "=" * 50 -ForegroundColor Cyan
Write-Host ""

# requirements.txt 파일 존재 확인
if (Test-Path "requirements.txt") {
    Write-Host "✓ requirements.txt 파일을 찾았습니다." -ForegroundColor Green
    Write-Host ""
    
    Write-Host "필요한 패키지들을 설치합니다..." -ForegroundColor Yellow
    Write-Host ""
    
    # pip install 실행
    try {
        & pip install -r requirements.txt
        
        if ($LASTEXITCODE -eq 0) {
            Write-Host ""
            Write-Host "✓ 모든 패키지가 성공적으로 설치되었습니다!" -ForegroundColor Green
        } else {
            Write-Host ""
            Write-Host "✗ 패키지 설치 중 오류가 발생했습니다." -ForegroundColor Red
        }
    }
    catch {
        Write-Host ""
        Write-Host "✗ 설치 중 예외가 발생했습니다: $($_.Exception.Message)" -ForegroundColor Red
    }
} else {
    Write-Host "✗ requirements.txt 파일을 찾을 수 없습니다." -ForegroundColor Red
    Write-Host "현재 디렉토리에 requirements.txt 파일이 있는지 확인해주세요." -ForegroundColor Yellow
}

Write-Host ""
Write-Host "=" * 50 -ForegroundColor Cyan
Write-Host "아무 키나 누르면 종료됩니다..." -ForegroundColor Yellow
$null = $Host.UI.RawUI.ReadKey("NoEcho,IncludeKeyDown")