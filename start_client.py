# coding: utf-8
import os
import sys
from core.client import CapsWriterClient

if __name__ == "__main__":
    # 直接实例化并启动门面类即可
    # 环境初始化职责已下放至 CapsWriterClient
    CapsWriterClient(managed='--managed' in sys.argv).start()
