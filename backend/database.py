import os
import sqlite3
from typing import Optional

from .models import Clip

STATUS_IDLE = "idle"
STATUS_RUNNING = "running"
STATUS_SUCCESS = "success"
STATUS_FAILED = "failed"
STATUS_DIRTY = "dirty"

_DB_VERSION = 8
_EXPECTED_SPEAKER_COLS = {"id", "name", "color", "created_at", "reference_version"}
_EXPECTED_ANALYSIS_COLS = {"speaker_id", "episode", "threshold", "analyzed_at", "clip_count", "selected_count", "status", "updated_at", "reason"}
_EXPECTED_CLIPS_COLS = {"id", "episode", "start", "end", "text", "selected_speaker_id", "trim_start", "trim_end"}
_VIDEO_EXTS = {".mkv", ".mp4", ".avi", ".webm", ".flv", ".mov"}
_SUB_EXTS = {".ass", ".srt", ".ssa"}


def get_project_db_path(project_dir: str) -> str:
    return os.path.join(project_dir, "project.db")


def get_db(project_dir: str) -> sqlite3.Connection:
    conn = sqlite3.connect(get_project_db_path(project_dir))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _columns(conn, table):
    return [r["name"] for r in conn.execute(f"PRAGMA table_info({table})")]


def _exists(conn, table):
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone() is not None


def init_project_db(project_dir: str):
    """Create the deliberately small v0.3 business schema.

    Previous versions are rebuilt once, retaining only the data that still has a
    meaning in v0.3. Predictions are intentionally disposable cache data.
    """
    conn = get_db(project_dir)
    try:
        conn.execute("BEGIN")
        if _exists(conn, "clips") and set(_columns(conn, "clips")) != _EXPECTED_CLIPS_COLS:
            old_cols = set(_columns(conn, "clips"))
            selection = "selected_speaker_id" if "selected_speaker_id" in old_cols else "NULL"
            trim_s = "trim_start" if "trim_start" in old_cols else "0.0"
            trim_e = "trim_end" if "trim_end" in old_cols else "0.0"
            conn.execute("ALTER TABLE clips RENAME TO clips_legacy")
            conn.execute("""CREATE TABLE clips (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                episode TEXT NOT NULL, start REAL NOT NULL, end REAL NOT NULL,
                text TEXT NOT NULL, selected_speaker_id INTEGER,
                trim_start REAL NOT NULL DEFAULT 0.0, trim_end REAL NOT NULL DEFAULT 0.0
            )""")
            conn.execute(f"""INSERT INTO clips (id, episode, start, end, text, selected_speaker_id, trim_start, trim_end)
                SELECT id, episode, start, end, text, {selection}, {trim_s}, {trim_e} FROM clips_legacy""")
            conn.execute("DROP TABLE clips_legacy")
        else:
            conn.execute("""CREATE TABLE IF NOT EXISTS clips (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                episode TEXT NOT NULL, start REAL NOT NULL, end REAL NOT NULL,
                text TEXT NOT NULL, selected_speaker_id INTEGER,
                trim_start REAL NOT NULL DEFAULT 0.0, trim_end REAL NOT NULL DEFAULT 0.0
            )""")

        if _exists(conn, "speakers") and set(_columns(conn, "speakers")) != _EXPECTED_SPEAKER_COLS:
            old_cols = set(_columns(conn, "speakers"))
            created = "created_at" if "created_at" in old_cols else "datetime('now', 'localtime')"
            conn.execute("ALTER TABLE speakers RENAME TO speakers_legacy")
            conn.execute("""CREATE TABLE speakers (
                id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL UNIQUE,
                color TEXT NOT NULL DEFAULT '#0ea5e9',
                created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
                reference_version INTEGER NOT NULL DEFAULT 0
            )""")
            conn.execute(f"INSERT INTO speakers (id, name, color, created_at) SELECT id, name, color, {created} FROM speakers_legacy")
            conn.execute("DROP TABLE speakers_legacy")
        else:
            conn.execute("""CREATE TABLE IF NOT EXISTS speakers (
                id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL UNIQUE,
                color TEXT NOT NULL DEFAULT '#0ea5e9',
                created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
                reference_version INTEGER NOT NULL DEFAULT 0
            )""")

        conn.execute("""CREATE TABLE IF NOT EXISTS speaker_references (
            speaker_id INTEGER NOT NULL, clip_id INTEGER NOT NULL,
            PRIMARY KEY (speaker_id, clip_id),
            FOREIGN KEY (speaker_id) REFERENCES speakers(id) ON DELETE CASCADE,
            FOREIGN KEY (clip_id) REFERENCES clips(id) ON DELETE CASCADE
        )""")
        if _exists(conn, "speaker_analysis") and set(_columns(conn, "speaker_analysis")) != _EXPECTED_ANALYSIS_COLS:
            conn.execute("DROP TABLE speaker_analysis")
        conn.execute("""CREATE TABLE IF NOT EXISTS speaker_analysis (
            speaker_id INTEGER NOT NULL,
            episode TEXT NOT NULL,
            threshold REAL NOT NULL,
            analyzed_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
            clip_count INTEGER NOT NULL DEFAULT 0,
            selected_count INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'idle',
            updated_at TEXT,
            reason TEXT,
            PRIMARY KEY (speaker_id, episode),
            FOREIGN KEY (speaker_id) REFERENCES speakers(id) ON DELETE CASCADE
        )""")
        conn.execute("""CREATE TABLE IF NOT EXISTS speaker_prototype (
            speaker_id INTEGER PRIMARY KEY,
            status TEXT NOT NULL DEFAULT 'idle',
            reference_version INTEGER NOT NULL DEFAULT 0,
            created_at TEXT,
            updated_at TEXT,
            FOREIGN KEY (speaker_id) REFERENCES speakers(id) ON DELETE CASCADE
        )""")
        conn.execute("DROP TABLE IF EXISTS predictions")
        conn.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)")
        conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES('db_version', ?)", (str(_DB_VERSION),))
        conn.commit()
    finally:
        conn.close()


def insert_clips_batch(project_dir: str, clips: list[Clip]) -> int:
    conn = get_db(project_dir)
    try:
        cur = conn.executemany("INSERT INTO clips (episode, start, end, text) VALUES (?, ?, ?, ?)", [(c.episode, c.start, c.end, c.text) for c in clips])
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


def set_clip_trim(project_dir: str, clip_id: int, trim_start: float, trim_end: float):
    conn = get_db(project_dir)
    try:
        conn.execute("UPDATE clips SET trim_start=?, trim_end=? WHERE id=?", (trim_start, trim_end, clip_id))
        conn.commit()
    finally:
        conn.close()


def get_clip(project_dir: str, clip_id: int) -> Optional[Clip]:
    conn = get_db(project_dir)
    try:
        row = conn.execute("SELECT * FROM clips WHERE id=?", (clip_id,)).fetchone()
        return Clip(**dict(row)) if row else None
    finally:
        conn.close()


def get_clips(project_dir: str, episode: str = "") -> list[Clip]:
    conn = get_db(project_dir)
    try:
        query, params = "SELECT * FROM clips", []
        if episode:
            query += " WHERE episode=?"; params.append(episode)
        query += " ORDER BY episode, id"
        return [Clip(**dict(r)) for r in conn.execute(query, params).fetchall()]
    finally:
        conn.close()


def query_clips(project_dir: str, speaker_id: Optional[int] = None, episode: str = "", keyword: str = "", selected: Optional[bool] = None) -> list[dict]:
    conn = get_db(project_dir)
    try:
        where, params = [], []
        if episode: where.append("episode=?"); params.append(episode)
        if keyword: where.append("text LIKE ?"); params.append(f"%{keyword}%")
        if selected is True:
            if speaker_id is None: where.append("selected_speaker_id IS NOT NULL")
            else: where.append("selected_speaker_id=?"); params.append(speaker_id)
        elif selected is False: where.append("selected_speaker_id IS NULL")
        sql = "SELECT * FROM clips" + (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY episode, id"
        return [dict(r) for r in conn.execute(sql, params).fetchall()]
    finally:
        conn.close()


def set_clip_speaker(project_dir: str, clip_id: int, speaker_id: Optional[int]):
    conn = get_db(project_dir)
    try:
        conn.execute("UPDATE clips SET selected_speaker_id=? WHERE id=?", (speaker_id, clip_id)); conn.commit()
    finally: conn.close()


def delete_clips_by_episode(project_dir: str, episode: str):
    conn = get_db(project_dir)
    try:
        conn.execute("DELETE FROM clips WHERE episode=?", (episode,))
        conn.execute("DELETE FROM speaker_analysis WHERE episode=?", (episode,))
        conn.commit()
    finally: conn.close()


def get_episodes(project_dir: str) -> list[str]:
    conn = get_db(project_dir)
    try: return [r["episode"] for r in conn.execute("SELECT DISTINCT episode FROM clips ORDER BY episode")]
    finally: conn.close()


def get_speaker_stats(project_dir: str) -> list[dict]:
    conn = get_db(project_dir)
    try:
        rows = conn.execute("""SELECT s.id, s.name, s.color, s.created_at,
            (SELECT COUNT(*) FROM speaker_references r WHERE r.speaker_id=s.id) reference_count,
            (SELECT COUNT(*) FROM clips c WHERE c.selected_speaker_id=s.id) selected_count
            FROM speakers s ORDER BY s.id""").fetchall()
        return [dict(r) for r in rows]
    finally: conn.close()


def get_episode_stats(project_dir: str) -> list[dict]:
    conn = get_db(project_dir)
    try:
        return [dict(r) for r in conn.execute("""SELECT episode, COUNT(*) clip_count,
            SUM(CASE WHEN selected_speaker_id IS NOT NULL THEN 1 ELSE 0 END) selected_count
            FROM clips GROUP BY episode ORDER BY episode""").fetchall()]
    finally: conn.close()


def scan_material_pairs(material_dir: str) -> list[dict]:
    if not os.path.isdir(material_dir): return []
    found = {}
    for name in os.listdir(material_dir):
        path = os.path.join(material_dir, name); base, ext = os.path.splitext(name); ext = ext.lower()
        if ext in _VIDEO_EXTS: found.setdefault(base.lower(), {"name": base})["video_path"] = path
        if ext in _SUB_EXTS: found.setdefault(base.lower(), {"name": base})["subtitle_path"] = path
    return [v for _, v in sorted(found.items()) if "video_path" in v and "subtitle_path" in v]


def set_speaker_analysis(project_dir: str, speaker_id: int, episode: str, threshold: float, clip_count: int, selected_count: int, status: str = STATUS_IDLE):
    conn = get_db(project_dir)
    try:
        conn.execute("""INSERT OR REPLACE INTO speaker_analysis (speaker_id, episode, threshold, analyzed_at, clip_count, selected_count, status, updated_at)
            VALUES (?, ?, ?, datetime('now', 'localtime'), ?, ?, ?, datetime('now', 'localtime'))""",
            (speaker_id, episode, threshold, clip_count, selected_count, status))
        conn.commit()
    finally:
        conn.close()


def get_speaker_analysis_by_episode(project_dir: str, speaker_id: int, episode: str) -> dict:
    conn = get_db(project_dir)
    try:
        row = conn.execute(
            "SELECT * FROM speaker_analysis WHERE speaker_id=? AND episode=?",
            (speaker_id, episode)
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def get_speaker_analysis(project_dir: str) -> list[dict]:
    conn = get_db(project_dir)
    try:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM speaker_analysis ORDER BY speaker_id, episode"
        ).fetchall()]
    finally:
        conn.close()


def delete_speaker_analysis(project_dir: str, speaker_id: int, episode: str = None):
    conn = get_db(project_dir)
    try:
        if episode:
            conn.execute("DELETE FROM speaker_analysis WHERE speaker_id=? AND episode=?", (speaker_id, episode))
        else:
            conn.execute("DELETE FROM speaker_analysis WHERE speaker_id=?", (speaker_id,))
        conn.commit()
    finally:
        conn.close()


def get_prototype_status(project_dir: str, speaker_id: int) -> dict:
    conn = get_db(project_dir)
    try:
        row = conn.execute("SELECT * FROM speaker_prototype WHERE speaker_id=?", (speaker_id,)).fetchone()
        return dict(row) if row else {"speaker_id": speaker_id, "status": STATUS_IDLE, "reference_version": 0}
    finally:
        conn.close()


def set_prototype_status(project_dir: str, speaker_id: int, status: str, reference_version: int = 0):
    conn = get_db(project_dir)
    try:
        conn.execute("""INSERT OR REPLACE INTO speaker_prototype (speaker_id, status, reference_version, updated_at)
            VALUES (?, ?, ?, datetime('now', 'localtime'))""",
            (speaker_id, status, reference_version))
        conn.commit()
    finally:
        conn.close()


def bump_reference_version(project_dir: str, speaker_id: int, conn=None):
    close_after = False
    if conn is None:
        conn = get_db(project_dir)
        close_after = True
    try:
        conn.execute("UPDATE speakers SET reference_version = reference_version + 1 WHERE id=?", (speaker_id,))
        conn.commit()
    finally:
        if close_after:
            conn.close()


def mark_prototype_dirty(project_dir: str, speaker_id: int):
    set_prototype_status(project_dir, speaker_id, STATUS_DIRTY)
    mark_analyses_dirty(project_dir, speaker_id, reason="prototype_dirty")


def mark_analyses_dirty(project_dir: str, speaker_id: int, reason: str = "prototype_changed"):
    conn = get_db(project_dir)
    try:
        conn.execute("""UPDATE speaker_analysis SET status=?, updated_at=datetime('now', 'localtime'), reason=?
            WHERE speaker_id=? AND status=?""",
            (STATUS_DIRTY, reason, speaker_id, STATUS_SUCCESS))
        conn.commit()
    finally:
        conn.close()
