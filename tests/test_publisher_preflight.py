from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime, timedelta
from pathlib import Path

from PIL import Image

import app.chrome_bridge as chrome_bridge
import app.config as config
import app.publisher as publisher
import app.x_uploader as x_uploader


def _isolate_publisher(monkeypatch, tmp_path: Path) -> Path:
    sau = tmp_path / "sau"
    (sau / "cookies").mkdir(parents=True, exist_ok=True)
    cfg = tmp_path / "settings.json"
    jobs = tmp_path / "jobs_history.json"
    monkeypatch.setattr(config, "CONFIG_FILE", cfg)
    monkeypatch.setattr(config, "JOBS_FILE", jobs)
    monkeypatch.setattr(config, "VENDOR_SAU_DIR", sau)
    monkeypatch.setattr(publisher, "VENDOR_SAU_DIR", sau)
    monkeypatch.setattr(publisher, "JOBS_FILE", jobs)
    monkeypatch.setattr(x_uploader, "VENDOR_SAU_DIR", sau)
    monkeypatch.setattr(chrome_bridge, "VENDOR_SAU_DIR", sau)
    publisher.ACTIVE_JOBS.clear()
    config.save_settings(config.DEFAULT_SETTINGS)
    return sau


def test_preflight_catches_real_errors_and_passes_when_valid(monkeypatch, tmp_path: Path) -> None:
    sau = _isolate_publisher(monkeypatch, tmp_path)

    async def fake_chrome_status():
        return {"running": False, "cdp_url": chrome_bridge.CDP_URL, "browser": None}

    monkeypatch.setattr(publisher, "is_studio_chrome_running", fake_chrome_status)

    video = tmp_path / "sample.mp4"
    video.write_bytes(b"\x00" * 1024)
    cover = tmp_path / "cover.jpg"
    Image.new("RGB", (640, 360), color=(20, 40, 80)).save(cover, format="JPEG")

    bad_copy = {
        "douyin": {"title": "Valid Douyin Title", "description": "desc", "tags": ["t1"]},
        "shipinhao": {"short_title": "短", "title": "Valid", "description": "desc", "tags": []},
        "weibo": {"title": "Weibo Title", "description": "desc", "tags": []},
        "x": {"text": "A" * 305},
    }

    # 1. Missing cookies, bad short_title, missing Weibo cover, bad X length, past schedule
    past_time = (datetime.now() - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M")
    res_bad = asyncio.run(
        publisher.run_preflight_summary(
            video_path=str(video),
            poster_path=None,
            covers={},
            platforms=["douyin", "shipinhao", "weibo", "x"],
            copy_payload=bad_copy,
            options={"schedule": past_time, "x_use_long_note": False},
        )
    )
    assert res_bad["ok"] is False
    assert res_bad["platforms"]["douyin"]["ok"] is False
    assert any("10 minutes" in e for e in res_bad["platforms"]["douyin"]["errors"])
    assert any("6–16" in e for e in res_bad["platforms"]["shipinhao"]["errors"])
    assert any("cover" in e.lower() for e in res_bad["platforms"]["weibo"]["errors"])
    assert any("280" in e for e in res_bad["platforms"]["x"]["errors"])

    # 2. Write valid cookie files and supply valid copy, cover, future schedule -> passes!
    for fname in ("douyin_creator.json", "tencent_creator.json", "weibo_creator.json"):
        (sau / "cookies" / fname).write_text(json.dumps({"cookies": [{"name": "ok", "value": "1"}]}), encoding="utf-8")
    x_uploader.save_x_tokens_cookie("creator", "auth123", "ct0123")

    future_time = (datetime.now() + timedelta(hours=2)).strftime("%Y-%m-%d %H:%M")
    good_copy = {
        "douyin": {"title": "Douyin Ready", "description": "desc", "tags": ["tag1"]},
        "shipinhao": {"short_title": "精选高光深度版", "title": "Shipinhao Ready", "description": "desc", "tags": []},
        "weibo": {"title": "Weibo Ready", "description": "desc", "tags": []},
        "x": {"text": "Concise English teaser hook under 280 chars."},
    }
    res_good = asyncio.run(
        publisher.run_preflight_summary(
            video_path=str(video),
            poster_path=str(cover),
            covers={"original": str(cover), "weibo_cover": str(cover)},
            platforms=["douyin", "shipinhao", "weibo", "x"],
            copy_payload=good_copy,
            options={"schedule": future_time, "x_use_long_note": False},
        )
    )
    assert res_good["ok"] is True
    for plat in ("douyin", "shipinhao", "weibo", "x"):
        assert res_good["platforms"][plat]["ok"] is True
        assert res_good["platforms"][plat]["status"] == "success"


def test_dry_run_job_marks_failed_when_preflight_fails(monkeypatch, tmp_path: Path) -> None:
    _isolate_publisher(monkeypatch, tmp_path)

    async def fake_chrome_status():
        return {"running": False, "cdp_url": chrome_bridge.CDP_URL, "browser": None}

    monkeypatch.setattr(publisher, "is_studio_chrome_running", fake_chrome_status)

    video = tmp_path / "clip.mp4"
    video.write_bytes(b"1234")

    async def _run():
        job = publisher.start_publish_job(
            video_path=str(video),
            poster_path=None,
            covers={},
            platforms=["weibo"],
            copy_payload={"weibo": {"title": "T", "description": "D", "tags": []}},
            options={},
            dry_run=True,
        )
        for _ in range(40):
            if job.status != "running":
                break
            await asyncio.sleep(0.02)
        return job

    job = asyncio.run(_run())
    assert job.status == "failed"
    assert job.platform_states["weibo"]["status"] == "failed"
    assert job.issues.get("weibo")


def test_cancel_job_stops_running_job_cleanly(monkeypatch, tmp_path: Path) -> None:
    sau = _isolate_publisher(monkeypatch, tmp_path)
    (sau / "cookies" / "douyin_creator.json").write_text('{"cookies":[{"name":"a","value":"1"}]}', encoding="utf-8")

    async def fake_chrome_status():
        return {"running": False, "cdp_url": chrome_bridge.CDP_URL, "browser": None}

    async def slow_subprocess(*args, **kwargs):
        await asyncio.sleep(5)
        return (0, "")

    monkeypatch.setattr(publisher, "is_studio_chrome_running", fake_chrome_status)
    monkeypatch.setattr(publisher, "_run_sau_subprocess", slow_subprocess)

    video = tmp_path / "clip.mp4"
    video.write_bytes(b"1234")

    async def _run():
        job = publisher.start_publish_job(
            video_path=str(video),
            poster_path=None,
            covers={},
            platforms=["douyin", "weibo"],
            copy_payload={"douyin": {"title": "Hi"}, "weibo": {"title": "Hi"}},
            dry_run=False,
        )
        await asyncio.sleep(0.05)
        cancelled = await publisher.cancel_job(job.job_id)
        await asyncio.sleep(0.05)
        return cancelled

    res = asyncio.run(_run())
    assert res["status"] == "cancelled"
    assert res["cancelled"] is True
    assert res["platform_states"]["douyin"]["status"] == "cancelled"
    assert res["platform_states"]["weibo"]["status"] == "cancelled"


def test_upload_x_video_passes_cdp_url_without_mutating_env(monkeypatch, tmp_path: Path) -> None:
    _isolate_publisher(monkeypatch, tmp_path)
    captured = {}

    async def fake_browser_upload(video_path, post_text, account_name, settings, log, cdp_url=""):
        captured["cdp_url"] = cdp_url
        captured["env_val"] = os.environ.get("SAU_CDP_URL")
        return {"status": "published", "platform": "x", "mode": "browser"}

    monkeypatch.setattr(x_uploader, "_upload_x_via_browser", fake_browser_upload)
    monkeypatch.delenv("SAU_CDP_URL", raising=False)

    res = asyncio.run(
        x_uploader.upload_x_video(
            tmp_path / "v.mp4",
            "Hello X",
            "creator",
            {"x_config": {"mode": "browser"}},
            lambda _: None,
            cdp_url="http://127.0.0.1:9333",
        )
    )
    assert res["status"] == "published"
    assert captured["cdp_url"] == "http://127.0.0.1:9333"
    assert captured["env_val"] is None
    assert "SAU_CDP_URL" not in os.environ
