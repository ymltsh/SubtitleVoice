import os
import yaml

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
CONFIG_DIR = os.path.join(ROOT, "workspace")
CONFIG_FILE = os.path.join(CONFIG_DIR, "config.yaml")

DEFAULT_CONFIG = {
    "version": "0.1",
    "ffmpeg": {
        "path": "",
    },
}


def _load() -> dict:
    if os.path.isfile(CONFIG_FILE):
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        # Merge with defaults for missing keys
        merged = DEFAULT_CONFIG.copy()
        _deep_merge(merged, data)
        return merged
    return DEFAULT_CONFIG.copy()


def _save(cfg: dict):
    os.makedirs(CONFIG_DIR, exist_ok=True)
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        yaml.dump(cfg, f, allow_unicode=True, default_flow_style=False, sort_keys=False)


def _deep_merge(base: dict, override: dict):
    for k, v in override.items():
        if k in base and isinstance(base[k], dict) and isinstance(v, dict):
            _deep_merge(base[k], v)
        else:
            base[k] = v


def get(key: str, default=None):
    cfg = _load()
    parts = key.split(".")
    val = cfg
    for p in parts:
        if isinstance(val, dict):
            val = val.get(p, default)
        else:
            return default
    return val


def set(key: str, value):
    cfg = _load()
    parts = key.split(".")
    target = cfg
    for p in parts[:-1]:
        if p not in target:
            target[p] = {}
        target = target[p]
    target[parts[-1]] = value
    _save(cfg)


def all_config() -> dict:
    return _load()
