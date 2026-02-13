# YouTube Channel Scraper

Extract video metadata (title, views, likes, upload date) and transcripts from any YouTube channel.

## Features

- 📊 **Video Metadata**: Title, views, likes, upload date
- 📝 **Full Transcripts**: Auto-generated or manual captions
- 📥 **CSV Export**: Ready for analysis
- 🌐 **Web Interface**: Modern dark theme UI
- 🚀 **Deploy Ready**: Render.com configuration included

## Prerequisites

- Python 3.7+
- [YouTube Data API Key](https://console.cloud.google.com/) (enable "YouTube Data API v3")

## Quick Start

### Option 1: Web Application

```bash
cd youtube_channel_scraper
pip install -r requirements.txt
python app.py
```

Visit **http://localhost:5000** and enter your channel URL + API key.

### Option 2: Command Line

```bash
python youtube_scraper.py "https://www.youtube.com/@ChannelName" --api-key YOUR_API_KEY
```

## Deploy to Render.com

[![Deploy to Render](https://render.com/images/deploy-to-render-button.svg)](https://render.com/deploy)

1. Push this folder to a GitHub repository
2. Go to [Render.com](https://render.com) → New → Blueprint
3. Connect your repository
4. Render will auto-detect `render.yaml` and deploy

## Output CSV Columns

| Column | Description |
|--------|-------------|
| `video_id` | YouTube video ID |
| `title` | Video title |
| `views` | View count |
| `likes` | Like count |
| `upload_date` | Upload date (YYYY-MM-DD) |
| `transcript_available` | True/False |
| `transcript` | Full transcript text |

## Notes

- **API Quota**: YouTube Data API has a daily quota (10,000 units). Each video costs ~1-3 units.
- **Transcripts**: Not all videos have transcripts. Missing transcripts are marked in the CSV.
