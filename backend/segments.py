"""Generate safe, reviewable training segments from subtitle-level Clips."""
import re

from .database import (get_clips, get_training_segments, query_clips,
                       replace_auto_training_segments)
from .models import ExportItem


DEFAULT_MIN_DURATION = 3.0
DEFAULT_MAX_DURATION = 10.0
DEFAULT_MAX_GAP = 0.35


def _join_text(parts: list[str]) -> str:
    text = ""
    for part in (value.strip() for value in parts):
        if not part:
            continue
        if text and re.search(r"[A-Za-z0-9]$", text) and re.match(r"[A-Za-z0-9]", part):
            text += " "
        text += part
    return text


def _make_segment(clips: list) -> dict:
    return {
        "start": clips[0].effective_start,
        "end": clips[-1].effective_end,
        "text": _join_text([clip.text for clip in clips]),
        "clip_ids": [clip.id for clip in clips],
    }


def _split_run(run: list, min_duration: float, max_duration: float) -> list[dict]:
    """Greedily retain the longest safe range within the desired duration ceiling."""
    segments, current = [], []
    for clip in run:
        if not current:
            current = [clip]
            continue
        prospective = clip.effective_end - current[0].effective_start
        if prospective <= max_duration:
            current.append(clip)
            continue
        if len(current) >= 2 and current[-1].effective_end - current[0].effective_start >= min_duration:
            segments.append(_make_segment(current))
        current = [clip]
    if current and len(current) >= 2 and current[-1].effective_end - current[0].effective_start >= min_duration:
        segments.append(_make_segment(current))
    return segments


def build_training_segment_candidates(project_dir: str, speaker_id: int, episode: str,
                                      min_duration: float = DEFAULT_MIN_DURATION,
                                      max_duration: float = DEFAULT_MAX_DURATION,
                                      max_gap: float = DEFAULT_MAX_GAP) -> dict:
    if not 0 < min_duration <= max_duration:
        raise ValueError("目标时长范围无效")
    if max_gap < 0:
        raise ValueError("最大间隔不能小于 0")

    clips = sorted(get_clips(project_dir, episode), key=lambda clip: (clip.effective_start, clip.id or 0))
    runs, current = [], []
    for clip in clips:
        valid = clip.effective_duration > 0 and clip.selected_speaker_id == speaker_id
        contiguous = (current and valid and
                      clip.effective_start >= current[-1].effective_end and
                      clip.effective_start - current[-1].effective_end <= max_gap)
        if valid and (not current or contiguous):
            current.append(clip)
        else:
            if current:
                runs.append(current)
            current = [clip] if valid else []
    if current:
        runs.append(current)

    approved_clip_ids = {clip_id for segment in get_training_segments(project_dir, speaker_id, episode)
                         if segment["status"] == "approved" for clip_id in segment["clip_ids"]}
    candidates = [segment for run in runs for segment in _split_run(run, min_duration, max_duration)
                  if not approved_clip_ids.intersection(segment["clip_ids"])]
    created = replace_auto_training_segments(project_dir, speaker_id, episode, candidates)
    return {
        "episode": episode,
        "created": created,
        "runs": len(runs),
        "min_duration": min_duration,
        "max_duration": max_duration,
        "max_gap": max_gap,
    }


def get_export_items(project_dir: str, speaker_id: int) -> list[ExportItem]:
    """Approved segments replace their component clips; other kept clips stay exportable."""
    approved = [segment for segment in get_training_segments(project_dir, speaker_id)
                if segment["status"] == "approved" and segment["clip_count"] >= 2]
    covered_clip_ids = {clip_id for segment in approved for clip_id in segment["clip_ids"]}
    items = [ExportItem(id=f"segment_{segment['id']:06d}", episode=segment["episode"],
                        start=segment["start"], end=segment["end"], text=segment["text"])
             for segment in approved]
    items.extend(ExportItem(id=str(clip.id).zfill(4), episode=clip.episode,
                            start=clip.effective_start, end=clip.effective_end, text=clip.text)
                 for clip in get_clips(project_dir)
                 if clip.selected_speaker_id == speaker_id and clip.id not in covered_clip_ids
                 and clip.effective_duration > 0)
    return sorted(items, key=lambda item: (item.episode, item.start, item.id))


def build_review_items(project_dir: str, speaker_id: int | None, episode: str = "",
                       keyword: str = "", selected: bool | None = None) -> tuple[list[dict], int]:
    """Return chronological visual units, keeping approved training segments intact."""
    rows = query_clips(project_dir, speaker_id, episode, "", selected)
    clips_by_id = {row["id"]: row for row in rows}
    query = keyword.casefold().strip()
    items, covered = [], set()

    if speaker_id is not None and selected is not False:
        for segment in get_training_segments(project_dir, speaker_id, episode):
            if segment["status"] != "approved" or segment["clip_count"] < 2:
                continue
            members = [clips_by_id.get(clip_id) for clip_id in segment["clip_ids"]]
            if not members or any(member is None for member in members):
                continue
            if query and not any(query in member["text"].casefold() for member in members):
                continue
            items.append({"type": "segment", "segment": segment, "clips": members,
                          "episode": segment["episode"], "start": segment["start"]})
            covered.update(segment["clip_ids"])

    for row in rows:
        if row["id"] in covered:
            continue
        if query and query not in row["text"].casefold():
            continue
        items.append({"type": "clip", "clip": row, "episode": row["episode"], "start": row["start"]})

    return sorted(items, key=lambda item: (item["episode"], item["start"], item["type"])), len(rows)
