from django.template import Context, Template
from django.test import SimpleTestCase
from django.urls import reverse

from editor.runtime_assets import ASSET_NAMES, asset_path, asset_version


class RuntimeAssetTests(SimpleTestCase):
    def test_editor_assets_are_served_from_running_release(self):
        for name in ASSET_NAMES:
            with self.subTest(name=name):
                response = self.client.get(
                    reverse("runtime_asset", kwargs={"name": name}),
                    {"v": asset_version(name)},
                    secure=True,
                )
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.content, asset_path(name).read_bytes())
                self.assertIn("immutable", response.headers["Cache-Control"])

    def test_template_tag_does_not_use_collectstatic_copy(self):
        rendered = Template(
            "{% load versioned_static %}{% versioned_static 'editor/workbench.js' %}"
        ).render(Context())

        self.assertTrue(rendered.startswith("/_assets/editor/workbench.js?v="))
        self.assertNotIn("/static/", rendered)

    def test_unknown_runtime_asset_is_not_exposed(self):
        response = self.client.get("/_assets/editor/settings.py", secure=True)

        self.assertEqual(response.status_code, 404)

    def test_mobile_workbench_stacks_a_desktop_scaled_preview_above_chat(self):
        css = asset_path("workbench.css").read_text(encoding="utf-8")
        javascript = asset_path("workbench.js").read_text(encoding="utf-8")

        self.assertIn("grid-template-rows: auto minmax(0, 1fr)", css)
        self.assertIn("aspect-ratio: 16 / 9", css)
        self.assertIn("width: 1280px", css)
        self.assertIn("height: 720px", css)
        self.assertIn("--mobile-preview-scale", javascript)
