"""Limited source inventory, kept separate from reference-PDF verification."""
import json
import zipfile
from collections import Counter
from xml.etree import ElementTree as ET
from verification import normalized


def audit(source, output_pdf):
    evidence_path = source.parent / 'evidence.json'
    evidence = json.loads(evidence_path.read_text(encoding='utf-8-sig')) if evidence_path.exists() else {}
    if evidence.get('representation') == 'pdf-compatible':
        from ai_pdf import digest
        from pypdf import PdfReader
        byte_match = digest(source) == digest(output_pdf) == evidence['source_sha256'] == evidence['output_sha256']
        actual_pages=len(PdfReader(str(output_pdf)).pages)
        native=evidence.get('capture_kind')=='illustrator-raster'
        mismatch=native and evidence.get('pages')!=actual_pages
        dependencies=bool(evidence.get('missing_fonts') or evidence.get('missing_links'))
        status='difference' if not byte_match or mismatch else 'partial_match' if native and evidence.get('capture_complete') and not dependencies else 'review'
        notes=['原样保留 Illustrator 上次保存的 PDF 表现，不重新排版或栅格化。','原生画板预览与 PDF 单独比较；隐藏图层、画板外对象仍不在可打印页面范围内。'] if native else ['内嵌 PDF 不等于 AI 私有编辑数据；本次未取得原生画板信息。']
        if evidence.get('native_capture_error'):notes.append('原生画板采集失败：'+evidence['native_capture_error'][:300])
        if dependencies:notes.append('缺少字体或链接图片，源文件打开结果可能已经发生替换或遗漏。')
        return {'status':status,'summary':'原生画板数量与 PDF 页数不同' if mismatch else '源内嵌 PDF 与输出逐字节一致；已采集原生画板' if native else '源文件内嵌 PDF 与输出逐字节一致；AI 私有画板尚未核验','data_integrity_match':byte_match,'embedded_pdf_pages':evidence['embedded_pdf_pages'],'expected_pages':evidence.get('pages'),'actual_pages':actual_pages,'missing_fonts':evidence.get('missing_fonts',[]),'missing_links':evidence.get('missing_links',[]),'notes':notes}
    notes = []
    expected_text = evidence.get('text', '')
    if source.suffix == '.docx':
        with zipfile.ZipFile(source) as archive:
            names = [n for n in archive.namelist() if n == 'word/document.xml' or n.startswith(('word/header', 'word/footer')) and n.endswith('.xml')]
            if sum(archive.getinfo(n).file_size for n in names) > 20 * 1024 * 1024:
                return {'status': 'review', 'summary': '文档 XML 过大，未执行源文字清单检查'}
            ns = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'
            expected_text = ''.join(node.text or '' for n in names for node in ET.fromstring(archive.read(n)).iter(ns+'t'))
            notes.append('DOCX 检查正文、文本框及页眉页脚 XML 中的文字；不检查公式、图片内部文字和关联资源。隐藏/修订文字可能导致差异提示。')
    if not expected_text and 'pages' not in evidence:
        return {'status': 'review', 'summary': '此格式尚未取得源对象清单，需参考 PDF 或人工检查'}
    from pypdf import PdfReader
    reader = PdfReader(str(output_pdf))
    actual_text = ''.join(page.extract_text() or '' for page in reader.pages) if expected_text.strip() else ''
    expected = normalized(''.join(ch for ch in expected_text if ch.isprintable()))
    missing = Counter(expected) - Counter(normalized(actual_text))
    count_mismatch = 'pages' in evidence and evidence['pages'] != len(reader.pages)
    difference = bool(missing) or count_mismatch
    result={'status': 'difference' if difference else 'partial_match', 'summary': '源内容清单发现差异' if difference else '已采集的源内容项目未发现缺失', 'missing_characters': sum(missing.values()), 'missing_sample': ''.join(missing.keys())[:100], 'expected_pages': evidence.get('pages'), 'actual_pages': len(reader.pages), 'page_count_mismatch': count_mismatch, 'source_text_characters':len(expected), 'notes': notes + ['源文字检查使用字符计数，不验证顺序和坐标；无法替代独立视觉参考。']}
    if evidence.get('sheet_inventory'):
        result['sheets']=evidence['sheet_inventory']
        result['notes']+=evidence.get('capture_errors',[])+['表格只核对可见且在打印区域内的显示文字；图表、图片与分页仍需页面核对。']
        outside=sum(s.get('outside_print_area',0) for s in evidence['sheet_inventory'])
        if outside:
            result.update(status='difference',summary=f'发现 {outside} 个非空单元格位于打印区域外，导出范围可能不完整')
    return result
