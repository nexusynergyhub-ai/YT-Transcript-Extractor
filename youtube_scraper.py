#!/usr/bin/env python3
"""
YouTube Channel Scraper

Extracts video metadata (title, views, likes, upload date) and transcripts
from all videos on a YouTube channel and exports to CSV.

Usage:
    python youtube_scraper.py <channel_url> --api-key <YOUR_API_KEY>
    python youtube_scraper.py <channel_url>  # Uses YOUTUBE_API_KEY env variable
"""

import argparse
import csv
import os
import re
import sys
from datetime import datetime

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from youtube_transcript_api import YouTubeTranscriptApi
from youtube_transcript_api._errors import (
    NoTranscriptFound,
    TranscriptsDisabled,
    VideoUnavailable,
)

# Initialize transcript API client with optional proxy support (new API syntax for v1.2.0+)
proxy_url = os.environ.get('PROXY_URL')
if proxy_url:
    proxies = {'http': proxy_url, 'https': proxy_url}
    transcript_api = YouTubeTranscriptApi(proxies=proxies)
else:
    transcript_api = YouTubeTranscriptApi()


def extract_channel_id(youtube, channel_url: str) -> str:
    """
    Extract channel ID from various YouTube URL formats.
    
    Supports:
    - youtube.com/channel/CHANNEL_ID
    - youtube.com/@handle
    - youtube.com/c/customname
    - youtube.com/user/username
    """
    # Direct channel ID format
    match = re.search(r'youtube\.com/channel/([a-zA-Z0-9_-]+)', channel_url)
    if match:
        return match.group(1)
    
    # Handle format (@username)
    match = re.search(r'youtube\.com/@([a-zA-Z0-9_-]+)', channel_url)
    if match:
        handle = match.group(1)
        # Use search to find channel by handle
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


def get_all_video_ids(youtube, playlist_id: str) -> list:
    """Fetch all video IDs from a playlist."""
    video_ids = []
    next_page_token = None
    
    print("Fetching video list...")
    
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
        
        print(f"  Found {len(video_ids)} videos so far...")
    
    print(f"Total videos found: {len(video_ids)}")
    return video_ids


def get_video_details(youtube, video_ids: list) -> dict:
    """Fetch video details (title, views, likes, upload date) for multiple videos."""
    video_details = {}
    
    # Process in batches of 50 (API limit)
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
                'upload_date': snippet.get('publishedAt', '')[:10],  # YYYY-MM-DD
                'views': stats.get('viewCount', '0'),
                'likes': stats.get('likeCount', '0'),
            }
    
    return video_details


def get_transcript(video_id: str) -> tuple:
    """
    Fetch transcript for a video.
    
    Returns:
        tuple: (transcript_text, transcript_available)
    """
    try:
        # Use new API syntax: instance.fetch() instead of static get_transcript()
        transcript = transcript_api.fetch(video_id)
        # Combine all transcript segments
        full_transcript = ' '.join([entry.text for entry in transcript])
        return full_transcript, True
    except (NoTranscriptFound, TranscriptsDisabled, VideoUnavailable):
        return '', False
    except Exception as e:
        print(f"  Warning: Could not fetch transcript for {video_id}: {e}")
        return '', False


def scrape_channel(channel_url: str, api_key: str, output_file: str = None) -> str:
    """
    Main function to scrape a YouTube channel.
    
    Args:
        channel_url: YouTube channel URL
        api_key: YouTube Data API key
        output_file: Output CSV filename (optional)
    
    Returns:
        Path to the output CSV file
    """
    # Initialize YouTube API client
    youtube = build('youtube', 'v3', developerKey=api_key)
    
    # Extract channel ID
    print(f"Processing channel: {channel_url}")
    channel_id = extract_channel_id(youtube, channel_url)
    print(f"Channel ID: {channel_id}")
    
    # Get uploads playlist
    uploads_playlist_id = get_uploads_playlist_id(youtube, channel_id)
    
    # Get all video IDs
    video_ids = get_all_video_ids(youtube, uploads_playlist_id)
    
    if not video_ids:
        print("No videos found in this channel.")
        return None
    
    # Get video details
    print("Fetching video details...")
    video_details = get_video_details(youtube, video_ids)
    
    # Generate output filename if not provided
    if not output_file:
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        output_file = f'youtube_channel_{channel_id}_{timestamp}.csv'
    
    # Fetch transcripts and write to CSV
    print(f"Fetching transcripts and writing to {output_file}...")
    
    with open(output_file, 'w', newline='', encoding='utf-8') as csvfile:
        fieldnames = ['video_id', 'title', 'views', 'likes', 'upload_date', 
                      'transcript_available', 'transcript']
        writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
        writer.writeheader()
        
        for idx, video_id in enumerate(video_ids, 1):
            details = video_details.get(video_id, {})
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
            
            # Progress indicator
            status = "✓" if transcript_available else "✗"
            print(f"  [{idx}/{len(video_ids)}] {status} {details.get('title', video_id)[:50]}")
    
    print(f"\nDone! Output saved to: {output_file}")
    return output_file


def main():
    parser = argparse.ArgumentParser(
        description='Scrape YouTube channel videos and transcripts to CSV'
    )
    parser.add_argument(
        'channel_url',
        help='YouTube channel URL (e.g., https://www.youtube.com/@ChannelName)'
    )
    parser.add_argument(
        '--api-key',
        help='YouTube Data API key (or set YOUTUBE_API_KEY env variable)',
        default=os.environ.get('YOUTUBE_API_KEY')
    )
    parser.add_argument(
        '--output', '-o',
        help='Output CSV filename (default: auto-generated)',
        default=None
    )
    
    args = parser.parse_args()
    
    if not args.api_key:
        print("Error: YouTube API key is required.")
        print("Either provide --api-key or set YOUTUBE_API_KEY environment variable.")
        print("\nTo get an API key:")
        print("1. Go to https://console.cloud.google.com/")
        print("2. Create a project and enable YouTube Data API v3")
        print("3. Create credentials (API key)")
        sys.exit(1)
    
    try:
        scrape_channel(args.channel_url, args.api_key, args.output)
    except HttpError as e:
        print(f"YouTube API Error: {e}")
        sys.exit(1)
    except ValueError as e:
        print(f"Error: {e}")
        sys.exit(1)
    except KeyboardInterrupt:
        print("\nOperation cancelled by user.")
        sys.exit(0)


if __name__ == '__main__':
    main()
