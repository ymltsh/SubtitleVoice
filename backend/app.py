import json
import os
import re
import shutil
import subprocess
import threading
from datetime import datetime

from flask import Flask, jsonify, request, send_file
from flask_cors import CORS

from .database import (STATUS_DIRTY, STATUS_FAILED, STATUS_IDLE, STATUS_RUNNING,
    STATUS_SUCCESS, bump_reference_version, clear_training_segments, delete_clips_by_episode, get_clip,
    get_db, get_episode_stats, get_episodes, get_prototype_status,
    get_speaker_analysis, get_speaker_analysis_by_episode, get_speaker_stats,
    init_project_db, insert_clips_batch, mark_analyses_dirty,
    mark_prototype_dirty, query_clips, scan_material_pairs, set_clip_speaker,
    set_clip_trim, set_prototype_status, set_speaker_analysis)
from .exporter import export_gpt_sovits
from .ffmpeg_utils import get_ffmpeg_path
from .models import Clip
from .parser import parse_subtitle
from .speaker.manager import (add_reference, create_speaker, delete_speaker,
    get_references, get_speaker, get_speakers, remove_reference)
from .segments import build_review_items, build_training_segment_candidates, get_export_items
from .database import get_training_segments, set_training_segment_status

app = Flask(__name__, static_folder="../frontend", static_url_path="")
CORS(app)
ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
WORKSPACE = os.path.join(ROOT, "workspace")
VST_VERSION = "0.3"
_jobs: dict[str, dict] = {}
_jobs_lock = threading.Lock()


def _project_dir(name):
    return os.path.join(WORKSPACE, re.sub(r'[<>:"/\\|?*]', "_", name))


def _vst_path(directory): return os.path.join(directory, "project.vst")
def _resolve(name):
    directory = _project_dir(name)
    return directory if os.path.isfile(_vst_path(directory)) else ""
def _read(directory):
    with open(_vst_path(directory), encoding="utf-8") as f: return json.load(f)
def _write(directory, data):
    with open(_vst_path(directory), "w", encoding="utf-8") as f: json.dump(data, f, ensure_ascii=False, indent=2)
def _error(message, status=400): return jsonify({"error": message}), status
def _project_or_404(name):
    directory = _resolve(name)
    return directory or None


def _episode_config(directory, episode):
    return next((item for item in _read(directory).get("episodes", []) if item["name"] == episode), None)


def _import_episode(directory, episode, force=False):
    if episode in get_episodes(directory) and not force:
        return {"episode": episode, "clip_count": len(query_clips(directory, episode=episode)), "skipped": True}
    item = _episode_config(directory, episode)
    if not item or not os.path.isfile(item["subtitle"]): raise ValueError("字幕文件不存在")
    lines = parse_subtitle(item["subtitle"])
    delete_clips_by_episode(directory, episode)
    insert_clips_batch(directory, [Clip(episode=episode, start=line.start, end=line.end, text=line.text) for line in lines])
    return {"episode": episode, "clip_count": len(lines), "skipped": False}


def _set_job(key, **changes):
    with _jobs_lock:
        _jobs.setdefault(key, {"status": STATUS_DIRTY, "step": "", "current": 0, "total": 0, "error": ""})
        _jobs[key].update(changes)


def _run_analysis(directory, speaker_id, episode, threshold):
    key = f"{directory}:{speaker_id}:{episode}"
    proto_key = f"{directory}:proto:{speaker_id}"
    try:
        from .speaker.cache import build_embedding_cache, build_wav_cache
        from .speaker.prototype import build_prototype
        from .speaker.retrieval import retrieve
        cfg = _read(directory)
        episode_cfg = next((item for item in cfg.get("episodes", []) if item["name"] == episode), None)
        if not episode_cfg:
            raise ValueError("素材不存在")
        set_speaker_analysis(directory, speaker_id, episode, threshold, 0, 0, STATUS_RUNNING)
        _set_job(key, status=STATUS_RUNNING, step="准备音频", current=0, total=1)
        _import_episode(directory, episode)
        result = build_wav_cache(directory, episode, episode_cfg["video"],
            progress_callback=lambda current, total, _state: _set_job(key, status=STATUS_RUNNING, step="准备音频", current=current, total=total))
        if result.get("errors"): raise RuntimeError("部分音频缓存生成失败")
        _set_job(key, status=STATUS_RUNNING, step="分析语音", current=0, total=1)
        embedded = build_embedding_cache(directory)
        if embedded.get("error"): raise RuntimeError(embedded["error"])
        _set_job(key, status=STATUS_RUNNING, step="匹配候选", current=0, total=1)
        from .database import get_db
        conn = get_db(directory)
        ref_ver = conn.execute("SELECT reference_version FROM speakers WHERE id=?", (speaker_id,)).fetchone()
        ref_ver = ref_ver["reference_version"] if ref_ver else 0
        conn.close()
        prev = get_prototype_status(directory, speaker_id)
        if prev["status"] == STATUS_SUCCESS and prev["reference_version"] == ref_ver:
            prototype = {"reference_count": prev.get("reference_count", 0)}
            _set_job(proto_key, status=STATUS_SUCCESS, step="Prototype 已缓存，跳过重建")
        else:
            set_prototype_status(directory, speaker_id, STATUS_RUNNING)
            _set_job(proto_key, status=STATUS_RUNNING, step="生成 Prototype")
            prototype = build_prototype(directory, speaker_id)
            if prototype.get("error"): raise RuntimeError("参考素材不可用于分析：" + prototype["error"])
            set_prototype_status(directory, speaker_id, STATUS_SUCCESS, ref_ver)
            _set_job(proto_key, status=STATUS_SUCCESS, step="Prototype 已生成")
            mark_analyses_dirty(directory, speaker_id, reason="prototype_changed")
        results = retrieve(directory, speaker_id, episode)
        conn = get_db(directory)
        try:
            conn.execute("""UPDATE training_segments SET status=?, updated_at=datetime('now', 'localtime')
                WHERE speaker_id=? AND episode=? AND status IN ('candidate', 'approved')""",
                ("stale", speaker_id, episode))
            conn.execute("UPDATE clips SET selected_speaker_id=NULL WHERE selected_speaker_id=? AND episode=?", (speaker_id, episode))
            ids = [(speaker_id, item["clip_id"]) for item in results if item["score"] >= threshold]
            conn.executemany("UPDATE clips SET selected_speaker_id=? WHERE id=? AND selected_speaker_id IS NULL AND episode=?", [(s, c, episode) for s, c in ids])
            conn.commit()
        finally: conn.close()
        selected = len(ids)
        set_speaker_analysis(directory, speaker_id, episode, threshold, len(results), selected, STATUS_SUCCESS)
        _set_job(key, status=STATUS_SUCCESS, step="完成", current=1, total=1, result={"candidates": len(results), "selected": selected, "threshold": threshold, "episode": episode})
    except Exception as error:
        set_speaker_analysis(directory, speaker_id, episode, threshold, 0, 0, STATUS_FAILED)
        set_prototype_status(directory, speaker_id, STATUS_FAILED)
        _set_job(key, status=STATUS_FAILED, error=str(error), step="失败")


@app.route("/")
def index(): return send_file(os.path.join(app.static_folder, "index.html"))


@app.route("/api/projects", methods=["GET"])
def list_projects():
    result = []
    os.makedirs(WORKSPACE, exist_ok=True)
    for folder in os.listdir(WORKSPACE):
        directory = os.path.join(WORKSPACE, folder)
        if os.path.isfile(_vst_path(directory)):
            try:
                item = _read(directory); item.update(folder=folder, episode_count=len(item.get("episodes", []))); result.append(item)
            except (OSError, ValueError): pass
    return jsonify(sorted(result, key=lambda item: item.get("created_at", ""), reverse=True))


@app.route("/api/projects", methods=["POST"])
def create_project_api():
    name = (request.get_json() or {}).get("name", "").strip()
    if not name: return _error("请输入项目名称")
    directory = _project_dir(name); root = directory; suffix = 2
    while os.path.exists(directory): directory = f"{root}_{suffix}"; suffix += 1
    os.makedirs(os.path.join(directory, "cache"), exist_ok=True); os.makedirs(os.path.join(directory, "export"), exist_ok=True)
    item = {"name": name, "version": VST_VERSION, "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "episodes": []}
    _write(directory, item); init_project_db(directory)
    item.update(folder=os.path.basename(directory), episode_count=0)
    return jsonify(item)


@app.route("/api/projects/<path:name>/open", methods=["POST"])
def open_project(name):
    directory = _project_or_404(name)
    if not directory: return _error("项目不存在", 404)
    init_project_db(directory); item = _read(directory)
    item.update(folder=os.path.basename(directory), project_dir=directory, episode_count=len(item.get("episodes", [])))
    return jsonify(item)


@app.route("/api/projects/<path:name>", methods=["DELETE"])
def delete_project_api(name):
    directory = _project_or_404(name)
    if not directory: return _error("项目不存在", 404)
    shutil.rmtree(directory); return jsonify({"ok": True})


@app.route("/api/projects/<path:name>/episodes", methods=["POST"])
def add_episode(name):
    directory = _project_or_404(name)
    if not directory: return _error("项目不存在", 404)
    data = request.get_json() or {}; video = data.get("video_path", "").strip(); subtitle = data.get("subtitle_path", "").strip(); episode = data.get("episode_name", "").strip()
    if not episode or not os.path.isfile(video) or not os.path.isfile(subtitle): return _error("请提供有效的视频、字幕与剧集名称")
    cfg = _read(directory)
    if any(item["name"] == episode for item in cfg["episodes"]): return _error("剧集名称已存在", 409)
    cfg["episodes"].append({"name": episode, "video": video, "subtitle": subtitle}); _write(directory, cfg)
    imported = _import_episode(directory, episode)
    return jsonify({"episode": episode, "clip_count": imported["clip_count"]})


@app.route("/api/projects/<path:name>/episodes/<path:episode>", methods=["DELETE"])
def delete_episode(name, episode):
    directory = _project_or_404(name)
    if not directory: return _error("项目不存在", 404)
    cfg = _read(directory); cfg["episodes"] = [item for item in cfg["episodes"] if item["name"] != episode]; _write(directory, cfg)
    delete_clips_by_episode(directory, episode); return jsonify({"ok": True})


@app.route("/api/episodes/import", methods=["POST"])
def import_episode():
    data = request.get_json() or {}; directory = _project_or_404(data.get("project", ""))
    if not directory: return _error("项目不存在", 404)
    try: return jsonify(_import_episode(directory, data.get("episode", ""), bool(data.get("force"))))
    except ValueError as error: return _error(str(error), 404)


@app.route("/api/sidebar")
def sidebar():
    directory = _project_or_404(request.args.get("project", ""))
    if not directory: return _error("项目不存在", 404)
    speaker_id = request.args.get("speaker_id", type=int)
    episodes = get_episode_stats(directory)
    analyses = get_speaker_analysis(directory) if speaker_id else []
    for ep in episodes:
        match = next((a for a in analyses if a["speaker_id"] == speaker_id and a["episode"] == ep["episode"]), None)
        if match:
            ep["analysis_status"] = match.get("status", "idle")
            ep["analyzed"] = match.get("status") == STATUS_SUCCESS
        else:
            ep["analysis_status"] = STATUS_IDLE
            ep["analyzed"] = False
    speakers = get_speaker_stats(directory)
    return jsonify({"speakers": speakers, "episodes": episodes})


@app.route("/api/speakers/<int:speaker_id>/prototype/status")
def prototype_status(speaker_id):
    directory = _project_or_404(request.args.get("project", ""))
    if not directory: return _error("项目不存在", 404)
    proto_key = f"{directory}:proto:{speaker_id}"
    with _jobs_lock:
        if _jobs.get(proto_key, {}).get("status") == STATUS_RUNNING:
            return jsonify(_jobs[proto_key])
    return jsonify(get_prototype_status(directory, speaker_id))


@app.route("/api/clips")
def clips():
    directory = _project_or_404(request.args.get("project", ""))
    if not directory: return _error("项目不存在", 404)
    speaker = request.args.get("speaker", type=int); selected_arg = request.args.get("selected")
    selected = None if selected_arg not in ("true", "false") else selected_arg == "true"
    rows = query_clips(directory, speaker, request.args.get("episode", ""), request.args.get("keyword", ""), selected)
    scores = {}
    if speaker:
        from .speaker.retrieval import get_scores
        scores = get_scores(directory, speaker, request.args.get("episode", ""))
        minimum = request.args.get("score", type=float)
        if minimum is not None: rows = [row for row in rows if scores.get(row["id"], -1) >= minimum]
    for row in rows:
        row["score"] = scores.get(row["id"]) if speaker else None
        es = row["start"] + row.get("trim_start", 0.0)
        ee = row["end"] + row.get("trim_end", 0.0)
        row["effective_start"] = round(es, 3)
        row["effective_end"] = round(ee, 3)
        row["duration"] = round(ee - es, 3)
    page = max(1, request.args.get("page", 1, type=int)); limit = min(500, max(1, request.args.get("limit", 100, type=int))); total = len(rows)
    return jsonify({"clips": rows[(page-1)*limit:page*limit], "total": total, "page": page, "limit": limit, "total_pages": max(1, (total + limit - 1) // limit)})


def _add_clip_display_fields(row, scores: dict, speaker_id: int | None):
    row["score"] = scores.get(row["id"]) if speaker_id else None
    es = row["start"] + row.get("trim_start", 0.0)
    ee = row["end"] + row.get("trim_end", 0.0)
    row["effective_start"] = round(es, 3)
    row["effective_end"] = round(ee, 3)
    row["duration"] = round(ee - es, 3)


@app.route("/api/review-items")
def review_items():
    directory = _project_or_404(request.args.get("project", ""))
    if not directory: return _error("项目不存在", 404)
    speaker = request.args.get("speaker", type=int)
    selected_arg = request.args.get("selected")
    selected = None if selected_arg not in ("true", "false") else selected_arg == "true"
    items, total_clips = build_review_items(directory, speaker, request.args.get("episode", ""),
                                            request.args.get("keyword", ""), selected)
    scores = {}
    if speaker:
        from .speaker.retrieval import get_scores
        scores = get_scores(directory, speaker, request.args.get("episode", ""))
    for item in items:
        if item["type"] == "segment":
            for row in item["clips"]:
                _add_clip_display_fields(row, scores, speaker)
        else:
            _add_clip_display_fields(item["clip"], scores, speaker)
    page = max(1, request.args.get("page", 1, type=int))
    limit = min(500, max(1, request.args.get("limit", 100, type=int)))
    total = len(items)
    return jsonify({
        "items": items[(page-1)*limit:page*limit], "total": total, "total_clips": total_clips,
        "page": page, "limit": limit, "total_pages": max(1, (total + limit - 1) // limit),
    })


@app.route("/api/clips/<int:clip_id>/selection", methods=["PATCH"])
def select_clip(clip_id):
    data = request.get_json() or {}; directory = _project_or_404(data.get("project", ""))
    if not directory: return _error("项目不存在", 404)
    speaker_id = data.get("selected_speaker_id")
    if speaker_id is not None and not get_speaker(directory, int(speaker_id)): return _error("角色不存在", 404)
    set_clip_speaker(directory, clip_id, int(speaker_id) if speaker_id is not None else None)
    return jsonify({"ok": True})


@app.route("/api/clips/<int:clip_id>/trim", methods=["PATCH"])
def trim_clip(clip_id):
    data = request.get_json() or {}
    directory = _project_or_404(data.get("project", ""))
    if not directory: return _error("项目不存在", 404)
    clip = get_clip(directory, clip_id)
    if not clip: return _error("片段不存在", 404)
    trim_start = float(data.get("trim_start", 0))
    trim_end = float(data.get("trim_end", 0))
    trim_start = max(-2.0, min(2.0, trim_start))
    trim_end = max(-2.0, min(2.0, trim_end))
    if clip.end + trim_end <= clip.start + trim_start:
        return _error("微调后的片段时长必须大于 0")
    set_clip_trim(directory, clip_id, round(trim_start, 3), round(trim_end, 3))
    conn = get_db(directory)
    try:
        ref_rows = conn.execute(
            "SELECT DISTINCT speaker_id FROM speaker_references WHERE clip_id=?", (clip_id,)
        ).fetchall()
    finally:
        conn.close()
    if ref_rows:
        from .speaker.manager import get_speaker
        for ref_row in ref_rows:
            sid = ref_row["speaker_id"]
            bump_reference_version(directory, sid)
            mark_prototype_dirty(directory, sid)
    return jsonify({"ok": True})


@app.route("/api/speakers/<int:speaker_id>/training-segments")
def training_segments(speaker_id):
    directory = _project_or_404(request.args.get("project", ""))
    if not directory: return _error("项目不存在", 404)
    if not get_speaker(directory, speaker_id): return _error("角色不存在", 404)
    return jsonify(get_training_segments(directory, speaker_id, request.args.get("episode", "")))


@app.route("/api/speakers/<int:speaker_id>/training-segments/generate", methods=["POST"])
def generate_training_segments(speaker_id):
    data = request.get_json() or {}
    directory = _project_or_404(data.get("project", ""))
    if not directory: return _error("项目不存在", 404)
    if not get_speaker(directory, speaker_id): return _error("角色不存在", 404)
    episode = str(data.get("episode", "")).strip()
    if not episode or not _episode_config(directory, episode): return _error("请指定有效素材")
    try:
        result = build_training_segment_candidates(
            directory, speaker_id, episode,
            float(data.get("min_duration", 3.0)),
            float(data.get("max_duration", 10.0)),
            float(data.get("max_gap", 0.35)),
        )
        return jsonify(result)
    except ValueError as error:
        return _error(str(error))


@app.route("/api/speakers/<int:speaker_id>/training-segments", methods=["DELETE"])
def clear_speaker_training_segments(speaker_id):
    data = request.get_json() or {}
    directory = _project_or_404(data.get("project", ""))
    if not directory: return _error("项目不存在", 404)
    if not get_speaker(directory, speaker_id): return _error("角色不存在", 404)
    episode = str(data.get("episode", "")).strip()
    if not episode or not _episode_config(directory, episode): return _error("请指定有效素材")
    return jsonify({"deleted": clear_training_segments(directory, speaker_id, episode)})


@app.route("/api/training-segments/<int:segment_id>/status", methods=["PATCH"])
def update_training_segment_status(segment_id):
    data = request.get_json() or {}
    directory = _project_or_404(data.get("project", ""))
    if not directory: return _error("项目不存在", 404)
    try:
        if not set_training_segment_status(directory, segment_id, data.get("status", "")):
            return _error("训练片段不存在", 404)
        return jsonify({"ok": True})
    except ValueError as error:
        return _error(str(error))


@app.route("/api/speakers", methods=["GET", "POST"])
def speakers():
    project = request.args.get("project", "") if request.method == "GET" else (request.get_json() or {}).get("project", "")
    directory = _project_or_404(project)
    if not directory: return _error("项目不存在", 404)
    if request.method == "GET": return jsonify(get_speakers(directory))
    data = request.get_json() or {}; name = data.get("name", "").strip()
    if not name: return _error("请输入角色名称")
    try: return jsonify(create_speaker(directory, name, data.get("color", "#0ea5e9")))
    except Exception: return _error("角色名称已存在", 409)


@app.route("/api/speakers/<int:speaker_id>", methods=["GET", "DELETE"])
def speaker(speaker_id):
    directory = _project_or_404(request.args.get("project", ""))
    if not directory: return _error("项目不存在", 404)
    if request.method == "DELETE": delete_speaker(directory, speaker_id); return jsonify({"ok": True})
    item = get_speaker(directory, speaker_id)
    return jsonify(item) if item else _error("角色不存在", 404)


@app.route("/api/speakers/<int:speaker_id>/references", methods=["GET", "POST"])
def references(speaker_id):
    project = request.args.get("project", "") if request.method == "GET" else (request.get_json() or {}).get("project", "")
    directory = _project_or_404(project)
    if not directory: return _error("项目不存在", 404)
    if request.method == "GET": return jsonify(get_references(directory, speaker_id))
    clip_id = (request.get_json() or {}).get("clip_id")
    if not get_clip(directory, clip_id): return _error("片段不存在", 404)
    add_reference(directory, speaker_id, clip_id); return jsonify({"ok": True})


@app.route("/api/speakers/<int:speaker_id>/references/<int:clip_id>", methods=["DELETE"])
def reference(speaker_id, clip_id):
    directory = _project_or_404(request.args.get("project", ""))
    if not directory: return _error("项目不存在", 404)
    remove_reference(directory, speaker_id, clip_id); return jsonify({"ok": True})


@app.route("/api/speakers/<int:speaker_id>/analyze", methods=["POST"])
def analyze(speaker_id):
    data = request.get_json() or {}; directory = _project_or_404(data.get("project", ""))
    if not directory: return _error("项目不存在", 404)
    episode = data.get("episode", "").strip()
    if not episode: return _error("请指定素材")
    if not get_speaker(directory, speaker_id): return _error("角色不存在", 404)
    if not get_references(directory, speaker_id): return _error("请先添加至少一个参考素材")
    threshold = max(-1.0, min(1.0, float(data.get("threshold", 0.4))))
    key = f"{directory}:{speaker_id}:{episode}"
    with _jobs_lock:
        if _jobs.get(key, {}).get("status") == STATUS_RUNNING: return _error("该角色正在对此素材进行 AI 查找", 409)
    _set_job(key, status=STATUS_RUNNING, step="开始 AI 查找", current=0, total=1)
    threading.Thread(target=_run_analysis, args=(directory, speaker_id, episode, threshold), daemon=True).start()
    return jsonify({"ok": True})


@app.route("/api/speakers/<int:speaker_id>/analyze/status")
def analyze_status(speaker_id):
    directory = _project_or_404(request.args.get("project", ""))
    if not directory: return _error("项目不存在", 404)
    episode = request.args.get("episode", "")
    analysis_key = f"{directory}:{speaker_id}:{episode}" if episode else ""
    proto_key = f"{directory}:proto:{speaker_id}"
    with _jobs_lock:
        if analysis_key and _jobs.get(analysis_key, {}).get("status") == STATUS_RUNNING:
            return jsonify(_jobs[analysis_key])
        if _jobs.get(proto_key, {}).get("status") == STATUS_RUNNING:
            return jsonify({"status": STATUS_DIRTY, "step": "Prototype 正在生成", "reason": "prototype_running"})
    saved = get_speaker_analysis_by_episode(directory, speaker_id, episode) if episode else None
    if saved:
        return jsonify({
            "status": saved.get("status", STATUS_IDLE),
            "step": saved["status"] if saved.get("status") != STATUS_SUCCESS else "完成",
            "result": {"candidates": saved["clip_count"], "selected": saved["selected_count"], "threshold": saved["threshold"], "episode": saved["episode"]},
            "reason": saved.get("reason", "")
        })
    return jsonify({"status": STATUS_IDLE, "step": "尚未运行"})


@app.route("/api/speakers/<int:speaker_id>/export", methods=["POST"])
def export_speaker(speaker_id):
    data = request.get_json() or {}; directory = _project_or_404(data.get("project", ""))
    if not directory: return _error("项目不存在", 404)
    speaker_obj = get_speaker(directory, speaker_id)
    if not speaker_obj: return _error("角色不存在", 404)
    require_success = data.get("require_success", False)
    rows = get_export_items(directory, speaker_id)
    exported_eps = []; skipped_eps = []
    if require_success:
        analyses = get_speaker_analysis(directory)
        success_eps = {a["episode"] for a in analyses if a["speaker_id"] == speaker_id and a["status"] == STATUS_SUCCESS}
        exported_rows = [r for r in rows if r.episode in success_eps]
        all_eps = sorted(set(r.episode for r in rows))
        exported_eps = sorted(success_eps & set(r.episode for r in rows))
        skipped_eps = [e for e in all_eps if e not in success_eps]
    else:
        exported_rows = rows
        exported_eps = sorted(set(r.episode for r in rows))
    output = os.path.join(directory, "export", re.sub(r'[<>:"/\\|?*]', "_", speaker_obj["name"]))
    ep_videos = {item["name"]: item["video"] for item in _read(directory).get("episodes", [])}
    language = (data.get("language") or "ZH").strip().upper()
    skip_short = bool(data.get("skip_short", False))
    result = export_gpt_sovits(directory, exported_rows, output, ep_videos, language=language, skip_short=skip_short)
    result["exported_episodes"] = exported_eps
    result["skipped_episodes"] = skipped_eps
    result["ffmpeg_available"] = bool(get_ffmpeg_path())
    # include first 5 errors for debugging
    result["error_sample"] = result.get("errors", [])[:5]
    return jsonify(result)


@app.route("/api/speakers/<int:speaker_id>/export/summary")
def export_summary(speaker_id):
    directory = _project_or_404(request.args.get("project", ""))
    if not directory: return _error("项目不存在", 404)
    speaker_obj = get_speaker(directory, speaker_id)
    if not speaker_obj: return _error("角色不存在", 404)
    analyses = get_speaker_analysis(directory)
    export_items = get_export_items(directory, speaker_id)
    ep_analyses = {a["episode"]: a for a in analyses if a["speaker_id"] == speaker_id}
    episodes = []; total = 0; success_total = 0; dirty_total = 0
    for ep_cfg in _read(directory).get("episodes", []):
        ep_name = ep_cfg["name"]
        a = ep_analyses.get(ep_name, {})
        status = a.get("status", STATUS_IDLE)
        selected = sum(1 for item in export_items if item.episode == ep_name)
        episodes.append({"episode": ep_name, "status": status, "selected_count": selected})
        total += selected
        if status == STATUS_SUCCESS: success_total += selected
        elif status == STATUS_DIRTY: dirty_total += selected
    return jsonify({
        "speaker": speaker_obj["name"],
        "episodes": episodes,
        "total_selected": total,
        "success_selected": success_total,
        "dirty_selected": dirty_total
    })


@app.route("/api/projects/<path:name>/export-summary")
def batch_export_summary(name):
    directory = _project_or_404(name)
    if not directory: return _error("项目不存在", 404)
    speakers = get_speakers(directory)
    analyses = get_speaker_analysis(directory)
    result = []; total_clips = 0
    for s in speakers:
        ep_analyses = {a["episode"]: a for a in analyses if a["speaker_id"] == s["id"]}
        export_items = get_export_items(directory, s["id"])
        episodes = []; sp_total = 0; sp_success = 0; sp_dirty = 0
        for ep_cfg in _read(directory).get("episodes", []):
            ep_name = ep_cfg["name"]
            a = ep_analyses.get(ep_name, {})
            status = a.get("status", STATUS_IDLE)
            selected = sum(1 for item in export_items if item.episode == ep_name)
            episodes.append({"episode": ep_name, "status": status, "selected_count": selected})
            sp_total += selected
            if status == STATUS_SUCCESS: sp_success += selected
            elif status == STATUS_DIRTY: sp_dirty += selected
        result.append({
            "speaker": s["name"], "speaker_id": s["id"],
            "episodes": episodes,
            "total_selected": sp_total,
            "success_selected": sp_success,
            "dirty_selected": sp_dirty
        })
        total_clips += sp_total
    return jsonify({"speakers": result, "total_speakers": len(speakers), "total_clips": total_clips})


@app.route("/api/projects/<path:name>/export-all", methods=["POST"])
def export_all(name):
    directory = _project_or_404(name)
    if not directory: return _error("项目不存在", 404)
    data = request.get_json() or {}
    require_success = data.get("require_success", False)
    language = (data.get("language") or "ZH").strip().upper()
    skip_short = bool(data.get("skip_short", False))
    speakers = get_speakers(directory)
    analyses = get_speaker_analysis(directory)
    results = []; total_wavs = 0
    for s in speakers:
        rows = get_export_items(directory, s["id"])
        skipped = []
        if require_success:
            success_eps = {a["episode"] for a in analyses if a["speaker_id"] == s["id"] and a["status"] == STATUS_SUCCESS}
            exported_rows = [r for r in rows if r.episode in success_eps]
            all_eps = sorted(set(r.episode for r in rows))
            skipped = [e for e in all_eps if e not in success_eps]
        else:
            exported_rows = rows
        if exported_rows:
            output = os.path.join(directory, "export", re.sub(r'[<>:"/\\|?*]', "_", s["name"]))
            ep_videos = {item["name"]: item["video"] for item in _read(directory).get("episodes", [])}
            r = export_gpt_sovits(directory, exported_rows, output, ep_videos, language=language, skip_short=skip_short)
            results.append({"speaker": s["name"], "wav_count": r["wav_count"], "error_count": r.get("error_count", 0), "skipped_short": r.get("skipped_short", 0), "skipped_episodes": skipped, "output_dir": r["output_dir"]})
            total_wavs += r["wav_count"]
        else:
            results.append({"speaker": s["name"], "wav_count": 0, "skipped_episodes": skipped, "output_dir": None})
    return jsonify({"speakers": results, "total_speakers": len(results), "total_wavs": total_wavs})


@app.route("/api/video")
def video():
    path = request.args.get("path", "")
    return send_file(path, conditional=True) if os.path.isfile(path) else _error("视频不存在", 404)


@app.route("/api/pick-file")
def pick_file_api():
    ft = request.args.get("type", "all")
    if ft == "video":
        flt = "视频文件 (*.mp4;*.mkv;*.avi;*.mov;*.flv;*.wmv)|*.mp4;*.mkv;*.avi;*.mov;*.flv;*.wmv|所有文件 (*.*)|*.*"
    elif ft == "subtitle":
        flt = "字幕文件 (*.ass;*.srt;*.ssa;*.vtt)|*.ass;*.srt;*.ssa;*.vtt|所有文件 (*.*)|*.*"
    else:
        flt = "所有文件 (*.*)|*.*"
    ps = f'''Add-Type -AssemblyName System.Windows.Forms
$d = New-Object System.Windows.Forms.OpenFileDialog
$d.Filter = "{flt}"
$d.Title = "选择文件"
if ($d.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) {{ $d.FileName }} else {{ "" }}'''
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True, timeout=30)
        path = r.stdout.strip()
        name = os.path.splitext(os.path.basename(path))[0] if path else ""
        return jsonify({"path": path, "name": name})
    except Exception:
        return jsonify({"path": "", "name": ""})


def main():
    os.makedirs(WORKSPACE, exist_ok=True)
    app.run(host="0.0.0.0", port=8766, debug=True, threaded=True, use_reloader=False)


if __name__ == "__main__": main()
