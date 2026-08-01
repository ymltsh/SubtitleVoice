import os
import json

from ..database import get_db

PROTOTYPE_DIRNAME = "prototype"


def _prototype_dir(project_dir: str) -> str:
    return os.path.join(project_dir, "cache", PROTOTYPE_DIRNAME)


def _prototype_path(project_dir: str, speaker_id: int) -> str:
    return os.path.join(_prototype_dir(project_dir), f"speaker_{speaker_id}.npy")


def _prototype_meta_path(project_dir: str, speaker_id: int) -> str:
    return os.path.join(_prototype_dir(project_dir), f"speaker_{speaker_id}.json")


def build_prototype(project_dir: str, speaker_id: int, method: str = "mean") -> dict:
    from .cache import get_clip_embedding
    import numpy as np

    os.makedirs(_prototype_dir(project_dir), exist_ok=True)

    conn = get_db(project_dir)
    rows = conn.execute(
        "SELECT clip_id FROM speaker_references WHERE speaker_id = ? ORDER BY clip_id",
        (speaker_id,)
    ).fetchall()
    conn.close()

    if not rows:
        return {"error": "No reference clips for this speaker"}

    embeddings = []
    clip_ids = []
    for row in rows:
        clip_id = row["clip_id"]
        emb = get_clip_embedding(project_dir, clip_id)
        if emb is not None:
            embeddings.append(emb)
            clip_ids.append(clip_id)

    if not embeddings:
        return {"error": "No cached embeddings found for reference clips. Build embedding cache first."}

    embeddings = np.array(embeddings)

    if method == "mean":
        prototype = np.mean(embeddings, axis=0)
    elif method == "median":
        prototype = np.median(embeddings, axis=0)
    else:
        return {"error": f"Unknown method: {method}"}

    np.save(_prototype_path(project_dir, speaker_id), prototype)

    meta = {
        "speaker_id": speaker_id,
        "reference_count": len(clip_ids),
        "reference_clips": clip_ids,
        "method": method,
        "dimension": int(prototype.shape[0]),
    }
    with open(_prototype_meta_path(project_dir, speaker_id), "w") as f:
        json.dump(meta, f, indent=2)

    return meta


def get_prototype(project_dir: str, speaker_id: int):
    """Load a prototype only while an AI analysis is running."""
    import numpy as np
    path = _prototype_path(project_dir, speaker_id)
    if not os.path.isfile(path):
        return None
    return np.load(path)


def has_prototype(project_dir: str, speaker_id: int) -> bool:
    return os.path.isfile(_prototype_path(project_dir, speaker_id))


def delete_prototype(project_dir: str, speaker_id: int):
    for path in [_prototype_path(project_dir, speaker_id),
                  _prototype_meta_path(project_dir, speaker_id)]:
        if os.path.isfile(path):
            os.remove(path)
