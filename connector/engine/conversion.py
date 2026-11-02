import os
import shutil
import subprocess
import re
import json
from pathlib import Path

from ai_pdf import preserve_ai_pdf
from image_quality import preserve_office_images

ROOT = Path(__file__).resolve().parent


def find_soffice():
    candidates = [os.environ.get('SOFFICE_PATH'), shutil.which('soffice'), r'C:\Program Files\LibreOffice\program\soffice.exe']
    return next((p for p in candidates if p and Path(p).is_file()), None)


def capabilities():
    native = {}
    details = {}
    if os.name == 'nt':
        import winreg
        for name, progid in {'Word': 'Word.Application', 'Excel': 'Excel.Application', 'PowerPoint': 'PowerPoint.Application', 'WPS文字': 'KWPS.Application', 'WPS表格': 'KET.Application', 'WPS演示': 'KWPP.Application', 'Illustrator': 'Illustrator.Application', 'CorelDRAW': 'CorelDRAW.Application'}.items():
            try:
                with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, progid + r'\CLSID') as key:
                    clsid = winreg.QueryValue(key, None)
                with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, 'CLSID\\' + clsid + r'\LocalServer32') as key:
                    command = winreg.QueryValue(key, None)
                executable = registered_executable(command)
                native[name] = bool(executable and Path(executable).is_file())
                details[name] = '已检测到' if native[name] else '注册路径失效'
            except OSError:
                native[name] = False
                details[name] = '未检测到'
    return {'native': native, 'native_details': details, 'ai_pdf_compatible': True, 'libreoffice': bool(find_soffice()), 'rasterizer': bool(shutil.which('pdftoppm')), 'note': '检测到注册信息不代表软件已授权或转换已通过实测。'}


def registered_executable(command):
    match = re.match(r'^\s*(?:"([^"]+\.exe)"|(.+?\.exe)(?:\s|$))', command, re.IGNORECASE)
    return os.path.expandvars(match.group(1) or match.group(2)) if match else None


def convert(source, target, engine='auto'):
    ext = source.suffix.lower()
    if ext == '.pdf':
        shutil.copyfile(source, target)
        return 'PDF 原文件（未转换）'
    preserved = ext == '.ai' and preserve_ai_pdf(source, target)
    saved_evidence = json.loads((target.parent/'evidence.json').read_text(encoding='utf-8')) if preserved else None
    if preserved and not capabilities()['native'].get('Illustrator'):
        return 'AI 内嵌 PDF（原数据保留；原生画板接口不可用）'
    if engine == 'libreoffice':
        raise ValueError('严格字体与原图质量模式不允许 LibreOffice 兼容替换导出。请选择已安装原字体的原生软件；不会自动替换字体或降低分辨率。')
    if os.name != 'nt':
        raise ValueError('原生引擎需要 Windows 和对应桌面软件')
    # No silent fallback: a failure in the selected native renderer is actionable.
    request = target.parent / 'request.json'
    request.write_text(json.dumps(dict(source=str(source), target=str(target), engine=engine, keep_pdf=bool(preserved)), ensure_ascii=False), encoding='utf-8')
    try:
        result = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-STA', '-ExecutionPolicy', 'Bypass', '-File', str(ROOT / 'scripts' / 'export.ps1'), '-RequestPath', str(request)], capture_output=True, timeout=180)
    except subprocess.TimeoutExpired as exc:
        if preserved:
            saved_evidence['native_capture_error']='Illustrator 原生画板采集超时'
            (target.parent/'evidence.json').write_text(json.dumps(saved_evidence,ensure_ascii=False),encoding='utf-8')
            return 'AI 内嵌 PDF（原生核对未完成）'
        raise RuntimeError('原生软件导出超过 180 秒；请检查软件弹窗。任务未通过。') from exc
    if result.returncode or not target.is_file():
        error = result.stderr.decode('utf-8', errors='replace').strip()
        (target.parent / 'native-error.log').write_text(error, encoding='utf-8')
        if preserved:
            saved_evidence['native_capture_error']=error[-1500:]
            (target.parent/'evidence.json').write_text(json.dumps(saved_evidence,ensure_ascii=False),encoding='utf-8')
            return 'AI 内嵌 PDF（原生核对未完成）'
        if ext == '.ai' and ('80040154' in error.lower() or 'class not registered' in error.lower()):
            raise RuntimeError('此 AI 未包含可用的 PDF 兼容内容，且 Illustrator 自动化注册已失效。请修复 Illustrator 安装注册，或在原软件勾选“创建 PDF 兼容文件”后重新保存。详细错误已记录。')
        raise RuntimeError('原生导出失败。请确认软件已安装、已授权并能正常打开文件。' + error[-1200:])
    if preserved:
        native_evidence=json.loads((target.parent/'evidence.json').read_text(encoding='utf-8-sig'))
        native_evidence.update(saved_evidence)
        (target.parent/'evidence.json').write_text(json.dumps(native_evidence,ensure_ascii=False,indent=2),encoding='utf-8')
        return 'AI 原样保留 + Illustrator 原生画板核对'
    if ext in {'.doc', '.docx', '.wps', '.xls', '.xlsx', '.et', '.ppt', '.pptx', '.dps'}:
        try:
            quality = preserve_office_images(source, target)
        except Exception:
            target.unlink(missing_ok=True)
            raise
        evidence_path = target.parent / 'evidence.json'
        evidence = json.loads(evidence_path.read_text(encoding='utf-8-sig')) if evidence_path.is_file() else {}
        evidence['image_quality'] = quality
        evidence_path.write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding='utf-8')
    return result.stdout.decode('utf-8', errors='replace').strip() or '原生引擎'
