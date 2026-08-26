# coding: utf-8
import hashlib
import io
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from config_server import ModelPaths, ServerConfig
import core.server.worker.check_model as check_model_module
from core.server.worker.model_download import (
    ModelDownloadError,
    ModelPackage,
    _download_archive,
    _safe_extract,
    _verify_archive,
    download_and_install,
    ensure_package,
    packages_for_model_type,
)


class FakeResponse(io.BytesIO):
    def __init__(self, payload: bytes, status: int = 200):
        super().__init__(payload)
        self.status = status

    def getcode(self):
        return self.status


def build_package(target_dir: Path, payload: bytes, asset_name: str = "test-model.zip") -> ModelPackage:
    return ModelPackage(
        key="test",
        display_name="测试模型",
        asset_name=asset_name,
        size=len(payload),
        sha256=hashlib.sha256(payload).hexdigest(),
        target_dir=target_dir,
        required_files=(Path("model.bin"),),
    )


def build_zip(entries: dict[str, bytes]) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, content in entries.items():
            archive.writestr(name, content)
    return output.getvalue()


class ModelManifestTests(unittest.TestCase):
    def test_paraformer_also_requires_punctuation_package(self):
        packages = packages_for_model_type("paraformer")
        self.assertEqual([package.key for package in packages], ["paraformer", "punc"])

    def test_qwen_default_uses_smaller_q4_release(self):
        with patch.object(ServerConfig, "qwen_asr_download_variant", "q4_k"):
            package = packages_for_model_type("qwen_asr")[0]
        self.assertEqual(package.asset_name, "Qwen3-ASR-1.7B-q4_k.zip")
        self.assertEqual(
            package.sha256,
            "9b3d2a66a4a26a0404c32085ec838b7c482495a7827919a5aa674de617c2757b",
        )

    def test_unknown_qwen_variant_is_rejected(self):
        with patch.object(ServerConfig, "qwen_asr_download_variant", "unknown"):
            with self.assertRaisesRegex(ValueError, "q4_k、q5_k"):
                packages_for_model_type("qwen_asr")


class ArchiveDownloadTests(unittest.TestCase):
    def test_partial_download_resumes_with_http_range(self):
        payload = b"0123456789" * 100
        with tempfile.TemporaryDirectory() as directory:
            part_path = Path(directory) / "model.zip.part"
            part_path.write_bytes(payload[:137])
            package = build_package(Path(directory) / "installed", payload)
            requests = []

            def opener(request, timeout):
                requests.append((request, timeout))
                return FakeResponse(payload[137:], status=206)

            _download_archive(package, part_path, opener=opener)
            _verify_archive(part_path, package)

        self.assertEqual(requests[0][0].get_header("Range"), "bytes=137-")
        self.assertEqual(requests[0][1], ServerConfig.model_download_timeout)

    def test_server_without_range_support_restarts_download(self):
        payload = b"complete model archive"
        with tempfile.TemporaryDirectory() as directory:
            part_path = Path(directory) / "model.zip.part"
            part_path.write_bytes(b"stale partial")
            package = build_package(Path(directory) / "installed", payload)

            _download_archive(
                package,
                part_path,
                opener=lambda request, timeout: FakeResponse(payload, status=200),
            )

            self.assertEqual(part_path.read_bytes(), payload)

    def test_archive_digest_mismatch_is_rejected(self):
        payload = b"downloaded bytes"
        with tempfile.TemporaryDirectory() as directory:
            part_path = Path(directory) / "model.zip.part"
            part_path.write_bytes(payload)
            package = ModelPackage(
                key="test",
                display_name="测试模型",
                asset_name="model.zip",
                size=len(payload),
                sha256="0" * 64,
                target_dir=Path(directory) / "installed",
                required_files=(Path("model.bin"),),
            )
            with self.assertRaisesRegex(ModelDownloadError, "SHA-256"):
                _verify_archive(part_path, package)


class ArchiveInstallTests(unittest.TestCase):
    def test_zip_path_traversal_is_rejected(self):
        payload = build_zip({"../outside.bin": b"bad"})
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive_path = root / "model.zip"
            destination = root / "extract"
            archive_path.write_bytes(payload)
            destination.mkdir()

            with self.assertRaisesRegex(ModelDownloadError, "越界路径"):
                _safe_extract(archive_path, destination)

            self.assertFalse((root / "outside.bin").exists())

    def test_nested_release_folder_is_found_and_installed(self):
        payload = build_zip({
            "release-folder/model.bin": b"model data",
            "release-folder/config.json": b"{}",
        })
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            model_root = root / "models"
            target = model_root / "InstalledModel"
            package = build_package(target, payload)

            with patch.object(ModelPaths, "model_dir", model_root):
                download_and_install(
                    package,
                    opener=lambda request, timeout: FakeResponse(payload),
                )

            self.assertEqual((target / "model.bin").read_bytes(), b"model data")
            self.assertEqual((target / "config.json").read_bytes(), b"{}")
            self.assertFalse((model_root / ".downloads" / "test-model.zip.part").exists())

    def test_existing_complete_model_skips_network_download(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "model"
            target.mkdir()
            (target / "model.bin").write_bytes(b"ready")
            package = build_package(target, b"unused")

            with patch(
                "core.server.worker.model_download.download_and_install"
            ) as download:
                self.assertFalse(ensure_package(package))

            download.assert_not_called()


class ModelCheckIntegrationTests(unittest.TestCase):
    def test_missing_selected_model_is_downloaded_before_startup(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "model"
            package = build_package(target, b"unused")

            def install(selected):
                selected.target_dir.mkdir(parents=True)
                (selected.target_dir / "model.bin").write_bytes(b"ready")
                return True

            with (
                patch.object(ServerConfig, "model_type", "qwen_asr"),
                patch.object(ServerConfig, "auto_download_models", True),
                patch.object(check_model_module, "packages_for_model_type", return_value=(package,)),
                patch.object(check_model_module, "ensure_package", side_effect=install) as ensure,
                patch.object(check_model_module, "console"),
                patch.object(check_model_module, "logger"),
            ):
                check_model_module.check_model()

            ensure.assert_called_once_with(package)

    def test_disabled_auto_download_exits_with_missing_model(self):
        with tempfile.TemporaryDirectory() as directory:
            package = build_package(Path(directory) / "model", b"unused")
            with (
                patch.object(ServerConfig, "model_type", "qwen_asr"),
                patch.object(ServerConfig, "auto_download_models", False),
                patch.object(check_model_module, "packages_for_model_type", return_value=(package,)),
                patch.object(check_model_module, "ensure_package") as ensure,
                patch.object(check_model_module, "console"),
                patch.object(check_model_module, "logger"),
            ):
                with self.assertRaises(SystemExit):
                    check_model_module.check_model()

            ensure.assert_not_called()


if __name__ == "__main__":
    unittest.main()
