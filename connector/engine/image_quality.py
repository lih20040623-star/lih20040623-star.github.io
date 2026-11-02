"""Keep original OOXML picture pixels in native-generated PDFs.

Native Office exporters may resample pictures despite their quality flags.
Match unambiguous source pictures, then embed their original pixel data with
lossless compression, preserving the PDF's placement, clipping and vector text.
"""
from collections import Counter
from io import BytesIO
from pathlib import Path
import hashlib
import zipfile
import zlib

import numpy as np
from PIL import Image
from pypdf import PdfReader, PdfWriter
from pypdf.generic import NameObject, NumberObject, ArrayObject, EncodedStreamObject


def _fingerprint(image):
    rgb = image.convert('RGBA')
    bg = Image.new('RGBA', rgb.size, 'white')
    bg.alpha_composite(rgb)
    return np.asarray(bg.convert('RGB').resize((96, 96), Image.Resampling.LANCZOS), dtype=np.float32) / 255


def _source_images(source, folder):
    payloads = []
    if zipfile.is_zipfile(source):
        with zipfile.ZipFile(source) as archive:
            for info in archive.infolist():
                if not info.is_dir() and info.filename.startswith(('word/media/', 'ppt/media/', 'xl/media/')):
                    payloads.append((info.filename, archive.read(info)))
    elif (folder / 'original-images').is_dir():
        payloads = [(p.name, p.read_bytes()) for p in (folder / 'original-images').iterdir() if p.is_file()]
    result = []
    seen = set()
    for name, data in payloads:
        try:
            image = Image.open(BytesIO(data))
            image.load()
        except Exception:
            continue  # EMF/SVG and other vector parts have no pixel resolution.
        if image.mode not in {'RGB', 'RGBA', 'CMYK', 'L', 'LA', 'P', '1'}:
            raise ValueError('IMAGE_QUALITY_UNSUPPORTED: Unsupported source picture mode: ' + image.mode)
        canonical = image.convert('RGBA')
        digest = hashlib.sha256(canonical.tobytes()).digest()
        key = (canonical.size, digest)
        if key in seen:
            continue
        seen.add(key)
        result.append(dict(name=name, image=image.copy(), fingerprint=_fingerprint(image), digest=digest))
    return result


def _stream(image, writer):
    alpha = None
    if image.mode in {'RGBA', 'LA'} or (image.mode == 'P' and 'transparency' in image.info):
        rgba = image.convert('RGBA')
        alpha = rgba.getchannel('A')
        image = rgba.convert('RGB')
    elif image.mode not in {'RGB', 'CMYK', 'L'}:
        image = image.convert('RGB' if image.mode == 'P' else 'L')
    color_space = {'RGB': '/DeviceRGB', 'CMYK': '/DeviceCMYK', 'L': '/DeviceGray'}[image.mode]
    obj = EncodedStreamObject()
    obj._data = zlib.compress(image.tobytes(), 6)
    obj.update({NameObject('/Type'): NameObject('/XObject'), NameObject('/Subtype'): NameObject('/Image'),
                NameObject('/Width'): NumberObject(image.width), NameObject('/Height'): NumberObject(image.height),
                NameObject('/BitsPerComponent'): NumberObject(8), NameObject('/ColorSpace'): NameObject(color_space),
                NameObject('/Filter'): NameObject('/FlateDecode')})
    profile = image.info.get('icc_profile')
    if profile:
        icc = EncodedStreamObject()
        icc._data = zlib.compress(profile)
        icc.update({NameObject('/Filter'): NameObject('/FlateDecode'), NameObject('/N'): NumberObject(len(image.getbands()))})
        obj[NameObject('/ColorSpace')] = ArrayObject([NameObject('/ICCBased'), writer._add_object(icc)])
    if alpha is not None and alpha.getextrema() != (255, 255):
        obj[NameObject('/SMask')] = writer._add_object(_stream(alpha, writer))
    return obj


def preserve_office_images(source, target):
    source, target = Path(source), Path(target)
    images = _source_images(source, target.parent)
    report = dict(policy='original-pixels-lossless', source_pictures=len(images), checked=0, restored=0, pictures=[])
    if not images:
        if any(page.images for page in PdfReader(target).pages):
            raise ValueError('IMAGE_QUALITY_UNVERIFIED: 无法取得源图片以核对分辨率，已停止导出。旧版 Office 文件请先另存为 DOCX、XLSX 或 PPTX 后重试。')
        return report
    reader = PdfReader(target)
    writer = PdfWriter()
    writer.clone_document_from_reader(reader)
    seen = set()
    for page in writer.pages:
        for picture in page.images:
            if picture.indirect_reference is None:
                raise ValueError('IMAGE_QUALITY_UNVERIFIED: 无法核对 PDF 内联图片的原始分辨率，已停止导出。')
            identity = picture.indirect_reference.idnum
            if identity in seen:
                continue
            seen.add(identity)
            raster = picture.image
            thumb = _fingerprint(raster)
            candidates = sorted(((float(np.mean(np.abs(thumb - item['fingerprint']))), i) for i, item in enumerate(images)))
            score, index = candidates[0]
            if score > .025:
                raise ValueError('IMAGE_QUALITY_UNVERIFIED: 有图片无法与源图核对，已停止导出，未交付降质结果。')
            item = images[index]
            digest = hashlib.sha256(raster.convert('RGBA').tobytes()).digest()
            exact = raster.size == item['image'].size and digest == item['digest']
            if not exact and len(candidates) > 1 and candidates[1][0] <= score * 1.3 + .001:
                raise ValueError('IMAGE_QUALITY_UNVERIFIED: 多张源图过于相似，无法可靠核对原图，已停止导出。')
            original_size = item['image'].size
            report['checked'] += 1
            report['pictures'].append(dict(source=item['name'], source_pixels=list(original_size),
                                           exported_pixels=list(raster.size), restored=not exact))
            if not exact:
                replacement = _stream(item['image'], writer)
                obj = picture.indirect_reference.get_object()
                obj.clear()
                obj.update(replacement)
                obj._data = replacement._data
                report['restored'] += 1
    if report['restored']:
        temporary = target.with_suffix('.quality.pdf')
        try:
            writer.write(temporary)
            # Read the actual stored pixels before accepting the repaired result.
            check = PdfReader(temporary)
            actual = Counter((im.image.size, hashlib.sha256(im.image.convert('RGBA').tobytes()).digest())
                             for page in check.pages for im in page.images)
            expected = {(item['image'].size, item['digest']) for item in images}
            if any(key not in expected for key in actual):
                raise ValueError('IMAGE_QUALITY_UNVERIFIED: 原图像素核对未通过，已停止导出。')
            temporary.replace(target)
        finally:
            temporary.unlink(missing_ok=True)
    return report
