from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path

from fastapi.testclient import TestClient
from PIL import Image

import app.asset_scanner as asset_scanner
import app.chrome_bridge as chrome_bridge
import app.config as config
import app.main as main_mod
from app.ai_copywriter import generate_platform_copy
from app.srt_parser import parse_subtitle_file


def _isolate_storage(monkeypatch, tmp_path: Path) -> None:
    staging = tmp_path / "staging"
    covers = tmp_path / "covers"
    sau = tmp_path / "sau"
    cfg = tmp_path / "settings.json"
    jobs = tmp_path / "jobs_history.json"
    staging.mkdir(parents=True, exist_ok=True)
    covers.mkdir(parents=True, exist_ok=True)
    sau.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(config, "CONFIG_FILE", cfg)
    monkeypatch.setattr(config, "JOBS_FILE", jobs)
    monkeypatch.setattr(config, "UPLOADS_STAGING_DIR", staging)
    monkeypatch.setattr(config, "COVERS_CACHE_DIR", covers)
    monkeypatch.setattr(config, "VENDOR_SAU_DIR", sau)

    monkeypatch.setattr(main_mod, "UPLOADS_STAGING_DIR", staging)
    monkeypatch.setattr(main_mod, "VENDOR_SAU_DIR", sau)
    monkeypatch.setattr(asset_scanner, "COVERS_CACHE_DIR", covers)
    monkeypatch.setattr(chrome_bridge, "VENDOR_SAU_DIR", sau)


def test_media_range_requests_and_416(monkeypatch, tmp_path: Path) -> None:
    _isolate_storage(monkeypatch, tmp_path)
    sample = tmp_path / "clip.mp4"
    sample.write_bytes(b"0123456789abcdefghij")  # 20 bytes

    empty = tmp_path / "empty.mp4"
    empty.write_bytes(b"")

    client = TestClient(main_mod.app)

    r_ok = client.get("/api/media", params={"path": str(sample)}, headers={"Range": "bytes=0-4"})
    assert r_ok.status_code == 206
    assert r_ok.content == b"01234"
    assert r_ok.headers["content-range"] == "bytes 0-4/20"

    r_suffix = client.get("/api/media", params={"path": str(sample)}, headers={"Range": "bytes=-5"})
    assert r_suffix.status_code == 206
    assert r_suffix.content == b"fghij"
    assert r_suffix.headers["content-range"] == "bytes 15-19/20"

    r_oob = client.get("/api/media", params={"path": str(sample)}, headers={"Range": "bytes=50-60"})
    assert r_oob.status_code == 416
    assert r_oob.headers["content-range"] == "bytes */20"

    r_bad = client.get("/api/media", params={"path": str(sample)}, headers={"Range": "bytes=foo-bar"})
    assert r_bad.status_code == 416

    r_empty_plain = client.get("/api/media", params={"path": str(empty)})
    assert r_empty_plain.status_code == 200
    assert r_empty_plain.content == b""

    r_empty_range = client.get("/api/media", params={"path": str(empty)}, headers={"Range": "bytes=0-4"})
    assert r_empty_range.status_code == 416


def test_probe_video_metadata_reads_real_dimensions() -> None:
    demo_vid = Path(__file__).resolve().parent.parent / "vendor" / "social-auto-upload" / "videos" / "demo.mp4"
    if not demo_vid.is_file():
        return
    meta = asset_scanner.probe_video_metadata(demo_vid)
    assert meta["width"] > 0
    assert meta["height"] > 0
    assert meta["duration_sec"] > 0
    assert meta["duration_label"] != "Ready to publish"
    assert meta["aspect_label"] != "MP4"


def test_cover_cache_hits_and_invalidates_on_change(monkeypatch, tmp_path: Path) -> None:
    _isolate_storage(monkeypatch, tmp_path)
    poster = tmp_path / "poster.png"
    Image.new("RGB", (800, 1200), color=(24, 32, 56)).save(poster, format="PNG")

    first = asset_scanner.prepare_cover_variants(poster)
    assert first["portrait_3x4"] and Path(first["portrait_3x4"]).is_file()
    assert Path(first["weibo_cover"]).stat().st_size <= asset_scanner.MAX_WEIBO_COVER_BYTES

    mtime_before = Path(first["portrait_3x4"]).stat().st_mtime_ns
    second = asset_scanner.prepare_cover_variants(poster)
    assert second["portrait_3x4"] == first["portrait_3x4"]
    assert Path(second["portrait_3x4"]).stat().st_mtime_ns == mtime_before

    # Modify poster with new size and timestamp -> cache key must change
    time.sleep(0.02)
    Image.new("RGB", (900, 1200), color=(200, 90, 40)).save(poster, format="PNG")
    os.utime(poster, None)
    third = asset_scanner.prepare_cover_variants(poster)
    assert third["portrait_3x4"] != first["portrait_3x4"]


def test_srt_parser_keeps_over_800_karaoke_cues(tmp_path: Path) -> None:
    srt = tmp_path / "long_karaoke.srt"
    blocks = []
    for i in range(1, 951):
        start_s = i - 1
        h, m, s = start_s // 3600, (start_s % 3600) // 60, start_s % 60
        blocks.append(
            f"{i}\n{h:02d}:{m:02d}:{s:02d},000 --> {h:02d}:{m:02d}:{s:02d},800\n"
            f'<font color="#f97316">Word{i}</font> line content\n'
        )
    srt.write_text("\n".join(blocks), encoding="utf-8")
    parsed = parse_subtitle_file(srt)
    assert parsed["is_karaoke"] is True
    assert parsed["cue_count"] == 950
    assert len(parsed["cues"]) == 950


def test_offline_copywriter_adapts_to_user_hint() -> None:
    bundle = {
        "bundle_name": "ai_workflow_highlight",
        "video": {"file_name": "ai_workflow_highlight.mp4", "duration_sec": 195.0},
        "subtitle": {
            "teaser_hook_text": "Most creators waste hours re-uploading videos by hand.",
            "sentences": [
                {"time_label": "00:00", "text": "Most creators waste hours re-uploading videos by hand."},
                {"time_label": "01:10", "text": "One prompt adapts copy to four platforms automatically."},
                {"time_label": "02:30", "text": "Real Chrome CDP publishing avoids headless QR blocks."},
            ],
            "transcript_excerpt": "...",
        },
    }
    settings = {"ai": {"api_key": ""}}
    viral = asyncio.run(generate_platform_copy(bundle, settings, user_hint="突出开头前15秒最反直觉的高能悬念"))
    minimal = asyncio.run(generate_platform_copy(bundle, settings, user_hint="极简克制、高信息密度总结核心看点"))

    assert viral["douyin"]["title"] != minimal["douyin"]["title"]
    assert 6 <= len(viral["shipinhao"]["short_title"]) <= 16
    assert 6 <= len(minimal["shipinhao"]["short_title"]) <= 16


def test_import_cookies_handles_null_expiry_and_windows_curl(monkeypatch, tmp_path: Path) -> None:
    _isolate_storage(monkeypatch, tmp_path)

    # Reject unknown platform
    try:
        chrome_bridge.import_cookies_from_text("tiktok", "[]")
        assert False, "Expected ValueError for unsupported platform"
    except ValueError:
        pass

    # Cookie-Editor JSON with expirationDate: null
    json_payload = json.dumps([
        {"name": "sessionid", "value": "abc123", "domain": ".douyin.com", "expirationDate": None},
    ])
    out_dy = chrome_bridge.import_cookies_from_text("douyin", json_payload)
    saved_dy = json.loads(out_dy.read_text(encoding="utf-8"))
    assert saved_dy["cookies"][0]["expires"] == -1

    # Windows CMD Copy as cURL with caret escaping
    win_curl = 'curl ^"https://weibo.com/ajax^" ^\n  -H ^"cookie: SUB=token1^; SUBP=token2^"'
    out_wb = chrome_bridge.import_cookies_from_text("weibo", win_curl)
    saved_wb = json.loads(out_wb.read_text(encoding="utf-8"))
    names = {c["name"]: c["value"] for c in saved_wb["cookies"]}
    assert names == {"SUB": "token1", "SUBP": "token2"}


def test_bundle_upload_rejects_unsupported_and_merges_existing_slots(monkeypatch, tmp_path: Path) -> None:
    _isolate_storage(monkeypatch, tmp_path)
    client = TestClient(main_mod.app)

    # Unsupported-only drop -> 400 and no empty directory left behind
    r_bad = client.post(
        "/api/bundle/upload",
        files=[("files", ("notes.txt", b"hello", "text/plain"))],
    )
    assert r_bad.status_code == 400
    assert list((tmp_path / "staging").iterdir()) == []

    # Multi-file upload with mixed ignored file + scoring
    poster_bytes_io = tmp_path / "src_poster.png"
    Image.new("RGB", (600, 800), color=(15, 25, 45)).save(poster_bytes_io, format="PNG")
    poster_bytes = poster_bytes_io.read_bytes()

    r_ok = client.post(
        "/api/bundle/upload",
        data={"bundle_title": "Ep04"},
        files=[
            ("files", ("raw_take.mp4", b"1234567890", "video/mp4")),
            ("files", ("final_highlight.mp4", b"12345", "video/mp4")),
            ("files", ("poster_cover.png", poster_bytes, "image/png")),
            ("files", ("readme.txt", b"ignore me", "text/plain")),
        ],
    )
    assert r_ok.status_code == 200
    payload = r_ok.json()
    assert payload["ignored_files"] == ["readme.txt"]
    assert payload["bundle"]["video"]["file_name"] == "final_highlight.mp4"
    video_path = payload["bundle"]["video"]["file_path"]
    poster_path = payload["bundle"]["poster"]["variants"]["original"]

    # Single-slot SRT replacement preserving existing video & poster
    r_sub = client.post(
        "/api/bundle/upload",
        data={
            "bundle_title": "Ep04",
            "existing_video": video_path,
            "existing_poster": poster_path,
        },
        files=[("files", ("karaoke.srt", b"1\n00:00:00,000 --> 00:00:02,000\nHook line\n", "text/plain"))],
    )
    assert r_sub.status_code == 200
    merged = r_sub.json()["bundle"]
    assert merged["video"]["file_path"] == video_path
    assert merged["poster"]["variants"]["original"] == poster_path
    assert merged["subtitle"]["file_name"] == "karaoke.srt"
