#!/usr/bin/env bash
set -e

echo "==> Upgrading pip..."
pip install --upgrade pip setuptools wheel

echo "==> Installing dependencies..."
pip install -r requirements.txt

echo "==> Updating yt-dlp..."
pip install -U yt-dlp

echo "==> Checking installation..."
python -c "import yt_dlp; print('yt-dlp OK:', yt_dlp.version.__version__)"
python -c "import fastapi; print('FastAPI OK:', fastapi.__version__)"

echo "==> Build complete!"
