from __future__ import annotations

import hashlib
import tempfile
import unittest
import zipfile
from pathlib import Path

from qqpet_app.updater import UpdateError, extract_executable, is_newer


class UpdaterTests(unittest.TestCase):
    def test_semantic_version_comparison(self) -> None:
        self.assertTrue(is_newer("v1.6.0", "1.5.0"))
        self.assertTrue(is_newer("1.5.1", "v1.5.0"))
        self.assertFalse(is_newer("v1.5.0", "1.5.0"))
        self.assertFalse(is_newer("1.4.9", "1.5.0"))

    def test_extracts_only_expected_executable(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            archive = root / "release.zip"
            with zipfile.ZipFile(archive, "w") as package:
                package.writestr("package/QQ宠物助手.exe", b"safe-binary")
                package.writestr("../config.yaml", b"must-not-extract")
            output = extract_executable(archive, root / "updates")
            self.assertEqual(output.read_bytes(), b"safe-binary")
            self.assertFalse((root / "config.yaml").exists())

    def test_rejects_archive_without_unique_executable(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            archive = root / "release.zip"
            with zipfile.ZipFile(archive, "w") as package:
                package.writestr("a/QQ宠物助手.exe", b"one")
                package.writestr("b/QQ宠物助手.exe", b"two")
            with self.assertRaises(UpdateError):
                extract_executable(archive, root / "updates")

