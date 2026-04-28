import os, re, uuid, tempfile
from pathlib import Path
from typing import Optional

import yt_dlp
from fastapi import FastAPI, HTTPException, BackgroundTasks, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(title="MediaGrabNow API", version="1.0.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=False, allow_methods=["*"], allow_headers=["*"])

TEMP_DIR = Path(tempfile.gettempdir()) / "mgn"
TEMP_DIR.mkdir(exist_ok=True)
COOKIE_FILE = Path(__file__).parent / "cookies.txt"

def detect_platform(url):
    u = url.lower()
    if "youtube.com" in u or "youtu.be" in u: return "youtube"
    if "instagram.com" in u: return "instagram"
    if "tiktok.com" in u: return "tiktok"
    if "facebook.com" in u or "fb.watch" in u: return "facebook"
    if "twitter.com" in u or "x.com" in u: return "twitter"
    if "pinterest.com" in u: return "pinterest"
    if "vimeo.com" in u: return "vimeo"
    if "reddit.com" in u or "redd.it" in u: return "reddit"
    if "threads.net" in u: return "threads"
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
    hmap = {"4k":2160,"2160p":2160,"2k":1440,"1440p":1440,
            "1080p":1080,"720p":720,"480p":480,"360p":360,"240p":240,"144p":144}
    h = hmap.get(quality.lower(), 720)
    # Very flexible — tries many combinations so it always finds something
    return (
        f"bestvideo[height<={h}][ext=mp4]+bestaudio[ext=m4a]/bestvideo[height<={h}][ext=mp4]+bestaudio/"
        f"bestvideo[height<={h}]+bestaudio[ext=m4a]/bestvideo[height<={h}]+bestaudio/"
        f"best[height<={h}][ext=mp4]/best[height<={h}]/best"
    )

def base_ydl_opts():
    """Base yt-dlp options — YouTube bot detection fix"""
    opts = {
        "quiet": True,
        "noplaylist": True,
        "http_headers": {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            "Accept-Language": "en-US,en;q=0.9",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        },
        "extractor_args": {
            "youtube": {
                # mweb + android work without cookies on server
                "player_client": ["mweb", "android", "tv_embedded"],
            }
        },
    }
    # Cookies file agar hai to use karo
    if COOKIE_FILE.exists():
        opts["cookiefile"] = str(COOKIE_FILE)
    return opts

def find_file(fid):
    for f in TEMP_DIR.iterdir():
        if f.name.startswith(fid): return f
    return None

def clean_old():
    import time
    now = time.time()
    for f in TEMP_DIR.iterdir():
        try:
            if now - f.stat().st_mtime > 3600: f.unlink()
        except: pass

@app.get("/")
def root(): return {"status":"ok","service":"MediaGrabNow API","version":"1.0.0"}

@app.get("/health")
def health(): return {"status":"ok"}

@app.post("/info")
async def get_info(request: Request):
    try: body = await request.json()
    except: raise HTTPException(400,"Invalid JSON")
    url = (body.get("url") or "").strip()
    if not url.startswith("http"): raise HTTPException(400,"Invalid URL")

    opts = {**base_ydl_opts(), "skip_download": True}

    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
    except Exception as e:
        raise HTTPException(422, str(e)[:300])

    dur = int(info.get("duration") or 0)
    vfmts, seen = [], set()
    for f in reversed(info.get("formats",[])):
        h = f.get("height")
        if not h or f.get("vcodec","none")=="none" or h in seen: continue
        seen.add(h)
        ql = ("4K" if h>=2160 else "2K" if h>=1440 else "1080p" if h>=1080
              else "720p" if h>=720 else "480p" if h>=480 else "360p" if h>=360
              else "240p" if h>=240 else "144p")
        fs = f.get("filesize") or f.get("filesize_approx")
        vfmts.append({"quality":ql,"height":h,"format":"MP4",
                      "size":fmt_size(fs) if fs else est_size(h,dur),"fast":h<=1080})
    vfmts.sort(key=lambda x:x["height"],reverse=True)

    afmts, seen_a = [], set()
    for f in info.get("formats",[]):
        if f.get("vcodec","none")!="none": continue
        abr = int(f.get("abr") or 0)
        if abr<48 or abr in seen_a: continue
        seen_a.add(abr)
        fs = f.get("filesize") or f.get("filesize_approx")
        afmts.append({"quality":f"{abr} kbps","abr":abr,"format":"MP3",
                      "size":fmt_size(fs) if fs else est_audio(abr,dur),"fast":True})
    afmts.sort(key=lambda x:x["abr"],reverse=True)
    if not afmts:
        afmts = [{"quality":"128 kbps","abr":128,"format":"MP3","size":est_audio(128,dur),"fast":True}]

    thumbs = info.get("thumbnails",[])
    thumb = info.get("thumbnail","")
    if thumbs:
        best = max(thumbs,key=lambda t:(t.get("width") or 0)*(t.get("height") or 0))
        thumb = best.get("url",thumb)

    return JSONResponse({"success":True,"platform":detect_platform(url),
        "title":info.get("title","Unknown"),"thumbnail":thumb,
        "duration":fmt_dur(dur),"duration_sec":dur,"uploader":info.get("uploader",""),
        "video_formats":vfmts,"audio_formats":afmts})

@app.post("/download")
async def download_video(request: Request, bg: BackgroundTasks):
    try: body = await request.json()
    except: raise HTTPException(400,"Invalid JSON")
    url = (body.get("url") or "").strip()
    fmt = (body.get("format") or "mp4").lower()
    mtype = (body.get("type") or "video").lower()
    qual = body.get("quality") or "720p"
    if not url.startswith("http"): raise HTTPException(400,"Invalid URL")

    fid = str(uuid.uuid4())[:8]
    tpl = str(TEMP_DIR/fid)+".%(ext)s"
    opts = base_ydl_opts()

    if mtype=="thumbnail" or fmt=="jpg":
        opts.update({"skip_download":True,"writethumbnail":True,"outtmpl":tpl})
        ext,mime = "jpg","image/jpeg"
    elif mtype=="audio" or fmt in ("mp3","m4a"):
        opts.update({"format":"bestaudio/best","outtmpl":tpl})
        ext,mime = "mp3","audio/mpeg"
    else:
        opts.update({"format":get_fmt(qual,mtype),"outtmpl":tpl,"merge_output_format":"mp4"})
        ext,mime = "mp4","video/mp4"

    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url,download=True)
    except yt_dlp.utils.DownloadError as e:
        raise HTTPException(422,str(e)[:300])
    except Exception as e:
        raise HTTPException(500,str(e)[:300])

    dl = find_file(fid)
    if not dl: raise HTTPException(500,"File not found after download")
    safe = re.sub(r"[^\w\s-]","",info.get("title","video"))[:50].strip()
    fn = re.sub(r"\s+","_",safe)+"."+ext
    bg.add_task(clean_old)
    return FileResponse(path=str(dl),media_type=mime,filename=fn,
        headers={"Content-Disposition":f'attachment; filename="{fn}"',"Cache-Control":"no-cache"})

@app.post("/clip")
async def download_clip(request: Request, bg: BackgroundTasks):
    try: body = await request.json()
    except: raise HTTPException(400,"Invalid JSON")
    url=(body.get("url") or "").strip()
    start=body.get("start","0:00"); end=body.get("end","1:00")
    qual=body.get("quality","720p")
    if not url.startswith("http"): raise HTTPException(400,"Invalid URL")
    def pt(t):
        p=str(t).split(":")
        try: return int(p[0])*60+int(p[1]) if len(p)==2 else int(p[0])
        except: return 0
    ss,es=pt(start),pt(end)
    if es<=ss: raise HTTPException(400,"End must be after start")
    fid=str(uuid.uuid4())[:8]
    opts={**base_ydl_opts(),"format":get_fmt(qual,"video"),
          "outtmpl":str(TEMP_DIR/fid)+".%(ext)s","merge_output_format":"mp4",
          "postprocessor_args":{"ffmpeg":["-ss",str(ss),"-t",str(es-ss)]}}
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info=ydl.extract_info(url,download=True)
    except Exception as e: raise HTTPException(500,str(e)[:300])
    dl=find_file(fid)
    if not dl: raise HTTPException(500,"Clip not found")
    safe=re.sub(r"[^\w\s-]","",info.get("title","clip"))[:40].strip()
    fn=re.sub(r"\s+","_",safe)+"_clip.mp4"
    bg.add_task(clean_old)
    return FileResponse(path=str(dl),media_type="video/mp4",filename=fn,
        headers={"Content-Disposition":f'attachment; filename="{fn}"'})
