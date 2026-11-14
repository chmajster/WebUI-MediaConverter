from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from threading import RLock
from .models import AppSettings


class Store:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.lock = RLock()
        with self.db:
            self.db.executescript('''
            CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS presets (name TEXT PRIMARY KEY, data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, data TEXT NOT NULL, created TEXT NOT NULL);
            ''')
        # A subprocess cannot survive a clean application restart as a managed job.
        for job in self.jobs():
            if job['status'] in ('waiting', 'running'):
                job['status'] = 'interrupted'
                job['error'] = 'Zadanie przerwano po zamknięciu aplikacji. Możesz je ponowić.'
                self.save_job(job)

    def settings(self) -> AppSettings:
        with self.lock:
            row = self.db.execute("SELECT value FROM settings WHERE key='app'").fetchone()
        try:
            return AppSettings.model_validate_json(row[0]) if row else AppSettings()
        except ValueError:
            return AppSettings()

    def save_settings(self, value: AppSettings):
        with self.lock, self.db:
            self.db.execute("INSERT OR REPLACE INTO settings VALUES ('app', ?)", (value.model_dump_json(),))

    def presets(self) -> dict[str, dict]:
        with self.lock:
            return {r['name']: json.loads(r['data']) for r in self.db.execute('SELECT * FROM presets ORDER BY name')}

    def save_preset(self, name: str, data: dict):
        if not name.strip() or len(name) > 80:
            raise ValueError('Nazwa profilu musi mieć od 1 do 80 znaków.')
        with self.lock, self.db:
            self.db.execute('INSERT OR REPLACE INTO presets VALUES (?, ?)', (name.strip(), json.dumps(data, ensure_ascii=False)))

    def delete_preset(self, name: str):
        with self.lock, self.db:
            self.db.execute('DELETE FROM presets WHERE name=?', (name,))

    def save_job(self, job: dict):
        with self.lock, self.db:
            self.db.execute('INSERT OR REPLACE INTO jobs VALUES (?, ?, ?)',
                            (job['id'], json.dumps(job, ensure_ascii=False), job['created']))

    def jobs(self) -> list[dict]:
        with self.lock:
            return [json.loads(r['data']) for r in self.db.execute('SELECT data FROM jobs ORDER BY created DESC')]

    def delete_jobs(self, ids: list[str]):
        with self.lock, self.db:
            self.db.executemany('DELETE FROM jobs WHERE id=?', [(i,) for i in ids])
