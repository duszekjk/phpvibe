import os
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = os.environ.get(
    "DJANGO_SECRET_KEY",
    "development-only-change-me-7tV!9xQ2#Lm5@Pr8$Dz4%Nk6^Hs1&Yc3",
)
DEBUG = os.environ.get("DJANGO_DEBUG", "0") == "1"
ALLOWED_HOSTS = [item.strip() for item in os.environ.get("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1").split(",") if item.strip()]
CSRF_TRUSTED_ORIGINS = [item.strip() for item in os.environ.get("DJANGO_CSRF_TRUSTED_ORIGINS", "").split(",") if item.strip()]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "editor",
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

ROOT_URLCONF = "phpvibe.urls"
TEMPLATES = [{
    "BACKEND": "django.template.backends.django.DjangoTemplates",
    "DIRS": [BASE_DIR / "templates"],
    "APP_DIRS": True,
    "OPTIONS": {"context_processors": [
        "django.template.context_processors.request",
        "django.contrib.auth.context_processors.auth",
        "django.contrib.messages.context_processors.messages",
    ]},
}]
WSGI_APPLICATION = "phpvibe.wsgi.application"

DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": BASE_DIR / "db.sqlite3"}}
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "pl"
TIME_ZONE = "Europe/Warsaw"
USE_I18N = True
USE_TZ = True
STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "dashboard"
LOGOUT_REDIRECT_URL = "login"

SITE_CONFIG_DIR = Path(os.environ.get("VIBE_SITE_CONFIG_DIR", BASE_DIR / "site_configs")).resolve()
WORKSPACE_ROOT = Path(os.environ.get("VIBE_WORKSPACE_ROOT", BASE_DIR / "var" / "workspaces")).resolve()
OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-5.6-terra")
OPENAI_MAX_TOOL_ROUNDS = int(os.environ.get("OPENAI_MAX_TOOL_ROUNDS", "12"))
FILE_MAX_BYTES = int(os.environ.get("VIBE_FILE_MAX_BYTES", str(512 * 1024)))
IMAGE_UPLOAD_MAX_BYTES = int(os.environ.get("VIBE_IMAGE_UPLOAD_MAX_BYTES", str(20 * 1024 * 1024)))
PREVIEW_TOKEN_MAX_AGE = int(os.environ.get("VIBE_PREVIEW_TOKEN_MAX_AGE", str(8 * 60 * 60)))
PANEL_ORIGIN = os.environ.get("VIBE_PANEL_ORIGIN", "https://phpvibe.duszekjk.com").rstrip("/")

SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_SSL_REDIRECT = os.environ.get("DJANGO_SECURE_SSL_REDIRECT", "1") == "1"
SECURE_HSTS_SECONDS = int(os.environ.get("DJANGO_SECURE_HSTS_SECONDS", "31536000"))
SECURE_HSTS_INCLUDE_SUBDOMAINS = os.environ.get("DJANGO_SECURE_HSTS_INCLUDE_SUBDOMAINS", "0") == "1"
SECURE_HSTS_PRELOAD = os.environ.get("DJANGO_SECURE_HSTS_PRELOAD", "0") == "1"
SESSION_COOKIE_SECURE = os.environ.get("DJANGO_SECURE_COOKIES", "1") == "1"
CSRF_COOKIE_SECURE = os.environ.get("DJANGO_SECURE_COOKIES", "1") == "1"
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = "DENY"

# LaunchAgent redirects stderr to phpvibe-error.log. Keep Django's exception
# tracebacks on that stream instead of relying on Gunicorn's lifecycle-only
# messages. Python's logging formatter appends exc_info automatically, so an
# unhandled HTTP 500 includes the complete traceback after this first line.
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "diagnostic": {
            "format": "{asctime} {levelname} pid={process} {name} {module}:{lineno} {message}",
            "style": "{",
        },
    },
    "handlers": {
        "stderr": {
            "class": "logging.StreamHandler",
            "formatter": "diagnostic",
            "stream": "ext://sys.stderr",
        },
    },
    "root": {
        "handlers": ["stderr"],
        "level": os.environ.get("DJANGO_LOG_LEVEL", "INFO"),
    },
    "loggers": {
        "django": {
            "handlers": ["stderr"],
            "level": os.environ.get("DJANGO_LOG_LEVEL", "INFO"),
            "propagate": False,
        },
        "django.request": {
            "handlers": ["stderr"],
            "level": "ERROR",
            "propagate": False,
        },
        "django.security": {
            "handlers": ["stderr"],
            "level": "WARNING",
            "propagate": False,
        },
        "editor": {
            "handlers": ["stderr"],
            "level": os.environ.get("DJANGO_LOG_LEVEL", "INFO"),
            "propagate": False,
        },
    },
}
