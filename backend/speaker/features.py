"""Prepare audio features for an analysis snapshot across multiple sources."""
from collections import defaultdict
import os
from typing import Callable

from ..database import get_clips, get_reference_clips
from .cache import build_embedding_cache, build_wav_cache


class FeaturePreparationError(RuntimeError):
    pass


def prepare_analysis_features(project_dir: str, speaker_id: int, target_episode: str,
                              episode_videos: dict[str, str],
                              progress_callback: Callable[[str, int, int], None] | None = None) -> dict:
    """Ensure current reference and target clips have WAVs and embeddings.

    References are grouped by their own source episode, not the target episode.
    This makes a role trained from episode A usable for an initial analysis of
    episode B without an accidental cache warm-up requirement.
    """
    references = get_reference_clips(project_dir, speaker_id)
    if not references:
        raise FeaturePreparationError("请先添加至少一个参考素材")
    targets = get_clips(project_dir, target_episode)
    if not targets:
        raise FeaturePreparationError("当前素材没有可分析的字幕片段")

    grouped: dict[str, set[int]] = defaultdict(set)
    reference_ids = {clip.id for clip in references}
    for clip in references:
        grouped[clip.episode].add(clip.id)
    for clip in targets:
        grouped[clip.episode].add(clip.id)

    missing_sources = sorted(episode for episode in grouped
                             if not episode_videos.get(episode) or not os.path.isfile(episode_videos[episode]))
    if missing_sources:
        raise FeaturePreparationError("参考或目标素材缺少视频路径：" + "、".join(missing_sources))

    ordered_episodes = sorted(grouped, key=lambda episode: (episode == target_episode, episode))
    total = sum(len(grouped[episode]) for episode in ordered_episodes)
    completed = 0
    wav_generated = wav_skipped = 0
    for episode in ordered_episodes:
        clip_ids = grouped[episode]
        source_label = "参考素材" if episode != target_episode else "当前素材"
        if progress_callback:
            progress_callback(f"准备{source_label}：{episode}", completed, total)
        result = build_wav_cache(
            project_dir, episode, episode_videos[episode], clip_ids=clip_ids,
            progress_callback=lambda current, _group_total, _state, label=source_label, ep=episode, offset=completed:
                progress_callback(f"准备{label}：{ep}", offset + current, total) if progress_callback else None,
        )
        if result.get("errors"):
            raise FeaturePreparationError(f"{source_label}“{episode}”有 {result['errors']} 个音频片段生成失败")
        completed += len(clip_ids)
        wav_generated += result["generated"]
        wav_skipped += result["skipped"]

    all_clip_ids = set().union(*grouped.values())
    if progress_callback:
        progress_callback("计算声纹特征", 0, len(all_clip_ids))
    embedding = build_embedding_cache(
        project_dir, clip_ids=all_clip_ids,
        progress_callback=lambda current, embed_total, _state:
            progress_callback("计算声纹特征", current, embed_total) if progress_callback else None,
    )
    if embedding.get("error"):
        raise FeaturePreparationError(embedding["error"])
    if embedding.get("errors"):
        raise FeaturePreparationError(f"有 {embedding['errors']} 个声纹特征计算失败")

    return {
        "reference_clip_count": len(reference_ids),
        "target_clip_count": len(targets),
        "source_episodes": ordered_episodes,
        "wav_generated": wav_generated,
        "wav_skipped": wav_skipped,
        "embedding_generated": embedding["generated"],
        "embedding_skipped": embedding["skipped"],
    }
