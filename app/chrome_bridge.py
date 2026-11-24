"""Real Chrome CDP Bridge (Port 9333) & Universal Cookie Importer.

Solves platform QR risk-control ('status not good') permanently by:
1. Launching normal Google Chrome (`chrome.exe --remote-debugging-port=9333 --user-data-dir=...`)
   with ZERO Playwright startup flags and a persistent profile on disk.
2. Syncing live login cookies & storageState from that real Chrome directly into OnePost.
3. Allowing uploaders to attach directly over CDP (`http://127.0.0.1:9333`) so uploads happen inside
   tabs of your exact logged-in Chrome browser.
4. Also supporting 1-click paste of Cookie-Editor JSON / Copy-as-cURL / raw Cookie headers from any Chrome window.
"""
from __future__ import annotations

import asyncio
import json
import re
import subprocess
from pathlib import Path
from typing import Any

import httpx

from app.config import DATA_DIR, PROJECT_ROOT, VENDOR_SAU_DIR, load_settings

CDP_PORT = 9333
CDP_URL = f"http://127.0.0.1:{CDP_PORT}"
STUDIO_CHROME_PROFILE = DATA_DIR / "studio_chrome_profile"
OPEN_CHROME_BAT = PROJECT_ROOT / "open_chrome.bat"
_CDP_SYNC_LOCK = asyncio.Lock()

PLATFORM_URLS = {
    "douyin": "https://creator.douyin.com/creator-micro/home",
    "shipinhao": "https://channels.weixin.qq.com/platform",
    "weibo": "https://weibo.com/",
    "x": "https://x.com/home",
}

PLATFORM_DOMAINS = {
    "douyin": ".douyin.com",
    "shipinhao": ".weixin.qq.com",
    "weibo": ".weibo.com",
    "x": ".x.com",
}

COOKIE_FILE_PREFIX = {
    "douyin": "douyin",
    "shipinhao": "tencent",
    "weibo": "weibo",
    "x": "x",
}


def get_platform_cookie_path(platform: str, account: str = "creator") -> Path:
    cdir = VENDOR_SAU_DIR / "cookies"
    cdir.mkdir(parents=True, exist_ok=True)
    prefix = COOKIE_FILE_PREFIX[platform]
    return cdir / f"{prefix}_{account}.json"


async def is_studio_chrome_running() -> dict[str, Any]:
    try:
        async with httpx.AsyncClient(timeout=1.5, trust_env=False) as client:
            resp = await client.get(f"{CDP_URL}/json/version")
            if resp.status_code == 200:
                data = resp.json()
                return {
                    "running": True,
                    "cdp_url": CDP_URL,
                    "browser": data.get("Browser", "Chrome"),
                }
    except Exception:
        pass
    return {"running": False, "cdp_url": CDP_URL, "browser": None}


async def launch_studio_chrome(target_platform: str | None = None) -> dict[str, Any]:
    """Launch real desktop Google Chrome with persistent profile & CDP port 9333,
    or open/focus the requested platform tab if Studio Chrome is already running.
    """
    status = await is_studio_chrome_running()
    url = PLATFORM_URLS.get(target_platform or "", "")

    if status["running"]:
        if url:
            await _open_tab_in_running_chrome(url)
        return {
            "launched": True,
            "already_running": True,
            "cdp_url": CDP_URL,
            "bat_path": str(OPEN_CHROME_BAT),
            "message": "Studio Chrome is open! Switched/opened login tab in your Chrome window.",
        }

    settings = load_settings()
    chrome_exe = (settings.get("chrome_path") or r"C:\Program Files\Google\Chrome\Application\chrome.exe").strip()
    STUDIO_CHROME_PROFILE.mkdir(parents=True, exist_ok=True)

    urls = [url] if url else list(PLATFORM_URLS.values())
    args = [
        chrome_exe,
        f"--remote-debugging-port={CDP_PORT}",
        f"--user-data-dir={STUDIO_CHROME_PROFILE}",
        "--no-first-run",
        "--no-default-browser-check",
        *urls,
    ]

    # Try launching via explorer.exe on the .bat when opening all tabs, or detached console process
    # so Windows brings it onto the interactive desktop session.
    try:
        if not target_platform and OPEN_CHROME_BAT.is_file():
            subprocess.Popen(["explorer.exe", str(OPEN_CHROME_BAT)])
        else:
            creation_flags = getattr(subprocess, "DETACHED_PROCESS", 0x00000008) | getattr(
                subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200
            )
            subprocess.Popen(args, creationflags=creation_flags, close_fds=True)
    except Exception:
        subprocess.Popen(args)

    # Wait up to 4 seconds to verify port 9333 came online
    import asyncio

    for _ in range(8):
        await asyncio.sleep(0.5)
        st = await is_studio_chrome_running()
        if st["running"]:
            return {
                "launched": True,
                "already_running": False,
                "cdp_url": CDP_URL,
                "bat_path": str(OPEN_CHROME_BAT),
                "message": "Real Studio Chrome is running! Log into your accounts in Chrome, then click Sync.",
            }

    return {
        "launched": False,
        "already_running": False,
        "cdp_url": CDP_URL,
        "bat_path": str(OPEN_CHROME_BAT),
        "message": f"If Chrome didn't pop to the front, double-click: {OPEN_CHROME_BAT}",
    }


async def _open_tab_in_running_chrome(url: str) -> None:
    from patchright.async_api import async_playwright

    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp(CDP_URL)
        context = browser.contexts[0] if browser.contexts else await browser.new_context()
        # Check if a tab for this host is already open
        host = url.split("/")[2]
        for pg in context.pages:
            if host in (pg.url or ""):
                await pg.bring_to_front()
                return
        page = await context.new_page()
        await page.goto(url, wait_until="domcontentloaded", timeout=30000)
        await page.bring_to_front()


def _has_platform_auth(platform: str, cookies: list[dict[str, Any]], open_urls: list[str]) -> bool:
    if platform == "douyin":
        return any(
            "douyin.com" in (c.get("domain") or "")
            and c.get("name") in ("sessionid", "sessionid_ss", "sid_tt")
            and c.get("value")
            for c in cookies
        )
    if platform == "shipinhao":
        # Shipinhao uses sessionid/wxuin/finder_username cookies OR an authenticated /platform/post URL
        has_cookie = any(
            ("weixin.qq.com" in (c.get("domain") or "") or "qq.com" in (c.get("domain") or ""))
            and c.get("name") in ("sessionid", "wxuin", "finder_username", "uin")
            and c.get("value")
            for c in cookies
        )
        on_creator_dashboard = any(
            "channels.weixin.qq.com/platform" in u and "login" not in u for u in open_urls
        )
        return has_cookie or on_creator_dashboard
    if platform == "weibo":
        return any(
            "weibo" in (c.get("domain") or "")
            and c.get("name") in ("SUB", "SUBP")
            and c.get("value")
            for c in cookies
        )
    if platform == "x":
        names = {
            c.get("name")
            for c in cookies
            if ("x.com" in (c.get("domain") or "") or "twitter.com" in (c.get("domain") or ""))
            and c.get("value")
        }
        return "auth_token" in names and "ct0" in names
    return False


async def sync_login_from_studio_chrome() -> dict[str, Any]:
    """Connect to live Chrome on port 9333, inspect cookies + localStorage, and save storageState files."""
    st = await is_studio_chrome_running()
    if not st["running"]:
        return {
            "chrome_running": False,
            "synced_platforms": [],
            "message": "Studio Chrome (port 9333) is not open yet. Click '1. Open Real Chrome' first.",
        }

    from patchright.async_api import async_playwright

    settings = load_settings()
    accounts = settings.get("accounts") or {}
    synced: list[str] = []

    async with _CDP_SYNC_LOCK:
        async with async_playwright() as p:
            browser = await p.chromium.connect_over_cdp(CDP_URL)
            if not browser.contexts:
                return {
                    "chrome_running": True,
                    "synced_platforms": [],
                    "message": "Chrome is open, no active profile context found.",
                }
            context = browser.contexts[0]
            state_dict = await context.storage_state()
            all_cookies = state_dict.get("cookies") or []
            open_urls = [pg.url or "" for pg in context.pages]

            for plat in ("douyin", "shipinhao", "weibo", "x"):
                if _has_platform_auth(plat, all_cookies, open_urls):
                    acc = accounts.get(plat, "creator")
                    out_file = get_platform_cookie_path(plat, acc)
                    out_file.write_text(json.dumps(state_dict, ensure_ascii=False, indent=2), encoding="utf-8")
                    synced.append(plat)

    return {
        "chrome_running": True,
        "synced_platforms": synced,
        "message": (
            f"Synced live Chrome login for: {', '.join(synced)}"
            if synced
            else "Chrome is open — sign in to Douyin / Shipinhao / Weibo / X in that Chrome window, then click Sync!"
        ),
    }


def _safe_parse_expiry(val: Any) -> float:
    if val is None or isinstance(val, bool):
        return -1
    try:
        parsed = float(val)
        return parsed if parsed > 0 else -1
    except (TypeError, ValueError):
        return -1


def _normalize_windows_curl_escapes(text: str) -> str:
    """Undo Windows CMD `Copy as cURL` caret-escaping (^\", ^&, ^%, ^^) if present."""
    if "^" not in text:
        return text
    cleaned = text.replace("^\r\n", " ").replace("^\n", " ")
    cleaned = re.sub(r'\^([&|^<>%"\'=;])', r"\1", cleaned)
    return cleaned


def import_cookies_from_text(platform: str, raw_text: str, account: str = "creator") -> Path:
    """Universal importer for cookies copied from ANY everyday Chrome window!
    Accepts:
    1. Playwright storage_state JSON (`{"cookies": [...]}`)
    2. Cookie-Editor / EditThisCookie JSON array (`[{"name": "...", "value": "..."}]`)
    3. `Copy as cURL (bash / cmd)` from Chrome DevTools Network tab
    4. Raw `Cookie: key=val; key2=val2` header text
    """
    if platform not in PLATFORM_DOMAINS:
        raise ValueError(f"Unsupported platform: {platform}")

    text = raw_text.strip()
    if not text:
        raise ValueError("Please paste your Cookie JSON, cURL command, or Cookie header string.")

    default_domain = PLATFORM_DOMAINS[platform]
    cookies_out: list[dict[str, Any]] = []
    origins_out: list[dict[str, Any]] = []

    # Case 1 & 2: JSON
    if text.startswith("{") or text.startswith("["):
        parsed = json.loads(text)
        if isinstance(parsed, dict) and isinstance(parsed.get("cookies"), list):
            raw_list = parsed["cookies"]
            origins_out = parsed.get("origins") or []
        elif isinstance(parsed, list):
            raw_list = parsed
        else:
            raise ValueError("Unrecognized JSON cookie format.")

        for item in raw_list:
            if not isinstance(item, dict) or not item.get("name"):
                continue
            same_site_raw = str(item.get("sameSite") or "Lax").capitalize()
            if same_site_raw not in ("Strict", "Lax", "None"):
                same_site_raw = "None" if "no_restriction" in str(item.get("sameSite", "")).lower() else "Lax"
            exp = item.get("expirationDate", item.get("expires", -1))
            cookies_out.append(
                {
                    "name": str(item["name"]),
                    "value": str(item.get("value", "")),
                    "domain": str(item.get("domain") or default_domain),
                    "path": str(item.get("path") or "/"),
                    "expires": _safe_parse_expiry(exp),
                    "httpOnly": bool(item.get("httpOnly", False)),
                    "secure": bool(item.get("secure", True)),
                    "sameSite": same_site_raw,
                }
            )
    else:
        # Case 3: Copy as cURL (bash or Windows cmd) -> extract -b '...' or -H 'cookie: ...'
        normalized = _normalize_windows_curl_escapes(text)
        cookie_str = normalized
        curl_b = re.search(r"""(?:-b|--cookie)\s+["']([^"'\r\n]+)["']""", normalized, re.IGNORECASE)
        curl_h = re.search(r"""-H\s+["']cookie:\s*([^"'\r\n]+)["']""", normalized, re.IGNORECASE)
        plain_h = re.search(r"""^cookie:\s*([^\r\n]+)""", normalized, re.IGNORECASE | re.MULTILINE)
        if curl_b:
            cookie_str = curl_b.group(1)
        elif curl_h:
            cookie_str = curl_h.group(1)
        elif plain_h:
            cookie_str = plain_h.group(1).strip()

        for pair in cookie_str.split(";"):
            pair = pair.strip()
            if "=" not in pair:
                continue
            k, v = pair.split("=", 1)
            k, v = k.strip(), v.strip()
            if not k:
                continue
            domains = [default_domain]
            if platform == "x":
                domains = [".x.com", ".twitter.com"]
            for dom in domains:
                cookies_out.append(
                    {
                        "name": k,
                        "value": v,
                        "domain": dom,
                        "path": "/",
                        "expires": -1,
                        "httpOnly": False,
                        "secure": True,
                        "sameSite": "Lax",
                    }
                )

    if not cookies_out:
        raise ValueError("No valid cookies found in the pasted text. Try copying from Cookie-Editor or DevTools Copy-as-cURL.")

    out_path = get_platform_cookie_path(platform, account)
    out_path.write_text(
        json.dumps({"cookies": cookies_out, "origins": origins_out}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return out_path
