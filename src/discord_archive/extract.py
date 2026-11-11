"""Read-only, bounded extraction. Optional engines fail into review, never success."""
from pathlib import Path
import subprocess
import sys
import json
import zipfile
from xml.etree import ElementTree
from .models import Extraction

TEXT_SUFFIXES = {'.txt', '.md', '.py', '.js', '.ts', '.tsx', '.jsx', '.java', '.c', '.cpp', '.h', '.cs', '.json', '.yaml', '.yml', '.toml', '.csv', '.html', '.css', '.sql', '.r', '.ipynb', '.log'}


def _read_document(path: Path) -> Extraction:
    suffix = path.suffix.lower()
    if suffix in TEXT_SUFFIXES:
        data = path.read_bytes()
        encodings = ('utf-16',) if data.startswith((b'\xff\xfe', b'\xfe\xff')) else ('utf-8-sig', 'cp949')
        for encoding in encodings:
            try:
                text = data.decode(encoding)
                if '\x00' not in text:
                    return Extraction(text.replace('\r\n', '\n').replace('\r', '\n'))
            except UnicodeError:
                continue
        return Extraction(status='review_required', reason='텍스트 인코딩을 읽지 못했습니다')
    if suffix == '.docx':
        with zipfile.ZipFile(path) as z:
            info = z.getinfo('word/document.xml')
            if info.file_size > 20 * 1024**2:
                raise ValueError('DOCX expanded content too large')
            xml = z.read(info)
        if b'<!DOCTYPE' in xml.upper() or b'<!ENTITY' in xml.upper():
            raise ValueError('Unsupported XML entities')
        root = ElementTree.fromstring(xml)
        ns = {'w': 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'}
        text = '\n'.join(''.join(p.itertext()) for p in root.findall('.//w:p', ns))
        return Extraction(text) if text.strip() else Extraction(status='review_required', reason='문서에 추출할 텍스트가 없습니다')
    if suffix == '.pdf':
        from pypdf import PdfReader
        reader = PdfReader(path)
        if reader.is_encrypted:
            raise ValueError('암호화된 PDF')
        if len(reader.pages) > 500:
            raise ValueError('PDF 500페이지 초과: 나눠서 올려주세요')
        parts = [page.extract_text() or '' for page in reader.pages]
        text = '\n'.join(parts)
        if not text.strip() or any(not s.strip() for s in parts):
            return Extraction(text, 'review_required', '스캔 또는 빈 PDF 페이지 포함: OCR 텍스트를 첨부해주세요')
        return Extraction(text)
    if suffix in {'.png', '.jpg', '.jpeg', '.webp', '.bmp', '.tif', '.tiff'}:
        from PIL import Image
        import pytesseract
        with Image.open(path) as image:
            if image.width * image.height > 40_000_000:
                raise ValueError('Image too large')
            text = pytesseract.image_to_string(image, lang='kor+eng', timeout=45)
        return Extraction(text) if text.strip() else Extraction(status='review_required', reason='사진에서 글자를 찾지 못했습니다')
    return Extraction(status='review_required', reason='미지원 형식: 파일명·메모로 확인 필요')


def extract_file(path: Path) -> Extraction:
    if not path.is_file() or path.stat().st_size > 25 * 1024**2:
        return Extraction(status='review_required', reason='파일이 없거나 추출 크기 제한 초과')
    try:
        if path.suffix.lower() in TEXT_SUFFIXES | {'.docx'}:
            return _read_document(path)
        # Isolate third-party parsers, including memory-hungry PDF/OCR, with a deadline.
        proc = subprocess.run([sys.executable, '-m', 'discord_archive.extract', str(path.resolve())],
                              capture_output=True, text=True, encoding='utf-8', timeout=60)
        if proc.returncode:
            raise ValueError('추출 엔진 오류 또는 미설치')
        return Extraction(**json.loads(proc.stdout))
    except Exception as exc:
        return Extraction(status='review_required', reason=f'추출 실패 ({type(exc).__name__}): 형식·암호·선택 엔진 설치를 확인해주세요')


if __name__ == '__main__':
    from dataclasses import asdict
    try:
        result = _read_document(Path(sys.argv[1]))
    except Exception:
        result = Extraction(status='review_required', reason='추출 엔진 미설치 또는 문서 읽기 실패')
    print(json.dumps(asdict(result), ensure_ascii=True))
