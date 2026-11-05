"""User-facing startup: reuse a healthy instance, or select a free local port."""
import argparse
import json
import socket
import sys
import threading
import time
import urllib.request
import webbrowser


def running_platform(port):
    try:
        with urllib.request.urlopen(f'http://127.0.0.1:{port}/api/session', timeout=.6) as response:
            data = json.loads(response.read(65536))
        if (isinstance(data, dict) and str(data.get('build', '')).startswith('background-controls-')
                and data.get('demo') == 'source_code/tested_software/notepad++/notepad++.exe'):
            return data
    except (OSError, ValueError):
        pass
    return None


def choose_port(preferred, count=10):
    for port in range(preferred, min(65536, preferred+count)):
        if running_platform(port):
            return port, True
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as candidate:
                if sys.platform == 'win32':
                    candidate.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
                candidate.bind(('127.0.0.1', port))
            return port, False
        except OSError:
            continue
    raise ValueError('没有找到可用本地端口，请关闭旧服务或使用 --port 指定其他端口。')


def main(argv=None):
    parser = argparse.ArgumentParser(description='GUI TestLab startup')
    parser.add_argument('--port', type=int, default=8877)
    parser.add_argument('--no-browser', action='store_true')
    args = parser.parse_args(argv)
    if not 1024 <= args.port <= 65535:
        raise SystemExit('端口需在 1024～65535 之间')
    port, reuse = choose_port(args.port)
    url = f'http://127.0.0.1:{port}'
    if reuse:
        print('平台已经运行，连接已有服务：'+url, flush=True)
        if not args.no_browser:
            webbrowser.open(url, new=2)
        return
    print('启动平台：'+url, flush=True)
    if port != args.port:
        print(f'原端口 {args.port} 被其他程序占用，已改用 {port}。', flush=True)
    if not args.no_browser:
        def open_when_ready():
            for _ in range(50):
                if running_platform(port):
                    webbrowser.open(url, new=2)
                    return
                time.sleep(.2)
        threading.Thread(target=open_when_ready, daemon=True).start()
    import server
    sys.argv = [sys.argv[0], '--port', str(port)]
    server.main()


if __name__ == '__main__':
    main()
