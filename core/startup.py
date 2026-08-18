# coding: utf-8
"""Windows 登录启动项：只使用当前用户的注册表，不要求管理员权限。"""

from __future__ import annotations

import winreg

from core.runtime_settings import ROOT_DIR


RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE_NAME = "CapsWriterOfflineManager"


def startup_command() -> str:
    """返回可从任意工作目录启动管理器的绝对命令。"""
    pythonw = ROOT_DIR / ".venv" / "Scripts" / "pythonw.exe"
    manager = ROOT_DIR / "start_manager.py"
    return f'"{pythonw}" "{manager}" --restart'


def is_startup_enabled() -> bool:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            value, _ = winreg.QueryValueEx(key, VALUE_NAME)
        return bool(value)
    except FileNotFoundError:
        return False
    except OSError:
        return False


def set_startup_enabled(enabled: bool) -> None:
    """创建或移除本软件登录项；不会触碰其他软件的启动项。"""
    if enabled:
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            winreg.SetValueEx(key, VALUE_NAME, 0, winreg.REG_SZ, startup_command())
        return
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            winreg.DeleteValue(key, VALUE_NAME)
    except FileNotFoundError:
        pass

