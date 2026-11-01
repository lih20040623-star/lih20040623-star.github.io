"""Preserve an AI file's saved PDF representation without re-rendering it."""
import hashlib
import json
import re
import shutil

from pypdf import PdfReader
from pypdf.errors import PdfReadError


def pdf_representation(source):
    with source.open('rb') as stream:
        if not stream.read(8).startswith(b'%PDF-'):
            return None
    try:
        reader = PdfReader(str(source), strict=False)
        if reader.is_encrypted:
            raise ValueError('AI 中的 PDF 兼容内容已加密，请先在 Illustrator 中解除密码后再导出')
        if not reader.pages:
            return None
        # Non-PDF-compatible AI files can contain a valid one-page PDF warning.
        # A PDF header alone must never turn that placeholder into a success.
        text = re.sub(r'\s+', '', reader.pages[0].extract_text() or '').lower()
        if any(marker in text for marker in ('savedwithoutpdfcontent', 'savedwithoutpdf-compatible', 'savedwithoutpdfcompatible')):
            return None
        return len(reader.pages)
    except PdfReadError:
        return None


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def preserve_ai_pdf(source, target):
    pages = pdf_representation(source)
    if pages is None:
        return False
    shutil.copyfile(source, target)
    source_hash, output_hash = digest(source), digest(target)
    if source_hash != output_hash:
        raise RuntimeError('AI 内嵌 PDF 复制后校验失败，请重试')
    evidence = {'representation': 'pdf-compatible', 'embedded_pdf_pages': pages, 'source_sha256': source_hash, 'output_sha256': output_hash}
    (target.parent / 'evidence.json').write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding='utf-8')
    return True
