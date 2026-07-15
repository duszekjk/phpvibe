from pathlib import Path
import tomllib

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from editor.config import load_site_config
from editor.models import Site


class Command(BaseCommand):
    help = "Tworzy lub aktualizuje rekordy stron na podstawie plików site_configs/*.toml."

    def handle(self, *args, **options):
        paths = sorted(Path(settings.SITE_CONFIG_DIR).glob("*.toml"))
        if not paths:
            raise CommandError(f"Brak plików TOML w {settings.SITE_CONFIG_DIR}")
        load_site_config.cache_clear()
        for path in paths:
            try:
                with path.open("rb") as handle:
                    raw = tomllib.load(handle)
                key = path.stem
                config = load_site_config(key)
                site, created = Site.objects.update_or_create(
                    config_key=key,
                    defaults={
                        "name": raw.get("name", key),
                        "slug": raw.get("slug", key),
                        "is_active": bool(raw.get("is_active", True)),
                    },
                )
            except Exception as exc:
                raise CommandError(f"Błąd w {path}: {exc}") from exc
            action = "Dodano" if created else "Zaktualizowano"
            self.stdout.write(self.style.SUCCESS(f"{action}: {site.name} ({config.root_path})"))
