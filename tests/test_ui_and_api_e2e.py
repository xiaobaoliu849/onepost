from __future__ import annotations

import re
import time
from pathlib import Path

from fastapi.testclient import TestClient

import app.asset_scanner as asset_scanner
import app.chrome_bridge as chrome_bridge
import app.config as config
import app.main as main_mod
import app.publisher as publisher
import app.x_uploader as x_uploader


def _isolate_all(monkeypatch, tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    staging = data_dir / "staging"
    covers = data_dir / "covers"
    sau = tmp_path / "sau"
    cfg = data_dir / "settings.json"
    jobs = data_dir / "jobs_history.json"

    for d in (data_dir, staging, covers, sau / "cookies"):
        d.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(config, "DATA_DIR", data_dir)
    monkeypatch.setattr(config, "CONFIG_FILE", cfg)
    monkeypatch.setattr(config, "JOBS_FILE", jobs)
    monkeypatch.setattr(config, "UPLOADS_STAGING_DIR", staging)
    monkeypatch.setattr(config, "COVERS_CACHE_DIR", covers)
    monkeypatch.setattr(config, "VENDOR_SAU_DIR", sau)

    monkeypatch.setattr(main_mod, "DATA_DIR", data_dir)
    monkeypatch.setattr(main_mod, "UPLOADS_STAGING_DIR", staging)
    monkeypatch.setattr(main_mod, "VENDOR_SAU_DIR", sau)
    monkeypatch.setattr(asset_scanner, "COVERS_CACHE_DIR", covers)
    monkeypatch.setattr(publisher, "JOBS_FILE", jobs)
    monkeypatch.setattr(publisher, "VENDOR_SAU_DIR", sau)
    monkeypatch.setattr(x_uploader, "VENDOR_SAU_DIR", sau)
    monkeypatch.setattr(chrome_bridge, "VENDOR_SAU_DIR", sau)

    publisher.ACTIVE_JOBS.clear()


def test_static_dom_id_integrity() -> None:
    root = Path(__file__).resolve().parent.parent
    html = (root / "static" / "index.html").read_text(encoding="utf-8")
    js = (root / "static" / "app.js").read_text(encoding="utf-8")

    html_ids = set(re.findall(r'\bid="([^"]+)"', html))
    js_literal_ids = set(re.findall(r'\$\("([^"]+)"\)', js))

    missing = sorted(js_literal_ids - html_ids)
    assert not missing, f"DOM IDs referenced in app.js missing from index.html: {missing}"

    dynamic_expected = {
        "chk_douyin", "chk_shipinhao", "chk_weibo", "chk_x",
        "cardStatus_douyin", "cardStatus_shipinhao", "cardStatus_weibo", "cardStatus_x",
        "pills_dy_tags", "pills_sp_tags", "pills_wb_tags",
        "thumb_portrait_3x4", "thumb_landscape_4x3", "thumb_weibo_cover",
    }
    missing_dyn = sorted(dynamic_expected - html_ids)
    assert not missing_dyn, f"Dynamic DOM IDs missing from index.html: {missing_dyn}"


def test_e2e_studio_api_flow(monkeypatch, tmp_path: Path) -> None:
    _isolate_all(monkeypatch, tmp_path)

    # Copy demo video into isolated vendor dir if present in repo so demo bundle has a real video
    repo_demo = Path(__file__).resolve().parent.parent / "vendor" / "social-auto-upload" / "videos" / "demo.mp4"
    if repo_demo.is_file():
        iso_videos = tmp_path / "sau" / "videos"
        iso_videos.mkdir(parents=True, exist_ok=True)
        (iso_videos / "demo.mp4").write_bytes(repo_demo.read_bytes())

    async def fake_chrome_status():
        return {"running": False, "cdp_url": chrome_bridge.CDP_URL, "browser": None}

    monkeypatch.setattr(publisher, "is_studio_chrome_running", fake_chrome_status)
    monkeypatch.setattr(chrome_bridge, "is_studio_chrome_running", fake_chrome_status)

    client = TestClient(main_mod.app)

    # 1. HTML & Health
    r_index = client.get("/")
    assert r_index.status_code == 200
    assert "OnePost" in r_index.text

    r_health = client.get("/api/health")
    assert r_health.status_code == 200
    assert r_health.json()["ok"] is True

    # 2. Settings round-trip preserving multipost.api_key & brand signatures
    r_get_cfg = client.get("/api/settings")
    assert r_get_cfg.status_code == 200
    cfg = r_get_cfg.json()
    cfg["default_watch_dir"] = str(tmp_path)
    cfg["ai"]["brand_signature_cn"] = "— OnePost Studio"
    cfg["ai"]["brand_signature_en"] = "— OnePost"
    cfg["multipost"]["api_key"] = "mp-secret-token"
    r_post_cfg = client.post("/api/settings", json=cfg)
    assert r_post_cfg.status_code == 200
    saved = r_post_cfg.json()
    assert saved["multipost"]["api_key"] == "mp-secret-token"
    assert saved["ai"]["brand_signature_cn"] == "— OnePost Studio"
    assert saved["default_watch_dir"] == str(tmp_path)

    # 3. AI Test without API key -> clean failure payload
    r_ai_test = client.post("/api/ai/test", json={"api_key": ""})
    assert r_ai_test.status_code == 200
    assert r_ai_test.json()["ok"] is False

    # 4. Demo bundle load -> real video probe + 3 cover variants + karaoke subs
    r_demo = client.post("/api/bundle/demo")
    assert r_demo.status_code == 200
    demo_payload = r_demo.json()
    bundle = demo_payload["bundle"]
    assert bundle["video"] is not None
    assert bundle["video"]["duration_sec"] >= 0
    assert bundle["poster"]["variants"]["portrait_3x4"]
    assert bundle["poster"]["variants"]["landscape_4x3"]
    assert bundle["poster"]["variants"]["weibo_cover"]
    assert bundle["subtitle"]["is_karaoke"] is True

    # 5. Copy generation across two distinct hint tones
    r_copy1 = client.post("/api/copy/generate", json={"bundle": bundle, "user_hint": "突出开头前15秒最反直觉的高能悬念"})
    r_copy2 = client.post("/api/copy/generate", json={"bundle": bundle, "user_hint": "极简克制、高信息密度总结核心看点"})
    assert r_copy1.status_code == 200 and r_copy2.status_code == 200
    c1 = r_copy1.json()["copy"]
    c2 = r_copy2.json()["copy"]
    assert c1["douyin"]["title"] != c2["douyin"]["title"]

    # 6. Synchronous preflight check & dry-run job lifecycle
    req_body = {
        "video_path": bundle["video"]["file_path"],
        "poster_path": bundle["poster"]["variants"]["original"],
        "covers": bundle["poster"]["variants"],
        "platforms": ["douyin", "shipinhao", "weibo", "x"],
        "copy_payload": c1,
        "options": {},
        "dry_run": True,
    }
    r_pf = client.post("/api/preflight", json=req_body)
    assert r_pf.status_code == 200
    pf = r_pf.json()
    assert set(pf["platforms"].keys()) == {"douyin", "shipinhao", "weibo", "x"}

    r_pub = client.post("/api/publish", json=req_body)
    assert r_pub.status_code == 200
    job_id = r_pub.json()["job"]["job_id"]

    # Poll briefly until dry-run finishes and verify in /api/jobs
    final_job = None
    for _ in range(30):
        r_job = client.get(f"/api/jobs/{job_id}")
        assert r_job.status_code == 200
        final_job = r_job.json()["job"]
        if final_job["status"] != "running":
            break
        time.sleep(0.05)

    assert final_job is not None
    assert final_job["status"] != "running"
    r_jobs = client.get("/api/jobs")
    assert r_jobs.status_code == 200
    assert any(j["job_id"] == job_id for j in r_jobs.json()["jobs"])
