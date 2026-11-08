"""Pass 2: replay the approved plan (page visits AND button clicks) while recording + narrating."""
import subprocess
import time
from pathlib import Path

from django.conf import settings
from playwright.sync_api import sync_playwright

from . import explorer, interact, tts

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
  document.addEventListener('mousedown', e => {
    const r = document.createElement('div');
    r.style.cssText = 'position:fixed;z-index:2147483646;pointer-events:none;border:3px solid #ff5a3c;' +
      'border-radius:50%;width:10px;height:10px;transform:translate(-50%,-50%);opacity:1;' +
      'left:' + e.clientX + 'px;top:' + e.clientY + 'px;transition:all .5s ease-out';
    document.documentElement.appendChild(r);
    requestAnimationFrame(() => { r.style.width = '60px'; r.style.height = '60px'; r.style.opacity = '0'; });
    setTimeout(() => r.remove(), 600);
  }, true);
})();
"""

HIGHLIGHT_ON = "e => { e.__old = e.style.outline; e.style.outline = '3px solid #ff5a3c'; e.style.outlineOffset = '3px'; }"
HIGHLIGHT_OFF = "e => { e.style.outline = e.__old || ''; }"

MIN_STEP_SECONDS = 4.0
PAD_SECONDS = 0.8


def _glide(page, x, y, steps=25):
    page.mouse.move(x, y, steps=steps)


def _hold(page, started, hold_seconds):
    remaining = hold_seconds - (time.monotonic() - started)
    if remaining > 0:
        page.wait_for_timeout(int(remaining * 1000))


def _ensure_on_page(page, url):
    if not interact.same_url(page.url, url):
        page.goto(url, wait_until="domcontentloaded", timeout=30000)
    interact.settle(page, 400)


def _show_page(page, step, hold_seconds):
    """Page step: visit it, point at a few controls, scroll if long, hold for the narration."""
    w, h = settings.VIDEO_SIZE
    started = time.monotonic()
    _ensure_on_page(page, step["url"])
    _glide(page, w * 0.5, h * 0.35)
    try:
        buttons = page.locator("button:visible, [role=tab]:visible")
        for i in range(min(2, buttons.count())):
            box = buttons.nth(i).bounding_box()
            if box:
                _glide(page, box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
                page.wait_for_timeout(500)
    except Exception:
        pass
    try:
        scroll_h = page.evaluate("document.documentElement.scrollHeight")
        if scroll_h > h * 1.3 and hold_seconds > 5:
            for _ in range(3):
                page.mouse.wheel(0, (scroll_h - h) / 3)
                page.wait_for_timeout(900)
    except Exception:
        pass
    _hold(page, started, hold_seconds)


def _show_action(page, ctx, step, hold_seconds):
    """Action step: move to the control, highlight it, click it, show the result, then undo."""
    w, h = settings.VIDEO_SIZE
    started = time.monotonic()
    action = step["action"]
    _ensure_on_page(page, step["url"])

    loc = interact.locate(page, action)
    if loc is None:  # control not found this time — fall back to just showing the page
        _hold(page, started, hold_seconds)
        return
    try:
        loc.scroll_into_view_if_needed(timeout=2000)
        box = loc.bounding_box()
        if box:
            _glide(page, box["x"] + box["width"] / 2, box["y"] + box["height"] / 2, steps=35)
        try:
            loc.evaluate(HIGHLIGHT_ON)
        except Exception:
            pass
        page.wait_for_timeout(900)  # let the viewer see what we're about to click
        loc.click(timeout=3000)
        interact.settle(page, 900)
        interact.close_extra_pages(ctx, page)
        try:
            page.evaluate("document.querySelectorAll('*').forEach(e => { if (e.__old !== undefined) e.style.outline = e.__old; })")
        except Exception:
            pass
        if interact.dialog_open(page):  # draw the eye to the popup
            dbox = page.locator(interact.DIALOG_SEL).first.bounding_box()
            if dbox:
                _glide(page, dbox["x"] + dbox["width"] / 2, dbox["y"] + min(dbox["height"] / 3, 150))
    except Exception:
        pass

    _hold(page, started, hold_seconds)
    try:
        interact.apply_reset(page, step["url"], action, step.get("reset", "reload"))
    except Exception:
        try:
            page.goto(step["url"], wait_until="domcontentloaded", timeout=30000)
        except Exception:
            pass


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

    # 1) Narration audio first, so we know how long to stay on each step.
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
        interact.install_write_block(ctx)  # safe mode: clicks must not change the user's data
        page = ctx.new_page()
        page.on("dialog", lambda d: d.dismiss())
        t0 = time.monotonic()
        page.wait_for_timeout(600)
        for s, mp3, hold in prepared:
            start = time.monotonic() - t0
            if mp3:
                clips.append((mp3, start + 0.2))
            try:
                if s.get("kind") == "action" and s.get("action"):
                    _show_action(page, ctx, s, hold)
                else:
                    _show_page(page, s, hold)
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
