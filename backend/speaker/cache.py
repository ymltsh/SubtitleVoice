from __future__ import annotations

"""Version-safe, per-clip audio and embedding cache.

Embeddings are stored as regular numeric ``.npy`` arrays.  No project data is
ever unpickled, so opening a project copied from another machine cannot execute
Python code.  Per-clip files also make trim/import invalidation exact.
"""
import json
import os
import subprocess

from ..ffmpeg_utils import get_ffmpeg_path
from ..database import get_clips

CACHE_DIRNAME = "cache"
WAV_DIRNAME = "wav"
EMBEDDING_DIRNAME = "embedding"
WAV_SAMPLE_RATE = 22050
META_FILE = "cache_meta.json"
LEGACY_EMBEDDING_FILE = "ecapa.npy"


def _cache_dir(project_dir: str) -> str:
    return os.path.join(project_dir, CACHE_DIRNAME)


def _wav_dir(project_dir: str) -> str:
    return os.path.join(_cache_dir(project_dir), WAV_DIRNAME)


def _embedding_dir(project_dir: str) -> str:
    return os.path.join(_cache_dir(project_dir), EMBEDDING_DIRNAME)


def _meta_path(project_dir: str) -> str:
    return os.path.join(_cache_dir(project_dir), META_FILE)


def get_clip_wav_path(project_dir: str, clip_id: int) -> str:
    return os.path.join(_wav_dir(project_dir), f"{clip_id:06d}.wav")


def _embedding_path(project_dir: str, clip_id: int) -> str:
    return os.path.join(_embedding_dir(project_dir), f"{clip_id:06d}.npy")


def _read_meta(project_dir: str) -> dict:
    path = _meta_path(project_dir)
    try:
        with open(path, "r", encoding="utf-8") as file:
            return json.load(file)
    except (OSError, ValueError, TypeError):
        return {"wav_count": 0, "embedding_count": 0, "version": 2}


def _write_meta(project_dir: str, meta: dict):
    os.makedirs(_cache_dir(project_dir), exist_ok=True)
    with open(_meta_path(project_dir), "w", encoding="utf-8") as file:
        json.dump(meta, file, ensure_ascii=False, indent=2)


class CacheInvalidator:
    """The one place that removes derived audio, vectors, and score caches."""

    def __init__(self, project_dir: str):
        self.project_dir = project_dir

    def _remove(self, path: str):
        try:
            os.remove(path)
        except FileNotFoundError:
            pass

    def invalidate_clip(self, clip_id: int, episode: str = ""):
        self._remove(get_clip_wav_path(self.project_dir, clip_id))
        self._remove(_embedding_path(self.project_dir, clip_id))
        self._remove(os.path.join(_embedding_dir(self.project_dir), LEGACY_EMBEDDING_FILE))
        if episode:
            self.invalidate_retrieval(episode)
        self.refresh_meta()

    def invalidate_episode(self, episode: str, clip_ids: list[int] | None = None):
        # The caller captures IDs before deleting the subtitle rows, allowing
        # exact cleanup without scanning or trusting orphaned filenames.
        for clip_id in clip_ids or []:
            self._remove(get_clip_wav_path(self.project_dir, clip_id))
            self._remove(_embedding_path(self.project_dir, clip_id))
        self.invalidate_retrieval(episode)
        self._remove(os.path.join(_embedding_dir(self.project_dir), LEGACY_EMBEDDING_FILE))
        self.refresh_meta()

    def invalidate_retrieval(self, episode: str):
        import glob
        safe = episode.replace("/", "_").replace("\\", "_")
        for path in glob.glob(os.path.join(_cache_dir(self.project_dir), "retrieval", f"speaker_*_{safe}.json")):
            self._remove(path)

    def refresh_meta(self):
        wav_dir = _wav_dir(self.project_dir)
        embedding_dir = _embedding_dir(self.project_dir)
        meta = _read_meta(self.project_dir)
        meta.update({
            "wav_count": len([name for name in os.listdir(wav_dir) if name.endswith(".wav")]) if os.path.isdir(wav_dir) else 0,
            "embedding_count": len([name for name in os.listdir(embedding_dir) if name.endswith(".npy")]) if os.path.isdir(embedding_dir) else 0,
            "version": 2,
        })
        _write_meta(self.project_dir, meta)


def build_wav_cache(project_dir: str, episode: str, video_path: str,
                    progress_callback=None, include_unreviewed: bool = False) -> dict:
    os.makedirs(_wav_dir(project_dir), exist_ok=True)
    os.makedirs(_embedding_dir(project_dir), exist_ok=True)
    clips = get_clips(project_dir, episode)
    selected_clips = [clip for clip in clips if clip.selected_speaker_id is not None]
    target_clips = clips  # analysis needs both positive and negative examples
    total, generated, skipped, errors = len(target_clips), 0, 0, []
    ffmpeg = get_ffmpeg_path() or "ffmpeg"

    for index, clip in enumerate(target_clips):
        wav_path = get_clip_wav_path(project_dir, clip.id)
        if os.path.isfile(wav_path):
            skipped += 1
            if progress_callback:
                progress_callback(index + 1, total, "skipped")
            continue
        command = [ffmpeg, "-y", "-ss", str(clip.effective_start), "-t", str(clip.effective_duration),
                   "-i", video_path, "-ac", "1", "-ar", str(WAV_SAMPLE_RATE), "-sample_fmt", "s16", wav_path]
        try:
            result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace")
            if result.returncode:
                errors.append({"clip_id": clip.id, "error": result.stderr[:200]})
            else:
                generated += 1
        except Exception as error:
            errors.append({"clip_id": clip.id, "error": str(error)})
        if progress_callback:
            progress_callback(index + 1, total, "processed")

    CacheInvalidator(project_dir).refresh_meta()
    return {"total": total, "generated": generated, "skipped": skipped, "errors": len(errors),
            "error_list": errors[:10], "total_clips": len(clips),
            "selected_clips": len(selected_clips), "unselected_clips": len(clips) - len(selected_clips)}


def build_embedding_cache(project_dir: str, progress_callback=None) -> dict:
    from .embedding import is_encoder_available, get_encoder
    import numpy as np
    if not is_encoder_available():
        return {"error": "ECAPA encoder not available. Install torch + speechbrain."}

    # Do not load the legacy object/pickle cache.  It is ignored and replaced
    # gradually by safe per-clip arrays.
    os.makedirs(_embedding_dir(project_dir), exist_ok=True)
    CacheInvalidator(project_dir)._remove(os.path.join(_embedding_dir(project_dir), LEGACY_EMBEDDING_FILE))
    live_clips = [clip for clip in get_clips(project_dir) if os.path.isfile(get_clip_wav_path(project_dir, clip.id))]
    total = len(live_clips)
    if not total:
        return {"total": 0, "generated": 0, "skipped": 0, "errors": 0}
    if progress_callback:
        progress_callback(0, total, "loading_model")
    encoder = get_encoder()
    generated = skipped = errors = 0
    for index, clip in enumerate(live_clips):
        path = _embedding_path(project_dir, clip.id)
        if os.path.isfile(path):
            try:
                np.load(path, allow_pickle=False)
                skipped += 1
                if progress_callback:
                    progress_callback(index + 1, total, "skipped")
                continue
            except (OSError, ValueError):
                CacheInvalidator(project_dir)._remove(path)
        try:
            embedding = np.asarray(encoder.encode(get_clip_wav_path(project_dir, clip.id)))
            np.save(path, embedding, allow_pickle=False)
            generated += 1
        except Exception:
            errors += 1
        if progress_callback:
            progress_callback(index + 1, total, f"emb_{generated}")
    CacheInvalidator(project_dir).refresh_meta()
    return {"total": total, "generated": generated, "skipped": skipped, "errors": errors}


def get_clip_embedding(project_dir: str, clip_id: int) -> np.ndarray | None:
    import numpy as np
    path = _embedding_path(project_dir, clip_id)
    if not os.path.isfile(path):
        return None
    try:
        return np.load(path, allow_pickle=False)
    except (OSError, ValueError):
        return None


def load_all_embeddings(project_dir: str):
    import numpy as np
    ids, embeddings = [], []
    for clip in get_clips(project_dir):
        embedding = get_clip_embedding(project_dir, clip.id)
        if embedding is not None:
            ids.append(str(clip.id))
            embeddings.append(embedding)
    return ids, np.asarray(embeddings) if embeddings else None


def clear_cache(project_dir: str):
    import shutil
    for dirname in (WAV_DIRNAME, EMBEDDING_DIRNAME):
        path = os.path.join(_cache_dir(project_dir), dirname)
        if os.path.isdir(path):
            shutil.rmtree(path)
    try:
        os.remove(_meta_path(project_dir))
    except FileNotFoundError:
        pass
