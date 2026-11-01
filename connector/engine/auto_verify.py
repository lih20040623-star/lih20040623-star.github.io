"""Compare PDF pages with native, pre-PDF raster captures and source inventories."""
import json
import shutil
import subprocess
from collections import deque

import numpy as np
from PIL import Image, ImageDraw, ImageFilter
from pypdf import PdfReader

from source_audit import audit
from verification import inspect
from comparison_policy import visual_policy, VISUAL_IGNORE_BELOW_RATIO, VISUAL_POLICY_NOTE

MAX_PAGES = 100
COLOR_TOLERANCE = 32
MIN_REGION_PIXELS = 12


def regions_for(mask):
    """Group nearby differences into visible, normalized page rectangles."""
    height, width = mask.shape
    cell = 24
    rows,columns=(height+cell-1)//cell,(width+cell-1)//cell
    padded=np.pad(mask,((0,rows*cell-height),(0,columns*cell-width)))
    occupied=set(zip(*np.nonzero(padded.reshape(rows,cell,columns,cell).any(axis=(1,3)))))
    regions = []
    while occupied:
        start = occupied.pop()
        queue, cells = deque([start]), [start]
        while queue:
            y, x = queue.popleft()
            for dy, dx in ((0,1),(0,-1),(1,0),(-1,0),(1,1),(1,-1),(-1,1),(-1,-1)):
                neighbor = (y+dy, x+dx)
                if neighbor in occupied:
                    occupied.remove(neighbor)
                    queue.append(neighbor)
                    cells.append(neighbor)
        pixel_count=0
        x0,y0,x1,y1=width,height,0,0
        for y, x in cells:
            yy, xx = np.nonzero(mask[y*cell:(y+1)*cell, x*cell:(x+1)*cell])
            pixel_count+=len(yy)
            if len(yy):
                x0=min(x0,int(xx.min())+x*cell);x1=max(x1,int(xx.max())+x*cell+1)
                y0=min(y0,int(yy.min())+y*cell);y1=max(y1,int(yy.max())+y*cell+1)
        if pixel_count >= MIN_REGION_PIXELS:
            regions.append({'x':x0/width,'y':y0/height,'width':(x1-x0)/width,'height':(y1-y0)/height,'pixels':pixel_count})
    return sorted(regions, key=lambda r:r['pixels'], reverse=True)


def compare_rasters(source_path, output_path, overlay_path):
    with Image.open(source_path) as ref, Image.open(output_path) as out:
        ref, out = ref.convert('RGB'), out.convert('RGB')
        common_width,common_height=min(ref.width,out.width),min(ref.height,out.height)
        if ref.size != out.size:
            if max(abs(ref.width-out.width),abs(ref.height-out.height)) <= 1:
                # Native raster exporters round point dimensions differently.
                # Pad <=1 px at the right/bottom; never stretch or crop artwork.
                size=(max(ref.width,out.width),max(ref.height,out.height))
                ref_pad,out_pad=Image.new('RGB',size,'white'),Image.new('RGB',size,'white')
                ref_pad.paste(ref,(0,0));out_pad.paste(out,(0,0));ref,out=ref_pad,out_pad
            else:
                overlay=out.copy();ImageDraw.Draw(overlay).rectangle((0,0,out.width-1,out.height-1),outline='red',width=3);overlay.save(overlay_path)
                return {'size_match':False,'changed_ratio':None,'regions':[],'region_count':0,'difference':True}
        a,b = np.asarray(ref).astype(np.int16), np.asarray(out).astype(np.int16)
        amin,amax = (np.asarray(ref.filter(f)).astype(np.int16) for f in (ImageFilter.MinFilter(3),ImageFilter.MaxFilter(3)))
        bmin,bmax = (np.asarray(out.filter(f)).astype(np.int16) for f in (ImageFilter.MinFilter(3),ImageFilter.MaxFilter(3)))
        # Bidirectional neighborhood comparison tolerates antialiasing / <=1 px
        # renderer shifts while still detecting a stroke missing in either view.
        mask = np.any((a < bmin-COLOR_TOLERANCE)|(a > bmax+COLOR_TOLERANCE)|(b < amin-COLOR_TOLERANCE)|(b > amax+COLOR_TOLERANCE), axis=2)
        # Extra rounding-edge pixels have no corresponding native pixel.
        # Exclude only that <=1 px boundary instead of reporting a white pad
        # against a colored background as missing artwork.
        mask[common_height:,:]=False
        mask[:,common_width:]=False
        regions = regions_for(mask)
        ratio=float(mask.mean())
        decision=visual_policy(ratio,bool(regions))
        overlay = out.copy()
        draw = ImageDraw.Draw(overlay)
        mark_color='#b88719' if decision['visual_difference_ignored'] else '#e53935'
        for index, region in enumerate(regions[:60]):
            x,y = region['x']*out.width,region['y']*out.height
            box=(max(0,x-3),max(0,y-3),min(out.width,x+region['width']*out.width+3),min(out.height,y+region['height']*out.height+3))
            draw.rectangle(box,outline=mark_color,width=2)
            draw.text((box[0]+2,box[1]+2),str(index+1),fill=mark_color)
        overlay.save(overlay_path)
        return {'size_match':True,'changed_ratio':ratio,'regions':regions[:60],'region_count':len(regions),**decision,'unchecked_rounding_border_pixels':int(mask.size-common_width*common_height),'source_blank':bool(np.all(a>=250)),'output_blank':bool(np.all(b>=250))}


def output_raster(pdf, preview, number, width, height, dpi=None):
    scale=['-r',str(dpi)] if dpi else ['-scale-to-x',str(width),'-scale-to-y',str(height)]
    subprocess.run(['pdftoppm','-f',str(number),'-l',str(number),*scale,'-singlefile','-png',str(pdf),str(preview)],check=True,capture_output=True,timeout=60)
    return preview.with_suffix('.png')


def verify_native(output, evidence, previews):
    reader = inspect(output)
    captured = evidence.get('source_pages', [])
    report = {'verdict':'review','summary':'源页面核对未完整执行','pages':len(reader.pages),'source_pages':evidence.get('pages'),'checks':[],'comparisons':[],'scope':'原软件中可见、可打印的页面内容','verification_mode':'native-raster','limitations':['自动比较使用原软件单独导出的页面 PNG 和 PDF 渲染结果，不需要手动提供参考 PDF。两条导出路径仍共享原软件，不能排除原软件打开时已发生的字体替换。','图像比较允许 1 像素邻域、RGB 通道差值 32；小于 12 个差异像素的孤立区域不作为异常。画布取整多出的 1 像素边界无对应源像素，不计入比较；预览保留完整，不拉伸图像。不能保证检测所有微小、低对比度差异。','不包含隐藏图层、画板外对象、不可打印内容及印刷色彩认证。源对象数与 PDF 对象数不可直接一一对应。']}
    report['visual_ignore_below_ratio']=VISUAL_IGNORE_BELOW_RATIO
    report['limitations'].insert(0,VISUAL_POLICY_NOTE)
    if not shutil.which('pdftoppm'):
        report['summary']='缺少 PDF 渲染工具，自动页面核对未执行'
        return report
    page_match = evidence.get('pages') == len(reader.pages)
    report['checks'].append({'name':'源页面与 PDF 页数','result':'通过' if page_match else '发现差异'})
    complete = bool(evidence.get('capture_complete')) and len(captured) == len(reader.pages) and len(reader.pages) <= MAX_PAGES
    difference = not page_match
    for entry in captured[:MAX_PAGES]:
        number = entry['page']
        if number < 1 or number > len(reader.pages):
            continue
        source = previews / entry.get('image', 'missing.png')
        if not source.is_file():
            complete=False
            continue
        page = reader.pages[number-1]
        size_match = abs(float(page.mediabox.width)-entry['width_points']) < .5 and abs(float(page.mediabox.height)-entry['height_points']) < .5 and page.rotation == 0
        rendered = output_raster(output,previews/f'output-{number}',number,entry['width'],entry['height'],entry.get('dpi'))
        result = compare_rasters(source,rendered,previews/f'diff-{number}.png')
        result.update(page=number,size_match=size_match and result['size_match'],text_check='文字与图形按页面图像比较',missing_characters=None,extra_characters=None,objects=len(entry.get('objects',[])),image_labels={'reference':'原软件页面','output':'导出 PDF','diff':'差异位置'},images={'reference':source.name,'output':rendered.name,'diff':f'diff-{number}.png'})
        result['difference'] = result['difference'] or not result['size_match']
        if result.get('visual_difference_ignored'):
            result['image_labels']['diff']='已忽略的视觉差异（保留记录）'
        for region in result['regions']:
            region['related_objects'] = []
            for obj in entry.get('objects', []):
                x,y,w,h = obj['box']
                if obj['type'] not in {7,'GroupItem'} and x < region['x']+region['width'] and x+w > region['x'] and y < region['y']+region['height'] and y+h > region['y']:
                    region['related_objects'].append({'type':obj['type'],'text':obj.get('text','')[:120],'font':obj.get('font','')})
            region['related_objects'] = region['related_objects'][:12]
        report['comparisons'].append(result)
        difference = difference or result['difference']
    report['limitations'].extend(evidence.get('capture_errors',[]))
    report['ignored_visual_pages']=sum(p.get('visual_difference_ignored',False) for p in report['comparisons'])
    if difference:
        report.update(verdict='difference',summary='自动核对发现疑似差异，已标记页面和位置')
    elif complete:
        if report['ignored_visual_pages']:
            report.update(verdict='within_tolerance',summary='按设置通过：各页视觉差异低于 2%，已忽略')
        else:
            report.update(verdict='auto_match',summary='自动核对通过：原软件可见页面与 PDF 在当前检测精度内一致')
    return report


def automatic_report(source, output, reference, previews):
    from verification import verify
    evidence_path=source.parent/'evidence.json'
    evidence=json.loads(evidence_path.read_text(encoding='utf-8-sig')) if evidence_path.exists() else {}
    if evidence.get('capture_kind') in {'coreldraw-raster','word-emf','illustrator-raster','powerpoint-raster'}:
        report=verify_native(output,evidence,previews)
        if reference.exists():
            external=verify(output,reference,previews/'external')
            report['external_reference']={'verdict':external['verdict'],'summary':external['summary']}
            if external['verdict']=='difference':
                report.update(verdict='difference',summary='独立参考 PDF 与导出结果存在差异')
    else:
        report=verify(output,reference,previews)
    try:
        source_result=audit(source,output)
    except Exception as exc:
        source_result={'status':'review','summary':'源清单检查未完成：'+str(exc)[:300]}
        if report['verdict'] in {'auto_match','within_tolerance'} and report.get('verification_mode')=='native-raster':
            report.update(verdict='review',summary='页面视觉一致，但源内容清单检查未完成')
    report['source_audit']=source_result
    if source_result['status']=='difference':
        report.update(verdict='difference',summary='源内容清单发现差异，请检查核对报告')
    elif source_result.get('data_integrity_match') and not reference.exists() and not evidence.get('capture_kind'):
        report.update(verdict='preserved',summary='内嵌 PDF 内容已原样保留；AI 私有画板和隐藏内容未核验',scope='AI 文件上次保存的 PDF 兼容内容',verification_mode='byte-preservation')
        report['checks'].append({'name':'源内嵌 PDF 与输出 SHA-256','result':'逐字节一致'})
    elif source_result['status']=='review' and report['verdict'] in {'auto_match','within_tolerance'} and report.get('verification_mode')=='native-raster':
        report.update(verdict='review',summary='页面视觉一致，源清单或关联资源尚未完整核验')
    return report
