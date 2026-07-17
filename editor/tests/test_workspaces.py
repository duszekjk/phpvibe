from io import BytesIO
from pathlib import Path
import stat
import subprocess
import tempfile
from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.contrib.auth import get_user_model
from django.core.exceptions import ImproperlyConfigured, PermissionDenied, ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from editor.config import load_site_config
from editor.forms import StartSessionForm
from editor.models import ChatMessage, EditSession, PageConversation, Revision, Site, SiteMembership
from editor.navigation import get_or_create_page_conversation, normalize_page_url
from editor.preview_access import add_preview_token, make_preview_token
from editor.services.assistant import AssistantError, _replay_input, run_chat_turn
from editor.services.file_tools import read_file, replace_text, write_file
from editor.services.images import ImageUploadError, process_image_upload
from editor.services.site_links import LinkSuggestion
from editor.services.workspaces import (
    WorkspaceBusyError,
    WorkspaceError,
    changed_paths,
    create_workspace,
    publish_workspace,
    refresh_preview_assets,
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

    def _write_config(self):
        (self.config_dir / "test.toml").write_text(
            f'''name = "Test"\nroot_path = "{self.source}"\nallowed_hosts = ["example.org"]\npreview_url_template = "https://preview.example/{{session_id}}/"\nallowed_extensions = [".php", ".css", ".txt"]\nbackup_path = "{self.backup_dir}"\ndescription = "Testowa strona PHP"\n[[preview_replacements]]\npath = "index.php"\nproduction_text = "<?php "\npreview_text = "<?php /* __PHPVIBE_PREVIEW_TEST__ */ "\nrequired = true\n''',
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

    def image_upload(self, name="duze-zdjecie.jpg", size=(3000, 1800), color=(220, 90, 30)):
        from PIL import Image

        output = BytesIO()
        Image.new("RGB", size, color).save(output, format="JPEG", quality=92)
        return SimpleUploadedFile(name, output.getvalue(), content_type="image/jpeg")

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

    def test_uncommitted_workspace_edit_is_counted_and_published(self):
        item = self.new_session()
        target = Path(item.workspace_path) / "index.php"
        target.write_text(target.read_text(encoding="utf-8").replace("stara treść", "oczekująca zmiana"), encoding="utf-8")
        self.client.force_login(self.user)

        response = self.client.get(item.get_absolute_url(), secure=True)

        self.assertEqual(changed_paths(item), ["index.php"])
        self.assertContains(response, "Zmiany <b>1</b>", html=True)
        self.assertContains(response, "Zatwierdź i opublikuj")
        self.assertNotContains(response, "Publikowanie jest wyłączone")
        self.assertEqual(publish_workspace(item), ["index.php"])
        self.assertIn("oczekująca zmiana", self.source.joinpath("index.php").read_text(encoding="utf-8"))
        self.assertTrue(item.revisions.filter(summary="Zatwierdzenie zmian roboczych").exists())

    def test_legacy_false_publish_setting_cannot_disable_publication(self):
        config_path = self.config_dir / "test.toml"
        config_path.write_text(
            config_path.read_text(encoding="utf-8") + "\npublish_enabled = false\n",
            encoding="utf-8",
        )
        load_site_config.cache_clear()
        item = self.new_session()
        replace_text(item, "index.php", "stara treść", "nowa treść", False, "Zmiana")
        self.client.force_login(self.user)

        response = self.client.get(item.get_absolute_url(), secure=True)

        self.assertContains(response, "Zatwierdź i opublikuj")
        self.assertNotContains(response, "Publikowanie jest wyłączone")
        self.assertEqual(publish_workspace(item), ["index.php"])

    def test_config_requires_backup_path_for_publication(self):
        config_path = self.config_dir / "test.toml"
        config_path.write_text(
            "\n".join(
                line for line in config_path.read_text(encoding="utf-8").splitlines()
                if not line.startswith("backup_path =")
            ),
            encoding="utf-8",
        )
        load_site_config.cache_clear()

        with self.assertRaisesRegex(ImproperlyConfigured, "backup_path"):
            load_site_config("test")

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

    def test_existing_workspace_refreshes_preview_runtime_without_a_new_session(self):
        item = self.new_session()
        bridge = Path(item.workspace_path) / "__phpvibe_preview" / "preview-bridge.js"
        bridge.write_text("obsolete bridge", encoding="utf-8")

        refresh_preview_assets(item)

        content = bridge.read_text(encoding="utf-8")
        self.assertIn('post("link-clicked", { href: targetUrl.href })', content)
        self.assertNotIn("obsolete bridge", content)

    def test_preview_runtime_is_not_committed_with_a_content_edit(self):
        item = self.new_session()
        bridge = Path(item.workspace_path) / "__phpvibe_preview" / "preview-bridge.js"
        bridge.write_text("runtime update", encoding="utf-8")

        replace_text(item, "index.php", "stara treść", "nowa treść", False, "Zmiana")

        revision = item.revisions.get()
        self.assertEqual(revision.changed_files, ["index.php"])
        self.assertEqual(bridge.read_text(encoding="utf-8"), "runtime update")

    def test_binary_assets_are_copied_but_not_added_to_git_history(self):
        (self.source / "photo.jpg").write_bytes(b"binary-image" * 100)

        item = self.new_session()
        root = Path(item.workspace_path)
        tracked = subprocess.run(
            ["git", "ls-files"], cwd=root, check=True, capture_output=True, text=True
        ).stdout.splitlines()

        self.assertTrue((root / "photo.jpg").is_file())
        self.assertNotIn("photo.jpg", tracked)
        self.assertIn("index.php", tracked)

    def test_uploaded_image_creates_optimized_tracked_variants(self):
        item = self.new_session()

        attachment = process_image_upload(item, self.image_upload())

        self.assertEqual(set(attachment["variants"]), {"large", "background", "content", "button"})
        self.assertEqual((attachment["source_width"], attachment["source_height"]), (3000, 1800))
        root = Path(item.workspace_path)
        tracked = subprocess.run(
            ["git", "ls-files"], cwd=root, check=True, capture_output=True, text=True
        ).stdout.splitlines()
        limits = {"large": 2560, "background": 1920, "content": 1280, "button": 800}
        for name, variant in attachment["variants"].items():
            path = root / variant["path"]
            self.assertTrue(path.is_file())
            self.assertEqual(path.read_bytes()[:4], b"RIFF")
            self.assertLessEqual(max(variant["width"], variant["height"]), limits[name])
            self.assertIn(variant["path"], tracked)
        self.assertEqual(item.revisions.count(), 1)
        self.assertEqual(
            set(item.revisions.get().changed_files),
            {variant["path"] for variant in attachment["variants"].values()},
        )

    def test_uploaded_image_is_removed_by_reset(self):
        item = self.new_session()
        attachment = process_image_upload(item, self.image_upload(size=(1200, 800)))

        reset_workspace(item)

        root = Path(item.workspace_path)
        for variant in attachment["variants"].values():
            self.assertFalse((root / variant["path"]).exists())

    def test_uploaded_image_variants_are_published_as_binary_files(self):
        item = self.new_session()
        attachment = process_image_upload(item, self.image_upload(size=(1400, 900)))

        paths = publish_workspace(item)

        self.assertEqual(set(paths), {variant["path"] for variant in attachment["variants"].values()})
        for variant in attachment["variants"].values():
            self.assertEqual(
                (self.source / variant["path"]).read_bytes(),
                (Path(item.workspace_path) / variant["path"]).read_bytes(),
            )

    def test_invalid_image_upload_is_rejected_without_workspace_change(self):
        item = self.new_session()
        upload = SimpleUploadedFile("udawane.jpg", b"not-an-image", content_type="image/jpeg")

        with self.assertRaisesRegex(ImageUploadError, "prawidłowym"):
            process_image_upload(item, upload)

        self.assertEqual(changed_paths(item), [])

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

    @patch("editor.views.get_site_link_suggestions")
    def test_url_suggestions_endpoint_returns_links_for_a_site_member(self, suggestions):
        suggestions.return_value = [
            LinkSuggestion("Wspólnota → Przymierze", "https://example.org/?strona=wspolnota&podstrona=przymierze")
        ]
        self.client.force_login(self.user)

        response = self.client.get(reverse("site_url_suggestions", kwargs={"site_id": self.site.pk}), secure=True)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["suggestions"][0]["label"], "Wspólnota → Przymierze")

    @patch("editor.views.get_site_link_suggestions")
    def test_url_suggestions_endpoint_does_not_expose_sites_without_membership(self, suggestions):
        outsider = get_user_model().objects.create_user("outsider", password="secret123")
        self.client.force_login(outsider)

        response = self.client.get(reverse("site_url_suggestions", kwargs={"site_id": self.site.pk}), secure=True)

        self.assertEqual(response.status_code, 404)
        suggestions.assert_not_called()

    def test_start_session_renders_searchable_url_suggestions(self):
        self.client.force_login(self.user)

        response = self.client.get(reverse("start_session"), secure=True)

        self.assertEqual(response.context["form"].initial["site"], self.site.pk)
        self.assertContains(response, f'<option value="{self.site.pk}" selected>Test</option>', html=True)
        self.assertContains(response, reverse("site_url_suggestions", kwargs={"site_id": 0}))
        self.assertContains(response, "Podpowiedzi ze strony głównej")
        self.assertRegex(response.content.decode(), r'src="/_assets/editor/start-session\.js\?v=[0-9a-f]{12}"')

    def test_page_url_rejects_invalid_port(self):
        with self.assertRaises(ValidationError):
            normalize_page_url("https://example.org:not-a-port/", frozenset({"example.org"}))

    def test_page_url_removes_preview_token_path_and_query(self):
        url = normalize_page_url(
            "https://example.org/__vibe_token/signed%3Atoken/"
            "?strona=wspolnota&podstrona=diakonie&__vibe_token=signed%3Atoken&diakonie=medialna",
            frozenset({"example.org"}),
        )

        self.assertEqual(
            url,
            "https://example.org/?strona=wspolnota&podstrona=diakonie&diakonie=medialna",
        )

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

    def test_config_deduplicates_identical_preview_replacements(self):
        config_path = self.config_dir / "test.toml"
        content = config_path.read_text(encoding="utf-8")
        duplicate = '''\n[[preview_replacements]]
path = "index.php"
production_text = "<?php "
preview_text = "<?php /* __PHPVIBE_PREVIEW_TEST__ */ "
required = true
'''
        config_path.write_text(content + duplicate, encoding="utf-8")
        load_site_config.cache_clear()

        config = load_site_config("test")

        self.assertEqual(len(config.preview_replacements), 1)

    def test_workspace_records_completed_copy_progress(self):
        expected_files = 2
        expected_bytes = sum(path.stat().st_size for path in (self.source / "index.php", self.source / "main.css"))

        item = self.new_session()

        self.assertEqual(item.copy_stage, "Gotowe")
        self.assertEqual(item.copy_files_total, expected_files)
        self.assertEqual(item.copy_files_done, expected_files)
        self.assertEqual(item.copy_bytes_total, expected_bytes)
        self.assertEqual(item.copy_bytes_done, expected_bytes)

    def test_progress_endpoint_reports_copy_size_and_stage(self):
        item = EditSession.objects.create(
            site=self.site,
            owner=self.user,
            title="Kopiowanie",
            target_url="https://example.org/",
            copy_stage="Kopiowanie plików…",
            copy_bytes_total=4096,
            copy_bytes_done=1024,
            copy_files_total=8,
            copy_files_done=3,
        )
        self.client.force_login(self.user)

        response = self.client.get(reverse("session_progress", kwargs={"session_id": item.pk}), secure=True)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {
            "status": "preparing",
            "stage": "Kopiowanie plików…",
            "bytes_total": 4096,
            "bytes_done": 1024,
            "files_total": 8,
            "files_done": 3,
            "error": "",
        })
        self.assertIn("no-store", response.headers["Cache-Control"])

    def test_session_page_is_never_cached(self):
        item = self.new_session()
        self.client.force_login(self.user)

        response = self.client.get(item.get_absolute_url(), secure=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn("no-store", response.headers["Cache-Control"])

    def test_failed_session_does_not_render_a_preview_iframe(self):
        item = EditSession.objects.create(
            site=self.site,
            owner=self.user,
            title="Nieudana kopia",
            target_url="https://example.org/",
            status=EditSession.Status.FAILED,
            error_message="Nie znaleziono transformacji podglądu.",
        )
        self.client.force_login(self.user)

        response = self.client.get(item.get_absolute_url(), secure=True)

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'id="site-preview"')
        self.assertContains(response, "Kopia robocza nie powstała")
        self.assertContains(response, "Nie znaleziono transformacji podglądu.")

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
        self.assertRegex(response.content.decode(), r'src="/_assets/editor/workbench\.js\?v=[0-9a-f]{12}"')
        self.assertRegex(response.content.decode(), r'href="/_assets/editor/workbench\.css\?v=[0-9a-f]{12}"')
        self.assertContains(response, "/__vibe_token/")
        self.assertContains(response, f'/rozmowy/{item.pk}/usun/')
        self.assertNotContains(response, 'id="pwa-install-prompt"')

    def test_obsolete_token_conversation_is_repaired_on_open(self):
        item = self.new_session()
        expected = item.conversations.get()
        broken_url = "https://example.org/__vibe_token/old-token/?strona=start"
        broken = PageConversation.objects.create(
            session=item,
            target_url=broken_url,
            normalized_url=broken_url,
            label="Błędny adres",
        )
        message = ChatMessage.objects.create(
            session=item,
            conversation=broken,
            role=ChatMessage.Role.USER,
            content="Zachowaj tę wiadomość",
        )
        self.client.force_login(self.user)

        response = self.client.get(broken.get_absolute_url(), secure=True)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["conversation"].pk, expected.pk)
        self.assertEqual(response.context["conversation"].target_url, "https://example.org/?strona=start")
        self.assertFalse(PageConversation.objects.filter(pk=broken.pk).exists())
        message.refresh_from_db()
        self.assertEqual(message.conversation_id, expected.pk)

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
        def save_revision(*_args, **_kwargs):
            Revision.objects.create(
                session=item,
                commit_hash="a" * 40,
                summary="Zmieniono tekst",
                changed_files=["index.php"],
            )
            return SimpleNamespace(content="Zmieniono tekst.")

        with patch("editor.views.run_chat_turn", side_effect=save_revision) as run:
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

    def test_inline_text_edit_rejects_false_success_without_file_revision(self):
        item = self.new_session()
        conversation = item.conversations.get()
        self.client.force_login(self.user)
        with patch("editor.views.run_chat_turn", return_value=SimpleNamespace(content="Gotowe.")):
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

        self.assertEqual(response.status_code, 409)
        self.assertFalse(response.json()["ok"])
        self.assertIn("nie zapisało żadnej zmiany", response.json()["error"])

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

    def test_uploaded_image_is_sent_to_responses_api_and_visible_in_chat(self):
        item = self.new_session()
        conversation = item.conversations.get()
        attachment = process_image_upload(item, self.image_upload(size=(1000, 700)))
        final = SimpleNamespace(id="response-image", output=[], output_text="Użyłem zdjęcia.")
        client = SimpleNamespace(responses=SimpleNamespace(create=Mock(return_value=final)))

        with patch("openai.OpenAI", return_value=client):
            message = run_chat_turn(
                item,
                conversation,
                "Ustaw jako tło.",
                model_text="Ustaw jako tło. Wariant background: /example.webp",
                attachments=[attachment],
            )

        request = client.responses.create.call_args.kwargs
        content = request["input"][0]["content"]
        self.assertEqual(content[0]["type"], "input_text")
        self.assertEqual(content[1]["type"], "input_image")
        self.assertEqual(content[1]["detail"], "high")
        self.assertTrue(content[1]["image_url"].startswith("data:image/webp;base64,"))
        self.assertEqual(message.context, {})
        user_message = conversation.messages.get(role=ChatMessage.Role.USER)
        self.assertEqual(user_message.context["attachments"][0]["name"], "duze-zdjecie.jpg")

        self.client.force_login(self.user)
        response = self.client.get(
            reverse("message_attachment", kwargs={
                "session_id": item.pk,
                "message_id": user_message.pk,
                "variant": "content",
            }),
            secure=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "image/webp")
        self.assertEqual(
            b"".join(response.streaming_content),
            (Path(item.workspace_path) / attachment["variants"]["content"]["path"]).read_bytes(),
        )

    def test_chat_upload_processes_image_and_passes_all_variants_to_assistant(self):
        item = self.new_session()
        conversation = item.conversations.get()
        self.client.force_login(self.user)

        with patch("editor.views.run_chat_turn", return_value=SimpleNamespace(content="Gotowe.")) as run:
            response = self.client.post(
                reverse("send_message", kwargs={"session_id": item.pk, "conversation_id": conversation.pk}),
                {"message": "Ustaw zdjęcie jako tło", "image": self.image_upload(size=(900, 600))},
                secure=True,
            )

        self.assertRedirects(response, conversation.get_absolute_url(), fetch_redirect_response=False)
        attachment = run.call_args.kwargs["attachments"][0]
        self.assertEqual(set(attachment["variants"]), {"large", "background", "content", "button"})
        self.assertIn("background-size: cover", run.call_args.kwargs["model_text"])
        self.assertIn(attachment["variants"]["background"]["path"], run.call_args.kwargs["model_text"])

    def test_workbench_renders_image_upload_control(self):
        item = self.new_session()
        self.client.force_login(self.user)

        response = self.client.get(item.get_absolute_url(), secure=True)

        self.assertContains(response, 'enctype="multipart/form-data"')
        self.assertContains(response, 'id="id_image"')
        self.assertContains(response, "Dodaj zdjęcie")

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

    def test_owner_sees_delete_action_on_dashboard(self):
        item = self.new_session()
        self.client.force_login(self.user)

        response = self.client.get(reverse("dashboard"), secure=True)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, reverse("delete_session", kwargs={"session_id": item.pk}))
        self.assertContains(response, "Opcje rozmowy")
        self.assertContains(response, "Usuń rozmowę")
        self.assertContains(response, 'id="pwa-install-prompt"')

    def test_other_site_member_does_not_see_dashboard_delete_action(self):
        item = self.new_session()
        other = get_user_model().objects.create_user("dashboard-viewer", password="secret123")
        SiteMembership.objects.create(site=self.site, user=other, role=SiteMembership.Role.PUBLISHER)
        self.client.force_login(other)

        response = self.client.get(reverse("dashboard"), secure=True)

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, reverse("delete_session", kwargs={"session_id": item.pk}))

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
