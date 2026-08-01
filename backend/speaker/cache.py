import os
import subprocess
import time

import numpy as np

from ..ffmpeg_utils import get_ffmpeg_path
from ..database import get_clips, get_db

CACHE_DIRNAME = "cache"
WAV_DIRNAME = "wav"
EMBEDDING_DIRNAME = "embedding"

WAV_SAMPLE_RATE = 22050
EMBEDDING_FILE = "ecapa.npy"
META_FILE = "cache_meta.json"


def _cache_dir(project_dir: str) -> str:
    return os.path.join(project_dir, CACHE_DIRNAME)


def _wav_dir(project_dir: str) -> str:
    return os.path.join(_cache_dir(project_dir), WAV_DIRNAME)


def _embedding_dir(project_dir: str) -> str:
    return os.path.join(_cache_dir(project_dir), EMBEDDING_DIRNAME)


def _embedding_path(project_dir: str) -> str:
    return os.path.join(_embedding_dir(project_dir), EMBEDDING_FILE)


def _meta_path(project_dir: str) -> str:
    return os.path.join(_cache_dir(project_dir), META_FILE)


def get_clip_wav_path(project_dir: str, clip_id: int) -> str:
    return os.path.join(_wav_dir(project_dir), f"{clip_id:06d}.wav")


def _read_meta(project_dir: str) -> dict:
    import json
    path = _meta_path(project_dir)
    if os.path.isfile(path):
        with open(path, "r") as f:
            return json.load(f)
    return {"wav_count": 0, "embedding_count": 0, "version": 1}


def _write_meta(project_dir: str, meta: dict):
    import json
    os.makedirs(_cache_dir(project_dir), exist_ok=True)
    with open(_meta_path(project_dir), "w") as f:
        json.dump(meta, f, indent=2)


def build_wav_cache(project_dir: str, episode: str, video_path: str,
                    progress_callback=None, include_unreviewed: bool = False) -> dict:
    os.makedirs(_wav_dir(project_dir), exist_ok=True)
    os.makedirs(_embedding_dir(project_dir), exist_ok=True)

    clips = get_clips(project_dir, episode)
    selected_clips = [c for c in clips if c.selected_speaker_id is not None]
    unselected_clips = [c for c in clips if c.selected_speaker_id is None]

    if include_unreviewed:
        target_clips = clips
    else:
        target_clips = clips

    total = len(target_clips)

    if total == 0:
        return {
            "total": total,
            "generated": 0,
            "skipped": 0,
            "errors": 0,
            "total_clips": len(clips),
            "selected_clips": len(selected_clips),
            "unselected_clips": len(unselected_clips),
            "hint": "",
        }

    ffmpeg = get_ffmpeg_path() or "ffmpeg"
    generated = 0
    skipped = 0
    errors = []

    for i, clip in enumerate(target_clips):
        wav_path = get_clip_wav_path(project_dir, clip.id)

        if os.path.isfile(wav_path):
            skipped += 1
            if progress_callback:
                progress_callback(i + 1, total, "skipped")
            continue

        start = clip.effective_start
        duration = clip.effective_duration

        cmd = [
            ffmpeg, "-y", "-ss", str(start), "-t", str(duration),
            "-i", video_path, "-ac", "1", "-ar", str(WAV_SAMPLE_RATE),
            "-sample_fmt", "s16", wav_path,
        ]

        try:
            r = subprocess.run(cmd, capture_output=True, text=True,
                             encoding="utf-8", errors="replace")
            if r.returncode != 0:
                errors.append({"clip_id": clip.id, "error": r.stderr[:200]})
            else:
                generated += 1
        except Exception as e:
            errors.append({"clip_id": clip.id, "error": str(e)})

        if progress_callback:
            progress_callback(i + 1, total, "processed")

    # Count actual WAV files on disk
    wav_dir_path = _wav_dir(project_dir)
    actual_count = len([f for f in os.listdir(wav_dir_path) if f.endswith(".wav")]) if os.path.isdir(wav_dir_path) else 0

    meta = _read_meta(project_dir)
    meta["wav_count"] = actual_count
    _write_meta(project_dir, meta)

    return {
        "total": total, "generated": generated, "skipped": skipped,
        "errors": len(errors), "error_list": errors[:10],
        "total_clips": len(clips),
        "selected_clips": len(selected_clips),
        "unselected_clips": len(unselected_clips),
    }


def build_embedding_cache(project_dir: str, progress_callback=None) -> dict:
    from .embedding import is_encoder_available, get_encoder

    if not is_encoder_available():
        return {"error": "ECAPA encoder not available. Install torch + speechbrain."}

    os.makedirs(_embedding_dir(project_dir), exist_ok=True)

    wav_dir = _wav_dir(project_dir)
    wav_files = []
    if os.path.isdir(wav_dir):
        wav_files = sorted(f for f in os.listdir(wav_dir) if f.endswith(".wav"))

    total = len(wav_files)
    if total == 0:
        return {"total": 0, "generated": 0, "skipped": 0, "errors": 0}

    if progress_callback:
        progress_callback(0, total, "loading_model")

    encoder = get_encoder()
    embeddings = {}
    meta = _read_meta(project_dir)
    cached_ids = set()

    emb_path = _embedding_path(project_dir)
    if os.path.isfile(emb_path):
        try:
            existing = np.load(emb_path, allow_pickle=True)
            if isinstance(existing, np.ndarray) and existing.dtype == object:
                existing = existing.item()
            if isinstance(existing, dict):
                cached_ids = set(existing.keys())
                embeddings = existing
        except Exception:
            pass

    generated = 0
    skipped = 0
    errors = 0

    for i, fname in enumerate(wav_files):
        clip_id = str(int(os.path.splitext(fname)[0]))
        if clip_id in cached_ids:
            skipped += 1
            if progress_callback:
                progress_callback(i + 1, total, "skipped")
            continue

        wav_path = os.path.join(wav_dir, fname)
        try:
            emb = encoder.encode(wav_path)
            embeddings[clip_id] = emb
            generated += 1
        except Exception:
            errors += 1

        if progress_callback:
            progress_callback(i + 1, total, "emb_{}".format(generated))

    np.save(emb_path, embeddings, allow_pickle=True)

    meta["embedding_count"] = len(embeddings)
    meta["version"] = meta.get("version", 1) + 1
    _write_meta(project_dir, meta)

    return {
        "total": total, "generated": generated, "skipped": skipped,
        "errors": errors,
    }


def get_clip_embedding(project_dir: str, clip_id: int) -> np.ndarray:
    emb_path = _embedding_path(project_dir)
    if not os.path.isfile(emb_path):
        return None
    data = np.load(emb_path, allow_pickle=True)
    if isinstance(data, np.ndarray) and data.dtype == object:
        data = data.item()
    key = str(clip_id)
    return data.get(key)


def load_all_embeddings(project_dir: str):
    emb_path = _embedding_path(project_dir)
    if not os.path.isfile(emb_path):
        return [], None
    data = np.load(emb_path, allow_pickle=True)
    if isinstance(data, np.ndarray) and data.dtype == object:
        data = data.item()
    ids = sorted(data.keys(), key=lambda x: int(x))
    matrix = np.array([data[k] for k in ids])
    return ids, matrix


def clear_cache(project_dir: str):
    import shutil
    d = _cache_dir(project_dir)
    for sub in [WAV_DIRNAME, EMBEDDING_DIRNAME]:
        subpath = os.path.join(d, sub)
        if os.path.isdir(subpath):
            shutil.rmtree(subpath)
    mp = _meta_path(project_dir)
    if os.path.isfile(mp):
        os.remove(mp)
