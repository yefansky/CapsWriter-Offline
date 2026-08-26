# coding: utf-8
"""CapsWriter 官方模型包的检测、断点下载、校验和安全安装。"""

from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from config_server import ModelDownloadLinks, ModelPaths, ServerConfig as Config
from . import logger


_MIB = 1024 * 1024


class ModelDownloadError(RuntimeError):
    """模型下载、校验或安装失败。"""


@dataclass(frozen=True)
class ModelPackage:
    key: str
    display_name: str
    asset_name: str
    size: int
    sha256: str
    target_dir: Path
    required_files: tuple[Path, ...]

    @property
    def url(self) -> str:
        return f"{ModelDownloadLinks.release_assets.rstrip('/')}/{self.asset_name}"


def _relative_files(target_dir: Path, paths: tuple[Path, ...]) -> tuple[Path, ...]:
    return tuple(path.relative_to(target_dir) for path in paths)


def _package(key: str, qwen_variant: str | None = None) -> ModelPackage:
    if key == "fun_asr_nano":
        target = ModelPaths.fun_asr_nano_gguf_dir
        return ModelPackage(
            key=key,
            display_name="Fun-ASR-Nano-GGUF",
            asset_name="Fun-ASR-Nano-GGUF.zip",
            size=834231292,
            sha256="26a557923aedc44f1a3033d0a9b9c7b13cbb551f57fb9fd4b15a67bb4b57f998",
            target_dir=target,
            required_files=_relative_files(target, (
                ModelPaths.fun_asr_nano_gguf_encoder_adaptor,
                ModelPaths.fun_asr_nano_gguf_ctc,
                ModelPaths.fun_asr_nano_gguf_llm_decode,
                ModelPaths.fun_asr_nano_gguf_token,
            )),
        )
    if key == "sensevoice":
        target = ModelPaths.sensevoice_dir
        return ModelPackage(
            key=key,
            display_name="SenseVoice-Small-ONNX",
            asset_name="Sensevoice-Small-ONNX.zip",
            size=433798984,
            sha256="3948b5761f12db1c01d7a7e596294b43b0316aa5c7a8df77981e78573997dcbb",
            target_dir=target,
            required_files=_relative_files(target, (
                ModelPaths.sensevoice_encoder,
                ModelPaths.sensevoice_decoder,
                ModelPaths.sensevoice_tokenizer,
            )),
        )
    if key == "paraformer":
        target = ModelPaths.paraformer_dir
        return ModelPackage(
            key=key,
            display_name="Paraformer",
            asset_name="Paraformer.zip",
            size=239979687,
            sha256="a12a3f9791483329441c94ad759cbcf258d7246784a6d368cd3c591add4d888b",
            target_dir=target,
            required_files=_relative_files(target, (
                ModelPaths.paraformer_model,
                ModelPaths.paraformer_tokens,
            )),
        )
    if key == "punc":
        target = ModelPaths.punc_model_dir.parent
        return ModelPackage(
            key=key,
            display_name="Punct-CT-Transformer",
            asset_name="Punct-CT-Transformer.zip",
            size=271770396,
            sha256="de106e6cf13764bd3124f31864bc30158f04961788765b63262bdd5ba21fa421",
            target_dir=target,
            required_files=_relative_files(target, (ModelPaths.punc_model_dir,)),
        )
    if key == "qwen_asr":
        variant = (qwen_variant or Config.qwen_asr_download_variant).lower()
        variants = {
            "q4_k": (
                "Qwen3-ASR-1.7B-q4_k.zip",
                1410584449,
                "9b3d2a66a4a26a0404c32085ec838b7c482495a7827919a5aa674de617c2757b",
            ),
            "q5_k": (
                "Qwen3-ASR-1.7B-q5_k.zip",
                1951570656,
                "f40040fe62a5ef0c09f8699fdbcb30f18bb8ae2bcd515ed4954e1f62b8b0e88f",
            ),
        }
        if variant not in variants:
            raise ValueError(f"不支持的 Qwen3-ASR 下载规格：{variant}；可选 q4_k、q5_k")
        asset_name, size, digest = variants[variant]
        target = ModelPaths.qwen3_asr_gguf_dir
        return ModelPackage(
            key=key,
            display_name=f"Qwen3-ASR-1.7B-{variant}",
            asset_name=asset_name,
            size=size,
            sha256=digest,
            target_dir=target,
            required_files=_relative_files(target, (
                ModelPaths.qwen3_asr_gguf_encoder_frontend,
                ModelPaths.qwen3_asr_gguf_encoder_backend,
                ModelPaths.qwen3_asr_gguf_llm_decode,
            )),
        )
    if key == "force_aligner":
        target = ModelPaths.force_aligner_gguf_dir
        return ModelPackage(
            key=key,
            display_name="Qwen3-ForcedAligner-0.6B",
            asset_name="Qwen3-ForcedAligner-0.6B.zip",
            size=626205170,
            sha256="8fa035db150e1621a01cbb313bf152b593818b0f15dd94180452f22fc77fbf43",
            target_dir=target,
            required_files=_relative_files(target, (
                ModelPaths.force_aligner_gguf_encoder_frontend,
                ModelPaths.force_aligner_gguf_encoder_backend,
                ModelPaths.force_aligner_gguf_llm_decode,
            )),
        )
    raise ValueError(f"未知模型包：{key}")


def packages_for_model_type(model_type: str) -> tuple[ModelPackage, ...]:
    """返回当前识别引擎启动前必须具备的模型包。"""
    normalized = model_type.strip().lower()
    if normalized == "paraformer":
        return (_package("paraformer"), _package("punc"))
    if normalized in {"fun_asr_nano", "sensevoice", "qwen_asr"}:
        return (_package(normalized),)
    raise ValueError(f"不支持的模型类型：{model_type}")


def optional_package(key: str) -> ModelPackage:
    """返回按功能延迟下载的可选模型包。"""
    return _package(key)


def missing_files(package: ModelPackage) -> list[Path]:
    return [package.target_dir / relative for relative in package.required_files
            if not (package.target_dir / relative).is_file()]


def _human_size(value: int) -> str:
    size = float(value)
    for unit in ("B", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


def _verify_archive(path: Path, package: ModelPackage) -> None:
    actual_size = path.stat().st_size
    if actual_size != package.size:
        raise ModelDownloadError(
            f"{package.display_name} 下载大小不正确："
            f"{_human_size(actual_size)} / {_human_size(package.size)}"
        )
    digest = hashlib.sha256()
    with path.open("rb") as archive:
        while chunk := archive.read(4 * _MIB):
            digest.update(chunk)
    actual_digest = digest.hexdigest()
    if actual_digest.lower() != package.sha256.lower():
        raise ModelDownloadError(
            f"{package.display_name} SHA-256 校验失败；下载文件可能损坏"
        )


def _download_archive(
    package: ModelPackage,
    part_path: Path,
    opener: Callable = urlopen,
) -> None:
    existing = part_path.stat().st_size if part_path.exists() else 0
    if existing > package.size:
        part_path.unlink()
        existing = 0
    if existing == package.size:
        return

    headers = {"User-Agent": "CapsWriter-Offline-Model-Downloader/1.0"}
    if existing:
        headers["Range"] = f"bytes={existing}-"
    request = Request(package.url, headers=headers)
    try:
        response = opener(request, timeout=Config.model_download_timeout)
    except (HTTPError, URLError, TimeoutError, OSError) as exc:
        raise ModelDownloadError(f"无法连接模型下载地址：{exc}") from exc

    with response:
        status = getattr(response, "status", None) or response.getcode()
        resumed = bool(existing and status == 206)
        if existing and not resumed:
            logger.info("下载服务器未接受断点续传，将从头下载")
            existing = 0
        mode = "ab" if resumed else "wb"
        downloaded = existing
        last_percent = -1
        with part_path.open(mode) as output:
            while True:
                chunk = response.read(4 * _MIB)
                if not chunk:
                    break
                output.write(chunk)
                downloaded += len(chunk)
                percent = min(100, int(downloaded * 100 / package.size))
                if percent >= last_percent + 2 or downloaded == package.size:
                    logger.info(
                        f"正在下载 {package.display_name}：{percent}% "
                        f"({_human_size(downloaded)} / {_human_size(package.size)})"
                    )
                    last_percent = percent
                if downloaded > package.size:
                    raise ModelDownloadError(f"{package.display_name} 下载数据超过发布大小")


def _safe_extract(archive_path: Path, destination: Path) -> None:
    root = destination.resolve()
    with zipfile.ZipFile(archive_path) as archive:
        for info in archive.infolist():
            mode = (info.external_attr >> 16) & 0o170000
            if mode == 0o120000:
                raise ModelDownloadError(f"模型压缩包包含不允许的符号链接：{info.filename}")
            target = (destination / info.filename).resolve()
            if target != root and root not in target.parents:
                raise ModelDownloadError(f"模型压缩包包含越界路径：{info.filename}")
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(info) as source, target.open("wb") as output:
                shutil.copyfileobj(source, output, length=4 * _MIB)


def _locate_model_root(extracted_dir: Path, package: ModelPackage) -> Path:
    candidates = [extracted_dir, *(path for path in extracted_dir.rglob("*") if path.is_dir())]
    matches = [candidate for candidate in candidates
               if all((candidate / relative).is_file() for relative in package.required_files)]
    if not matches:
        expected = "、".join(path.as_posix() for path in package.required_files)
        raise ModelDownloadError(
            f"{package.display_name} 压缩包结构不正确，未找到：{expected}"
        )
    return min(matches, key=lambda path: len(path.relative_to(extracted_dir).parts))


def _merge_model_tree(source: Path, target: Path) -> None:
    target.mkdir(parents=True, exist_ok=True)
    for source_path in source.rglob("*"):
        relative = source_path.relative_to(source)
        target_path = target / relative
        if source_path.is_dir():
            target_path.mkdir(parents=True, exist_ok=True)
            continue
        target_path.parent.mkdir(parents=True, exist_ok=True)
        os.replace(source_path, target_path)


def download_and_install(package: ModelPackage, opener: Callable = urlopen) -> None:
    """下载、校验并安装一个模型包；失败时保留 `.part` 供下次续传。"""
    download_dir = ModelPaths.model_dir / ".downloads"
    download_dir.mkdir(parents=True, exist_ok=True)
    part_path = download_dir / f"{package.asset_name}.part"

    required_free = max(package.size * 3, package.size + 512 * _MIB)
    free = shutil.disk_usage(download_dir).free
    if free < required_free:
        raise ModelDownloadError(
            f"磁盘空间不足：下载并解压 {package.display_name} 至少需要约 "
            f"{_human_size(required_free)}，当前可用 {_human_size(free)}"
        )

    logger.info(f"未找到 {package.display_name}，开始从官方 Release 自动下载")
    try:
        if part_path.exists() and part_path.stat().st_size == package.size:
            try:
                _verify_archive(part_path, package)
            except ModelDownloadError:
                part_path.unlink()
        _download_archive(package, part_path, opener=opener)
        _verify_archive(part_path, package)

        with tempfile.TemporaryDirectory(
            prefix=f".{package.key}-extract-",
            dir=download_dir,
        ) as temporary:
            extracted_dir = Path(temporary)
            logger.info(f"正在解压并安装 {package.display_name}")
            _safe_extract(part_path, extracted_dir)
            source = _locate_model_root(extracted_dir, package)
            _merge_model_tree(source, package.target_dir)

        still_missing = missing_files(package)
        if still_missing:
            names = "、".join(path.name for path in still_missing)
            raise ModelDownloadError(f"{package.display_name} 安装后仍缺少：{names}")
        part_path.unlink(missing_ok=True)
        logger.info(f"{package.display_name} 下载、校验和安装完成")
    except (zipfile.BadZipFile, OSError) as exc:
        raise ModelDownloadError(f"{package.display_name} 安装失败：{exc}") from exc


def ensure_package(package: ModelPackage, opener: Callable = urlopen) -> bool:
    """确保模型包可用；返回本次是否执行了下载。"""
    if not missing_files(package):
        return False
    if not Config.auto_download_models:
        names = "、".join(path.name for path in missing_files(package))
        raise ModelDownloadError(f"自动下载已关闭，缺少模型文件：{names}")
    download_and_install(package, opener=opener)
    return True
