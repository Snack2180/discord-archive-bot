import tempfile
import unittest
from pathlib import Path
from discord_archive.models import Item, PublishReceipt
from discord_archive.store import Store
from discord_archive.indexing import index_history
from discord_archive.organize import process_job
from discord_archive.review import approve, authorize


class Gateway:
    allowed_channels = ['10', '20']
    inbox_id = '30'
    review_id = '40'
    delete_inbox = True
    def __init__(self):
        self.messages = {'10': [Item('1','1','10','Deleted User','','최소자승법')], '20': []}
        self.published = {}
        self.deleted = []
        self.valid = True
        self.fail_publish = False
    async def history(self, channel, after=''):
        for item in self.messages.get(channel, []):
            if not after or int(item.id) > int(after):
                yield item
    async def prepare(self, item):
        return item
    async def publish(self, item, channel, job_id):
        if self.fail_publish:
            raise RuntimeError('interrupted')
        result = PublishReceipt(['100'])
        self.published[job_id] = result
        return result
    async def preflight(self, item, channel):
        return None
    async def reconcile(self, job_id, channel):
        return self.published.get(job_id)
    async def verify(self, receipt, item):
        return self.valid
    async def delete_source(self, message_id):
        self.deleted.append(message_id)
    def archive_url(self, channel, message):
        return f'https://discord.com/channels/1/{channel}/{message}'


class WorkflowTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = Store(Path(self.tmp.name) / 'db')
        self.addCleanup(self.store.close)
        self.g = Gateway()

    async def test_existing_history_preserved_and_resumed(self):
        await index_history(self.g, self.store, ['10'])
        await index_history(self.g, self.store, ['10'])
        self.assertEqual(len(self.store.search('최소자승법', [])), 1)
        self.assertEqual(self.g.deleted, [])
        self.assertEqual(self.g.published, {})
        with self.assertRaises(ValueError):
            await index_history(self.g, self.store, ['99'])

    def ready(self):
        self.store.upsert_item(Item('2','2','30','owner','','코드'))
        self.store.create_job('2')
        self.store.update_job('2', state='ready', destination='10')

    async def test_verified_archive_then_delete_and_idempotent_retry(self):
        self.ready()
        self.assertEqual(await process_job('2', self.store, self.g), 'done')
        self.assertEqual(self.g.deleted, ['2'])
        self.assertTrue(self.store.get_item('2').archive_url.endswith('/100'))
        await process_job('2', self.store, self.g)
        self.assertEqual(self.g.deleted, ['2'])

    async def test_hash_failure_and_partial_publish_never_delete(self):
        self.ready()
        self.g.valid = False
        self.assertEqual(await process_job('2', self.store, self.g), 'review_pending')
        self.assertEqual(self.g.deleted, [])

    async def test_crash_after_publish_reconciles_without_duplicate(self):
        self.ready()
        self.store.update_job('2', state='publishing')
        self.g.published['2'] = PublishReceipt(['100'])
        self.g.fail_publish = True
        self.assertEqual(await process_job('2', self.store, self.g), 'done')
        self.assertEqual(self.g.deleted, ['2'])

    async def test_uncertain_publish_holds_original(self):
        self.ready()
        self.store.update_job('2', state='publishing')
        self.assertEqual(await process_job('2', self.store, self.g), 'review_pending')
        self.assertEqual(self.g.deleted, [])

    async def test_preflight_failure_can_be_corrected_and_approved(self):
        self.ready()
        async def oversized(item, channel):
            raise ValueError('upload too large')
        self.g.preflight = oversized
        self.assertEqual(await process_job('2', self.store, self.g), 'review_pending')
        self.assertEqual(self.g.published, {})
        self.assertEqual(self.g.deleted, [])
        async def corrected(item, channel):
            return None
        self.g.preflight = corrected
        self.assertTrue(approve(self.store, '2', '10', [], ['10', '20']))
        self.assertEqual(await process_job('2', self.store, self.g), 'done')

    async def test_old_history_can_never_be_deleted_by_job(self):
        self.ready()
        item = self.store.get_item('2')
        item.channel_id = '10'
        self.store.upsert_item(item)
        self.assertEqual(await process_job('2', self.store, self.g), 'review_pending')
        self.assertEqual(self.g.deleted, [])

    async def test_confirmation_is_owner_only_and_survives_restart(self):
        self.ready()
        self.store.update_job('2', state='review_pending')
        with self.assertRaises(PermissionError):
            authorize('other', '1', 'owner', '1')
        self.assertTrue(approve(self.store, '2', '10', ['Python'], ['10','20']))
        self.assertFalse(approve(self.store, '2', '20', [], ['10','20']))
        self.assertEqual(self.store.examples(), [('코드', '10')])
        self.assertEqual(await process_job('2', self.store, self.g), 'done')

