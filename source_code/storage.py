import json
from pathlib import Path
import sqlite3
import time
import uuid
from contextlib import contextmanager


class Store:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript('''PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS tasks(id TEXT PRIMARY KEY, created REAL, status TEXT, config TEXT, runtime TEXT);
            CREATE TABLE IF NOT EXISTS events(task TEXT, seq INTEGER, data TEXT, PRIMARY KEY(task,seq));
            CREATE TABLE IF NOT EXISTS notes(task TEXT, seq INTEGER, note TEXT, false_positive INTEGER, PRIMARY KEY(task,seq));''')

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.root / 'platform.sqlite', timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def directory(self, task):
        if not isinstance(task, str) or len(task) != 12 or any(c not in '0123456789abcdef' for c in task):
            raise ValueError('任务编号无效')
        return self.root / task

    def create(self, config):
        task = uuid.uuid4().hex[:12]
        (self.directory(task) / 'evidence').mkdir(parents=True)
        with self.connect() as db:
            db.execute('INSERT INTO tasks VALUES(?,?,?,?,?)', (task, time.time(), 'queued', json.dumps(config, ensure_ascii=False), '{}'))
        return task

    def update(self, task, status=None, **runtime):
        with self.connect() as db:
            row = db.execute('SELECT runtime,status FROM tasks WHERE id=?', (task,)).fetchone()
            if not row:
                raise KeyError(task)
            value = json.loads(row['runtime'])
            value.update(runtime)
            db.execute('UPDATE tasks SET status=?,runtime=? WHERE id=?',
                       (status or row['status'], json.dumps(value, ensure_ascii=False), task))

    def event(self, task, **data):
        with self.connect() as db:
            seq = db.execute('SELECT COALESCE(MAX(seq),0)+1 FROM events WHERE task=?', (task,)).fetchone()[0]
            data.update(seq=seq, time=time.time())
            db.execute('INSERT INTO events VALUES(?,?,?)', (task, seq, json.dumps(data, ensure_ascii=False)))
        return data

    def events(self, task, after=0, limit=100):
        with self.connect() as db:
            rows = db.execute('SELECT data FROM events WHERE task=? AND seq>? ORDER BY seq LIMIT ?',
                              (task, after, min(limit, 1000))).fetchall()
            notes = {r['seq']: dict(r) for r in db.execute('SELECT * FROM notes WHERE task=?', (task,))}
        result = [json.loads(row['data']) for row in rows]
        for item in result:
            item['review'] = notes.get(item['seq'])
        return result

    def task(self, task):
        with self.connect() as db:
            row = db.execute('SELECT * FROM tasks WHERE id=?', (task,)).fetchone()
            count = db.execute('SELECT COUNT(*) FROM events WHERE task=?', (task,)).fetchone()[0]
        if not row:
            raise KeyError(task)
        return dict(row) | {'config': json.loads(row['config']), 'runtime': json.loads(row['runtime']), 'event_count': count}

    def tasks(self):
        with self.connect() as db:
            ids = [r[0] for r in db.execute('SELECT id FROM tasks ORDER BY created DESC LIMIT 100')]
        return [self.task(task) for task in ids]

    def note(self, task, seq, note, false_positive):
        with self.connect() as db:
            if not db.execute('SELECT 1 FROM events WHERE task=? AND seq=?', (task, seq)).fetchone():
                raise ValueError('事件不存在')
            db.execute('INSERT OR REPLACE INTO notes VALUES(?,?,?,?)', (task, seq, str(note)[:2000], bool(false_positive)))
