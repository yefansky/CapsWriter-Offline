# coding: utf-8

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core.software_update import (
    CHECKSUM_ASSET,
    INSTALLER_ASSET,
    ReleaseInfo,
    UpdateCheckError,
    check_for_update,
    is_installed,
    load_release_info,
)


class FakeResponse:
    def __init__(self, payload: dict):
        self.payload = json.dumps(payload).encode("utf-8")

    def read(self):
        return self.payload

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


class SoftwareUpdateTests(unittest.TestCase):
    def test_source_checkout_does_not_opt_into_self_update(self):
        with tempfile.TemporaryDirectory() as directory:
            info = load_release_info(Path(directory))
        self.assertEqual(info.source, "source")
        self.assertFalse(info.update_enabled)

    def test_release_manifest_and_installed_marker(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "release.json").write_text('{"repository":"owner/repo","tag":"v2.6.4"}', encoding="utf-8")
            (root / "installation.json").write_text("{}", encoding="utf-8")
            info = load_release_info(root)
            self.assertEqual(info.repository, "owner/repo")
            self.assertEqual(info.tag, "v2.6.4")
            self.assertTrue(is_installed(root))

    def test_same_tag_reports_no_update(self):
        payload = {"tag_name": "v2.6.4", "assets": []}
        with patch("core.software_update.urlopen", return_value=FakeResponse(payload)):
            self.assertIsNone(check_for_update(ReleaseInfo("owner/repo", "v2.6.4", "release")))

    def test_new_release_requires_installer_and_checksum(self):
        payload = {
            "tag_name": "v2.6.5",
            "name": "CapsWriter v2.6.5",
            "html_url": "https://example.test/release",
            "assets": [
                {"name": INSTALLER_ASSET, "browser_download_url": "https://example.test/setup.exe"},
                {"name": CHECKSUM_ASSET, "browser_download_url": "https://example.test/SHA256SUMS.txt"},
            ],
        }
        with patch("core.software_update.urlopen", return_value=FakeResponse(payload)):
            candidate = check_for_update(ReleaseInfo("owner/repo", "v2.6.4", "release"))
        self.assertEqual(candidate.tag, "v2.6.5")
        self.assertEqual(candidate.installer_url, "https://example.test/setup.exe")

    def test_new_release_without_checksum_is_rejected(self):
        payload = {
            "tag_name": "v2.6.5",
            "assets": [{"name": INSTALLER_ASSET, "browser_download_url": "https://example.test/setup.exe"}],
        }
        with patch("core.software_update.urlopen", return_value=FakeResponse(payload)):
            with self.assertRaisesRegex(UpdateCheckError, "SHA256SUMS"):
                check_for_update(ReleaseInfo("owner/repo", "v2.6.4", "release"))
