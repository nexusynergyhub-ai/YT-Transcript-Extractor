"""
YouTube Channel Scraper - Flask Web Application

A web interface for scraping YouTube channel videos and transcripts.
"""

import csv
import os
import re
import uuid
import threading
from datetime import datetime
from io import StringIO

from flask import Flask, render_template, request, jsonify, send_file, redirect, url_for
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from youtube_transcript_api import YouTubeTranscriptApi
from youtube_transcript_api._errors import (
    NoTranscriptFound,
    TranscriptsDisabled,
    VideoUnavailable,
)

app = Flask(__name__)

# In-memory job storage (use Redis for production)
jobs = {}

# Initialize transcript API client with optional proxy support
proxy_url = os.environ.get('PROXY_URL')
if proxy_url:
    proxies = {'http': proxy_url, 'https': proxy_url}
    transcript_api = YouTubeTranscriptApi(proxies=proxies)
else:
    transcript_api = YouTubeTranscriptApi()


class ScraperJob:
    """Represents a scraping job with progress tracking."""
    
    def __init__(self, job_id, channel_url, api_key):
        self.job_id = job_id
        self.channel_url = channel_url
        self.api_key = api_key
        self.status = 'pending'
        self.progress = 0
        self.total_videos = 0
        self.current_video = ''
        self.message = 'Initializing...'
        self.error = None
        self.csv_data = None
        self.channel_id = None
    
    def update(self, status=None, progress=None, total=None, current=None, message=None):
        if status:
            self.status = status
        if progress is not None:
            self.progress = progress
        if total is not None:
            self.total_videos = total
        if current:
            self.current_video = current
        if message:
            self.message = message


def extract_channel_id(youtube, channel_url: str) -> str:
    """Extract channel ID from various YouTube URL formats."""
    # Direct channel ID format
    match = re.search(r'youtube\.com/channel/([a-zA-Z0-9_-]+)', channel_url)
    if match:
        return match.group(1)
    
    # Handle format (@username)
    match = re.search(r'youtube\.com/@([a-zA-Z0-9_-]+)', channel_url)
    if match:
        handle = match.group(1)
        request = youtube.search().list(
            part='snippet',
            q=f'@{handle}',
            type='channel',
            maxResults=1
        )
        response = request.execute()
        if response['items']:
            return response['items'][0]['snippet']['channelId']
        raise ValueError(f"Could not find channel for handle: @{handle}")
    
    # Custom URL format (/c/customname)
    match = re.search(r'youtube\.com/c/([a-zA-Z0-9_-]+)', channel_url)
    if match:
        custom_name = match.group(1)
        request = youtube.search().list(
            part='snippet',
            q=custom_name,
            type='channel',
            maxResults=1
        )
        response = request.execute()
        if response['items']:
            return response['items'][0]['snippet']['channelId']
        raise ValueError(f"Could not find channel for custom URL: {custom_name}")
    
    # User format (/user/username)
    match = re.search(r'youtube\.com/user/([a-zA-Z0-9_-]+)', channel_url)
    if match:
        username = match.group(1)
        request = youtube.channels().list(
            part='id',
            forUsername=username
        )
        response = request.execute()
        if response['items']:
            return response['items'][0]['id']
        raise ValueError(f"Could not find channel for username: {username}")
    
    raise ValueError(f"Could not parse channel URL: {channel_url}")


def get_uploads_playlist_id(youtube, channel_id: str) -> str:
    """Get the uploads playlist ID for a channel."""
    request = youtube.channels().list(
        part='contentDetails',
        id=channel_id
    )
    response = request.execute()
    
    if not response['items']:
        raise ValueError(f"Channel not found: {channel_id}")
    
    return response['items'][0]['contentDetails']['relatedPlaylists']['uploads']


def get_all_video_ids(youtube, playlist_id: str, job: ScraperJob) -> list:
    """Fetch all video IDs from a playlist."""
    video_ids = []
    next_page_token = None
    
    job.update(message='Fetching video list...')
    
    while True:
        request = youtube.playlistItems().list(
            part='contentDetails',
            playlistId=playlist_id,
            maxResults=50,
            pageToken=next_page_token
        )
        response = request.execute()
        
        for item in response['items']:
            video_ids.append(item['contentDetails']['videoId'])
        
        next_page_token = response.get('nextPageToken')
        if not next_page_token:
            break
        
        job.update(message=f'Found {len(video_ids)} videos...')
    
    return video_ids


def get_video_details(youtube, video_ids: list) -> dict:
    """Fetch video details for multiple videos."""
    video_details = {}
    
    for i in range(0, len(video_ids), 50):
        batch = video_ids[i:i+50]
        
        request = youtube.videos().list(
            part='snippet,statistics',
            id=','.join(batch)
        )
        response = request.execute()
        
        for item in response['items']:
            video_id = item['id']
            snippet = item['snippet']
            stats = item.get('statistics', {})
            
            video_details[video_id] = {
                'title': snippet.get('title', ''),
                'upload_date': snippet.get('publishedAt', '')[:10],
                'views': stats.get('viewCount', '0'),
                'likes': stats.get('likeCount', '0'),
            }
    
    return video_details


def get_transcript(video_id: str) -> tuple:
    """Fetch transcript for a video."""
    try:
        transcript = transcript_api.fetch(video_id)
        full_transcript = ' '.join([entry.text for entry in transcript])
        return full_transcript, True
    except (NoTranscriptFound, TranscriptsDisabled, VideoUnavailable):
        return '', False
    except Exception:
        return '', False


def run_scraper(job: ScraperJob):
    """Background task to run the scraper."""
    try:
        job.update(status='running', message='Connecting to YouTube API...')
        
        # Initialize YouTube API
        youtube = build('youtube', 'v3', developerKey=job.api_key)
        
        # Extract channel ID
        job.update(message='Extracting channel ID...')
        channel_id = extract_channel_id(youtube, job.channel_url)
        job.channel_id = channel_id
        
        # Get uploads playlist
        uploads_playlist_id = get_uploads_playlist_id(youtube, channel_id)
        
        # Get all video IDs
        video_ids = get_all_video_ids(youtube, uploads_playlist_id, job)
        
        if not video_ids:
            job.update(status='completed', message='No videos found in this channel.')
            job.csv_data = ''
            return
        
        job.update(total=len(video_ids), message='Fetching video details...')
        
        # Get video details
        video_details = get_video_details(youtube, video_ids)
        
        # Create CSV in memory
        output = StringIO()
        fieldnames = ['video_id', 'title', 'views', 'likes', 'upload_date', 
                      'transcript_available', 'transcript']
        writer = csv.DictWriter(output, fieldnames=fieldnames)
        writer.writeheader()
        
        # Fetch transcripts and write to CSV
        for idx, video_id in enumerate(video_ids, 1):
            details = video_details.get(video_id, {})
            title = details.get('title', video_id)[:50]
            
            job.update(
                progress=idx,
                current=title,
                message=f'Processing video {idx}/{len(video_ids)}: {title}...'
            )
            
            transcript, transcript_available = get_transcript(video_id)
            
            row = {
                'video_id': video_id,
                'title': details.get('title', ''),
                'views': details.get('views', '0'),
                'likes': details.get('likes', '0'),
                'upload_date': details.get('upload_date', ''),
                'transcript_available': transcript_available,
                'transcript': transcript,
            }
            writer.writerow(row)
        
        job.csv_data = output.getvalue()
        job.update(status='completed', message=f'Successfully scraped {len(video_ids)} videos!')
        
    except HttpError as e:
        job.update(status='error', message=f'YouTube API Error: {str(e)}')
        job.error = str(e)
    except ValueError as e:
        job.update(status='error', message=f'Error: {str(e)}')
        job.error = str(e)
    except Exception as e:
        job.update(status='error', message=f'Unexpected error: {str(e)}')
        job.error = str(e)


@app.route('/')
def index():
    """Homepage with input form."""
    has_env_key = bool(os.environ.get('YouTube_Data_API_v3'))
    return render_template('index.html', has_env_key=has_env_key)


@app.route('/scrape', methods=['POST'])
def start_scrape():
    """Start a new scraping job."""
    channel_url = request.form.get('channel_url', '').strip()
    api_key = request.form.get('api_key', '').strip()
    
    if not channel_url:
        return render_template('index.html', error='Please enter a channel URL')
    
    # Use environment variable as fallback if no API key provided
    if not api_key:
        api_key = os.environ.get('YouTube_Data_API_v3', '')
    
    if not api_key:
        return render_template('index.html', error='Please enter your YouTube API key or set YouTube_Data_API_v3 environment variable')
    
    # Create job
    job_id = str(uuid.uuid4())
    job = ScraperJob(job_id, channel_url, api_key)
    jobs[job_id] = job
    
    # Start background thread
    thread = threading.Thread(target=run_scraper, args=(job,))
    thread.daemon = True
    thread.start()
    
    return redirect(url_for('results', job_id=job_id))


@app.route('/results/<job_id>')
def results(job_id):
    """Results page with progress tracking."""
    if job_id not in jobs:
        return redirect(url_for('index'))
    
    return render_template('results.html', job_id=job_id)


@app.route('/status/<job_id>')
def status(job_id):
    """Get job status as JSON."""
    if job_id not in jobs:
        return jsonify({'error': 'Job not found'}), 404
    
    job = jobs[job_id]
    return jsonify({
        'status': job.status,
        'progress': job.progress,
        'total': job.total_videos,
        'current': job.current_video,
        'message': job.message,
        'error': job.error,
    })


@app.route('/download/<job_id>')
def download(job_id):
    """Download the CSV file."""
    if job_id not in jobs:
        return redirect(url_for('index'))
    
    job = jobs[job_id]
    
    if job.status != 'completed' or not job.csv_data:
        return redirect(url_for('results', job_id=job_id))
    
    # Create file-like object from string
    output = StringIO(job.csv_data)
    
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    filename = f'youtube_channel_{job.channel_id or "data"}_{timestamp}.csv'
    
    return send_file(
        __import__('io').BytesIO(job.csv_data.encode('utf-8')),
        mimetype='text/csv',
        as_attachment=True,
        download_name=filename
    )


if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)
