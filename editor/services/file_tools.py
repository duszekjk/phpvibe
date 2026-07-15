from __future__ import annotations

import json
from pathlib import Path

from django.conf import settings

from editor.config import load_site_config
from editor.models import EditSession

from .workspaces import atomic_write, commit_change, editable_files, ensure_editable, safe_path, workspace_root


def list_files(edit_session: EditSession, query: str = "") -> str:
    root = workspace_root(edit_session)
    config = load_site_config(edit_session.site.config_key)
    needle = query.casefold().strip()
    results = []
    for path in editable_files(root, config):
        relative = path.relative_to(root).as_posix()
        if not needle or needle in relative.casefold():
            results.append(relative)
        if len(results) >= 200:
            break
    return json.dumps({"files": results, "truncated": len(results) == 200}, ensure_ascii=False)


def read_file(edit_session: EditSession, path: str, start_line: int = 1, end_line: int = 300) -> str:
    target = safe_path(edit_session, path)
    ensure_editable(edit_session, target)
    if target.stat().st_size > settings.FILE_MAX_BYTES:
        return json.dumps({"error": "Plik przekracza limit odczytu."}, ensure_ascii=False)
    lines = target.read_text(encoding="utf-8", errors="replace").splitlines()
    start = max(1, int(start_line))
    end = min(len(lines), max(start, int(end_line)), start + 499)
    numbered = "\n".join(f"{number}: {lines[number - 1]}" for number in range(start, end + 1))
    return json.dumps({"path": path, "start": start, "end": end, "total_lines": len(lines), "content": numbered}, ensure_ascii=False)


def search_text(edit_session: EditSession, query: str) -> str:
    needle = query.casefold()
    if len(needle) < 2:
        return json.dumps({"error": "Zapytanie musi mieć co najmniej 2 znaki."}, ensure_ascii=False)
    root = workspace_root(edit_session)
    config = load_site_config(edit_session.site.config_key)
    matches = []
    for path in editable_files(root, config):
        if path.stat().st_size > settings.FILE_MAX_BYTES:
            continue
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for number, line in enumerate(lines, 1):
            if needle in line.casefold():
                matches.append({"path": path.relative_to(root).as_posix(), "line": number, "text": line[:300]})
                if len(matches) >= 100:
                    return json.dumps({"matches": matches, "truncated": True}, ensure_ascii=False)
    return json.dumps({"matches": matches, "truncated": False}, ensure_ascii=False)


def write_file(edit_session: EditSession, path: str, content: str, summary: str) -> str:
    target = safe_path(edit_session, path, must_exist=False)
    ensure_editable(edit_session, target)
    atomic_write(target, content)
    revision = commit_change(edit_session, summary or f"Zmiana pliku {path}")
    return json.dumps({"ok": True, "path": path, "commit": revision.commit_hash if revision else None}, ensure_ascii=False)


def replace_text(
    edit_session: EditSession,
    path: str,
    old_text: str,
    new_text: str,
    replace_all: bool,
    summary: str,
) -> str:
    target = safe_path(edit_session, path)
    ensure_editable(edit_session, target)
    content = target.read_text(encoding="utf-8")
    count = content.count(old_text)
    if count == 0:
        return json.dumps({"ok": False, "error": "Nie znaleziono podanego tekstu."}, ensure_ascii=False)
    if count > 1 and not replace_all:
        return json.dumps({"ok": False, "error": f"Tekst występuje {count} razy; doprecyzuj fragment albo ustaw replace_all."}, ensure_ascii=False)
    updated = content.replace(old_text, new_text, -1 if replace_all else 1)
    atomic_write(target, updated)
    revision = commit_change(edit_session, summary or f"Zmiana w pliku {path}")
    return json.dumps({"ok": True, "path": path, "replacements": count if replace_all else 1, "commit": revision.commit_hash if revision else None}, ensure_ascii=False)


TOOL_SCHEMAS = [
    {
        "type": "function", "name": "list_files",
        "description": "List editable text files in the isolated workspace, optionally filtering paths by a substring.",
        "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"], "additionalProperties": False},
        "strict": True,
    },
    {
        "type": "function", "name": "search_text",
        "description": "Search case-insensitively inside editable files. Use this to locate content related to the target URL before editing.",
        "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"], "additionalProperties": False},
        "strict": True,
    },
    {
        "type": "function", "name": "read_file",
        "description": "Read at most 500 numbered lines from one editable file.",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string"}, "start_line": {"type": "integer"}, "end_line": {"type": "integer"}
        }, "required": ["path", "start_line", "end_line"], "additionalProperties": False},
        "strict": True,
    },
    {
        "type": "function", "name": "replace_text",
        "description": "Safely replace an exact text fragment and commit the change. Prefer this over writing an entire existing file.",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string"}, "old_text": {"type": "string"}, "new_text": {"type": "string"},
            "replace_all": {"type": "boolean"}, "summary": {"type": "string"}
        }, "required": ["path", "old_text", "new_text", "replace_all", "summary"], "additionalProperties": False},
        "strict": True,
    },
    {
        "type": "function", "name": "write_file",
        "description": "Write a complete editable text file atomically and commit it. Use for new files or only after reading the full existing file.",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string"}, "content": {"type": "string"}, "summary": {"type": "string"}
        }, "required": ["path", "content", "summary"], "additionalProperties": False},
        "strict": True,
    },
]


def execute_tool(edit_session: EditSession, name: str, arguments: dict) -> str:
    handlers = {
        "list_files": list_files,
        "search_text": search_text,
        "read_file": read_file,
        "write_file": write_file,
        "replace_text": replace_text,
    }
    handler = handlers.get(name)
    if not handler:
        return json.dumps({"error": "Nieznane narzędzie."}, ensure_ascii=False)
    try:
        return handler(edit_session, **arguments)
    except Exception as exc:
        return json.dumps({"error": str(exc)}, ensure_ascii=False)
