"""Locations and bounded, user-facing configuration."""
from pathlib import Path
import hashlib
import json
import os
import site
import sys
import time
import uuid
import platform

ROOT = Path(__file__).resolve().parent
PROJECT = ROOT.parent
SOFTWARE_ROOT = ROOT / 'tested_software'
DEMO = SOFTWARE_ROOT / 'notepad++' / 'notepad++.exe'


def resolve_program(value=None):
    """Project-relative paths are anchored to this project, never the shell cwd."""
    path = Path(value or DEMO).expanduser()
    if not path.is_absolute():
        path = PROJECT / path
    return path.resolve()


def portable_path(value):
    """Persist project assets relatively; external user programs remain local-only."""
    path = Path(value)
    if not path.is_absolute():
        return path.as_posix()
    try:
        return path.resolve().relative_to(PROJECT.resolve()).as_posix()
    except ValueError:
        return str(path)

DESKTOP_HELP = ('当前进程无法操作 Windows 交互桌面，测试已中断。这是运行环境问题，不是被测软件缺陷。'
                '请保持桌面解锁、远程桌面会话已连接，并从当前登录用户的普通终端或 start_gui_platform.bat 启动平台；'
                '不要在受限沙箱、Windows 服务或非交互会话中运行。')


class DesktopUnavailable(RuntimeError):
    pass


class BackgroundUnsupported(RuntimeError):
    """Never silently fall back to physical input in background mode."""
    pass


def is_desktop_error(error):
    message = str(error).lower()
    return isinstance(error, DesktopUnavailable) or 'no active desktop' in message or 'setcursorpos' in message and 'no error message' in message


def dependencies():
    version = f'py{sys.version_info.major}{sys.version_info.minor}'
    candidates = [ROOT / '.gui_deps' / version]
    for candidate in candidates:
        if (candidate / 'pywinauto').is_dir():
            site.addsitedir(str(candidate))
            return candidate
    return None


def ready():
    return sys.platform == 'win32' and dependencies() is not None


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def validate(raw):
    executable = resolve_program(raw.get('executable'))
    if executable.suffix.lower() != '.exe' or not executable.is_file():
        raise ValueError('请选择本机存在的图形界面 EXE')
    config = {'executable': portable_path(executable), 'profile': 'notepad' if executable == DEMO.resolve() else 'generic'}
    for name, default, lower, upper in [('rounds', 1, 1, 10), ('actions', 25, 1, 300),
                                        ('depth', 3, 1, 8), ('timeout', 12, 3, 60),
                                        ('seconds', 300, 15, 3600), ('interval', .7, .2, 5)]:
        value = float(raw.get(name, default))
        if not lower <= value <= upper:
            raise ValueError(f'{name} 需在 {lower}～{upper} 之间')
        config[name] = value if name == 'interval' else int(value)
    args = raw.get('args', [])
    if not isinstance(args, list) or len(args) > 20 or any(not isinstance(a, str) or len(a) > 2000 for a in args):
        raise ValueError('启动参数格式不正确')
    config['args'] = args
    config['backend'] = raw.get('backend', 'win32')
    if config['backend'] not in ('win32', 'uia'):
        raise ValueError('控件后端无效')
    config['interaction'] = raw.get('interaction', 'background')
    if config['interaction'] not in ('background', 'foreground'):
        raise ValueError('交互方式必须为后台控件操作或前台真实输入')
    for name, default in [('excluded', '删除,卸载,支付,购买,登录,密码,更新,打印,Delete,Uninstall,Purchase,Login,Password,Update,Print'),
                          ('text', 'GUI test 123'), ('expected', '')]:
        config[name] = str(raw.get(name, default))[:2000]
    config['sha256'] = digest(executable)
    config['version'] = '8.8.7' if config['profile'] == 'notepad' else '未读取版本；以文件 SHA256 识别'
    config['environment'] = {'os': platform.platform(), 'python': platform.python_version(), 'method': '界面状态广度优先遍历'}
    return config


def atomic_json(path, value):
    path = Path(path)
    temp = path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp')
    temp.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')
    try:
        for attempt in range(12):
            try:
                os.replace(temp, path)
                return
            except PermissionError:
                if attempt == 11:
                    raise
                time.sleep(.025)
    finally:
        if temp.exists():
            temp.unlink(missing_ok=True)
