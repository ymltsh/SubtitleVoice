"""Disposable retrieval-result cache.

Scores are not business records.  Every analysis overwrites one small JSON file
per speaker per episode, while the review decision remains clips.selected_speaker_id.
"""
import json
import os

import numpy as np


def _path(project_dir: str, speaker_id: int, episode: str) -> str:
    safe = episode.replace("/", "_").replace("\\", "_")
    return os.path.join(project_dir, "cache", "retrieval", f"speaker_{speaker_id}_{safe}.json")


def _cosine_similarity(query, gallery):
    query = query / (np.linalg.norm(query) + 1e-8)
    gallery = gallery / (np.linalg.norm(gallery, axis=1, keepdims=True) + 1e-8)
    return np.dot(gallery, query)


def retrieve(project_dir: str, speaker_id: int, episode: str) -> list[dict]:
    from .prototype import get_prototype
    from .cache import load_all_embeddings
    from ..database import get_db
    prototype = get_prototype(project_dir, speaker_id)
    ids, embeddings = load_all_embeddings(project_dir)
    if prototype is None or embeddings is None or len(embeddings) == 0:
        return []
    conn = get_db(project_dir)
    try:
        ep_ids = set(str(r["id"]) for r in conn.execute("SELECT id FROM clips WHERE episode=?", (episode,)).fetchall())
    finally:
        conn.close()
    filtered = [(cid, emb) for cid, emb in zip(ids, embeddings) if cid in ep_ids]
    if not filtered:
        return []
    ep_ids_list, ep_embeddings = zip(*filtered)
    ep_embeddings = np.array(ep_embeddings)
    results = [{"clip_id": int(clip_id), "score": float(score)} for clip_id, score in zip(ep_ids_list, _cosine_similarity(prototype, ep_embeddings))]
    results.sort(key=lambda result: result["score"], reverse=True)
    os.makedirs(os.path.dirname(_path(project_dir, speaker_id, episode)), exist_ok=True)
    with open(_path(project_dir, speaker_id, episode), "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    return results


def get_scores(project_dir: str, speaker_id: int, episode: str = "") -> dict[int, float]:
    if not episode:
        return {}
    path = _path(project_dir, speaker_id, episode)
    if not os.path.isfile(path): return {}
    try:
        with open(path, encoding="utf-8") as f:
            return {int(row["clip_id"]): float(row["score"]) for row in json.load(f)}
    except (OSError, ValueError, KeyError, TypeError):
        return {}


def delete_scores(project_dir: str, speaker_id: int, episode: str = ""):
    if episode:
        path = _path(project_dir, speaker_id, episode)
        if os.path.isfile(path): os.remove(path)
    else:
        import glob
        pattern = os.path.join(project_dir, "cache", "retrieval", f"speaker_{speaker_id}_*.json")
        for p in glob.glob(pattern):
            os.remove(p)
