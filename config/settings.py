import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "dev-only-change-me")
DEBUG = os.environ.get("DJANGO_DEBUG", "1") == "1"
ALLOWED_HOSTS = os.environ.get("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1").split(",")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "recorder",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ]
        },
    }
]

WSGI_APPLICATION = "config.wsgi.application"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",
        "OPTIONS": {"timeout": 20},
    }
}

AUTH_PASSWORD_VALIDATORS = []
LANGUAGE_CODE = "en-us"
TIME_ZONE = "Asia/Kolkata"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

LOGIN_URL = "/admin/login/"
LOGIN_REDIRECT_URL = "/"

# ---- Tutorial maker settings ----
# Optional: a real Fernet key (Fernet.generate_key()). If empty, one is derived from SECRET_KEY.
FERNET_KEY = os.environ.get("TUTORIAL_FERNET_KEY", "")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
CLAUDE_MODEL = os.environ.get("CLAUDE_MODEL", "claude-sonnet-5-5")
MAX_PAGES = int(os.environ.get("TUTORIAL_MAX_PAGES", "15"))          # nav pages to visit
MAX_ACTIONS_PER_PAGE = int(os.environ.get("TUTORIAL_MAX_ACTIONS", "10"))  # buttons/tabs clicked per page
MAX_STEPS = int(os.environ.get("TUTORIAL_MAX_STEPS", "60"))         # hard cap on total video steps
# Safe mode: abort POST/PUT/PATCH/DELETE while exploring/recording so clicks can't change data.
# Turn off (=0) only if your app needs POST for reading data (e.g. GraphQL) and you use a throw-away account.
BLOCK_WRITES = os.environ.get("TUTORIAL_BLOCK_WRITES", "1") == "1"
VIDEO_SIZE = (1280, 720)
# SSRF guard: reject URLs that resolve to private/loopback IPs unless this is True.
ALLOW_PRIVATE_URLS = os.environ.get("TUTORIAL_ALLOW_PRIVATE_URLS", "0") == "1"
