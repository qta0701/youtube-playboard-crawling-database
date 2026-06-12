"""
Innertube API를 활용한 YouTube 자막 추출
2025년 YouTube 정책 변화에 대응하는 다중 폴백 시스템
"""

import requests
import json
import re
import xml.etree.ElementTree as ET
from typing import List, Dict, Optional, Tuple
import logging

logger = logging.getLogger(__name__)


class InnertubeTranscriptExtractor:
    """Innertube API를 사용한 자막 추출기 (2025년 방식)"""

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
            'Accept': 'application/json',
            'Accept-Language': 'ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7',
            'Content-Type': 'application/json',
            'Origin': 'https://www.youtube.com',
            'Referer': 'https://www.youtube.com/'
        })

    def extract_transcript(self, video_id: str) -> Dict:
        """
        Innertube API를 사용하여 자막 추출

        Returns:
            {
                'success': bool,
                'transcript': List[Tuple[str, str]],  # (timestamp, text)
                'language': str,
                'error': str
            }
        """
        result = {
            'success': False,
            'transcript': [],
            'language': 'unknown',
            'error': ''
        }

        try:
            logger.info(f"{video_id}: Innertube API로 자막 추출 시도")

            # Android 클라이언트 시뮬레이션 (2025년 권장 방식)
            player_response = self._get_player_response_android(video_id)

            if not player_response:
                logger.warning(f"{video_id}: Innertube player response 실패, Web 클라이언트 시도")
                player_response = self._get_player_response_web(video_id)

            if not player_response:
                result['error'] = 'Innertube API 응답 없음'
                return result

            # 자막 트랙 추출
            captions = player_response.get('captions', {})
            caption_tracks = captions.get('playerCaptionsTracklistRenderer', {}).get('captionTracks', [])

            if not caption_tracks:
                result['error'] = '자막 트랙 없음'
                logger.warning(f"{video_id}: 자막 트랙을 찾을 수 없음")
                return result

            logger.info(f"{video_id}: {len(caption_tracks)}개 자막 트랙 발견")

            # 한국어 우선, 없으면 첫 번째 트랙
            selected_track = None
            for track in caption_tracks:
                lang_code = track.get('languageCode', '')
                if lang_code in ['ko', 'kr']:
                    selected_track = track
                    logger.info(f"{video_id}: 한국어 자막 트랙 선택")
                    break

            if not selected_track:
                selected_track = caption_tracks[0]
                logger.info(f"{video_id}: 첫 번째 자막 트랙 선택 - {selected_track.get('languageCode', 'unknown')}")

            # 자막 URL에서 데이터 다운로드
            base_url = selected_track.get('baseUrl', '')
            if not base_url:
                result['error'] = '자막 URL 없음'
                return result

            # XML 형식으로 자막 다운로드
            caption_url = base_url + '&fmt=srv3'  # srv3 = XML 형식
            logger.debug(f"{video_id}: 자막 다운로드 URL - {caption_url}")

            response = self.session.get(caption_url, timeout=10)
            if response.status_code != 200:
                result['error'] = f'자막 다운로드 실패: HTTP {response.status_code}'
                logger.warning(f"{video_id}: {result['error']}")
                return result

            # XML 파싱
            transcript = self._parse_xml_caption(response.text, video_id)

            if transcript:
                result['success'] = True
                result['transcript'] = transcript
                result['language'] = selected_track.get('languageCode', 'unknown')
                logger.info(f"✅ {video_id}: Innertube API로 {len(transcript)}개 세그먼트 추출 성공")
            else:
                result['error'] = 'XML 파싱 실패 또는 빈 자막'

        except requests.exceptions.Timeout:
            result['error'] = 'Innertube API 타임아웃'
            logger.warning(f"{video_id}: {result['error']}")
        except Exception as e:
            result['error'] = f'Innertube API 오류: {str(e)}'
            logger.exception(f"{video_id}: Innertube API 예외:")

        return result

    def _get_player_response_android(self, video_id: str) -> Optional[Dict]:
        """Android 클라이언트로 player response 가져오기 (2025년 권장)"""
        try:
            url = 'https://www.youtube.com/youtubei/v1/player'
            params = {
                'key': 'AIzaSyA8eiZmM1FaDVjRy-df2KTyQ_vz_yYM39w',  # 공개 Android API 키
                'prettyPrint': 'false'
            }

            data = {
                'context': {
                    'client': {
                        'clientName': 'ANDROID',
                        'clientVersion': '19.09.37',
                        'androidSdkVersion': 30,
                        'hl': 'ko',
                        'gl': 'KR'
                    }
                },
                'videoId': video_id
            }

            response = self.session.post(url, params=params, json=data, timeout=10)

            if response.status_code == 200:
                player_response = response.json()
                logger.debug(f"{video_id}: Android 클라이언트 응답 성공")
                return player_response
            else:
                logger.debug(f"{video_id}: Android 클라이언트 실패 - HTTP {response.status_code}")
                return None

        except Exception as e:
            logger.debug(f"{video_id}: Android 클라이언트 예외 - {e}")
            return None

    def _get_player_response_web(self, video_id: str) -> Optional[Dict]:
        """Web 클라이언트로 player response 가져오기 (폴백)"""
        try:
            url = 'https://www.youtube.com/youtubei/v1/player'
            params = {
                'key': 'AIzaSyAO_FJ2SlqU8Q4STEHLGCilw_Y9_11qcW8',  # 공개 Web API 키
                'prettyPrint': 'false'
            }

            data = {
                'context': {
                    'client': {
                        'clientName': 'WEB',
                        'clientVersion': '2.20250109.01.00',
                        'hl': 'ko',
                        'gl': 'KR'
                    }
                },
                'videoId': video_id
            }

            response = self.session.post(url, params=params, json=data, timeout=10)

            if response.status_code == 200:
                player_response = response.json()
                logger.debug(f"{video_id}: Web 클라이언트 응답 성공")
                return player_response
            else:
                logger.debug(f"{video_id}: Web 클라이언트 실패 - HTTP {response.status_code}")
                return None

        except Exception as e:
            logger.debug(f"{video_id}: Web 클라이언트 예외 - {e}")
            return None

    def _parse_xml_caption(self, xml_text: str, video_id: str) -> List[Tuple[str, str]]:
        """XML 형식 자막 파싱"""
        try:
            root = ET.fromstring(xml_text)
            transcript = []

            for text_elem in root.findall('.//text'):
                start = float(text_elem.get('start', '0'))
                text = text_elem.text or ''

                # HTML 엔티티 디코딩
                text = text.replace('&amp;', '&').replace('&lt;', '<').replace('&gt;', '>').replace('&quot;', '"')
                text = text.replace('\n', ' ').strip()

                if text:
                    # 타임스탬프 포맷
                    timestamp = self._seconds_to_timestamp(start)
                    transcript.append((timestamp, text))

            logger.debug(f"{video_id}: XML 파싱 완료 - {len(transcript)}개 세그먼트")
            return transcript

        except ET.ParseError as e:
            logger.warning(f"{video_id}: XML 파싱 오류 - {e}")
            return []
        except Exception as e:
            logger.exception(f"{video_id}: XML 파싱 예외:")
            return []

    def _seconds_to_timestamp(self, seconds: float) -> str:
        """초를 HH:MM:SS 형식으로 변환"""
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        secs = int(seconds % 60)
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"


# 간단한 테스트
if __name__ == '__main__':
    logging.basicConfig(level=logging.DEBUG)
    extractor = InnertubeTranscriptExtractor()

    # 테스트 비디오 ID
    test_video_id = 'MdoEdQqY6C8'
    result = extractor.extract_transcript(test_video_id)

    print(f"\n성공: {result['success']}")
    print(f"언어: {result['language']}")
    print(f"세그먼트 수: {len(result['transcript'])}")
    if result['error']:
        print(f"오류: {result['error']}")
    if result['transcript']:
        print(f"\n첫 3개 세그먼트:")
        for ts, text in result['transcript'][:3]:
            print(f"  [{ts}] {text}")
