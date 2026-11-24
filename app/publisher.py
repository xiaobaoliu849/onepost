"""Unified Multi-Platform Publishing Orchestrator (Douyin + Shipinhao + Weibo + X).

Features:
- Powered by open-source `dreammis/social-auto-upload` (`sau_cli.py`) for Douyin, Shipinhao (`tencent`), and Weibo
- Paired with `x_uploader.py` for X (Twitter) via Patchright Browser Cookie or Official X API v2
- Split-Network Routing: enforces Direct CN connection (`NO_PROXY=*`, strips HTTP_PROXY/HTTPS_PROXY) for
  Douyin, Shipinhao, and Weibo so domestic accounts never leak overseas VPN IPs, while routing X via Proxy!
- Real-time job status, per-platform step tracking, Dry-Run preflight verification, and 1-click Login triggers.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.chrome_bridge import CDP_URL, is_studio_chrome_running, sync_login_from_studio_chrome
from app.config import JOBS_FILE, VENDOR_SAU_DIR, load_settings
from app.multipost_bridge import publish_via_multipost
from app.x_uploader import check_x_cookie_fast, get_x_cookie_file, login_x_browser, upload_x_video

SAU_CLI_SCRIPT = VENDOR_SAU_DIR / "sau_cli.py"

SAU_PLATFORM_MAP = {
    "douyin": "douyin",
    "shipinhao": "tencent",
    "weibo": "weibo",
}

PLATFORM_LABELS = {
    "douyin": "Douyin (抖音)",
    "shipinhao": "Shipinhao (视频号)",
    "weibo": "Weibo (微博)",
    "x": "X (Twitter)",
}


@dataclass
class PublishJob:
    job_id: str
    created_at: float
    video_path: str
    poster_path: str | None
    platforms: list[str]
    dry_run: bool = False
    status: str = "running"  # running | completed | partial_failed | failed
    platform_states: dict[str, dict[str, Any]] = field(default_factory=dict)
    logs: list[str] = field(default_factory=list)

    def log(self, message: str) -> None:
        stamp = time.strftime("%H:%M:%S")
        entry = f"[{stamp}] {message}"
        self.logs.append(entry)

    def to_dict(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "created_at": self.created_at,
            "video_path": self.video_path,
            "poster_path": self.poster_path,
            "platforms": self.platforms,
            "dry_run": self.dry_run,
            "status": self.status,
            "platform_states": self.platform_states,
            "logs": self.logs[-300:],
        }


ACTIVE_JOBS: dict[str, PublishJob] = {}


def _save_job_history(job: PublishJob) -> None:
    history: list[dict[str, Any]] = []
    if JOBS_FILE.is_file():
        try:
            history = json.loads(JOBS_FILE.read_text(encoding="utf-8"))
        except Exception:
            history = []
    history = [h for h in history if h.get("job_id") != job.job_id]
    history.insert(0, job.to_dict())
    JOBS_FILE.write_text(json.dumps(history[:30], ensure_ascii=False, indent=2), encoding="utf-8")


def list_recent_jobs() -> list[dict[str, Any]]:
    live = [j.to_dict() for j in ACTIVE_JOBS.values()]
    live_ids = {j["job_id"] for j in live}
    saved: list[dict[str, Any]] = []
    if JOBS_FILE.is_file():
        try:
            saved = [h for h in json.loads(JOBS_FILE.read_text(encoding="utf-8")) if h.get("job_id") not in live_ids]
        except Exception:
            saved = []
    combined = sorted(live + saved, key=lambda item: item.get("created_at", 0), reverse=True)
    return combined[:25]


def get_job(job_id: str) -> dict[str, Any] | None:
    if job_id in ACTIVE_JOBS:
        return ACTIVE_JOBS[job_id].to_dict()
    for item in list_recent_jobs():
        if item.get("job_id") == job_id:
            return item
    return None


def _sau_cookie_path(sau_platform: str, account_name: str) -> Path:
    cookies_dir = VENDOR_SAU_DIR / "cookies"
    cookies_dir.mkdir(parents=True, exist_ok=True)
    return cookies_dir / f"{sau_platform}_{account_name}.json"


async def get_all_accounts_status(deep_verify: bool = False) -> dict[str, Any]:
    """Return instant status of Douyin, Shipinhao, Weibo, and X accounts (auto-syncing if Studio Chrome is open)."""
    chrome_live = await is_studio_chrome_running()
    if chrome_live["running"]:
        try:
            await sync_login_from_studio_chrome()
        except Exception:
            pass

    settings = load_settings()
    accounts = settings.get("accounts") or {}
    net = settings.get("network") or {}
    result: dict[str, Any] = {}

    for ui_plat, sau_plat in SAU_PLATFORM_MAP.items():
        acc = accounts.get(ui_plat, "creator")
        cpath = _sau_cookie_path(sau_plat, acc)
        exists = cpath.is_file() and cpath.stat().st_size > 20
        verified = exists
        detail = f"Cookie saved ({cpath.name})" if exists else "Not connected — click Connect to scan QR"

        if exists and deep_verify:
            code, out = await _run_sau_subprocess([sau_plat, "check", "--account", acc], settings)
            verified = code == 0 and "valid" in out.lower() and "invalid" not in out.lower()
            detail = "Live cookie verified" if verified else "Cookie expired — please re-scan QR"

        result[ui_plat] = {
            "platform": ui_plat,
            "label": PLATFORM_LABELS[ui_plat],
            "account": acc,
            "ready": verified,
            "cookie_exists": exists,
            "route": "Direct CN IP (No Proxy)" if net.get("cn_direct_bypass_proxy", True) else "System Default",
            "detail": detail,
        }

    x_acc = accounts.get("x", "creator")
    x_stat = await check_x_cookie_fast(x_acc, settings)
    result["x"] = {
        "platform": "x",
        "label": PLATFORM_LABELS["x"],
        "account": x_acc,
        "ready": x_stat["ready"],
        "cookie_exists": get_x_cookie_file(x_acc).is_file(),
        "mode": x_stat["mode"],
        "route": (
            f"Proxy ({net.get('x_proxy_url')})"
            if net.get("x_use_proxy", True) and net.get("x_proxy_url")
            else "Direct Network"
        ),
        "detail": x_stat["detail"],
    }
    return result


async def trigger_platform_login(platform: str) -> dict[str, Any]:
    """Open visible Chrome window so the user can scan QR / log in to the target platform."""
    settings = load_settings()
    accounts = settings.get("accounts") or {}
    acc = accounts.get(platform, "creator")

    if platform == "x":
        return await login_x_browser(acc, settings)

    if platform not in SAU_PLATFORM_MAP:
        raise ValueError(f"Unsupported platform: {platform}")

    sau_plat = SAU_PLATFORM_MAP[platform]
    code, out = await _run_sau_subprocess(
        [sau_plat, "login", "--account", acc, "--headed"],
        settings,
        timeout_sec=240,
    )
    cookie_file = _sau_cookie_path(sau_plat, acc)
    success = code == 0 and cookie_file.is_file()
    return {
        "success": success,
        "platform": platform,
        "account": acc,
        "cookie_file": str(cookie_file),
        "output": out[-1200:],
    }


def _build_clean_cn_env(settings: dict[str, Any], cdp_url: str = "") -> dict[str, str]:
    """Strip proxy environment variables when publishing to Douyin, Shipinhao, and Weibo."""
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    if cdp_url:
        env["SAU_CDP_URL"] = cdp_url
    else:
        env.pop("SAU_CDP_URL", None)
    if (settings.get("network") or {}).get("cn_direct_bypass_proxy", True):
        for key in (
            "HTTP_PROXY",
            "HTTPS_PROXY",
            "ALL_PROXY",
            "http_proxy",
            "https_proxy",
            "all_proxy",
        ):
            env.pop(key, None)
        env["NO_PROXY"] = "*"
    return env


async def _run_sau_subprocess(
    cli_args: list[str],
    settings: dict[str, Any],
    log_fn=None,
    timeout_sec: int = 600,
    cdp_url: str = "",
) -> tuple[int, str]:
    cmd = [sys.executable, str(SAU_CLI_SCRIPT), *cli_args]
    env = _build_clean_cn_env(settings, cdp_url=cdp_url)

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        cwd=str(VENDOR_SAU_DIR),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        env=env,
    )
    collected: list[str] = []
    assert proc.stdout is not None

    try:
        async with asyncio.timeout(timeout_sec):
            while True:
                line = await proc.stdout.readline()
                if not line:
                    break
                decoded = line.decode("utf-8", errors="replace").rstrip()
                if decoded:
                    collected.append(decoded)
                    if log_fn:
                        log_fn(decoded)
            code = await proc.wait()
            return code, "\n".join(collected)
    except TimeoutError:
        proc.kill()
        raise RuntimeError(f"Command timed out after {timeout_sec}s: {' '.join(cli_args[:3])}")


def build_sau_upload_args(
    platform: str,
    account: str,
    video_path: Path,
    covers: dict[str, str | None],
    meta: dict[str, Any],
    options: dict[str, Any],
    headless: bool,
) -> list[str]:
    """Construct exact `sau_cli.py` arguments for Douyin, Shipinhao (`tencent`), or Weibo."""
    sau_plat = SAU_PLATFORM_MAP[platform]
    title = (meta.get("title") or "").strip()[:30]
    desc = (meta.get("description") or "").strip()
    tags_list = meta.get("tags") or []
    if isinstance(tags_list, str):
        tags_csv = ",".join(t.strip().lstrip("#") for t in tags_list.split(",") if t.strip())
    else:
        tags_csv = ",".join(str(t).strip().lstrip("#") for t in tags_list if str(t).strip())

    args = [
        sau_plat,
        "upload-video",
        "--account",
        account,
        "--file",
        str(video_path.resolve()),
        "--title",
        title or video_path.stem[:25],
    ]
    if desc:
        args.extend(["--desc", desc])
    if tags_csv:
        args.extend(["--tags", tags_csv])

    portrait = covers.get("portrait_3x4") or covers.get("original")
    landscape = covers.get("landscape_4x3") or covers.get("original")
    weibo_cov = covers.get("weibo_cover") or covers.get("original")

    if platform in ("douyin", "shipinhao"):
        if portrait and Path(portrait).is_file():
            args.extend(["--thumbnail-portrait", str(Path(portrait).resolve())])
        if landscape and Path(landscape).is_file():
            args.extend(["--thumbnail-landscape", str(Path(landscape).resolve())])
        schedule_str = (options.get("schedule") or "").strip()
        if schedule_str:
            args.extend(["--schedule", schedule_str])

    if platform == "shipinhao":
        short_title = (meta.get("short_title") or "").strip()
        if short_title:
            args.extend(["--short-title", short_title[:16]])
        if options.get("shipinhao_draft"):
            args.append("--draft")

    if platform == "weibo":
        if weibo_cov and Path(weibo_cov).is_file():
            args.extend(["--thumbnail", str(Path(weibo_cov).resolve())])

    args.append("--headless" if headless else "--headed")
    return args


def start_publish_job(
    video_path: str,
    poster_path: str | None,
    covers: dict[str, str | None],
    platforms: list[str],
    copy_payload: dict[str, Any],
    options: dict[str, Any] | None = None,
    dry_run: bool = False,
) -> PublishJob:
    """Create and launch a background multi-platform publish job."""
    options = options or {}
    vpath = Path(video_path).expanduser().resolve()
    if not vpath.is_file():
        raise FileNotFoundError(f"Video file not found: {vpath}")

    job_id = uuid.uuid4().hex[:10]
    job = PublishJob(
        job_id=job_id,
        created_at=time.time(),
        video_path=str(vpath),
        poster_path=poster_path,
        platforms=platforms,
        dry_run=dry_run,
        platform_states={
            p: {"status": "queued", "label": PLATFORM_LABELS.get(p, p), "message": "Waiting to start"}
            for p in platforms
        },
    )
    ACTIVE_JOBS[job_id] = job
    asyncio.create_task(_execute_job(job, vpath, covers, copy_payload, options))
    return job


async def _execute_job(
    job: PublishJob,
    vpath: Path,
    covers: dict[str, str | None],
    copy_payload: dict[str, Any],
    options: dict[str, Any],
) -> None:
    settings = load_settings()
    accounts = settings.get("accounts") or {}
    headless = bool(options.get("headless", settings.get("headless_upload", False)))
    mp_cfg = settings.get("multipost") or {}

    mode_banner = "DRY-RUN PREFLIGHT" if job.dry_run else "LIVE PUBLISH"
    job.log(f"=== Starting {mode_banner} for {vpath.name} -> {', '.join(job.platforms)} ===")

    chrome_info = await is_studio_chrome_running()
    active_cdp = CDP_URL if chrome_info["running"] else ""
    if active_cdp:
        job.log(f"[Chrome Bridge] Live Studio Chrome detected ({active_cdp})! Syncing sessions & attaching directly...")
        try:
            await sync_login_from_studio_chrome()
        except Exception as exc:
            job.log(f"[Chrome Bridge] Sync warning: {exc}")

    if mp_cfg.get("enabled") and not job.dry_run:
        try:
            p_cover = Path(covers["original"]) if covers.get("original") else None
            await publish_via_multipost(vpath, p_cover, copy_payload, job.platforms, settings, job.log)
            for p in job.platforms:
                job.platform_states[p] = {
                    "status": "success",
                    "label": PLATFORM_LABELS.get(p, p),
                    "message": "Dispatched to MultiPost Browser Extension",
                }
            job.status = "completed"
            _save_job_history(job)
            return
        except Exception as exc:
            job.log(f"[MultiPost] Extension unreachable ({exc}); falling back to native uploaders...")

    # Execute platforms sequentially for domestic browser safety (prevents simultaneous Chrome CPU spikes),
    # while streaming crystal-clear per-platform progress!
    success_count = 0
    for plat in job.platforms:
        label = PLATFORM_LABELS.get(plat, plat)
        job.platform_states[plat]["status"] = "running"
        job.platform_states[plat]["message"] = "Validating & preparing upload..."
        job.log(f"--- [{label}] Starting ---")

        try:
            if plat in SAU_PLATFORM_MAP:
                acc = accounts.get(plat, "creator")
                meta = copy_payload.get(plat) or {}
                cli_args = build_sau_upload_args(plat, acc, vpath, covers, meta, options, headless)
                cookie_file = _sau_cookie_path(SAU_PLATFORM_MAP[plat], acc)

                if job.dry_run:
                    cookie_note = "Cookie READY" if cookie_file.is_file() else "Cookie NOT connected yet"
                    job.log(f"[{label}] Route: Direct CN IP (Proxy stripped)")
                    job.log(f"[{label}] Account '{acc}': {cookie_note} ({cookie_file.name})")
                    job.log(f"[{label}] Command validated: sau {' '.join(cli_args)}")
                    job.platform_states[plat] = {
                        "status": "success",
                        "label": label,
                        "message": f"Preflight passed ({cookie_note})",
                    }
                    success_count += 1
                    continue

                if not cookie_file.is_file() and not active_cdp:
                    raise RuntimeError(
                        f"{label} account '{acc}' is not logged in yet. Click 'Open Real Chrome' to log in first."
                    )

                job.platform_states[plat]["message"] = (
                    "Publishing in your live Chrome tab..." if active_cdp else "Uploading via Creator Studio..."
                )
                code, out = await _run_sau_subprocess(
                    cli_args,
                    settings,
                    log_fn=lambda ln, lb=label: job.log(f"[{lb}] {ln}"),
                    cdp_url=active_cdp,
                )
                if code != 0:
                    raise RuntimeError(f"Uploader exited with code {code}. Tail: {out[-300:]}")

                job.platform_states[plat] = {
                    "status": "success",
                    "label": label,
                    "message": "Published successfully!",
                }
                job.log(f"[{label}] Completed successfully!")
                success_count += 1

            elif plat == "x":
                acc = accounts.get("x", "creator")
                x_meta = copy_payload.get("x") or {}
                use_long = bool((settings.get("x_config") or {}).get("is_premium")) and bool(options.get("x_use_long_note"))
                post_text = (x_meta.get("long_note") if use_long else x_meta.get("text")) or x_meta.get("text") or vpath.stem
                x_stat = await check_x_cookie_fast(acc, settings)

                if job.dry_run:
                    net = settings.get("network") or {}
                    route = net.get("x_proxy_url") if net.get("x_use_proxy") else "Direct"
                    job.log(f"[X] Mode: {x_stat['mode']} | Route: {route} | Auth: {x_stat['detail']}")
                    job.log(f"[X] Post length: {len(post_text)} chars | Video: {vpath.name}")
                    job.platform_states["x"] = {
                        "status": "success",
                        "label": label,
                        "message": f"Preflight passed ({x_stat['detail']})",
                    }
                    success_count += 1
                    continue

                job.platform_states["x"]["message"] = "Uploading video to X..."
                prev_cdp = os.environ.get("SAU_CDP_URL")
                try:
                    if active_cdp:
                        os.environ["SAU_CDP_URL"] = active_cdp
                    res = await upload_x_video(vpath, post_text, acc, settings, job.log)
                finally:
                    if prev_cdp is None:
                        os.environ.pop("SAU_CDP_URL", None)
                    else:
                        os.environ["SAU_CDP_URL"] = prev_cdp
                msg = f"Published ({res.get('post_url')})" if res.get("post_url") else "Published to X!"
                job.platform_states["x"] = {
                    "status": "success",
                    "label": label,
                    "message": msg,
                }
                success_count += 1
            else:
                raise ValueError(f"Unknown platform '{plat}'")

        except Exception as exc:
            job.log(f"[{label}] ERROR: {exc}")
            job.platform_states[plat] = {
                "status": "failed",
                "label": label,
                "message": str(exc),
            }

    if success_count == len(job.platforms):
        job.status = "completed"
    elif success_count > 0:
        job.status = "partial_failed"
    else:
        job.status = "failed"

    job.log(f"=== Job finished with status: {job.status.upper()} ({success_count}/{len(job.platforms)} succeeded) ===")
    _save_job_history(job)
