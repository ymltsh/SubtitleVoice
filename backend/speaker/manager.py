from ..database import bump_reference_version, get_db, mark_prototype_dirty


def create_speaker(project_dir: str, name: str, color: str = "#0ea5e9") -> dict:
    conn = get_db(project_dir)
    cur = conn.execute(
        "INSERT INTO speakers (name, color) VALUES (?, ?)",
        (name, color),
    )
    conn.commit()
    speaker_id = cur.lastrowid
    row = conn.execute("SELECT * FROM speakers WHERE id = ?", (speaker_id,)).fetchone()
    conn.close()
    return dict(row)


def get_speakers(project_dir: str) -> list:
    conn = get_db(project_dir)
    rows = conn.execute(
        "SELECT s.*, (SELECT COUNT(*) FROM speaker_references WHERE speaker_id = s.id) as reference_count FROM speakers s ORDER BY s.id"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_speaker(project_dir: str, speaker_id: int) -> dict:
    conn = get_db(project_dir)
    row = conn.execute("SELECT * FROM speakers WHERE id = ?", (speaker_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def update_speaker(project_dir: str, speaker_id: int, name: str = None,
                   color: str = None):
    conn = get_db(project_dir)
    fields = []
    params = []
    if name is not None:
        fields.append("name = ?")
        params.append(name)
    if color is not None:
        fields.append("color = ?")
        params.append(color)
    if fields:
        params.append(speaker_id)
        conn.execute(f"UPDATE speakers SET {', '.join(fields)} WHERE id = ?", params)
        conn.commit()
    conn.close()


def delete_speaker(project_dir: str, speaker_id: int):
    conn = get_db(project_dir)
    conn.execute("UPDATE clips SET selected_speaker_id = NULL WHERE selected_speaker_id = ?", (speaker_id,))
    conn.execute("DELETE FROM speaker_references WHERE speaker_id = ?", (speaker_id,))
    conn.execute("DELETE FROM speakers WHERE id = ?", (speaker_id,))
    conn.commit()
    conn.close()

    from .prototype import delete_prototype
    delete_prototype(project_dir, speaker_id)
    from .retrieval import delete_scores
    delete_scores(project_dir, speaker_id)


def add_reference(project_dir: str, speaker_id: int, clip_id: int):
    conn = get_db(project_dir)
    existing = conn.execute(
        "SELECT 1 FROM speaker_references WHERE speaker_id = ? AND clip_id = ?",
        (speaker_id, clip_id),
    ).fetchone()
    if not existing:
        conn.execute(
            "INSERT INTO speaker_references (speaker_id, clip_id) VALUES (?, ?)",
            (speaker_id, clip_id),
        )
        bump_reference_version(project_dir, speaker_id, conn)
        conn.commit()
    conn.close()
    if not existing:
        mark_prototype_dirty(project_dir, speaker_id)


def remove_reference(project_dir: str, speaker_id: int, clip_id: int):
    conn = get_db(project_dir)
    conn.execute(
        "DELETE FROM speaker_references WHERE speaker_id = ? AND clip_id = ?",
        (speaker_id, clip_id),
    )
    bump_reference_version(project_dir, speaker_id, conn)
    conn.commit()
    conn.close()

    from .prototype import delete_prototype
    delete_prototype(project_dir, speaker_id)
    mark_prototype_dirty(project_dir, speaker_id)


def get_references(project_dir: str, speaker_id: int) -> list:
    conn = get_db(project_dir)
    rows = conn.execute(
        """SELECT sr.*, c.episode, c.start, c.end, c.text, c.selected_speaker_id, c.trim_start, c.trim_end
           FROM speaker_references sr
           JOIN clips c ON sr.clip_id = c.id
           WHERE sr.speaker_id = ?
           ORDER BY sr.clip_id""",
        (speaker_id,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]
