import tempfile
import unittest
from pathlib import Path
from discord_archive.models import Item
from discord_archive.store import Store
from discord_archive.runtime import Runtime
from discord_archive.commands import search_results
from discord_archive.review import persistent_reviews
from test_workflow import Gateway


class RuntimeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = Store(Path(self.tmp.name) / 'db')
        self.addCleanup(self.store.close)
        self.g = Gateway()
        self.runtime = Runtime(self.store, self.g, Path(self.tmp.name))

    async def test_inbox_cold_start_waits_and_feedback_retrains(self):
        item = Item('2','2','30','owner','','Python 최소자승법')
        await self.runtime.ingest(item)
        self.assertEqual(self.store.job('2')['state'], 'review_pending')
        await self.runtime.ingest(item)
        self.assertEqual(self.store.examples(), [])
        await self.runtime.confirm('2', '10', ['Python', '수학'])
        self.assertEqual(self.store.job('2')['state'], 'done')
        self.assertTrue((Path(self.tmp.name) / 'models' / 'current.json').exists())
        results = search_results(self.store, '', ['수학', 'Python'], 1)
        self.assertIn('https://discord.com/channels/1/10/100', results)
        self.assertEqual(self.g.deleted, ['2'])

    async def test_poll_after_offline_includes_old_and_new_messages(self):
        self.g.messages['30'] = [Item('2','2','30','owner','','메모'), Item('3','3','30','owner','','코드')]
        await self.runtime.poll()
        await self.runtime.poll()
        self.assertEqual(len(self.store.jobs()), 2)
        self.assertEqual(self.store.setting('inbox_cursor'), '3')

    async def test_failed_send_cannot_be_approved_as_fresh_publish(self):
        await self.runtime.ingest(Item('2','2','30','owner','','코드'))
        self.g.fail_publish = True
        await self.runtime.confirm('2','10',[])
        with self.assertRaises(ValueError):
            await self.runtime.confirm('2','10',[])
        self.assertEqual(self.g.deleted, [])

    async def test_failed_alt_read_cannot_be_approved_and_deleted(self):
        async def failed_prepare(item):
            item.issues = ['ALT: 전사문 수집 실패']
            return item
        self.g.prepare = failed_prepare
        await self.runtime.ingest(Item('2','2','30','owner','','https://altalt.io/note/11111111-2222-3333-4444-555555555555'))
        with self.assertRaises(ValueError):
            await self.runtime.confirm('2','10',[])
        self.assertEqual(self.g.deleted, [])

    async def test_ready_job_review_buttons_restored_before_recovery(self):
        self.store.upsert_item(Item('2','2','30','owner','','자료'))
        self.store.create_job('2')
        self.store.update_job('2', state='ready', destination='10', review_message='999')
        self.assertEqual(persistent_reviews(self.store), [('2', '999')])
        self.g.valid = False
        await self.runtime.poll()
        self.assertEqual(self.store.job('2')['state'], 'review_pending')
        self.assertEqual(persistent_reviews(self.store), [('2', '999')])

    def test_restart_unlocks_only_known_presend_file_error(self):
        known = "게시 결과 불확실: 'File' object does not support the context manager protocol"
        for job_id, receipt, error in [('2','',known), ('3','', '게시 결과 불확실: timeout'), ('4','{"message_ids":["100"]}',known)]:
            self.store.upsert_item(Item(job_id,job_id,'30','owner','','메모'))
            self.store.create_job(job_id)
            self.store.update_job(job_id,state='review_pending',receipt=receipt,error=error)
        Runtime(self.store, self.g, Path(self.tmp.name))
        self.assertEqual(self.store.job('2')['error'], '')
        self.assertEqual(self.store.job('2')['state'], 'review_pending')
        self.assertEqual(self.store.job('3')['error'], '게시 결과 불확실: timeout')
        self.assertEqual(self.store.job('4')['error'], known)

