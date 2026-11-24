"""Single-Prompt / Single-Command Multi-Platform Video Publisher CLI.

Examples:
  # 1. Dry-run preflight on a finished highlight folder (auto-discovers video + poster card + karaoke SRT)
  python publish_cli.py --folder "D:/Videos/Ep01_Highlight" --dry-run

  # 2. One-command live publish to Douyin, Shipinhao, Weibo, and X with an AI prompt angle
  python publish_cli.py --folder "D:/Videos/Ep01_Highlight" --prompt "Focus on the teaser hook insight" --platforms douyin,shipinhao,weibo,x

  # 3. Connect/login to a platform account once (saves cookies locally)
  python publish_cli.py --login shipinhao
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from app.ai_copywriter import generate_platform_copy
from app.asset_scanner import scan_bundle_directory
from app.config import load_settings
from app.publisher import (
    ACTIVE_JOBS,
    cancel_job,
    get_all_accounts_status,
    start_publish_job,
    trigger_platform_login,
)


async def async_main() -> int:
    parser = argparse.ArgumentParser(description="OnePost — One-Command Multi-Platform Publisher")
    parser.add_argument("--folder", help="Folder containing finished video (.mp4), poster card (.png/.jpg), and karaoke .srt")
    parser.add_argument("--platforms", default="douyin,shipinhao,weibo,x", help="Comma-separated platforms: douyin,shipinhao,weibo,x")
    parser.add_argument("--prompt", default="", help="Single prompt / angle to guide AI platform copywriting")
    parser.add_argument("--schedule", default="", help="Optional schedule 'YYYY-MM-DD HH:MM' (Douyin & Shipinhao)")
    parser.add_argument("--draft", action="store_true", help="Save Shipinhao upload as draft")
    parser.add_argument("--dry-run", action="store_true", help="Validate assets, copy, routes & cookies without uploading")
    parser.add_argument("--login", choices=["douyin", "shipinhao", "weibo", "x"], help="Open browser to log in & save platform cookies")
    parser.add_argument("--status", action="store_true", help="Show account connection status for all 4 platforms")
    args = parser.parse_args()

    if args.status:
        statuses = await get_all_accounts_status(deep_verify=False)
        for plat, info in statuses.items():
            badge = "READY" if info["ready"] else "NOT CONNECTED"
            print(f"  [{badge:13}] {info['label']:22} | Route: {info['route']:24} | {info['detail']}")
        return 0

    if args.login:
        print(f"Opening Chrome to connect '{args.login}'...")
        res = await trigger_platform_login(args.login)
        print(res)
        return 0 if res.get("success") else 1

    if not args.folder:
        parser.print_help()
        return 1

    folder = Path(args.folder).expanduser().resolve()
    print(f"[1/3] Scanning bundle folder: {folder}")
    bundle = scan_bundle_directory(folder)
    video = bundle.get("video")
    if not video:
        print("ERROR: No video file (.mp4/.mov/.mkv) found in folder.")
        return 1

    poster = bundle.get("poster") or {}
    sub = bundle.get("subtitle") or {}
    print(f"      Video   : {video['file_name']} ({video['size_mb']} MB)")
    print(f"      Poster  : {poster.get('file_name') or 'None'}")
    print(f"      Karaoke : {sub.get('file_name') or 'None'} ({sub.get('cue_count', 0)} cues)")

    settings = load_settings()
    print("[2/3] Generating platform-native copy from Karaoke SRT...")
    copy_payload = await generate_platform_copy(bundle, settings, user_hint=args.prompt)
    print(f"      Copy source: {copy_payload.get('source')}")
    print(f"      Douyin title    : {copy_payload['douyin']['title']}")
    print(f"      Shipinhao short : {copy_payload['shipinhao']['short_title']}")
    print(f"      Weibo title     : {copy_payload['weibo']['title']}")
    print(f"      X post preview  : {copy_payload['x']['text'][:90]}...")

    platforms = [p.strip().lower() for p in args.platforms.split(",") if p.strip()]
    print(f"[3/3] Dispatching {'DRY-RUN' if args.dry_run else 'LIVE PUBLISH'} -> {', '.join(platforms)}")

    job = start_publish_job(
        video_path=video["file_path"],
        poster_path=(poster.get("variants") or {}).get("original"),
        covers=poster.get("variants") or {},
        platforms=platforms,
        copy_payload=copy_payload,
        options={"schedule": args.schedule, "shipinhao_draft": args.draft},
        dry_run=args.dry_run,
    )

    seen_logs = 0
    try:
        while True:
            live = ACTIVE_JOBS.get(job.job_id)
            if not live:
                break
            while seen_logs < len(live.logs):
                print("  " + live.logs[seen_logs])
                seen_logs += 1
            if live.status != "running":
                break
            await asyncio.sleep(0.5)
    except KeyboardInterrupt:
        print("\n[!] Cancelling active publish job...")
        await cancel_job(job.job_id)
        return 130

    if args.dry_run and job.issues:
        print("\nPreflight Issues Summary:")
        for plat, items in job.issues.items():
            for msg in items:
                print(f"  - [{plat}] {msg}")

    return 0 if job.status == "completed" else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(async_main()))
