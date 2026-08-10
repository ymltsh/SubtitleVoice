import os
import sqlite3
from typing import Optional

from .models import Clip

STATUS_IDLE = "idle"
STATUS_RUNNING = "running"
STATUS_SUCCESS = "success"
STATUS_FAILED = "failed"
STATUS_DIRTY = "dirty"

_DB_VERSION = 10
_EXPECTED_SPEAKER_COLS = {"id", "name", "color", "created_at", "reference_version"}
_EXPECTED_ANALYSIS_COLS = {"speaker_id", "episode", "threshold", "analyzed_at", "clip_count", "selected_count", "status", "updated_at", "reason"}
_EXPECTED_CLIPS_COLS = {"id", "episode", "start", "end", "text", "selected_speaker_id", "assignment_source", "trim_start", "trim_end"}
_VIDEO_EXTS = {".mkv", ".mp4", ".avi", ".webm", ".flv", ".mov"}
_SUB_EXTS = {".ass", ".srt", ".ssa"}


def get_project_db_path(project_dir: str) -> str:
    return os.path.join(project_dir, "project.db")


def get_db(project_dir: str) -> sqlite3.Connection:
    conn = sqlite3.connect(get_project_db_path(project_dir))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA journal_mode=WAL")
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
        # Only truly incompatible legacy schemas are rebuilt.  Additive changes
        # must not discard a user's project when it is opened after an upgrade.
        required_clip_cols = {"id", "episode", "start", "end", "text", "selected_speaker_id"}
        if _exists(conn, "clips") and not required_clip_cols.issubset(set(_columns(conn, "clips"))):
            old_cols = set(_columns(conn, "clips"))
            selection = "selected_speaker_id" if "selected_speaker_id" in old_cols else "NULL"
            trim_s = "trim_start" if "trim_start" in old_cols else "0.0"
            trim_e = "trim_end" if "trim_end" in old_cols else "0.0"
            conn.execute("ALTER TABLE clips RENAME TO clips_legacy")
            conn.execute("""CREATE TABLE clips (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                episode TEXT NOT NULL, start REAL NOT NULL, end REAL NOT NULL,
                text TEXT NOT NULL, selected_speaker_id INTEGER,
                assignment_source TEXT,
                trim_start REAL NOT NULL DEFAULT 0.0, trim_end REAL NOT NULL DEFAULT 0.0
            )""")
            conn.execute(f"""INSERT INTO clips (id, episode, start, end, text, selected_speaker_id, assignment_source, trim_start, trim_end)
                SELECT id, episode, start, end, text, {selection},
                CASE WHEN {selection} IS NULL THEN NULL ELSE 'manual' END, {trim_s}, {trim_e} FROM clips_legacy""")
            conn.execute("DROP TABLE clips_legacy")
        else:
            conn.execute("""CREATE TABLE IF NOT EXISTS clips (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                episode TEXT NOT NULL, start REAL NOT NULL, end REAL NOT NULL,
                text TEXT NOT NULL, selected_speaker_id INTEGER,
                assignment_source TEXT,
                trim_start REAL NOT NULL DEFAULT 0.0, trim_end REAL NOT NULL DEFAULT 0.0
            )""")
        if "assignment_source" not in _columns(conn, "clips"):
            conn.execute("ALTER TABLE clips ADD COLUMN assignment_source TEXT")
        # Existing selections predate source tracking.  Treat them as manual so
        # an upgrade can never erase already-reviewed work on the first re-run.
        conn.execute("""UPDATE clips SET assignment_source='manual'
            WHERE selected_speaker_id IS NOT NULL AND assignment_source IS NULL""")

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
        conn.execute("""CREATE TABLE IF NOT EXISTS analysis_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            speaker_id INTEGER NOT NULL,
            episode TEXT NOT NULL,
            threshold REAL NOT NULL,
            prototype_version INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL,
            started_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
            completed_at TEXT,
            error TEXT,
            FOREIGN KEY (speaker_id) REFERENCES speakers(id) ON DELETE CASCADE
        )""")
        conn.execute("""CREATE TABLE IF NOT EXISTS clip_predictions (
            analysis_run_id INTEGER NOT NULL,
            clip_id INTEGER NOT NULL,
            score REAL NOT NULL,
            PRIMARY KEY (analysis_run_id, clip_id),
            FOREIGN KEY (analysis_run_id) REFERENCES analysis_runs(id) ON DELETE CASCADE,
            FOREIGN KEY (clip_id) REFERENCES clips(id) ON DELETE CASCADE
        )""")
        conn.execute("""CREATE TABLE IF NOT EXISTS jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            kind TEXT NOT NULL,
            speaker_id INTEGER,
            episode TEXT,
            threshold REAL,
            status TEXT NOT NULL,
            step TEXT NOT NULL DEFAULT '',
            current INTEGER NOT NULL DEFAULT 0,
            total INTEGER NOT NULL DEFAULT 0,
            error TEXT NOT NULL DEFAULT '',
            result_json TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
            FOREIGN KEY (speaker_id) REFERENCES speakers(id) ON DELETE CASCADE
        )""")
        conn.execute("""CREATE TABLE IF NOT EXISTS training_segments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            speaker_id INTEGER NOT NULL,
            episode TEXT NOT NULL,
            start REAL NOT NULL,
            end REAL NOT NULL,
            text TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'candidate',
            origin TEXT NOT NULL DEFAULT 'auto',
            created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
            FOREIGN KEY (speaker_id) REFERENCES speakers(id) ON DELETE CASCADE,
            CHECK (end > start),
            CHECK (status IN ('candidate', 'approved', 'rejected', 'stale')),
            CHECK (origin IN ('auto', 'manual'))
        )""")
        conn.execute("""CREATE TABLE IF NOT EXISTS training_segment_clips (
            segment_id INTEGER NOT NULL,
            clip_id INTEGER NOT NULL,
            position INTEGER NOT NULL,
            PRIMARY KEY (segment_id, clip_id),
            UNIQUE (segment_id, position),
            FOREIGN KEY (segment_id) REFERENCES training_segments(id) ON DELETE CASCADE,
            FOREIGN KEY (clip_id) REFERENCES clips(id) ON DELETE CASCADE
        )""")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_clips_episode_speaker ON clips(episode, selected_speaker_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_predictions_clip ON clip_predictions(clip_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status, kind)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_training_segments_speaker_episode ON training_segments(speaker_id, episode, status)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_training_segment_clips_clip ON training_segment_clips(clip_id)")
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
        row = conn.execute("SELECT episode FROM clips WHERE id=?", (clip_id,)).fetchone()
        references = conn.execute("SELECT speaker_id FROM speaker_references WHERE clip_id=?", (clip_id,)).fetchall()
        conn.execute("UPDATE clips SET trim_start=?, trim_end=? WHERE id=?", (trim_start, trim_end, clip_id))
        _mark_training_segments_stale_for_clip(conn, clip_id)
        for reference in references:
            conn.execute("UPDATE speakers SET reference_version=reference_version+1 WHERE id=?", (reference["speaker_id"],))
        conn.commit()
    finally:
        conn.close()
    if row:
        _invalidate_clip_media(project_dir, clip_id, row["episode"])
        mark_episode_analyses_dirty(project_dir, row["episode"], "clip_trimmed")
    for reference in references:
        _invalidate_speaker_prototype(project_dir, reference["speaker_id"])


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
        # A blank manual choice is a deliberate rejection, not an invitation for
        # the next AI run to put the clip back.
        conn.execute("UPDATE clips SET selected_speaker_id=?, assignment_source='manual' WHERE id=?", (speaker_id, clip_id))
        _mark_training_segments_stale_for_clip(conn, clip_id)
        conn.commit()
    finally: conn.close()


def delete_clips_by_episode(project_dir: str, episode: str):
    conn = get_db(project_dir)
    try:
        clip_ids = [row["id"] for row in conn.execute("SELECT id FROM clips WHERE episode=?", (episode,)).fetchall()]
        references = conn.execute("""SELECT DISTINCT speaker_id FROM speaker_references
            WHERE clip_id IN (SELECT id FROM clips WHERE episode=?)""", (episode,)).fetchall()
        conn.execute("DELETE FROM training_segments WHERE episode=?", (episode,))
        conn.execute("DELETE FROM clips WHERE episode=?", (episode,))
        conn.execute("DELETE FROM speaker_analysis WHERE episode=?", (episode,))
        conn.commit()
    finally: conn.close()
    _invalidate_episode_media(project_dir, episode, clip_ids)
    for reference in references:
        _bump_and_invalidate_speaker(project_dir, reference["speaker_id"])


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


def mark_episode_analyses_dirty(project_dir: str, episode: str, reason: str = "episode_changed"):
    """Mark every completed analysis for changed episode audio as stale."""
    conn = get_db(project_dir)
    try:
        conn.execute("""UPDATE speaker_analysis SET status=?, updated_at=datetime('now', 'localtime'), reason=?
            WHERE episode=? AND status=?""", (STATUS_DIRTY, reason, episode, STATUS_SUCCESS))
        conn.commit()
    finally:
        conn.close()


def _invalidate_clip_media(project_dir: str, clip_id: int, episode: str):
    from .speaker.cache import CacheInvalidator
    CacheInvalidator(project_dir).invalidate_clip(clip_id, episode)


def _invalidate_episode_media(project_dir: str, episode: str, clip_ids: list[int] | None = None):
    from .speaker.cache import CacheInvalidator
    CacheInvalidator(project_dir).invalidate_episode(episode, clip_ids)


def _invalidate_speaker_prototype(project_dir: str, speaker_id: int):
    from .speaker.prototype import delete_prototype
    delete_prototype(project_dir, speaker_id)
    mark_prototype_dirty(project_dir, speaker_id)


def _bump_and_invalidate_speaker(project_dir: str, speaker_id: int):
    bump_reference_version(project_dir, speaker_id)
    _invalidate_speaker_prototype(project_dir, speaker_id)


def create_analysis_run(project_dir: str, speaker_id: int, episode: str, threshold: float,
                        prototype_version: int) -> int:
    conn = get_db(project_dir)
    try:
        cur = conn.execute("""INSERT INTO analysis_runs
            (speaker_id, episode, threshold, prototype_version, status) VALUES (?, ?, ?, ?, ?)""",
            (speaker_id, episode, threshold, prototype_version, STATUS_RUNNING))
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def finish_analysis_run(project_dir: str, run_id: int, status: str, error: str = ""):
    conn = get_db(project_dir)
    try:
        conn.execute("""UPDATE analysis_runs SET status=?, completed_at=datetime('now', 'localtime'), error=?
            WHERE id=?""", (status, error, run_id))
        conn.commit()
    finally:
        conn.close()


def replace_auto_assignments(project_dir: str, speaker_id: int, episode: str,
                             threshold: float, results: list[dict], analysis_run_id: int) -> int:
    """Persist predictions and refresh only this speaker's prior AI suggestions.

    Rows marked ``manual`` are final human keep/reject decisions, including a
    manual rejection with a NULL speaker id, and are never modified here.
    """
    conn = get_db(project_dir)
    try:
        conn.execute("BEGIN")
        conn.executemany("INSERT OR REPLACE INTO clip_predictions (analysis_run_id, clip_id, score) VALUES (?, ?, ?)",
                         [(analysis_run_id, item["clip_id"], item["score"]) for item in results])
        conn.execute("""UPDATE training_segments SET status=?, updated_at=datetime('now', 'localtime')
            WHERE speaker_id=? AND episode=? AND status IN ('candidate', 'approved')""",
                     ("stale", speaker_id, episode))
        conn.execute("""UPDATE clips SET selected_speaker_id=NULL, assignment_source=NULL
            WHERE episode=? AND selected_speaker_id=? AND assignment_source='auto'""",
                     (episode, speaker_id))
        candidates = [item for item in results if item["score"] >= threshold]
        for item in candidates:
            conn.execute("""UPDATE clips SET selected_speaker_id=?, assignment_source='auto'
                WHERE id=? AND episode=? AND COALESCE(assignment_source, 'auto') <> 'manual'
                AND (selected_speaker_id IS NULL OR (selected_speaker_id=? AND assignment_source='auto'))""",
                         (speaker_id, item["clip_id"], episode, speaker_id))
        conn.commit()
        return len(candidates)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def create_job(project_dir: str, kind: str, speaker_id: int, episode: str, threshold: float,
               status: str = STATUS_RUNNING) -> int:
    conn = get_db(project_dir)
    try:
        cur = conn.execute("""INSERT INTO jobs(kind, speaker_id, episode, threshold, status)
            VALUES (?, ?, ?, ?, ?)""", (kind, speaker_id, episode, threshold, status))
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def update_job(project_dir: str, job_id: int, **changes):
    allowed = {"status", "step", "current", "total", "error", "result_json"}
    values = {key: value for key, value in changes.items() if key in allowed}
    if not values:
        return
    if "result" in changes:
        import json
        values["result_json"] = json.dumps(changes["result"], ensure_ascii=False)
    assignments = ", ".join(f"{field}=?" for field in values)
    conn = get_db(project_dir)
    try:
        conn.execute(f"UPDATE jobs SET {assignments}, updated_at=datetime('now', 'localtime') WHERE id=?",
                     [*values.values(), job_id])
        conn.commit()
    finally:
        conn.close()


def get_job(project_dir: str, job_id: int) -> dict | None:
    conn = get_db(project_dir)
    try:
        row = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        if not row:
            return None
        item = dict(row)
        import json
        item["result"] = json.loads(item.pop("result_json") or "null")
        return item
    finally:
        conn.close()


def has_running_job(project_dir: str) -> bool:
    conn = get_db(project_dir)
    try:
        return conn.execute("SELECT 1 FROM jobs WHERE status=? LIMIT 1", (STATUS_RUNNING,)).fetchone() is not None
    finally:
        conn.close()


def recover_interrupted_jobs(project_dir: str):
    """A daemon thread cannot survive process exit; leave an auditable outcome."""
    conn = get_db(project_dir)
    try:
        interrupted = conn.execute("SELECT speaker_id, episode FROM jobs WHERE status IN (?, ?)", (STATUS_RUNNING, "queued")).fetchall()
        conn.execute("""UPDATE jobs SET status=?, step=?, error=?, updated_at=datetime('now', 'localtime')
            WHERE status IN (?, ?)""", (STATUS_FAILED, "服务已重启", "任务被服务重启中断", STATUS_RUNNING, "queued"))
        for job in interrupted:
            conn.execute("""UPDATE speaker_analysis SET status=?, reason=?, updated_at=datetime('now', 'localtime')
                WHERE speaker_id=? AND episode=? AND status=?""",
                         (STATUS_FAILED, "任务被服务重启中断", job["speaker_id"], job["episode"], STATUS_RUNNING))
        conn.execute("""UPDATE analysis_runs SET status=?, error=?, completed_at=datetime('now', 'localtime')
            WHERE status=?""", (STATUS_FAILED, "任务被服务重启中断", STATUS_RUNNING))
        conn.commit()
    finally:
        conn.close()


def _mark_training_segments_stale_for_clip(conn, clip_id: int):
    conn.execute("""UPDATE training_segments SET status=?, updated_at=datetime('now', 'localtime')
        WHERE status IN ('candidate', 'approved') AND id IN (
            SELECT segment_id FROM training_segment_clips WHERE clip_id=?
        )""", ("stale", clip_id))


def replace_auto_training_segments(project_dir: str, speaker_id: int, episode: str,
                                   segments: list[dict]) -> int:
    """Replace only pending automatic suggestions; approved history remains intact."""
    conn = get_db(project_dir)
    try:
        conn.execute("DELETE FROM training_segments WHERE speaker_id=? AND episode=? AND origin='auto' AND status='candidate'",
                     (speaker_id, episode))
        valid_segments = [segment for segment in segments if len(segment["clip_ids"]) >= 2]
        for segment in valid_segments:
            cur = conn.execute("""INSERT INTO training_segments
                (speaker_id, episode, start, end, text, status, origin)
                VALUES (?, ?, ?, ?, ?, 'candidate', 'auto')""",
                (speaker_id, episode, segment["start"], segment["end"], segment["text"]))
            conn.executemany("INSERT INTO training_segment_clips(segment_id, clip_id, position) VALUES (?, ?, ?)",
                             [(cur.lastrowid, clip_id, pos) for pos, clip_id in enumerate(segment["clip_ids"])])
        conn.commit()
        return len(valid_segments)
    finally:
        conn.close()


def clear_training_segments(project_dir: str, speaker_id: int, episode: str) -> int:
    """Remove every merge decision for one speaker in one episode."""
    conn = get_db(project_dir)
    try:
        cur = conn.execute("DELETE FROM training_segments WHERE speaker_id=? AND episode=?",
                           (speaker_id, episode))
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


def get_training_segments(project_dir: str, speaker_id: int, episode: str = "") -> list[dict]:
    conn = get_db(project_dir)
    try:
        where, params = ["ts.speaker_id=?"], [speaker_id]
        if episode:
            where.append("ts.episode=?")
            params.append(episode)
        rows = conn.execute(f"""SELECT ts.*,
            (SELECT COUNT(*) FROM training_segment_clips tsc WHERE tsc.segment_id=ts.id) AS clip_count,
            (SELECT GROUP_CONCAT(clip_id, ',') FROM (
                SELECT clip_id FROM training_segment_clips WHERE segment_id=ts.id ORDER BY position
            )) AS clip_ids
            FROM training_segments ts WHERE {' AND '.join(where)}
            ORDER BY ts.episode, ts.start, ts.id""", params).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["clip_ids"] = [int(value) for value in item["clip_ids"].split(",")] if item["clip_ids"] else []
            item["duration"] = round(item["end"] - item["start"], 3)
            result.append(item)
        return result
    finally:
        conn.close()


def set_training_segment_status(project_dir: str, segment_id: int, status: str) -> bool:
    if status not in {"candidate", "approved", "rejected"}:
        raise ValueError("无效的训练片段状态")
    conn = get_db(project_dir)
    try:
        previous = "approved" if status == "candidate" else "candidate"
        single_clip_guard = "" if status == "candidate" else """AND (
            SELECT COUNT(*) FROM training_segment_clips WHERE segment_id=training_segments.id
        ) >= 2"""
        cur = conn.execute(f"""UPDATE training_segments SET status=?, updated_at=datetime('now', 'localtime')
            WHERE id=? AND status=? {single_clip_guard}""", (status, segment_id, previous))
        conn.commit()
        return cur.rowcount == 1
    finally:
        conn.close()
