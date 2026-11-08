"""Pass 2: record the approved plan in a fresh browser, add a visible cursor, mix narration."""
import subprocess
import time
from pathlib import Path

from django.conf import settings
from playwright.sync_api import TimeoutError as PWTimeout
from playwright.sync_api import sync_playwright

from . import explorer, tts

# Visible cursor + click ripple, injected into every page.
CURSOR_JS = """
(() => {
  if (window.__tm) return; window.__tm = true;
  const c = document.createElement('div');
  c.style.cssText = 'position:fixed;z-index:2147483647;width:22px;height:22px;border-radius:50%;' +
    'background:rgba(255,80,60,.55);border:2px solid #fff;box-shadow:0 0 8px rgba(0,0,0,.4);' +
    'pointer-events:none;transform:translate(-50%,-50%);transition:left .05s,top .05s;left:-50px;top:-50px';
  const add = () => document.documentElement.appendChild(c);
  document.readyState === 'loading' ? document.addEventListener('DOMContentLoaded', add) : add();
  document.addEventListener('mousemove', e => { c.style.left = e.clientX + 'px'; c.style.top = e.clientY + 'px'; }, true);
})();
"""

MIN_STEP_SECONDS = 4.0
PAD_SECONDS = 0.8


def _glide(page, x, y, steps=25):
    page.mouse.move(x, y, steps=steps)


def _show_step(page, step, hold_seconds):
    """Navigate, move the cursor around, scroll if the page is long, then hold until time is up."""
    w, h = settings.VIDEO_SIZE
    started = time.monotonic()
    if page.url.rstrip("/") != step["url"].rstrip("/"):
        page.goto(step["url"], wait_until="domcontentloaded", timeout=30000)
    try:
        page.wait_for_load_state("networkidle", timeout=6000)
    except PWTimeout:
        pass

    _glide(page, w * 0.5, h * 0.35)
    # Point at the first couple of buttons so the viewer sees what's clickable.
    try:
        buttons = page.locator("button:visible, [role=tab]:visible")
        for i in range(min(2, buttons.count())):
            box = buttons.nth(i).bounding_box()
            if box:
                _glide(page, box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
                page.wait_for_timeout(500)
    except Exception:
        pass

    # Gentle scroll on long pages.
    try:
        scroll_h = page.evaluate("document.documentElement.scrollHeight")
        if scroll_h > h * 1.3 and hold_seconds > 5:
            for _ in range(3):
                page.mouse.wheel(0, (scroll_h - h) / 3)
                page.wait_for_timeout(900)
    except Exception:
        pass

    remaining = hold_seconds - (time.monotonic() - started)
    if remaining > 0:
        page.wait_for_timeout(int(remaining * 1000))


def _mix_audio(clips, out_path: Path, total_seconds: float):
    """clips = [(path, start_seconds)] -> one aac track with each clip delayed to its start."""
    if not clips:
        return False
    cmd = ["ffmpeg", "-y", "-loglevel", "error"]
    for path, _ in clips:
        cmd += ["-i", str(path)]
    parts = [f"[{i}:a]adelay={int(start * 1000)}|{int(start * 1000)}[a{i}]" for i, (_, start) in enumerate(clips)]
    labels = "".join(f"[a{i}]" for i in range(len(clips)))
    parts.append(f"{labels}amix=inputs={len(clips)}:normalize=0:duration=longest[out]")
    cmd += ["-filter_complex", ";".join(parts), "-map", "[out]", "-t", f"{total_seconds:.2f}", str(out_path)]
    return subprocess.run(cmd).returncode == 0


def record(job, username, password):
    """Record every included step. Returns path of the final mp4 (absolute)."""
    work = job.work_dir
    work.mkdir(parents=True, exist_ok=True)
    rec_dir = work / "rec"
    rec_dir.mkdir(exist_ok=True)
    w, h = settings.VIDEO_SIZE
    steps = [s for s in job.steps if s.get("include")]
    if not steps:
        raise RuntimeError("No steps selected.")

    # 1) Narration audio first, so we know how long to stay on each page.
    prepared = []
    for i, s in enumerate(steps):
        mp3 = work / f"voice_{i}.mp3"
        dur = tts.synthesize(s["narration"], job.language, mp3)
        has_audio = bool(dur) and mp3.exists()
        hold = max(MIN_STEP_SECONDS, (dur or 0) + PAD_SECONDS)
        prepared.append((s, mp3 if has_audio else None, hold))

    # 2) Log in OFF-camera and save cookies — the password never appears in the video.
    state = work / "state.json"
    explorer.save_session(job.url, username, password, state)

    # 3) Record with the saved session.
    clips = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(
            viewport={"width": w, "height": h},
            storage_state=str(state),
            record_video_dir=str(rec_dir),
            record_video_size={"width": w, "height": h},
        )
        ctx.add_init_script(CURSOR_JS)
        page = ctx.new_page()
        t0 = time.monotonic()
        page.wait_for_timeout(600)
        for s, mp3, hold in prepared:
            start = time.monotonic() - t0
            if mp3:
                clips.append((mp3, start + 0.2))
            try:
                _show_step(page, s, hold)
            except Exception:
                page.wait_for_timeout(int(hold * 1000))
        page.wait_for_timeout(800)
        total = time.monotonic() - t0
        video_path = Path(page.video.path())
        ctx.close()  # flushes the .webm
        browser.close()
    state.unlink(missing_ok=True)  # session cookies are sensitive too

    # 4) Mix audio + encode final mp4.
    final = work / "tutorial.mp4"
    mixed = work / "voice_mix.m4a"
    have_audio = _mix_audio(clips, mixed, total + 1)
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-i", str(video_path)]
    if have_audio:
        cmd += ["-i", str(mixed)]
    cmd += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p"]
    cmd += ["-c:a", "aac", "-shortest"] if have_audio else ["-an"]
    cmd += ["-movflags", "+faststart", str(final)]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0 or not final.exists():
        raise RuntimeError(f"ffmpeg failed: {r.stderr[-400:]}")
    return final
