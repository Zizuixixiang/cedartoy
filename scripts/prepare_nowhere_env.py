#!/usr/bin/env python3
"""Isolated, resumable Nowhere runtime preparation. Default is read-only preflight.

No sudo, shell commands, service actions, system Python changes or optional terrain.
Linux x86_64/glibc only; run with host Python >=3.10. --prepare performs installation.
"""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import signal
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / '.nowhere-runtime'
VENV = ROOT / '.venv-nowhere'
REQUIREMENTS = ROOT / 'nowhere_adapter/requirements.txt'
VERSION = '3.11.13'
RELEASE = '20250918'
ARCHIVE = f'cpython-{VERSION}%2B{RELEASE}-x86_64-unknown-linux-gnu-install_only_stripped.tar.gz'
URL = f'https://github.com/astral-sh/python-build-standalone/releases/download/{RELEASE}/{ARCHIVE}'
SHA256 = '511ceceeff184ad742a35583755f5070c0274cc0f0bb5c9218abd939240a2146'
UPSTREAM = 'f0803c1053e5f97a6f44bab26e15043d71825ec7'
OWNER = 'cedartoy-nowhere-environment-v1'
MIB = 1024 * 1024


def require_disk(minimum=768 * MIB):
    free = shutil.disk_usage(ROOT).free
    if free < minimum:
        raise RuntimeError(f'磁盘不足：剩余 {free // MIB} MiB，需要至少 {minimum // MIB} MiB；未清理任何他人文件')
    return free


def clean_env(temp):
    # Do not consume credentials, pip indexes/config or shared Python configuration.
    env = {k: v for k, v in os.environ.items() if not k.startswith(('PIP_', 'PYTHON', 'UV_'))}
    env.update(PIP_CONFIG_FILE=os.devnull, PIP_DISABLE_PIP_VERSION_CHECK='1',
               PYTHONNOUSERSITE='1', PYTHONDONTWRITEBYTECODE='1', TMPDIR=str(temp))
    return env


def run(command, env, timeout=900):
    """Bound total time and disk throughout pip downloads/unpacking; never print raw logs."""
    with tempfile.TemporaryFile(dir=env['TMPDIR']) as log:
        proc = subprocess.Popen(command, env=env, cwd=ROOT, stdout=log,
                                stderr=subprocess.STDOUT, start_new_session=True)
        deadline = time.monotonic() + timeout
        try:
            while proc.poll() is None:
                require_disk()
                if time.monotonic() > deadline:
                    raise RuntimeError(f'步骤超时（{timeout} 秒）')
                time.sleep(.5)
            if proc.returncode:
                # Index URLs may carry credentials in other environments; do not echo pip output.
                raise RuntimeError(f'步骤退出码 {proc.returncode}；已停止。检查官方 PyPI/GitHub 可达性、平台 wheel 兼容性及磁盘。')
            log.seek(0)
            return log.read().decode('utf-8')
        finally:
            if proc.poll() is None:
                os.killpg(proc.pid, signal.SIGTERM)
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    os.killpg(proc.pid, signal.SIGKILL)
                    proc.wait()


def download(target):
    deadline = time.monotonic() + 180
    digest, size = hashlib.sha256(), 0
    with urllib.request.urlopen(URL, timeout=20) as response, target.open('wb') as output:
        while True:
            chunk = response.read(1024 * 1024)
            if not chunk:
                break
            size += len(chunk)
            if size > 40 * MIB or time.monotonic() > deadline:
                raise RuntimeError('解释器下载超时或超出 40 MiB 限额')
            require_disk()
            digest.update(chunk)
            output.write(chunk)
    if digest.hexdigest() != SHA256:
        raise RuntimeError('解释器 SHA-256 不匹配；拒绝解包')


def unpack(archive, target):
    with tarfile.open(archive, 'r:gz') as tar:
        members = tar.getmembers()
        if sum(item.size for item in members) > 256 * MIB:
            raise RuntimeError('解释器解包体积超限')
        for item in members:
            dest = (target / item.name).resolve()
            if not dest.is_relative_to(target.resolve()) or item.isdev() or item.isfifo():
                raise RuntimeError('不安全的解释器归档路径')
            if item.issym() or item.islnk():
                link = (dest.parent / item.linkname if item.issym() else target / item.linkname).resolve()
                if not link.is_relative_to(target.resolve()):
                    raise RuntimeError('不安全的解释器归档链接')
        tar.extractall(target, members=members)


def metadata():
    return {'owner': OWNER, 'python': VERSION, 'release': RELEASE, 'sha256': SHA256,
            'upstream': UPSTREAM, 'requirements_sha256': hashlib.sha256(REQUIREMENTS.read_bytes()).hexdigest()}


def preflight():
    if platform.system() != 'Linux' or platform.machine() != 'x86_64' or platform.libc_ver()[0] != 'glibc':
        raise RuntimeError('本脚本仅支持 Linux x86_64/glibc；其他宿主需明确准备对应独立解释器')
    head = subprocess.check_output(['git', '-C', str(ROOT / 'vendor/nowhere'), 'rev-parse', 'HEAD'], timeout=10, text=True).strip()
    if head != UPSTREAM:
        raise RuntimeError('上游版本不符；拒绝自动 checkout 或更新')
    for directory in (RUNTIME, VENV):
        if directory.is_symlink():
            raise RuntimeError(f'拒绝符号链接环境目录：{directory.name}')
        if directory.exists() and not (directory / '.nowhere-owner').is_file():
            raise RuntimeError(f'已有非本脚本管理的 {directory.name}；不覆盖，请人工核实')
        if directory.exists() and (directory / '.nowhere-owner').read_text() != OWNER:
            raise RuntimeError('环境目录归属标记不匹配')
    require_disk(768 * MIB if (RUNTIME / 'python/bin/python3.11').exists() else 2048 * MIB)
    print(f'preflight OK: Linux x86_64/glibc; pinned Python {VERSION}; free {shutil.disk_usage(ROOT).free // MIB} MiB')


def prepare():
    preflight()
    RUNTIME.mkdir(exist_ok=True)
    (RUNTIME / '.nowhere-owner').write_text(OWNER)
    with (RUNTIME / 'prepare.lock').open('w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with tempfile.TemporaryDirectory(prefix='prepare-', dir=RUNTIME) as temp:
            temp = Path(temp)
            env = clean_env(temp)
            python = RUNTIME / 'python/bin/python3.11'
            if not python.exists():
                if (RUNTIME / 'python').exists():
                    raise RuntimeError('解释器目录不完整；保留现场，需人工检查')
                print('Downloading checksum-pinned standalone Python (29 MiB)…', flush=True)
                archive = temp / 'python.tar.gz'
                download(archive)
                unpack(archive, temp)
                (temp / 'python').rename(RUNTIME / 'python')
            actual = run([str(python), '-I', '-c', 'import platform;print(platform.python_version())'], env, 30).strip()
            if actual != VERSION:
                raise RuntimeError('独立解释器实际版本不符')
            manifest = RUNTIME / 'manifest.json'
            if manifest.exists():
                saved = json.loads(manifest.read_text())
                if any(saved.get(k) != v for k, v in metadata().items()):
                    raise RuntimeError('环境 manifest 与固定版本不符；不自动升级已有环境')
                freeze = run([str(VENV / 'bin/python'), '-I', '-m', 'pip', 'freeze', '--all'], env, 30)
                if freeze != saved['freeze']:
                    raise RuntimeError('环境包版本发生漂移；不静默修复')
                run([str(VENV / 'bin/python'), '-I', '-m', 'pip', 'check'], env, 30)
                print('READY: existing environment verified; no reinstall')
                return
            VENV.mkdir(exist_ok=True)
            (VENV / '.nowhere-owner').write_text(OWNER)
            if not (VENV / 'bin/python').exists():
                run([str(python), '-I', '-m', 'venv', str(VENV)], env, 120)
            vpython = str(VENV / 'bin/python')
            print('Installing pinned runtime dependencies (no cache, wheels only, 15 minute total timeout)…', flush=True)
            pip = [vpython, '-I', '-m', 'pip', 'install', '--index-url', 'https://pypi.org/simple',
                   '--no-cache-dir', '--no-input', '--only-binary=:all:', '--timeout', '20', '--retries', '1']
            run(pip + ['pip==25.2'], env, 180)
            run(pip + ['-r', str(REQUIREMENTS), '--report', str(temp / 'pip-report.json')], env)
            run([vpython, '-I', '-m', 'pip', 'check'], env, 30)
            freeze = run([vpython, '-I', '-m', 'pip', 'freeze', '--all'], env, 30)
            # Official index only. Record resolved versions and wheel hashes, not environment variables.
            report = json.loads((temp / 'pip-report.json').read_text())
            wheels = [{'name': x['metadata']['name'], 'version': x['metadata']['version'],
                       'hashes': x.get('download_info', {}).get('archive_info', {}).get('hashes', {})}
                      for x in report['install']]
            (RUNTIME / 'resolved-requirements.txt').write_text(freeze)
            staged = RUNTIME / 'manifest.json.tmp'
            staged.write_text(json.dumps({**metadata(), 'freeze': freeze, 'wheels': wheels}, indent=2) + '\n')
            staged.replace(manifest)
            print('READY: .venv-nowhere; versions and wheel hashes recorded in .nowhere-runtime/manifest.json')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--prepare', action='store_true', help='download/install isolated environment; otherwise preflight only')
    args = parser.parse_args()
    try:
        prepare() if args.prepare else preflight()
    except Exception as exc:
        # Suppress URL/credential-bearing network exception strings.
        print('STOP: ' + (str(exc) if isinstance(exc, RuntimeError) else type(exc).__name__), file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
