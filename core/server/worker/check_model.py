# coding: utf-8
"""
模型检查模块

检查配置的语音模型文件；缺失时自动从官方 Release 下载并校验。
"""

from config_server import ServerConfig as Config
from config_server import ModelPaths, ModelDownloadLinks
from core.server.state import console
from .model_download import (
    ModelDownloadError,
    ensure_package,
    missing_files,
    packages_for_model_type,
)
from . import logger



def check_model() -> None:
    """
    根据配置的模型类型检查所需的模型文件是否存在
    
    如果模型文件不存在，默认自动下载；下载失败时显示原因和手动下载链接。
    
    Raises:
        SystemExit: 当模型类型不支持或模型文件缺失时退出
    """
    model_type = Config.model_type.lower()
    logger.debug(f"检查模型文件, 类型: {model_type}")

    try:
        packages = packages_for_model_type(model_type)
    except ValueError:
        error_msg = f"不支持的模型类型: {Config.model_type}"
        logger.error(error_msg)
        console.print(f'''
    [bold red]不支持的模型类型：{Config.model_type}[/bold red]

    请在 config_server.py 中将 ServerConfig.model_type 设置为：
    - 'fun_asr_nano'
    - 'sensevoice'
    - 'paraformer'
    - 'qwen_asr'

        ''', style='bright_red')
        raise SystemExit(1)

    missing_before = [(package, missing_files(package)) for package in packages]
    missing_before = [(package, paths) for package, paths in missing_before if paths]
    if missing_before:
        for package, paths in missing_before:
            for file_path in paths:
                logger.warning(f"模型文件缺失: {file_path}")
        if Config.auto_download_models:
            console.print('[yellow]首次运行检测到模型缺失，将自动下载；可在运行日志查看进度。[/yellow]')
            try:
                for package, _ in missing_before:
                    ensure_package(package)
            except (ModelDownloadError, ValueError) as exc:
                logger.error(f"模型自动下载失败：{exc}")

    missing_after = [(package, missing_files(package)) for package in packages]
    missing_after = [(package, paths) for package, paths in missing_after if paths]
    if missing_after:
        total_missing = sum(len(paths) for _, paths in missing_after)
        logger.error(f"模型文件检查失败，共 {total_missing} 个文件缺失")
        error_msg = f'\n[bold red]未能找到模型文件[/bold red]\n\n'
        error_msg += f'当前配置的模型类型：[bold yellow]{model_type}[/bold yellow]\n\n'
        for package, paths in missing_after:
            error_msg += f'\n{package.display_name}：\n'
            for file_path in paths:
                error_msg += f'未找到：[bold yellow]{file_path.name}[/bold yellow]\n'

        # 提供统一下载页面链接
        error_msg += f'\n模型发布页面：\n'
        error_msg += f'[cyan]{ModelDownloadLinks.models_page}[/cyan]\n\n'

        error_msg += f'请根据发布页说明，将模型解压到 [cyan]{ModelPaths.model_dir}[/cyan] 下的正确目录\n'
        error_msg += '\n'
        
        logger.error(error_msg)
        raise SystemExit(1)

    # 所有必需文件检查通过
    logger.info(f"模型文件检查通过 ({model_type})")
    console.print(f'[green4]模型文件检查通过 ({model_type})', end='\n\n')
