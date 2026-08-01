import os
import json
import subprocess
import zipfile
import shutil
from urllib.request import urlretrieve

from .config import get as cfg_get, set as cfg_set, all_config as cfg_all

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
DEFAULT_FFMPEG_DIR = os.path.join(ROOT, "dependencies", "ffmpeg")
DEFAULT_FFMPEG_EXE = os.path.join(DEFAULT_FFMPEG_DIR, "bin", "ffmpeg.exe")
DEFAULT_FFPROBE_EXE = os.path.join(DEFAULT_FFMPEG_DIR, "bin", "ffprobe.exe")

FFMPEG_URL = (
    "https://hub.wgen.top/https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/"
    "ffmpeg-master-latest-win64-gpl.zip"
)


def get_ffmpeg_path() -> str:
    custom = cfg_get("ffmpeg.path", "")
    if custom and os.path.isfile(custom):
        return custom
    if os.path.isfile(DEFAULT_FFMPEG_EXE):
        return DEFAULT_FFMPEG_EXE
    try:
        subprocess.run(["ffmpeg", "-version"], capture_output=True, encoding="utf-8", errors="replace", timeout=5)
        return "ffmpeg"
    except Exception:
        return ""


def get_ffprobe_path() -> str:
    custom = cfg_get("ffmpeg.path", "")
    if custom and os.path.isfile(custom):
        probe = os.path.join(os.path.dirname(custom), "ffprobe.exe")
        if os.path.isfile(probe):
            return probe
        return custom
    if os.path.isfile(DEFAULT_FFPROBE_EXE):
        return DEFAULT_FFPROBE_EXE
    try:
        subprocess.run(["ffprobe", "-version"], capture_output=True, encoding="utf-8", errors="replace", timeout=5)
        return "ffprobe"
    except Exception:
        return ""


def is_ffmpeg_ready() -> bool:
    return bool(get_ffmpeg_path())


def get_config() -> dict:
    return {
        "ffmpeg_path": cfg_get("ffmpeg.path", ""),
        "ffmpeg_source": _describe_source(cfg_get("ffmpeg.path", "")),
        "ready": is_ffmpeg_ready(),
        "active_path": get_ffmpeg_path() or "",
        "config": cfg_all(),
    }


def set_custom_ffmpeg(path: str):
    cfg_set("ffmpeg.path", path)


def _describe_source(custom_path: str) -> str:
    if custom_path and os.path.isfile(custom_path):
        return "custom"
    if os.path.isfile(DEFAULT_FFMPEG_EXE):
        return "dependencies"
    try:
        subprocess.run(["ffmpeg", "-version"], capture_output=True, timeout=5)
        return "system"
    except Exception:
        return "none"


def download_ffmpeg() -> dict:
    os.makedirs(DEFAULT_FFMPEG_DIR, exist_ok=True)
    zip_path = os.path.join(DEFAULT_FFMPEG_DIR, "ffmpeg.zip")

    try:
        urlretrieve(FFMPEG_URL, zip_path)
    except Exception as e:
        return {"error": f"下载失败: {e}"}

    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
            members = zf.namelist()
            prefix = os.path.commonpath([m for m in members if not m.endswith("/")])
            for f in os.listdir(DEFAULT_FFMPEG_DIR):
                fp = os.path.join(DEFAULT_FFMPEG_DIR, f)
                if os.path.isfile(fp):
                    os.remove(fp)
                elif os.path.isdir(fp) and f != "bin":
                    shutil.rmtree(fp, ignore_errors=True)
            zf.extractall(DEFAULT_FFMPEG_DIR)
            extracted = os.path.join(DEFAULT_FFMPEG_DIR, prefix)
            if os.path.isdir(extracted) and extracted != DEFAULT_FFMPEG_DIR:
                for item in os.listdir(extracted):
                    src = os.path.join(extracted, item)
                    dst = os.path.join(DEFAULT_FFMPEG_DIR, item)
                    if os.path.exists(dst):
                        if os.path.isdir(dst):
                            shutil.rmtree(dst, ignore_errors=True)
                        else:
                            os.remove(dst)
                    shutil.move(src, dst)
                shutil.rmtree(extracted, ignore_errors=True)
    except Exception as e:
        return {"error": f"解压失败: {e}"}
    finally:
        if os.path.exists(zip_path):
            os.remove(zip_path)

    if os.path.isfile(DEFAULT_FFMPEG_EXE):
        return {"ok": True, "path": DEFAULT_FFMPEG_EXE}
    return {"error": "安装后未找到 ffmpeg.exe"}


def probe_subtitle_tracks(video_path: str) -> list:
    ffprobe = get_ffprobe_path()
    if not ffprobe:
        return []

    try:
        result = subprocess.run(
            [ffprobe, "-v", "quiet", "-print_format", "json",
             "-show_streams", "-select_streams", "s", video_path],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=30,
        )
        data = json.loads(result.stdout)
        tracks = []
        sub_counter = 0
        for s in data.get("streams", []):
            idx = s.get("index", -1)
            codec = s.get("codec_name", "unknown")
            lang = s.get("tags", {}).get("language", "und")
            title = s.get("tags", {}).get("title", "")
            tracks.append({
                "index": idx,
                "sub_index": sub_counter,
                "codec": codec,
                "language": lang,
                "title": title,
                "label": f"Track {sub_counter}: {lang}" + (f" ({title})" if title else ""),
            })
            sub_counter += 1
        return tracks
    except Exception:
        return []


def extract_subtitle(video_path: str, output_dir: str, track_index: int = 0) -> list:
    ffmpeg = get_ffmpeg_path()
    if not ffmpeg:
        raise RuntimeError("FFmpeg 未安装")

    os.makedirs(output_dir, exist_ok=True)
    base = os.path.splitext(os.path.basename(video_path))[0]
    # track_index is the subtitle-relative index (0, 1, 2...), not global stream index
    map_spec = f"0:s:{track_index}"

    errors = []

    # Try 1: copy codec as .ass
    out_path = os.path.join(output_dir, f"{base}.track{track_index}.ass")
    r = subprocess.run(
        [ffmpeg, "-y", "-i", video_path, "-map", map_spec, "-c:s", "copy", out_path],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60,
    )
    if r.returncode == 0 and os.path.exists(out_path):
        return [out_path]
    errors.append(f"[copy .ass] {_clean_ffmpeg_stderr(r.stderr)}")

    # Try 2: copy codec as .srt
    out_path_srt = os.path.join(output_dir, f"{base}.track{track_index}.srt")
    r2 = subprocess.run(
        [ffmpeg, "-y", "-i", video_path, "-map", map_spec, "-c:s", "copy", out_path_srt],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60,
    )
    if r2.returncode == 0 and os.path.exists(out_path_srt):
        return [out_path_srt]
    errors.append(f"[copy .srt] {_clean_ffmpeg_stderr(r2.stderr)}")

    # Try 3: transcode to .ass (no -c:s copy)
    r3 = subprocess.run(
        [ffmpeg, "-y", "-i", video_path, "-map", map_spec, out_path],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60,
    )
    if r3.returncode == 0 and os.path.exists(out_path):
        return [out_path]
    errors.append(f"[transcode .ass] {_clean_ffmpeg_stderr(r3.stderr)}")

    # Try 4: transcode to .srt
    r4 = subprocess.run(
        [ffmpeg, "-y", "-i", video_path, "-map", map_spec, out_path_srt],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60,
    )
    if r4.returncode == 0 and os.path.exists(out_path_srt):
        return [out_path_srt]
    errors.append(f"[transcode .srt] {_clean_ffmpeg_stderr(r4.stderr)}")

    raise RuntimeError("FFmpeg 提取失败:\n" + "\n".join(errors))


def _clean_ffmpeg_stderr(stderr: str) -> str:
    """Strip ffmpeg banner and extract relevant error lines."""
    if not stderr:
        return ""
    lines = stderr.strip().split("\n")
    # Filter out banner/configuration lines
    cleaned = []
    for line in lines:
        s = line.strip()
        if not s:
            continue
        if s.startswith("ffmpeg version") or s.startswith("Copyright") or s.startswith("built with") or s.startswith("configuration:") or s.startswith("lib"):
            continue
        if "Press [q] to stop" in s:
            continue
        if "Output #0" in s or "Stream #0" in s:
            continue
        if "video:" in s and "audio:" in s and "subtitle:" in s:
            continue  # summary line
        cleaned.append(s)
    return "\n".join(cleaned) or stderr.strip()
