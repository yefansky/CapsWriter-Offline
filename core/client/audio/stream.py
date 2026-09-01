# coding: utf-8
"""
音频流管理模块

提供 AudioStreamManager 类用于管理音频输入流，包括流的创建、
启动、停止和设备检测。
"""

from __future__ import annotations

import queue
import time
import threading
from typing import TYPE_CHECKING, Optional

import numpy as np
import sounddevice as sd

from core.client.state import console
from .devices import input_candidates, is_remote_session, preferred_input_device
from .device_events import (
    AudioEndpointEvent,
    CAPTURE_FLOW_ID,
    DEVICE_STATE_ACTIVE,
    create_audio_endpoint_watcher,
)
from . import logger

if TYPE_CHECKING:
    from core.client.state import ClientState
    from ..app import CapsWriterClient



class AudioStreamManager:
    """
    音频流管理器

    负责管理音频输入流的生命周期，包括：
    - 检测和选择音频设备
    - 创建和启动音频流
    - 处理音频数据回调
    - 流的重启和关闭

    Attributes:
        state: 客户端状态实例
        sample_rate: 采样率（默认 48000Hz）
        block_duration: 每个数据块的时长（秒，默认 0.05s）
    """

    SAMPLE_RATE = 48000
    BLOCK_DURATION = 0.05  # 50ms
    DEVICE_MONITOR_INTERVAL = 5.0
    RECONNECT_DEBOUNCE_SECONDS = 0.75
    RECONNECT_RETRY_SECONDS = 0.5
    RECONNECT_MAX_RESOLVE_ATTEMPTS = 3

    def __init__(self, app: CapsWriterClient):
        """
        初始化音频流管理器

        Args:
            app: 客户端 App 实例
        """
        self.app = app
        self._channels = 1
        self._running = False  # 标志是否应该运行
        self._monitor_stop = threading.Event()
        self._monitor_wake = threading.Event()
        self._monitor_thread: Optional[threading.Thread] = None
        self._device_identity = None
        self._remote_session = is_remote_session()
        self._stream_lock = threading.RLock()
        self._endpoint_watcher = None
        self._endpoint_events: queue.SimpleQueue[AudioEndpointEvent] = queue.SimpleQueue()
        self._endpoint_active: dict[str, bool] = {}
        self._default_capture_endpoint_id: Optional[str] = None
        self._pending_reconnects: dict[str, float] = {}
        self._pending_reconnect_attempts: dict[str, int] = {}
        self._confirmed_capture_reconnects: set[str] = set()
        self._reconnect_wait_logged = False

    @property
    def state(self) -> ClientState:
        """快捷访问状态单例"""
        return self.app.state

    def _audio_callback(
        self,
        indata: np.ndarray,
        frames: int,
        time_info,
        status: sd.CallbackFlags
    ) -> None:
        """
        音频数据回调函数

        当音频流接收到新数据时调用，将数据放入异步队列中。
        """
        # 只在录音状态时处理数据
        if not self.state.recording:
            return

        import asyncio

        # 将数据放入队列
        if self.app.loop and self.state.queue_in:
            asyncio.run_coroutine_threadsafe(
                self.state.queue_in.put({
                    'type': 'data',
                    'time': time.time(),
                    'data': indata.copy(),
                }),
                self.app.loop
            )

    def _on_stream_finished(self) -> None:
        """音频流结束回调"""
        if not threading.main_thread().is_alive():
            return
        if not self._running:
            return

        logger.info("音频流意外结束，正在尝试重启...")
        self.reopen()

    def start(self) -> Optional[sd.InputStream]:
        """
        启动音频流

        Returns:
            创建的音频输入流，如果失败返回 None
        """
        with self._stream_lock:
            if self._running:
                logger.debug("音频流已在运行，跳过启动")
                return self.state.stream

            # 先重建 PortAudio 的设备清单。RDP 断开后，长驻进程可能继续缓存
            # 已不存在的“远程音频”，只查询默认设备会永远重试同一个坏索引。
            try:
                candidates = input_candidates(refresh=True)
            except UnicodeDecodeError:
                logger.warning("无法获取麦克风设备名称（编码问题）")
                return None
            except Exception as e:
                logger.error(f"检测麦克风设备失败: {e}")
                return None

            if not candidates:
                logger.error("未找到麦克风设备")
                return None

            # 按优先级逐个尝试。本机控制台会排除远程音频；默认设备缺失时，
            # 优先真实麦克风，并把立体声混音/线路输入留作最后兜底。
            last_error: Exception | None = None
            for device in candidates:
                device_index = device['index']
                self._channels = min(2, device['max_input_channels'])
                device_name = device.get('name', '未知设备')
                logger.info(f"尝试输入设备: #{device_index} {device_name}, 声道数: {self._channels}")
                try:
                    stream = sd.InputStream(
                        samplerate=self.SAMPLE_RATE,
                        blocksize=int(self.BLOCK_DURATION * self.SAMPLE_RATE),
                        device=device_index,
                        dtype="float32",
                        channels=self._channels,
                        callback=self._audio_callback,
                        finished_callback=self._on_stream_finished,
                    )
                    stream.start()

                    self.state.stream = stream
                    self._running = True
                    self._device_identity = (device_index, device.get('name'), device.get('hostapi'))
                    self._remote_session = is_remote_session()
                    if not self.app.managed:
                        console.print(f'使用音频设备：[italic]{device_name}，声道数：{self._channels}', end='\n\n')
                    logger.info(f"音频流已绑定到输入设备: #{device_index} {device_name}")
                    return stream
                except (sd.PortAudioError, ValueError) as exc:
                    last_error = exc
                    logger.warning(f"输入设备 #{device_index} 无法打开，尝试下一项: {exc}")
                except Exception as exc:
                    last_error = exc
                    logger.warning(f"创建输入流失败，尝试下一项: {exc}", exc_info=True)

            logger.error(f"所有输入设备均无法打开: {last_error}")
            if last_error and '-9999' in str(last_error) and not self.app.managed:
                console.print("""
[bold red]检测到麦克风被占用或权限异常（错误码 -9999）[/bold red]
请尝试以下解决方案：

  1. 设置 > 隐私和安全性 > 麦克风，将「允许桌面应用访问麦克风」打开
  2. 状态栏右下角音量图标 > 右键菜单 > 声音 > 麦克风的属性，关闭「允许应用程序独占控制该设备」
  3. 状态栏右下角音量图标 > 右键菜单 > 声音 > 麦克风的属性，关闭「增强效果」
""")
            return None

    def start_device_monitor(self) -> None:
        """每 5 秒检查一次默认输入设备，支持拔插后的自动恢复。"""
        if self._monitor_thread and self._monitor_thread.is_alive():
            return
        self._monitor_stop.clear()
        self._monitor_wake.clear()
        self._monitor_thread = threading.Thread(target=self._monitor_devices, name="mic-device-monitor", daemon=True)
        self._monitor_thread.start()
        try:
            watcher = create_audio_endpoint_watcher(self._enqueue_audio_endpoint_event)
            watcher.start()
            self._endpoint_watcher = watcher
            logger.info("[麦克风重连] Windows 音频端点监听已启动")
        except Exception as exc:
            self._endpoint_watcher = None
            logger.error(f"[麦克风重连] Windows 音频端点监听启动失败: {exc}", exc_info=True)

    def _enqueue_audio_endpoint_event(self, event: AudioEndpointEvent) -> None:
        """Core Audio 回调只入队并唤醒监控线程，不能直接重建音频流。"""
        self._endpoint_events.put(event)
        self._monitor_wake.set()

    @staticmethod
    def _endpoint_label(endpoint_id: str) -> str:
        return endpoint_id if len(endpoint_id) <= 56 else f"{endpoint_id[:28]}...{endpoint_id[-20:]}"

    def _schedule_endpoint_reconnect(self, endpoint_id: str) -> None:
        if not endpoint_id or endpoint_id in self._pending_reconnects:
            return
        self._pending_reconnects[endpoint_id] = time.monotonic() + self.RECONNECT_DEBOUNCE_SECONDS
        self._pending_reconnect_attempts[endpoint_id] = 0
        self._reconnect_wait_logged = False

    def _drain_audio_endpoint_events(self) -> None:
        """在监控线程里合并 Added/Active/DefaultChanged 等重复通知。"""
        while True:
            try:
                event = self._endpoint_events.get_nowait()
            except queue.Empty:
                return

            endpoint_id = event.endpoint_id
            if event.kind == "removed" or (
                event.kind == "state_changed" and event.state_id != DEVICE_STATE_ACTIVE
            ):
                self._endpoint_active[endpoint_id] = False
                self._pending_reconnects.pop(endpoint_id, None)
                self._pending_reconnect_attempts.pop(endpoint_id, None)
                self._confirmed_capture_reconnects.discard(endpoint_id)
                continue

            if event.kind == "default_changed":
                if event.flow_id != CAPTURE_FLOW_ID:
                    continue
                if endpoint_id == self._default_capture_endpoint_id:
                    continue
                self._default_capture_endpoint_id = endpoint_id
                self._endpoint_active[endpoint_id] = True
                self._schedule_endpoint_reconnect(endpoint_id)
                continue

            if event.kind not in {"added", "state_changed"}:
                continue
            if event.kind == "state_changed" and event.state_id != DEVICE_STATE_ACTIVE:
                continue
            if self._endpoint_active.get(endpoint_id) is True:
                continue
            self._endpoint_active[endpoint_id] = True
            self._schedule_endpoint_reconnect(endpoint_id)

    def _next_monitor_delay(self) -> float:
        if not self._pending_reconnects:
            return self.DEVICE_MONITOR_INTERVAL
        delay = min(self._pending_reconnects.values()) - time.monotonic()
        if delay <= 0 and self.state.recording:
            return 0.25
        return max(0.0, min(self.DEVICE_MONITOR_INTERVAL, delay))

    def _resolve_ready_capture_reconnects(self) -> None:
        """防抖结束后确认事件属于捕获端点；暂不可查询时有限重试。"""
        if self._endpoint_watcher is None:
            return
        now = time.monotonic()
        ready_ids = [
            endpoint_id
            for endpoint_id, ready_at in self._pending_reconnects.items()
            if ready_at <= now and endpoint_id not in self._confirmed_capture_reconnects
        ]
        for endpoint_id in ready_ids:
            try:
                is_capture = self._endpoint_watcher.is_capture_endpoint(endpoint_id)
            except Exception as exc:
                attempts = self._pending_reconnect_attempts.get(endpoint_id, 0) + 1
                self._pending_reconnect_attempts[endpoint_id] = attempts
                if attempts >= self.RECONNECT_MAX_RESOLVE_ATTEMPTS:
                    self._pending_reconnects.pop(endpoint_id, None)
                    self._pending_reconnect_attempts.pop(endpoint_id, None)
                    logger.warning(
                        "[麦克风重连] 无法确认重新上线端点是否为麦克风，已跳过: "
                        f"endpoint={self._endpoint_label(endpoint_id)}, error={exc}"
                    )
                else:
                    self._pending_reconnects[endpoint_id] = now + self.RECONNECT_RETRY_SECONDS
                continue

            if not is_capture:
                self._pending_reconnects.pop(endpoint_id, None)
                self._pending_reconnect_attempts.pop(endpoint_id, None)
                continue
            self._confirmed_capture_reconnects.add(endpoint_id)

    def _reopen_for_reconnected_endpoints(self) -> bool:
        """同一批重新上线的捕获端点只执行一次安全重开。"""
        self._resolve_ready_capture_reconnects()
        ready_capture_ids = [
            endpoint_id
            for endpoint_id in self._confirmed_capture_reconnects
            if self._pending_reconnects.get(endpoint_id, float("inf")) <= time.monotonic()
        ]
        if not ready_capture_ids:
            return False

        labels = ", ".join(self._endpoint_label(endpoint_id) for endpoint_id in sorted(ready_capture_ids))
        if self.state.recording:
            if not self._reconnect_wait_logged:
                logger.info(
                    "[麦克风重连] 检测到麦克风重新连接，当前正在录音，"
                    f"已挂起一次音频流重建: endpoint={labels}"
                )
                self._reconnect_wait_logged = True
            return False

        for endpoint_id in ready_capture_ids:
            self._pending_reconnects.pop(endpoint_id, None)
            self._pending_reconnect_attempts.pop(endpoint_id, None)
            self._confirmed_capture_reconnects.discard(endpoint_id)
        self._reconnect_wait_logged = False
        logger.warning(
            "[麦克风重连] 检测到麦克风重新连接，正在执行一次音频流重建: "
            f"endpoint={labels}"
        )
        stream = self.reopen()
        if stream is None:
            logger.error("[麦克风重连] 音频流重建失败，将由设备监控继续恢复")
        else:
            logger.info("[麦克风重连] 音频流重建完成，已重新绑定可用麦克风")
        return True

    def _monitor_devices(self) -> None:
        while not self._monitor_stop.is_set():
            self._monitor_wake.wait(self._next_monitor_delay())
            self._monitor_wake.clear()
            if self._monitor_stop.is_set():
                break
            self._drain_audio_endpoint_events()
            if self._reopen_for_reconnected_endpoints():
                continue

            remote_session = is_remote_session()
            if remote_session != self._remote_session:
                mode = "远程桌面" if remote_session else "本机控制台"
                logger.info(f"检测到登录会话切换为{mode}，重新枚举输入设备")
                self._remote_session = remote_session
                self.reopen()
                continue

            try:
                # 流已停止时才能安全重建 PortAudio 设备清单。
                # 这样麦克风重连后不会永久留在旧的空设备快照里。
                device = preferred_input_device(refresh=not self._running)
                if device is None:
                    raise sd.PortAudioError("未找到可用输入设备")
                identity = (device['index'], device.get('name'), device.get('hostapi'))
            except (sd.PortAudioError, ValueError):
                if self._running:
                    logger.warning("检测到麦克风已断开，等待设备重新接入")
                    self.stop()
                continue
            except Exception as exc:
                logger.debug(f"检查麦克风失败，将在下一轮重试: {exc}")
                continue

            if not self._running:
                logger.info("检测到可用麦克风，自动恢复音频流")
                self.start()
            elif identity != self._device_identity:
                logger.info("默认麦克风已变化，自动重建音频流")
                self.reopen()

    def stop(self) -> None:
        """停止音频流"""
        with self._stream_lock:
            if not self._running:
                return

            self._running = False  # 标记为停止
            if self.state.stream is not None:
                try:
                    self.state.stream.close()
                    logger.debug("音频流已停止")
                except Exception as e:
                    logger.debug(f"停止音频流时发生错误: {e}")
                finally:
                    self.state.stream = None

    def shutdown(self) -> None:
        """停止监控线程和音频流，仅供应用退出时调用。"""
        if self._endpoint_watcher is not None:
            try:
                self._endpoint_watcher.stop()
                logger.info("[麦克风重连] Windows 音频端点监听已停止")
            except Exception as exc:
                logger.warning(f"[麦克风重连] 停止 Windows 音频端点监听失败: {exc}")
            finally:
                self._endpoint_watcher = None
        self._monitor_stop.set()
        self._monitor_wake.set()
        self.stop()

    def reopen(self) -> Optional[sd.InputStream]:
        """
        重新启动音频流

        Returns:
            新创建的音频输入流
        """
        with self._stream_lock:
            logger.info("正在重启音频流并重新枚举默认麦克风...")
            self.stop()
            time.sleep(0.1)
            return self.start()
