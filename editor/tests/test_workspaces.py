from pathlib import Path
import tempfile
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.test import TestCase, override_settings

from editor.config import load_site_config
from editor.forms import StartSessionForm
from editor.models import EditSession, Site, SiteMembership
from editor.navigation import get_or_create_page_conversation
from editor.preview_access import make_preview_token
from editor.services.assistant import _replay_input
from editor.services.file_tools import read_file, replace_text, write_file
from editor.services.workspaces import WorkspaceError, create_workspace, publish_workspace, reset_workspace, safe_path


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
            f'''name = "Test"\nroot_path = "{self.source}"\nallowed_hosts = ["example.org"]\npreview_url_template = "https://preview.example/{{session_id}}/"\nallowed_extensions = [".php", ".css"]\npublish_enabled = {str(publish).lower()}\nbackup_path = "{self.backup_dir}"\ndescription = "Testowa strona PHP"\n[[preview_replacements]]\npath = "index.php"\nproduction_text = "<?php "\npreview_text = "<?php /* __PHPVIBE_PREVIEW_TEST__ */ "\nrequired = true\n''',
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

    def test_protected_file_is_hidden_from_assistant(self):
        (self.source / "secret.php").write_text("<?php return 'token';", encoding="utf-8")
        item = self.new_session()
        with self.assertRaises(PermissionDenied):
            read_file(item, "secret.php")

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

    def test_form_rejects_url_from_another_host(self):
        form = StartSessionForm(
            data={"site": self.site.pk, "title": "Test", "target_url": "https://evil.example/path"},
            user=self.user,
        )
        self.assertFalse(form.is_valid())
        self.assertIn("target_url", form.errors)

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

    def test_preview_auth_endpoint_requires_membership(self):
        item = self.new_session()
        url = f"/wewnetrzne/podglad/{item.pk}/autoryzuj/"
        self.assertEqual(self.client.get(url, secure=True).status_code, 401)
        token = make_preview_token(item, self.user)
        self.assertEqual(self.client.get(url, {"token": token}, secure=True).status_code, 204)
        self.client.force_login(self.user)
        self.assertEqual(self.client.get(url, secure=True).status_code, 204)
