"""SQLite persistence. Transactions are short; network calls never hold a DB lock."""
from __future__ import annotations
import contextlib
import json
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any


def uid() -> str:
    return uuid.uuid4().hex


def dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS projects (
 id TEXT PRIMARY KEY, title TEXT NOT NULL, config TEXT NOT NULL,
 status TEXT NOT NULL DEFAULT 'draft', stage TEXT NOT NULL DEFAULT '准备',
 created REAL NOT NULL, updated REAL NOT NULL, error TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS sources (
 id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
 url TEXT NOT NULL, title TEXT NOT NULL, text TEXT NOT NULL DEFAULT '',
 digest TEXT NOT NULL DEFAULT '', status TEXT NOT NULL, meta TEXT NOT NULL,
 UNIQUE(project_id,url)
);
CREATE TABLE IF NOT EXISTS artifacts (
 project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
 key TEXT NOT NULL, value TEXT NOT NULL, created REAL NOT NULL,
 PRIMARY KEY(project_id,key)
);
CREATE TABLE IF NOT EXISTS jobs (
 id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
 status TEXT NOT NULL, snapshot TEXT NOT NULL, stop TEXT NOT NULL DEFAULT '',
 created REAL NOT NULL, updated REAL NOT NULL, error TEXT NOT NULL DEFAULT ''
);
CREATE UNIQUE INDEX IF NOT EXISTS one_active_job ON jobs(project_id)
 WHERE status IN ('queued','running');
CREATE TABLE IF NOT EXISTS calls (
 id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
 step TEXT NOT NULL, hash TEXT NOT NULL, provider TEXT NOT NULL, model TEXT NOT NULL,
 state TEXT NOT NULL, reserved INTEGER NOT NULL DEFAULT 0,
 input_tokens INTEGER NOT NULL DEFAULT 0, output_tokens INTEGER NOT NULL DEFAULT 0,
 charged INTEGER NOT NULL DEFAULT 0, usage_kind TEXT NOT NULL DEFAULT '',
 response TEXT NOT NULL DEFAULT '', error TEXT NOT NULL DEFAULT '', created REAL NOT NULL,
 elapsed REAL NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS calls_cache ON calls(project_id,hash,state);
CREATE TABLE IF NOT EXISTS events (
 id INTEGER PRIMARY KEY AUTOINCREMENT, project_id TEXT NOT NULL REFERENCES projects(id),
 level TEXT NOT NULL, message TEXT NOT NULL, detail TEXT NOT NULL, created REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS chapters (
 project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
 number INTEGER NOT NULL, title TEXT NOT NULL, text TEXT NOT NULL,
 summary TEXT NOT NULL, report TEXT NOT NULL, created REAL NOT NULL,
 PRIMARY KEY(project_id,number)
);
CREATE TABLE IF NOT EXISTS revisions (
 id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id),
 number INTEGER NOT NULL, label TEXT NOT NULL, title TEXT NOT NULL, text TEXT NOT NULL,
 report TEXT NOT NULL, created REAL NOT NULL, UNIQUE(project_id,number,label)
);
CREATE TABLE IF NOT EXISTS memories (
 project_id TEXT NOT NULL REFERENCES projects(id), key TEXT NOT NULL,
 kind TEXT NOT NULL, content TEXT NOT NULL, chapter INTEGER NOT NULL,
 closed INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(project_id,key)
);
"""


class Store:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        with self.connect() as conn:
            conn.executescript(SCHEMA)

    @contextlib.contextmanager
    def connect(self):
        conn = sqlite3.connect(self.path, timeout=20)
        conn.row_factory = sqlite3.Row
        from .quality import char_count
        conn.create_function("char_count", 1, char_count, deterministic=True)
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            yield conn
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()

    def all(self, sql: str, args: tuple = ()) -> list[dict]:
        with self.connect() as conn:
            return [dict(r) for r in conn.execute(sql, args).fetchall()]

    def one(self, sql: str, args: tuple = ()) -> dict | None:
        rows = self.all(sql, args)
        return rows[0] if rows else None

    def execute(self, sql: str, args: tuple = ()) -> None:
        with self.connect() as conn:
            conn.execute(sql, args)

    def setting(self, key: str, default: Any = None) -> Any:
        row = self.one("SELECT value FROM settings WHERE key=?", (key,))
        return json.loads(row["value"]) if row else default

    def put_setting(self, key: str, value: Any) -> None:
        self.execute("INSERT INTO settings VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                     (key, dumps(value)))

    def artifact(self, project: str, key: str) -> Any:
        row = self.one("SELECT value FROM artifacts WHERE project_id=? AND key=?", (project, key))
        return json.loads(row["value"]) if row else None

    def put_artifact(self, project: str, key: str, value: Any) -> None:
        self.execute("INSERT INTO artifacts VALUES (?,?,?,?) ON CONFLICT(project_id,key) DO UPDATE SET value=excluded.value,created=excluded.created",
                     (project, key, dumps(value), time.time()))

    def event(self, project: str, message: str, level: str = "info", **detail: Any) -> None:
        self.execute("INSERT INTO events(project_id,level,message,detail,created) VALUES (?,?,?,?,?)",
                     (project, level, message, dumps(detail), time.time()))

    def stage(self, project: str, stage: str) -> None:
        self.execute("UPDATE projects SET stage=?,updated=? WHERE id=?", (stage, time.time(), project))

    def usage(self, project: str) -> dict:
        row = self.one("""SELECT COALESCE(SUM(charged),0) charged,
            COALESCE(SUM(CASE WHEN state='inflight' THEN reserved ELSE 0 END),0) reserved,
            COALESCE(SUM(input_tokens),0) input_tokens, COALESCE(SUM(output_tokens),0) output_tokens,
            COALESCE(SUM(CASE WHEN usage_kind='uncertain' THEN charged ELSE 0 END),0) uncertain_tokens,
            COUNT(*) calls FROM calls WHERE project_id=?""", (project,))
        return row or {}

    def recover(self) -> None:
        """Do not pretend interrupted requests were free. Never replay successful steps."""
        with self.connect() as conn:
            conn.execute("""UPDATE calls SET state='unknown',charged=reserved,usage_kind='uncertain',
                error='服务在请求期间中断，按预留量保守记账；供应商实际账单可能不同' WHERE state='inflight'""")
            conn.execute("UPDATE jobs SET status='queued',updated=? WHERE status='running'", (time.time(),))
