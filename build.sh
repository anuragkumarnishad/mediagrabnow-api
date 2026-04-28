#!/usr/bin/env bash
# Render.com build script
# FFmpeg install karo + Python dependencies

set -e

echo "==> Installing FFmpeg..."
apt-get update -qq && apt-get install -y ffmpeg

echo "==> Installing Python dependencies..."
pip install -r requirements.txt

echo "==> Updating yt-dlp to latest..."
pip install -U yt-dlp

echo "==> Build complete!"
ffmpeg -version | head -1
python -c "import yt_dlp; print('yt-dlp version:', yt_dlp.version.__version__)"
