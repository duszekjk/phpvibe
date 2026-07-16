from pathlib import Path
import tempfile

from django.test import SimpleTestCase

from deploy.php_preview_cgi import resolve_script


class PhpPreviewCgiTests(SimpleTestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.workspace_root = Path(self.temporary.name).resolve()
        self.session_id = "9abeb6c9-4529-4a16-a408-529101b3bd40"
        self.site = self.workspace_root / self.session_id / "site"
        self.site.mkdir(parents=True)
        self.script = self.site / "index.php"
        self.script.write_text("<?php echo 'ok';", encoding="utf-8")

    def tearDown(self):
        self.temporary.cleanup()

    def test_matching_url_and_workspace_script_are_accepted(self):
        root, script = resolve_script(
            str(self.script),
            f"/vibe/{self.session_id}/index.php",
            workspace_root=self.workspace_root,
        )
        self.assertEqual(root, self.site)
        self.assertEqual(script, self.script)

    def test_script_outside_authorized_session_is_rejected(self):
        outside = self.workspace_root / "outside.php"
        outside.write_text("<?php", encoding="utf-8")
        with self.assertRaises(ValueError):
            resolve_script(
                str(outside),
                f"/vibe/{self.session_id}/index.php",
                workspace_root=self.workspace_root,
            )

    def test_symlinked_php_script_is_rejected(self):
        linked = self.site / "linked.php"
        linked.symlink_to(self.script)
        with self.assertRaises(ValueError):
            resolve_script(
                str(linked),
                f"/vibe/{self.session_id}/linked.php",
                workspace_root=self.workspace_root,
            )
