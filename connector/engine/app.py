"""Local PDF conversion workbench. Bind to loopback only: desktop engines need isolation."""
import json
import mimetypes
import os
import re
import shutil
import stat
import subprocess
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from email.parser import BytesParser
from email.policy import default
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from conversion import convert, capabilities
from verification import verify
from source_audit import audit
from auto_verify import automatic_report

ROOT = Path(os.environ.get('PDFSWITCH_HOME', Path(__file__).resolve().parent)).resolve()
DATA = ROOT / 'data'
DATA.mkdir(exist_ok=True)
POOL = ThreadPoolExecutor(max_workers=1)  # Desktop applications are serialized.
LOCK = threading.Lock()
JOBS = {}
ACTIVE_JOBS = set()
LIMIT = 100 * 1024 * 1024
EXTENSIONS = {'.doc', '.docx', '.wps', '.xls', '.xlsx', '.et', '.ppt', '.pptx', '.dps', '.ai', '.cdr', '.pdf'}


def persist(job):
    with LOCK:
        JOBS[job['id']] = job
        path = DATA / job['id'] / 'job.json'
        tmp = path.with_suffix('.tmp')
        tmp.write_text(json.dumps(job, ensure_ascii=False, indent=2), encoding='utf-8')
        tmp.replace(path)


def process(job):
    with LOCK:
        ACTIVE_JOBS.add(job['id'])
    try:
        _process(job)
    finally:
        with LOCK:
            ACTIVE_JOBS.discard(job['id'])


def _process(job):
    folder = DATA / job['id']
    try:
        job.update(status='converting', message='正在调用转换引擎')
        persist(job)
        engine = convert(folder / ('source' + job['extension']), folder / 'output.pdf', job['engine'])
        job.update(engine_used=engine, status='checking', message='正在检查 PDF 和参考页面')
        persist(job)
        report = automatic_report(folder / ('source' + job['extension']), folder / 'output.pdf', folder / 'reference.pdf', folder / 'previews')
        job.update(status='done', message=report['summary'], report=report)
    except Exception as exc:
        job.update(status='failed', message=str(exc)[:2000])
    persist(job)


def checked_task_directory(job_id):
    if not re.fullmatch(r'[0-9a-f]{32}', job_id):
        raise ValueError('任务标识无效')
    base = DATA.resolve()
    directory = DATA / job_id
    if not base.is_relative_to(ROOT.resolve()) or directory.resolve().parent != base:
        raise ValueError('任务目录超出项目数据范围')
    return directory


def hide_task(job_id):
    """Called under LOCK: persist a list-only removal without moving artifacts."""
    directory = checked_task_directory(job_id)
    if directory.exists():
        if not directory.is_dir():
            raise ValueError('任务目录异常')
        info = directory.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise ValueError('任务目录不能是链接')
        archived = dict(JOBS[job_id], web_deleted=True, web_deleted_at=time.time())
        temporary = directory / ('.job-' + uuid.uuid4().hex + '.tmp')
        with temporary.open('x', encoding='utf-8') as stream:
            json.dump(archived, stream, ensure_ascii=False, indent=2)
        temporary.replace(directory / 'job.json')
    JOBS.pop(job_id)


def load_jobs():
    visible = {}
    for stored in DATA.glob('*/job.json'):
        try:
            job = json.loads(stored.read_text(encoding='utf-8'))
            if job.get('web_deleted'):
                continue
            if job['status'] in {'queued', 'converting', 'checking'}:
                job.update(status='failed', message='服务已重启，请重新提交此任务')
            visible[job['id']] = job
        except (ValueError, KeyError):
            pass
    return visible


JOBS = load_jobs()


class Handler(BaseHTTPRequestHandler):
    def send(self, code, value):
        body = json.dumps(value, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        route = urlsplit(self.path).path
        if route == '/api/health':
            return self.send(200, {'application': 'PDFSwitch', 'status': 'ok', 'project': str(ROOT), 'pid': os.getpid()})
        if route == '/api/jobs':
            with LOCK:
                return self.send(200, sorted(JOBS.values(), key=lambda j: j['created'], reverse=True))
        if route == '/api/capabilities':
            return self.send(200, capabilities())
        if route.startswith('/files/'):
            parts = route.split('/')
            if len(parts) < 4 or parts[2] not in JOBS:
                return self.send(404, {'error': '文件不存在'})
            relative = '/'.join(parts[3:])
            # Only generated artifacts are exposed; never source documents.
            allowed = relative in {'output.pdf', 'reference.pdf', 'job.json'} or (relative.startswith('previews/') and relative.endswith('.png'))
            base = (DATA / parts[2]).resolve()
            file = (base / relative).resolve()
            if not allowed or not file.is_relative_to(base):
                return self.send(404, {'error': '文件不存在'})
        else:
            file = ROOT / 'web' / ('index.html' if route == '/' else route.lstrip('/'))
            if not file.resolve().is_relative_to((ROOT / 'web').resolve()):
                return self.send(404, {'error': '文件不存在'})
        if not file.is_file():
            return self.send(404, {'error': '文件不存在'})
        self.send_response(200)
        self.send_header('Content-Type', mimetypes.guess_type(file)[0] or 'application/octet-stream')
        self.send_header('Content-Length', str(file.stat().st_size))
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        with file.open('rb') as stream:
            shutil.copyfileobj(stream, self.wfile)

    def do_POST(self):
        # Reject cross-origin submissions to the local desktop automation service.
        origin = self.headers.get('Origin')
        if origin and origin != 'http://' + self.headers.get('Host', ''):
            return self.send(403, {'error': '不允许跨站提交'})
        route = urlsplit(self.path).path
        parts = route.strip('/').split('/')
        if len(parts) == 4 and parts[:2] == ['api', 'jobs'] and parts[3] == 'retry':
            with LOCK:
                job = JOBS.get(parts[2])
                if not job:
                    return self.send(404, {'error': '任务不存在'})
                if job['status'] not in {'failed', 'done'}:
                    return self.send(409, {'error': '任务正在处理，请稍候'})
                if sum(j['status'] in {'queued', 'converting', 'checking'} for j in JOBS.values()) >= 20:
                    return self.send(429, {'error': '队列已满，请稍后重试'})
                job.setdefault('attempts', []).append({'status': job['status'], 'message': job['message'], 'retried_at': time.time()})
                job.update(status='queued', message='等待重新转换')
                job.pop('report', None)
                job.pop('engine_used', None)
            persist(job)
            POOL.submit(process, job)
            return self.send(202, job)
        if route != '/api/jobs':
            return self.send(404, {'error': '接口不存在'})
        try:
            length = int(self.headers.get('Content-Length', 0))
            if not 0 < length <= LIMIT:
                return self.send(413, {'error': '每个任务上传总量不得超过 100 MB'})
            if sum(j['status'] in {'queued', 'converting', 'checking'} for j in JOBS.values()) >= 20:
                return self.send(429, {'error': '队列已满，请稍后重试'})
            kind = self.headers.get('Content-Type', '')
            if not kind.startswith('multipart/form-data;'):
                raise ValueError('请使用文件上传表单')
            message = BytesParser(policy=default).parsebytes(('Content-Type: ' + kind + '\r\n\r\n').encode() + self.rfile.read(length))
            fields = {p.get_param('name', header='content-disposition'): p for p in message.iter_parts()}
            source = fields.get('source')
            if source is None or not source.get_filename():
                raise ValueError('请选择源文件')
            name = source.get_filename().replace('\\', '/').split('/')[-1]
            extension = Path(name).suffix.lower()
            if extension not in EXTENSIONS:
                raise ValueError('暂不支持该格式；宏启用格式暂不接收')
            engine = fields['engine'].get_content() if 'engine' in fields else 'auto'
            if engine not in {'auto', 'office', 'wps', 'libreoffice'}:
                raise ValueError('无效转换引擎')
            reference = fields.get('reference')
            if reference is not None and reference.get_filename() and not reference.get_payload(decode=True).startswith(b'%PDF-'):
                raise ValueError('参考文件必须是 PDF')
            job = dict(id=uuid.uuid4().hex, name=name, extension=extension, engine=engine, status='queued', message='等待转换', created=time.time())
            folder = DATA / job['id']
            folder.mkdir()
            (folder / ('source' + extension)).write_bytes(source.get_payload(decode=True))
            if reference is not None and reference.get_filename():
                (folder / 'reference.pdf').write_bytes(reference.get_payload(decode=True))
            persist(job)
            POOL.submit(process, job)
            self.send(202, job)
        except (ValueError, KeyError, TypeError) as exc:
            self.send(400, {'error': str(exc)})

    def do_DELETE(self):
        origin = self.headers.get('Origin')
        if origin and origin != 'http://' + self.headers.get('Host', ''):
            return self.send(403, {'error': '不允许跨站提交'})
        parts = urlsplit(self.path).path.strip('/').split('/')
        if len(parts) != 3 or parts[:2] != ['api', 'jobs']:
            return self.send(404, {'error': '接口不存在'})
        job_id = parts[2]
        try:
            with LOCK:
                job = JOBS.get(job_id)
                if not job:
                    return self.send(404, {'error': '任务不存在或已删除'})
                if job['status'] not in {'done', 'failed'} or job_id in ACTIVE_JOBS:
                    return self.send(409, {'error': '任务正在处理，完成后才能删除'})
                hide_task(job_id)
        except ValueError as exc:
            return self.send(400, {'error': str(exc)})
        except OSError:
            return self.send(409, {'error': '任务记录暂时无法保存，请稍后重试'})
        return self.send(200, {'deleted': job_id, 'files_preserved': True,
                               'message': '任务已从网页移除，原任务文件夹及文件已保留'})


if __name__ == '__main__':
    port = int(os.environ.get('PORT', '8765'))
    server = ThreadingHTTPServer(('127.0.0.1', port), Handler)
    print(f'PDFSwitch: http://127.0.0.1:{port}', flush=True)
    server.serve_forever()
