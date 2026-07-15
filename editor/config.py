from dataclasses import dataclass
from fnmatch import fnmatch
from functools import lru_cache
from pathlib import Path
import tomllib
from urllib.parse import urlsplit, urlunsplit

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured


@dataclass(frozen=True)
class PreviewReplacement:
    path: str
    production_text: str
    preview_text: str
    required: bool = True


@dataclass(frozen=True)
class SiteConfig:
    key: str
    root_path: Path
    allowed_hosts: frozenset[str]
    preview_url_template: str
    description: str
    allowed_extensions: frozenset[str]
    ignored_names: frozenset[str]
    protected_paths: tuple[str, ...]
    preview_replacements: tuple[PreviewReplacement, ...]
    publish_enabled: bool
    backup_path: Path | None

    def preview_url(self, session_id, target_url: str | None = None) -> str:
        base = self.preview_url_template.format(session_id=session_id)
        if not target_url:
            return base
        base_parts = urlsplit(base)
        target_parts = urlsplit(target_url)
        base_path = base_parts.path.rstrip("/")
        target_path = target_parts.path or "/"
        path = base_path + (target_path if target_path.startswith("/") else f"/{target_path}")
        return urlunsplit((base_parts.scheme, base_parts.netloc, path, target_parts.query, ""))

    def is_protected(self, relative_path: str) -> bool:
        return any(fnmatch(relative_path, pattern) for pattern in self.protected_paths)


@lru_cache(maxsize=64)
def load_site_config(key: str) -> SiteConfig:
    if not key or not key.replace("-", "").replace("_", "").isalnum():
        raise ImproperlyConfigured("Nieprawidłowy klucz konfiguracji strony.")
    path = settings.SITE_CONFIG_DIR / f"{key}.toml"
    try:
        with path.open("rb") as handle:
            raw = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ImproperlyConfigured(f"Nie można wczytać konfiguracji {path}: {exc}") from exc

    root = Path(raw["root_path"]).expanduser().resolve()
    if not root.is_dir():
        raise ImproperlyConfigured(f"Katalog strony nie istnieje: {root}")
    hosts = frozenset(str(item).lower() for item in raw.get("allowed_hosts", []))
    if not hosts:
        raise ImproperlyConfigured("Konfiguracja musi zawierać allowed_hosts.")
    extensions = frozenset(
        item if str(item).startswith(".") else f".{item}"
        for item in raw.get("allowed_extensions", [".php", ".css", ".js", ".html", ".txt", ".md"])
    )
    backup = raw.get("backup_path")
    configured_protected = tuple(raw.get("protected_paths", [".env", ".env.*", "*secret*", "*credentials*"]))
    preview_replacements = tuple(
        PreviewReplacement(
            path=str(item["path"]),
            production_text=str(item["production_text"]),
            preview_text=str(item["preview_text"]),
            required=bool(item.get("required", True)),
        )
        for item in raw.get("preview_replacements", [])
    )
    return SiteConfig(
        key=key,
        root_path=root,
        allowed_hosts=hosts,
        preview_url_template=raw.get("preview_url_template", ""),
        description=raw.get("description", ""),
        allowed_extensions=extensions,
        ignored_names=frozenset(raw.get("ignored_names", [".git", ".DS_Store", "vendor", "node_modules"])),
        protected_paths=configured_protected + ("__phpvibe_preview", "__phpvibe_preview/*", "__phpvibe_preview/**"),
        preview_replacements=preview_replacements,
        publish_enabled=bool(raw.get("publish_enabled", False)),
        backup_path=Path(backup).expanduser().resolve() if backup else None,
    )
