import os
from googleapiclient.discovery import build
from google.oauth2 import service_account
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from youtube_transcript_api import YouTubeTranscriptApi
from youtube_transcript_api._errors import TranscriptsDisabled, NoTranscriptFound
import pickle
from config import Config
from logger_config import setup_logger, log_exception

logger = setup_logger('youtube_handler')


class YouTubeTranscriptExtractor:
    SCOPES = ['https://www.googleapis.com/auth/youtube.force-ssl']

    def __init__(self):
        self.youtube_service = None
        self._authenticate()

    def _authenticate(self):
        try:
            if os.path.exists(Config.SERVICE_ACCOUNT_FILE):
                logger.info("Attempting authentication with Service Account...")
                credentials = service_account.Credentials.from_service_account_file(
                    Config.SERVICE_ACCOUNT_FILE, scopes=self.SCOPES
                )
                self.youtube_service = build('youtube', 'v3', credentials=credentials)
                logger.info("Service Account authentication successful")
            elif os.path.exists(Config.CLIENT_SECRET_FILE):
                logger.info("Attempting OAuth 2.0 authentication...")
                creds = None

                if os.path.exists('token.pickle'):
                    with open('token.pickle', 'rb') as token:
                        creds = pickle.load(token)

                if not creds or not creds.valid:
                    if creds and creds.expired and creds.refresh_token:
                        creds.refresh(Request())
                    else:
                        flow = InstalledAppFlow.from_client_secrets_file(
                            Config.CLIENT_SECRET_FILE, self.SCOPES
                        )
                        creds = flow.run_local_server(port=0)

                    with open('token.pickle', 'wb') as token:
                        pickle.dump(creds, token)

                self.youtube_service = build('youtube', 'v3', credentials=creds)
                logger.info("OAuth authentication successful")
            else:
                logger.warning("No credential files found. Using API Key only (limited functionality).")
                self.youtube_service = build('youtube', 'v3', developerKey=Config.YOUTUBE_API_KEY)

        except Exception as e:
            logger.error(f"Authentication error: {e}")
            logger.info("Falling back to API Key authentication...")
            self.youtube_service = build('youtube', 'v3', developerKey=Config.YOUTUBE_API_KEY)

    def get_video_metadata(self, video_id):
        try:
            request = self.youtube_service.videos().list(
                part='snippet,statistics',
                id=video_id
            )
            response = request.execute()

            if response['items']:
                video = response['items'][0]
                return {
                    'title': video['snippet']['title'],
                    'description': video['snippet']['description'],
                    'channel': video['snippet']['channelTitle'],
                    'views': video['statistics'].get('viewCount', 'N/A'),
                    'likes': video['statistics'].get('likeCount', 'N/A')
                }
            else:
                logger.warning(f"No metadata found for video ID: {video_id}")
                return None

        except Exception as e:
            logger.error(f"Error fetching metadata for {video_id}: {e}")
            return None

    def get_transcript(self, video_id, languages=['ko', 'en']):
        try:
            logger.info(f"Attempting to fetch transcript for video ID: {video_id}")

            transcript_list = YouTubeTranscriptApi.list_transcripts(video_id)

            try:
                transcript = transcript_list.find_transcript(languages)
            except:
                transcript = transcript_list.find_generated_transcript(languages)

            transcript_data = transcript.fetch()

            full_text = ' '.join([item['text'] for item in transcript_data])

            logger.info(f"Successfully fetched transcript for {video_id}")
            return full_text

        except TranscriptsDisabled:
            logger.warning(f"Transcripts are disabled for video {video_id}")
            return None
        except NoTranscriptFound:
            logger.warning(f"No transcript found for video {video_id}")
            return None
        except Exception as e:
            logger.error(f"Error fetching transcript for {video_id}: {e}")
            return None

    def save_transcript(self, video_id, transcript_text, filename=None):
        try:
            if not os.path.exists(Config.TRANSCRIPTS_DIR):
                os.makedirs(Config.TRANSCRIPTS_DIR)

            if filename is None:
                filename = f"{video_id}_transcript.txt"

            filepath = os.path.join(Config.TRANSCRIPTS_DIR, filename)

            with open(filepath, 'w', encoding='utf-8') as f:
                f.write(transcript_text)

            logger.info(f"Transcript saved to {filepath}")
            return filepath

        except Exception as e:
            logger.error(f"Error saving transcript: {e}")
            return None

    def extract_transcripts_batch(self, video_ids, save_to_file=True):
        results = []

        for video_id in video_ids:
            logger.info(f"Processing video: {video_id}")

            metadata = self.get_video_metadata(video_id)
            transcript = self.get_transcript(video_id)

            result = {
                'video_id': video_id,
                'metadata': metadata,
                'transcript': transcript,
                'status': 'success' if transcript else 'failed'
            }

            if transcript and save_to_file:
                filepath = self.save_transcript(video_id, transcript)
                result['file_path'] = filepath

            results.append(result)

        return results
