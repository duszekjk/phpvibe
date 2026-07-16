from __future__ import annotations

from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from django.core.exceptions import ValidationError

from .config import load_site_config
from .models import EditSession, PageConversation


def normalize_page_url(url: str, allowed_hosts: frozenset[str]) -> str:
    try:
        parts = urlsplit(url.strip())
    except ValueError as exc:
        raise ValidationError("Adres jest nieprawidłowy.") from exc
    hostname = (parts.hostname or "").lower()
    if parts.scheme not in {"http", "https"} or hostname not in allowed_hosts:
        raise ValidationError("Ten adres nie należy do edytowanej strony.")
    try:
        parsed_port = parts.port
    except ValueError as exc:
        raise ValidationError("Adres zawiera nieprawidłowy port.") from exc
    port = f":{parsed_port}" if parsed_port else ""
    netloc = hostname + port
    path = parts.path or "/"
    query = urlencode(parse_qsl(parts.query, keep_blank_values=True), doseq=True)
    return urlunsplit((parts.scheme.lower(), netloc, path, query, ""))


def page_label(url: str) -> str:
    parts = urlsplit(url)
    if parts.query:
        return f"{parts.path or '/'}?{parts.query}"[:180]
    return (parts.path or "/")[:180]


def get_or_create_page_conversation(edit_session: EditSession, url: str) -> tuple[PageConversation, bool]:
    config = load_site_config(edit_session.site.config_key)
    normalized = normalize_page_url(url, config.allowed_hosts)
    return PageConversation.objects.get_or_create(
        session=edit_session,
        normalized_url=normalized,
        defaults={"target_url": normalized, "label": page_label(normalized)},
    )
