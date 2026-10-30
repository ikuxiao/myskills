"""Start the application with uv-managed Python and requirements."""
from __future__ import annotations

from pathlib import Path
import shutil
import subprocess
import sys


def main() -> int:
    uv = shutil.which('uv')
    if uv is None:
        print('未找到 uv，请先安装：python -m pip install uv', file=sys.stderr)
        return 1

    root = Path(__file__).resolve().parent
    command = [
        uv, 'run', '--no-project', '--python', '3.12',
        '--with-requirements', str(root / 'requirements.txt'),
        '--', 'python', str(root / 'main.py'), *sys.argv[1:],
    ]
    try:
        return subprocess.call(command, cwd=root)
    except KeyboardInterrupt:
        return 130
    except OSError as exc:
        print(f'uv 启动失败：{exc}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
