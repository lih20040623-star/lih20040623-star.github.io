"""Keep user-facing PDFs together, preserving source names without overwrites."""
from pathlib import Path, PureWindowsPath
import re
import shutil

OUTPUT_FOLDER = 'PDF输出'


def pdf_filename(source_name):
    # Accept a basename even if legacy job metadata contains a Windows path.
    stem = PureWindowsPath(str(source_name)).stem
    stem = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', stem).strip().rstrip('. ')
    if not stem:
        stem = '未命名'
    if re.fullmatch(r'(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])', stem.split('.')[0], re.IGNORECASE):
        stem = '_' + stem
    return stem + '.pdf'


def save_pdf(source, source_name, root):
    """Return the new path; reserve the name atomically so existing PDFs survive."""
    root = Path(root).resolve()
    directory = root / OUTPUT_FOLDER
    directory.mkdir(exist_ok=True)
    info = directory.lstat()
    if directory.is_symlink() or getattr(info, 'st_file_attributes', 0) & 0x400 or directory.resolve() != directory:
        raise ValueError('PDF 输出文件夹不能是链接，请使用连接器目录中的普通文件夹。')
    filename = pdf_filename(source_name)
    stem = filename[:-4]
    for number in range(1, 10001):
        candidate = directory / (filename if number == 1 else f'{stem} ({number}).pdf')
        try:
            stream = candidate.open('xb')
        except FileExistsError:
            continue
        try:
            with stream, Path(source).open('rb') as input_stream:
                shutil.copyfileobj(input_stream, stream)
        except BaseException:
            candidate.unlink(missing_ok=True)
            raise
        return candidate
    raise ValueError('同名 PDF 数量过多，请整理 PDF输出 文件夹后重试。')

