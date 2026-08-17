# coding: utf-8
"""CapsWriter 本地输入管理器：唯一 UI、唯一任务栏入口、唯一生命周期入口。"""

from __future__ import annotations

import argparse
import ctypes
import json
import os
import re
import queue
import signal
import subprocess
import sys
import time
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk
from typing import Any

from core.runtime_settings import ROOT_DIR, load_settings, save_settings


INSTANCE_FILE = ROOT_DIR / "logs" / "manager-instance.json"
HOT_FILE = ROOT_DIR / "hot.txt"
RULE_FILE = ROOT_DIR / "hot-rule.txt"
SERVER_HOT_FILE = ROOT_DIR / "hot-server.txt"
MUTEX_NAME = "Local\\CapsWriterOfflineManager"


class InstanceGuard:
    def __init__(self) -> None:
        self.handle = None

    def acquire(self) -> bool:
        self.handle = ctypes.windll.kernel32.CreateMutexW(None, False, MUTEX_NAME)
        return ctypes.windll.kernel32.GetLastError() != 183

    def release(self) -> None:
        if self.handle:
            ctypes.windll.kernel32.CloseHandle(self.handle)
            self.handle = None


def stop_previous_manager() -> None:
    """只依据本项目实例记录终止旧管理器及其子树，不扫描或误杀其他软件。"""
    try:
        pid = int(json.loads(INSTANCE_FILE.read_text(encoding="utf-8"))["pid"])
    except (OSError, ValueError, KeyError, json.JSONDecodeError):
        return
    if pid != os.getpid():
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, check=False)
        time.sleep(0.5)


def read_lines(path: Path) -> list[str]:
    try:
        return [line.strip() for line in path.read_text(encoding="utf-8").splitlines()
                if line.strip() and not line.lstrip().startswith("#")]
    except OSError:
        return []


def write_lines(path: Path, values: list[str], header: str) -> None:
    cleaned = list(dict.fromkeys(value.strip() for value in values if value.strip()))
    path.write_text(header + "\n" + "\n".join(cleaned) + ("\n" if cleaned else ""), encoding="utf-8")


class CapsWriterManager(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("CapsWriter 本地输入管理器")
        self.geometry("880x620")
        self.minsize(760, 520)
        self.children_processes: list[subprocess.Popen] = []
        self._server_process: subprocess.Popen | None = None
        self._client_process: subprocess.Popen | None = None
        self._engine_generation = 0
        self._tray_icon = None
        self._tray_commands: queue.Queue[str] = queue.Queue()
        self._last_log_text = ""
        self.protocol("WM_DELETE_WINDOW", self.hide_window)
        self._build_ui()
        self._start_tray()
        self.after(150, self._process_tray_commands)
        self.start_engine()
        self.after(750, self.refresh_logs)

    def _build_ui(self) -> None:
        root = ttk.Frame(self, padding=14)
        root.pack(fill="both", expand=True)
        top = ttk.Frame(root)
        top.pack(fill="x")
        ttk.Label(top, text="CapsWriter 本地输入管理器", font=("Microsoft YaHei UI", 16, "bold")).pack(side="left")
        self.status = ttk.Label(top, text="正在启动…")
        self.status.pack(side="left", padx=18)
        ttk.Button(top, text="重启输入引擎", command=self.restart_engine).pack(side="right")

        notebook = ttk.Notebook(root)
        notebook.pack(fill="both", expand=True, pady=(12, 0))
        self.shortcut_tab = ttk.Frame(notebook, padding=10)
        self.words_tab = ttk.Frame(notebook, padding=10)
        self.mapping_tab = ttk.Frame(notebook, padding=10)
        self.logs_tab = ttk.Frame(notebook, padding=10)
        notebook.add(self.shortcut_tab, text="快捷键")
        notebook.add(self.words_tab, text="热词")
        notebook.add(self.mapping_tab, text="转换词")
        notebook.add(self.logs_tab, text="运行日志")
        self._build_shortcuts()
        self._build_words()
        self._build_mappings()
        self._build_logs()

    def _build_shortcuts(self) -> None:
        columns = ("key", "type", "hold", "suppress", "enabled")
        self.shortcut_table = ttk.Treeview(self.shortcut_tab, columns=columns, show="headings", height=12)
        headings = {"key": "按键", "type": "类型", "hold": "触发方式", "suppress": "阻塞原按键", "enabled": "启用"}
        for col in columns:
            self.shortcut_table.heading(col, text=headings[col])
            self.shortcut_table.column(col, width=130, anchor="center")
        self.shortcut_table.pack(fill="both", expand=True)
        controls = ttk.Frame(self.shortcut_tab); controls.pack(fill="x", pady=8)
        self.key_entry = ttk.Entry(controls, width=20); self.key_entry.pack(side="left")
        self.type_var = tk.StringVar(value="keyboard")
        ttk.Combobox(controls, textvariable=self.type_var, values=("keyboard", "mouse"), state="readonly", width=12).pack(side="left", padx=6)
        ttk.Button(controls, text="添加", command=self.add_shortcut).pack(side="left")
        ttk.Button(controls, text="删除选中", command=self.delete_shortcut).pack(side="left", padx=6)
        ttk.Button(controls, text="保存并立即生效", command=self.save_shortcuts).pack(side="right")
        self.reload_shortcuts()

    def _build_words(self) -> None:
        pane = ttk.PanedWindow(self.words_tab, orient="horizontal"); pane.pack(fill="both", expand=True)
        left = ttk.Labelframe(pane, text="热词库（每行一个）", padding=8)
        right = ttk.Labelframe(pane, text="从文章找低频候选词", padding=8)
        pane.add(left, weight=1); pane.add(right, weight=1)
        self.hot_text = tk.Text(left, wrap="word"); self.hot_text.pack(fill="both", expand=True)
        ttk.Button(left, text="保存并热更新", command=self.save_hotwords).pack(anchor="e", pady=(8, 0))
        ttk.Label(right, text="粘贴文章：").pack(anchor="w")
        self.article_text = tk.Text(right, height=10, wrap="word"); self.article_text.pack(fill="both", expand=True)
        row = ttk.Frame(right); row.pack(fill="x", pady=6)
        ttk.Button(row, text="提取低频候选", command=self.extract_candidates).pack(side="left")
        ttk.Button(row, text="将选中候选加入热词", command=self.add_candidates).pack(side="right")
        self.candidates = tk.Listbox(right, selectmode="extended", height=10); self.candidates.pack(fill="both", expand=True)
        self.reload_hotwords()

    def _build_mappings(self) -> None:
        ttk.Label(self.mapping_tab, text="每行一条：原词 = 转换后文字。支持正则表达式；保存后客户端在约 0.2 秒内热更新。").pack(anchor="w")
        self.rule_text = tk.Text(self.mapping_tab, wrap="none"); self.rule_text.pack(fill="both", expand=True, pady=8)
        ttk.Button(self.mapping_tab, text="保存并热更新", command=self.save_mappings).pack(anchor="e")
        self.reload_mappings()

    def _build_logs(self) -> None:
        ttk.Label(self.logs_tab, text="实时读取 client_latest.log 与 server_latest.log；选中后可复制报错。 ").pack(anchor="w")
        self.log_view = tk.Text(self.logs_tab, wrap="none", state="disabled", font=("Consolas", 9))
        self.log_view.pack(fill="both", expand=True, pady=8)
        controls = ttk.Frame(self.logs_tab); controls.pack(fill="x")
        ttk.Button(controls, text="复制选中", command=self.copy_selected_log).pack(side="left")
        ttk.Button(controls, text="复制全部", command=self.copy_all_logs).pack(side="left", padx=6)
        ttk.Button(controls, text="立即刷新", command=self.refresh_logs).pack(side="right")

    def _start_tray(self) -> None:
        """管理器关闭窗口后仍驻留系统托盘，退出动作必须由菜单明确触发。"""
        try:
            import pystray
            from PIL import Image, ImageDraw
        except ImportError:
            self.status.configure(text="未安装托盘依赖；请使用“退出并停止输入”关闭。")
            return
        image = Image.new("RGBA", (64, 64), (37, 99, 160, 255))
        draw = ImageDraw.Draw(image)
        draw.ellipse((17, 12, 47, 42), fill=(255, 215, 66, 255))
        draw.rectangle((22, 39, 42, 52), fill=(255, 255, 255, 255))
        menu = pystray.Menu(
            pystray.MenuItem("打开 CapsWriter 管理器", lambda icon, item: self._tray_commands.put("show"), default=True),
            pystray.MenuItem("重启输入引擎", lambda icon, item: self._tray_commands.put("restart")),
            pystray.MenuItem("退出并停止输入", lambda icon, item: self._tray_commands.put("shutdown")),
        )
        self._tray_icon = pystray.Icon("capswriter_manager", image, "CapsWriter 本地输入管理器", menu)
        import threading
        threading.Thread(target=self._tray_icon.run, name="capswriter-manager-tray", daemon=True).start()

    def _process_tray_commands(self) -> None:
        try:
            while True:
                command = self._tray_commands.get_nowait()
                if command == "show":
                    self.show_window()
                elif command == "restart":
                    self.restart_engine()
                elif command == "shutdown":
                    self.shutdown()
                    return
                elif command == "engine_failed":
                    self.status.configure(text="识别引擎启动失败，请打开运行日志查看错误")
                elif command == "engine_timeout":
                    self.status.configure(text="识别模型启动超时，请打开运行日志查看错误")
                elif command == "engine_ready":
                    self.status.configure(text="本地输入引擎已就绪（麦克风每 5 秒自动重连检查）")
        except queue.Empty:
            pass
        if self.winfo_exists():
            self.after(150, self._process_tray_commands)

    def hide_window(self) -> None:
        self.withdraw()
        self.status.configure(text="管理器已隐藏，输入引擎仍在运行")

    def show_window(self) -> None:
        self.deiconify()
        self.state("normal")
        self.lift()
        self.focus_force()

    def _read_log_tail(self, path: Path, max_chars: int = 22000) -> str:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
            return text[-max_chars:]
        except OSError as exc:
            return f"无法读取 {path.name}: {exc}"

    def refresh_logs(self) -> None:
        content = "\n\n===== 客户端日志 =====\n" + self._read_log_tail(ROOT_DIR / "logs" / "client_latest.log")
        content += "\n\n===== 服务端日志 =====\n" + self._read_log_tail(ROOT_DIR / "logs" / "server_latest.log")
        if content != self._last_log_text and not self.log_view.tag_ranges("sel"):
            follow_tail = not self._last_log_text or self.log_view.yview()[1] >= 0.995
            self._last_log_text = content
            self.log_view.configure(state="normal")
            self.log_view.delete("1.0", "end")
            self.log_view.insert("1.0", content)
            if follow_tail:
                self.log_view.see("end")
            self.log_view.configure(state="disabled")
        if self.winfo_exists():
            self.after(1000, self.refresh_logs)

    def copy_selected_log(self) -> None:
        try:
            text = self.log_view.get("sel.first", "sel.last")
        except tk.TclError:
            messagebox.showinfo("复制日志", "请先在日志页选中要复制的内容。")
            return
        self.clipboard_clear(); self.clipboard_append(text)
        self.status.configure(text="已复制选中的日志")

    def copy_all_logs(self) -> None:
        self.clipboard_clear(); self.clipboard_append(self._last_log_text)
        self.status.configure(text="已复制当前日志")

    def reload_shortcuts(self) -> None:
        self.shortcut_table.delete(*self.shortcut_table.get_children())
        for shortcut in load_settings()["shortcuts"]:
            self.shortcut_table.insert("", "end", values=(shortcut["key"], shortcut.get("type", "keyboard"), "长按" if shortcut.get("hold_mode", True) else "单击", "是" if shortcut.get("suppress", False) else "否", "是" if shortcut.get("enabled", True) else "否"))

    def add_shortcut(self) -> None:
        key = self.key_entry.get().strip().lower()
        if not key:
            return
        kind = self.type_var.get()
        if kind == "mouse" and key not in {"x1", "x2"}:
            messagebox.showerror("快捷键无效", "鼠标快捷键当前支持 x1 或 x2。")
            return
        self.shortcut_table.insert("", "end", values=(key, kind, "长按", "是", "是"))
        self.key_entry.delete(0, "end")

    def delete_shortcut(self) -> None:
        for item in self.shortcut_table.selection():
            self.shortcut_table.delete(item)

    def save_shortcuts(self) -> None:
        shortcuts = []
        seen = set()
        for item in self.shortcut_table.get_children():
            key, kind, hold, suppress, enabled = self.shortcut_table.item(item, "values")
            if key in seen:
                messagebox.showerror("快捷键重复", f"{key} 已存在。")
                return
            seen.add(key)
            shortcuts.append({"key": key, "type": kind, "hold_mode": hold == "长按", "suppress": suppress == "是", "enabled": enabled == "是"})
        save_settings({"shortcuts": shortcuts})
        self.status.configure(text="快捷键已保存并热更新")

    def reload_hotwords(self) -> None:
        self.hot_text.delete("1.0", "end"); self.hot_text.insert("1.0", "\n".join(read_lines(HOT_FILE)))

    def save_hotwords(self) -> None:
        words = self.hot_text.get("1.0", "end").splitlines()
        write_lines(HOT_FILE, words, "# 由 CapsWriter 管理器维护的热词，每行一个")
        self.status.configure(text="热词已保存并热更新")

    def extract_candidates(self) -> None:
        text = self.article_text.get("1.0", "end")
        tokens = re.findall(r"[A-Za-z][A-Za-z0-9_+.-]{2,}|[\u4e00-\u9fff]{2,8}", text)
        counts: dict[str, int] = {}
        for token in tokens:
            counts[token] = counts.get(token, 0) + 1
        known = set(read_lines(HOT_FILE))
        choices = sorted((token for token, count in counts.items() if count == 1 and token not in known), key=lambda x: (-len(x), x))[:200]
        self.candidates.delete(0, "end")
        for token in choices:
            self.candidates.insert("end", token)

    def add_candidates(self) -> None:
        selected = [self.candidates.get(i) for i in self.candidates.curselection()]
        if selected:
            existing = self.hot_text.get("1.0", "end").strip()
            self.hot_text.delete("1.0", "end")
            self.hot_text.insert("1.0", "\n".join(filter(None, [existing, *selected])))
            self.save_hotwords()

    def reload_mappings(self) -> None:
        self.rule_text.delete("1.0", "end"); self.rule_text.insert("1.0", "\n".join(read_lines(RULE_FILE)))

    def save_mappings(self) -> None:
        rules = self.rule_text.get("1.0", "end").splitlines()
        write_lines(RULE_FILE, rules, "# 由 CapsWriter 管理器维护：原词 = 转换后文字")
        self.status.configure(text="转换词已保存并热更新")

    def start_engine(self) -> None:
        self._engine_generation += 1
        generation = self._engine_generation
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        self._server_process = subprocess.Popen([sys.executable, "start_server.py", "--managed"], cwd=ROOT_DIR, creationflags=flags)
        self.children_processes.append(self._server_process)
        self.status.configure(text="正在加载本地识别模型，输入引擎将在就绪后自动连接…")
        import threading
        threading.Thread(target=self._start_client_when_server_ready, args=(generation, self._server_process), name="capswriter-engine-start", daemon=True).start()

    def _start_client_when_server_ready(self, generation: int, server_process: subprocess.Popen) -> None:
        """等待服务端端口就绪再启动录音客户端，避免首次连接失败导致按键无输出。"""
        import socket
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            if generation != self._engine_generation:
                return
            if server_process.poll() is not None:
                self._tray_commands.put("engine_failed")
                return
            try:
                with socket.create_connection(("127.0.0.1", 6016), timeout=0.5):
                    break
            except OSError:
                time.sleep(0.5)
        else:
            self._tray_commands.put("engine_timeout")
            return

        if generation != self._engine_generation:
            return

        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        self._client_process = subprocess.Popen([sys.executable, "start_client.py", "--managed"], cwd=ROOT_DIR, creationflags=flags)
        self.children_processes.append(self._client_process)
        self._tray_commands.put("engine_ready")

    def restart_engine(self) -> None:
        self.stop_engine(); self.start_engine()

    def stop_engine(self) -> None:
        self._engine_generation += 1
        for process in self.children_processes:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=4)
                except subprocess.TimeoutExpired:
                    process.kill()
        self.children_processes.clear()

    def shutdown(self) -> None:
        self.stop_engine()
        if self._tray_icon:
            self._tray_icon.stop()
            self._tray_icon = None
        try: INSTANCE_FILE.unlink(missing_ok=True)
        except OSError: pass
        self.destroy()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--restart", action="store_true")
    args = parser.parse_args()
    if args.restart:
        stop_previous_manager()
    guard = InstanceGuard()
    if not guard.acquire():
        messagebox.showinfo("CapsWriter", "管理器已在运行；为避免重复录音监听，本次启动已取消。")
        return
    INSTANCE_FILE.parent.mkdir(exist_ok=True)
    INSTANCE_FILE.write_text(json.dumps({"pid": os.getpid()}), encoding="utf-8")
    try:
        CapsWriterManager().mainloop()
    finally:
        guard.release()


if __name__ == "__main__":
    main()
