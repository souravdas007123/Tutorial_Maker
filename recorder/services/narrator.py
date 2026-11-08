"""Write narration for each step — Claude (with the screenshot) if configured, else a template."""
import base64
import logging
from pathlib import Path

from django.conf import settings

log = logging.getLogger(__name__)

LANG_NAMES = {"en": "English", "hi": "Hindi (Devanagari script)"}

TEMPLATES = {
    "en": {
        "intro": "Welcome! In this tutorial we'll walk through {label}. ",
        "page": "This is the {label} section. ",
        "headings": "Here you'll find {items}. ",
        "actions": "You can use options like {items}. ",
        "click": "Now let's click on {name}. ",
        "dialog": "A window opens where you can work with {items}. ",
        "result": "This reveals {items}. ",
    },
    "hi": {
        "intro": "नमस्ते! इस ट्यूटोरियल में हम {label} के बारे में जानेंगे। ",
        "page": "यह {label} सेक्शन है। ",
        "headings": "यहाँ आपको {items} दिखेगा। ",
        "actions": "आप {items} जैसे विकल्प इस्तेमाल कर सकते हैं। ",
        "click": "अब हम {name} पर क्लिक करते हैं। ",
        "dialog": "यहाँ एक विंडो खुलती है जिसमें आप {items} देख सकते हैं। ",
        "result": "इससे {items} दिखाई देता है। ",
    },
}


def _fallback(step, lang, first):
    t = TEMPLATES.get(lang, TEMPLATES["en"])
    if step.get("kind") == "action":
        name = step["action"]["name"]
        text = t["click"].format(name=name)
        items = step["fields"][:3] or step["headings"][:3] or step["actions"][:3]
        if items:
            key = "dialog" if step.get("opens_dialog") else "result"
            text += t[key].format(items=", ".join(items))
        return text.strip()
    text = t["intro"].format(label=step["label"]) if first else t["page"].format(label=step["label"])
    if step["headings"]:
        text += t["headings"].format(items=", ".join(step["headings"][:3]))
    if step["actions"]:
        text += t["actions"].format(items=", ".join(step["actions"][:4]))
    return text.strip()


def _claude_client():
    if not settings.ANTHROPIC_API_KEY:
        return None
    try:
        import anthropic

        return anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)
    except Exception:  # package missing
        return None


def _claude_narration(client, step, lang, first, last, image_path):
    lang_name = LANG_NAMES.get(lang, "English")
    kind_line = ""
    if step.get("kind") == "action":
        kind_line = (
            f"This step is a CLICK: the presenter clicks the {step['action']['role']} "
            f"\"{step['action']['name']}\" on the \"{step['parent']}\" page"
            f"{' and a dialog/popup opens' if step.get('opens_dialog') else ''}"
            f"{' and it navigates to a new screen' if step.get('navigated') else ''}. "
            "The screenshot shows the result AFTER the click. Say what to click, then what it does/shows.\n"
        )
    prompt = (
        f"You are writing the voice-over for a software tutorial video. Language: {lang_name}.\n"
        f"Write 2-4 short, friendly sentences (max 70 words) explaining this screen to a new user: "
        f"what it is for and what they can do here. Speak naturally, no markdown, no emojis, "
        f"no URLs.{' This is the first screen: start with a short welcome.' if first else ''}"
        f"{' This is the last screen: end with a one-line wrap-up.' if last else ''}\n\n"
        f"{kind_line}"
        f"Page name: {step['label']}\nTitle: {step['title']}\n"
        f"Headings: {step['headings']}\nButtons/tabs: {step['actions']}\nInput fields: {step['fields']}\n"
        "Never read out any personal data visible in the screenshot."
    )
    content = []
    img = Path(image_path)
    if img.exists():
        content.append(
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": "image/png",
                    "data": base64.b64encode(img.read_bytes()).decode(),
                },
            }
        )
    content.append({"type": "text", "text": prompt})
    msg = client.messages.create(
        model=settings.CLAUDE_MODEL,
        max_tokens=300,
        messages=[{"role": "user", "content": content}],
    )
    return "".join(b.text for b in msg.content if b.type == "text").strip()


def add_narration(steps, lang):
    client = _claude_client()
    for i, step in enumerate(steps):
        first, last = i == 0, i == len(steps) - 1
        text = ""
        if client:
            try:
                text = _claude_narration(
                    client, step, lang, first, last, Path(settings.MEDIA_ROOT) / step["screenshot"]
                )
            except Exception as e:  # network / quota — fall back
                log.warning("Claude narration failed for step %s: %s", i, e)
        step["narration"] = text or _fallback(step, lang, first)
    return steps
