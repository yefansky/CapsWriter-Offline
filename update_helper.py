# coding: utf-8
"""独立更新助手：下载、SHA-256 校验、安装并拉起最新版管理器。"""

from __future__ import annotations

import argparse
import hashlib
import os
import subprocess
import tempfile
import threading
from pathlib import Path
from tkinter import messagebox, ttk
import tkinter as tk
from urllib.request import Request, urlopen


CHUNK_SIZE = 1024 * 1024


def _download(url: str, target: Path, progress) -> None:
    request = Request(url, headers={"User-Agent": "CapsWriter-Offline-Updater/1.0"})
    with urlopen(request, timeout=30) as response, target.open("wb") as output:
        total = int(response.headers.get("Content-Length", "0") or 0)
        downloaded = 0
        while chunk := response.read(CHUNK_SIZE):
            output.write(chunk)
            downloaded += len(chunk)
            progress(downloaded, total)


def _expected_digest(checksum_url: str, asset_name: str) -> str:
    request = Request(checksum_url, headers={"User-Agent": "CapsWriter-Offline-Updater/1.0"})
    with urlopen(request, timeout=20) as response:
        content = response.read().decode("utf-8")
    for line in content.splitlines():
        pieces = line.split()
        if len(pieces) >= 2 and pieces[-1].lstrip("*") == asset_name:
            digest = pieces[0].lower()
            if len(digest) == 64 and all(char in "0123456789abcdef" for char in digest):
                return digest
    raise RuntimeError(f"更新校验清单中缺少 {asset_name}")


def _verify(path: Path, expected: str) -> None:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(4 * CHUNK_SIZE):
            digest.update(chunk)
    if digest.hexdigest().lower() != expected:
        raise RuntimeError("更新包 SHA-256 校验失败，已拒绝安装")


class UpdateWindow(tk.Tk):
    def __init__(self, args: argparse.Namespace):
        super().__init__()
        self.args = args
        self.title("CapsWriter 正在更新")
        self.geometry("460x150")
        self.resizable(False, False)
        self.protocol("WM_DELETE_WINDOW", lambda: None)
        ttk.Label(self, text="正在下载并校验最新版本，请不要关闭此窗口。").pack(padx=24, pady=(24, 8))
        self.detail = ttk.Label(self, text="准备下载…")
        self.detail.pack(padx=24, anchor="w")
        self.progress = ttk.Progressbar(self, orient="horizontal", length=410, mode="determinate")
        self.progress.pack(padx=24, pady=12)
        threading.Thread(target=self._run, name="capswriter-updater", daemon=True).start()

    def _set_progress(self, downloaded: int, total: int) -> None:
        def apply() -> None:
            if total:
                self.progress.configure(maximum=total, value=downloaded)
                self.detail.configure(text=f"正在下载：{downloaded / 1024 / 1024:.1f} / {total / 1024 / 1024:.1f} MB")
            else:
                self.detail.configure(text=f"正在下载：{downloaded / 1024 / 1024:.1f} MB")
        self.after(0, apply)

    def _run(self) -> None:
        installer = Path(tempfile.gettempdir()) / f"CapsWriter-Offline-{self.args.tag}-Setup.exe"
        part = installer.with_suffix(".part")
        try:
            expected = _expected_digest(self.args.checksum_url, self.args.asset_name)
            self.after(0, lambda: self.detail.configure(text="正在下载更新包…"))
            _download(self.args.installer_url, part, self._set_progress)
            self.after(0, lambda: self.detail.configure(text="正在校验更新包…"))
            _verify(part, expected)
            os.replace(part, installer)
            self.after(0, lambda: self.detail.configure(text="正在安装最新版本…"))
            subprocess.run(
                [str(installer), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/CLOSEAPPLICATIONS"],
                check=True,
            )
            restart = Path(self.args.restart_exe)
            if restart.is_file():
                subprocess.Popen([str(restart), "--restart"], cwd=str(restart.parent))
            self.after(0, self._success)
        except Exception as exc:
            self.after(0, lambda: self._failure(str(exc)))
        finally:
            part.unlink(missing_ok=True)

    def _success(self) -> None:
        messagebox.showinfo("CapsWriter 已更新", "已安装最新版本并重新启动 CapsWriter。", parent=self)
        self.destroy()

    def _failure(self, detail: str) -> None:
        messagebox.showerror("CapsWriter 更新失败", f"未修改当前安装。\n\n原因：{detail}", parent=self)
        self.destroy()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tag", required=True)
    parser.add_argument("--installer-url", required=True)
    parser.add_argument("--checksum-url", required=True)
    parser.add_argument("--asset-name", required=True)
    parser.add_argument("--restart-exe", required=True)
    args = parser.parse_args()
    UpdateWindow(args).mainloop()


if __name__ == "__main__":
    main()
