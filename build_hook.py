import sys
import os
from os.path import abspath, dirname, exists, join, normcase

# 将「执行文件所在目录」添加到「模块查找路径」
# 这确保了可以找到复制的源文件（config.py, core/ 等）
executable_dir = dirname(sys.executable)
sys.path.insert(0, executable_dir)

# PyInstaller 打包时，第三方依赖（DLL, PYD）放在 internal/ 目录
# 需要将 internal/ 也添加到路径，否则 Python 无法找到这些依赖
internal_dir = join(executable_dir, 'internal')

# onedir 的 server/client 以 ``<程序目录>/internal`` 作为自身 _MEIPASS，
# 可以继续把该目录放在前面。单文件 manager 的 _MEIPASS 是临时解包目录；
# 此时同级 internal 属于另外两个 exe，不能让它遮蔽 manager 自带的模块。
bundle_dir = getattr(sys, '_MEIPASS', '')
uses_sibling_internal = (
    not bundle_dir
    or normcase(abspath(bundle_dir)) == normcase(abspath(internal_dir))
)
if exists(internal_dir) and uses_sibling_internal:
    sys.path.insert(0, internal_dir)
