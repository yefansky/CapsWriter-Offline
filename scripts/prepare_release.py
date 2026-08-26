#!/usr/bin/env python3
"""在打包前写入不可变的 Release 标识。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tag", required=True)
    parser.add_argument("--repository", default="yefansky/CapsWriter-Offline")
    args = parser.parse_args()
    manifest = {
        "tag": args.tag,
        "repository": args.repository,
    }
    (ROOT / "release.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
