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
1. **Exploring** – headless login, finds nav links, then on EVERY page clicks each safe control
   (buttons, tabs, dropdowns, accordions, popup openers). A click that changes the screen becomes its own
   "action" step with a screenshot; the page is then reset (Esc / toggle / reload) before the next click.
   Skipped: Logout/Delete/Save/Submit/Pay/Send… labels, form-submit buttons, external tabs.
2. **Approval** – you untick pages and edit narration.
3. **Recording** – logs in again *off-camera* (password never in the video), records with a visible cursor,
   gTTS voice-over per page, ffmpeg mixes → `media/jobs/<id>/tutorial.mp4`.

## Safe mode (important)
`TUTORIAL_BLOCK_WRITES=1` (default) aborts POST/PUT/PATCH/DELETE while exploring and recording, and browser
confirm() dialogs are dismissed, so clicking can't create/edit/delete data. Search/filter requests are allowed.
If your app needs POST just to load data (GraphQL), set `TUTORIAL_BLOCK_WRITES=0` and use a throw-away demo account.
Limits: `TUTORIAL_MAX_PAGES` (8), `TUTORIAL_MAX_ACTIONS` per page (10), `TUTORIAL_MAX_STEPS` total (40).

## Security notes
- Credentials are Fernet-encrypted and wiped when the job finishes/fails. Set `TUTORIAL_FERNET_KEY` in production.
- Private/loopback URLs are rejected (SSRF guard). For local testing: `TUTORIAL_ALLOW_PRIVATE_URLS=1`.
- Add Django user login before exposing publicly (views are open in this MVP).
- Threads are used for background work; move to Celery/RQ for production.

## Known limits (next steps)
2FA/captcha logins, SPA apps with no `<a>` links (needs click-based discovery), blur for sensitive data,
captions/zoom effects, better voices (ElevenLabs/OpenAI TTS), multi-user accounts.
