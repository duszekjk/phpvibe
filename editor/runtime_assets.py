from __future__ import annotations

import hashlib
from pathlib import Path

from django.conf import settings


ASSET_NAMES = frozenset({"app.css", "start-session.js", "workbench.css", "workbench.js"})


def asset_path(name: str) -> Path:
    if name not in ASSET_NAMES:
        raise KeyError(name)
    return settings.BASE_DIR / "editor" / "static" / "editor" / name


def asset_version(name: str) -> str:
    return hashlib.sha256(asset_path(name).read_bytes()).hexdigest()[:12]
