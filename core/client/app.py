# coding: utf-8
"""
CapsWriter Offline 客户端主程序门面类 (Facade)

采用外观模式统一管理音频流 (AudioStreamManager)、
识别结果处理 (ResultProcessor) 和快捷键管理 (ShortcutManager)。
"""

import os
import sys
import asyncio
import threading
import time
from pathlib import Path

from .state import ClientState
from . import logger
from config_client import ClientConfig as Config, __version__
from core.tools.signal_handler import register_signal
from .state import console
from .connection import WebSocketManager
from typing import TYPE_CHECKING, Optional
from .manager import (
    TrayManager,
    MicRunner, FileRunner
)
from .audio.stream import AudioStreamManager
from .shortcut.shortcut_manager import ShortcutManager
from .shortcut.shortcut_config import Shortcut
from core.runtime_settings import SETTINGS_PATH, load_settings

from .udp.udp_control import UDPController

from .hotword.manager import HotwordManager
from .llm.llm_handler import LLMHandler
from .output.text_output import TextOutput
from .diary.diary_writer import DiaryWriter
from core.tools.empty_working_set import empty_current_working_set
from platform import system



class CapsWriterClient:
    """
    CapsWriter 客户端门面类
    
    管理的外部接口简洁：start()。
    """
    def __init__(self, managed: bool = False):
        # 确保正确的工作目录
        self.base_dir = Path(__file__).parents[2]
        self.managed = managed
        os.chdir(self.base_dir)
            
        # 初始化事件循环
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
            
        # 初始化状态容器
        self.state = ClientState(app=self)

        # 初始化热词管理器
        self.hotword = HotwordManager(
            hotword_files=None,
            threshold=Config.hot_thresh,
            similar_threshold=Config.hot_similar
        )

        # 4. 初始化 LLM 润色系统
        self.llm = LLMHandler(app=self)
        
        self.output = TextOutput()
        self.diary = DiaryWriter(base_path=self.base_dir)

        # 初始化各管理器
        self.ws = WebSocketManager(self)
        self.tray = TrayManager(self)

        # 实例化硬件资源管理组件
        self.stream = AudioStreamManager(self)
        self.shortcut = ShortcutManager(self, [Shortcut(**sc) for sc in Config.shortcuts])
        self.udp = UDPController(self.shortcut)
        self._settings_stop = threading.Event()
        self._settings_thread: Optional[threading.Thread] = None
        self._settings_mtime: Optional[float] = None

        # 内存清理
        empty_current_working_set()

    def stop(self):
        """
        统一释放所有资源（清理顺序：硬件 -> 托盘 -> WebSocket -> State）
        """
        logger.info("正在执行 CapsWriterClient 资源释放...")

        # 1. 停止核心运行组件
        self.udp.stop()
        self.shortcut.stop()
        self.stream.shutdown()
        self._settings_stop.set()

        # 2. 托盘资源
        self.tray.stop()

        # 3. 关闭监控
        self.hotword.stop()
        self.llm.stop()

        # 4. 关闭 WebSocket 连接
        self.ws.close_sync()

        # 5. 重置 State
        try:
            self.state.reset()
        except Exception as e:
            logger.warning(f"重置状态时发生错误: {e}")

        # 6. 停止事件循环（最后一步，确保前面的异步操作已调度）
        self.loop.stop()

        logger.info("资源释放完成")
        console.print('[green4]再见！')

    def start_settings_watcher(self) -> None:
        """管理器保存快捷键后无需重启客户端即可生效。"""
        if self._settings_thread and self._settings_thread.is_alive():
            return
        self._settings_stop.clear()
        self._settings_mtime = SETTINGS_PATH.stat().st_mtime if SETTINGS_PATH.exists() else None
        self._settings_thread = threading.Thread(target=self._watch_settings, name="settings-watcher", daemon=True)
        self._settings_thread.start()

    def _watch_settings(self) -> None:
        while not self._settings_stop.wait(0.5):
            try:
                mtime = SETTINGS_PATH.stat().st_mtime
            except OSError:
                mtime = None
            if mtime == self._settings_mtime:
                continue
            self._settings_mtime = mtime
            try:
                configs = load_settings()['shortcuts']
                replacement = ShortcutManager(self, [Shortcut(**item) for item in configs])
                old = self.shortcut
                self.shortcut = replacement
                replacement.start()
                old.stop()
                self.udp.manager = replacement
                logger.info("管理器配置已热更新：快捷键监听器已重建")
            except Exception as exc:
                logger.error(f"应用管理器快捷键配置失败，保留原配置: {exc}", exc_info=True)


    def start(self):
        """
        启动客户端 (唯一入口)
        
        自动根据命令行参数识别模式。内部管理异步循环。
        """

        # 注册退出函数
        register_signal(self.stop)

        files = [Path(f) for f in sys.argv[1:] if os.path.exists(f)]

        if files:
            # 文件转录模式
            runner = FileRunner(self, files)
        else:
            # 麦克风实时模式
            runner = MicRunner(self)
        
        try:
            self.loop.run_until_complete(runner.run())
        except RuntimeError:
            ...
