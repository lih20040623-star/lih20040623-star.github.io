"""Reference comparison, never a claim of complete source fidelity."""
import shutil
import subprocess
import unicodedata
from collections import Counter
from pathlib import Path

import numpy as np
from PIL import Image
from pypdf import PdfReader
from comparison_policy import visual_policy, VISUAL_IGNORE_BELOW_RATIO, VISUAL_POLICY_NOTE

MAX_PAGES = 100


def normalized(text):
    return ''.join(unicodedata.normalize('NFKC', text).split())


def inspect(path):
    reader = PdfReader(str(path), strict=False)
    if reader.is_encrypted:
        raise ValueError('加密 PDF 无法核对，请先解除密码')
    if not reader.pages:
        raise ValueError('PDF 没有页面')
    return reader


def render(pdf, output, number):
    output.parent.mkdir(exist_ok=True, parents=True)
    subprocess.run(['pdftoppm', '-f', str(number), '-l', str(number), '-r', '120', '-scale-to', '2400', '-singlefile', '-png', str(pdf), str(output)], check=True, capture_output=True, timeout=60)
    return output.with_suffix('.png')


def verify(output, reference, previews):
    actual = inspect(output)
    report = dict(verdict='review', summary='PDF 已生成；缺少独立参考，需人工确认', pages=len(actual.pages), checks=[], comparisons=[], limitations=['核对只覆盖可见导出页面；不验证隐藏图层、隐藏工作表、画板外对象、外部资源是否本应存在。', '参考 PDF 必须由用户在原软件中确认完整；同一转换引擎生成的两个结果不能证明源文件完整。', '视觉比较在 120 DPI、最长边 2400 像素下进行，不能证明印刷色彩、矢量结构或微小细节一致。'])
    report['checks'].append({'name': 'PDF 可读取', 'result': '通过'})
    report['visual_ignore_below_ratio']=VISUAL_IGNORE_BELOW_RATIO
    report['limitations'].insert(0,VISUAL_POLICY_NOTE)
    if not reference.exists():
        return report
    baseline = inspect(reference)
    report['reference_pages'] = len(baseline.pages)
    difference = len(actual.pages) != len(baseline.pages)
    report['checks'].append({'name': '页数一致', 'result': '通过' if not difference else '发现差异'})
    rasterizer = shutil.which('pdftoppm')
    incomplete = not rasterizer or max(len(actual.pages), len(baseline.pages)) > MAX_PAGES
    if not rasterizer:
        report['limitations'].append('未安装 pdftoppm，视觉核对未执行。')
    if max(len(actual.pages), len(baseline.pages)) > MAX_PAGES:
        report['limitations'].append('超过 100 页；只检查前 100 页，不能判定整体通过。')
    for index in range(min(len(actual.pages), len(baseline.pages), MAX_PAGES)):
        a, b = actual.pages[index], baseline.pages[index]
        sizes = [float(a.mediabox.width), float(a.mediabox.height)]
        expected_sizes = [float(b.mediabox.width), float(b.mediabox.height)]
        size_match = all(abs(x-y) < .5 for x, y in zip(sizes, expected_sizes)) and a.rotation == b.rotation
        text_a, text_b = normalized(a.extract_text() or ''), normalized(b.extract_text() or '')
        missing = Counter(text_b) - Counter(text_a)
        extra = Counter(text_a) - Counter(text_b)
        text_match = not missing and not extra
        row = dict(page=index+1, size_match=size_match, missing_characters=sum(missing.values()), extra_characters=sum(extra.values()), text_check='字符数量一致' if text_match else '文字存在差异', changed_ratio=None)
        visual_match = False
        if rasterizer:
            ap = render(output, previews / f'output-{index+1}', index+1)
            bp = render(reference, previews / f'reference-{index+1}', index+1)
            with Image.open(ap) as ai, Image.open(bp) as bi:
                aa, bb = np.asarray(ai.convert('RGB')).astype(np.int16), np.asarray(bi.convert('RGB')).astype(np.int16)
                if aa.shape == bb.shape:
                    mask = np.max(np.abs(aa-bb), axis=2) > 24
                    ratio = float(mask.mean())
                    row['changed_ratio'] = ratio
                    row.update(visual_policy(ratio,bool(mask.any())))
                    visual_match = not row['difference']
                    overlay = aa.astype(np.uint8).copy()
                    overlay[mask] = [239, 68, 68]
                    Image.fromarray(overlay).save(previews / f'diff-{index+1}.png')
                row['images'] = {'output': f'output-{index+1}.png', 'reference': f'reference-{index+1}.png'}
                if aa.shape == bb.shape:
                    row['images']['diff'] = f'diff-{index+1}.png'
        row['difference'] = not size_match or not text_match or (bool(rasterizer) and not visual_match)
        difference = difference or row['difference']
        report['comparisons'].append(row)
    report['ignored_visual_pages']=sum(p.get('visual_difference_ignored',False) for p in report['comparisons'])
    if difference:
        report.update(verdict='difference', summary='发现差异，请检查标记页面')
    elif incomplete:
        report.update(verdict='review', summary='核对未完整执行，需人工确认')
    else:
        if report['ignored_visual_pages']:
            report.update(verdict='within_tolerance',summary='按设置通过：各页视觉差异低于 2%，已忽略；字符等检查通过')
        else:
            report.update(verdict='reference_match', summary='与参考 PDF 在当前检查范围内一致；源文件完整性仍需确认')
    return report
