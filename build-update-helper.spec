# -*- mode: python ; coding: utf-8 -*-
"""独立更新助手必须是单文件，避免安装时覆盖正在运行的管理器。"""

a = Analysis(
    ['update_helper.py'],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    name='CapsWriter-Update',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    icon=['assets\\icon.ico'],
)
