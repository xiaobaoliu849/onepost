"""In-Page Headless QR Code Login Session Manager for Douyin, Shipinhao, and Weibo."""
from __future__ import annotations

import asyncio
import base64
import os
import sys
import time
from pathlib import Path
from typing import Any

from app.config import VENDOR_SAU_DIR, load_settings

if str(VENDOR_SAU_DIR) not in sys.path:
    sys.path.insert(0, str(VENDOR_SAU_DIR))

QR_SESSIONS: dict[str, dict[str, Any]] = {}
QR_TASKS: dict[str, asyncio.Task] = {}

SAU_MAP = {
    "douyin": "douyin",
    "shipinhao": "tencent",
    "weibo": "weibo",
}

APP_HINTS = {
    "douyin": "Open the Douyin (抖音) App on your phone and scan this QR code",
    "shipinhao": "Open WeChat (微信) on your phone and scan to authorize Video Account (视频号助手)",
    "weibo": "Open the Weibo (微博) App on your phone and scan this QR code",
}


def _cookie_file(platform: str, account: str) -> Path:
    sau_plat = SAU_MAP[platform]
    cdir = VENDOR_SAU_DIR / "cookies"
    cdir.mkdir(parents=True, exist_ok=True)
    return cdir / f"{sau_plat}_{account}.json"


def get_qr_session_status(platform: str) -> dict[str, Any]:
    return QR_SESSIONS.get(
        platform,
        {"platform": platform, "status": "idle", "qr_data_url": "", "message": "Idle"},
    )


def start_qr_login_session(platform: str) -> dict[str, Any]:
    if platform not in SAU_MAP:
        raise ValueError(f"QR login is supported for douyin, shipinhao, and weibo (got {platform})")

    # Cancel any stale running session for the same platform
    prev_task = QR_TASKS.get(platform)
    if prev_task and not prev_task.done():
        prev_task.cancel()

    QR_SESSIONS[platform] = {
        "platform": platform,
        "status": "fetching_qr",
        "qr_data_url": "",
        "message": "Fetching live login QR code from official Creator Studio (3–6s)...",
        "updated_at": time.time(),
    }
    QR_TASKS[platform] = asyncio.create_task(_run_qr_flow(platform))
    return QR_SESSIONS[platform]


async def _run_qr_flow(platform: str) -> None:
    settings = load_settings()
    account = (settings.get("accounts") or {}).get(platform, "creator")
    account_file = _cookie_file(platform, account)

    # Strip proxy env vars in current process briefly while launching domestic browser
    saved_proxies: dict[str, str] = {}
    if (settings.get("network") or {}).get("cn_direct_bypass_proxy", True):
        for k in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
            if k in os.environ:
                saved_proxies[k] = os.environ.pop(k)

    async def on_qrcode(payload: dict[str, Any]) -> None:
        data_url = (payload.get("image_data_url") or "").strip()
        img_path = payload.get("image_path") or ""
        if not data_url and img_path and Path(img_path).is_file():
            raw = Path(img_path).read_bytes()
            mime = "image/png" if str(img_path).lower().endswith(".png") else "image/jpeg"
            data_url = f"data:{mime};base64,{base64.b64encode(raw).decode('ascii')}"

        if data_url:
            QR_SESSIONS[platform] = {
                "platform": platform,
                "status": "waiting_scan",
                "qr_data_url": data_url,
                "message": APP_HINTS.get(platform, "Scan QR code on your phone"),
                "updated_at": time.time(),
            }

    try:
        if platform == "douyin":
            from uploader.douyin_uploader.main import douyin_cookie_gen

            res = await douyin_cookie_gen(
                str(account_file),
                qrcode_callback=on_qrcode,
                headless=True,
            )
        elif platform == "shipinhao":
            from uploader.tencent_uploader.main import tencent_cookie_gen

            res = await tencent_cookie_gen(
                str(account_file),
                qrcode_callback=on_qrcode,
                headless=True,
            )
        else:
            from uploader.weibo_uploader.main import weibo_cookie_gen

            res = await weibo_cookie_gen(
                str(account_file),
                qrcode_callback=on_qrcode,
                headless=True,
            )

        if res and res.get("success"):
            QR_SESSIONS[platform] = {
                "platform": platform,
                "status": "success",
                "qr_data_url": "",
                "message": f"Connected! Session saved ({account_file.name}).",
                "updated_at": time.time(),
            }
        else:
            msg = (res or {}).get("message") or "QR scan timed out or failed."
            QR_SESSIONS[platform] = {
                "platform": platform,
                "status": "failed",
                "qr_data_url": QR_SESSIONS.get(platform, {}).get("qr_data_url", ""),
                "message": msg,
                "updated_at": time.time(),
            }
    except asyncio.CancelledError:
        pass
    except Exception as exc:
        QR_SESSIONS[platform] = {
            "platform": platform,
            "status": "failed",
            "qr_data_url": "",
            "message": f"QR login error: {exc}",
            "updated_at": time.time(),
        }
    finally:
        for k, v in saved_proxies.items():
            os.environ[k] = v
