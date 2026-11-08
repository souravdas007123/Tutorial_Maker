# Tutorial Maker (Django MVP)

User gives a URL + test login → app logs in with Playwright, explores the pages, drafts narration
(Claude), lets the user approve/edit, then records a narrated MP4 tutorial.

## Run

```bash
python -m venv .venv && source .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -r requirements.txt
playwright install chromium
# ffmpeg must be installed and on PATH (brew/apt/choco install ffmpeg)

export ANTHROPIC_API_KEY=sk-ant-...     # optional; without it narration uses simple templates
python manage.py makemigrations recorder
python manage.py migrate
python manage.py runserver
```

Open http://127.0.0.1:8000/ → **New tutorial**.

## Flow
1. **Exploring** – headless login, finds nav links, screenshots each page (skips Logout/Delete/Pay-style links).
2. **Approval** – you untick pages and edit narration.
3. **Recording** – logs in again *off-camera* (password never in the video), records with a visible cursor,
   gTTS voice-over per page, ffmpeg mixes → `media/jobs/<id>/tutorial.mp4`.

## Security notes
- Credentials are Fernet-encrypted and wiped when the job finishes/fails. Set `TUTORIAL_FERNET_KEY` in production.
- Private/loopback URLs are rejected (SSRF guard). For local testing: `TUTORIAL_ALLOW_PRIVATE_URLS=1`.
- Add Django user login before exposing publicly (views are open in this MVP).
- Threads are used for background work; move to Celery/RQ for production.

## Known limits (next steps)
2FA/captcha logins, SPA apps with no `<a>` links (needs click-based discovery), blur for sensitive data,
captions/zoom effects, better voices (ElevenLabs/OpenAI TTS), multi-user accounts.
