#!/usr/bin/env bash
set -e

echo "==> Installing system dependencies..."
apt-get update -qq
apt-get install -y ffmpeg

echo "==> Upgrading pip..."
pip install --upgrade pip

echo "==> Installing Python dependencies..."
pip install fastapi==0.109.2
pip install "uvicorn[standard]==0.27.1"
pip install aiofiles==23.2.1
pip install python-multipart==0.0.9
pip install httpx==0.27.0
pip install pydantic==2.6.4
pip install yt-dlp

echo "==> Updating yt-dlp to latest..."
pip install -U yt-dlp

echo "==> Build complete!"
ffmpeg -version | head -1
python -c "import yt_dlp; print('yt-dlp version:', yt_dlp.version.__version__)"
python -c "import fastapi; print('FastAPI version:', fastapi.__version__)"
