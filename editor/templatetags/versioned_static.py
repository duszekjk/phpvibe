from functools import lru_cache
import hashlib
from pathlib import Path

from django import template
from django.contrib.staticfiles import finders
from django.templatetags.static import static


register = template.Library()


@lru_cache(maxsize=128)
def _content_version(path: str) -> str:
    located = finders.find(path)
    if not located:
        return "missing"
    digest = hashlib.sha256(Path(located).read_bytes()).hexdigest()
    return digest[:12]


@register.simple_tag
def versioned_static(path: str) -> str:
    """Return a static URL whose cache key changes with the file contents."""
    return f"{static(path)}?v={_content_version(path)}"
