"""Shared helpers for finding, clicking and undoing UI interactions (used by explore AND record)."""
import re
from urllib.parse import urlparse

from django.conf import settings

# Labels we never click (destructive, or they would change the user's data).
SKIP_LABEL = re.compile(
    r"log ?out|sign ?out|delete|remove|destroy|deactivate|unsubscribe|checkout|\bpay\b|cancel|"
    r"submit|save|confirm|send|place order|update|apply|upload|import|reset|clear|approve|reject|"
    r"publish|archive|disable|purchase|buy|invite|transfer|withdraw|\bpost\b",
    re.I,
)

# Mutating requests to these paths are harmless reads (search boxes etc.).
SAFE_WRITE_PATH = re.compile(r"search|query|filter|autocomplete|suggest", re.I)

DIALOG_SEL = "[role=dialog]:not([aria-hidden=true]), dialog[open], .modal.show"

CANDIDATES_JS = """
() => {
  const vis = e => {
    const r = e.getBoundingClientRect(), s = getComputedStyle(e);
    return r.width > 4 && r.height > 4 && s.visibility !== 'hidden' && s.display !== 'none'
      && !e.disabled && e.getAttribute('aria-disabled') !== 'true';
  };
  const nameOf = e => (e.innerText || e.getAttribute('aria-label') || e.title || e.value || '')
      .trim().replace(/\\s+/g, ' ');
  const sel = 'button, [role=button], [role=tab], summary, input[type=button], a[href^="#"], ' +
              'a[data-bs-toggle], a[data-toggle], [aria-haspopup], [aria-expanded]';
  const out = [];
  document.querySelectorAll(sel).forEach(e => {
    if (!vis(e)) return;
    const type = (e.getAttribute('type') || '').toLowerCase();
    // Buttons inside a <form> default to "submit" -> skip, we must not submit forms.
    if (e.tagName === 'BUTTON' && e.closest('form') && (type === '' || type === 'submit')) return;
    if (type === 'submit') return;
    const explicit = e.getAttribute('role');
    const role = explicit || (e.tagName === 'A' ? 'link' : 'button');
    const name = nameOf(e);
    if (!name || name.length > 40) return;
    out.push({role, name});
  });
  return out;
}
"""

SNAPSHOT_JS = """
(dialogSel) => {
  const txt = (document.body.innerText || '').slice(0, 30000);
  let h = 0;
  for (let i = 0; i < txt.length; i++) h = (h * 31 + txt.charCodeAt(i)) | 0;
  const count = s => document.querySelectorAll(s).length;
  return {
    h, len: txt.length,
    dialogs: count(dialogSel),
    expanded: count('[aria-expanded=true]'),
    selected: count('[aria-selected=true]'),
  };
}
"""

INFO_JS = """
(scopeSel) => {
  const root = (scopeSel && document.querySelector(scopeSel)) || document;
  const vis = e => e.offsetParent !== null || getComputedStyle(e).position === 'fixed';
  const txt = e => (e.innerText || e.value || e.getAttribute('aria-label') || '').trim().replace(/\\s+/g, ' ');
  return {
    headings: Array.from(root.querySelectorAll('h1,h2,h3,h4,[role=heading]')).filter(vis).map(txt).filter(Boolean).slice(0, 6),
    actions: Array.from(root.querySelectorAll('button, [role=button], input[type=submit], [role=tab]'))
      .filter(vis).map(txt).filter(t => t && t.length < 40).slice(0, 12),
    fields: Array.from(root.querySelectorAll('input:not([type=hidden]), select, textarea'))
      .filter(vis).map(e => e.getAttribute('placeholder') || e.getAttribute('name') || e.type).filter(Boolean).slice(0, 10),
  };
}
"""


def same_url(a: str, b: str) -> bool:
    pa, pb = urlparse(a), urlparse(b)
    return (pa.netloc, pa.path.rstrip("/"), pa.query) == (pb.netloc, pb.path.rstrip("/"), pb.query)


def candidates(page, limit):
    """Clickable, non-destructive, de-duplicated controls on the current page."""
    seen, out = set(), []
    for c in page.evaluate(CANDIDATES_JS):
        key = (c["role"], c["name"])
        if key in seen or SKIP_LABEL.search(c["name"]):
            continue
        seen.add(key)
        out.append({"role": c["role"], "name": c["name"], "nth": 0})
        if len(out) >= limit:
            break
    return out


def locate(page, action):
    """Playwright locator for a stored action {role,name,nth}; falls back to visible text."""
    loc = page.get_by_role(action["role"], name=action["name"], exact=True)
    if loc.count() == 0:
        loc = page.get_by_text(action["name"], exact=True)
    if loc.count() == 0:
        return None
    return loc.nth(min(action.get("nth", 0), loc.count() - 1))


def snapshot(page):
    return page.evaluate(SNAPSHOT_JS, DIALOG_SEL)


def page_info(page, scope=None):
    return page.evaluate(INFO_JS, scope)


def dialog_open(page) -> bool:
    return page.locator(DIALOG_SEL).count() > 0


def settle(page, ms=900):
    page.wait_for_timeout(ms)
    try:
        page.wait_for_load_state("networkidle", timeout=3000)
    except Exception:
        pass


def close_dialog(page):
    page.keyboard.press("Escape")
    page.wait_for_timeout(400)
    if dialog_open(page):
        try:
            page.locator(DIALOG_SEL).get_by_role(
                "button", name=re.compile(r"close|cancel|dismiss|×|^x$|got it|ok", re.I)
            ).first.click(timeout=1500)
            page.wait_for_timeout(400)
        except Exception:
            pass


def apply_reset(page, page_url, action, method):
    """Undo an action so the next one starts from a clean page."""
    if method == "none":
        return
    if method == "escape":
        close_dialog(page)
    elif method == "toggle":
        loc = locate(page, action)
        if loc:
            loc.click(timeout=2000)
            page.wait_for_timeout(500)
    else:  # reload
        page.goto(page_url, wait_until="domcontentloaded", timeout=30000)
        settle(page, 500)


def find_reset(page, page_url, action, before):
    """Try the gentlest undo first; return the method that restored the original state."""
    if action["role"] == "tab":
        return "none"  # tabs just switch; no need to undo
    for method in ("escape", "toggle"):
        if method == "escape" and not dialog_open(page):
            continue
        if method == "toggle" and (dialog_open(page) or not same_url(page.url, page_url)):
            continue
        try:
            apply_reset(page, page_url, action, method)
            if same_url(page.url, page_url) and snapshot(page) == before:
                return method
        except Exception:
            pass
    apply_reset(page, page_url, action, "reload")
    return "reload"


def install_write_block(ctx):
    """Safe mode: abort POST/PUT/PATCH/DELETE so exploring can't change the user's data."""
    if not getattr(settings, "BLOCK_WRITES", True):
        return

    def handler(route):
        req = route.request
        if req.method in ("POST", "PUT", "PATCH", "DELETE") and not SAFE_WRITE_PATH.search(req.url):
            return route.abort()
        return route.continue_()

    ctx.route("**/*", handler)


def close_extra_pages(ctx, keep) -> int:
    """Close popups/new tabs opened by a click. Returns how many were closed."""
    extras = [p for p in ctx.pages if p is not keep]
    for p in extras:
        try:
            p.close()
        except Exception:
            pass
    return len(extras)
