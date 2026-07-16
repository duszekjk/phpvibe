from django import template
from django.urls import reverse

from editor.runtime_assets import asset_version


register = template.Library()


@register.simple_tag
def versioned_static(path: str) -> str:
    """Serve editor assets from the same release as the running Django code."""
    prefix = "editor/"
    if not path.startswith(prefix):
        raise ValueError("versioned_static obsługuje wyłącznie zasoby interfejsu edytora.")
    name = path.removeprefix(prefix)
    return f"{reverse('runtime_asset', kwargs={'name': name})}?v={asset_version(name)}"
