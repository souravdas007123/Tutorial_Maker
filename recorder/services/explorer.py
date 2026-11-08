"""Pass 1: log in, discover pages, then CLICK every safe control on each page to find features."""
import re
from pathlib import Path
from urllib.parse import urljoin, urlparse

from django.conf import settings
from playwright.sync_api import TimeoutError as PWTimeout
from playwright.sync_api import sync_playwright

from . import interact

DANGEROUS = interact.SKIP_LABEL

NAV_JS = """
() => {
  const sel = 'nav a, aside a, [role=navigation] a, [class*=sidebar] a, [class*=menu] a, header a';
  let els = Array.from(document.querySelectorAll(sel));
  if (els.length < 2) els = Array.from(document.querySelectorAll('a[href]'));
  return els.filter(e => e.offsetParent !== null).map(e => ({
    label: (e.innerText || e.getAttribute('aria-label') || e.title || '').trim().replace(/\\s+/g, ' '),
    href: e.getAttribute('href') || ''
  }));
}
"""


class LoginError(Exception):
    pass


def login(page, url, username, password, timeout=15000):
    """Generic login: find the password field, fill username + password, submit."""
    page.goto(url, wait_until="domcontentloaded", timeout=30000)
    user_sel = "input[type=email], input[type=text], input:not([type])"
    try:
        page.locator("input[type=password]:visible, " + user_sel).first.wait_for(timeout=timeout)
    except PWTimeout:
        raise LoginError("Could not find a login form on that page.")

    user_box = page.locator(user_sel).locator("visible=true").first
    pw_box = page.locator("input[type=password]").locator("visible=true")

    if user_box.count():
        user_box.fill(username)
    # Two-step logins: the password box appears after submitting the username.
    if pw_box.count() == 0:
        page.keyboard.press("Enter")
        try:
            page.locator("input[type=password]").locator("visible=true").first.wait_for(timeout=timeout)
        except PWTimeout:
            raise LoginError("Password field never appeared.")
        pw_box = page.locator("input[type=password]").locator("visible=true")

    pw_box.first.fill(password)
    pw_box.first.press("Enter")
    try:
        page.wait_for_load_state("networkidle", timeout=timeout)
    except PWTimeout:
        pass

    if page.locator("input[type=password]").locator("visible=true").count():
        raise LoginError(
            "Still on the login form after submitting — wrong credentials, 2FA or a captcha?"
        )


def discover_nav(page, max_pages):
    """Unique same-origin nav links, skipping dangerous-looking ones."""
    origin = urlparse(page.url).netloc
    seen, out = set(), []
    for item in page.evaluate(NAV_JS):
        label, href = item["label"], item["href"]
        if not label or len(label) > 40 or DANGEROUS.search(label) or DANGEROUS.search(href):
            continue
        if href.startswith(("#", "mailto:", "tel:", "javascript:")):
            continue
        full = urljoin(page.url, href)
        parsed = urlparse(full)
        if parsed.netloc != origin:
            continue
        key = parsed.path.rstrip("/") or "/"
        if key in seen:
            continue
        seen.add(key)
        out.append({"label": label, "url": full})
        if len(out) >= max_pages:
            break
    return out


def _step(idx, kind, label, url, rel_dir, page, info, parent="", action=None, reset="none"):
    return {
        "id": idx,
        "kind": kind,  # "page" or "action"
        "label": label,
        "parent": parent,
        "url": url,  # the PAGE url this step happens on
        "action": action,  # {role,name,nth} for kind=action
        "reset": reset,
        "screenshot": f"{rel_dir}/shot_{idx}.png",
        "title": page.title(),
        "headings": info["headings"],
        "actions": info["actions"],
        "fields": info["fields"],
        "narration": "",
        "include": True,
    }


def _try_action(page, ctx, target, cand, idx, out_dir, rel_dir):
    """Click one control. Returns a step dict if it visibly did something, else None."""
    page_url = target["url"]
    loc = interact.locate(page, cand)
    if loc is None:
        return None
    before = interact.snapshot(page)
    url_before = page.url
    try:
        loc.scroll_into_view_if_needed(timeout=2000)
        loc.click(timeout=3000)
    except Exception:
        return None
    interact.settle(page)

    if interact.close_extra_pages(ctx, page):
        return None  # opened an external tab/popup — not a feature we can show in-page
    after = interact.snapshot(page)
    navigated = not interact.same_url(page.url, url_before)
    if not navigated and after == before:
        return None  # nothing changed

    new_dialog = after["dialogs"] > before["dialogs"]
    info = interact.page_info(page, interact.DIALOG_SEL if new_dialog else None)
    page.screenshot(path=str(out_dir / f"shot_{idx}.png"))
    label = f"{target['label']} → {cand['name']}"
    step = _step(idx, "action", label, page_url, rel_dir, page, info, parent=target["label"], action=cand)
    step["opens_dialog"] = new_dialog
    step["navigated"] = navigated
    step["reset"] = interact.find_reset(page, page_url, cand, before)
    return step


def explore(url, username, password, out_dir: Path, rel_dir: str, max_pages=None):
    """Log in, screenshot each page, then click each safe control on it. Returns step dicts."""
    max_pages = max_pages or settings.MAX_PAGES
    out_dir.mkdir(parents=True, exist_ok=True)
    w, h = settings.VIDEO_SIZE
    steps = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(viewport={"width": w, "height": h})
        page = ctx.new_page()
        page.on("dialog", lambda d: d.dismiss())  # confirm()/alert() -> cancel, never accept
        login(page, url, username, password)
        interact.install_write_block(ctx)  # login done; from here on nothing may change data

        landing = page.url
        nav = discover_nav(page, max_pages)
        targets = [{"label": "Dashboard", "url": landing}] + [
            n for n in nav if not interact.same_url(n["url"], landing)
        ]

        for t in targets[: max_pages + 1]:
            if len(steps) >= settings.MAX_STEPS:
                break
            try:
                if not interact.same_url(page.url, t["url"]):
                    page.goto(t["url"], wait_until="domcontentloaded", timeout=30000)
                interact.settle(page, 500)
                idx = len(steps)
                page.screenshot(path=str(out_dir / f"shot_{idx}.png"))
                steps.append(_step(idx, "page", t["label"], t["url"], rel_dir, page, interact.page_info(page)))
                cands = interact.candidates(page, settings.MAX_ACTIONS_PER_PAGE)
            except Exception:
                continue

            for cand in cands:
                if len(steps) >= settings.MAX_STEPS:
                    break
                try:
                    step = _try_action(page, ctx, t, cand, len(steps), out_dir, rel_dir)
                except Exception:
                    step = None
                    try:  # recover to a clean page
                        page.goto(t["url"], wait_until="domcontentloaded", timeout=30000)
                    except Exception:
                        break
                if step:
                    steps.append(step)
        browser.close()

    if not steps:
        raise LoginError("Logged in, but no pages could be explored.")
    return steps


def save_session(url, username, password, state_path: Path):
    """Log in (unrecorded) and persist cookies so the recorded pass never shows the password."""
    w, h = settings.VIDEO_SIZE
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(viewport={"width": w, "height": h})
        page = ctx.new_page()
        login(page, url, username, password)
        ctx.storage_state(path=str(state_path))
        browser.close()
