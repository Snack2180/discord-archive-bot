import tempfile
import unittest
import importlib.util
from types import SimpleNamespace
from pathlib import Path
from discord_archive.models import Item, Attachment
from discord_archive.discord_gateway import archive_files, safe_filename
from discord_archive.discord_gateway import DiscordGateway
from discord_archive.config import Config
from discord_archive.store import Store


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


@unittest.skipUnless(importlib.util.find_spec('discord'), 'discord.py required')
class DiscordUploadTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_discord_file_upload_is_closed_on_success_and_failure(self):
        for fail in (False, True):
            with self.subTest(fail=fail), tempfile.TemporaryDirectory() as d:
                root = Path(d)
                store = Store(root / 'db')
                try:
                    cfg = Config('1','2','3','4',['5'], data_dir=root)
                    files = []
                    class Channel:
                        guild = SimpleNamespace(id=1, me=object(), filesize_limit=25*1024**2)
                        def permissions_for(self, member):
                            return SimpleNamespace(view_channel=True, send_messages=True, attach_files=True, read_message_history=True)
                        async def send(self, content, *, file, allowed_mentions):
                            files.append(file)
                            self.uploaded = file.fp.read()
                            if fail:
                                raise RuntimeError('synthetic send failure')
                            return SimpleNamespace(id=100)
                    channel = Channel()
                    gateway = DiscordGateway(SimpleNamespace(get_channel=lambda channel_id: channel), cfg, store)
                    item = Item('20','20','3','2','','original message')
                    store.upsert_item(item)
                    store.create_job(item.id)
                    if fail:
                        with self.assertRaisesRegex(RuntimeError, 'synthetic send failure'):
                            await gateway.publish(item, '5', item.id)
                    else:
                        receipt = await gateway.publish(item, '5', item.id)
                        self.assertEqual(receipt.message_ids, ['100'])
                        self.assertIn(b'original message', channel.uploaded)
                        self.assertIn('100', store.job(item.id)['receipt'])
                    self.assertEqual(len(files), 1)
                    self.assertTrue(files[0].fp.closed)
                finally:
                    store.close()

