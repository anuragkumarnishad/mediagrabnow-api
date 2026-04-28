"""
MediaGrabNow.com - FastAPI Backend
Run: uvicorn main:app --host 0.0.0.0 --port $PORT
"""

import os
import re
import uuid
import tempfile
from pathlib import Path
from typing import Optional

import yt_dlp
from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

app = FastAPI(title="MediaGrabNow API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

TEMP_DIR = Path(tempfile.gettempdir()) / "mgn"
TEMP_DIR.mkdir(exist_ok=True)

class InfoReq(BaseModel):
    url: str

class DlReq(BaseModel):
    url: str
    quality: str = "720p"
    format: str = "mp4"
    type: str = "video"
    platform: Optional[str] = None
    noWatermark: bool = True

def detect_platform(url):
    url = url.lower()
    if "youtube.com" in url or "youtu.be" in url: return "youtube"
    if "instagram.com" in url: return "instagram"
    if "tiktok.com" in url: return "tiktok"
    if "facebook.com" in url or "fb.watch" in url: return "facebook"
    if "twitter.com" in url or "x.com" in url: return "twitter"
    if "pinterest.com" in url: return "pinterest"
    if "vimeo.com" in url: return "vimeo"
    if "reddit.com" in url or "redd.it" in url: return "reddit"
    if "threads.net" in url: return "threads"
    return "unknown"

def fmt_size(b):
    if not b: return "~? MB"
    if b < 1048576: return f"{b/1024:.0f} KB"
    if b < 1073741824: return f"{b/1048576:.1f} MB"
    return f"{b/1073741824:.2f} GB"

def est_size(h, dur):
    if not dur: return "~? MB"
    rates = {2160:15000,1440:8000,1080:4000,720:2500,480:1200,360:700,240:400,144:200}
    return "~" + fmt_size(int(rates.get(h,2000)*125*dur))

def est_audio(abr, dur):
    if not dur: return "~? MB"
    return "~" + fmt_size(int(abr*125*dur))

def fmt_dur(s):
    if not s: return ""
    h,m,sec = s//3600,(s%3600)//60,s%60
    return f"{h}:{m:02d}:{sec:02d}" if h else f"{m}:{sec:02d}"

def get_fmt(quality, mtype):
    if mtype == "audio": return "bestaudio/best"
    hmap = {"4k":2160,"2160p":2160,"2k":1440,"1440p":1440,"1080p":1080,
            "720p":720,"480p":480,"360p":360,"240p":240,"144p":144}
    h = hmap.get(quality.lower(), 720)
    return f"bestvideo[height<={h}][ext=mp4]+bestaudio[ext=m4a]/bestvideo[height<={h}]+bestaudio/best[height<={h}]/best"

def clean_old():
    import time
    now = time.time()
    for f in TEMP_DIR.iterdir():
        try:
            if now - f.stat().st_mtime > 3600: f.unlink()
        except: pass

def find_file(file_id):
    for f in TEMP_DIR.iterdir():
        if f.name.startswith(file_id): return f
    return None

@app.get("/")
def root():
    return {"status": "ok", "service": "MediaGrabNow API", "version": "1.0.0"}

@app.get("/health")
def health():
    return {"status": "ok"}

@app.post("/info")
def get_info(req: InfoReq):
    url = req.url.strip()
    if not url.startswith("http"):
        raise HTTPException(400, "Invalid URL")
    try:
        with yt_dlp.YoutubeDL({"quiet":True,"skip_download":True,"noplaylist":True}) as ydl:
            info = ydl.extract_info(url, download=False)
    except Exception as e:
        raise HTTPException(422, str(e)[:200])

    dur = info.get("duration", 0) or 0

    vfmts, seen = [], set()
    for f in reversed(info.get("formats", [])):
        h = f.get("height")
        if not h or f.get("vcodec") == "none" or h in seen: continue
        seen.add(h)
        ql = "4K" if h>=2160 else "2K" if h>=1440 else "1080p" if h>=1080 else "720p" if h>=720 else "480p" if h>=480 else "360p" if h>=360 else "240p" if h>=240 else "144p"
        fs = f.get("filesize") or f.get("filesize_approx")
        vfmts.append({"quality":ql,"height":h,"format":"MP4","size":fmt_size(fs) if fs else est_size(h,dur),"fast":h<=1080})
    vfmts.sort(key=lambda x: x["height"], reverse=True)

    afmts, seen_a = [], set()
    for f in info.get("formats", []):
        if f.get("vcodec") != "none": continue
        abr = int(f.get("abr") or 0)
        if abr < 48 or abr in seen_a: continue
        seen_a.add(abr)
        fs = f.get("filesize") or f.get("filesize_approx")
        afmts.append({"quality":f"{abr} kbps","abr":abr,"format":"MP3","size":fmt_size(fs) if fs else est_audio(abr,dur),"fast":True})
    afmts.sort(key=lambda x: x["abr"], reverse=True)
    if not afmts:
        afmts = [{"quality":"128 kbps","abr":128,"format":"MP3","size":est_audio(128,dur),"fast":True}]

    thumbs = info.get("thumbnails", [])
    thumb = info.get("thumbnail", "")
    if thumbs:
        best = max(thumbs, key=lambda t: (t.get("width") or 0)*(t.get("height") or 0))
        thumb = best.get("url", thumb)

    return {
        "success": True,
        "platform": detect_platform(url),
        "title": info.get("title", "Unknown"),
        "thumbnail": thumb,
        "duration": fmt_dur(dur),
        "duration_sec": dur,
        "uploader": info.get("uploader", ""),
        "video_formats": vfmts,
        "audio_formats": afmts,
    }

@app.post("/download")
def download_video(req: DlReq, bg: BackgroundTasks):
    url = req.url.strip()
    if not url.startswith("http"):
        raise HTTPException(400, "Invalid URL")

    fmt = req.format.lower()
    mtype = req.type.lower()
    fid = str(uuid.uuid4())[:8]
    tpl = str(TEMP_DIR / fid) + ".%(ext)s"

    if mtype == "thumbnail" or fmt == "jpg":
        opts = {"quiet":True,"skip_download":True,"writethumbnail":True,"outtmpl":tpl}
        ext, mime = "jpg", "image/jpeg"
    elif mtype == "audio" or fmt in ("mp3","m4a"):
        opts = {"quiet":True,"format":"bestaudio/best","outtmpl":tpl,"noplaylist":True}
        ext, mime = "mp3", "audio/mpeg"
    else:
        opts = {"quiet":True,"format":get_fmt(req.quality,mtype),"outtmpl":tpl,"noplaylist":True,"merge_output_format":"mp4"}
        ext, mime = "mp4", "video/mp4"

    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
    except yt_dlp.utils.DownloadError as e:
        raise HTTPException(422, str(e)[:200])
    except Exception as e:
        raise HTTPException(500, str(e)[:200])

    dl = find_file(fid)
    if not dl:
        raise HTTPException(500, "Download failed — file not found")

    safe = re.sub(r"[^\w\s-]","",info.get("title","video"))[:50].strip()
    safe = re.sub(r"\s+","_",safe)
    fn = f"{safe}.{ext}"
    bg.add_task(clean_old)

    return FileResponse(
        path=str(dl), media_type=mime, filename=fn,
        headers={"Content-Disposition": f'attachment; filename="{fn}"', "Cache-Control": "no-cache"}
    )

@app.post("/clip")
def download_clip(url: str, start: str = "0:00", end: str = "1:00", quality: str = "720p", bg: BackgroundTasks = None):
    if not url.startswith("http"):
        raise HTTPException(400, "Invalid URL")
    def pt(t):
        p = t.split(":")
        try: return int(p[0])*60+int(p[1]) if len(p)==2 else int(p[0])
        except: return 0
    ss, es = pt(start), pt(end)
    if es <= ss: raise HTTPException(400, "End must be after start")
    fid = str(uuid.uuid4())[:8]
    opts = {"quiet":True,"format":get_fmt(quality,"video"),"outtmpl":str(TEMP_DIR/fid)+".%(ext)s","noplaylist":True,"merge_output_format":"mp4","postprocessor_args":{"ffmpeg":["-ss",str(ss),"-t",str(es-ss)]}}
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
    except Exception as e:
        raise HTTPException(500, str(e)[:200])
    dl = find_file(fid)
    if not dl: raise HTTPException(500, "Clip not found")
    safe = re.sub(r"[^\w\s-]","",info.get("title","clip"))[:40].strip()
    fn = f"{re.sub(r'\\s+','_',safe)}_clip.mp4"
    if bg: bg.add_task(clean_old)
    return FileResponse(path=str(dl), media_type="video/mp4", filename=fn, headers={"Content-Disposition":f'attachment; filename="{fn}"'})
    
