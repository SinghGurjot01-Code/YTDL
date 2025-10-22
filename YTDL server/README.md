# YTDL Server

A Flask-based YouTube downloader API with CAPTCHA verification.
Deployed on Render: <your-render-url>

## API Endpoints

- `/api/generate-captcha` → Generate CAPTCHA
- `/api/verify-captcha` → Verify CAPTCHA
- `/api/download` → Start download
- `/api/download-status/<job_id>` → Check status
- `/api/download-file/<job_id>` → Download file
