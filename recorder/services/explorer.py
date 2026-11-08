"""Pass 1: log in, discover pages (incl. collapsed sidebar groups), CLICK every safe control,
and — in demo mode — fill forms with sample data and submit them."""
from pathlib import Path
from urllib.parse import urljoin, urlparse

from django.conf import settings
from playwright.sync_api import TimeoutError as PWTimeout
from playwright.sync_api import sync_playwright

from . import interact

DANGEROUS = interact.SKIP_LABEL

# Links inside nav/sidebar containers are collected even when hidden (collapsed submenus) —
# goto() works on them regardless. Generic page links are only used as a fallback.
NAV_JS = """
() => {
  const navSel = 'nav a, aside a, [role=navigation] a, [class*=sidebar] a, [class*=menu] a, header a';
  let els = Array.from(document.querySelectorAll(navSel));
  if (els.length < 2) els = Array.from(document.querySelectorAll('a[href]')).filter(e => e.offsetParent !== null);
  return els.map(e => ({
    label: (e.innerText || e.textContent || e.getAttribute('aria-label') || e.title || '')
              .trim().replace(/\\s+/g, ' '),
    href: e.getAttribute('href') || ''
  }));
}
"""

# Toggles that expand a collapsed sidebar / nav group (Bootstrap, AdminLTE, Tailwind, custom...).
NAV_TOGGLE_CSS = ", ".join(
    f"{scope} {sel}"
    for scope in ("nav", "aside", "[role=navigation]", "[class*=sidebar]", "[class*=side-nav]", "[class*=menu]")
    for sel in (
        "[aria-expanded=false]",
        "[data-bs-toggle=collapse]",
        "[data-toggle=collapse]",
        ".dropdown-toggle",
        ".has-submenu > a",
        ".has-sub > a",
        ".has-children > a",
        ".treeview > a",
        ".menu-item-has-children > a",
        ".submenu-toggle",
        "summary",
    )
)


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


def expand_nav_groups(page, rounds=3):
    """Open collapsed sidebar groups (also nested ones) so their child links become visible."""
    for _ in range(rounds):
        loc = page.locator(NAV_TOGGLE_CSS)
        clicked = 0
        for i in range(min(loc.count(), 40)):
            el = loc.nth(i)
            try:
                if not el.is_visible() or el.get_attribute("aria-expanded") == "true":
                    continue
                before = page.url
                el.click(timeout=1500)
                page.wait_for_timeout(250)
                if not interact.same_url(page.url, before):  # the toggle was a real link
                    page.go_back(wait_until="domcontentloaded")
                    page.wait_for_timeout(300)
                clicked += 1
            except Exception:
                continue
        if not clicked:
            break
    try:
        page.keyboard.press("Escape")  # close any user/profile dropdown we opened
    except Exception:
        pass


def discover_nav(page, max_pages):
    """Unique same-origin nav links (sidebar groups expanded first), skipping dangerous ones."""
    expand_nav_groups(page)
    origin = urlparse(page.url).netloc
    seen, out = set(), []
    for item in page.evaluate(NAV_JS):
        label, href = item["label"], item["href"]
        if not label or len(label) > 40 or DANGEROUS.search(label) or DANGEROUS.search(href):
            continue
        if href.startswith(("#", "mailto:", "tel:", "javascript:")) or not href:
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
        "kind": kind,  # "page" | "action" | "fill" | "submit"
        "label": label,
        "parent": parent,
        "url": url,  # the PAGE url this step happens on
        "action": action,  # {role,name,nth} for click/submit steps
        "reset": reset,
        "screenshot": f"{rel_dir}/shot_{idx}.png",
        "title": page.title(),
        "headings": info["headings"],
        "actions": info["actions"],
        "fields": info["fields"],
        "narration": "",
        "include": True,
    }


def _demo_form(page, scope, label, page_url, start_idx, out_dir, rel_dir):
    """Fill the form in `scope` (dialog selector, or None = whole page) and submit it.

    Returns [fill_step, submit_step?]; the last one carries reset="reload".
    """
    if len(interact.form_fields(page, scope)) < (1 if scope else 2):
        return []
    filled = interact.fill_form(page, scope, interact.new_token())
    if not filled:
        return []
    page.wait_for_timeout(400)
    page.screenshot(path=str(out_dir / f"shot_{start_idx}.png"))
    fill = _step(
        start_idx, "fill", f"{label} → fill the form", page_url, rel_dir, page,
        interact.page_info(page, scope), parent=label,
    )
    fill["scope"] = scope or ""
    fill["filled"] = filled
    steps = [fill]

    btn, name = interact.find_submit(page, scope)
    if btn is not None:
        try:
            btn.scroll_into_view_if_needed(timeout=2000)
            btn.click(timeout=3000)
            interact.settle(page, 1500)
            interact.close_extra_pages(page.context, page)
            page.screenshot(path=str(out_dir / f"shot_{start_idx + 1}.png"))
            sub = _step(
                start_idx + 1, "submit", f"{label} → {name}", page_url, rel_dir, page,
                interact.page_info(page), parent=label,
                action={"role": "button", "name": name, "nth": 0},
            )
            sub["scope"] = scope or ""
            sub["submit_name"] = name
            steps.append(sub)
        except Exception:
            pass
    steps[-1]["reset"] = "reload"
    return steps


def _try_action(page, ctx, target, cand, idx, out_dir, rel_dir, demo):
    """Click one control. Returns a list of steps (empty if it visibly did nothing)."""
    page_url = target["url"]
    loc = interact.locate(page, cand)
    if loc is None:
        return []
    before = interact.snapshot(page)
    url_before = page.url
    try:
        loc.scroll_into_view_if_needed(timeout=2000)
        loc.click(timeout=3000)
    except Exception:
        return []
    interact.settle(page)

    if interact.close_extra_pages(ctx, page):
        return []  # opened an external tab/popup — not a feature we can show in-page
    after = interact.snapshot(page)
    navigated = not interact.same_url(page.url, url_before)
    if not navigated and after == before:
        return []  # nothing changed

    new_dialog = after["dialogs"] > before["dialogs"]
    info = interact.page_info(page, interact.DIALOG_SEL if new_dialog else None)
    page.screenshot(path=str(out_dir / f"shot_{idx}.png"))
    label = f"{target['label']} → {cand['name']}"
    step = _step(idx, "action", label, page_url, rel_dir, page, info, parent=target["label"], action=cand)
    step["opens_dialog"] = new_dialog
    step["navigated"] = navigated

    if demo and (new_dialog or navigated):
        scope = interact.DIALOG_SEL if new_dialog else None
        extra = _demo_form(page, scope, label, page_url, idx + 1, out_dir, rel_dir)
        if extra:
            step["reset"] = "none"  # the form must stay open for the next steps
            interact.apply_reset(page, page_url, cand, "reload")
            return [step] + extra

    step["reset"] = interact.find_reset(page, page_url, cand, before)
    return [step]


def explore(url, username, password, out_dir: Path, rel_dir: str, max_pages=None, demo=False):
    """Log in, screenshot each page, click each safe control. Returns step dicts."""
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
        # Login done. Safe mode: nothing may change data. Demo mode: writes allowed.
        interact.install_write_block(ctx, block=not demo)

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
                if demo:  # this page itself is a form (e.g. "Add patient")
                    extra = _demo_form(page, None, t["label"], t["url"], len(steps), out_dir, rel_dir)
                    if extra:
                        steps.extend(extra)
                        interact.apply_reset(page, t["url"], None, "reload")
                cands = interact.candidates(page, settings.MAX_ACTIONS_PER_PAGE)
            except Exception:
                continue

            for cand in cands:
                if len(steps) >= settings.MAX_STEPS:
                    break
                try:
                    new = _try_action(page, ctx, t, cand, len(steps), out_dir, rel_dir, demo)
                except Exception:
                    new = []
                    try:  # recover to a clean page
                        page.goto(t["url"], wait_until="domcontentloaded", timeout=30000)
                    except Exception:
                        break
                steps.extend(new)
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
