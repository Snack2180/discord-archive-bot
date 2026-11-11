import tempfile
import unittest
from pathlib import Path
from discord_archive.models import Item, Attachment
from discord_archive.discord_gateway import archive_files, safe_filename


class GatewayPayloadTests(unittest.TestCase):
    def test_full_original_body_and_attachments_have_stable_manifest(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            p = root / 'source.py'
            p.write_bytes(b'print(1)')
            item = Item('2','2','30','old','2025-01-01','A' * 6000, attachments=[Attachment('a','code.py',str(p))])
            files = archive_files(item, root)
            self.assertEqual(len(files), 2)
            self.assertIn('A' * 6000, files[-1][0].read_text(encoding='utf-8'))
            self.assertEqual(files[0][1], '000-code.py')
            self.assertEqual(len(files[0][2]), 64)
            self.assertNotIn('/', safe_filename('../../x.py'))
            self.assertNotIn('\\', safe_filename('..\\x.py'))

    def test_missing_file_is_not_archive_success(self):
        with tempfile.TemporaryDirectory() as d:
            item = Item('2','2','30','old','','x', attachments=[Attachment('a','file','missing')])
            with self.assertRaises((ValueError, FileNotFoundError)):
                archive_files(item, Path(d))

