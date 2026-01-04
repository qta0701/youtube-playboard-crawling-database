/**
 * 클립보드 복사 및 유틸리티 함수
 */

/**
 * 테이블 데이터를 엑셀용 탭 구분 텍스트로 변환하여 클립보드에 복사
 * @param {string} tableId - 테이블 element ID
 */
function copyTableToClipboard(tableId) {
    const table = document.getElementById(tableId);
    if (!table) {
        alert('테이블을 찾을 수 없습니다');
        return;
    }

    let text = '';
    const rows = table.querySelectorAll('tr');

    rows.forEach((row, rowIndex) => {
        const cells = row.querySelectorAll('th, td');
        const rowData = [];

        cells.forEach(cell => {
            // 링크가 있으면 텍스트만 추출
            let cellText = cell.textContent.trim();
            rowData.push(cellText);
        });

        text += rowData.join('\t') + '\n';
    });

    copyToClipboard(text);
}

/**
 * JSON 데이터를 엑셀용 탭 구분 텍스트로 변환하여 클립보드에 복사
 * @param {Array} data - JSON 데이터 배열
 */
function copyDataToClipboard(data) {
    if (!data || data.length === 0) {
        alert('복사할 데이터가 없습니다');
        return;
    }

    // 헤더 생성
    const headers = Object.keys(data[0]);
    let text = headers.join('\t') + '\n';

    // 데이터 행 생성
    data.forEach(row => {
        const rowData = headers.map(header => {
            let value = row[header] || '';
            // 쉼표나 탭이 포함된 경우 따옴표로 감싸기
            if (String(value).includes('\t') || String(value).includes(',')) {
                value = `"${value}"`;
            }
            return value;
        });
        text += rowData.join('\t') + '\n';
    });

    copyToClipboard(text);
}

/**
 * 텍스트를 클립보드에 복사
 * @param {string} text - 복사할 텍스트
 */
function copyToClipboard(text) {
    if (navigator.clipboard && window.isSecureContext) {
        // Navigator clipboard API 사용 (HTTPS 환경)
        navigator.clipboard.writeText(text).then(() => {
            showToast('클립보드에 복사되었습니다!', 'success');
        }).catch(err => {
            console.error('클립보드 복사 실패:', err);
            fallbackCopyToClipboard(text);
        });
    } else {
        // Fallback 방법
        fallbackCopyToClipboard(text);
    }
}

/**
 * 클립보드 복사 fallback 메서드 (HTTP 환경용)
 * @param {string} text - 복사할 텍스트
 */
function fallbackCopyToClipboard(text) {
    const textArea = document.createElement('textarea');
    textArea.value = text;
    textArea.style.position = 'fixed';
    textArea.style.left = '-999999px';
    textArea.style.top = '-999999px';
    document.body.appendChild(textArea);
    textArea.focus();
    textArea.select();

    try {
        const successful = document.execCommand('copy');
        if (successful) {
            showToast('클립보드에 복사되었습니다!', 'success');
        } else {
            showToast('클립보드 복사 실패', 'error');
        }
    } catch (err) {
        console.error('Fallback 복사 실패:', err);
        showToast('클립보드 복사 실패', 'error');
    }

    document.body.removeChild(textArea);
}

/**
 * 토스트 메시지 표시
 * @param {string} message - 표시할 메시지
 * @param {string} type - 메시지 타입 ('success', 'error', 'info')
 */
function showToast(message, type = 'info') {
    // 기존 토스트 제거
    const existingToast = document.getElementById('toast-message');
    if (existingToast) {
        existingToast.remove();
    }

    // 토스트 생성
    const toast = document.createElement('div');
    toast.id = 'toast-message';
    toast.className = `toast toast-${type}`;
    toast.textContent = message;

    // 스타일
    toast.style.position = 'fixed';
    toast.style.bottom = '20px';
    toast.style.right = '20px';
    toast.style.padding = '15px 25px';
    toast.style.borderRadius = '8px';
    toast.style.color = 'white';
    toast.style.fontSize = '14px';
    toast.style.fontWeight = '600';
    toast.style.zIndex = '10000';
    toast.style.boxShadow = '0 4px 12px rgba(0,0,0,0.15)';
    toast.style.animation = 'slideIn 0.3s ease-out';

    if (type === 'success') {
        toast.style.background = '#28a745';
    } else if (type === 'error') {
        toast.style.background = '#dc3545';
    } else {
        toast.style.background = '#17a2b8';
    }

    document.body.appendChild(toast);

    // 3초 후 제거
    setTimeout(() => {
        toast.style.animation = 'slideOut 0.3s ease-out';
        setTimeout(() => toast.remove(), 300);
    }, 3000);
}

// CSS 애니메이션 추가
if (!document.getElementById('toast-animations')) {
    const style = document.createElement('style');
    style.id = 'toast-animations';
    style.textContent = `
        @keyframes slideIn {
            from {
                transform: translateX(400px);
                opacity: 0;
            }
            to {
                transform: translateX(0);
                opacity: 1;
            }
        }
        @keyframes slideOut {
            from {
                transform: translateX(0);
                opacity: 1;
            }
            to {
                transform: translateX(400px);
                opacity: 0;
            }
        }
    `;
    document.head.appendChild(style);
}

/**
 * CSV 다운로드 함수
 * @param {Array} data - JSON 데이터 배열
 * @param {string} filename - 파일명
 */
function downloadCSV(data, filename = 'data.csv') {
    if (!data || data.length === 0) {
        alert('다운로드할 데이터가 없습니다');
        return;
    }

    const headers = Object.keys(data[0]);
    let csvContent = '\uFEFF'; // UTF-8 BOM for Excel

    // 헤더 추가
    csvContent += headers.join(',') + '\n';

    // 데이터 행 추가
    data.forEach(row => {
        const rowData = headers.map(header => {
            let value = row[header] || '';
            // 쉼표나 줄바꿈이 포함된 경우 따옴표로 감싸기
            if (String(value).includes(',') || String(value).includes('\n') || String(value).includes('"')) {
                value = `"${String(value).replace(/"/g, '""')}"`;
            }
            return value;
        });
        csvContent += rowData.join(',') + '\n';
    });

    // Blob 생성 및 다운로드
    const blob = new Blob([csvContent], { type: 'text/csv;charset=utf-8;' });
    const link = document.createElement('a');
    const url = URL.createObjectURL(blob);

    link.setAttribute('href', url);
    link.setAttribute('download', filename);
    link.style.visibility = 'hidden';

    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);

    showToast('CSV 파일이 다운로드되었습니다', 'success');
}

/**
 * 특정 컬럼만 복사
 * @param {Array} data - JSON 데이터 배열
 * @param {Array} columns - 복사할 컬럼 배열
 */
function copySelectedColumns(data, columns) {
    if (!data || data.length === 0) {
        alert('복사할 데이터가 없습니다');
        return;
    }

    // 헤더 생성
    let text = columns.join('\t') + '\n';

    // 데이터 행 생성
    data.forEach(row => {
        const rowData = columns.map(col => row[col] || '');
        text += rowData.join('\t') + '\n';
    });

    copyToClipboard(text);
}
