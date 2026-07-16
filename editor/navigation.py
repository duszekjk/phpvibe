from __future__ import annotations

from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from django.core.exceptions import ValidationError

from .config import load_site_config
from .models import EditSession, PageConversation


def remove_preview_credentials(path: str, query: str) -> tuple[str, str]:
    """Remove preview-only authorization data that must never enter a public URL."""
    segments = path.split("/")
    try:
        marker_index = segments.index("__vibe_token")
    except ValueError:
        marker_index = -1
    if marker_index >= 0 and marker_index + 1 < len(segments):
        remaining = segments[marker_index + 2:]
        path = "/" + "/".join(remaining)
        if not remaining:
            path = "/"
    clean_query = urlencode(
        [(key, value) for key, value in parse_qsl(query, keep_blank_values=True) if key != "__vibe_token"],
        doseq=True,
    )
    return path, clean_query


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
    path, query = remove_preview_credentials(parts.path or "/", parts.query)
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


def repair_page_conversation(edit_session: EditSession, conversation: PageConversation) -> PageConversation:
    """Repair URLs saved by an obsolete preview script without losing their chat."""
    config = load_site_config(edit_session.site.config_key)
    normalized = normalize_page_url(conversation.target_url, config.allowed_hosts)
    normalized_session_url = normalize_page_url(edit_session.target_url, config.allowed_hosts)
    if edit_session.target_url != normalized_session_url:
        edit_session.target_url = normalized_session_url
        edit_session.save(update_fields=["target_url", "updated_at"])
    if conversation.target_url == normalized and conversation.normalized_url == normalized:
        return conversation
    duplicate = edit_session.conversations.filter(normalized_url=normalized).exclude(pk=conversation.pk).first()
    if duplicate:
        conversation.messages.update(conversation=duplicate)
        conversation.delete()
        return duplicate
    conversation.target_url = normalized
    conversation.normalized_url = normalized
    conversation.label = page_label(normalized)
    conversation.save(update_fields=["target_url", "normalized_url", "label", "updated_at"])
    return conversation
