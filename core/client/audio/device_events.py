# coding: utf-8
"""Windows 音频端点事件监听。

系统回调只把轻量事件交给上层；设备查询和 PortAudio 重建由客户端监控线程执行，
避免在 Core Audio 回调线程里阻塞或获取音频流锁。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Callable, Optional


DEVICE_STATE_ACTIVE = 0x00000001
DEVICE_STATE_DISABLED = 0x00000002
DEVICE_STATE_NOTPRESENT = 0x00000004
DEVICE_STATE_UNPLUGGED = 0x00000008
CAPTURE_FLOW_ID = 1


@dataclass(frozen=True)
class AudioEndpointEvent:
    """一次 Windows 音频端点变化通知。"""

    kind: str
    endpoint_id: str
    state_id: Optional[int] = None
    flow_id: Optional[int] = None


class WindowsAudioEndpointWatcher:
    """使用 Windows Core Audio 监听端点新增、移除和状态变化。"""

    def __init__(self, on_event: Callable[[AudioEndpointEvent], None]):
        self._on_event = on_event
        self._enumerator = None
        self._callback = None
        self._started = False

    def start(self) -> None:
        if self._started:
            return
        if os.name != "nt":
            raise RuntimeError("Windows 音频端点监听仅支持 Windows")

        from pycaw.callbacks import MMNotificationClient
        from pycaw.utils import AudioUtilities

        sink = self._on_event

        class NotificationClient(MMNotificationClient):
            def on_device_added(self, added_device_id):
                sink(AudioEndpointEvent("added", str(added_device_id)))

            def on_device_removed(self, removed_device_id):
                sink(AudioEndpointEvent("removed", str(removed_device_id)))

            def on_device_state_changed(self, device_id, new_state, new_state_id):
                sink(
                    AudioEndpointEvent(
                        "state_changed",
                        str(device_id),
                        state_id=int(new_state_id),
                    )
                )

            def on_default_device_changed(
                self,
                flow,
                flow_id,
                role,
                role_id,
                default_device_id,
            ):
                if default_device_id:
                    sink(
                        AudioEndpointEvent(
                            "default_changed",
                            str(default_device_id),
                            flow_id=int(flow_id),
                        )
                    )

        enumerator = AudioUtilities.GetDeviceEnumerator()
        callback = NotificationClient()
        enumerator.RegisterEndpointNotificationCallback(callback)
        self._enumerator = enumerator
        self._callback = callback
        self._started = True

    def is_capture_endpoint(self, endpoint_id: str) -> bool:
        """端点事件离开系统回调后，再确认它是否为录音输入设备。"""
        import comtypes
        from pycaw.utils import AudioUtilities

        comtypes.CoInitialize()
        try:
            return AudioUtilities.GetEndpointDataFlow(endpoint_id, outputType=1) == CAPTURE_FLOW_ID
        finally:
            comtypes.CoUninitialize()

    def stop(self) -> None:
        if not self._started:
            return
        try:
            self._enumerator.UnregisterEndpointNotificationCallback(self._callback)
        finally:
            self._started = False
            self._callback = None
            self._enumerator = None


def create_audio_endpoint_watcher(
    on_event: Callable[[AudioEndpointEvent], None],
) -> WindowsAudioEndpointWatcher:
    return WindowsAudioEndpointWatcher(on_event)
