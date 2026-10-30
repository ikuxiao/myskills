"""Run with python main.py; no database, frontend build or remote API needed."""
from __future__ import annotations

import argparse
import logging
import os
from pathlib import Path
import sys

from filelock import FileLock, Timeout

# Some portable Windows Python distributions omit the script directory.
sys.path.insert(0, str(Path(__file__).resolve().parent))


def default_data_dir() -> Path:
    if os.name == 'nt':
        return Path(os.environ.get('LOCALAPPDATA', str(Path.home() / 'AppData' / 'Local'))) / 'AI-Dev-Center'
    return Path(os.environ.get('XDG_DATA_HOME', str(Path.home() / '.local' / 'share'))) / 'ai-dev-center'


def main():
    parser = argparse.ArgumentParser(description='AI 开发效率与资源管理中心（仅本机）')
    parser.add_argument('--data-dir', type=Path, default=default_data_dir(), help='数据目录，默认用户本地数据目录')
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--no-open', action='store_true', help='不自动打开浏览器')
    parser.add_argument('--no-seed', action='store_true', help='新数据目录不创建示例资源')
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        parser.error('端口需在 1024–65535 之间。')
    from app.storage import AppError
    from app.resources import ResourceManager
    try:
        manager = ResourceManager(args.data_dir)
        instance = FileLock(str(manager.store.root / '.instance.lock'), timeout=0)
        instance.acquire()
    except Timeout:
        parser.exit(1, '此数据目录已由另一实例使用。请切换端口及 --data-dir，或关闭原实例。\n')
    except (AppError, OSError) as exc:
        parser.exit(1, f'启动失败：{exc}\n')
    try:
        log_dir = manager.store.root / 'logs'
        log_dir.mkdir(parents=True, exist_ok=True)
        from logging.handlers import RotatingFileHandler
        logging.basicConfig(level=logging.INFO, handlers=[logging.StreamHandler(), RotatingFileHandler(log_dir / 'app.log', maxBytes=2_000_000, backupCount=3, encoding='utf-8')])
        if not args.no_seed:
            manager.seed()
        from nicegui import app, core, ui
        from starlette.middleware.trustedhost import TrustedHostMiddleware
        app.add_middleware(TrustedHostMiddleware, allowed_hosts=['127.0.0.1', 'localhost'])
        core.sio.eio.cors_allowed_origins = [f'http://127.0.0.1:{args.port}', f'http://localhost:{args.port}']
        from app.ui import register_ui
        register_ui(manager)
        print(f'AI Dev Center: http://127.0.0.1:{args.port}', flush=True)
        print(f'Data: {manager.store.root}', flush=True)
        ui.run(host='127.0.0.1', port=args.port, reload=False, show=not args.no_open, title='AI Dev Center · 开发效率与资源管理中心', language='zh-CN', favicon='🌿', show_welcome_message=False)
    except (AppError, OSError) as exc:
        print(f'启动失败：{exc}', file=sys.stderr)
        raise SystemExit(1) from exc
    finally:
        instance.release()


if __name__ == '__main__':
    main()
