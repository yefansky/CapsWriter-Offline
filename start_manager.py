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
import sounddevice as sd
from pathlib import Path
from tkinter import messagebox, simpledialog, ttk
from typing import Any

from config_server import ServerConfig
from core.client.audio.devices import preferred_input_device
from core.runtime_settings import ROOT_DIR, load_settings, save_settings
from core.startup import is_startup_enabled, set_startup_enabled


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


def build_client_hotword_entry(target: str, aliases: str = "") -> str:
    """把最终输出和模型错识别写法合成 hot.txt 的一条定向纠错规则。"""
    parts = [target.strip(), *(part.strip() for part in aliases.split("|"))]
    unique_parts = list(dict.fromkeys(part for part in parts if part))
    return " | ".join(unique_parts)


def server_hotword_supported(model_type: str) -> bool:
    """返回当前服务端识别引擎是否会读取 hot-server.txt。"""
    return model_type.strip().lower() not in {"qwen_asr", "paraformer"}


def server_hotword_help(model_type: str, count: int) -> str:
    """说明当前识别引擎是否真的会读取服务端热词。"""
    if not server_hotword_supported(model_type):
        return (
            f"服务端识别热词库（hot-server.txt，{count} 条）。"
            f"当前引擎 {model_type} 不读取服务端热词；这里的改动不会影响识别结果。"
            "请使用客户端定向纠错。"
        )
    return (
        f"服务端识别热词库（hot-server.txt，{count} 条）。"
        "保存后会自动重启识别引擎，使模型重新载入热词。"
    )


def read_rule_rows(path: Path) -> tuple[list[str], list[tuple[str, str]]]:
    """读取转换规则，保留注释/无法在表格中表达的行，规则拆成原词和替换词。"""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return [], []
    preserved: list[str] = []
    rows: list[tuple[str, str]] = []
    for line in lines:
        if " = " not in line or line.lstrip().startswith("#"):
            preserved.append(line)
            continue
        source, replacement = line.split(" = ", maxsplit=1)
        source, replacement = source.strip(), replacement.strip()
        if source:
            rows.append((source, replacement))
        else:
            preserved.append(line)
    return preserved, rows


def write_rule_rows(path: Path, preserved: list[str], rows: list[tuple[str, str]]) -> None:
    """写回二维规则表，同时保留原有说明注释。"""
    unique_rows = list(dict.fromkeys((source.strip(), replacement.strip()) for source, replacement in rows if source.strip()))
    content = [*preserved, *(f"{source} = {replacement}" for source, replacement in unique_rows)]
    path.write_text(("\n".join(content) + "\n") if content else "", encoding="utf-8")


def log_line_tag(line: str) -> str | None:
    """为日志行返回显示标签；按最高严重级别着色。"""
    upper = line.upper()
    if "CRITICAL" in upper or "ERROR" in upper or "TRACEBACK" in upper:
        return "log_error"
    if "WARNING" in upper or " WARN" in upper:
        return "log_warning"
    if "DEBUG" in upper:
        return "log_debug"
    if "INFO" in upper:
        return "log_info"
    if line.lstrip().startswith("====="):
        return "log_section"
    return None


def microphone_status_text(device: dict[str, Any]) -> str:
    """生成顶部状态栏的默认输入设备说明。"""
    return f"麦克风：已连接 · #{device['index']} {device.get('name', '未知设备')}"


class CapsWriterManager(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("CapsWriter 本地输入管理器")
        self.geometry("1060x700")
        self.minsize(860, 560)
        self._configure_vscode_theme()
        self.children_processes: list[subprocess.Popen] = []
        self._server_process: subprocess.Popen | None = None
        self._client_process: subprocess.Popen | None = None
        self._engine_generation = 0
        self._tray_icon = None
        self._tray_commands: queue.Queue[str] = queue.Queue()
        self._last_log_text = ""
        self._log_refresh_after_id: str | None = None
        self.protocol("WM_DELETE_WINDOW", self.hide_window)
        self._build_ui()
        self._start_tray()
        self.after(150, self._process_tray_commands)
        self.after(200, self.refresh_microphone_status)
        self.start_engine()
        self._schedule_log_refresh(750)

    def _configure_vscode_theme(self) -> None:
        """使用不依赖第三方组件的 VS Code 深色编辑器视觉规范。"""
        colors = {
            "background": "#1E1E1E", "panel": "#252526", "surface": "#2D2D30",
            "hover": "#37373D", "border": "#3E3E42", "text": "#D4D4D4",
            "muted": "#A7A7A7", "blue": "#007ACC", "blue_hover": "#1488D4",
            "green": "#89D185", "red": "#F48771", "selection": "#264F78",
        }
        self.colors = colors
        self.configure(background=colors["background"])
        # ttk 样式中的字体族名含空格会被 Tcl 拆成多个参数；使用单词字体族确保 Windows/Tk 兼容。
        self.option_add("*Font", "Arial 10")
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure(".", background=colors["background"], foreground=colors["text"])
        style.configure("TFrame", background=colors["background"])
        style.configure("Header.TFrame", background=colors["panel"])
        style.configure("TLabel", background=colors["background"], foreground=colors["text"])
        style.configure("Title.TLabel", background=colors["panel"], foreground="#FFFFFF", font=("Arial", 17, "bold"))
        style.configure("Subtitle.TLabel", background=colors["panel"], foreground=colors["muted"], font=("Arial", 9))
        style.configure("MicOnline.TLabel", background="#173A2B", foreground=colors["green"], padding=(10, 5))
        style.configure("MicOffline.TLabel", background="#48252A", foreground=colors["red"], padding=(10, 5))
        style.configure("Engine.TLabel", background=colors["surface"], foreground=colors["text"], padding=(10, 5))
        style.configure("TButton", background=colors["surface"], foreground=colors["text"], borderwidth=0, padding=(10, 6))
        style.map("TButton", background=[("active", colors["hover"]), ("pressed", "#45454A")])
        style.configure("Accent.TButton", background=colors["blue"], foreground="#FFFFFF", padding=(12, 7))
        style.map("Accent.TButton", background=[("active", colors["blue_hover"]), ("pressed", "#0067AC")])
        style.configure("TEntry", fieldbackground=colors["surface"], foreground=colors["text"], bordercolor=colors["border"], insertcolor="#FFFFFF", padding=6)
        style.configure("TCombobox", fieldbackground=colors["surface"], background=colors["surface"], foreground=colors["text"], arrowcolor=colors["text"], padding=5)
        style.map("TCombobox", fieldbackground=[("readonly", colors["surface"])], foreground=[("readonly", colors["text"])])
        style.configure("Treeview", background=colors["surface"], fieldbackground=colors["surface"], foreground=colors["text"], borderwidth=0, rowheight=29)
        style.map("Treeview", background=[("selected", colors["selection"])], foreground=[("selected", "#FFFFFF")])
        style.configure("Treeview.Heading", background="#333337", foreground="#E7E7E7", relief="flat", font=("Arial", 9, "bold"), padding=(8, 7))
        style.map("Treeview.Heading", background=[("active", colors["hover"])])
        style.layout("Flat.Treeview", [("Treeview.treearea", {"sticky": "nswe"})])
        style.configure("Flat.Treeview", background=colors["surface"], fieldbackground=colors["surface"], foreground=colors["text"], borderwidth=0, rowheight=29)
        style.map("Flat.Treeview", background=[("selected", colors["selection"])], foreground=[("selected", "#FFFFFF")])
        style.configure("Flat.Treeview.Heading", background="#333337", foreground="#E7E7E7", relief="flat", font=("Arial", 9, "bold"), padding=(8, 7))
        style.configure("TNotebook", background=colors["background"], borderwidth=0, tabmargins=(0, 0, 0, 0))
        style.configure("TNotebook.Tab", background=colors["surface"], foreground=colors["muted"], padding=(13, 7), borderwidth=0, relief="flat")
        style.map("TNotebook.Tab", background=[("selected", colors["background"]), ("active", colors["hover"])], foreground=[("selected", "#FFFFFF"), ("active", "#FFFFFF")])
        style.configure("TLabelframe", background=colors["background"], bordercolor=colors["border"], relief="flat", borderwidth=0)
        style.configure("TLabelframe.Label", background=colors["background"], foreground="#DCDCDC", font=("Arial", 10, "bold"))
        style.configure("TScrollbar", background=colors["surface"], troughcolor=colors["background"], bordercolor=colors["background"], arrowcolor=colors["muted"])

    def _build_ui(self) -> None:
        root = ttk.Frame(self, padding=0)
        root.pack(fill="both", expand=True)
        top = ttk.Frame(root, style="Header.TFrame", padding=(20, 14))
        top.pack(fill="x")
        brand = ttk.Frame(top, style="Header.TFrame"); brand.pack(side="left")
        ttk.Label(brand, text="CapsWriter", style="Title.TLabel").pack(anchor="w")
        ttk.Label(brand, text="LOCAL INPUT MANAGER", style="Subtitle.TLabel").pack(anchor="w")
        ttk.Button(top, text="重启输入引擎", command=self.restart_engine, style="Accent.TButton").pack(side="right")
        self.mic_status = ttk.Label(top, text="麦克风：检测中…", style="MicOnline.TLabel")
        self.mic_status.pack(side="right", padx=(0, 10))
        self.status = ttk.Label(top, text="正在启动…", style="Engine.TLabel")
        self.status.pack(side="right", padx=(0, 10))

        body = tk.Frame(root, background=self.colors["background"])
        body.pack(fill="both", expand=True)
        sidebar = tk.Frame(body, background="#181818", width=166)
        sidebar.pack(side="left", fill="y")
        sidebar.pack_propagate(False)
        tk.Label(sidebar, text="管理", background="#181818", foreground="#858585", font=("Arial", 9, "bold"), anchor="w").pack(fill="x", padx=18, pady=(22, 8))
        self._page_host = ttk.Frame(body, padding=16)
        self._page_host.pack(side="left", fill="both", expand=True)
        self._pages: dict[str, ttk.Frame] = {}
        self._nav_buttons: dict[str, tk.Button] = {}
        for key, label in (("logs", "运行日志"), ("shortcuts", "快捷键"), ("words", "热词库"), ("mappings", "转换词"), ("system", "系统设置")):
            button = tk.Button(
                sidebar, text=label, command=lambda page=key: self._show_page(page), anchor="w",
                background="#181818", foreground="#C8C8C8", activebackground="#2A2D2E", activeforeground="#FFFFFF",
                relief="flat", borderwidth=0, highlightthickness=0, padx=18, pady=10, font=("Arial", 10), cursor="hand2",
            )
            button.pack(fill="x", padx=8, pady=2)
            self._nav_buttons[key] = button
        tk.Frame(sidebar, background="#2A2D2E", height=1).pack(fill="x", padx=16, pady=14)
        tk.Label(sidebar, text="本地模型 · 离线运行", background="#181818", foreground="#777777", font=("Arial", 9), anchor="w").pack(fill="x", padx=18)
        self.shortcut_tab = ttk.Frame(self._page_host, padding=4)
        self.words_tab = ttk.Frame(self._page_host, padding=4)
        self.mapping_tab = ttk.Frame(self._page_host, padding=4)
        self.logs_tab = ttk.Frame(self._page_host, padding=4)
        self.system_tab = ttk.Frame(self._page_host, padding=4)
        self._pages = {"logs": self.logs_tab, "shortcuts": self.shortcut_tab, "words": self.words_tab, "mappings": self.mapping_tab, "system": self.system_tab}
        self._build_shortcuts()
        self._build_words()
        self._build_mappings()
        self._build_logs()
        self._build_system_settings()
        self._show_page("logs")

    def _show_page(self, page: str) -> None:
        """扁平侧栏导航，替换传统凸起标签页。"""
        for name, frame in self._pages.items():
            frame.pack_forget()
            button = self._nav_buttons[name]
            selected = name == page
            button.configure(
                background="#094771" if selected else "#181818",
                foreground="#FFFFFF" if selected else "#C8C8C8",
                activebackground="#0E639C" if selected else "#2A2D2E",
            )
        self._pages[page].pack(fill="both", expand=True)

    def _build_shortcuts(self) -> None:
        columns = ("key", "type", "hold", "suppress", "enabled")
        self.shortcut_table = ttk.Treeview(self.shortcut_tab, columns=columns, show="headings", height=12, style="Flat.Treeview")
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
        self._word_values = {"client": read_lines(HOT_FILE), "server": read_lines(SERVER_HOT_FILE)}
        self._word_search: dict[str, tk.StringVar] = {}
        self._word_tables: dict[str, ttk.Treeview] = {}
        self._word_entries: dict[str, ttk.Entry] = {}
        self._word_alias_entries: dict[str, ttk.Entry] = {}
        word_switcher = tk.Frame(self.words_tab, background=self.colors["background"])
        word_switcher.pack(fill="x", pady=(0, 12))
        client_words_tab = ttk.Frame(self.words_tab, padding=0)
        server_words_tab = ttk.Frame(self.words_tab, padding=0)
        self._word_pages = {"client": client_words_tab, "server": server_words_tab}
        self._word_nav_buttons: dict[str, tk.Button] = {}
        for key, text in (("client", f"客户端纠错热词  {len(read_lines(HOT_FILE))}"), ("server", f"服务端识别热词  {len(read_lines(SERVER_HOT_FILE))}")):
            button = tk.Button(
                word_switcher, text=text, command=lambda page=key: self._show_word_page(page),
                background="#2D2D30", foreground="#C8C8C8", activebackground="#3E3E42", activeforeground="#FFFFFF",
                relief="flat", borderwidth=0, highlightthickness=0, padx=14, pady=8, font=("Arial", 10), cursor="hand2",
            )
            button.pack(side="left", padx=(0, 6))
            self._word_nav_buttons[key] = button

        pane = ttk.PanedWindow(client_words_tab, orient="horizontal"); pane.pack(fill="both", expand=True)
        left = ttk.Labelframe(pane, text="客户端纠错热词（hot.txt）", padding=8)
        right = ttk.Labelframe(pane, text="从文章找低频候选词", padding=8)
        pane.add(left, weight=1); pane.add(right, weight=1)
        ttk.Label(
            left,
            text=(
                "主热词是最终输出；错识别别名填写模型实际听成的文字。"
                "例如：主热词“子agent”，错识别别名“是 agent | 贼 agent”。"
                "别名会精确强制纠错；只填主热词仍受音素相似度门槛限制。"
            ),
            wraplength=430,
        ).pack(anchor="w", pady=(0, 8))
        self._build_word_table(left, "client", self.save_hotwords)
        ttk.Label(right, text="粘贴文章：").pack(anchor="w")
        self.article_text = tk.Text(
            right, height=10, wrap="word", background=self.colors["surface"], foreground=self.colors["text"],
            insertbackground="#FFFFFF", selectbackground=self.colors["selection"], relief="flat", borderwidth=0, highlightthickness=0,
        ); self.article_text.pack(fill="both", expand=True, pady=(4, 0))
        row = ttk.Frame(right); row.pack(fill="x", pady=6)
        ttk.Button(row, text="提取低频候选", command=self.extract_candidates).pack(side="left")
        ttk.Button(row, text="将选中候选加入热词", command=self.add_candidates).pack(side="right")
        self.candidates = tk.Listbox(
            right, selectmode="extended", height=10, background=self.colors["surface"], foreground=self.colors["text"],
            selectbackground=self.colors["selection"], selectforeground="#FFFFFF", relief="flat", borderwidth=0, highlightthickness=0,
        ); self.candidates.pack(fill="both", expand=True)
        ttk.Label(
            server_words_tab,
            text=server_hotword_help(ServerConfig.model_type, len(self._word_values["server"])),
            wraplength=780,
        ).pack(anchor="w")
        self._build_word_table(server_words_tab, "server", self.save_server_hotwords)
        self._show_word_page("client")

    def _show_word_page(self, page: str) -> None:
        """使用扁平分段按钮代替内嵌 Notebook 标签。"""
        for name, frame in self._word_pages.items():
            frame.pack_forget()
            selected = name == page
            self._word_nav_buttons[name].configure(
                background="#0E639C" if selected else "#2D2D30",
                foreground="#FFFFFF" if selected else "#C8C8C8",
                activebackground="#1177BB" if selected else "#3E3E42",
            )
        self._word_pages[page].pack(fill="both", expand=True)

    def _build_word_table(self, parent: ttk.Widget, kind: str, save_command) -> None:
        search_row = ttk.Frame(parent); search_row.pack(fill="x", pady=(0, 6))
        ttk.Label(search_row, text="搜索：").pack(side="left")
        search = tk.StringVar()
        search.trace_add("write", lambda *_: self._refresh_word_table(kind))
        ttk.Entry(search_row, textvariable=search).pack(side="left", fill="x", expand=True)
        ttk.Button(search_row, text="清除", command=lambda: search.set("")).pack(side="left", padx=(6, 0))
        self._word_search[kind] = search

        columns = ("number", "part0") if kind == "client" else ("number", "word")
        table = ttk.Treeview(parent, columns=columns, show="headings", selectmode="extended", style="Flat.Treeview")
        table.heading("number", text="#")
        table.column("number", width=45, anchor="center", stretch=False)
        if kind == "client":
            self._configure_client_word_columns(table)
        else:
            table.heading("word", text="热词")
            table.column("word", width=480)
        table.pack(fill="both", expand=True)
        table.bind("<Double-1>", lambda event: self._edit_word(kind, event))
        self._word_tables[kind] = table

        if kind == "client":
            inputs = ttk.Frame(parent); inputs.pack(fill="x", pady=(7, 0))
            ttk.Label(inputs, text="最终输出：").grid(row=0, column=0, sticky="w", pady=2)
            entry = ttk.Entry(inputs)
            entry.grid(row=0, column=1, sticky="ew", pady=2)
            ttk.Label(inputs, text="错识别别名：").grid(row=1, column=0, sticky="w", pady=2)
            alias_entry = ttk.Entry(inputs)
            alias_entry.grid(row=1, column=1, sticky="ew", pady=2)
            ttk.Label(inputs, text="多个用 | 分隔").grid(row=1, column=2, sticky="w", padx=(6, 0), pady=2)
            ttk.Button(inputs, text="添加", command=lambda: self._add_word(kind)).grid(row=0, column=2, sticky="e", padx=(6, 0), pady=2)
            ttk.Button(inputs, text="删除选中", command=lambda: self._delete_words(kind)).grid(row=0, column=3, padx=(6, 0), pady=2)
            ttk.Button(inputs, text="保存并热更新", command=save_command).grid(row=1, column=3, padx=(6, 0), pady=2)
            inputs.columnconfigure(1, weight=1)
            self._word_alias_entries[kind] = alias_entry
        else:
            inputs = ttk.Frame(parent); inputs.pack(fill="x", pady=(7, 0))
            entry = ttk.Entry(inputs)
            entry.pack(fill="x", expand=True)
        self._word_entries[kind] = entry

        if kind != "client":
            controls = ttk.Frame(parent); controls.pack(fill="x", pady=(7, 0))
            ttk.Button(controls, text="添加", command=lambda: self._add_word(kind)).pack(side="left")
            ttk.Button(controls, text="删除选中", command=lambda: self._delete_words(kind)).pack(side="left", padx=6)
            save_label = "保存并重启识别引擎" if server_hotword_supported(ServerConfig.model_type) else "保存（当前引擎不使用）"
            ttk.Button(controls, text=save_label, command=save_command).pack(side="right")
        self._refresh_word_table(kind)

    def _refresh_word_table(self, kind: str) -> None:
        table = self._word_tables[kind]
        if kind == "client":
            self._configure_client_word_columns(table)
        table.delete(*table.get_children())
        keyword = self._word_search[kind].get().strip().casefold()
        for number, word in enumerate(self._word_values[kind], start=1):
            if not keyword or keyword in word.casefold():
                if kind == "client":
                    parts = [part.strip() for part in word.split("|")]
                    table.insert("", "end", values=(number, *parts))
                else:
                    table.insert("", "end", values=(number, word))

    def _configure_client_word_columns(self, table: ttk.Treeview) -> None:
        """每个 | 分段一个单元格；列数按当前词库最长的纠错组自动扩展。"""
        part_count = max(1, *(len(word.split("|")) for word in self._word_values.get("client", [])))
        columns = ("number", *(f"part{index}" for index in range(part_count)))
        if tuple(table["columns"]) == columns:
            return
        table.configure(columns=columns)
        table.heading("number", text="#")
        table.column("number", width=45, anchor="center", stretch=False)
        for index in range(part_count):
            column = f"part{index}"
            table.heading(column, text="主热词" if index == 0 else f"别名 {index}")
            table.column(column, width=145 if index else 160, minwidth=90)

    def _add_word(self, kind: str) -> None:
        word = self._word_entries[kind].get().strip()
        if kind == "client":
            aliases = self._word_alias_entries[kind].get().strip()
            word = build_client_hotword_entry(word, aliases)
        if word and word not in self._word_values[kind]:
            self._word_values[kind].append(word)
            self._word_entries[kind].delete(0, "end")
            if kind == "client":
                self._word_alias_entries[kind].delete(0, "end")
            self._refresh_word_table(kind)

    def _delete_words(self, kind: str) -> None:
        positions = {int(self._word_tables[kind].item(item, "values")[0]) - 1 for item in self._word_tables[kind].selection()}
        if positions:
            self._word_values[kind] = [word for index, word in enumerate(self._word_values[kind]) if index not in positions]
            self._refresh_word_table(kind)

    def _edit_word(self, kind: str, event) -> None:
        """双击词条即可编辑；搜索筛选状态不会影响实际词库。"""
        table = self._word_tables[kind]
        item = table.identify_row(event.y)
        if not item:
            return
        if kind == "client":
            self._edit_client_word_cell(item, table.identify_column(event.x))
            return
        values = table.item(item, "values")
        if len(values) < 2:
            return
        original = values[1]
        replacement = simpledialog.askstring("编辑热词", "热词：", initialvalue=original, parent=self)
        if replacement is None:
            return
        replacement = replacement.strip()
        if not replacement:
            messagebox.showwarning("热词不能为空", "请填写热词，或使用“删除选中”。", parent=self)
            return
        if replacement != original and replacement in self._word_values[kind]:
            messagebox.showwarning("热词重复", f"“{replacement}”已在词库中。", parent=self)
            return
        position = self._word_values[kind].index(original)
        self._word_values[kind][position] = replacement
        self._refresh_word_table(kind)
        self.status.configure(text="热词已修改；点击保存后生效")

    def _edit_client_word_cell(self, item: str, column: str) -> None:
        """编辑以 | 分隔的某一段；空白别名单元格也可双击新增。"""
        if not column.startswith("#") or column == "#1":
            return
        table = self._word_tables["client"]
        values = table.item(item, "values")
        position = int(values[0]) - 1
        original = self._word_values["client"][position]
        parts = [part.strip() for part in original.split("|")]
        part_index = int(column[1:]) - 2
        initial = parts[part_index] if part_index < len(parts) else ""
        replacement = simpledialog.askstring(
            "编辑纠错热词",
            "主热词：" if part_index == 0 else f"别名 {part_index}（留空可删除）：",
            initialvalue=initial,
            parent=self,
        )
        if replacement is None:
            return
        replacement = replacement.strip()
        if part_index == 0 and not replacement:
            messagebox.showwarning("主热词不能为空", "请填写主热词；如需删除整条规则请使用“删除选中”。", parent=self)
            return
        if part_index < len(parts):
            if replacement:
                parts[part_index] = replacement
            elif part_index:
                parts.pop(part_index)
        elif replacement:
            parts.append(replacement)
        updated = " | ".join(part for part in parts if part)
        if updated != original and updated in self._word_values["client"]:
            messagebox.showwarning("热词重复", f"“{updated}”已在词库中。", parent=self)
            return
        self._word_values["client"][position] = updated
        self._configure_client_word_columns(table)
        self._refresh_word_table("client")
        self.status.configure(text="纠错热词已修改；点击保存后热更新")

    def _build_mappings(self) -> None:
        ttk.Label(self.mapping_tab, text="左列为原词/正则，右列为替换文字。双击单元格可编辑；保存后客户端约 0.2 秒内热更新。").pack(anchor="w")
        self._rule_preserved, self._rule_values = read_rule_rows(RULE_FILE)
        self.rule_search = tk.StringVar()
        self.rule_search.trace_add("write", lambda *_: self._refresh_rule_table())
        search_row = ttk.Frame(self.mapping_tab); search_row.pack(fill="x", pady=(8, 6))
        ttk.Label(search_row, text="搜索：").pack(side="left")
        ttk.Entry(search_row, textvariable=self.rule_search).pack(side="left", fill="x", expand=True)
        ttk.Button(search_row, text="清除", command=lambda: self.rule_search.set("")).pack(side="left", padx=(6, 0))

        self.rule_table = ttk.Treeview(self.mapping_tab, columns=("source", "replacement"), show="headings", selectmode="extended", style="Flat.Treeview")
        self.rule_table.heading("source", text="原词 / 正则")
        self.rule_table.heading("replacement", text="替换为")
        self.rule_table.column("source", width=410)
        self.rule_table.column("replacement", width=330)
        self.rule_table.pack(fill="both", expand=True)
        self.rule_table.bind("<Double-1>", self._edit_rule_cell)

        controls = ttk.Frame(self.mapping_tab); controls.pack(fill="x", pady=(7, 0))
        self.rule_source_entry = ttk.Entry(controls, width=30); self.rule_source_entry.pack(side="left", fill="x", expand=True)
        self.rule_replacement_entry = ttk.Entry(controls, width=25); self.rule_replacement_entry.pack(side="left", fill="x", expand=True, padx=6)
        ttk.Button(controls, text="添加", command=self._add_rule).pack(side="left")
        ttk.Button(controls, text="删除选中", command=self._delete_rules).pack(side="left", padx=6)
        ttk.Button(controls, text="保存并热更新", command=self.save_mappings).pack(side="right")
        self._refresh_rule_table()

    def _build_system_settings(self) -> None:
        ttk.Label(self.system_tab, text="系统设置", style="Title.TLabel").pack(anchor="w", pady=(6, 4))
        ttk.Label(self.system_tab, text="管理器仅在当前 Windows 用户登录后自动运行，不需要管理员权限。", style="Subtitle.TLabel").pack(anchor="w", pady=(0, 18))
        card = tk.Frame(self.system_tab, background="#2D2D30", highlightthickness=1, highlightbackground="#3E3E42", padx=18, pady=16)
        card.pack(fill="x")
        tk.Label(card, text="随系统启动", background="#2D2D30", foreground="#FFFFFF", font=("Arial", 12, "bold"), anchor="w").pack(anchor="w")
        self.startup_detail = tk.Label(card, background="#2D2D30", foreground="#A7A7A7", font=("Arial", 10), anchor="w")
        self.startup_detail.pack(anchor="w", pady=(5, 12))
        self.startup_button = ttk.Button(card, command=self.toggle_startup, style="Accent.TButton")
        self.startup_button.pack(anchor="w")
        self._refresh_startup_setting()

    def _refresh_startup_setting(self) -> None:
        enabled = is_startup_enabled()
        self.startup_detail.configure(text="已启用：登录后自动启动本地输入管理器。" if enabled else "未启用：需要手动运行 install.bat 或 run.bat。")
        self.startup_button.configure(text="关闭随系统启动" if enabled else "开启随系统启动")

    def toggle_startup(self) -> None:
        enabled = not is_startup_enabled()
        try:
            set_startup_enabled(enabled)
        except OSError as exc:
            messagebox.showerror("启动项设置失败", f"无法修改当前用户的 Windows 启动项：\n{exc}", parent=self)
            return
        self._refresh_startup_setting()
        self.status.configure(text="已开启随系统启动" if enabled else "已关闭随系统启动")

    def _refresh_rule_table(self) -> None:
        self.rule_table.delete(*self.rule_table.get_children())
        keyword = self.rule_search.get().strip().casefold()
        for source, replacement in self._rule_values:
            if not keyword or keyword in source.casefold() or keyword in replacement.casefold():
                self.rule_table.insert("", "end", values=(source, replacement))

    def _add_rule(self) -> None:
        source = self.rule_source_entry.get().strip()
        replacement = self.rule_replacement_entry.get().strip()
        if not source:
            messagebox.showwarning("原词不能为空", "请填写原词或正则表达式。", parent=self)
            return
        if any(existing == source for existing, _ in self._rule_values):
            messagebox.showwarning("原词重复", f"“{source}”已有转换规则。", parent=self)
            return
        self._rule_values.append((source, replacement))
        self.rule_source_entry.delete(0, "end"); self.rule_replacement_entry.delete(0, "end")
        self._refresh_rule_table()

    def _delete_rules(self) -> None:
        selected = {tuple(self.rule_table.item(item, "values")) for item in self.rule_table.selection()}
        if selected:
            self._rule_values = [rule for rule in self._rule_values if rule not in selected]
            self._refresh_rule_table()

    def _edit_rule_cell(self, event) -> None:
        item = self.rule_table.identify_row(event.y)
        column = self.rule_table.identify_column(event.x)
        if not item or column not in ("#1", "#2"):
            return
        source, replacement = self.rule_table.item(item, "values")
        is_source = column == "#1"
        value = simpledialog.askstring("编辑转换词", "原词 / 正则：" if is_source else "替换为：", initialvalue=source if is_source else replacement, parent=self)
        if value is None:
            return
        value = value.strip()
        if is_source and not value:
            messagebox.showwarning("原词不能为空", "原词或正则表达式不能为空。", parent=self)
            return
        updated = (value, replacement) if is_source else (source, value)
        if is_source and value != source and any(existing == value for existing, _ in self._rule_values):
            messagebox.showwarning("原词重复", f"“{value}”已有转换规则。", parent=self)
            return
        position = self._rule_values.index((source, replacement))
        self._rule_values[position] = updated
        self._refresh_rule_table()
        self.status.configure(text="转换词已修改；点击保存后热更新")

    def _build_logs(self) -> None:
        ttk.Label(self.logs_tab, text="实时读取 client_latest.log 与 server_latest.log；选中后可复制报错。 ").pack(anchor="w")
        log_container = ttk.Frame(self.logs_tab)
        log_container.pack(fill="both", expand=True, pady=8)
        self.log_view = tk.Text(
            log_container, wrap="none", state="disabled", font=("Consolas", 9), background="#181818",
            foreground="#D4D4D4", insertbackground="#FFFFFF", selectbackground=self.colors["selection"], relief="flat", borderwidth=0, highlightthickness=0,
        )
        self.log_view.tag_configure("log_error", foreground="#F48771")
        self.log_view.tag_configure("log_warning", foreground="#CCA700")
        self.log_view.tag_configure("log_info", foreground="#4FC1FF")
        self.log_view.tag_configure("log_debug", foreground="#808080")
        self.log_view.tag_configure("log_section", foreground="#C586C0", font=("Consolas", 9, "bold"))
        scrollbar_options = {
            "background": "#3E3E42", "activebackground": "#5A5D5E", "troughcolor": "#181818",
            "relief": "flat", "borderwidth": 0, "highlightthickness": 0, "elementborderwidth": 0,
        }
        vertical_scrollbar = tk.Scrollbar(log_container, orient="vertical", command=self.log_view.yview, **scrollbar_options)
        horizontal_scrollbar = tk.Scrollbar(log_container, orient="horizontal", command=self.log_view.xview, **scrollbar_options)
        self.log_view.configure(yscrollcommand=vertical_scrollbar.set, xscrollcommand=horizontal_scrollbar.set)
        self.log_view.grid(row=0, column=0, sticky="nsew")
        vertical_scrollbar.grid(row=0, column=1, sticky="ns")
        horizontal_scrollbar.grid(row=1, column=0, sticky="ew")
        log_container.rowconfigure(0, weight=1)
        log_container.columnconfigure(0, weight=1)
        controls = ttk.Frame(self.logs_tab); controls.pack(fill="x")
        ttk.Button(controls, text="复制选中", command=self.copy_selected_log).pack(side="left")
        ttk.Button(controls, text="复制全部", command=self.copy_all_logs).pack(side="left", padx=6)
        ttk.Button(controls, text="立即刷新", command=lambda: self.refresh_logs(force=True)).pack(side="right")

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

    def refresh_microphone_status(self) -> None:
        """独立显示默认输入设备；与客户端的 5 秒热插拔恢复相互独立。"""
        try:
            device = preferred_input_device()
            if device is None:
                raise sd.PortAudioError("未找到可用输入设备")
            if device.get("max_input_channels", 0) < 1:
                raise sd.PortAudioError("默认设备没有输入声道")
            self.mic_status.configure(text=microphone_status_text(device), style="MicOnline.TLabel")
        except Exception:
            self.mic_status.configure(text="麦克风：未连接（持续检查中）", style="MicOffline.TLabel")
        if self.winfo_exists():
            self.after(2000, self.refresh_microphone_status)

    def _schedule_log_refresh(self, delay_ms: int = 1000) -> None:
        """保持唯一的日志轮询任务，避免手动刷新叠加定时器。"""
        if self._log_refresh_after_id is not None:
            return
        try:
            if self.winfo_exists():
                self._log_refresh_after_id = self.after(delay_ms, self._run_scheduled_log_refresh)
        except tk.TclError:
            self._log_refresh_after_id = None

    def _run_scheduled_log_refresh(self) -> None:
        """执行一次刷新；单次异常不能打断后续轮询。"""
        self._log_refresh_after_id = None
        try:
            self.refresh_logs()
        except Exception as exc:
            try:
                self.status.configure(text=f"日志刷新失败，正在自动重试：{exc}")
            except tk.TclError:
                pass
        finally:
            self._schedule_log_refresh()

    def _log_selection_active(self) -> bool:
        """只在用户仍聚焦日志选区时暂停自动重绘。"""
        return bool(self.log_view.tag_ranges("sel")) and self.focus_get() == self.log_view

    def refresh_logs(self, force: bool = False) -> None:
        """读取并重绘日志；手动刷新可越过当前文本选区。"""
        content = "\n\n===== 客户端日志 =====\n" + self._read_log_tail(ROOT_DIR / "logs" / "client_latest.log")
        content += "\n\n===== 服务端日志 =====\n" + self._read_log_tail(ROOT_DIR / "logs" / "server_latest.log")
        if content == self._last_log_text or (not force and self._log_selection_active()):
            return
        follow_tail = not self._last_log_text or self.log_view.yview()[1] >= 0.995
        self.log_view.configure(state="normal")
        try:
            self.log_view.delete("1.0", "end")
            for line in content.splitlines(keepends=True):
                tag = log_line_tag(line)
                self.log_view.insert("end", line, tag) if tag else self.log_view.insert("end", line)
            if follow_tail:
                self.log_view.see("end")
        finally:
            self.log_view.configure(state="disabled")
        # 只有完整重绘成功后才提交快照；失败时下一轮必须重试同一内容。
        self._last_log_text = content

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
        self._word_values["client"] = read_lines(HOT_FILE)
        self._refresh_word_table("client")

    def save_hotwords(self) -> None:
        write_lines(HOT_FILE, self._word_values["client"], "# 由 CapsWriter 管理器维护的热词，每行一个")
        self.status.configure(text="热词已保存并热更新")

    def reload_server_hotwords(self) -> None:
        self._word_values["server"] = read_lines(SERVER_HOT_FILE)
        self._refresh_word_table("server")

    def save_server_hotwords(self) -> None:
        write_lines(SERVER_HOT_FILE, self._word_values["server"], "# 由 CapsWriter 管理器维护的服务端识别热词，每行一个")
        if not server_hotword_supported(ServerConfig.model_type):
            self.status.configure(text=f"服务端热词已保存；当前 {ServerConfig.model_type} 引擎不会读取它")
            return
        self.status.configure(text="服务端热词已保存，正在重启识别引擎以载入新词库…")
        self.restart_engine()

    def extract_candidates(self) -> None:
        text = self.article_text.get("1.0", "end")
        tokens = re.findall(r"[A-Za-z][A-Za-z0-9_+.-]{2,}|[\u4e00-\u9fff]{2,8}", text)
        counts: dict[str, int] = {}
        for token in tokens:
            counts[token] = counts.get(token, 0) + 1
        known = set(self._word_values["client"])
        choices = sorted((token for token, count in counts.items() if count == 1 and token not in known), key=lambda x: (-len(x), x))[:200]
        self.candidates.delete(0, "end")
        for token in choices:
            self.candidates.insert("end", token)

    def add_candidates(self) -> None:
        selected = [self.candidates.get(i) for i in self.candidates.curselection()]
        if selected:
            self._word_values["client"] = list(dict.fromkeys([*self._word_values["client"], *selected]))
            self._refresh_word_table("client")
            self.save_hotwords()

    def reload_mappings(self) -> None:
        self._rule_preserved, self._rule_values = read_rule_rows(RULE_FILE)
        self._refresh_rule_table()

    def save_mappings(self) -> None:
        write_rule_rows(RULE_FILE, self._rule_preserved, self._rule_values)
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
        if self._log_refresh_after_id is not None:
            try:
                self.after_cancel(self._log_refresh_after_id)
            except tk.TclError:
                pass
            self._log_refresh_after_id = None
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
    parser.add_argument("--enable-startup", action="store_true")
    parser.add_argument("--disable-startup", action="store_true")
    parser.add_argument("--stop", action="store_true")
    args = parser.parse_args()
    if args.stop:
        stop_previous_manager()
    if args.enable_startup:
        set_startup_enabled(True)
    if args.disable_startup:
        set_startup_enabled(False)
    if args.enable_startup or args.disable_startup or args.stop:
        return
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
