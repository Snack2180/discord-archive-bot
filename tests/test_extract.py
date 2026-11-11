import tempfile
import unittest
import zipfile
from pathlib import Path
from discord_archive.extract import extract_file


class ExtractTests(unittest.TestCase):
    def test_cp949_korean_is_not_misdetected_as_utf16(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'legacy.txt'
            p.write_bytes('수학'.encode('cp949'))
            self.assertEqual(extract_file(p).text, '수학')

    def test_bom_utf16_korean(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'unicode.txt'
            p.write_bytes('수학 파이썬'.encode('utf-16'))
            self.assertEqual(extract_file(p).text, '수학 파이썬')

    def test_text_and_code_preserve_original(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'sample.py'
            p.write_text('print("한글")\n', encoding='utf-8')
            self.assertEqual(extract_file(p).text, 'print("한글")\n')
            self.assertEqual(p.read_text(encoding='utf-8'), 'print("한글")\n')

    def test_docx_extracts_paragraphs_and_tables(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'sample.docx'
            with zipfile.ZipFile(p, 'w') as z:
                z.writestr('word/document.xml', '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>최소자승법</w:t></w:r></w:p></w:body></w:document>')
            self.assertEqual(extract_file(p).text.strip(), '최소자승법')

    def test_failures_preserve_files(self):
        with tempfile.TemporaryDirectory() as d:
            for name, data in [('bad.docx', b'broken'), ('old.hwp', b'unknown'), ('audio.mp3', b'audio'), ('scan.png', b'broken'), ('locked.pdf', b'%PDF-invalid')]:
                p = Path(d) / name
                p.write_bytes(data)
                result = extract_file(p)
                self.assertEqual(result.status, 'review_required')
                self.assertTrue(result.reason)
                self.assertEqual(p.read_bytes(), data)

