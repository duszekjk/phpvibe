from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit

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


def add_preview_token(url: str, token: str, session_id) -> str:
    parts = urlsplit(url)
    marker = f"/{session_id}/"
    if marker not in parts.path:
        raise ValueError("Adres podglądu nie zawiera identyfikatora kopii.")
    prefix, suffix = parts.path.split(marker, 1)
    path = f"{prefix}{marker}__vibe_token/{quote(token, safe='')}/{suffix}"
    query = parse_qsl(parts.query, keep_blank_values=True)
    query = [(key, value) for key, value in query if key != "__vibe_token"]
    # Query parameter keeps preview bridges already copied into existing
    # workspaces compatible. Apache authorizes every resource from the signed
    # path, so this no longer relies on third-party cookies in an iframe.
    query.append(("__vibe_token", token))
    return urlunsplit((parts.scheme, parts.netloc, path, urlencode(query), parts.fragment))
