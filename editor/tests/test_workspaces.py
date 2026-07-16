from pathlib import Path
import stat
import tempfile
from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.contrib.auth import get_user_model
from django.core.exceptions import ImproperlyConfigured, PermissionDenied, ValidationError
from django.test import TestCase, override_settings

from editor.config import load_site_config
from editor.forms import StartSessionForm
from editor.models import EditSession, Site, SiteMembership
from editor.navigation import get_or_create_page_conversation, normalize_page_url
from editor.preview_access import add_preview_token, make_preview_token
from editor.services.assistant import AssistantError, _replay_input, run_chat_turn
from editor.services.file_tools import read_file, replace_text, write_file
from editor.services.workspaces import (
    WorkspaceBusyError,
    WorkspaceError,
    create_workspace,
    publish_workspace,
    reset_workspace,
    safe_path,
    workspace_operation_lock,
)


class WorkspaceTests(TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        base = Path(self.temp.name)
        self.source = base / "source"
        self.source.mkdir()
        (self.source / "index.php").write_text("<?php echo 'stara treść';", encoding="utf-8")
        (self.source / "main.css").write_text("body { color: black; }", encoding="utf-8")
        self.config_dir = base / "configs"
        self.config_dir.mkdir()
        self.workspace_dir = base / "workspaces"
        self.backup_dir = base / "backups"
        self._write_config()
        self.settings_override = override_settings(
            SITE_CONFIG_DIR=self.config_dir,
            WORKSPACE_ROOT=self.workspace_dir,
            FILE_MAX_BYTES=100_000,
        )
        self.settings_override.enable()
        load_site_config.cache_clear()
        self.user = get_user_model().objects.create_user("ewa", password="secret123")
        self.site = Site.objects.create(name="Test", slug="test", config_key="test")
        SiteMembership.objects.create(site=self.site, user=self.user, role=SiteMembership.Role.PUBLISHER)

    def tearDown(self):
        load_site_config.cache_clear()
        self.settings_override.disable()
        self.temp.cleanup()

    def _write_config(self, *, publish=True):
        (self.config_dir / "test.toml").write_text(
            f'''name = "Test"\nroot_path = "{self.source}"\nallowed_hosts = ["example.org"]\npreview_url_template = "https://preview.example/{{session_id}}/"\nallowed_extensions = [".php", ".css", ".txt"]\npublish_enabled = {str(publish).lower()}\nbackup_path = "{self.backup_dir}"\ndescription = "Testowa strona PHP"\n[[preview_replacements]]\npath = "index.php"\nproduction_text = "<?php "\npreview_text = "<?php /* __PHPVIBE_PREVIEW_TEST__ */ "\nrequired = true\n''',
            encoding="utf-8",
        )

    def new_session(self):
        item = EditSession.objects.create(
            site=self.site,
            owner=self.user,
            title="Zmiana treści",
            target_url="https://example.org/?strona=start",
        )
        create_workspace(item)
        item.refresh_from_db()
        get_or_create_page_conversation(item, item.target_url)
        return item

    def test_path_cannot_escape_workspace(self):
        item = self.new_session()
        with self.assertRaises(PermissionDenied):
            safe_path(item, "../../sekret.txt", must_exist=False)

    def test_workspace_rejects_second_concurrent_operation(self):
        item = self.new_session()
        with workspace_operation_lock(item):
            with self.assertRaises(WorkspaceBusyError):
                with workspace_operation_lock(item):
                    pass

    @override_settings(PANEL_ORIGIN="https://panel.example")
    def test_preview_bridge_is_bound_to_configured_panel_origin(self):
        item = self.new_session()
        bridge = (Path(item.workspace_path) / "__phpvibe_preview" / "preview-bridge.js").read_text(encoding="utf-8")
        self.assertIn('const panelOrigin = "https://panel.example";', bridge)
        self.assertNotIn('postMessage({ source: "phpvibe-preview", type, ...payload }, "*")', bridge)
        self.assertIn("event.origin !== panelOrigin", bridge)

    @override_settings(PANEL_ORIGIN="https://panel.example/path")
    def test_preview_bridge_rejects_non_origin_panel_url(self):
        item = EditSession.objects.create(
            site=self.site,
            owner=self.user,
            title="Zmiana treści",
            target_url="https://example.org/",
        )
        with self.assertRaisesRegex(WorkspaceError, "VIBE_PANEL_ORIGIN"):
            create_workspace(item)

    @override_settings(PANEL_ORIGIN="https://panel.example:not-a-port")
    def test_preview_bridge_rejects_invalid_panel_port(self):
        item = EditSession.objects.create(
            site=self.site,
            owner=self.user,
            title="Zmiana treści",
            target_url="https://example.org/",
        )
        with self.assertRaisesRegex(WorkspaceError, "VIBE_PANEL_ORIGIN"):
            create_workspace(item)

    def test_protected_file_is_hidden_from_assistant(self):
        (self.source / "secret.php").write_text("<?php return 'token';", encoding="utf-8")
        item = self.new_session()
        with self.assertRaises(FileNotFoundError):
            read_file(item, "secret.php")

    def test_protected_file_is_not_copied_into_executable_preview(self):
        (self.source / "secret.php").write_text("<?php return 'token';", encoding="utf-8")
        item = self.new_session()
        self.assertFalse((Path(item.workspace_path) / "secret.php").exists())

    def test_assistant_cannot_create_file_in_ignored_directory(self):
        item = self.new_session()
        with self.assertRaises(PermissionDenied):
            write_file(item, "vendor/backdoor.php", "<?php", "Niedozwolony plik")

    def test_edit_preserves_file_permissions(self):
        self.source.joinpath("index.php").chmod(0o644)
        item = self.new_session()
        target = Path(item.workspace_path) / "index.php"
        original_mode = stat.S_IMODE(target.stat().st_mode)
        self.assertEqual(original_mode, 0o644)
        replace_text(item, "index.php", "stara treść", "nowa treść", False, "Zmiana")
        self.assertEqual(stat.S_IMODE(target.stat().st_mode), original_mode)

    def test_publish_preserves_production_file_permissions(self):
        self.source.joinpath("index.php").chmod(0o644)
        item = self.new_session()
        replace_text(item, "index.php", "stara treść", "nowa treść", False, "Zmiana")
        publish_workspace(item)
        self.assertEqual(stat.S_IMODE(self.source.joinpath("index.php").stat().st_mode), 0o644)

    def test_reset_restores_baseline_and_removes_new_files(self):
        item = self.new_session()
        replace_text(item, "index.php", "stara treść", "nowa treść", False, "Nowa treść")
        write_file(item, "nowy.php", "<?php echo 'nowy';", "Nowy plik")
        reset_workspace(item)
        root = Path(item.workspace_path)
        self.assertIn("stara treść", (root / "index.php").read_text(encoding="utf-8"))
        self.assertFalse((root / "nowy.php").exists())
        self.assertTrue((root / "__phpvibe_preview" / "preview-bridge.js").is_file())
        self.assertTrue((root / "__phpvibe_preview" / "preview.css").is_file())
        self.assertEqual(_replay_input(item.conversations.get()), [])

    def test_reset_restores_files_ignored_by_the_legacy_site(self):
        (self.source / ".gitignore").write_text("generated.txt\ncache.txt\n", encoding="utf-8")
        (self.source / "generated.txt").write_text("stan początkowy", encoding="utf-8")
        item = self.new_session()
        root = Path(item.workspace_path)

        replace_text(item, "generated.txt", "stan początkowy", "zmieniony", False, "Zmiana")
        (root / "cache.txt").write_text("plik utworzony w podglądzie", encoding="utf-8")
        reset_workspace(item)

        self.assertEqual((root / "generated.txt").read_text(encoding="utf-8"), "stan początkowy")
        self.assertFalse((root / "cache.txt").exists())

    def test_form_rejects_url_from_another_host(self):
        form = StartSessionForm(
            data={"site": self.site.pk, "title": "Test", "target_url": "https://evil.example/path"},
            user=self.user,
        )
        self.assertFalse(form.is_valid())
        self.assertIn("target_url", form.errors)

    def test_page_url_rejects_invalid_port(self):
        with self.assertRaises(ValidationError):
            normalize_page_url("https://example.org:not-a-port/", frozenset({"example.org"}))

    def test_config_rejects_missing_preview_template(self):
        config_path = self.config_dir / "test.toml"
        config_path.write_text(
            config_path.read_text(encoding="utf-8").replace(
                'preview_url_template = "https://preview.example/{session_id}/"',
                'preview_url_template = ""',
            ),
            encoding="utf-8",
        )
        load_site_config.cache_clear()
        with self.assertRaisesRegex(ImproperlyConfigured, "preview_url_template"):
            load_site_config("test")

    def test_config_rejects_backup_inside_production_root(self):
        config_path = self.config_dir / "test.toml"
        config_path.write_text(
            config_path.read_text(encoding="utf-8").replace(
                f'backup_path = "{self.backup_dir}"',
                f'backup_path = "{self.source / "backups"}"',
            ),
            encoding="utf-8",
        )
        load_site_config.cache_clear()
        with self.assertRaisesRegex(ImproperlyConfigured, "backup_path"):
            load_site_config("test")

    def test_publish_detects_production_conflict(self):
        item = self.new_session()
        replace_text(item, "index.php", "stara treść", "wersja robocza", False, "Robocza")
        (self.source / "index.php").write_text("<?php echo 'inna produkcja';", encoding="utf-8")
        with self.assertRaises(WorkspaceError):
            publish_workspace(item)

    def test_publish_updates_only_changed_file_and_keeps_backup(self):
        item = self.new_session()
        replace_text(item, "index.php", "stara treść", "opublikowana", False, "Publikacja")
        paths = publish_workspace(item)
        self.assertEqual(paths, ["index.php"])
        self.assertIn("opublikowana", (self.source / "index.php").read_text(encoding="utf-8"))
        self.assertNotIn("__PHPVIBE_PREVIEW", (self.source / "index.php").read_text(encoding="utf-8"))
        self.assertFalse((self.source / "__phpvibe_preview").exists())
        backups = list(self.backup_dir.rglob("index.php"))
        self.assertEqual(len(backups), 1)
        self.assertIn("stara treść", backups[0].read_text(encoding="utf-8"))

    def test_publish_aborts_when_preview_transform_cannot_be_removed(self):
        item = self.new_session()
        replace_text(
            item,
            "index.php",
            "/* __PHPVIBE_PREVIEW_TEST__ */ ",
            "",
            False,
            "Uszkodzenie znacznika podglądu",
        )
        with self.assertRaises(WorkspaceError):
            publish_workspace(item)
        self.assertNotIn("__PHPVIBE_PREVIEW", (self.source / "index.php").read_text(encoding="utf-8"))
        self.assertIn("stara treść", (self.source / "index.php").read_text(encoding="utf-8"))

    def test_conversation_page_renders_for_member(self):
        item = self.new_session()
        self.client.force_login(self.user)
        response = self.client.get(item.get_absolute_url(), secure=True)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Nowy czat tej podstrony")
        self.assertContains(response, 'src="/static/editor/workbench.js"')
        self.assertContains(response, 'href="/static/editor/workbench.css"')
        self.assertContains(response, "/__vibe_token/")
        self.assertContains(response, f'/rozmowy/{item.pk}/usun/')

    def test_multiple_page_chats_share_one_workspace(self):
        item = self.new_session()
        first_workspace = item.workspace_path
        self.client.force_login(self.user)
        response = self.client.post(
            f"/rozmowy/{item.pk}/strony/otworz/",
            {"url": "https://example.org/kontakt?dzial=biuro"},
            HTTP_ACCEPT="application/json",
            secure=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(item.conversations.count(), 2)
        item.refresh_from_db()
        self.assertEqual(item.workspace_path, first_workspace)
        self.assertEqual(len(list(self.workspace_dir.iterdir())), 1)

    def test_inline_text_edit_is_sent_to_active_page_chat(self):
        item = self.new_session()
        conversation = item.conversations.get()
        self.client.force_login(self.user)
        with patch("editor.views.run_chat_turn", return_value=SimpleNamespace(content="Zmieniono tekst.")) as run:
            response = self.client.post(
                f"/rozmowy/{item.pk}/strony/{conversation.pk}/edytuj-tekst/",
                {
                    "old_text": "Stary nagłówek",
                    "new_text": "Nowy nagłówek",
                    "selector": "main > h1",
                    "tag_name": "h1",
                    "outer_html": "<h1>Stary nagłówek</h1>",
                },
                HTTP_ACCEPT="application/json",
                secure=True,
            )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["ok"])
        self.assertEqual(run.call_args.args[1], conversation)
        self.assertIn(conversation.target_url, run.call_args.kwargs["model_text"])

    def test_openai_client_configuration_error_is_reported_as_chat_error(self):
        item = self.new_session()
        conversation = item.conversations.get()
        with patch("openai.OpenAI", side_effect=RuntimeError("missing key")):
            with self.assertRaisesRegex(AssistantError, "OpenAI"):
                run_chat_turn(item, conversation, "Zmień tekst")

    @override_settings(OPENAI_MAX_TOOL_ROUNDS=1)
    def test_final_response_after_last_allowed_tool_call_is_returned(self):
        item = self.new_session()
        conversation = item.conversations.get()
        tool_call = SimpleNamespace(
            type="function_call",
            name="list_files",
            arguments='{"query": ""}',
            call_id="call-1",
        )
        first = SimpleNamespace(id="response-1", output=[tool_call], output_text="")
        final = SimpleNamespace(id="response-2", output=[], output_text="Gotowe.")
        client = SimpleNamespace(responses=SimpleNamespace(create=Mock(side_effect=[first, final])))

        with patch("openai.OpenAI", return_value=client):
            message = run_chat_turn(item, conversation, "Pokaż pliki")

        self.assertEqual(message.content, "Gotowe.")
        self.assertEqual(client.responses.create.call_count, 2)

    def test_preview_auth_endpoint_requires_membership(self):
        item = self.new_session()
        url = f"/wewnetrzne/podglad/{item.pk}/autoryzuj/"
        self.assertEqual(self.client.get(url, secure=True).status_code, 401)
        token = make_preview_token(item, self.user)
        self.assertEqual(self.client.get(url, {"token": token}, secure=True).status_code, 204)
        self.client.force_login(self.user)
        self.assertEqual(self.client.get(url, secure=True).status_code, 204)

    def test_preview_token_is_carried_by_the_path_for_every_resource(self):
        session_id = "9abeb6c9-4529-4a16-a408-529101b3bd40"
        token = "signed:token.with-safe_parts"
        url = add_preview_token(
            f"https://preview.example/vibe/{session_id}/pliki/index.php?strona=start",
            token,
            session_id,
        )
        self.assertIn(f"/vibe/{session_id}/__vibe_token/signed%3Atoken.with-safe_parts/pliki/index.php", url)
        self.assertIn("__vibe_token=signed%3Atoken.with-safe_parts", url)

    def test_owner_can_delete_session_and_its_entire_workspace(self):
        item = self.new_session()
        workspace_parent = Path(item.workspace_path).parent
        self.client.force_login(self.user)

        response = self.client.post(
            f"/rozmowy/{item.pk}/usun/",
            {"confirmation": "DELETE"},
            secure=True,
        )

        self.assertRedirects(response, "/", fetch_redirect_response=False)
        self.assertFalse(EditSession.objects.filter(pk=item.pk).exists())
        self.assertFalse(workspace_parent.exists())

    def test_another_site_member_cannot_delete_someone_elses_session(self):
        item = self.new_session()
        workspace_parent = Path(item.workspace_path).parent
        other = get_user_model().objects.create_user("adam", password="secret123")
        SiteMembership.objects.create(site=self.site, user=other, role=SiteMembership.Role.PUBLISHER)
        self.client.force_login(other)

        response = self.client.post(
            f"/rozmowy/{item.pk}/usun/",
            {"confirmation": "DELETE"},
            secure=True,
        )

        self.assertEqual(response.status_code, 403)
        self.assertTrue(EditSession.objects.filter(pk=item.pk).exists())
        self.assertTrue(workspace_parent.exists())
