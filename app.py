#!/usr/bin/env python3
from flask import Flask, request, jsonify, send_file, render_template
from flask_cors import CORS
import yt_dlp
import os
import tempfile
import threading
import uuid
import subprocess
import shutil
import time
import logging
import random
from pathlib import Path
from datetime import datetime, timedelta
from PIL import Image, ImageDraw, ImageFont
import io
import base64

# -------------------------
# Setup Flask + CORS + Logs
# -------------------------
app = Flask(__name__)
CORS(app)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger("YTDL")

# -------------------------
# In-memory Storage
# -------------------------
download_status = {}
captcha_store = {}
verified_sessions = {}

# -------------------------
# Download Job Object
# -------------------------
class DownloadProgress:
    def __init__(self):
        self.status = "queued"
        self.progress = 0.0
        self.filename = ""
        self.error = ""
        self.temp_dir = ""
        self.ffmpeg_available = False
        self.title = ""
        self.completed = False

# -------------------------
# Utilities
# -------------------------
def format_duration(seconds):
    try:
        seconds = int(seconds)
    except Exception:
        return "Unknown"
    if seconds <= 0:
        return "00:00"
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}" if hours else f"{minutes:02d}:{seconds:02d}"

def check_ffmpeg():
    try:
        result = subprocess.run(['ffmpeg', '-version'], capture_output=True, text=True, timeout=5)
        return result.returncode == 0
    except Exception:
        return False

def safe_get_job(job_id):
    return download_status.get(job_id)

# -------------------------
# CAPTCHA Generation
# -------------------------
def generate_captcha_image(captcha_code):
    try:
        width, height = 200, 80
        image = Image.new('RGB', (width, height), color=(255, 255, 255))
        draw = ImageDraw.Draw(image)
        font = ImageFont.load_default()
        draw.text((50, 30), captcha_code, font=font, fill=(0, 0, 0))
        img_byte_arr = io.BytesIO()
        image.save(img_byte_arr, format='PNG')
        return "data:image/png;base64," + base64.b64encode(img_byte_arr.getvalue()).decode('utf-8')
    except Exception as e:
        logger.error(f"Error generating CAPTCHA image: {e}")
        return None

def cleanup_expired_captchas():
    now = datetime.now()
    for key in list(captcha_store.keys()):
        if now > captcha_store[key]['expires']:
            captcha_store.pop(key, None)
    for key in list(verified_sessions.keys()):
        if now > verified_sessions[key]['expires']:
            verified_sessions.pop(key, None)

# -------------------------
# Worker Thread for Downloads
# -------------------------
def download_worker(url, format_str, file_ext, job_id):
    job = safe_get_job(job_id)
    if job is None:
        return

    temp_dir = tempfile.mkdtemp(prefix='ytdl_', dir="/tmp")
    job.temp_dir = temp_dir
    job.ffmpeg_available = check_ffmpeg()

    ydl_opts = {
        'outtmpl': os.path.join(temp_dir, '%(title)s.%(ext)s'),
        'progress_hooks': [],
        'quiet': True,
        'no_warnings': True,
        'noplaylist': True,
    }

    if file_ext == 'mp3' and job.ffmpeg_available:
        ydl_opts.update({
            'format': 'bestaudio/best',
            'postprocessors': [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': file_ext,
                'preferredquality': '192'
            }],
        })
    else:
        ydl_opts['format'] = format_str

    job.status = 'downloading'
    job.progress = 0.0
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            job.filename = ydl.prepare_filename(info)
            job.title = info.get("title", "unknown")
            job.status = 'completed'
            job.progress = 100.0
            job.completed = True
    except Exception as e:
        job.status = 'error'
        job.error = str(e)
        job.completed = True
        logger.error("Download failed for job %s: %s", job_id, e)

# -------------------------
# Routes
# -------------------------
@app.route("/")
def home():
    return jsonify({"status": "running", "message": "✅ YTDL with CAPTCHA active"})

@app.route("/api/generate-captcha")
def generate_captcha():
    captcha_code = str(random.randint(1000, 9999))
    captcha_id = str(uuid.uuid4())
    captcha_image = generate_captcha_image(captcha_code)
    captcha_store[captcha_id] = {'code': captcha_code, 'expires': datetime.now() + timedelta(minutes=5)}
    data = {'captcha_id': captcha_id, 'captcha_image': captcha_image}
    return jsonify(data)

@app.route("/api/verify-captcha", methods=["POST"])
def verify_captcha():
    data = request.get_json() or {}
    captcha_id = data.get("captcha_id")
    user_input = data.get("captcha_input")
    cleanup_expired_captchas()
    if not captcha_id or not user_input:
        return jsonify({'error': 'CAPTCHA ID and input required'}), 400
    captcha_data = captcha_store.get(captcha_id)
    if not captcha_data or user_input != captcha_data['code']:
        return jsonify({'valid': False, 'error': 'Incorrect CAPTCHA or expired'})
    session_token = str(uuid.uuid4())
    verified_sessions[session_token] = {'verified_at': datetime.now(), 'expires': datetime.now() + timedelta(minutes=10)}
    captcha_store.pop(captcha_id, None)
    return jsonify({'valid': True, 'session_token': session_token})

@app.route("/api/download", methods=["POST"])
def download_video():
    data = request.get_json() or {}
    url = data.get("url")
    format_str = data.get("format", "best")
    file_ext = data.get("file_ext", "mp4")
    session_token = data.get("session_token")

    if not url:
        return jsonify({"error": "URL is required"}), 400
    if not session_token or session_token not in verified_sessions:
        return jsonify({"error": "CAPTCHA verification required"}), 403

    verified_sessions.pop(session_token, None)
    job_id = str(uuid.uuid4())
    download_status[job_id] = DownloadProgress()

    threading.Thread(target=download_worker, args=(url, format_str, file_ext, job_id), daemon=True).start()
    return jsonify({'job_id': job_id, 'ffmpeg_available': check_ffmpeg()})

@app.route("/api/download-status/<job_id>")
def get_status(job_id):
    job = safe_get_job(job_id)
    if not job:
        return jsonify({"error": "Invalid Job ID"}), 404
    return jsonify({
        "status": job.status,
        "progress": job.progress,
        "filename": os.path.basename(job.filename),
        "error": job.error,
        "title": job.title
    })

@app.route("/api/download-file/<job_id>")
def download_file(job_id):
    job = safe_get_job(job_id)
    if not job or job.status != "completed":
        return jsonify({"error": "File not ready"}), 400
    return send_file(os.path.abspath(job.filename), as_attachment=True)

# -------------------------
# Run on Render
# -------------------------
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    logger.info(f"🚀 YTDL Flask Server running on port {port}")
    app.run(host="0.0.0.0", port=port, debug=False)
