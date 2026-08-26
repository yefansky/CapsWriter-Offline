# coding: utf-8
"""Release 元数据和软件更新检查。

这里仅负责检查与描述最新 Release。实际下载、校验和安装由发行包中的
CapsWriter-Update.exe 独立执行，避免正在运行的管理器覆盖自身文件。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from core.runtime_settings import ROOT_DIR


DEFAULT_REPOSITORY = "yefansky/CapsWriter-Offline"
INSTALLER_ASSET = "CapsWriter-Offline-Setup.exe"
CHECKSUM_ASSET = "SHA256SUMS.txt"


class UpdateCheckError(RuntimeError):
    """无法查询 GitHub Release。"""


@dataclass(frozen=True)
class ReleaseInfo:
    repository: str
    tag: str
    source: str

    @property
    def update_enabled(self) -> bool:
        return self.source == "release" and bool(self.tag)


@dataclass(frozen=True)
class UpdateCandidate:
    tag: str
    name: str
    installer_url: str
    checksum_url: str
    release_url: str


def load_release_info(root: Path = ROOT_DIR) -> ReleaseInfo:
    """读取打包时写入的版本信息；源码目录明确不参与自动覆盖更新。"""
    manifest = root / "release.json"
    if not manifest.is_file():
        return ReleaseInfo(DEFAULT_REPOSITORY, "", "source")
    try:
        raw = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ReleaseInfo(DEFAULT_REPOSITORY, "", "source")
    repository = str(raw.get("repository") or DEFAULT_REPOSITORY).strip()
    tag = str(raw.get("tag") or "").strip()
    if "/" not in repository or not tag:
        return ReleaseInfo(DEFAULT_REPOSITORY, "", "source")
    return ReleaseInfo(repository, tag, "release")


def is_installed(root: Path = ROOT_DIR) -> bool:
    """安装包会写入标记；绿色包不自动改写原目录。"""
    return (root / "installation.json").is_file()


def _asset_url(assets: list[dict], name: str) -> str:
    for asset in assets:
        if asset.get("name") == name and isinstance(asset.get("browser_download_url"), str):
            return asset["browser_download_url"]
    raise UpdateCheckError(f"最新版本缺少发布文件：{name}")


def check_for_update(info: ReleaseInfo, timeout: int = 12) -> UpdateCandidate | None:
    """查询最新稳定版；相同 tag 即表示当前已是最新。"""
    if not info.update_enabled:
        return None
    endpoint = f"https://api.github.com/repos/{info.repository}/releases/latest"
    request = Request(
        endpoint,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "CapsWriter-Offline-Updater/1.0",
        },
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (HTTPError, URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        raise UpdateCheckError(f"无法检查更新：{exc}") from exc

    tag = str(payload.get("tag_name") or "").strip()
    if not tag:
        raise UpdateCheckError("最新 Release 未提供版本标签")
    if tag == info.tag:
        return None
    assets = payload.get("assets")
    if not isinstance(assets, list):
        raise UpdateCheckError("最新 Release 未提供可下载文件")
    return UpdateCandidate(
        tag=tag,
        name=str(payload.get("name") or tag),
        installer_url=_asset_url(assets, INSTALLER_ASSET),
        checksum_url=_asset_url(assets, CHECKSUM_ASSET),
        release_url=str(payload.get("html_url") or ""),
    )
