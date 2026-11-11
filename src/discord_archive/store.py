import json
import sqlite3
from pathlib import Path
from .models import Item
from .tags import canonicalize


class Store:
    def __init__(self, db_path: Path):
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(db_path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript('''
        PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS items(id TEXT PRIMARY KEY, data TEXT NOT NULL, text TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS tags(item_id TEXT, tag TEXT, PRIMARY KEY(item_id,tag));
        CREATE TABLE IF NOT EXISTS aliases(alias TEXT PRIMARY KEY, target TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS feedback(item_id TEXT PRIMARY KEY, channel_id TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY, state TEXT NOT NULL, destination TEXT DEFAULT '', receipt TEXT DEFAULT '', review_message TEXT DEFAULT '', error TEXT DEFAULT '');
        CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);
        ''')

    def close(self):
        self.db.close()

    def upsert_item(self, item: Item):
        with self.db:
            self.db.execute('INSERT INTO items VALUES(?,?,?) ON CONFLICT(id) DO UPDATE SET data=excluded.data,text=excluded.text',
                            (item.id, json.dumps(item.as_dict(), ensure_ascii=False), item.text()))
            self.db.execute('DELETE FROM tags WHERE item_id=?', (item.id,))
            self.db.executemany('INSERT OR IGNORE INTO tags VALUES(?,?)', [(item.id, self.tag(t)) for t in item.tags])

    def get_item(self, item_id: str) -> Item:
        row = self.db.execute('SELECT data FROM items WHERE id=?', (item_id,)).fetchone()
        if not row:
            raise KeyError(item_id)
        return Item.from_dict(json.loads(row[0]))

    def has_item(self, item_id):
        return self.db.execute('SELECT 1 FROM items WHERE id=?', (item_id,)).fetchone() is not None

    def tag(self, value):
        key = canonicalize(value)
        row = self.db.execute('SELECT target FROM aliases WHERE alias=?', (key,)).fetchone()
        return row[0] if row else key

    def set_alias(self, alias, target):
        alias, target = canonicalize(alias), self.tag(target)
        with self.db:
            self.db.execute('INSERT OR REPLACE INTO aliases VALUES(?,?)', (alias, target))
            self.db.execute('INSERT OR IGNORE INTO tags SELECT item_id,? FROM tags WHERE tag=?', (target, alias))
            self.db.execute('DELETE FROM tags WHERE tag=? AND tag<>?', (alias, target))
            self.db.execute('UPDATE aliases SET target=? WHERE target=?', (target, alias))

    def record_feedback(self, item_id, channel_id, tags):
        item = self.get_item(item_id)
        item.tags, item.destination_id = list(dict.fromkeys(self.tag(t) for t in tags if t.strip())), channel_id
        self.upsert_item(item)
        with self.db:
            self.db.execute('INSERT OR REPLACE INTO feedback VALUES(?,?)', (item_id, channel_id))

    def examples(self):
        return [(r['text'], r['channel_id']) for r in self.db.execute('SELECT i.text,f.channel_id FROM items i JOIN feedback f ON i.id=f.item_id ORDER BY i.id')]

    def weak_examples(self):
        return [(Item.from_dict(json.loads(r['data'])).text(), Item.from_dict(json.loads(r['data'])).channel_id) for r in self.db.execute('SELECT data FROM items WHERE id NOT IN (SELECT id FROM jobs)')]

    def vocabulary(self):
        return [r[0] for r in self.db.execute('SELECT DISTINCT tag FROM tags ORDER BY tag')]

    def search(self, query='', tags=None, limit=10, offset=0):
        clauses, args = [], []
        for term in query.split():
            clauses.append("text LIKE ? ESCAPE '\\'")
            args.append('%' + term.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_') + '%')
        for tag in tags or []:
            clauses.append('id IN (SELECT item_id FROM tags WHERE tag=?)')
            args.append(self.tag(tag))
        sql = 'SELECT data FROM items' + (' WHERE ' + ' AND '.join(clauses) if clauses else '') + ' ORDER BY id DESC LIMIT ? OFFSET ?'
        return [Item.from_dict(json.loads(r[0])) for r in self.db.execute(sql, args + [min(max(limit, 1), 100), max(offset, 0)])]

    def create_job(self, item_id):
        with self.db:
            self.db.execute("INSERT OR IGNORE INTO jobs(id,state) VALUES(?,'received')", (item_id,))

    def job(self, job_id):
        row = self.db.execute('SELECT * FROM jobs WHERE id=?', (job_id,)).fetchone()
        return dict(row) if row else None

    def jobs(self, states=None):
        return [dict(r) for r in self.db.execute('SELECT * FROM jobs') if states is None or r['state'] in states]

    def transition(self, job_id, expected, next_state):
        with self.db:
            cur = self.db.execute('UPDATE jobs SET state=? WHERE id=? AND state=?', (next_state, job_id, expected))
        return cur.rowcount == 1

    def update_job(self, job_id, **values):
        if not set(values) <= {'state', 'destination', 'receipt', 'review_message', 'error'}:
            raise ValueError('Invalid job field')
        with self.db:
            self.db.execute('UPDATE jobs SET ' + ','.join(k + '=?' for k in values) + ' WHERE id=?', [*values.values(), job_id])

    def setting(self, key, default=''):
        row = self.db.execute('SELECT value FROM settings WHERE key=?', (key,)).fetchone()
        return row[0] if row else default

    def set_setting(self, key, value):
        with self.db:
            self.db.execute('INSERT OR REPLACE INTO settings VALUES(?,?)', (key, str(value)))

    def backup(self, path):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        target = sqlite3.connect(path)
        try:
            self.db.backup(target)
        finally:
            target.close()
