"""
MediaGrabNow.com — Python FastAPI Backend
==========================================
Requirements:
    pip install fastapi uvicorn yt-dlp aiofiles python-multipart httpx

Run:
    uvicorn main:app --host 0.0.0.0 --port 8000 --reload

Production:
    uvicorn main:app --host 0.0.0.0 --port 8000 --workers 4
"""

import os
import re
import json
import uuid
import asyncio
import tempfile
import mimetypes
from pathlib import Path
from typing import Optional

import yt_dlp
import aiofiles
from fastapi import FastAPI, HTTPException, BackgroundTasks, Request
from fastapi.responses import FileResponse, StreamingResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

# ─── APP SETUP ─────────────────────────────────────────────────────────────────
app = FastAPI(
    title="MediaGrabNow API",
    description="Free video downloader backend for mediagrabnow.com",
    version="1.0.0"
)

# CORS — frontend se requests allow karo
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://www.mediagrabnow.com",
        "https://mediagrabnow.com",
        "http://localhost:3000",
        "http://127.0.0.1:5500",  # local dev
        "*"  # production mein specific domains karo
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─── TEMP FOLDER ───────────────────────────────────────────────────────────────
TEMP_DIR = Path(tempfile.gettempdir()) / "mediagrabnow"
TEMP_DIR.mkdir(exist_ok=True)

# FFmpeg path -- Render pe /usr/bin/ffmpeg hota hai
FFMPEG_PATH = os.environ.get("FFMPEG_PATH", "ffmpeg")

# ─── PLATFORM DETECT ───────────────────────────────────────────────────────────
PLATFORM_PATTERNS = {
    "youtube":   [r"youtube\.com", r"youtu\.be", r"youtube\.com/shorts"],
    "instagram": [r"instagram\.com"],
    "tiktok":    [r"tiktok\.com", r"vm\.tiktok\.com"],
    "facebook":  [r"facebook\.com", r"fb\.watch", r"fb\.com"],
    "twitter":   [r"twitter\.com", r"x\.com", r"t\.co"],
    "pinterest": [r"pinterest\.com", r"pin\.it"],
    "vimeo":     [r"vimeo\.com"],
    "reddit":    [r"reddit\.com", r"redd\.it", r"v\.redd\.it"],
    "threads":   [r"threads\.net"],
}

def detect_platform(url: str) -> str:
    for platform, patterns in PLATFORM_PATTERNS.items():
        for pat in patterns:
            if re.search(pat, url, re.IGNORECASE):
                return platform
    return "unknown"

# ─── REQUEST / RESPONSE MODELS ─────────────────────────────────────────────────
class InfoRequest(BaseModel):
    url: str

class DownloadRequest(BaseModel):
    url: str
    quality: str = "1080p"       # "4K", "2K", "1080p", "720p", "480p", "360p", "240p", "144p"
    format: str = "mp4"          # "mp4", "mp3", "m4a", "jpg"
    type: str = "video"          # "video", "audio", "thumbnail"
    platform: Optional[str] = None
    noWatermark: bool = True

# ─── YT-DLP HELPERS ────────────────────────────────────────────────────────────
def quality_to_height(quality: str) -> Optional[int]:
    """Quality string ko pixel height mein convert karo"""
    mapping = {
        "4k": 2160, "2160p": 2160,
        "2k": 1440, "1440p": 1440,
        "1080p": 1080, "fhd": 1080,
        "720p": 720, "hd": 720,
        "480p": 480,
        "360p": 360,
        "240p": 240,
        "144p": 144,
    }
    return mapping.get(quality.lower().strip())

def get_format_selector(quality: str, fmt: str, media_type: str) -> str:
    """yt-dlp format selector string banao"""
    if media_type == "audio" or fmt in ("mp3", "m4a"):
        return "bestaudio/best"

    if fmt == "jpg":
        return "best"  # thumbnail ke liye

    height = quality_to_height(quality)
    if height:
        # Exact quality ya usse neeche ki best
        return (
            f"bestvideo[height<={height}][ext=mp4]+bestaudio[ext=m4a]/"
            f"bestvideo[height<={height}]+bestaudio/"
            f"best[height<={height}]/"
            f"best"
        )
    return "bestvideo[ext=mp4]+bestaudio[ext=m4a]/bestvideo+bestaudio/best"

def clean_old_files():
    """1 ghante se purani temp files delete karo"""
    import time
    now = time.time()
    for f in TEMP_DIR.iterdir():
        try:
            if now - f.stat().st_mtime > 3600:
                f.unlink()
        except Exception:
            pass

# ─── ROUTE 1: GET VIDEO INFO (thumbnail, title, duration, formats) ─────────────
@app.post("/info")
async def get_info(req: InfoRequest):
    """
    Frontend ko video ki puri info do:
    - title, thumbnail, duration
    - available formats with size estimates
    """
    url = req.url.strip()
    if not url.startswith("http"):
        raise HTTPException(status_code=400, detail="Invalid URL")

    platform = detect_platform(url)

    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "noplaylist": True,
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)

        if not info:
            raise HTTPException(status_code=404, detail="Could not fetch video info")

        # ── Available video formats with sizes ──
        video_formats = []
        audio_formats = []
        seen_heights = set()

        raw_formats = info.get("formats", [])

        for f in reversed(raw_formats):
            height = f.get("height")
            fps    = f.get("fps", 0)
            vcodec = f.get("vcodec", "none")
            acodec = f.get("acodec", "none")
            ext    = f.get("ext", "mp4")
            fsize  = f.get("filesize") or f.get("filesize_approx")

            # Video format
            if vcodec != "none" and height and height not in seen_heights:
                seen_heights.add(height)

                # Quality label
                if height >= 2160:   qlabel = "4K"
                elif height >= 1440: qlabel = "2K"
                elif height >= 1080: qlabel = "1080p"
                elif height >= 720:  qlabel = "720p"
                elif height >= 480:  qlabel = "480p"
                elif height >= 360:  qlabel = "360p"
                elif height >= 240:  qlabel = "240p"
                else:                qlabel = "144p"

                # Size estimate
                size_str = format_size(fsize) if fsize else estimate_size(height, info.get("duration", 0))

                video_formats.append({
                    "quality": qlabel,
                    "height":  height,
                    "fps":     int(fps) if fps else 30,
                    "format":  "MP4",
                    "size":    size_str,
                    "fast":    height <= 1080,
                })

            # Audio-only format
            elif vcodec == "none" and acodec != "none":
                abr = f.get("abr", 0) or 0
                if abr >= 128 and len(audio_formats) < 4:
                    fsize_a = f.get("filesize") or f.get("filesize_approx")
                    size_str = format_size(fsize_a) if fsize_a else estimate_audio_size(abr, info.get("duration", 0))
                    audio_formats.append({
                        "quality": f"{int(abr)} kbps",
                        "abr":     int(abr),
                        "format":  "MP3",
                        "size":    size_str,
                        "fast":    abr <= 256,
                    })

        # Sort by quality
        video_formats.sort(key=lambda x: x["height"], reverse=True)
        audio_formats.sort(key=lambda x: x["abr"], reverse=True)

        # Deduplicate audio by kbps
        seen_abr = set()
        unique_audio = []
        for af in audio_formats:
            if af["abr"] not in seen_abr:
                seen_abr.add(af["abr"])
                unique_audio.append(af)

        # Duration format
        duration_sec = info.get("duration", 0)
        duration_str = format_duration(duration_sec)

        # Thumbnail — best quality
        thumbnails = info.get("thumbnails", [])
        thumb_url  = info.get("thumbnail", "")
        if thumbnails:
            best = max(thumbnails, key=lambda t: (t.get("width", 0) or 0) * (t.get("height", 0) or 0))
            thumb_url = best.get("url", thumb_url)

        return {
            "success":      True,
            "platform":     platform,
            "title":        info.get("title", "Unknown Title"),
            "thumbnail":    thumb_url,
            "duration":     duration_str,
            "duration_sec": duration_sec,
            "uploader":     info.get("uploader", ""),
            "view_count":   info.get("view_count", 0),
            "like_count":   info.get("like_count", 0),
            "description":  (info.get("description", "") or "")[:300],
            "video_formats": video_formats,
            "audio_formats": unique_audio or [
                {"quality": "128 kbps", "abr": 128, "format": "MP3",
                 "size": estimate_audio_size(128, duration_sec), "fast": True}
            ],
        }

    except yt_dlp.utils.DownloadError as e:
        raise HTTPException(status_code=422, detail=f"yt-dlp error: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Server error: {str(e)}")


# ─── ROUTE 2: DOWNLOAD — Browser native download ───────────────────────────────
@app.post("/download")
async def download_video(req: DownloadRequest, background_tasks: BackgroundTasks):
    """
    Video download karke browser ko direct stream karo.
    Browser ka native download dialog open hoga.
    """
    url = req.url.strip()
    if not url.startswith("http"):
        raise HTTPException(status_code=400, detail="Invalid URL")

    platform = req.platform or detect_platform(url)
    fmt      = req.format.lower()
    media_type = req.type.lower()

    # Unique temp file
    file_id   = str(uuid.uuid4())[:8]
    temp_path = TEMP_DIR / file_id

    # ── yt-dlp options ──
    ydl_opts = {
        "quiet":      True,
        "no_warnings": True,
        "noplaylist": True,
        "outtmpl":    str(temp_path) + ".%(ext)s",
    }

    # Thumbnail download
    if media_type == "thumbnail" or fmt == "jpg":
        ydl_opts.update({
            "skip_download":    True,
            "writethumbnail":   True,
            "convert_thumbnails": "jpg",
        })
        ext = "jpg"
        mime = "image/jpeg"

    # Audio download (MP3)
    elif media_type == "audio" or fmt in ("mp3", "m4a"):
        ydl_opts.update({
            "format": "bestaudio/best",
            "postprocessors": [{
                "key":            "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "192",
            }],
        })
        ext  = "mp3"
        mime = "audio/mpeg"

    # Video download (MP4)
    else:
        ydl_opts.update({
            "format": get_format_selector(req.quality, fmt, media_type),
            "postprocessors": [{
                "key":            "FFmpegVideoConvertor",
                "preferedformat": "mp4",
            }],
            "merge_output_format": "mp4",
        })

        # TikTok / Instagram — watermark hatao
        if platform == "tiktok" and req.noWatermark:
            ydl_opts["format"] = "download_addr-0/wm_source_m3u8_first/hd-0/hd/sd/ld/best"

        ext  = "mp4"
        mime = "video/mp4"

    try:
        # Download karo
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)

        # Downloaded file dhundo
        downloaded_file = None
        for f in TEMP_DIR.iterdir():
            if f.stem == file_id or f.name.startswith(file_id):
                downloaded_file = f
                break

        if not downloaded_file or not downloaded_file.exists():
            raise HTTPException(status_code=500, detail="Download failed — file not found")

        # Clean filename
        safe_title = re.sub(r'[^\w\s-]', '', info.get("title", "video"))[:50].strip()
        safe_title = re.sub(r'\s+', '_', safe_title)
        filename   = f"{safe_title}.{ext}"

        # Background mein purani files clean karo
        background_tasks.add_task(clean_old_files)

        # ── Browser native download ──
        # Content-Disposition: attachment → browser ka download dialog khulega
        return FileResponse(
            path=str(downloaded_file),
            media_type=mime,
            filename=filename,
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"',
                "X-Platform":          platform,
                "X-Quality":           req.quality,
                "Cache-Control":       "no-cache",
            }
        )

    except yt_dlp.utils.DownloadError as e:
        err = str(e)
        if "Private" in err or "private" in err:
            detail = "This video is private and cannot be downloaded."
        elif "not available" in err.lower():
            detail = "This video is not available in your region."
        elif "age" in err.lower():
            detail = "Age-restricted content cannot be downloaded."
        else:
            detail = f"Download failed: {err[:200]}"
        raise HTTPException(status_code=422, detail=detail)

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Server error: {str(e)}")


# ─── ROUTE 3: CLIP DOWNLOAD (start-end trim) ───────────────────────────────────
@app.post("/clip")
async def download_clip(
    url:     str,
    start:   str = "0:00",   # "1:30" format
    end:     str = "2:00",
    quality: str = "1080p",
    format:  str = "mp4",
    background_tasks: BackgroundTasks = None
):
    """
    Video ka ek clip download karo (start time se end time tak)
    FFmpeg se trim hoga
    """
    if not url.startswith("http"):
        raise HTTPException(status_code=400, detail="Invalid URL")

    file_id   = str(uuid.uuid4())[:8]
    temp_path = TEMP_DIR / file_id

    def parse_time(t: str) -> int:
        """'1:30' → 90 seconds"""
        parts = t.strip().split(":")
        try:
            if len(parts) == 2:
                return int(parts[0]) * 60 + int(parts[1])
            return int(parts[0])
        except:
            return 0

    start_sec = parse_time(start)
    end_sec   = parse_time(end)

    if end_sec <= start_sec:
        raise HTTPException(status_code=400, detail="End time must be after start time")

    duration = end_sec - start_sec

    ydl_opts = {
        "quiet":      True,
        "no_warnings": True,
        "noplaylist": True,
        "outtmpl":    str(temp_path) + ".%(ext)s",
        "format":     get_format_selector(quality, format, "video"),
        "merge_output_format": "mp4",
        "postprocessors": [{
            "key":            "FFmpegVideoRemuxer",
            "preferedformat": "mp4",
        }],
        # FFmpeg clip trim
        "postprocessor_args": {
            "ffmpeg": [
                "-ss", str(start_sec),
                "-t",  str(duration),
            ]
        },
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)

        downloaded_file = None
        for f in TEMP_DIR.iterdir():
            if f.name.startswith(file_id):
                downloaded_file = f
                break

        if not downloaded_file:
            raise HTTPException(status_code=500, detail="Clip creation failed")

        safe_title = re.sub(r'[^\w\s-]', '', info.get("title", "clip"))[:40].strip()
        safe_title = re.sub(r'\s+', '_', safe_title)
        filename   = f"{safe_title}_clip_{start.replace(':','-')}_{end.replace(':','-')}.mp4"

        if background_tasks:
            background_tasks.add_task(clean_old_files)

        return FileResponse(
            path=str(downloaded_file),
            media_type="video/mp4",
            filename=filename,
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"',
                "X-Clip-Start": start,
                "X-Clip-End":   end,
            }
        )

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ─── ROUTE 4: HEALTH CHECK ─────────────────────────────────────────────────────
@app.get("/health")
async def health():
    """Server status check"""
    import yt_dlp as ydl_mod
    return {
        "status":    "ok",
        "service":   "MediaGrabNow API",
        "version":   "1.0.0",
        "yt_dlp":    ydl_mod.version.__version__,
    }

@app.get("/")
async def root():
    return {"message": "MediaGrabNow API is running!", "docs": "/docs"}


# ─── HELPER FUNCTIONS ──────────────────────────────────────────────────────────
def format_size(bytes_val: int) -> str:
    """Bytes ko human readable size mein convert karo"""
    if not bytes_val:
        return "Unknown"
    if bytes_val < 1024 * 1024:
        return f"{bytes_val / 1024:.1f} KB"
    if bytes_val < 1024 * 1024 * 1024:
        return f"{bytes_val / (1024*1024):.1f} MB"
    return f"{bytes_val / (1024*1024*1024):.2f} GB"

def estimate_size(height: int, duration_sec: int) -> str:
    """Quality aur duration se size estimate karo (approximate bitrates)"""
    if not duration_sec:
        return "~? MB"
    # Average bitrates (video + audio) in kbps
    bitrates = {2160: 15000, 1440: 8000, 1080: 4000, 720: 2500, 480: 1200, 360: 700, 240: 400, 144: 200}
    bitrate = bitrates.get(height, 2000)
    size_bytes = (bitrate * 1000 / 8) * duration_sec
    return "~" + format_size(int(size_bytes))

def estimate_audio_size(abr: int, duration_sec: int) -> str:
    """Audio bitrate se size estimate karo"""
    if not duration_sec:
        return "~? MB"
    size_bytes = (abr * 1000 / 8) * duration_sec
    return "~" + format_size(int(size_bytes))

def format_duration(seconds: int) -> str:
    """Seconds ko '3:45' format mein convert karo"""
    if not seconds:
        return ""
    h = seconds // 3600
    m = (seconds % 3600) // 60
    s = seconds % 60
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"
