"""Precompiled runtime integrity, source matching and release selection."""

import contextlib
import hashlib
import importlib.util
import io
import json
import struct
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "native_package", ROOT / "installation/validate_package.py"
)
package_check = importlib.util.module_from_spec(spec)
spec.loader.exec_module(package_check)


class PackageChecks(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.project = Path(self.tmp.name)
        self.source = self.project / "touchscreen"
        self.package = self.source / "precompiled/armv7"
        for path in (
            "src/main.c",
            "runtime/start.sh",
            "developer-tools/build.sh",
            "developer-tools/build_assets.py",
            "developer-tools/package_arm.py",
            "developer-tools/package.json",
            "developer-tools/package-lock.json",
        ):
            destination = self.source / path
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text("source input\n")
        (self.project / "VERSION").write_text("1.0\n")
        (self.project / "install.sh").write_text("installer\n")
        files = (
            "openklipper-screen",
            "VERSION",
            "assets/nav-house-56.png",
            "licenses/LVGL-LICENSE",
            "licenses/cJSON-LICENSE",
            "licenses/Liberation-LICENSE",
            "licenses/Noto-LICENSE",
            "licenses/Lucide-LICENSE",
        )
        self.manifest = {
            "format": 1,
            "target": "armv7-linux-gnueabihf",
            "source_inputs": package_check.source_inputs(self.source),
            "files": {},
        }
        self.manifest["source_fingerprint"] = package_check.fingerprint(
            self.manifest["source_inputs"]
        )
        for name in files:
            destination = self.package / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            if name == "openklipper-screen":
                data = bytearray(20)
                data[:6] = b"\x7fELF\x01\x01"
                struct.pack_into("<H", data, 18, 40)
                destination.write_bytes(data)
                destination.chmod(0o755)
            else:
                destination.write_text("packaged resource\n")
            self.manifest["files"][name] = hashlib.sha256(
                destination.read_bytes()
            ).hexdigest()
        self.write_manifest()

    def write_manifest(self):
        (self.package / "manifest.json").write_text(json.dumps(self.manifest))

    def test_valid_package_and_documentation_only_change(self):
        package_check.validate(self.source, self.package)
        (self.source / "README.md").write_text("Documentation only")
        package_check.validate(self.source, self.package)

    def test_source_change_requires_package_update(self):
        (self.source / "src/main.c").write_text("changed runtime")
        with self.assertRaisesRegex(ValueError, "rebuilding"):
            package_check.validate(self.source, self.package)

    def test_corrupted_or_missing_files_fail(self):
        icon = self.package / "assets/nav-house-56.png"
        icon.write_text("corrupt")
        with self.assertRaisesRegex(ValueError, "checksum"):
            package_check.validate(self.source, self.package)
        icon.unlink()
        with self.assertRaises(OSError):
            package_check.validate(self.source, self.package)

    def test_invalid_package_path_is_rejected(self):
        self.manifest["files"]["../escape"] = "invalid"
        self.write_manifest()
        with self.assertRaisesRegex(ValueError, "path"):
            package_check.validate(self.source, self.package)

    def test_non_arm_and_non_executable_runtime_fail(self):
        binary = self.package / "openklipper-screen"
        binary.chmod(0o644)
        with self.assertRaisesRegex(ValueError, "executable"):
            package_check.validate(self.source, self.package)
        binary.chmod(0o755)
        binary.write_bytes(b"wrong architecture")
        self.manifest["files"]["openklipper-screen"] = hashlib.sha256(
            binary.read_bytes()
        ).hexdigest()
        self.write_manifest()
        with self.assertRaisesRegex(ValueError, "ARM"):
            package_check.validate(self.source, self.package)


class ReleaseSelection(unittest.TestCase):
    def setUp(self):
        script = (ROOT.parent / "install.sh").read_text()
        self.code = script.split("<<'PY_RELEASE'\n", 1)[1].split("\nPY_RELEASE", 1)[0]

    def select(self, releases=None, error=None):
        output = io.StringIO()
        response = mock.MagicMock()
        response.read.return_value = json.dumps(releases).encode()
        with (
            mock.patch("sys.argv", ["release_selector", "owner/repo"]),
            mock.patch(
                "urllib.request.urlopen", return_value=response, side_effect=error
            ),
            contextlib.redirect_stdout(output),
            contextlib.redirect_stderr(io.StringIO()),
        ):
            exec(compile(self.code, "installer-release-selector", "exec"), {})
        return output.getvalue().strip()

    def test_published_alpha_release_selected_and_drafts_ignored(self):
        selected = self.select(
            [
                {"draft": True, "tag_name": "draft"},
                {"draft": False, "prerelease": True, "tag_name": "v1.2.3-alpha.1"},
            ]
        )
        self.assertEqual(selected, "v1.2.3-alpha.1")

    def test_network_failure_and_empty_releases_never_fall_back_to_main(self):
        for options in ({"releases": []}, {"error": OSError("network unavailable")}):
            with self.assertRaises(SystemExit) as result:
                self.select(**options)
            self.assertEqual(result.exception.code, 1)
