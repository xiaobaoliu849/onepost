"""Optional REST Bridge to `leaperone/MultiPost-Extension` if user enables Chrome Extension mode."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

import httpx

LogFn = Callable[[str], None]


async def publish_via_multipost(
    video_path: Path,
    cover_path: Path | None,
    platforms_payload: dict[str, Any],
    selected_platforms: list[str],
    settings: dict[str, Any],
    log: LogFn,
) -> dict[str, Any]:
    mp_cfg = settings.get("multipost") or {}
    api_url = (mp_cfg.get("api_url") or "http://127.0.0.1:10721/api/post").strip()
    api_key = (mp_cfg.get("api_key") or "").strip()

    platform_map = {
        "douyin": "douyin",
        "shipinhao": "weixin-channels",
        "weibo": "weibo",
        "x": "x",
    }
    targets = [platform_map[p] for p in selected_platforms if p in platform_map]
    log(f"[MultiPost] Sending publish bundle to {api_url} for targets={targets}...")

    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    # Pick representative primary text while passing per-platform overrides
    dy = platforms_payload.get("douyin") or {}
    body = {
        "platforms": targets,
        "title": dy.get("title", ""),
        "content": dy.get("description", ""),
        "videos": [str(video_path.resolve())],
        "images": [str(cover_path.resolve())] if cover_path and cover_path.is_file() else [],
        "extra": platforms_payload,
    }
    async with httpx.AsyncClient(timeout=30.0, trust_env=False) as client:
        resp = await client.post(api_url, headers=headers, json=body)
        resp.raise_for_status()
        data = resp.json()
    log(f"[MultiPost] Extension accepted publish dispatch: {data}")
    return data
