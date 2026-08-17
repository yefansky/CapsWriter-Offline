# coding: utf-8
"""本地管理器与运行中的客户端共用的轻量配置存储。"""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any


ROOT_DIR = Path(__file__).resolve().parents[1]
SETTINGS_PATH = ROOT_DIR / "capswriter-settings.json"

DEFAULT_SETTINGS: dict[str, Any] = {
    "shortcuts": [
        {"key": "caps_lock", "type": "keyboard", "suppress": True, "hold_mode": True, "enabled": True},
        {"key": "x2", "type": "mouse", "suppress": True, "hold_mode": True, "enabled": True},
    ],
}


def load_settings() -> dict[str, Any]:
    """返回有效配置；损坏或不存在的文件不影响语音输入启动。"""
    try:
        saved = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
        if not isinstance(saved, dict):
            raise ValueError("配置根节点必须是对象")
    except (OSError, ValueError, json.JSONDecodeError):
        return deepcopy(DEFAULT_SETTINGS)

    result = deepcopy(DEFAULT_SETTINGS)
    shortcuts = saved.get("shortcuts")
    if isinstance(shortcuts, list):
        result["shortcuts"] = shortcuts
    return result


def save_settings(settings: dict[str, Any]) -> None:
    """原子写入，避免客户端在管理器保存中读到半截文件。"""
    payload = {"shortcuts": settings.get("shortcuts", [])}
    temporary = SETTINGS_PATH.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(SETTINGS_PATH)
