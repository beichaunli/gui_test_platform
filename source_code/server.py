"""Local-only GUI traversal workbench. One isolated worker owns the desktop."""
import argparse
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import secrets
import subprocess
import sys
import threading
import time
from urllib.parse import parse_qs, urlsplit
import re
import socket
import os

from common import ROOT, PROJECT, DEMO, ready, validate, dependencies, portable_path
from reports import build
from storage import Store
from hotkey import StopShortcut


class LocalHTTPServer(ThreadingHTTPServer):
    """On Windows, refuse duplicate listeners instead of sharing requests randomly."""
    allow_reuse_address = False

    def server_bind(self):
        if sys.platform == 'win32':
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


class Controller:
    def __init__(self, store):
        self.store = store
        self.lock = threading.Lock()
        self.process = None
        self.active = None
        for task in store.tasks():
            if task['status'] in ('queued', 'running', 'stopping'):
                store.update(task['id'], 'interrupted', message='上次服务中断；已有记录保留，请新建任务')
                build(store, task['id'])

    def start(self, raw, probe=False):
        config = validate(raw)
        if not ready():
            raise ValueError('GUI 依赖未就绪，请使用启动服务的同一 Python 执行 source_code/setup_gui.py')
        config['probe'] = probe
        with self.lock:
            if self.active:
                raise ValueError('当前桌面已有任务，请先停止或等待完成')
            task = self.store.create(config)
            log = (self.store.directory(task) / 'worker.log').open('wb')
            try:
                process = subprocess.Popen([sys.executable, str(ROOT / 'worker.py'), str(self.store.root), task],
                                           cwd=ROOT, stdout=log, stderr=log,
                                           creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0)
            finally:
                log.close()
            self.process, self.active = process, task
            threading.Thread(target=self.monitor, args=(task, process), daemon=True).start()
            return task

    def cleanup(self, task):
        try:
            dependencies()
            import psutil
            owners = json.loads((self.store.directory(task) / 'owner.json').read_text(encoding='utf-8'))
            for pid, birth in owners.items():
                try:
                    process = psutil.Process(int(pid))
                    if process.create_time() == birth:
                        process.kill()
                except psutil.Error:
                    pass
        except (OSError, ImportError, ValueError):
            pass

    def monitor(self, task, process):
        deadline = None
        limit = time.monotonic()+self.store.task(task)['config']['seconds']+30
        while process.poll() is None:
            if (self.store.directory(task) / 'stop').exists() and deadline is None:
                deadline = time.monotonic()+self.store.task(task)['config']['timeout']+2
            if (deadline and time.monotonic() > deadline) or time.monotonic() > limit:
                self.cleanup(task)
                process.kill()
                break
            time.sleep(.2)
        process.wait()
        self.cleanup(task)
        item = self.store.task(task)
        if item['status'] in ('queued', 'running', 'stopping'):
            stopped = (self.store.directory(task) / 'stop').exists()
            self.store.event(task, label='工作进程结束', category='system', status='cancelled' if stopped else 'environment_error',
                             reason='超时后停止工作进程，保留已有证据' if stopped else '工作进程异常退出，详见 worker.log', check='未验证功能正确性')
            self.store.update(task, 'stopped' if stopped else 'failed', message='测试已停止' if stopped else '执行进程异常退出', finished=time.time())
            build(self.store, task)
        with self.lock:
            if self.active == task:
                self.active, self.process = None, None

    def stop(self, task):
        with self.lock:
            if self.active != task:
                raise ValueError('该任务未在运行')
            (self.store.directory(task) / 'stop').touch()
            self.store.update(task, 'stopping', message='停止请求已收到；不再安排新动作，正在保留记录')


def handler(store, controller, token):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def send(self, data, content='application/json; charset=utf-8', status=200):
            if not isinstance(data, bytes):
                data = json.dumps(data, ensure_ascii=False).encode('utf-8')
            self.send_response(status)
            self.send_header('Content-Type', content)
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Referrer-Policy', 'no-referrer')
            self.send_header('Content-Security-Policy', "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; frame-ancestors 'none'")
            self.end_headers()
            try:
                self.wfile.write(data)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def allowed(self):
            host = self.headers.get('Host', '')
            return host in (f'127.0.0.1:{self.server.server_port}', f'localhost:{self.server.server_port}')

        def do_GET(self):
            if not self.allowed():
                return self.send({'error': '非法 Host'}, status=403)
            parsed = urlsplit(self.path)
            path, query = parsed.path, parse_qs(parsed.query)
            try:
                if path in ('/', '/app.js', '/style.css'):
                    name = 'index.html' if path == '/' else path[1:]
                    content = {'index.html': 'text/html', 'app.js': 'text/javascript', 'style.css': 'text/css'}[name]
                    return self.send((ROOT / 'static' / name).read_bytes(), content+'; charset=utf-8')
                if path == '/api/session':
                    return self.send({'token': token, 'ready': ready(), 'demo': portable_path(DEMO), 'demo_exists': DEMO.exists(),
                                      'active': controller.active, 'root': '.',
                                      'pid': os.getpid(), 'build': 'background-controls-v1',
                                      'stop_hotkey': 'Ctrl+Alt+F12' if getattr(controller, 'stop_shortcut', None) and controller.stop_shortcut.available else None})
                if path == '/api/tasks':
                    return self.send(store.tasks())
                single = re.fullmatch('/api/tasks/([a-f0-9]{12})/events/([0-9]+)', path)
                if single:
                    found = store.events(single[1], int(single[2])-1, 1)
                    if not found or found[0]['seq'] != int(single[2]):
                        raise ValueError('事件不存在')
                    return self.send(found[0])
                match = re.fullmatch('/api/tasks/([a-f0-9]{12})(?:/(events))?', path)
                if match:
                    task = match[1]
                    if match[2]:
                        after = max(0, int(query.get('after', ['0'])[0]))
                        return self.send(store.events(task, after, 100))
                    return self.send(store.task(task))
                match = re.fullmatch('/files/([a-f0-9]{12})/(.+)', path)
                if match:
                    directory = store.directory(match[1])
                    relative = match[2]
                    # Explicit file allowlist, no arbitrary files, traversal, worker config or secrets.
                    if not re.fullmatch(r'(report\.(?:html|json)|manifest\.json|evidence\.zip|preview\.png|evidence/[0-9]+\.(?:png|json))', relative):
                        raise ValueError('不允许访问此文件')
                    file = directory / relative
                    if not file.is_file():
                        return self.send({'error': '报告或截图尚未生成'}, status=404)
                    content = {'.png': 'image/png', '.html': 'text/html; charset=utf-8', '.json': 'application/json; charset=utf-8', '.zip': 'application/zip'}[file.suffix]
                    return self.send(file.read_bytes(), content)
                return self.send({'error': '接口不存在'}, status=404)
            except (ValueError, KeyError, OSError) as exc:
                self.send({'error': str(exc)}, status=400)

        def do_POST(self):
            if not self.allowed() or not hmac.compare_digest(self.headers.get('X-Test-Token', ''), token):
                return self.send({'error': '请刷新页面后操作'}, status=403)
            origin = self.headers.get('Origin')
            if origin and origin not in (f'http://127.0.0.1:{self.server.server_port}', f'http://localhost:{self.server.server_port}'):
                return self.send({'error': '非法来源'}, status=403)
            try:
                length = int(self.headers.get('Content-Length', 0))
                if not 0 < length <= 65536:
                    raise ValueError('请求体大小无效')
                raw = json.loads(self.rfile.read(length))
                if not isinstance(raw, dict):
                    raise ValueError('配置须为对象')
                if self.path in ('/api/tasks', '/api/probe'):
                    return self.send({'id': controller.start(raw, self.path == '/api/probe')})
                if self.path == '/api/choose-exe':
                    # Native picker runs on a fresh thread/process with no shell command interpolation.
                    if controller.active:
                        raise ValueError('请先停止当前测试，再选择程序')
                    result = subprocess.run([sys.executable, str(ROOT / 'picker.py')], capture_output=True, timeout=120,
                                            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0)
                    chosen = result.stdout.decode('utf-8').strip()
                    return self.send({'path': portable_path(chosen) if chosen else ''})
                match = re.fullmatch('/api/tasks/([a-f0-9]{12})/(stop|note)', self.path)
                if match:
                    if match[2] == 'stop':
                        controller.stop(match[1])
                    else:
                        store.note(match[1], int(raw['seq']), raw.get('note', ''), raw.get('false_positive', False))
                        if controller.active != match[1]:
                            build(store, match[1])
                    return self.send({'ok': True})
                self.send({'error': '接口不存在'}, status=404)
            except (ValueError, KeyError, OSError, subprocess.TimeoutExpired) as exc:
                self.send({'error': str(exc)}, status=400)

    return Handler


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=8877)
    parser.add_argument('--data-dir', type=Path, default=ROOT / 'data')
    args = parser.parse_args()
    store = Store(args.data_dir)
    controller = Controller(store)
    try:
        server = LocalHTTPServer(('127.0.0.1', args.port), handler(store, controller, secrets.token_urlsafe(32)))
    except OSError:
        raise SystemExit(f'端口 {args.port} 已被占用或不可用。请关闭旧平台服务，或使用 --port 指定其他端口。') from None
    controller.stop_shortcut = StopShortcut(controller).start()
    print(f'GUI TestLab: http://127.0.0.1:{args.port}', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        if controller.active:
            controller.stop(controller.active)
        deadline = time.monotonic()+65
        while controller.active and time.monotonic() < deadline:
            time.sleep(.1)
    finally:
        controller.stop_shortcut.close()
        server.server_close()


if __name__ == '__main__':
    main()
