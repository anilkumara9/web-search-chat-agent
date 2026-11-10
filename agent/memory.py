"""Persistent, timestamped conversation memory (SQLite + FTS5).

Every user question and assistant answer is stored with a UTC timestamp.
Recall has two parts:
  * recent(): the last N turns of the current session, always in context.
  * search(): keyword lookup over ALL past turns (all sessions), so
    "what did I ask you about X last week?" can be answered from facts.
"""
import re
import sqlite3
import time
from datetime import datetime, timezone

SCHEMA = """
CREATE TABLE IF NOT EXISTS turns(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id TEXT NOT NULL,
  session TEXT NOT NULL,
  ts REAL NOT NULL,
  role TEXT NOT NULL,
  content TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS turns_user ON turns(user_id, session, id);
CREATE VIRTUAL TABLE IF NOT EXISTS turns_fts USING fts5(content, content='turns', content_rowid='id');
CREATE TRIGGER IF NOT EXISTS turns_ai AFTER INSERT ON turns BEGIN
  INSERT INTO turns_fts(rowid, content) VALUES (new.id, new.content);
END;
"""

_WORD = re.compile(r"[A-Za-z0-9]{3,}")
_STOP = {"the", "and", "what", "did", "you", "was", "were", "about", "ask", "asked",
         "tell", "have", "has", "that", "this", "with", "for", "are", "can", "how",
         "when", "who", "why", "where", "earlier", "before", "again", "remember",
         "last", "ago", "our", "your", "from", "any", "one", "them", "they"}


def fmt_ts(ts: float, tz=timezone.utc) -> str:
    return datetime.fromtimestamp(ts, tz).strftime("%Y-%m-%d %H:%M UTC")


def ago(ts: float, now: float | None = None) -> str:
    """Human relative time computed from the stored timestamp, never by the model."""
    d = max(0, int((now if now is not None else time.time()) - ts))
    if d < 90: return "just now"
    if d < 5400: return f"{round(d/60)} min ago"
    if d < 129600: return f"{round(d/3600)} h ago"
    return f"{round(d/86400)} days ago"


class Memory:
    def __init__(self, path: str = "memory.db"):
        self.db = sqlite3.connect(path)
        self.db.executescript(SCHEMA)

    def add(self, user_id: str, session: str, role: str, content: str, ts: float | None = None) -> int:
        cur = self.db.execute(
            "INSERT INTO turns(user_id, session, ts, role, content) VALUES (?,?,?,?,?)",
            (user_id, session, ts if ts is not None else time.time(), role, content))
        self.db.commit()
        return cur.lastrowid

    def recent(self, user_id: str, session: str, n: int = 8):
        rows = self.db.execute(
            "SELECT ts, role, content FROM turns WHERE user_id=? AND session=? ORDER BY id DESC LIMIT ?",
            (user_id, session, n)).fetchall()
        return list(reversed(rows))

    def search(self, user_id: str, query: str, exclude_ids=(), limit: int = 5, user_only: bool = False):
        """Keyword search across every past turn, newest relevant first."""
        words = [w.lower() for w in _WORD.findall(query) if w.lower() not in _STOP]
        if not words:
            return []
        match = " OR ".join(f'"{w}"' for w in dict.fromkeys(words))
        sql = ("SELECT t.id, t.session, t.ts, t.role, t.content FROM turns_fts f "
               "JOIN turns t ON t.id=f.rowid WHERE turns_fts MATCH ? AND t.user_id=? ")
        args = [match, user_id]
        if user_only:
            sql += "AND t.role='user' "
        sql += "ORDER BY bm25(turns_fts), t.ts DESC LIMIT ?"
        args.append(limit + len(exclude_ids))
        rows = [r for r in self.db.execute(sql, args).fetchall() if r[0] not in exclude_ids]
        return rows[:limit]

    def user_questions(self, user_id: str, limit: int = 30):
        return self.db.execute(
            "SELECT ts, content FROM turns WHERE user_id=? AND role='user' ORDER BY id DESC LIMIT ?",
            (user_id, limit,)).fetchall()
