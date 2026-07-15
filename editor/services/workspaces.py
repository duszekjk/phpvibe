from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
from typing import Iterable

from django.conf import settings
from django.core.exceptions import PermissionDenied
from django.db import transaction

from editor.config import SiteConfig, load_site_config
from editor.models import ChatMessage, EditSession, Revision


class WorkspaceError(RuntimeError):
    pass


PREVIEW_ASSET_DIR = "__phpvibe_preview"


def _run_git(root: Path, *args: str) -> str:
    process = subprocess.run(
        ["git", "-c", "user.name=PHP Vibe", "-c", "user.email=phpvibe@localhost", *args],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    if process.returncode:
        raise WorkspaceError(process.stderr.strip() or process.stdout.strip() or "Polecenie Git nie powiodło się.")
    return process.stdout.strip()


def _ignore(config: SiteConfig):
    def callback(directory: str, names: list[str]) -> set[str]:
        return {
            name for name in names
            if name in config.ignored_names or (Path(directory) / name).is_symlink()
        }

    return callback


def install_preview_layer(destination: Path, config: SiteConfig) -> list[dict[str, str]]:
    asset_root = destination / PREVIEW_ASSET_DIR
    if asset_root.exists():
        raise WorkspaceError(f"Strona używa zastrzeżonego katalogu {PREVIEW_ASSET_DIR}.")
    source_root = settings.BASE_DIR / "editor" / "static" / "editor"
    asset_root.mkdir()
    shutil.copy2(source_root / "preview-bridge.js", asset_root / "preview-bridge.js")
    shutil.copy2(source_root / "preview.css", asset_root / "preview.css")

    applied = []
    for replacement in config.preview_replacements:
        target = (destination / replacement.path).resolve()
        if destination not in target.parents or not target.is_file() or target.is_symlink():
            if replacement.required:
                raise WorkspaceError(f"Nie można zastosować transformacji podglądu do {replacement.path}.")
            continue
        content = target.read_text(encoding="utf-8")
        count = content.count(replacement.production_text)
        if count != 1:
            if replacement.required:
                raise WorkspaceError(
                    f"Transformacja podglądu {replacement.path} oczekiwała jednego fragmentu, znaleziono: {count}."
                )
            continue
        atomic_write(target, content.replace(replacement.production_text, replacement.preview_text, 1))
        applied.append({
            "path": replacement.path,
            "production_text": replacement.production_text,
            "preview_text": replacement.preview_text,
        })
    return applied


def remove_preview_layer(edit_session: EditSession, relative_path: str, content: str) -> str:
    for replacement in edit_session.preview_transforms:
        if replacement.get("path") != relative_path:
            continue
        preview_text = replacement.get("preview_text", "")
        production_text = replacement.get("production_text", "")
        count = content.count(preview_text)
        if not preview_text or count != 1:
            raise WorkspaceError(
                f"Nie można bezpiecznie usunąć transformacji podglądu z {relative_path}; publikacja została przerwana."
            )
        content = content.replace(preview_text, production_text, 1)
    return content


def editable_files(root: Path, config: SiteConfig) -> Iterable[Path]:
    for path in root.rglob("*"):
        if not path.is_file() or path.is_symlink() or path.suffix.lower() not in config.allowed_extensions:
            continue
        relative = path.relative_to(root)
        if any(part in config.ignored_names for part in relative.parts) or config.is_protected(relative.as_posix()):
            continue
        yield path


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def build_manifest(root: Path, config: SiteConfig) -> dict[str, str]:
    return {path.relative_to(root).as_posix(): sha256(path) for path in editable_files(root, config)}


def create_workspace(edit_session: EditSession) -> None:
    config = load_site_config(edit_session.site.config_key)
    destination = (settings.WORKSPACE_ROOT / str(edit_session.pk) / "site").resolve()
    expected_parent = settings.WORKSPACE_ROOT.resolve()
    if expected_parent not in destination.parents:
        raise WorkspaceError("Nieprawidłowa ścieżka katalogu roboczego.")
    if destination.exists():
        raise WorkspaceError("Katalog roboczy tej rozmowy już istnieje.")

    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        shutil.copytree(config.root_path, destination, ignore=_ignore(config), copy_function=shutil.copy2)
        preview_transforms = install_preview_layer(destination, config)
        _run_git(destination, "init", "--quiet")
        _run_git(destination, "add", "--all")
        _run_git(destination, "commit", "--quiet", "-m", "Stan początkowy rozmowy")
        baseline = _run_git(destination, "rev-parse", "HEAD")
        manifest = build_manifest(config.root_path, config)
    except Exception:
        shutil.rmtree(destination.parent, ignore_errors=True)
        raise

    edit_session.workspace_path = str(destination)
    edit_session.baseline_commit = baseline
    edit_session.baseline_manifest = manifest
    edit_session.preview_transforms = preview_transforms
    edit_session.status = EditSession.Status.ACTIVE
    edit_session.error_message = ""
    edit_session.save(update_fields=[
        "workspace_path", "baseline_commit", "baseline_manifest", "preview_transforms", "status", "error_message", "updated_at"
    ])


def workspace_root(edit_session: EditSession) -> Path:
    root = Path(edit_session.workspace_path).resolve()
    expected = (settings.WORKSPACE_ROOT / str(edit_session.pk) / "site").resolve()
    if root != expected or not root.is_dir():
        raise WorkspaceError("Katalog roboczy jest niedostępny lub ma nieprawidłową ścieżkę.")
    return root


def safe_path(edit_session: EditSession, relative_path: str, *, must_exist: bool = True) -> Path:
    if not relative_path or "\x00" in relative_path:
        raise PermissionDenied("Nieprawidłowa ścieżka pliku.")
    root = workspace_root(edit_session)
    candidate = (root / relative_path).resolve()
    if candidate == root or root not in candidate.parents:
        raise PermissionDenied("Plik znajduje się poza katalogiem roboczym.")
    if ".git" in candidate.relative_to(root).parts:
        raise PermissionDenied("Dostęp do metadanych repozytorium jest zabroniony.")
    if must_exist and (not candidate.is_file() or candidate.is_symlink()):
        raise FileNotFoundError(relative_path)
    return candidate


def ensure_editable(edit_session: EditSession, path: Path) -> None:
    config = load_site_config(edit_session.site.config_key)
    relative = path.relative_to(workspace_root(edit_session)).as_posix()
    if config.is_protected(relative):
        raise PermissionDenied("Ten plik jest chroniony przez konfigurację strony.")
    if path.suffix.lower() not in config.allowed_extensions:
        raise PermissionDenied(f"Edycja plików {path.suffix or '(bez rozszerzenia)'} jest zabroniona.")


def atomic_write(path: Path, content: str) -> None:
    encoded = content.encode("utf-8")
    if len(encoded) > settings.FILE_MAX_BYTES:
        raise WorkspaceError(f"Plik przekracza limit {settings.FILE_MAX_BYTES} bajtów.")
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def commit_change(edit_session: EditSession, summary: str) -> Revision | None:
    root = workspace_root(edit_session)
    changed = [line for line in _run_git(root, "status", "--short").splitlines() if line]
    if not changed:
        return None
    _run_git(root, "add", "--all")
    _run_git(root, "commit", "--quiet", "-m", summary[:240])
    commit_hash = _run_git(root, "rev-parse", "HEAD")
    files = [line[3:] for line in changed]
    return Revision.objects.create(
        session=edit_session,
        commit_hash=commit_hash,
        summary=summary[:240],
        changed_files=files,
    )


@transaction.atomic
def reset_workspace(edit_session: EditSession) -> None:
    locked = EditSession.objects.select_for_update().get(pk=edit_session.pk)
    if locked.status not in {EditSession.Status.ACTIVE, EditSession.Status.PUBLISHED}:
        raise WorkspaceError("Tej rozmowy nie można teraz przywrócić.")
    root = workspace_root(locked)
    _run_git(root, "reset", "--hard", locked.baseline_commit)
    _run_git(root, "clean", "-fd")
    Revision.objects.create(
        session=locked,
        commit_hash=locked.baseline_commit,
        summary="Przywrócono stan początkowy rozmowy",
        changed_files=[],
    )
    for conversation in locked.conversations.all():
        ChatMessage.objects.create(
            session=locked,
            conversation=conversation,
            role=ChatMessage.Role.SYSTEM,
            content="Przywrócono stan początkowy. Dalsza rozmowa zaczyna się od czystej kopii strony.",
        )
    locked.conversations.update(last_response_id="")
    locked.last_response_id = ""
    locked.status = EditSession.Status.ACTIVE
    locked.save(update_fields=["last_response_id", "status", "updated_at"])


def changed_paths(edit_session: EditSession) -> list[str]:
    root = workspace_root(edit_session)
    output = _run_git(root, "diff", "--name-only", f"{edit_session.baseline_commit}..HEAD")
    return [line for line in output.splitlines() if line]


def current_diff(edit_session: EditSession, max_chars: int = 120_000) -> str:
    root = workspace_root(edit_session)
    output = _run_git(root, "diff", "--no-color", "--unified=3", f"{edit_session.baseline_commit}..HEAD", "--")
    if len(output) > max_chars:
        return output[:max_chars] + "\n\n[Diff skrócony przez aplikację]"
    return output


@transaction.atomic
def publish_workspace(edit_session: EditSession) -> list[str]:
    locked = EditSession.objects.select_for_update().select_related("site").get(pk=edit_session.pk)
    config = load_site_config(locked.site.config_key)
    if not config.publish_enabled or not config.backup_path:
        raise WorkspaceError("Publikowanie nie jest włączone w konfiguracji tej strony.")
    if locked.status != EditSession.Status.ACTIVE:
        raise WorkspaceError("Publikować można wyłącznie aktywną rozmowę.")

    paths = changed_paths(locked)
    if not paths:
        raise WorkspaceError("Nie ma zmian do opublikowania.")
    root = workspace_root(locked)
    for relative in paths:
        if config.is_protected(relative):
            raise WorkspaceError(f"Plik chroniony nie może być opublikowany: {relative}")

    conflicts = []
    for relative in paths:
        source_at_start = locked.baseline_manifest.get(relative)
        production = (config.root_path / relative).resolve()
        if config.root_path not in production.parents:
            conflicts.append(relative)
        elif source_at_start is None:
            if production.exists():
                conflicts.append(relative)
        elif not production.is_file() or source_at_start != sha256(production):
            conflicts.append(relative)
    if conflicts:
        raise WorkspaceError("Produkcja zmieniła się od początku rozmowy: " + ", ".join(conflicts))

    from django.utils import timezone

    stamp = timezone.now().strftime("%Y%m%d-%H%M%S")
    backup = (config.backup_path / config.key / f"{stamp}-{locked.pk}").resolve()
    backup.mkdir(parents=True, exist_ok=False)
    (backup / "manifest.json").write_text(
        json.dumps({"session": str(locked.pk), "files": paths}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    for relative in paths:
        working = safe_path(locked, relative)
        production = (config.root_path / relative).resolve()
        original = backup / relative
        original.parent.mkdir(parents=True, exist_ok=True)
        if production.exists():
            shutil.copy2(production, original)
        published_content = remove_preview_layer(locked, relative, working.read_text(encoding="utf-8"))
        atomic_write(production, published_content)

    locked.status = EditSession.Status.PUBLISHED
    locked.published_at = timezone.now()
    locked.save(update_fields=["status", "published_at", "updated_at"])
    return paths
