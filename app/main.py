"""FastAPI Backend for OnePost."""
from __future__ import annotations

import mimetypes
import re
import shutil
import time
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from PIL import Image, ImageDraw
from pydantic import BaseModel, Field

from app.ai_copywriter import generate_platform_copy
from app.asset_scanner import (
    IMAGE_EXTS,
    SUBTITLE_EXTS,
    VIDEO_EXTS,
    build_bundle_manifest,
    scan_bundle_directory,
    score_poster_candidate,
    score_subtitle_candidate,
    score_video_candidate,
)
from app.chrome_bridge import (
    import_cookies_from_text,
    is_studio_chrome_running,
    launch_studio_chrome,
    sync_login_from_studio_chrome,
)
from app.config import (
    DATA_DIR,
    PROJECT_ROOT,
    UPLOADS_STAGING_DIR,
    VENDOR_SAU_DIR,
    load_settings,
    save_settings,
)
from app.publisher import (
    cancel_job,
    get_all_accounts_status,
    get_job,
    list_recent_jobs,
    run_preflight_summary,
    start_publish_job,
    trigger_platform_login,
)
from app.qr_login import get_qr_session_status, start_qr_login_session
from app.x_uploader import save_x_tokens_cookie

STATIC_DIR = PROJECT_ROOT / "static"
STATIC_DIR.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="OnePost", version="2.0.0")


@app.middleware("http")
async def disable_static_cache(request: Request, call_next):
    response = await call_next(request)
    if request.url.path == "/" or request.url.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-store, max-age=0"
    return response


app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


class ScanFolderRequest(BaseModel):
    folder_path: str
    video_override: str | None = None
    poster_override: str | None = None
    subtitle_override: str | None = None


class GenerateCopyRequest(BaseModel):
    bundle: dict[str, Any]
    user_hint: str = ""


class PublishRequest(BaseModel):
    video_path: str
    poster_path: str | None = None
    covers: dict[str, str | None] = Field(default_factory=dict)
    platforms: list[str]
    copy_payload: dict[str, Any]
    options: dict[str, Any] = Field(default_factory=dict)
    dry_run: bool = False


@app.get("/", response_class=HTMLResponse)
async def root_index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/health")
async def api_health() -> dict[str, Any]:
    return {"ok": True, "service": "OnePost"}


@app.get("/api/settings")
async def api_get_settings() -> dict[str, Any]:
    return load_settings()


@app.post("/api/settings")
async def api_update_settings(payload: dict[str, Any]) -> dict[str, Any]:
    return save_settings(payload)


@app.get("/api/accounts")
async def api_accounts(
    deep: bool = Query(default=False),
    auto_sync: bool = Query(default=True),
) -> dict[str, Any]:
    statuses = await get_all_accounts_status(deep_verify=deep, auto_sync=auto_sync)
    return {"accounts": statuses}


class XCookieManualRequest(BaseModel):
    auth_token: str
    ct0: str


class ImportRawCookiesRequest(BaseModel):
    raw_text: str


class LaunchChromeRequest(BaseModel):
    platform: str | None = None


@app.get("/api/chrome/status")
async def api_chrome_status() -> dict[str, Any]:
    return await is_studio_chrome_running()


@app.post("/api/chrome/launch")
async def api_chrome_launch(req: LaunchChromeRequest) -> dict[str, Any]:
    res = await launch_studio_chrome(target_platform=req.platform)
    statuses = await get_all_accounts_status(deep_verify=False)
    return {"chrome": res, "accounts": statuses}


@app.post("/api/chrome/sync")
async def api_chrome_sync() -> dict[str, Any]:
    sync_res = await sync_login_from_studio_chrome()
    statuses = await get_all_accounts_status(deep_verify=False)
    return {"sync": sync_res, "accounts": statuses}


@app.post("/api/accounts/{platform}/import-cookies")
async def api_import_cookies(platform: str, req: ImportRawCookiesRequest) -> dict[str, Any]:
    try:
        settings = load_settings()
        acc = (settings.get("accounts") or {}).get(platform, "creator")
        out_path = import_cookies_from_text(platform, req.raw_text, account=acc)
        statuses = await get_all_accounts_status(deep_verify=False)
        return {"ok": True, "cookie_file": str(out_path), "accounts": statuses}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/accounts/{platform}/login")
async def api_login_platform(platform: str) -> dict[str, Any]:
    try:
        res = await trigger_platform_login(platform)
        statuses = await get_all_accounts_status(deep_verify=False)
        if not res.get("success"):
            raise HTTPException(status_code=400, detail=res.get("message") or res.get("output") or "Login did not complete")
        return {"result": res, "accounts": statuses}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/accounts/{platform}/qr/start")
async def api_qr_start(platform: str) -> dict[str, Any]:
    try:
        session = start_qr_login_session(platform)
        return {"session": session}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/accounts/{platform}/qr/status")
async def api_qr_status(platform: str) -> dict[str, Any]:
    session = get_qr_session_status(platform)
    accounts = None
    if session.get("status") == "success":
        accounts = await get_all_accounts_status(deep_verify=False)
    return {"session": session, "accounts": accounts}


@app.post("/api/accounts/x/cookies")
async def api_save_x_cookies(req: XCookieManualRequest) -> dict[str, Any]:
    try:
        settings = load_settings()
        acc = (settings.get("accounts") or {}).get("x", "creator")
        cfile = save_x_tokens_cookie(acc, req.auth_token, req.ct0)
        statuses = await get_all_accounts_status(deep_verify=False)
        return {"ok": True, "cookie_file": str(cfile), "accounts": statuses}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/bundle/scan")
async def api_scan_folder(req: ScanFolderRequest) -> dict[str, Any]:
    try:
        if req.video_override or req.poster_override or req.subtitle_override:
            folder = Path(req.folder_path).expanduser().resolve()
            base = scan_bundle_directory(folder)
            cands = base["candidates"]
            vpath = Path(req.video_override) if req.video_override else (Path(cands["videos"][0]) if cands["videos"] else None)
            ppath = Path(req.poster_override) if req.poster_override else (Path(cands["posters"][0]) if cands["posters"] else None)
            spath = Path(req.subtitle_override) if req.subtitle_override else (Path(cands["subtitles"][0]) if cands["subtitles"] else None)
            bundle = build_bundle_manifest(
                folder=folder,
                video_path=vpath,
                poster_path=ppath,
                subtitle_path=spath,
                all_videos=[Path(p) for p in cands["videos"]],
                all_posters=[Path(p) for p in cands["posters"]],
                all_subtitles=[Path(p) for p in cands["subtitles"]],
            )
        else:
            bundle = scan_bundle_directory(req.folder_path)

        settings = load_settings()
        copy_draft = await generate_platform_copy(bundle, settings)
        return {"bundle": bundle, "copy": copy_draft}
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/bundle/upload")
async def api_upload_bundle(
    files: list[UploadFile] = File(...),
    bundle_title: str = Form(default=""),
    existing_video: str = Form(default=""),
    existing_poster: str = Form(default=""),
    existing_subtitle: str = Form(default=""),
) -> dict[str, Any]:
    """Allow dragging & dropping Video + Poster Card + Karaoke SRT directly into the browser.
    Streams uploads in 1 MB chunks, scores multi-file drops, rejects unsupported-only drops cleanly,
    and optionally preserves untouched asset slots (`existing_*`) during single-slot replacement.
    """
    stamp = time.strftime("%Y%m%d_%H%M%S")
    safe_label = "".join(ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in (bundle_title or "bundle"))[:32]
    target_dir = UPLOADS_STAGING_DIR / f"{stamp}_{safe_label}"
    target_dir.mkdir(parents=True, exist_ok=True)

    saved_videos: list[Path] = []
    saved_posters: list[Path] = []
    saved_subs: list[Path] = []
    ignored_files: list[str] = []

    try:
        for uf in files:
            if not uf.filename:
                continue
            fname = Path(uf.filename).name
            ext = Path(fname).suffix.lower()
            if ext not in (VIDEO_EXTS | IMAGE_EXTS | SUBTITLE_EXTS):
                ignored_files.append(fname)
                await uf.close()
                continue

            dest = target_dir / fname
            with dest.open("wb") as out_f:
                while chunk := await uf.read(1024 * 1024):
                    out_f.write(chunk)
            await uf.close()

            if ext in VIDEO_EXTS:
                saved_videos.append(dest)
            elif ext in IMAGE_EXTS:
                saved_posters.append(dest)
            elif ext in SUBTITLE_EXTS:
                saved_subs.append(dest)

        if not (saved_videos or saved_posters or saved_subs):
            shutil.rmtree(target_dir, ignore_errors=True)
            raise HTTPException(
                status_code=400,
                detail="No supported video (.mp4/.mov/.mkv/.webm), image (.png/.jpg/.webp), or subtitle (.srt/.ass/.vtt) files were uploaded.",
            )

        # Preserve existing untouched slots when user drops/replaces a single file type
        def _keep_existing(raw_path: str) -> Path | None:
            if not raw_path or not raw_path.strip():
                return None
            p = Path(raw_path).expanduser().resolve()
            return p if p.is_file() else None

        if not saved_videos:
            kept_v = _keep_existing(existing_video)
            if kept_v:
                saved_videos.append(kept_v)
        if not saved_posters:
            kept_p = _keep_existing(existing_poster)
            if kept_p:
                saved_posters.append(kept_p)
        if not saved_subs:
            kept_s = _keep_existing(existing_subtitle)
            if kept_s:
                saved_subs.append(kept_s)

        saved_videos.sort(key=score_video_candidate, reverse=True)
        saved_posters.sort(key=score_poster_candidate, reverse=True)
        saved_subs.sort(key=score_subtitle_candidate, reverse=True)

        bundle = build_bundle_manifest(
            folder=target_dir,
            video_path=saved_videos[0] if saved_videos else None,
            poster_path=saved_posters[0] if saved_posters else None,
            subtitle_path=saved_subs[0] if saved_subs else None,
            all_videos=saved_videos,
            all_posters=saved_posters,
            all_subtitles=saved_subs,
        )
        settings = load_settings()
        copy_draft = await generate_platform_copy(bundle, settings, user_hint=bundle_title)
        return {"bundle": bundle, "copy": copy_draft, "ignored_files": ignored_files}
    except HTTPException:
        raise
    except Exception as exc:
        shutil.rmtree(target_dir, ignore_errors=True)
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/bundle/demo")
async def api_load_demo_bundle() -> dict[str, Any]:
    """Create & load a sample Video + Poster Card + Karaoke SRT bundle for instant testing."""
    demo_dir = DATA_DIR / "demo_highlight_bundle"
    demo_dir.mkdir(parents=True, exist_ok=True)

    demo_video = demo_dir / "ai_future_highlight_cut.mp4"
    vendor_demo_video = VENDOR_SAU_DIR / "videos" / "demo.mp4"
    if not demo_video.is_file() and vendor_demo_video.is_file():
        shutil.copy2(vendor_demo_video, demo_video)

    demo_poster = demo_dir / "poster_card_cover.png"
    if not demo_poster.is_file():
        img = Image.new("RGB", (1080, 1440), color=(18, 22, 36))
        draw = ImageDraw.Draw(img)
        draw.rectangle([(48, 48), (1032, 1392)], outline=(249, 115, 22), width=6)
        draw.rectangle([(90, 130), (420, 195)], fill=(249, 115, 22))
        draw.text((112, 148), "TEASER HIGHLIGHT", fill=(15, 18, 28))
        draw.text((90, 260), "WHY AI AGENTS CHANGE\nCREATOR WORKFLOWS", fill=(248, 250, 252))
        draw.text((90, 420), "3-Min Essential Cut  |  Karaoke Bilingual Subs", fill=(148, 163, 184))
        img.save(demo_poster, format="PNG")

    demo_srt = demo_dir / "karaoke_en_highlight.srt"
    if not demo_srt.is_file():
        demo_srt.write_text(
            "1\n"
            "00:00:00,200 --> 00:00:02,800\n"
            "<font color=\"#f97316\"><b>[TEASER HOOK]</b></font> Most creators spend <u>80% of their time</u> just re-uploading to different platforms.\n\n"
            "2\n"
            "00:00:02,850 --> 00:00:06,200\n"
            "When you combine <font color=\"#38bdf8\">karaoke-highlighted subtitles</font> with a sharp 3-minute cut, retention doubles.\n\n"
            "3\n"
            "00:00:06,300 --> 00:00:10,500\n"
            "Here is the exact automated publishing workflow across Douyin, Shipinhao, Weibo, and X.\n",
            encoding="utf-8",
        )

    bundle = scan_bundle_directory(demo_dir)
    settings = load_settings()
    copy_draft = await generate_platform_copy(
        bundle,
        settings,
        user_hint="AI创作者工作流效率革命：3分钟高能精剪与一键全平台分发",
    )
    return {"bundle": bundle, "copy": copy_draft}


class AiTestRequest(BaseModel):
    base_url: str | None = None
    api_key: str | None = None
    model: str | None = None


@app.post("/api/ai/test")
async def api_test_ai_connection(req: AiTestRequest | None = None) -> dict[str, Any]:
    """Verify configured OpenAI-compatible AI copywriter endpoint & credentials."""
    settings = load_settings()
    ai_cfg = settings.get("ai") or {}
    base_url = ((req.base_url if req and req.base_url is not None else ai_cfg.get("base_url")) or "https://api.deepseek.com/v1").strip().rstrip("/")
    api_key = ((req.api_key if req and req.api_key is not None else ai_cfg.get("api_key")) or "").strip()
    model = ((req.model if req and req.model is not None else ai_cfg.get("model")) or "deepseek-chat").strip()

    if not api_key:
        return {
            "ok": False,
            "message": "No API key provided — built-in Smart SRT Composer will be used offline.",
        }

    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(f"{base_url}/models", headers=headers)
            if resp.status_code in (404, 405):
                chat_resp = await client.post(
                    f"{base_url}/chat/completions",
                    headers=headers,
                    json={
                        "model": model,
                        "max_tokens": 5,
                        "messages": [{"role": "user", "content": "ping"}],
                    },
                )
                chat_resp.raise_for_status()
            else:
                resp.raise_for_status()
        return {"ok": True, "message": f"Connected to {base_url} (model: {model})"}
    except httpx.HTTPStatusError as exc:
        return {"ok": False, "message": f"Endpoint rejected request (HTTP {exc.response.status_code}). Check API key, base URL, or model name."}
    except Exception as exc:
        return {"ok": False, "message": f"Connection failed: {exc}"}


@app.post("/api/copy/generate")
async def api_generate_copy(req: GenerateCopyRequest) -> dict[str, Any]:
    settings = load_settings()
    copy_data = await generate_platform_copy(req.bundle, settings, user_hint=req.user_hint)
    return {"copy": copy_data}


@app.post("/api/preflight")
async def api_preflight(req: PublishRequest) -> dict[str, Any]:
    if not req.platforms:
        raise HTTPException(status_code=400, detail="Select at least one target platform.")
    return await run_preflight_summary(
        video_path=req.video_path,
        poster_path=req.poster_path,
        covers=req.covers,
        platforms=req.platforms,
        copy_payload=req.copy_payload,
        options=req.options,
    )


@app.post("/api/publish")
async def api_publish(req: PublishRequest) -> dict[str, Any]:
    if not req.platforms:
        raise HTTPException(status_code=400, detail="Select at least one target platform.")
    try:
        job = start_publish_job(
            video_path=req.video_path,
            poster_path=req.poster_path,
            covers=req.covers,
            platforms=req.platforms,
            copy_payload=req.copy_payload,
            options=req.options,
            dry_run=req.dry_run,
        )
        return {"job": job.to_dict()}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/jobs")
async def api_list_jobs() -> dict[str, Any]:
    return {"jobs": list_recent_jobs()}


@app.get("/api/jobs/{job_id}")
async def api_get_job(job_id: str) -> dict[str, Any]:
    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return {"job": job}


@app.post("/api/jobs/{job_id}/cancel")
async def api_cancel_job(job_id: str) -> dict[str, Any]:
    try:
        job = await cancel_job(job_id)
        return {"job": job}
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/api/media")
async def api_stream_local_media(path: str, request: Request):
    """Serve local video/poster files with RFC-compliant HTTP Range support for smooth video scrubbing."""
    target = Path(path).expanduser().resolve()
    if not target.is_file():
        raise HTTPException(status_code=404, detail="Media file not found")
    if target.suffix.lower() not in (VIDEO_EXTS | IMAGE_EXTS | SUBTITLE_EXTS):
        raise HTTPException(status_code=403, detail="Unsupported media extension")

    media_type, _ = mimetypes.guess_type(str(target))
    media_type = media_type or "application/octet-stream"
    file_size = target.stat().st_size
    range_header = request.headers.get("range")

    if file_size == 0 or not range_header or target.suffix.lower() not in VIDEO_EXTS:
        return FileResponse(target, media_type=media_type)

    # Parse Range: bytes=start-end | bytes=start- | bytes=-suffix
    m = re.fullmatch(r"bytes=(\d*)-(\d*)", range_header.strip().lower())
    if not m or (not m.group(1) and not m.group(2)):
        return Response(
            status_code=416,
            headers={"Content-Range": f"bytes */{file_size}"},
        )

    start_str, end_str = m.group(1), m.group(2)
    if not start_str:
        suffix_len = int(end_str)
        if suffix_len <= 0:
            return Response(status_code=416, headers={"Content-Range": f"bytes */{file_size}"})
        start = max(0, file_size - suffix_len)
        end = file_size - 1
    else:
        start = int(start_str)
        end = int(end_str) if end_str else min(start + 2 * 1024 * 1024 - 1, file_size - 1)
        end = min(end, file_size - 1)

    if start < 0 or start >= file_size or start > end:
        return Response(
            status_code=416,
            headers={"Content-Range": f"bytes */{file_size}"},
        )

    chunk_len = (end - start) + 1

    def iter_chunk():
        with target.open("rb") as f:
            f.seek(start)
            remaining = chunk_len
            while remaining > 0:
                data = f.read(min(65536, remaining))
                if not data:
                    break
                remaining -= len(data)
                yield data

    headers = {
        "Content-Range": f"bytes {start}-{end}/{file_size}",
        "Accept-Ranges": "bytes",
        "Content-Length": str(chunk_len),
    }
    return StreamingResponse(iter_chunk(), status_code=206, media_type=media_type, headers=headers)
