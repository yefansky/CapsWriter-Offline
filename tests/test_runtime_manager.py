# coding: utf-8
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from core.runtime_settings import load_settings, save_settings
from core.client.audio.stream import AudioStreamManager
from core.client.shortcut.shortcut_config import Shortcut
from core.client.shortcut.task import ShortcutTask
from core.client.audio.file_manager import _CREATE_NO_WINDOW
from core.startup import startup_command
from start_manager import log_line_tag, microphone_status_text, read_rule_rows, write_rule_rows


class RuntimeSettingsTests(unittest.TestCase):
    def test_save_and_load_shortcuts_atomically(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            with patch("core.runtime_settings.SETTINGS_PATH", path):
                save_settings({"shortcuts": [{"key": "f8", "type": "keyboard", "enabled": True}]})
                self.assertEqual(load_settings()["shortcuts"][0]["key"], "f8")
                self.assertFalse(path.with_suffix(".json.tmp").exists())

    def test_invalid_settings_falls_back_to_defaults(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            path.write_text("not json", encoding="utf-8")
            with patch("core.runtime_settings.SETTINGS_PATH", path):
                self.assertEqual(load_settings()["shortcuts"][0]["key"], "caps_lock")


class MicrophoneRecoveryTests(unittest.TestCase):
    def test_start_without_microphone_does_not_exit(self):
        app = Mock(); app.state.stream = None
        manager = AudioStreamManager(app)
        with patch("core.client.audio.stream.sd.query_devices", side_effect=Exception("no input")):
            self.assertIsNone(manager.start())

    def test_monitor_restarts_after_microphone_reappears(self):
        app = Mock(); app.state.stream = None
        manager = AudioStreamManager(app)
        manager._running = False
        manager._monitor_stop = Mock()
        manager._monitor_stop.wait.side_effect = [False, True]
        manager.start = Mock()
        with patch("core.client.audio.stream.sd.query_devices", return_value={"index": 7, "name": "USB Mic", "hostapi": 0}):
            manager._monitor_devices()
        manager.start.assert_called_once()


class ManagedModeTests(unittest.TestCase):
    def test_managed_shortcut_does_not_create_console_spinner(self):
        app = Mock()
        app.managed = True
        task = ShortcutTask(app, Shortcut(key="caps_lock"))
        self.assertIsNone(task._status)

    def test_ffmpeg_is_configured_without_console_window(self):
        self.assertIsInstance(_CREATE_NO_WINDOW, int)


class RuleTableStorageTests(unittest.TestCase):
    def test_rule_rows_split_into_two_columns_and_preserve_comments(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "hot-rule.txt"
            path.write_text("# 说明\n原词 = 替换词\n空替换 = \n", encoding="utf-8")
            preserved, rows = read_rule_rows(path)
            self.assertEqual(preserved, ["# 说明"])
            self.assertEqual(rows, [("原词", "替换词"), ("空替换", "")])
            write_rule_rows(path, preserved, [("原词", "新替换"), ("空替换", "")])
            self.assertEqual(
                path.read_text(encoding="utf-8"),
                "# 说明\n原词 = 新替换\n空替换 = \n",
            )


class LogHighlightTests(unittest.TestCase):
    def test_log_levels_are_colored_by_severity(self):
        self.assertEqual(log_line_tag("10:00 ERROR request failed"), "log_error")
        self.assertEqual(log_line_tag("10:00 WARNING retrying"), "log_warning")
        self.assertEqual(log_line_tag("10:00 INFO connected"), "log_info")
        self.assertEqual(log_line_tag("10:00 DEBUG details"), "log_debug")
        self.assertEqual(log_line_tag("===== 客户端日志 ====="), "log_section")


class MicrophoneStatusTests(unittest.TestCase):
    def test_top_status_includes_connected_device_name(self):
        self.assertEqual(
            microphone_status_text({"index": 1, "name": "Wireless Mic Rx"}),
            "麦克风：已连接 · #1 Wireless Mic Rx",
        )


class WindowsStartupTests(unittest.TestCase):
    def test_startup_command_uses_absolute_manager_path(self):
        command = startup_command()
        self.assertIn("start_manager.py", command)
        self.assertIn("pythonw.exe", command)
        self.assertTrue(command.endswith(" --restart"))


if __name__ == "__main__":
    unittest.main()
