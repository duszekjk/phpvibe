#!/private/var/www/phpvibe/.venv/bin/python
"""Apache RewriteMap helper for authorizing PHP Vibe preview requests."""

from __future__ import annotations

import os
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.parse import unquote_plus, urlencode
from urllib.request import Request, urlopen
from uuid import UUID


AUTH_ENDPOINT = os.environ.get(
    "PHPVIBE_AUTH_ENDPOINT",
    "http://127.0.0.1:8767/wewnetrzne/podglad/{session_id}/autoryzuj/",
)
PANEL_HOST = os.environ.get("PHPVIBE_PANEL_HOST", "phpvibe.duszekjk.com")
CACHE_SECONDS = float(os.environ.get("PHPVIBE_AUTH_CACHE_SECONDS", "10"))
MAX_CACHE_ENTRIES = 4096
_cache: dict[tuple[str, str], tuple[float, bool]] = {}


def parse_key(raw_key: str) -> tuple[str, str] | None:
    try:
        session_value, encoded_token = raw_key.strip().split(",", 1)
        session_id = str(UUID(session_value))
    except (ValueError, AttributeError):
        return None
    token = unquote_plus(encoded_token)
    if not token or len(token) > 4096 or "\n" in token or "\r" in token:
        return None
    return session_id, token


def _request_authorization(session_id: str, token: str) -> bool:
    url = AUTH_ENDPOINT.format(session_id=session_id)
    separator = "&" if "?" in url else "?"
    request = Request(
        f"{url}{separator}{urlencode({'token': token})}",
        headers={
            "Host": PANEL_HOST,
            "X-Forwarded-Proto": "https",
            "User-Agent": "phpvibe-apache-auth/1",
        },
    )
    try:
        with urlopen(request, timeout=2) as response:
            return response.status == 204
    except (HTTPError, URLError, TimeoutError, OSError):
        return False


def authorize(raw_key: str, *, now: float | None = None) -> bool:
    parsed = parse_key(raw_key)
    if parsed is None:
        return False
    current = time.monotonic() if now is None else now
    cached = _cache.get(parsed)
    if cached and cached[0] >= current:
        return cached[1]
    allowed = _request_authorization(*parsed)
    if len(_cache) >= MAX_CACHE_ENTRIES:
        _cache.clear()
    _cache[parsed] = (current + CACHE_SECONDS, allowed)
    return allowed


def main() -> None:
    for line in sys.stdin:
        try:
            result = "OK" if authorize(line) else "NULL"
        except Exception:
            result = "NULL"
        print(result, flush=True)


if __name__ == "__main__":
    main()
