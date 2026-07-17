from dataclasses import dataclass
from fnmatch import fnmatch
from functools import lru_cache
from pathlib import Path, PurePosixPath
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
    homepage_url: str
    preview_url_template: str
    description: str
    allowed_extensions: frozenset[str]
    ignored_names: frozenset[str]
    protected_paths: tuple[str, ...]
    preview_replacements: tuple[PreviewReplacement, ...]
    backup_path: Path
    asset_upload_path: str

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

    def is_uploaded_asset(self, relative_path: str) -> bool:
        return relative_path.startswith(f"{self.asset_upload_path}/")


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

    try:
        root_value = raw["root_path"]
    except KeyError as exc:
        raise ImproperlyConfigured("Konfiguracja musi zawierać root_path.") from exc
    root = Path(root_value).expanduser().resolve()
    if not root.is_dir():
        raise ImproperlyConfigured(f"Katalog strony nie istnieje: {root}")
    hosts = frozenset(str(item).strip().lower() for item in raw.get("allowed_hosts", []) if str(item).strip())
    if not hosts:
        raise ImproperlyConfigured("Konfiguracja musi zawierać allowed_hosts.")
    if any("://" in host or "/" in host or ":" in host for host in hosts):
        raise ImproperlyConfigured("allowed_hosts może zawierać tylko nazwy hostów, bez schematu, portu i ścieżki.")

    default_host = sorted(hosts, key=lambda host: (host.startswith("www."), len(host), host))[0]
    homepage_url = str(raw.get("homepage_url", f"https://{default_host}/")).strip()
    try:
        homepage_parts = urlsplit(homepage_url)
        homepage_parts.port
    except ValueError as exc:
        raise ImproperlyConfigured("homepage_url jest nieprawidłowy.") from exc
    if (
        homepage_parts.scheme not in {"http", "https"}
        or (homepage_parts.hostname or "").lower() not in hosts
        or homepage_parts.username
        or homepage_parts.password
        or homepage_parts.fragment
    ):
        raise ImproperlyConfigured("homepage_url musi być adresem skonfigurowanej strony, bez danych logowania i fragmentu.")

    preview_template = str(raw.get("preview_url_template", "")).strip()
    if preview_template.count("{session_id}") != 1:
        raise ImproperlyConfigured("preview_url_template musi zawierać dokładnie jeden placeholder {session_id}.")
    try:
        preview_parts = urlsplit(preview_template.format(session_id="00000000-0000-0000-0000-000000000000"))
        preview_parts.port
    except (ValueError, KeyError) as exc:
        raise ImproperlyConfigured("preview_url_template jest nieprawidłowy.") from exc
    if (
        preview_parts.scheme not in {"http", "https"}
        or not preview_parts.hostname
        or preview_parts.username
        or preview_parts.password
        or preview_parts.query
        or preview_parts.fragment
    ):
        raise ImproperlyConfigured(
            "preview_url_template musi być pełnym adresem HTTP(S), bez danych logowania, zapytania i fragmentu."
        )

    extensions = set()
    for item in raw.get("allowed_extensions", [".php", ".css", ".js", ".html", ".txt", ".md"]):
        extension = str(item).strip().lower()
        if not extension or "/" in extension or extension in {".", ".."}:
            raise ImproperlyConfigured(f"Nieprawidłowe rozszerzenie pliku: {item!r}")
        extensions.add(extension if extension.startswith(".") else f".{extension}")

    ignored_names = frozenset(str(item).strip() for item in raw.get("ignored_names", [".git", ".DS_Store", "vendor", "node_modules"]))
    if any(not item or item in {".", ".."} or "/" in item for item in ignored_names):
        raise ImproperlyConfigured("ignored_names może zawierać tylko pojedyncze nazwy plików lub katalogów.")

    backup = str(raw.get("backup_path", "")).strip()
    if not backup:
        raise ImproperlyConfigured("Konfiguracja musi zawierać backup_path wymagany do bezpiecznej publikacji.")
    backup_path = Path(backup).expanduser().resolve()
    if backup_path == root or root in backup_path.parents:
        raise ImproperlyConfigured("backup_path musi znajdować się poza katalogiem strony produkcyjnej.")
    configured_protected = tuple(raw.get("protected_paths", [".env", ".env.*", "*secret*", "*credentials*"]))
    asset_upload_path = str(raw.get("asset_upload_path", "pliki/images/phpvibe")).strip().strip("/")
    upload_parts = PurePosixPath(asset_upload_path)
    if (
        not asset_upload_path
        or upload_parts.is_absolute()
        or any(part in {"", ".", ".."} for part in upload_parts.parts)
        or "\\" in asset_upload_path
    ):
        raise ImproperlyConfigured("asset_upload_path musi być bezpieczną ścieżką względną w katalogu strony.")
    if any(fnmatch(f"{asset_upload_path}/test.webp", pattern) for pattern in configured_protected):
        raise ImproperlyConfigured("asset_upload_path nie może wskazywać chronionego katalogu.")
    preview_replacements_list = []
    seen_replacements = set()
    for item in raw.get("preview_replacements", []):
        replacement = PreviewReplacement(
            path=str(item["path"]),
            production_text=str(item["production_text"]),
            preview_text=str(item["preview_text"]),
            required=bool(item.get("required", True)),
        )
        if replacement in seen_replacements:
            continue
        seen_replacements.add(replacement)
        preview_replacements_list.append(replacement)
    preview_replacements = tuple(preview_replacements_list)
    return SiteConfig(
        key=key,
        root_path=root,
        allowed_hosts=hosts,
        homepage_url=homepage_url,
        preview_url_template=preview_template,
        description=raw.get("description", ""),
        allowed_extensions=frozenset(extensions),
        ignored_names=ignored_names,
        protected_paths=configured_protected + ("__phpvibe_preview", "__phpvibe_preview/*", "__phpvibe_preview/**"),
        preview_replacements=preview_replacements,
        backup_path=backup_path,
        asset_upload_path=asset_upload_path,
    )
