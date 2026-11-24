"""Persistent configuration management for OnePost."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
VENDOR_SAU_DIR = PROJECT_ROOT / "vendor" / "social-auto-upload"
DATA_DIR = PROJECT_ROOT / "data"
UPLOADS_STAGING_DIR = DATA_DIR / "staging"
COVERS_CACHE_DIR = DATA_DIR / "covers"
JOBS_FILE = DATA_DIR / "jobs_history.json"
CONFIG_FILE = DATA_DIR / "settings.json"

for _folder in (DATA_DIR, UPLOADS_STAGING_DIR, COVERS_CACHE_DIR):
    _folder.mkdir(parents=True, exist_ok=True)


def detect_default_chrome() -> str:
    candidates = [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    ]
    for path in candidates:
        if path and Path(path).is_file():
            return Path(path).as_posix()
    return ""


DEFAULT_SETTINGS: dict[str, Any] = {
    "chrome_path": detect_default_chrome(),
    "headless_upload": False,  # Default visible window for maximum transparency & anti-bot safety
    "default_watch_dir": "",
    "accounts": {
        "douyin": "creator",
        "shipinhao": "creator",
        "weibo": "creator",
        "x": "creator",
    },
    "network": {
        "cn_direct_bypass_proxy": True,  # Ensures Douyin/Shipinhao/Weibo never leak overseas proxy IP
        "x_proxy_url": os.environ.get("HTTPS_PROXY") or os.environ.get("HTTP_PROXY") or "",
        "x_use_proxy": False,  # Default False for TUN / transparent system routing; enable in Settings if using port proxy
    },
    "x_config": {
        "mode": "browser",  # "browser" (Playwright cookie session) or "api" (Official X API v2 + v1.1 media)
        "is_premium": True,  # 3-5 min highlight videos require X Premium (standard tier caps at 140s)
        "api_key": "",
        "api_secret": "",
        "access_token": "",
        "access_token_secret": "",
        "bearer_token": "",
    },
    "multipost": {
        "enabled": False,
        "api_url": "http://127.0.0.1:10721/api/post",
        "api_key": "",
    },
    "ai": {
        "provider": "openai_compatible",
        "base_url": os.environ.get("OPENAI_BASE_URL", "https://api.deepseek.com/v1"),
        "api_key": os.environ.get("OPENAI_API_KEY", ""),
        "model": os.environ.get("OPENAI_MODEL", "deepseek-chat"),
        "brand_signature_cn": "",
        "brand_signature_en": "",
    },
}


def _deep_merge(defaults: dict[str, Any], overrides: dict[str, Any]) -> dict[str, Any]:
    merged = dict(defaults)
    for k, v in overrides.items():
        if isinstance(v, dict) and isinstance(merged.get(k), dict):
            merged[k] = _deep_merge(merged[k], v)
        else:
            merged[k] = v
    return merged


def load_settings() -> dict[str, Any]:
    if not CONFIG_FILE.is_file():
        save_settings(DEFAULT_SETTINGS)
        return dict(DEFAULT_SETTINGS)
    try:
        raw = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        return _deep_merge(DEFAULT_SETTINGS, raw)
    except Exception:
        return dict(DEFAULT_SETTINGS)


def save_settings(new_settings: dict[str, Any]) -> dict[str, Any]:
    merged = _deep_merge(DEFAULT_SETTINGS, new_settings)
    CONFIG_FILE.write_text(json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8")
    _sync_sau_conf(merged)
    return merged


def _sync_sau_conf(settings: dict[str, Any]) -> None:
    """Keep vendor/social-auto-upload/conf.py aligned with user settings."""
    if not VENDOR_SAU_DIR.is_dir():
        return
    chrome_path = (settings.get("chrome_path") or "").replace("\\", "/")
    headless = bool(settings.get("headless_upload", False))
    conf_path = VENDOR_SAU_DIR / "conf.py"
    content = (
        "from pathlib import Path\n\n"
        "BASE_DIR = Path(__file__).parent.resolve()\n"
        'XHS_SERVER = "http://127.0.0.1:11901"\n'
        f'LOCAL_CHROME_PATH = "{chrome_path}"\n'
        f"LOCAL_CHROME_HEADLESS = {headless}\n"
        "DEBUG_MODE = True\n"
        "YT_PROXY = None\n"
    )
    conf_path.write_text(content, encoding="utf-8")


# Fresh clones have no vendor conf.py (it is machine-specific and gitignored): generate it on import.
if VENDOR_SAU_DIR.is_dir() and not (VENDOR_SAU_DIR / "conf.py").is_file():
    _sync_sau_conf(load_settings())
