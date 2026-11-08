"""Shared helpers for finding, clicking and undoing UI interactions (used by explore AND record)."""
import random
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
    if (e.closest('nav, aside, [class*=sidebar], [role=navigation]')) return;  // handled by nav discovery
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
  const visible = e => { const r = e.getBoundingClientRect(), st = getComputedStyle(e); return r.width > 0 && r.height > 0 && st.visibility !== 'hidden' && st.display !== 'none'; };
  return {
    h, len: txt.length,
    dialogs: Array.from(document.querySelectorAll(dialogSel)).filter(visible).length,
    expanded: count('[aria-expanded=true]'),
    selected: count('[aria-selected=true]'),
  };
}
"""

INFO_JS = """
(scopeSel) => {
  const vis = e => e.offsetParent !== null || getComputedStyle(e).position === 'fixed';
  const root = (scopeSel && Array.from(document.querySelectorAll(scopeSel)).find(vis)) || document;
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
    return page.evaluate(SNAPSHOT_JS, DIALOG_SEL)["dialogs"] > 0


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
            _root(page, DIALOG_SEL).get_by_role(
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


def install_write_block(ctx, block=None):
    """Safe mode: abort POST/PUT/PATCH/DELETE so exploring can't change the user's data.

    block=None -> follow settings.BLOCK_WRITES; block=False (demo mode) -> allow writes.
    """
    if block is None:
        block = getattr(settings, "BLOCK_WRITES", True)
    if not block:
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


# ---------------------------------------------------------------------------
# Demo mode: fill forms with sample data and submit them (test accounts only!)
# ---------------------------------------------------------------------------
DESTRUCTIVE = re.compile(
    r"log ?out|sign ?out|delete|remove|destroy|deactivate|unsubscribe|checkout|\bpay\b|cancel|"
    r"purchase|buy|withdraw|transfer|archive|disable|reject|reset|clear|import|upload",
    re.I,
)
SUBMIT_OK = re.compile(r"save|submit|create|add|register|confirm|update|apply|book|schedule|done|finish", re.I)
SEARCHISH = re.compile(r"search|filter|query", re.I)

FIELD_CSS = (
    "input:not([type=hidden]):not([type=submit]):not([type=button]):not([type=file])"
    ":not([type=image]):not([type=reset]):not([type=search]), select, textarea"
)

META_JS = """
e => {
  const lab = (e.id && document.querySelector('label[for="' + CSS.escape(e.id) + '"]')) || e.closest('label');
  let labText = '';
  if (lab) { const c = lab.cloneNode(true); c.querySelectorAll('select,input,textarea,option').forEach(x => x.remove()); labText = c.textContent; }
  const label = (labText || e.getAttribute('aria-label') || e.getAttribute('placeholder') || e.name || '')
      .trim().replace(/\\s+/g, ' ').slice(0, 50);
  return {
    tag: e.tagName.toLowerCase(), type: (e.getAttribute('type') || 'text').toLowerCase(),
    name: e.name || '', id: e.id || '', placeholder: e.getAttribute('placeholder') || '', label,
    required: !!e.required, disabled: !!e.disabled, readonly: !!e.readOnly,
    min: e.min || '', max: e.max || '', options: e.tagName === 'SELECT' ? e.options.length : 0,
  };
}
"""


def new_token() -> str:
    """5 digits, makes sample emails/phones/names unique on every run (avoids duplicate errors)."""
    return str(random.randint(10000, 99999))


def _root(page, scope):
    """Locator for the scope (e.g. the open dialog) — the first VISIBLE match, else the first."""
    if not scope:
        return page.locator("body")
    loc = page.locator(scope)
    for i in range(min(loc.count(), 10)):
        try:
            if loc.nth(i).is_visible():
                return loc.nth(i)
        except Exception:
            pass
    return loc.first


def form_fields(page, scope=None):
    """Visible, enabled, fillable fields (search boxes excluded) in a dialog scope or the page."""
    loc = _root(page, scope).locator(FIELD_CSS).locator("visible=true")
    out = []
    for i in range(min(loc.count(), 25)):
        try:
            meta = loc.nth(i).evaluate(META_JS)
        except Exception:
            continue
        key = " ".join([meta["name"], meta["id"], meta["placeholder"], meta["label"]])
        if meta["disabled"] or meta["readonly"] or SEARCHISH.search(key):
            continue
        meta["i"] = i
        out.append(meta)
    return out


def sample_value(meta, token) -> str:
    key = " ".join([meta["name"], meta["id"], meta["placeholder"], meta["label"]]).lower()
    t = meta["type"]
    if t == "email" or "email" in key:
        return f"demo{token}@example.com"
    if t == "tel" or re.search(r"phone|mobile|contact no|whatsapp", key):
        return f"9{token}{token[:4]}"
    if t == "number":
        def _int(v, default):
            try:
                return int(float(v))
            except (TypeError, ValueError):
                return default

        lo, hi = _int(meta["min"], 1), _int(meta["max"], 10**9)
        return str(min(max(10, lo), hi))
    if t == "date":
        return "2026-11-08"
    if t == "datetime-local":
        return "2026-11-08T10:00"
    if t == "time":
        return "10:00"
    if t == "password":
        return "Demo@12345"
    if t == "url" or "website" in key:
        return "https://example.com"
    if "first" in key and "name" in key:
        return "Demo"
    if "last" in key and "name" in key:
        return f"User{token}"
    if re.search(r"zip|pin ?code|postal", key):
        return "110001"
    if meta["tag"] == "textarea" or re.search(r"description|note|comment|address|remark|details", key):
        return "Sample text entered for the tutorial demo."
    if "name" in key or "title" in key:
        return f"Demo {meta['label'] or 'Item'} {token}"[:60]
    return f"Demo {token}"


def fill_form(page, scope, token, slow=False, on_field=None):
    """Fill every fillable field with sample data. Returns [{label, value}] for narration."""
    loc = _root(page, scope).locator(FIELD_CSS).locator("visible=true")
    filled = []
    for meta in form_fields(page, scope):
        el = loc.nth(meta["i"])
        label = meta["label"] or meta["name"] or meta["type"]
        try:
            if on_field:
                on_field(el)
            if meta["tag"] == "select":
                if meta["options"] < 2:
                    continue
                el.select_option(index=1, timeout=2000)
                value = el.evaluate("e => (e.options[e.selectedIndex] || {}).text || ''").strip()
            elif meta["type"] == "checkbox":
                if not meta["required"]:
                    continue
                el.check(timeout=1500)
                value = "checked"
            elif meta["type"] == "radio":
                el.check(timeout=1500)
                value = "selected"
            else:
                value = sample_value(meta, token)
                if slow:
                    el.click(timeout=1500)
                    el.press_sequentially(value, delay=45)
                else:
                    el.fill(value, timeout=2000)
            filled.append({"label": label, "value": value})
        except Exception:
            continue
    return filled


def find_submit(page, scope=None):
    """Locate a safe 'Save/Submit/Create…' button. Returns (locator, name) or (None, '')."""
    root = _root(page, scope) if scope else page.locator("form")
    cands = root.locator("button, input[type=submit], [role=button]").locator("visible=true")
    for i in range(min(cands.count(), 20)):
        el = cands.nth(i)
        try:
            name = (el.inner_text() or el.get_attribute("value") or "").strip().replace("\n", " ")
        except Exception:
            continue
        if name and len(name) < 40 and SUBMIT_OK.search(name) and not DESTRUCTIVE.search(name):
            return el, name
    return None, ""
