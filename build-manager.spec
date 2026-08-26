# -*- mode: python ; coding: utf-8 -*-
"""将统一管理器打成单文件 exe，供安装包与绿色版直接启动。"""

from PyInstaller.utils.hooks import collect_all, collect_data_files


binaries = []
datas = []
hiddenimports = [
    'sounddevice',
    'pynput',
    'pystray',
    'PIL',
    'PIL.Image',
]

try:
    pillow = collect_all('PIL')
    datas += pillow[0]
    binaries += pillow[1]
except Exception:
    pass

a = Analysis(
    ['start_manager.py'],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=['build_hook.py'],
    excludes=['IPython', 'PySide6', 'PySide2', 'PyQt5', 'matplotlib', 'wx'],
    noarchive=True,
)

# core 与配置文件作为发行包根目录的可编辑源码保留，运行时由 build_hook.py 定位。
private_modules = ['core', 'config_client', 'config_server', 'LLM']
a.pure = [
    item for item in a.pure
    if not any(item[0] == module or item[0].startswith(module + '.') for module in private_modules)
]
a.datas = [
    item for item in a.datas
    if not any(
        item[0].startswith(module + '/')
        or item[0] in (module + '.py', module + '.pyc')
        for module in private_modules
    )
]

pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    name='start_manager',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    icon=['assets\\icon.ico'],
)
