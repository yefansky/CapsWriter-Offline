#!/usr/bin/env python3
"""生成不含模型的绿色 ZIP 与供更新器校验的 SHA-256 清单。"""

from __future__ import annotations

import argparse
import hashlib
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist" / "CapsWriter-Offline"
RELEASE = ROOT / "release"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(4 * 1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def write_portable_zip(tag: str) -> Path:
    output = RELEASE / "CapsWriter-Offline-Portable.zip"
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path in sorted(DIST.rglob("*")):
            if not path.is_file():
                continue
            relative = path.relative_to(DIST)
            if relative.parts and relative.parts[0] == "models":
                continue
            archive.write(path, Path("CapsWriter-Offline") / relative)
    return output


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tag", required=True)
    args = parser.parse_args()
    required = [
        DIST / "start_manager.exe",
        DIST / "start_server.exe",
        DIST / "start_client.exe",
        DIST / "CapsWriter-Update.exe",
        DIST / "release.json",
    ]
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        raise SystemExit("构建产物不完整：\n" + "\n".join(missing))
    RELEASE.mkdir(exist_ok=True)
    write_portable_zip(args.tag)
    checksums = sorted(path for path in RELEASE.iterdir() if path.is_file() and path.name != "SHA256SUMS.txt")
    (RELEASE / "SHA256SUMS.txt").write_text(
        "".join(f"{sha256(path)}  {path.name}\n" for path in checksums),
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
