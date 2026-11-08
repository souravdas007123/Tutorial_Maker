"""Pass 1: log in to the target app and discover its pages/features."""
import re
from pathlib import Path
from urllib.parse import urljoin, urlparse

from django.conf import settings
from playwright.sync_api import TimeoutError as PWTimeout
from playwright.sync_api import sync_playwright

# "Safe mode": never navigate to links that look destructive.
DANGEROUS = re.compile(
    r"log ?out|sign ?out|delete|remove|destroy|deactivate|unsubscribe|checkout|\bpay\b|cancel",
    re.I,
)

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

PAGE_INFO_JS = """
() => {
  const vis = e => e.offsetParent !== null;
  const txt = e => (e.innerText || e.value || e.getAttribute('aria-label') || '').trim().replace(/\\s+/g, ' ');
  return {
    headings: Array.from(document.querySelectorAll('h1,h2,h3')).filter(vis).map(txt).filter(Boolean).slice(0, 6),
    actions: Array.from(document.querySelectorAll('button, [role=button], input[type=submit], [role=tab]'))
      .filter(vis).map(txt).filter(t => t && t.length < 40).slice(0, 12),
    fields: Array.from(document.querySelectorAll('input:not([type=hidden]), select, textarea'))
      .filter(vis).map(e => e.getAttribute('placeholder') || e.getAttribute('name') || e.type).filter(Boolean).slice(0, 10),
  };
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


def discover_nav(page, base_url, max_pages):
    """Return unique same-origin nav links, skipping dangerous-looking ones."""
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


def explore(url, username, password, out_dir: Path, rel_dir: str, max_pages=None):
    """Log in, screenshot the landing page + each nav target. Returns a list of step dicts."""
    max_pages = max_pages or settings.MAX_PAGES
    out_dir.mkdir(parents=True, exist_ok=True)
    w, h = settings.VIDEO_SIZE
    steps = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(viewport={"width": w, "height": h})
        page = ctx.new_page()
        login(page, url, username, password)

        landing = page.url
        nav = discover_nav(page, url, max_pages)
        targets = [{"label": "Dashboard", "url": landing}] + [
            n for n in nav if n["url"].rstrip("/") != landing.rstrip("/")
        ]

        for i, t in enumerate(targets[: max_pages + 1]):
            try:
                if page.url != t["url"]:
                    page.goto(t["url"], wait_until="domcontentloaded", timeout=30000)
                try:
                    page.wait_for_load_state("networkidle", timeout=8000)
                except PWTimeout:
                    pass
                shot = out_dir / f"shot_{i}.png"
                page.screenshot(path=str(shot))
                info = page.evaluate(PAGE_INFO_JS)
            except Exception:
                continue  # skip pages that fail to load
            steps.append(
                {
                    "id": i,
                    "label": t["label"],
                    "url": t["url"],
                    "screenshot": f"{rel_dir}/shot_{i}.png",
                    "title": page.title(),
                    "headings": info["headings"],
                    "actions": info["actions"],
                    "fields": info["fields"],
                    "narration": "",
                    "include": True,
                }
            )
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
