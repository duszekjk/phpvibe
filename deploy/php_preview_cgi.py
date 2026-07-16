#!/private/var/www/phpvibe/.venv/bin/python
"""Fail-closed CGI launcher for isolated PHP Vibe workspace previews."""

from __future__ import annotations

import os
from pathlib import Path
import re
import shutil
import sys
from uuid import UUID


WORKSPACE_ROOT = Path(os.environ.get("VIBE_WORKSPACE_ROOT", "/private/var/www/phpvibe-workspaces"))
PHP_CGI_CANDIDATES = (
    "/opt/homebrew/bin/php-cgi",
    "/usr/local/bin/php-cgi",
    "/usr/bin/php-cgi",
)
DISABLED_FUNCTIONS = "exec,shell_exec,system,passthru,proc_open,popen,pcntl_exec,dl"


def resolve_script(path_translated: str, path_info: str, *, workspace_root: Path = WORKSPACE_ROOT) -> tuple[Path, Path]:
    match = re.fullmatch(
        r"/vibe/([0-9a-fA-F-]{36})/(.*\.php)",
        path_info,
    )
    if not match:
        raise ValueError("Nieprawidłowa ścieżka URL skryptu PHP.")
    session_id = str(UUID(match.group(1)))
    root = (workspace_root / session_id / "site").resolve(strict=True)
    requested_target = Path(path_translated)
    if requested_target.is_symlink():
        raise ValueError("Dowiązania symboliczne skryptów PHP są zabronione.")
    target = requested_target.resolve(strict=True)
    if target.suffix.lower() != ".php" or root not in target.parents:
        raise ValueError("Skrypt PHP znajduje się poza autoryzowaną kopią.")
    expected = (root / match.group(2)).resolve(strict=True)
    if target != expected or target.is_symlink():
        raise ValueError("URL i ścieżka skryptu PHP nie są zgodne.")
    return root, target


def find_php_cgi() -> str | None:
    configured = os.environ.get("PHPVIBE_PHP_CGI", "").strip()
    candidates = (configured, *PHP_CGI_CANDIDATES, shutil.which("php-cgi") or "")
    for candidate in candidates:
        if candidate and Path(candidate).is_file() and os.access(candidate, os.X_OK):
            return candidate
    return None


def fail(message: str) -> None:
    sys.stdout.write(
        "Status: 503 Service Unavailable\r\n"
        "Content-Type: text/plain; charset=utf-8\r\n"
        "Cache-Control: no-store\r\n\r\n"
        f"Podgląd PHP jest niedostępny: {message}\n"
    )
    raise SystemExit(0)


def main() -> None:
    try:
        root, target = resolve_script(
            os.environ.get("PATH_TRANSLATED", ""),
            os.environ.get("PATH_INFO", ""),
        )
    except (OSError, ValueError) as exc:
        fail(str(exc))
    php_cgi = find_php_cgi()
    if php_cgi is None:
        fail("nie znaleziono wykonywalnego php-cgi")

    environment = os.environ.copy()
    environment["SCRIPT_FILENAME"] = str(target)
    environment["SCRIPT_NAME"] = os.environ.get("PATH_INFO", "")
    environment["REDIRECT_STATUS"] = "200"
    os.chdir(root)
    arguments = [
        php_cgi,
        "-d", f"open_basedir={root}:/private/tmp",
        "-d", f"disable_functions={DISABLED_FUNCTIONS}",
        "-d", "allow_url_fopen=0",
        "-d", "allow_url_include=0",
        "-d", "display_errors=0",
        "-d", "expose_php=0",
        "-d", "file_uploads=0",
        "-d", "log_errors=1",
        "-d", "max_execution_time=10",
        "-d", "max_input_time=10",
        "-d", "memory_limit=128M",
        "-d", "post_max_size=1M",
        "-d", "session.save_path=/private/tmp",
    ]
    os.execve(php_cgi, arguments, environment)


if __name__ == "__main__":
    main()
