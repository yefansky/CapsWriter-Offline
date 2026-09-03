# coding: utf-8
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, call, patch

import sounddevice as sd

from core.runtime_settings import load_settings, save_settings
from core.client.audio.stream import AudioStreamManager
from core.client.audio.devices import input_candidates, is_remote_input, is_unsuitable_input
from core.client.audio.device_events import (
    AudioEndpointEvent,
    DEVICE_STATE_ACTIVE,
    DEVICE_STATE_UNPLUGGED,
    WindowsAudioEndpointWatcher,
)
from core.client.shortcut.shortcut_config import Shortcut
from core.client.shortcut.task import ShortcutTask
from core.client.audio.file_manager import _CREATE_NO_WINDOW
from core.startup import startup_command
from start_manager import (
    CapsWriterManager,
    build_client_hotword_entry,
    log_line_tag,
    microphone_status_text,
    read_rule_rows,
    reconnect_log_excerpt,
    local_server_is_available,
    force_stop_local_capswriter_server,
    server_hotword_help,
    server_hotword_supported,
    stop_managed_process_tree,
    write_rule_rows,
)
from core.client.hotword.hot_phoneme import PhonemeCorrector


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
    def setUp(self):
        logger_patcher = patch("core.client.audio.stream.logger")
        self.stream_logger = logger_patcher.start()
        self.addCleanup(logger_patcher.stop)

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
        manager._monitor_stop.is_set.side_effect = [False, False, True]
        manager._monitor_wake = Mock()
        manager.start = Mock()
        with patch("core.client.audio.stream.preferred_input_device", return_value={"index": 7, "name": "USB Mic", "hostapi": 0}) as preferred:
            manager._monitor_devices()
        preferred.assert_called_once_with(refresh=True)
        manager.start.assert_called_once()

    def test_local_session_excludes_stale_remote_audio_and_prefers_real_microphone(self):
        devices = [
            {"index": 1, "name": "远程音频", "hostapi": 0, "max_input_channels": 2},
            {"index": 19, "name": "立体声混音 (Realtek Stereo Mix)", "hostapi": 3, "max_input_channels": 2},
            {"index": 21, "name": "麦克风 (Realtek HD Audio Mic input)", "hostapi": 3, "max_input_channels": 2},
        ]
        with (
            patch("core.client.audio.devices.is_remote_session", return_value=False),
            patch("core.client.audio.devices.sd.query_devices", side_effect=[devices[0], devices]),
        ):
            candidates = input_candidates(refresh_if_empty=False)
        self.assertEqual([device["index"] for device in candidates], [21])
        self.assertTrue(is_remote_input(devices[0]))
        self.assertTrue(is_unsuitable_input(devices[1]))

    def test_remote_session_does_not_fall_back_to_stereo_mix(self):
        devices = [
            {"index": 0, "name": "立体声混音 (Realtek Stereo Mix)", "hostapi": 3, "max_input_channels": 2},
            {"index": 2, "name": "Microsoft Sound Mapper - Input", "hostapi": 0, "max_input_channels": 2},
        ]
        with (
            patch("core.client.audio.devices.is_remote_session", return_value=True),
            patch("core.client.audio.devices.sd.query_devices", side_effect=[devices[0], devices]),
        ):
            candidates = input_candidates(refresh_if_empty=False)
        self.assertEqual(candidates, [])

    def test_reconnected_capture_endpoint_reopens_once_for_duplicate_notifications(self):
        app = Mock(); app.state.stream = None; app.state.recording = False
        manager = AudioStreamManager(app)
        manager._endpoint_watcher = Mock()
        manager._endpoint_watcher.is_capture_endpoint.return_value = True
        manager.reopen = Mock(return_value=Mock())

        manager._enqueue_audio_endpoint_event(AudioEndpointEvent("added", "mic-1"))
        manager._enqueue_audio_endpoint_event(
            AudioEndpointEvent("state_changed", "mic-1", state_id=DEVICE_STATE_ACTIVE)
        )
        manager._drain_audio_endpoint_events()
        manager._pending_reconnects["mic-1"] = 0

        self.assertTrue(manager._reopen_for_reconnected_endpoints())
        manager.reopen.assert_called_once()
        self.assertEqual(manager._pending_reconnects, {})
        self.assertEqual(manager._confirmed_capture_reconnects, set())

    def test_reconnect_waits_for_current_recording_then_reopens_once(self):
        app = Mock(); app.state.stream = None; app.state.recording = True
        manager = AudioStreamManager(app)
        manager._endpoint_watcher = Mock()
        manager._endpoint_watcher.is_capture_endpoint.return_value = True
        manager.reopen = Mock(return_value=Mock())
        manager._enqueue_audio_endpoint_event(AudioEndpointEvent("added", "mic-1"))
        manager._drain_audio_endpoint_events()
        manager._pending_reconnects["mic-1"] = 0

        self.assertFalse(manager._reopen_for_reconnected_endpoints())
        manager.reopen.assert_not_called()

        app.state.recording = False
        self.assertTrue(manager._reopen_for_reconnected_endpoints())
        manager.reopen.assert_called_once()

    def test_render_endpoint_arrival_does_not_reopen_microphone(self):
        app = Mock(); app.state.stream = None; app.state.recording = False
        manager = AudioStreamManager(app)
        manager._endpoint_watcher = Mock()
        manager._endpoint_watcher.is_capture_endpoint.return_value = False
        manager.reopen = Mock(return_value=Mock())
        manager._enqueue_audio_endpoint_event(AudioEndpointEvent("added", "speaker-1"))
        manager._drain_audio_endpoint_events()
        manager._pending_reconnects["speaker-1"] = 0

        self.assertFalse(manager._reopen_for_reconnected_endpoints())
        manager.reopen.assert_not_called()
        self.assertEqual(manager._pending_reconnects, {})

    def test_offline_then_active_creates_a_new_reconnect_generation(self):
        app = Mock(); app.state.stream = None; app.state.recording = False
        manager = AudioStreamManager(app)
        manager._endpoint_watcher = Mock()
        manager._endpoint_watcher.is_capture_endpoint.return_value = True
        manager.reopen = Mock(return_value=Mock())

        manager._enqueue_audio_endpoint_event(AudioEndpointEvent("added", "mic-1"))
        manager._drain_audio_endpoint_events()
        manager._pending_reconnects["mic-1"] = 0
        manager._reopen_for_reconnected_endpoints()

        manager._enqueue_audio_endpoint_event(
            AudioEndpointEvent("state_changed", "mic-1", state_id=DEVICE_STATE_UNPLUGGED)
        )
        manager._enqueue_audio_endpoint_event(
            AudioEndpointEvent("state_changed", "mic-1", state_id=DEVICE_STATE_ACTIVE)
        )
        manager._drain_audio_endpoint_events()
        manager._pending_reconnects["mic-1"] = 0
        manager._reopen_for_reconnected_endpoints()

        self.assertEqual(manager.reopen.call_count, 2)

    def test_remote_session_keeps_remote_audio_as_default(self):
        devices = [
            {"index": 1, "name": "Remote Audio", "hostapi": 0, "max_input_channels": 2},
            {"index": 21, "name": "Microphone (Realtek)", "hostapi": 3, "max_input_channels": 2},
        ]
        with (
            patch("core.client.audio.devices.is_remote_session", return_value=True),
            patch("core.client.audio.devices.sd.query_devices", side_effect=[devices[0], devices]),
        ):
            candidates = input_candidates(refresh_if_empty=False)
        self.assertEqual(candidates[0]["index"], 1)

    def test_monitor_reopens_when_login_session_changes(self):
        app = Mock(); app.state.stream = None
        manager = AudioStreamManager(app)
        manager._remote_session = True
        manager._monitor_stop = Mock()
        manager._monitor_stop.is_set.side_effect = [False, False, True]
        manager._monitor_wake = Mock()
        manager.reopen = Mock()
        with patch("core.client.audio.stream.is_remote_session", return_value=False):
            manager._monitor_devices()
        manager.reopen.assert_called_once()

    def test_start_falls_back_when_first_candidate_cannot_open(self):
        app = Mock(); app.state.stream = None; app.managed = True
        manager = AudioStreamManager(app)
        candidates = [
            {"index": 8, "name": "Unavailable Mic", "hostapi": 0, "max_input_channels": 2},
            {"index": 9, "name": "Working Mic", "hostapi": 0, "max_input_channels": 1},
        ]
        working_stream = Mock()
        with (
            patch("core.client.audio.stream.input_candidates", return_value=candidates),
            patch(
                "core.client.audio.stream.sd.InputStream",
                side_effect=[sd.PortAudioError("device unavailable"), working_stream],
            ) as input_stream,
        ):
            self.assertIs(manager.start(), working_stream)
        self.assertEqual(input_stream.call_count, 2)
        self.assertEqual(manager._device_identity, (9, "Working Mic", 0))

    def test_windows_endpoint_watcher_registers_and_forwards_events(self):
        events = []
        watcher = WindowsAudioEndpointWatcher(events.append)
        enumerator = Mock()
        with patch("pycaw.utils.AudioUtilities.GetDeviceEnumerator", return_value=enumerator):
            watcher.start()
        callback = enumerator.RegisterEndpointNotificationCallback.call_args.args[0]

        callback.on_device_added("mic-1")
        callback.on_device_state_changed("mic-1", "Active", DEVICE_STATE_ACTIVE)

        self.assertEqual([event.kind for event in events], ["added", "state_changed"])
        watcher.stop()
        enumerator.UnregisterEndpointNotificationCallback.assert_called_once_with(callback)


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


class HotwordCorrectionTests(unittest.TestCase):
    def test_manager_builds_explicit_correction_aliases(self):
        self.assertEqual(
            build_client_hotword_entry("子agent", "是 agent | 贼 agent | 是 agent"),
            "子agent | 是 agent | 贼 agent",
        )

    def test_explicit_aliases_force_observed_recognition_errors(self):
        corrector = PhonemeCorrector(threshold=0.85, similar_threshold=0.6)
        corrector.update_hotwords("子agent | 是 agent | 贼 agent | 是 A 君 | 是 A 君车 | 子 inject")
        cases = {
            "是 agent": "子agent",
            "是 A 君": "子agent",
            "是 A 君车": "子agent",
            "开一个贼 agent": "开一个子agent",
            "可以多开几个子 inject 去并行验证": "可以多开几个子agent 去并行验证",
        }
        for recognized, expected in cases.items():
            with self.subTest(recognized=recognized):
                self.assertEqual(corrector.correct(recognized).text, expected)

    def test_qwen_server_hotword_page_explains_unsupported_engine(self):
        help_text = server_hotword_help("qwen_asr", 10)
        self.assertFalse(server_hotword_supported("qwen_asr"))
        self.assertTrue(server_hotword_supported("fun_asr_nano"))
        self.assertIn("不读取服务端热词", help_text)
        self.assertIn("客户端定向纠错", help_text)


class LogHighlightTests(unittest.TestCase):
    def test_log_levels_are_colored_by_severity(self):
        self.assertEqual(log_line_tag("10:00 ERROR request failed"), "log_error")
        self.assertEqual(log_line_tag("10:00 WARNING retrying"), "log_warning")
        self.assertEqual(log_line_tag("10:00 INFO connected"), "log_info")
        self.assertEqual(log_line_tag("10:00 DEBUG details"), "log_debug")
        self.assertEqual(log_line_tag("===== 客户端日志 ====="), "log_section")

    def test_reconnect_excerpt_only_keeps_tagged_client_events(self):
        client_log = "\n".join(
            [
                "10:00 INFO 普通客户端日志",
                "10:01 INFO [麦克风重连] 检测到重新连接",
                "10:02 INFO [麦克风重连] 音频流重建完成",
            ]
        )
        excerpt = reconnect_log_excerpt(client_log)
        self.assertNotIn("普通客户端日志", excerpt)
        self.assertIn("检测到重新连接", excerpt)
        self.assertIn("音频流重建完成", excerpt)


class LogRefreshTests(unittest.TestCase):
    def build_manager(self):
        manager = Mock()
        manager._last_log_text = "旧日志"
        manager._read_log_tail.side_effect = [
            "客户端新日志\n10:01 INFO [麦克风重连] 音频流重建完成",
            "服务端新日志",
        ]
        manager.log_view.tag_ranges.return_value = ()
        manager.log_view.yview.return_value = (0.0, 1.0)
        manager.focus_get.return_value = None
        manager._log_selection_active.return_value = False
        return manager

    def test_active_selection_only_pauses_automatic_refresh_while_log_has_focus(self):
        manager = self.build_manager()
        manager.log_view.tag_ranges.return_value = ("1.0", "1.2")
        manager.focus_get.return_value = manager.log_view
        manager._log_selection_active.side_effect = lambda: CapsWriterManager._log_selection_active(manager)

        CapsWriterManager.refresh_logs(manager)

        manager.log_view.delete.assert_not_called()
        self.assertEqual(manager._last_log_text, "旧日志")

    def test_manual_refresh_overrides_active_selection(self):
        manager = self.build_manager()
        manager._log_selection_active.return_value = True

        CapsWriterManager.refresh_logs(manager, force=True)

        manager.log_view.delete.assert_called_once_with("1.0", "end")
        self.assertIn("客户端新日志", manager._last_log_text)
        self.assertIn("服务端新日志", manager._last_log_text)
        self.assertTrue(manager._last_log_text.rstrip().endswith("[麦克风重连] 音频流重建完成"))

    def test_stale_selection_does_not_pause_refresh_after_focus_moves_away(self):
        manager = self.build_manager()
        manager.log_view.tag_ranges.return_value = ("1.0", "1.2")
        manager.focus_get.return_value = Mock()
        manager._log_selection_active.side_effect = lambda: CapsWriterManager._log_selection_active(manager)

        CapsWriterManager.refresh_logs(manager)

        manager.log_view.delete.assert_called_once_with("1.0", "end")
        self.assertIn("客户端新日志", manager._last_log_text)

    def test_failed_render_keeps_previous_snapshot_for_next_retry(self):
        manager = self.build_manager()
        manager._log_selection_active.return_value = False
        manager.log_view.insert.side_effect = RuntimeError("tk redraw failed")

        with self.assertRaisesRegex(RuntimeError, "tk redraw failed"):
            CapsWriterManager.refresh_logs(manager)

        self.assertEqual(manager._last_log_text, "旧日志")
        self.assertEqual(
            manager.log_view.configure.call_args_list[-1],
            call(state="disabled"),
        )

    def test_scheduled_refresh_reschedules_after_one_failure(self):
        manager = Mock()
        manager._log_refresh_after_id = "old-job"
        manager.refresh_logs.side_effect = RuntimeError("temporary failure")

        CapsWriterManager._run_scheduled_log_refresh(manager)

        self.assertIsNone(manager._log_refresh_after_id)
        manager.status.configure.assert_called_once()
        manager._schedule_log_refresh.assert_called_once_with()

    def test_scheduler_keeps_only_one_pending_job(self):
        manager = Mock()
        manager._log_refresh_after_id = "existing-job"

        CapsWriterManager._schedule_log_refresh(manager, 750)

        manager.after.assert_not_called()

    def test_scheduler_records_new_pending_job(self):
        manager = Mock()
        manager._log_refresh_after_id = None
        manager.winfo_exists.return_value = True
        manager.after.return_value = "new-job"

        CapsWriterManager._schedule_log_refresh(manager, 750)

        manager.after.assert_called_once_with(750, manager._run_scheduled_log_refresh)
        self.assertEqual(manager._log_refresh_after_id, "new-job")


class MicrophoneStatusTests(unittest.TestCase):
    def test_top_status_includes_connected_device_name(self):
        self.assertEqual(
            microphone_status_text({"index": 1, "name": "Wireless Mic Rx"}),
            "麦克风：已连接 · #1 Wireless Mic Rx",
        )


class ManagedProcessTreeTests(unittest.TestCase):
    def test_stopping_managed_process_ends_its_windows_process_tree(self):
        process = Mock()
        process.pid = 43210
        process.poll.return_value = None
        with patch("start_manager.subprocess.run") as taskkill:
            stop_managed_process_tree(process)
        taskkill.assert_called_once_with(
            ["taskkill", "/PID", "43210", "/T", "/F"], capture_output=True, check=False,
        )
        process.wait.assert_called_once_with(timeout=4)
        process.kill.assert_not_called()

    def test_detects_running_local_server_before_starting_another(self):
        with patch("start_manager.socket.create_connection") as connect:
            self.assertTrue(local_server_is_available())
        connect.assert_called_once_with(("127.0.0.1", 6016), timeout=0.3)

    def test_treats_unreachable_local_server_as_absent(self):
        with patch("start_manager.socket.create_connection", side_effect=OSError):
            self.assertFalse(local_server_is_available())

    def test_force_restart_refuses_listener_outside_current_capswriter_source(self):
        listener = Mock(); listener.pid = 43210
        listener.laddr.port = 6016
        listener.status = "LISTEN"
        process = Mock(); process.cmdline.return_value = ["python", "other_server.py"]
        process.cwd.return_value = "C:\\other-app"
        with patch("psutil.net_connections", return_value=[listener]), patch("psutil.Process", return_value=process), patch("start_manager.subprocess.run") as taskkill:
            stopped, message = force_stop_local_capswriter_server()
        self.assertFalse(stopped)
        self.assertIn("已拒绝结束", message)
        taskkill.assert_not_called()


class WindowsStartupTests(unittest.TestCase):
    def test_startup_command_uses_absolute_manager_path(self):
        command = startup_command()
        self.assertIn("start_manager.py", command)
        self.assertIn("pythonw.exe", command)
        self.assertTrue(command.endswith(" --restart"))


if __name__ == "__main__":
    unittest.main()
