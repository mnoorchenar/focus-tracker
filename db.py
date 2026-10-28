"""SQLite storage: one row per focus session (epoch seconds, local time is derived on display)."""
import os
import sqlite3
from pathlib import Path

DATA_DIR = Path(os.environ.get("APPDATA", Path.home())) / "FocusTracker"
DB_PATH = DATA_DIR / "focus.db"
MIN_SESSION_S = 10

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project TEXT NOT NULL,
    start_ts INTEGER NOT NULL,
    end_ts INTEGER,
    last_ts INTEGER,
    plan_min INTEGER
);
CREATE INDEX IF NOT EXISTS idx_sessions_start ON sessions(start_ts);
CREATE TABLE IF NOT EXISTS projects (name TEXT PRIMARY KEY);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);
"""


class DB:
    def __init__(self, path=DB_PATH):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.con = sqlite3.connect(path)
        self.con.executescript(SCHEMA)
        cols = [r[1] for r in self.con.execute("PRAGMA table_info(sessions)")]
        if "plan_min" not in cols:  # databases created before plans existed
            self.con.execute("ALTER TABLE sessions ADD COLUMN plan_min INTEGER")
        self._recover()

    def _recover(self):
        # A crash or power loss leaves an open row; close it at the last heartbeat.
        self.con.execute(
            "UPDATE sessions SET end_ts = COALESCE(last_ts, start_ts) WHERE end_ts IS NULL")
        self.con.execute("DELETE FROM sessions WHERE end_ts - start_ts < ?", (MIN_SESSION_S,))
        self.con.commit()

    def get(self, key, default=None):
        row = self.con.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return row[0] if row else default

    def set(self, key, value):
        self.con.execute("INSERT OR REPLACE INTO settings VALUES (?, ?)", (key, str(value)))
        self.con.commit()

    # ---- sessions ----
    def start(self, project, ts, plan_min):
        self.add_project(project)
        cur = self.con.execute(
            "INSERT INTO sessions (project, start_ts, last_ts, plan_min) VALUES (?, ?, ?, ?)",
            (project, ts, ts, plan_min))
        self.con.commit()
        return cur.lastrowid

    def heartbeat(self, sid, ts):
        self.con.execute("UPDATE sessions SET last_ts=? WHERE id=?", (ts, sid))
        self.con.commit()

    def set_plan(self, sid, plan_min):
        self.con.execute("UPDATE sessions SET plan_min=? WHERE id=?", (plan_min, sid))
        self.con.commit()

    def stop(self, sid, ts):
        start = self.con.execute("SELECT start_ts FROM sessions WHERE id=?", (sid,)).fetchone()[0]
        if ts - start < MIN_SESSION_S:
            self.con.execute("DELETE FROM sessions WHERE id=?", (sid,))
        else:
            self.con.execute("UPDATE sessions SET end_ts=?, last_ts=? WHERE id=?", (ts, ts, sid))
        self.con.commit()

    def delete_session(self, sid):
        self.con.execute("DELETE FROM sessions WHERE id=?", (sid,))
        self.con.commit()

    def sessions(self, now):
        """All sessions as (id, project, start, end, plan_min); a running session ends at now."""
        return self.con.execute(
            "SELECT id, project, start_ts, COALESCE(end_ts, ?), plan_min FROM sessions "
            "ORDER BY start_ts", (now,)).fetchall()

    def seconds_since(self, since, now):
        row = self.con.execute(
            "SELECT SUM(MIN(COALESCE(end_ts, ?), ?) - MAX(start_ts, ?)) FROM sessions "
            "WHERE COALESCE(end_ts, ?) > ?", (now, now, since, now, since)).fetchone()
        return row[0] or 0

    # ---- projects ----
    def add_project(self, name):
        self.con.execute("INSERT OR IGNORE INTO projects VALUES (?)", (name,))
        self.con.commit()

    def projects(self):
        """Project names, most recently used first, then projects never used."""
        used = [r[0] for r in self.con.execute(
            "SELECT project FROM sessions GROUP BY project ORDER BY MAX(start_ts) DESC")]
        extra = [r[0] for r in self.con.execute("SELECT name FROM projects ORDER BY name")
                 if r[0] not in used]
        return used + extra

    def rename_project(self, old, new):
        self.add_project(new)
        self.con.execute("UPDATE sessions SET project=? WHERE project=?", (new, old))
        self.con.execute("DELETE FROM projects WHERE name=?", (old,))
        self.con.commit()

    def delete_project(self, name):
        self.con.execute("DELETE FROM sessions WHERE project=?", (name,))
        self.con.execute("DELETE FROM projects WHERE name=?", (name,))
        self.con.commit()
