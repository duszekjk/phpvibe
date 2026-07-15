from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from django.conf import settings
from django.core import signing


SALT = "phpvibe.preview-access.v1"


def make_preview_token(edit_session, user) -> str:
    return signing.dumps(
        {"session_id": str(edit_session.pk), "user_id": str(user.pk)},
        salt=SALT,
        compress=True,
    )


def verify_preview_token(token: str, session_id) -> int | None:
    try:
        payload = signing.loads(token, salt=SALT, max_age=settings.PREVIEW_TOKEN_MAX_AGE)
    except signing.BadSignature:
        return None
    if payload.get("session_id") != str(session_id):
        return None
    try:
        return int(payload["user_id"])
    except (KeyError, TypeError, ValueError):
        return None


def add_preview_token(url: str, token: str) -> str:
    parts = urlsplit(url)
    query = parse_qsl(parts.query, keep_blank_values=True)
    query = [(key, value) for key, value in query if key != "__vibe_token"]
    query.append(("__vibe_token", token))
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))
