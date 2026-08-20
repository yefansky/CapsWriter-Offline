# coding: utf-8
"""Windows 会话切换下的音频输入设备选择。"""

from __future__ import annotations

import ctypes
import os
import threading
from ctypes import wintypes
from typing import Any

import sounddevice as sd


_PORTAUDIO_LOCK = threading.RLock()
_REMOTE_MARKERS = ("远程音频", "remote audio", "remoteaudio", "rdp audio")
_PREFERRED_MIC_MARKERS = ("麦克风", "microphone", "mic input", " mic ")
_UNSUITABLE_INPUT_MARKERS = (
    "立体声混音",
    "stereo mix",
    "what u hear",
    "wave out mix",
    "loopback",
    "monitor of",
    "线路输入",
    "line input",
    "声音映射器",
    "sound mapper",
    "主声音捕获驱动程序",
    "primary sound capture",
)


def is_remote_session() -> bool:
    """返回当前进程所在 Windows 会话是否通过远程桌面连接。"""
    if os.name != "nt":
        return os.environ.get("SESSIONNAME", "").upper().startswith("RDP-")

    buffer = ctypes.c_void_p()
    byte_count = wintypes.DWORD()
    session_id = wintypes.DWORD()
    try:
        if not ctypes.windll.kernel32.ProcessIdToSessionId(os.getpid(), ctypes.byref(session_id)):
            raise ctypes.WinError()
        # WTSClientProtocolType: 0 表示本机控制台，2 表示 RDP。
        if not ctypes.windll.wtsapi32.WTSQuerySessionInformationW(
            None,
            session_id.value,
            16,
            ctypes.byref(buffer),
            ctypes.byref(byte_count),
        ):
            raise ctypes.WinError()
        protocol = ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ushort)).contents.value
        return protocol == 2
    except Exception:
        return os.environ.get("SESSIONNAME", "").upper().startswith("RDP-")
    finally:
        if buffer.value:
            ctypes.windll.wtsapi32.WTSFreeMemory(buffer)


def is_remote_input(device: dict[str, Any]) -> bool:
    name = str(device.get("name", "")).casefold()
    return any(marker in name for marker in _REMOTE_MARKERS)


def is_unsuitable_input(device: dict[str, Any]) -> bool:
    """返回设备是否不适合麦克风听写。

    立体声混音和系统映射器会在物理麦克风断开后仍然成功打开，
    却只生成近似静音的“假健康”音频流。
    """
    name = str(device.get("name", "")).casefold()
    return any(marker in name for marker in _UNSUITABLE_INPUT_MARKERS)


def refresh_portaudio_devices() -> None:
    """重新初始化 PortAudio，清掉远程桌面切换前缓存的设备清单。"""
    terminate = getattr(sd, "_terminate", None)
    initialize = getattr(sd, "_initialize", None)
    if not callable(terminate) or not callable(initialize):
        return
    with _PORTAUDIO_LOCK:
        terminate()
        initialize()


def _input_candidates() -> list[dict[str, Any]]:
    try:
        default_device = dict(sd.query_devices(kind="input"))
    except (sd.PortAudioError, ValueError):
        default_device = None

    devices = [
        dict(device)
        for device in sd.query_devices()
        if device.get("max_input_channels", 0) > 0 and not is_unsuitable_input(device)
    ]
    if default_device and is_unsuitable_input(default_device):
        default_device = None
    remote_session = is_remote_session()
    if not remote_session:
        devices = [device for device in devices if not is_remote_input(device)]
        if default_device and is_remote_input(default_device):
            default_device = None

    default_index = default_device.get("index") if default_device else None

    def rank(device: dict[str, Any]) -> tuple[int, int]:
        name = str(device.get("name", "")).casefold()
        if device.get("index") == default_index:
            priority = 0
        elif remote_session and is_remote_input(device):
            priority = 1
        elif any(marker in name for marker in _PREFERRED_MIC_MARKERS):
            priority = 2
        else:
            priority = 3
        return priority, int(device.get("index", 999999))

    return sorted(devices, key=rank)


def input_candidates(*, refresh: bool = False, refresh_if_empty: bool = True) -> list[dict[str, Any]]:
    """按当前会话排序输入设备；本机会话不会选残留的“远程音频”。"""
    with _PORTAUDIO_LOCK:
        if refresh:
            refresh_portaudio_devices()
        candidates = _input_candidates()
        if not candidates and refresh_if_empty and not refresh:
            refresh_portaudio_devices()
            candidates = _input_candidates()
        return candidates


def preferred_input_device(*, refresh: bool = False) -> dict[str, Any] | None:
    candidates = input_candidates(refresh=refresh)
    return candidates[0] if candidates else None
