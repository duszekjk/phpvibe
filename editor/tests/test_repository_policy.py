from pathlib import Path, PurePosixPath
import subprocess
import tomllib
import unittest


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def tracked_files() -> tuple[PurePosixPath, ...]:
    result = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=REPOSITORY_ROOT,
        check=True,
        capture_output=True,
    )
    return tuple(PurePosixPath(item.decode()) for item in result.stdout.split(b"\0") if item)


class RepositoryPolicyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        try:
            cls.tracked = tracked_files()
        except (FileNotFoundError, subprocess.CalledProcessError) as exc:
            raise unittest.SkipTest(f"Test wymaga kopii roboczej Git: {exc}") from exc

    def test_server_specific_configuration_is_not_tracked(self):
        forbidden = [
            str(path)
            for path in self.tracked
            if path.suffix.lower() in {".conf", ".plist"}
            or {"sites-enabled", "sites-available"}.intersection(path.parts)
        ]
        self.assertEqual(
            forbidden,
            [],
            "Konfiguracja Apache i LaunchAgent musi pozostać poza repozytorium: "
            + ", ".join(forbidden),
        )

    def test_runtime_and_private_files_are_not_tracked(self):
        forbidden = []
        for path in self.tracked:
            if path.name in {".env", "db.sqlite3", ".DS_Store"}:
                forbidden.append(str(path))
            elif path.suffix == ".pyc" or "__pycache__" in path.parts:
                forbidden.append(str(path))
            elif path.parts and path.parts[0] in {"staticfiles", "var"}:
                forbidden.append(str(path))
            elif (
                len(path.parts) == 2
                and path.parts[0] == "site_configs"
                and path.suffix == ".toml"
            ):
                forbidden.append(str(path))
        self.assertEqual(
            forbidden,
            [],
            "Pliki uruchomieniowe lub prywatne nie mogą być śledzone: " + ", ".join(forbidden),
        )

    def test_deployment_helpers_keep_executable_git_mode(self):
        result = subprocess.run(
            ["git", "ls-files", "--stage", "deploy/preview_auth_map.py", "deploy/php_preview_cgi.py"],
            cwd=REPOSITORY_ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        modes = {line.split(maxsplit=3)[3]: line.split(maxsplit=1)[0] for line in result.stdout.splitlines()}
        self.assertEqual(modes.get("deploy/preview_auth_map.py"), "100755")
        self.assertEqual(modes.get("deploy/php_preview_cgi.py"), "100755")

    def test_example_site_configuration_has_safe_defaults(self):
        example = REPOSITORY_ROOT / "site_configs" / "jerozolima.toml.example"
        with example.open("rb") as handle:
            config = tomllib.load(handle)

        self.assertFalse(config["publish_enabled"])
        self.assertEqual(config["preview_url_template"].count("{session_id}"), 1)
        self.assertTrue(config["allowed_hosts"])
        self.assertIn(".env", config["protected_paths"])
        self.assertTrue(config["preview_replacements"])

    def test_templates_reference_existing_static_assets(self):
        expected = {
            "templates/base.html": ("editor/app.css",),
            "templates/editor/session_detail.html": (
                "editor/workbench.css",
                "editor/workbench.js",
            ),
        }
        static_root = REPOSITORY_ROOT / "editor" / "static"
        for template_path, assets in expected.items():
            template = (REPOSITORY_ROOT / template_path).read_text(encoding="utf-8")
            for asset in assets:
                with self.subTest(template=template_path, asset=asset):
                    self.assertIn(asset, template)
                    self.assertTrue((static_root / asset).is_file())


if __name__ == "__main__":
    unittest.main()
