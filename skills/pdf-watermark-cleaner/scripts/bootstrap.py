#!/usr/bin/env python3
"""显式授权后安装到独立venv；默认和 --check 只读，不安装。"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

sys.dont_write_bytecode = True
from cleaner import local_path, PINS


def default_venv():
    return Path.home() / '.cache' / 'pdf-watermark-cleaner' / 'venv'


def python_in(path):
    return path / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')


def run(argv, env=None):
    return subprocess.run([str(x) for x in argv], shell=False, check=True, env=env)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--check', action='store_true', help='仅静态检查路径存在性，不启动目标解释器')
    mode.add_argument('--verify', action='store_true', help='仅在用户授权后启动已有可信环境运行 doctor')
    mode.add_argument('--install', action='store_true', help='仅在用户已授权安装后使用')
    parser.add_argument('--venv', default=str(default_venv()))
    args = parser.parse_args(argv)
    try:
        if sys.version_info < (3, 10):
            raise ValueError('需要Python >=3.10；请自行准备可信Python')
        target = local_path(args.venv)
        exe = python_in(target)
        if args.verify:
            if not exe.is_file():
                raise ValueError('目标解释器不存在；--verify 不创建或安装环境')
            run([exe, '-I', '-B', Path(__file__).with_name('cleaner.py'), 'doctor'])
            return 0
        if not args.install:
            exists = exe.is_file()
            print(json.dumps({'venv': str(target), 'executable': str(exe), 'exists': exists,
                              'verified': False,
                              'guidance': '仅检查路径存在性，未验证可运行；未启动目标解释器、未导入第三方依赖。确认已有环境可信并取得用户授权后使用 --verify；新环境安装另行授权后使用 --install。'}, ensure_ascii=False), flush=True)
            return 0 if exists else 1
        # 不复用现有环境，避免修改其它项目的依赖。
        if target.exists() or os.path.lexists(Path(args.venv).expanduser()):
            raise ValueError('隔离目录已存在；请使用 --check 或指定新的空路径，不自动覆盖')
        target.parent.mkdir(parents=True, exist_ok=True)
        target.mkdir(exist_ok=False)
        run([sys.executable, '-I', '-B', '-m', 'venv', target])
        env = os.environ.copy()
        for key in list(env):
            if key.upper().startswith(('PIP_', 'PYTHON')):
                del env[key]
        env['PIP_CONFIG_FILE'] = os.devnull
        command = [exe, '-I', '-B', '-m', 'pip', '--isolated', '--disable-pip-version-check',
                   'install', '--index-url', 'https://pypi.org/simple', '--only-binary=:all:',
                   '--no-deps', '--no-cache-dir', '--no-input']
        requirements = [f'{name}=={version}' for name, version in PINS.items()]
        # 在目标解释器上先核实可用wheel；拒绝源码包和构建脚本。
        run(command + ['--dry-run'] + requirements, env)
        run(command + requirements, env)
        run([exe, '-I', '-B', Path(__file__).with_name('cleaner.py'), 'doctor'])
        print(json.dumps({'ok': True, 'venv': str(target), 'python': str(exe)}, ensure_ascii=False))
        return 0
    except Exception as exc:
        print(json.dumps({'ok': False, 'error': str(exc),
                          'guidance': '失败环境原地保留；请检查错误，不自动清理或改用全局安装。'}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
