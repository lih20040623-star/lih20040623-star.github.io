"""Authenticated loopback companion for the public PDFSwitch website."""
import argparse
import hmac
import html
import json
import mimetypes
import os
from pathlib import Path
import secrets
import socket
import sys
import threading
import time
from urllib.parse import urlsplit
import webbrowser

SITE_URL = 'https://lih20040623-star.github.io/'
VERSION = '1.1.0'
PORT = 18765
FROZEN = bool(getattr(sys, 'frozen', False))
BUNDLE = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parent))
ENGINE = BUNDLE / 'engine'
WEB = (BUNDLE / 'site') if FROZEN else Path(__file__).resolve().parents[1] / 'site'
sys.path.insert(0, str(ENGINE))


class Pairing:
    def __init__(self):
        self.code = f'{secrets.randbelow(1000000):06d}'
        self.created = time.monotonic()
        self.token = secrets.token_urlsafe(32)
        self.failures = []
        self.lock = threading.Lock()

    def refresh(self):
        with self.lock:
            if time.monotonic() - self.created > 600:
                self.code = f'{secrets.randbelow(1000000):06d}'
                self.created = time.monotonic()
            return self.code

    def pair(self, code):
        with self.lock:
            now = time.monotonic()
            self.failures = [stamp for stamp in self.failures if now - stamp < 300]
            if len(self.failures) >= 5:
                return 429, {'error': '配对尝试过多，请 5 分钟后再试。'}
            if now - self.created > 600 or not hmac.compare_digest(str(code), self.code):
                self.failures.append(now)
                return 403, {'error': '配对码不正确或已过期。请打开本机连接器页面获取配对码。'}
            self.code = f'{secrets.randbelow(1000000):06d}'
            self.created = now
            self.failures.clear()
            return 200, {'token': self.token, 'application': 'PDFSwitch Connector'}


def make_handler(app, pairing, site_url=SITE_URL, dev_origin=None, port=PORT):
    site = urlsplit(site_url)
    allowed = {f'{site.scheme}://{site.netloc}', f'http://127.0.0.1:{port}'}
    if dev_origin:
        allowed.add(dev_origin)

    class Handler(app.Handler):
        def valid_host(self):
            return self.headers.get('Host') == f'127.0.0.1:{port}'

        def valid_origin(self):
            origin = self.headers.get('Origin')
            return origin is None or origin in allowed

        def authorized(self):
            supplied = self.headers.get('Authorization', '')
            return hmac.compare_digest(supplied, 'Bearer ' + pairing.token)

        def end_headers(self):
            origin = self.headers.get('Origin')
            if origin in allowed:
                self.send_header('Access-Control-Allow-Origin', origin)
                self.send_header('Vary', 'Origin')
                self.send_header('Access-Control-Allow-Private-Network', 'true')
            self.send_header('X-Frame-Options', 'DENY')
            self.send_header('Referrer-Policy', 'no-referrer')
            super().end_headers()

        def guard(self):
            if not self.valid_host() or not self.valid_origin():
                self.send(403, {'error': '不允许此网站访问本机连接器。'})
                return False
            if not self.authorized():
                self.send(401, {'error': '请先输入本机连接器的配对码。'})
                return False
            return True

        def do_OPTIONS(self):
            if not self.valid_host() or not self.valid_origin():
                return self.send(403, {'error': '来源不允许'})
            self.send_response(204)
            self.send_header('Access-Control-Allow-Methods', 'GET, POST, DELETE, OPTIONS')
            self.send_header('Access-Control-Allow-Headers', 'Authorization, Content-Type')
            self.send_header('Access-Control-Max-Age', '600')
            self.send_header('Content-Length', '0')
            self.end_headers()

        def do_GET(self):
            if not self.valid_host() or not self.valid_origin():
                return self.send(403, {'error': '来源不允许'})
            route = urlsplit(self.path).path
            if route.startswith(('/api/', '/files/')):
                if not self.guard():
                    return
                if route == '/api/health':
                    return self.send(200, {'application': 'PDFSwitch Connector', 'status': 'ok',
                                           'version': VERSION, 'output_directory': str(app.ROOT / 'PDF输出')})
                if route == '/api/jobs':
                    with app.LOCK:
                        jobs = sorted(app.JOBS.values(), key=lambda job: job['created'], reverse=True)
                        # Reports can contain local paths; these remain between the browser and this PC.
                        return self.send(200, jobs)
                return super().do_GET()
            if route == '/connect':
                # Never expose the pairing code to cross-origin fetches, images, or frames.
                if self.headers.get('Origin') or self.headers.get('Sec-Fetch-Mode') != 'navigate' or self.headers.get('Sec-Fetch-Dest') != 'document':
                    return self.send(403, {'error': '请在浏览器新标签页中打开本机连接器页面。'})
                code = pairing.refresh()
                cloud = html.escape(site_url + '#pair=' + code)
                local = '/#pair=' + code
                body = f'''<!doctype html><html lang="zh-CN"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>PDFSwitch 本机连接器</title>
<style>body{{font:16px "Microsoft YaHei",sans-serif;background:#f5f7f5;color:#24352f;margin:0;display:grid;place-items:center;min-height:100vh}}main{{max-width:480px;padding:40px;border-radius:18px;background:white;border:1px solid #dde6df}}h1{{font-size:24px}}.code{{font-size:44px;letter-spacing:12px;color:#1d654b;font-weight:bold}}p{{line-height:1.8}}a{{display:inline-block;color:#1d654b;margin:8px 15px 8px 0}}small{{color:#748277}}</style>
<main><h1>本机连接器已就绪</h1><p>在 PDFSwitch 网站输入下方配对码，或点击按钮带回网站。</p>
<p class="code">{code}</p><p><a href="{cloud}">回到网站并配对 ↗</a><a href="{local}">使用本机网页 ↗</a></p>
<small>配对码 10 分钟有效，使用后更新。文件只在这台电脑上处理。关闭连接器窗口后服务停止。</small></main></html>'''.encode('utf-8')
                self.send_response(200)
                self.send_header('Content-Type', 'text/html; charset=utf-8')
                self.send_header('Cache-Control', 'no-store')
                self.send_header('Content-Security-Policy', "frame-ancestors 'none'; default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'")
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                return self.wfile.write(body)
            relative = 'index.html' if route == '/' else route.lstrip('/')
            file = (WEB / relative).resolve()
            if not file.is_relative_to(WEB.resolve()) or not file.is_file():
                return self.send(404, {'error': '文件不存在'})
            self.send_response(200)
            self.send_header('Content-Type', mimetypes.guess_type(file)[0] or 'application/octet-stream')
            self.send_header('Content-Length', str(file.stat().st_size))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.end_headers()
            with file.open('rb') as stream:
                app.shutil.copyfileobj(stream, self.wfile)

        def do_POST(self):
            if not self.valid_host() or not self.valid_origin():
                return self.send(403, {'error': '来源不允许'})
            if urlsplit(self.path).path == '/api/pair':
                if self.headers.get('Content-Type', '').split(';')[0] != 'application/json':
                    return self.send(415, {'error': '配对请求格式不正确'})
                try:
                    length = int(self.headers.get('Content-Length', '0'))
                    if not 0 < length <= 1024:
                        raise ValueError()
                    payload = json.loads(self.rfile.read(length))
                    code = payload.get('code')
                    if not isinstance(code, str) or len(code) != 6 or not code.isascii() or not code.isdigit():
                        raise ValueError()
                except (ValueError, TypeError, AttributeError):
                    return self.send(400, {'error': '请输入六位数字配对码'})
                status, result = pairing.pair(code)
                return self.send(status, result)
            if not self.guard():
                return
            # The subclass already validated origin + bearer. Preserve the validated
            # Origin for CORS response headers while adapting the original local handler.
            self._dispatch_local(super().do_POST)

        def do_DELETE(self):
            if not self.guard():
                return
            self._dispatch_local(super().do_DELETE)

        def _dispatch_local(self, action):
            original = self.headers.get('Origin')
            if original:
                # Original code checks same-origin. A validated external request may use
                # an internal same-origin value, but end_headers restores CORS response.
                self.headers.replace_header('Origin', 'http://' + self.headers['Host'])
            self.response_origin = original
            try:
                action()
            finally:
                if original:
                    self.headers.replace_header('Origin', original)
                self.response_origin = None

        def send_response(self, code, message=None):
            # Restore the public Origin before any headers are emitted by the engine.
            original = getattr(self, 'response_origin', None)
            if original and self.headers.get('Origin') != original:
                self.headers.replace_header('Origin', original)
            super().send_response(code, message)

    return Handler


def main():
    parser = argparse.ArgumentParser(description='PDFSwitch 本地连接器')
    parser.add_argument('--no-browser', action='store_true')
    parser.add_argument('--data-root', type=Path)
    parser.add_argument('--dev-origin')
    parser.add_argument('--port', type=int, default=PORT, help='Loopback listening port (default: 18765)')
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        parser.error('--port must be between 1024 and 65535')
    port = args.port
    home = (args.data_root or (Path(sys.executable).parent if FROZEN else Path(__file__).resolve().parent)).resolve()
    if home.drive.upper() == 'C:':
        raise SystemExit('请将连接器解压到 D 盘后再运行，程序不会在 C 盘建立数据和缓存。')
    home.mkdir(parents=True, exist_ok=True)
    scratch = home / 'runtime-tmp'
    scratch.mkdir(exist_ok=True)
    os.environ.update(PDFSWITCH_HOME=str(home), TEMP=str(scratch), TMP=str(scratch), PYTHONDONTWRITEBYTECODE='1')
    font_cache = scratch / 'font-cache'
    font_cache.mkdir(exist_ok=True)
    font_config = scratch / 'fonts.conf'
    windows_fonts = Path(os.environ.get('WINDIR', r'C:\Windows')) / 'Fonts'
    font_config.write_text(
        '<?xml version="1.0"?><!DOCTYPE fontconfig SYSTEM "urn:fontconfig:fonts.dtd">'
        '<fontconfig><dir>' + html.escape(str(windows_fonts)) + '</dir><cachedir>'
        + html.escape(str(font_cache)) + '</cachedir></fontconfig>', encoding='utf-8')
    os.environ.update(FONTCONFIG_FILE=str(font_config), XDG_CACHE_HOME=str(scratch))
    poppler = BUNDLE / 'poppler'
    if poppler.is_dir():
        os.environ['PATH'] = str(poppler) + os.pathsep + os.environ.get('PATH', '')
    import app
    pairing = Pairing()
    from http.server import ThreadingHTTPServer
    class LocalServer(ThreadingHTTPServer):
        allow_reuse_address = False

        def server_bind(self):
            if hasattr(socket, 'SO_EXCLUSIVEADDRUSE'):
                self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            super().server_bind()
    try:
        server = LocalServer(('127.0.0.1', port), make_handler(app, pairing, dev_origin=args.dev_origin, port=port))
    except OSError:
        raise SystemExit(f'连接器端口 {port} 已占用。若连接器已运行，请打开 http://127.0.0.1:{port}/connect 获取配对码。')
    server.daemon_threads = True
    print('PDFSwitch Connector ' + VERSION, flush=True)
    print(f'Local connector: http://127.0.0.1:{port}/connect', flush=True)
    print(f'Pairing code (10 minutes): {pairing.code}', flush=True)
    print(f'Data directory: {home / "data"}', flush=True)
    print(f'PDF output directory: {home / "PDF输出"}', flush=True)
    print('Keep this window open. Press Ctrl+C to stop.', flush=True)
    if not args.no_browser:
        webbrowser.open(f'http://127.0.0.1:{port}/connect')
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print('Stopping connector...', flush=True)
    finally:
        server.server_close()
        app.POOL.shutdown(wait=True, cancel_futures=True)


if __name__ == '__main__':
    main()

