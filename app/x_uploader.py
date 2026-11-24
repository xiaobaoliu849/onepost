"""X (Twitter) Video Publisher supporting both Browser Cookie Session (Patchright) and Official X API v2.

Designed specifically to pair with `social-auto-upload`:
- Stores browser cookies in `vendor/social-auto-upload/cookies/x_<account>.json`
- Automatically routes traffic through the configured X Proxy (`http://127.0.0.1:7890` etc.)
- Supports long 3-5+ minute highlight videos via chunked media upload (API) or X Web Compose (Browser)
"""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any, Callable

from app.config import VENDOR_SAU_DIR

LogFn = Callable[[str], None]


def get_x_cookie_file(account_name: str) -> Path:
    cookies_dir = VENDOR_SAU_DIR / "cookies"
    cookies_dir.mkdir(parents=True, exist_ok=True)
    return cookies_dir / f"x_{account_name}.json"


def save_x_tokens_cookie(account_name: str, auth_token: str, ct0: str) -> Path:
    """Create a valid Playwright storage_state JSON from `auth_token` and `ct0` cookies."""
    auth_token = auth_token.strip().strip('"')
    ct0 = ct0.strip().strip('"')
    if not auth_token or not ct0:
        raise ValueError("Both `auth_token` and `ct0` are required.")

    cookies = []
    for domain in (".x.com", ".twitter.com"):
        cookies.append(
            {
                "name": "auth_token",
                "value": auth_token,
                "domain": domain,
                "path": "/",
                "expires": -1,
                "httpOnly": True,
                "secure": True,
                "sameSite": "None",
            }
        )
        cookies.append(
            {
                "name": "ct0",
                "value": ct0,
                "domain": domain,
                "path": "/",
                "expires": -1,
                "httpOnly": False,
                "secure": True,
                "sameSite": "Lax",
            }
        )

    cookie_file = get_x_cookie_file(account_name)
    cookie_file.write_text(json.dumps({"cookies": cookies, "origins": []}, indent=2), encoding="utf-8")
    return cookie_file


def _build_launch_kwargs(settings: dict[str, Any], headless: bool) -> dict[str, Any]:
    chrome_path = (settings.get("chrome_path") or "").strip()
    net = settings.get("network") or {}
    use_proxy = bool(net.get("x_use_proxy", True))
    proxy_url = (net.get("x_proxy_url") or "").strip()

    kwargs: dict[str, Any] = {
        "headless": headless,
        "args": [
            "--disable-blink-features=AutomationControlled",
            "--no-sandbox",
            "--disable-infobars",
        ],
    }
    if chrome_path and Path(chrome_path).is_file():
        kwargs["executable_path"] = chrome_path
    if use_proxy and proxy_url:
        kwargs["proxy"] = {"server": proxy_url}
    return kwargs


async def login_x_browser(account_name: str, settings: dict[str, Any], log: LogFn | None = None) -> dict[str, Any]:
    """Open visible Chrome window to x.com login, wait for user to sign in, and save storage_state."""
    from patchright.async_api import async_playwright

    cookie_file = get_x_cookie_file(account_name)
    if log:
        log(f"[X] Launching visible Chrome for X login (account: {account_name})...")

    launch_kwargs = _build_launch_kwargs(settings, headless=False)
    async with async_playwright() as p:
        browser = await p.chromium.launch(**launch_kwargs)
        context_kwargs: dict[str, Any] = {"viewport": {"width": 1280, "height": 860}}
        if cookie_file.is_file():
            context_kwargs["storage_state"] = str(cookie_file)
        context = await browser.new_context(**context_kwargs)
        page = await context.new_page()
        await page.goto("https://x.com/home", wait_until="domcontentloaded", timeout=60000)

        if log:
            log("[X] Please sign in to your X account in the opened Chrome window (up to 3 minutes)...")

        # Wait until home timeline compose button or account switcher appears
        try:
            await page.wait_for_selector(
                '[data-testid="SideNav_NewTweet_Button"], [data-testid="AppTabBar_Home_Link"], [data-testid="tweetTextarea_0"]',
                timeout=180000,
            )
            await context.storage_state(path=str(cookie_file))
            if log:
                log(f"[X] Login verified! Session saved to {cookie_file.name}")
            await browser.close()
            return {"success": True, "cookie_file": str(cookie_file)}
        except Exception as exc:
            await browser.close()
            return {"success": False, "message": f"X login timed out or closed early: {exc}"}


async def check_x_cookie_fast(account_name: str, settings: dict[str, Any]) -> dict[str, Any]:
    """Check if X credentials/cookies are configured."""
    x_cfg = settings.get("x_config") or {}
    mode = x_cfg.get("mode", "browser")
    if mode == "api":
        has_keys = all(
            bool((x_cfg.get(k) or "").strip())
            for k in ("api_key", "api_secret", "access_token", "access_token_secret")
        )
        return {
            "ready": has_keys,
            "mode": "api",
            "detail": "Official X API v2 keys configured" if has_keys else "Missing X API keys in Settings",
        }

    cookie_file = get_x_cookie_file(account_name)
    if not cookie_file.is_file():
        return {"ready": False, "mode": "browser", "detail": "Not logged in yet"}

    try:
        raw = cookie_file.read_text(encoding="utf-8")
        has_auth_token = '"auth_token"' in raw and '"ct0"' in raw
        return {
            "ready": has_auth_token,
            "mode": "browser",
            "detail": f"Cookie session active ({cookie_file.name})" if has_auth_token else "Cookie missing auth_token",
        }
    except Exception as exc:
        return {"ready": False, "mode": "browser", "detail": str(exc)}


async def upload_x_video(
    video_path: Path,
    post_text: str,
    account_name: str,
    settings: dict[str, Any],
    log: LogFn,
) -> dict[str, Any]:
    x_cfg = settings.get("x_config") or {}
    mode = x_cfg.get("mode", "browser")
    if mode == "api":
        return await asyncio.to_thread(_upload_x_via_official_api, video_path, post_text, settings, log)
    return await _upload_x_via_browser(video_path, post_text, account_name, settings, log)


async def _upload_x_via_browser(
    video_path: Path,
    post_text: str,
    account_name: str,
    settings: dict[str, Any],
    log: LogFn,
) -> dict[str, Any]:
    from patchright.async_api import async_playwright

    cookie_file = get_x_cookie_file(account_name)
    cdp_url = os.environ.get("SAU_CDP_URL", "").strip()
    if not cdp_url and not cookie_file.is_file():
        raise RuntimeError(
            f"X browser session not found ({cookie_file.name}). Click 'Connect' on the X card to sync from Chrome first."
        )

    headless = bool(settings.get("headless_upload", False))
    launch_kwargs = _build_launch_kwargs(settings, headless=headless)

    async with async_playwright() as p:
        if cdp_url:
            log(f"[X] Attaching directly to your live Chrome ({cdp_url})...")
            browser = await p.chromium.connect_over_cdp(cdp_url)
            context = browser.contexts[0] if browser.contexts else await browser.new_context()
        else:
            proxy_info = launch_kwargs.get("proxy", {}).get("server", "System/TUN")
            log(f"[X] Starting browser publisher (route: {proxy_info}, headless={headless})...")
            browser = await p.chromium.launch(**launch_kwargs)
            context = await browser.new_context(
                storage_state=str(cookie_file),
                viewport={"width": 1280, "height": 900},
            )
        page = await context.new_page()
        try:
            log("[X] Opening https://x.com/compose/post ...")
            await page.goto("https://x.com/compose/post", wait_until="domcontentloaded", timeout=60000)

            textarea = page.locator('[data-testid="tweetTextarea_0"]').first
            await textarea.wait_for(state="visible", timeout=30000)
            await textarea.click()
            await textarea.fill(post_text)
            log(f"[X] Filled post copy ({len(post_text)} chars). Attaching video {video_path.name}...")

            file_input = page.locator('input[data-testid="fileInput"], input[type="file"]').first
            await file_input.set_input_files(str(video_path))

            # Wait for video upload & transcoding to complete before tweetButton becomes enabled
            post_btn = page.locator('[data-testid="tweetButton"], [data-testid="tweetButtonInline"]').first
            log("[X] Waiting for X video upload & processing to hit 100%...")

            max_wait_sec = 420  # Up to 7 minutes for 3-5 min HD video upload
            for elapsed in range(0, max_wait_sec, 3):
                await asyncio.sleep(3)
                aria_disabled = await post_btn.get_attribute("aria-disabled")
                is_disabled = await post_btn.is_disabled()
                if aria_disabled != "true" and not is_disabled:
                    # Double check there is no active progress status indicator
                    status_text = await page.locator('[role="progressbar"], [data-testid="attachments"]').all_inner_texts()
                    joined = " ".join(status_text).lower()
                    if "uploading" not in joined or "100%" in joined or elapsed >= 9:
                        break
                if elapsed > 0 and elapsed % 15 == 0:
                    log(f"[X] Still uploading/processing media on X ({elapsed}s elapsed)...")
            else:
                raise RuntimeError("Timed out waiting for X video upload to complete.")

            log("[X] Media ready! Clicking Post button...")
            await post_btn.click()

            # Wait for compose modal to close or toast confirmation
            await asyncio.sleep(5)
            await context.storage_state(path=str(cookie_file))
            log("[X] Video post submitted to X!")
            return {"status": "published", "platform": "x", "mode": "browser"}
        finally:
            if cdp_url:
                try:
                    await page.close()
                except Exception:
                    pass
            else:
                await browser.close()


def _upload_x_via_official_api(
    video_path: Path,
    post_text: str,
    settings: dict[str, Any],
    log: LogFn,
) -> dict[str, Any]:
    import tweepy

    x_cfg = settings.get("x_config") or {}
    net = settings.get("network") or {}
    use_proxy = bool(net.get("x_use_proxy", True))
    proxy_url = (net.get("x_proxy_url") or "").strip()

    old_http = os.environ.get("HTTP_PROXY")
    old_https = os.environ.get("HTTPS_PROXY")
    try:
        if use_proxy and proxy_url:
            os.environ["HTTP_PROXY"] = proxy_url
            os.environ["HTTPS_PROXY"] = proxy_url
            log(f"[X-API] Routing official X API through proxy {proxy_url}...")

        auth = tweepy.OAuth1UserHandler(
            x_cfg["api_key"].strip(),
            x_cfg["api_secret"].strip(),
            x_cfg["access_token"].strip(),
            x_cfg["access_token_secret"].strip(),
        )
        api_v1 = tweepy.API(auth, wait_on_rate_limit=True)
        log(f"[X-API] Uploading {video_path.name} via chunked media upload...")
        media = api_v1.media_upload(
            filename=str(video_path),
            media_category="tweet_video",
            chunked=True,
        )
        log(f"[X-API] Media uploaded (media_id={media.media_id_string}). Creating post via X API v2...")

        client_v2 = tweepy.Client(
            consumer_key=x_cfg["api_key"].strip(),
            consumer_secret=x_cfg["api_secret"].strip(),
            access_token=x_cfg["access_token"].strip(),
            access_token_secret=x_cfg["access_token_secret"].strip(),
        )
        resp = client_v2.create_tweet(text=post_text, media_ids=[media.media_id_string])
        tweet_id = resp.data.get("id") if resp and resp.data else "unknown"
        post_url = f"https://x.com/i/web/status/{tweet_id}"
        log(f"[X-API] Published! URL: {post_url}")
        return {"status": "published", "platform": "x", "mode": "api", "post_url": post_url}
    finally:
        if old_http is None:
            os.environ.pop("HTTP_PROXY", None)
        else:
            os.environ["HTTP_PROXY"] = old_http
        if old_https is None:
            os.environ.pop("HTTPS_PROXY", None)
        else:
            os.environ["HTTPS_PROXY"] = old_https
