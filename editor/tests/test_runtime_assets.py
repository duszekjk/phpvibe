from django.template import Context, Template
from django.test import SimpleTestCase
from django.urls import reverse
from PIL import Image

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

    def test_web_app_manifest_is_installable_and_icons_are_public(self):
        response = self.client.get(reverse("web_app_manifest"), secure=True)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["Content-Type"], "application/manifest+json")
        manifest = response.json()
        self.assertEqual(manifest["display"], "standalone")
        self.assertEqual(manifest["start_url"], "/")
        self.assertEqual({item["sizes"] for item in manifest["icons"]}, {"192x192", "512x512"})
        self.assertIn("maskable", {item["purpose"] for item in manifest["icons"]})
        for icon in manifest["icons"]:
            with self.subTest(icon=icon["src"]):
                icon_response = self.client.get(icon["src"], secure=True)
                self.assertEqual(icon_response.status_code, 200)
                self.assertEqual(icon_response.headers["Content-Type"], "image/png")

    def test_pwa_icons_have_required_dimensions_and_opaque_corners(self):
        expected = {
            "apple-touch-icon.png": (180, 180),
            "pwa-icon-192.png": (192, 192),
            "pwa-icon-512.png": (512, 512),
            "pwa-icon-maskable-512.png": (512, 512),
        }
        for name, dimensions in expected.items():
            with self.subTest(name=name), Image.open(asset_path(name)) as icon:
                self.assertEqual(icon.size, dimensions)
                self.assertEqual(icon.convert("RGBA").getpixel((0, 0))[3], 255)

    def test_base_template_declares_manifest_and_safe_viewport(self):
        response = self.client.get(reverse("login"), secure=True)

        self.assertContains(response, 'rel="manifest"')
        self.assertContains(response, 'rel="apple-touch-icon"')
        self.assertContains(response, "viewport-fit=cover")
        self.assertNotContains(response, 'id="pwa-install-prompt"')
        self.assertRegex(response.content.decode(), r'src="/_assets/editor/pwa-install\.js\?v=[0-9a-f]{12}"')

    def test_install_prompt_exists_only_in_dashboard_template_and_hidden_button_stays_hidden(self):
        from django.conf import settings

        dashboard = (settings.BASE_DIR / "templates" / "editor" / "dashboard.html").read_text(encoding="utf-8")
        session = (settings.BASE_DIR / "templates" / "editor" / "session_detail.html").read_text(encoding="utf-8")
        css = asset_path("app.css").read_text(encoding="utf-8")

        self.assertIn('id="pwa-install-prompt"', dashboard)
        self.assertNotIn('id="pwa-install-prompt"', session)
        self.assertIn(".pwa-install-prompt .button[hidden]", css)
        self.assertIn("display: none", css)
