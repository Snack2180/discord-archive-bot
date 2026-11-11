import tempfile
import unittest
from pathlib import Path

from discord_archive.models import Item
from discord_archive.store import Store
from discord_archive.tags import resolve_alias
from discord_archive.config import load_config


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = Store(Path(self.tmp.name) / 'archive.db')
        self.addCleanup(self.store.close)

    def test_korean_search_and_idempotent_index(self):
        item = Item('1', '1', '10', 'old', '2025-01-01', '최소자승법 Python 코드')
        self.store.upsert_item(item)
        self.store.upsert_item(item)
        self.assertEqual([x.id for x in self.store.search('최소자승법', [])], ['1'])
        self.assertEqual(self.store.search('" OR * --', []), [])

    def test_alias_and_all_tags_search(self):
        for i in ['1', '2']:
            self.store.upsert_item(Item(i, i, '10', 'old', '', '코드'))
        self.store.record_feedback('1', '10', ['Python', '수학'])
        self.store.record_feedback('2', '10', ['Python'])
        self.store.set_alias('파이썬', 'Python')
        self.assertEqual([x.id for x in self.store.search('', ['파이썬', '수학'])], ['1'])
        self.assertEqual(resolve_alias(' python ', {'python': 'Python'}), 'Python')
        self.assertNotEqual(resolve_alias('C++', {}), resolve_alias('C', {}))

    def test_feedback_and_backup(self):
        self.store.upsert_item(Item('1', '1', '10', 'old', '', '수학'))
        self.assertEqual(self.store.examples(), [])
        self.store.record_feedback('1', '20', ['수학'])
        self.assertEqual(self.store.examples(), [('수학', '20')])
        backup = Path(self.tmp.name) / 'backup.db'
        self.store.backup(backup)
        other = Store(backup)
        try:
            self.assertEqual(other.examples(), [('수학', '20')])
        finally:
            other.close()

    def test_state_transition_is_compare_and_swap(self):
        self.store.upsert_item(Item('1', '1', '10', 'old', '', 'x'))
        self.store.create_job('1')
        self.assertTrue(self.store.transition('1', 'received', 'extracted'))
        self.assertFalse(self.store.transition('1', 'received', 'done'))

    def test_missing_config_fails_without_revealing_token(self):
        path = Path(self.tmp.name) / 'config.toml'
        path.write_text('guild_id = "1"', encoding='utf-8')
        with self.assertRaises(ValueError):
            load_config(path)


if __name__ == '__main__':
    unittest.main()
