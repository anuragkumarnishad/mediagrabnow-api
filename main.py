"""
MediaGrabNow.com - Production Backend
======================================
Features:
- YouTube, Instagram, TikTok, Facebook, Twitter, Pinterest, Vimeo, Reddit, Threads
- Real file sizes
- Native browser download
- Cookies support (YouTube fix)
- All qualities 144p to 4K
- MP3 audio extraction
- Thumbnail download
- Clip trimming
"""

import os, re, uuid, tempfile, logging
from pathlib import Path
from typing import Optional

import yt_dlp
from fastapi import FastAPI, HTTPException, BackgroundTasks, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware

# ── Logging ──────────────────────────────────────────────────────
logging.basicConfig(level=logging.INFO)
log = logging.getLogger("mgn")

# ── PO Token Cache ────────────────────────────────────────────────
_po_token   = None
_visitor_data = None

def get_po_token():
    """Generate fresh PO token for YouTube"""
    global _po_token, _visitor_data
    try:
        import potoken_generator.main as ptg
        result = ptg.get_po_token()
        _po_token     = result.get("poToken")
        _visitor_data = result.get("visitorData")
        log.info(f"PO Token generated: {str(_po_token)[:20]}...")
        return _po_token, _visitor_data
    except Exception as e:
        log.warning(f"PO Token generation failed: {e}")
        return None, None

# ── App ──────────────────────────────────────────────────────────
app = FastAPI(title="MediaGrabNow API", version="2.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Paths ─────────────────────────────────────────────────────────
BASE_DIR   = Path(__file__).parent
TEMP_DIR   = Path(tempfile.gettempdir()) / "mgn"
COOKIE_FILE = BASE_DIR / "cookies.txt"
TEMP_DIR.mkdir(exist_ok=True)

log.info(f"Cookie file exists: {COOKIE_FILE.exists()}")
log.info(f"Temp dir: {TEMP_DIR}")

# ── Platform detect ───────────────────────────────────────────────
def detect_platform(url: str) -> str:
    u = url.lower()
    if "youtube.com" in u or "youtu.be" in u: return "youtube"
    if "instagram.com" in u: return "instagram"
    if "tiktok.com" in u or "vm.tiktok.com" in u: return "tiktok"
    if "facebook.com" in u or "fb.watch" in u or "fb.com" in u: return "facebook"
    if "twitter.com" in u or "x.com" in u: return "twitter"
    if "pinterest.com" in u or "pin.it" in u: return "pinterest"
    if "vimeo.com" in u: return "vimeo"
    if "reddit.com" in u or "redd.it" in u or "v.redd.it" in u: return "reddit"
    if "threads.net" in u: return "threads"
    return "unknown"

# ── Helpers ───────────────────────────────────────────────────────
def fmt_size(b):
    if not b: return None
    if b < 1048576: return f"{b/1024:.0f} KB"
    if b < 1073741824: return f"{b/1048576:.1f} MB"
    return f"{b/1073741824:.2f} GB"

def est_size(h, dur):
    if not dur: return "~? MB"
    rates = {2160:15000,1440:8000,1080:4000,720:2500,480:1200,360:700,240:400,144:200}
    return "~" + (fmt_size(int(rates.get(h,2000)*125*dur)) or "? MB")

def est_audio(abr, dur):
    if not dur: return "~? MB"
    return "~" + (fmt_size(int(abr*125*dur)) or "? MB")

def fmt_dur(s):
    if not s: return ""
    s = int(s)
    h,m,sec = s//3600,(s%3600)//60,s%60
    return f"{h}:{m:02d}:{sec:02d}" if h else f"{m}:{sec:02d}"

def get_fmt(quality: str, mtype: str) -> str:
    if mtype == "audio": return "bestaudio/best"
    hmap = {
        "4k":2160,"2160p":2160,"2k":1440,"1440p":1440,
        "1080p":1080,"fhd":1080,"hd":720,"720p":720,
        "480p":480,"sd":480,"360p":360,"240p":240,"144p":144
    }
    h = hmap.get(quality.lower().strip(), 720)
    return (
        f"bestvideo[height<={h}][ext=mp4]+bestaudio[ext=m4a]/"
        f"bestvideo[height<={h}][ext=mp4]+bestaudio/"
        f"bestvideo[height<={h}]+bestaudio[ext=m4a]/"
        f"bestvideo[height<={h}]+bestaudio/"
        f"best[height<={h}]/best"
    )

def base_opts() -> dict:
    """Base yt-dlp options — works for all platforms"""
    opts = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "geo_bypass": True,
        "geo_bypass_country": "IN",
        "http_headers": {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            "Accept-Language": "en-IN,en;q=0.9,hi;q=0.8",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
            "Referer": "https://www.google.co.in/",
            "X-Forwarded-For": "157.32.0.1",
        },
        "extractor_args": {
            "youtube": {
                "player_client": ["tv_embedded", "web_embedded", "android"],
            }
        },
        "geo_bypass": True,
        "geo_bypass_country": "US",
        "socket_timeout": 30,
        "retries": 3,
        "fragment_retries": 3,
    }
    # Cookies add karo agar file hai
    if COOKIE_FILE.exists():
        opts["cookiefile"] = str(COOKIE_FILE)
        log.info("Using cookies.txt")
    return opts

def find_file(fid: str) -> Optional[Path]:
    for f in TEMP_DIR.iterdir():
        if f.name.startswith(fid):
            return f
    return None

def clean_old():
    import time
    now = time.time()
    for f in TEMP_DIR.iterdir():
        try:
            if now - f.stat().st_mtime > 3600:
                f.unlink()
        except: pass

def safe_filename(title: str, ext: str) -> str:
    safe = re.sub(r"[^\w\s-]", "", title or "video")[:60].strip()
    safe = re.sub(r"\s+", "_", safe)
    return f"{safe}.{ext}"

# ── Routes ────────────────────────────────────────────────────────

@app.get("/")
def root():
    return {"status": "ok", "service": "MediaGrabNow API", "version": "2.0.0"}

@app.get("/health")
def health():
    return {
        "status":   "ok",
        "cookies":  COOKIE_FILE.exists(),
        "po_token": bool(_po_token),
        "temp_dir": str(TEMP_DIR),
    }

@app.post("/refresh-token")
def refresh_token():
    """Manually refresh PO token"""
    tok, vis = get_po_token()
    return {"success": bool(tok), "po_token": str(tok)[:20]+"..." if tok else None}

# ── /info — Video info + formats ──────────────────────────────────
@app.post("/info")
async def get_info(request: Request):
    try:
        body = await request.json()
    except:
        raise HTTPException(400, "Invalid JSON")

    url = (body.get("url") or "").strip()
    if not url.startswith("http"):
        raise HTTPException(400, "Invalid URL")

    opts = {**base_opts(), "skip_download": True}

    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
    except yt_dlp.utils.DownloadError as e:
        raise HTTPException(422, str(e)[:300])
    except Exception as e:
        raise HTTPException(500, str(e)[:300])

    if not info:
        raise HTTPException(404, "Video not found")

    dur = int(info.get("duration") or 0)

    # Video formats
    vfmts, seen = [], set()
    for f in reversed(info.get("formats", [])):
        h = f.get("height")
        if not h or f.get("vcodec", "none") == "none" or h in seen:
            continue
        seen.add(h)
        ql = ("4K" if h>=2160 else "2K" if h>=1440 else "1080p" if h>=1080
              else "720p" if h>=720 else "480p" if h>=480 else "360p" if h>=360
              else "240p" if h>=240 else "144p")
        fs = f.get("filesize") or f.get("filesize_approx")
        fps = int(f.get("fps") or 30)
        vfmts.append({
            "quality": ql,
            "height":  h,
            "fps":     fps,
            "format":  "MP4",
            "size":    fmt_size(fs) if fs else est_size(h, dur),
            "fast":    h <= 1080,
        })
    vfmts.sort(key=lambda x: x["height"], reverse=True)

    # Audio formats
    afmts, seen_a = [], set()
    for f in info.get("formats", []):
        if f.get("vcodec", "none") != "none":
            continue
        abr = int(f.get("abr") or 0)
        if abr < 48 or abr in seen_a:
            continue
        seen_a.add(abr)
        fs = f.get("filesize") or f.get("filesize_approx")
        afmts.append({
            "quality": f"{abr} kbps",
            "abr":     abr,
            "format":  "MP3",
            "size":    fmt_size(fs) if fs else est_audio(abr, dur),
            "fast":    True,
        })
    afmts.sort(key=lambda x: x["abr"], reverse=True)
    if not afmts:
        afmts = [{"quality":"128 kbps","abr":128,"format":"MP3",
                  "size":est_audio(128,dur),"fast":True}]

    # Best thumbnail
    thumbs = info.get("thumbnails", [])
    thumb  = info.get("thumbnail", "")
    if thumbs:
        best  = max(thumbs, key=lambda t: (t.get("width") or 0)*(t.get("height") or 0))
        thumb = best.get("url", thumb)

    return JSONResponse({
        "success":       True,
        "platform":      detect_platform(url),
        "title":         info.get("title", "Unknown"),
        "thumbnail":     thumb,
        "duration":      fmt_dur(dur),
        "duration_sec":  dur,
        "uploader":      info.get("uploader") or info.get("channel", ""),
        "view_count":    info.get("view_count", 0),
        "video_formats": vfmts,
        "audio_formats": afmts,
    })

# ── /download — Native browser download ───────────────────────────
@app.post("/download")
async def download_video(request: Request, bg: BackgroundTasks):
    try:
        body = await request.json()
    except:
        raise HTTPException(400, "Invalid JSON")

    url   = (body.get("url") or "").strip()
    fmt   = (body.get("format") or "mp4").lower()
    mtype = (body.get("type") or "video").lower()
    qual  = (body.get("quality") or "720p").strip()

    log.info(f"Download request: url={url[:80]}, quality={qual}, type={mtype}, format={fmt}")

    if not url:
        raise HTTPException(400, "URL is empty — please paste a valid video URL")
    if not url.startswith("http"):
        raise HTTPException(400, f"Invalid URL: '{url[:50]}' — must start with http")

    fid = str(uuid.uuid4())[:8]
    tpl = str(TEMP_DIR / fid) + ".%(ext)s"
    opts = base_opts()

    if mtype == "thumbnail" or fmt == "jpg":
        opts.update({
            "skip_download": True,
            "writethumbnail": True,
            "convert_thumbnails": "jpg",
            "outtmpl": tpl,
        })
        ext, mime = "jpg", "image/jpeg"

    elif mtype == "audio" or fmt in ("mp3", "m4a"):
        opts.update({
            "format": "bestaudio/best",
            "outtmpl": tpl,
            "postprocessors": [{
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "192",
            }],
        })
        ext, mime = "mp3", "audio/mpeg"

    else:
        opts.update({
            "format": get_fmt(qual, mtype),
            "outtmpl": tpl,
            "merge_output_format": "mp4",
        })
        ext, mime = "mp4", "video/mp4"

    log.info(f"Downloading: {url} | quality={qual} | type={mtype} | fmt={fmt}")

    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
    except yt_dlp.utils.DownloadError as e:
        err = str(e)
        log.error(f"Download error: {err[:200]}")
        if "private" in err.lower():
            raise HTTPException(422, "This video is private.")
        if "not available" in err.lower():
            raise HTTPException(422, "Video not available in your region.")
        if "sign in" in err.lower() or "bot" in err.lower():
            raise HTTPException(422, "YouTube requires authentication. Please try again.")
        raise HTTPException(422, err[:300])
    except Exception as e:
        log.error(f"Server error: {str(e)[:200]}")
        raise HTTPException(500, str(e)[:300])

    dl = find_file(fid)
    if not dl or not dl.exists():
        raise HTTPException(500, "Download failed — file not found")

    fn = safe_filename(info.get("title", "video"), ext)
    bg.add_task(clean_old)

    log.info(f"Serving file: {dl.name} ({dl.stat().st_size} bytes)")

    return FileResponse(
        path=str(dl),
        media_type=mime,
        filename=fn,
        headers={
            "Content-Disposition": f'attachment; filename="{fn}"',
            "Cache-Control": "no-cache",
            "X-Platform": detect_platform(url),
            "X-Quality": qual,
        }
    )

# ── /clip — Trim & download clip ──────────────────────────────────
@app.post("/clip")
async def download_clip(request: Request, bg: BackgroundTasks):
    try:
        body = await request.json()
    except:
        raise HTTPException(400, "Invalid JSON")

    url   = (body.get("url") or "").strip()
    start = body.get("start", "0:00")
    end   = body.get("end", "1:00")
    qual  = body.get("quality", "720p")
    fmt   = body.get("format", "mp4").lower()

    if not url.startswith("http"):
        raise HTTPException(400, "Invalid URL")

    def pt(t):
        p = str(t).split(":")
        try: return int(p[0])*60+int(p[1]) if len(p)==2 else int(p[0])
        except: return 0

    ss, es = pt(start), pt(end)
    if es <= ss:
        raise HTTPException(400, "End time must be after start time")

    fid  = str(uuid.uuid4())[:8]
    opts = {
        **base_opts(),
        "format": get_fmt(qual, "video") if fmt != "mp3" else "bestaudio/best",
        "outtmpl": str(TEMP_DIR / fid) + ".%(ext)s",
        "merge_output_format": "mp4",
        "postprocessor_args": {
            "ffmpeg": ["-ss", str(ss), "-t", str(es - ss)]
        },
    }

    if fmt == "mp3":
        opts["format"] = "bestaudio[ext=m4a]/bestaudio/best" 

    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
    except Exception as e:
        raise HTTPException(500, str(e)[:300])

    dl = find_file(fid)
    if not dl:
        raise HTTPException(500, "Clip not found")

    out_ext = "mp3" if fmt == "mp3" else "mp4"
    fn = safe_filename(f"{info.get('title','clip')}_clip_{start}-{end}", out_ext)
    bg.add_task(clean_old)

    return FileResponse(
        path=str(dl),
        media_type="audio/mpeg" if fmt == "mp3" else "video/mp4",
        filename=fn,
        headers={"Content-Disposition": f'attachment; filename="{fn}"'}
    )
