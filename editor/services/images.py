from __future__ import annotations

from io import BytesIO
from pathlib import Path
import uuid

from django.conf import settings
from django.core.exceptions import PermissionDenied
from django.utils.text import slugify
from PIL import Image, ImageOps, UnidentifiedImageError

from editor.config import load_site_config
from editor.models import EditSession

from .workspaces import (
    WorkspaceError,
    atomic_write_bytes,
    commit_paths,
    safe_path,
    workspace_operation_lock,
)


class ImageUploadError(RuntimeError):
    pass


Image.MAX_IMAGE_PIXELS = 50_000_000
VARIANTS = {
    "large": (2560, 86),
    "background": (1920, 82),
    "content": (1280, 84),
    "button": (800, 80),
}


def _webp_bytes(source: Image.Image, max_dimension: int, quality: int) -> tuple[bytes, int, int]:
    image = source.copy()
    image.thumbnail((max_dimension, max_dimension), Image.Resampling.LANCZOS)
    output = BytesIO()
    image.save(output, format="WEBP", quality=quality, method=4, optimize=True)
    return output.getvalue(), image.width, image.height


def process_image_upload(edit_session: EditSession, uploaded) -> dict:
    if uploaded.size <= 0:
        raise ImageUploadError("Przesłany plik jest pusty.")
    if uploaded.size > settings.IMAGE_UPLOAD_MAX_BYTES:
        limit_mb = settings.IMAGE_UPLOAD_MAX_BYTES / 1048576
        raise ImageUploadError(f"Zdjęcie może mieć maksymalnie {limit_mb:.0f} MB.")

    try:
        uploaded.seek(0)
        with Image.open(uploaded) as opened:
            if opened.width < 32 or opened.height < 32:
                raise ImageUploadError("Zdjęcie jest zbyt małe.")
            if opened.width * opened.height > 50_000_000:
                raise ImageUploadError("Zdjęcie ma zbyt dużą rozdzielczość.")
            opened.load()
            source = ImageOps.exif_transpose(opened)
            has_alpha = source.mode in {"RGBA", "LA"} or (source.mode == "P" and "transparency" in source.info)
            source = source.convert("RGBA" if has_alpha else "RGB")
    except ImageUploadError:
        raise
    except (Image.DecompressionBombError, UnidentifiedImageError, OSError, ValueError) as exc:
        raise ImageUploadError("Plik nie jest prawidłowym obsługiwanym zdjęciem.") from exc

    config = load_site_config(edit_session.site.config_key)
    original_stem = slugify(Path(uploaded.name or "obraz").stem)[:60] or "obraz"
    stem = f"{original_stem}-{uuid.uuid4().hex[:10]}"
    written_paths: list[Path] = []
    metadata = {}

    try:
        with workspace_operation_lock(edit_session):
            for variant, (max_dimension, quality) in VARIANTS.items():
                relative = f"{config.asset_upload_path}/{stem}-{variant}.webp"
                target = safe_path(edit_session, relative, must_exist=False)
                content, width, height = _webp_bytes(source, max_dimension, quality)
                atomic_write_bytes(target, content, max_bytes=settings.IMAGE_UPLOAD_MAX_BYTES)
                written_paths.append(target)
                metadata[variant] = {
                    "path": relative,
                    "width": width,
                    "height": height,
                    "bytes": len(content),
                    "mime": "image/webp",
                }
            commit_paths(
                edit_session,
                f"Dodano i zoptymalizowano zdjęcie {Path(uploaded.name or 'obraz').name}"[:240],
                [item["path"] for item in metadata.values()],
            )
    except (PermissionDenied, WorkspaceError, OSError) as exc:
        for path in written_paths:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
        raise ImageUploadError(f"Nie udało się zapisać zdjęcia: {exc}") from exc

    return {
        "name": Path(uploaded.name or "obraz").name[:180],
        "source_width": source.width,
        "source_height": source.height,
        "analysis_variant": "content",
        "variants": metadata,
    }
