# OnePost Improvement & Polish Roadmap

> Living tracker of root-cause bugs, upload/processing upgrades, UI/UX interaction polish, automated test coverage, and next-step evolution for **OnePost**.

---

## Root-Cause Audit Summary

1. **Unsafe Live Publish Fallback (`static/app.js`)**: Clicking **Publish Selected** before loading a bundle silently loads the demo bundle and immediately starts publishing it to connected accounts.
2. **Upload & Processing Blind Spots (`static/app.js`, `app/main.py`)**:
   - File drops use bare `fetch()` with no upload progress bar, stage indicator, or cancel button.
   - `shutil.copyfileobj` runs synchronously on the async FastAPI event loop; unsupported files create empty staging folders and return HTTP 200; multi-file drops skip candidate scoring; dropping a single replacement file replaces the entire bundle instead of merging slots; `#fileInputBundle` isn't reset after upload; `dragleave` flickers on child nodes.
3. **Media Streaming & Video Probing (`app/main.py`, `app/asset_scanner.py`)**:
   - `/api/media` crashes with HTTP 500 on suffix/malformed/out-of-bounds `Range` headers and zero-byte files.
   - `probe_video_metadata` hardcodes `duration_sec = 0.0`, `width = 0`, `height = 0`, missing X Standard's 140s limit warning.
   - Cover generation cache keys ignore `mtime`/`size`, always re-render on every scan, and don't cap output JPEGs under Weibo's `< 5 MB` limit.
4. **Preflight, Cancellation & CDP Safety (`app/publisher.py`, `app/x_uploader.py`, `app/chrome_bridge.py`, `vendor/social-auto-upload`)**:
   - Dry-run preflight unconditionally reports `success` even when cookies are missing or titles/covers/schedules are invalid.
   - Running jobs cannot be cancelled; failed platforms cannot be retried individually; `ACTIVE_JOBS` grows unbounded.
   - `x_use_proxy` defaults disagree across `config.py`, `publisher.py`, and `x_uploader.py`; `SAU_CDP_URL` mutates global `os.environ`.
   - `DouYinVideo.upload` lacks `try...finally` cleanup (leaking tabs on error); `sync_login_from_studio_chrome` has no CDP lock; `import_cookies_from_text` crashes on `expirationDate: null`, Windows cURL caret escaping, or invalid platform names.
5. **UI/UX Interaction Gaps (`static/index.html`, `static/app.js`, `static/styles.css`)**:
   - Connect Modal Tab 2 hardcodes `"douyin"` when opened from the navbar; no toast feedback system; `opt_x_long` overwrites manual edits; `saveSettings()` drops `multipost.api_key`; karaoke captions stay frozen during silent pauses and drop cues past 800; no clickable transcript or lightbox navigation; Activity Console hides `/api/jobs` history; modals ignore `Escape`.

---

## Iterative Milestones

### [x] Milestone 1: Roadmap & Backend Core Reliability
- [x] Document audit findings & roadmap in `docs/IMPROVEMENT_ROADMAP.md`
- [x] Fix `/api/media` Range parsing (suffix ranges, 416 responses, empty files)
- [x] Upgrade `/api/bundle/upload` (async chunk streaming, slot merging via `existing_*`, candidate scoring, unsupported-file handling & empty-dir cleanup)
- [x] Add `POST /api/ai/test` endpoint for verifying OpenAI-compatible LLM connectivity
- [x] Probe real video dimensions, FPS, duration, aspect ratio & 140s X Standard warning via OpenCV in `app/asset_scanner.py`
- [x] Add `mtime`/`size`-aware cover cache hit-skipping & `< 4.5 MB` JPEG cap in `prepare_cover_variants`
- [x] Expand `app/srt_parser.py` cue/sentence limits (`2500` cues, `300` sentences)
- [x] Make offline copywriter fallback adapt meaningfully to `user_hint` tones
- [x] Harden `app/chrome_bridge.py` (`_CDP_SYNC_LOCK`, platform validation, safe expiry parsing, Windows cURL unescaping) & auto-detect Chrome/Edge in `open_chrome.bat`
- [x] Wrap `DouYinVideo.upload` in `try...finally` tab/browser cleanup
- [x] Add `tests/test_core_backend.py` & verify all tests pass

### [x] Milestone 2: Publisher Orchestrator — Real Preflight, Cancellation & Concurrency Safety
- [x] Align `x_use_proxy` default (`False`) & pass explicit `cdp_url` into `upload_x_video` (no `os.environ` mutation)
- [x] Implement `validate_platform_preflight(...)` checking cookies, schedule syntax/future time, title lengths, Shipinhao `short_title` (6–16), Weibo required cover, and X length/duration/size limits
- [x] Upgrade `dry_run` execution to report real `success` / `warning` / `failed` states and `issues` list
- [x] Implement `cancel_job(job_id)`, `POST /api/jobs/{job_id}/cancel`, `POST /api/preflight`, and `auto_sync` guard on `GET /api/accounts`
- [x] Evict stale completed jobs from `ACTIVE_JOBS` (>25) and expose progress summary in `PublishJob.to_dict()`
- [x] Update `publish_cli.py` for preflight issue reporting and clean `KeyboardInterrupt` job cancellation
- [x] Add `tests/test_publisher_preflight.py` & verify all tests pass

### [x] Milestone 3: Frontend Upload Progress, 3-Slot Asset Manager & Interactive Karaoke/Poster Experience
- [x] Add non-blocking Toast Notification stack (`#toastStack`)
- [x] Replace upload `fetch` with `XMLHttpRequest` progress + stage stepper + Cancel Upload button
- [x] Fix `dragDepth` dragover/dragleave flicker & add full-window drag-and-drop overlay
- [x] Add 3-Slot Bundle Asset Strip (Video / Poster Card / Karaoke SRT) with per-slot Replace/Add actions
- [x] Clear karaoke caption during silent playback gaps, support single-quoted tags/`<i>`, make Teaser Hook seek to `00:00`, and add collapsible clickable Synced Transcript panel
- [x] Upgrade Poster Lightbox with active ratio label and Prev/Next + Arrow-key navigation
- [x] Verify JS syntax & `pytest` suite

### [x] Milestone 4: Composer Interactions, Connect Modal Fix, Settings & Publish Command Dock
- [x] Fix Connect Modal Tab 2 with 4-platform pill selector + Deep Verify button
- [x] Add per-card Copy button, live Job Status badge, non-destructive `btnGenerateCopy` loading state, and dual short/long X draft memory (`state.xDrafts`)
- [x] Preserve `multipost.api_key` in Settings, add `default_watch_dir` + CN/EN brand signatures + **Test AI Connection** button
- [x] Replace unsafe silent demo auto-publish with Pre-Publish Confirmation Modal backed by `/api/preflight`
- [x] Upgrade Publish Dock with top progress bar, dynamic status indicator, **Cancel Job**, **Retry Failed**, `datetime-local` schedule picker + validation, and tabbed Activity Drawer (**Live Log** + **Recent Jobs** history)
- [x] Add `Escape` modal dismissal, theme icon toggle (`moon`/`sun`), responsive navbar wrapping, and `:focus-visible` styles
- [x] Verify JS syntax & `pytest` suite

### [/] Milestone 5: End-to-End Verification Suite, Documentation & Final Polish
- [ ] Add `tests/test_ui_and_api_e2e.py` verifying DOM ID integrity (`static/app.js` vs `static/index.html`) and isolated end-to-end API flows
- [ ] Update `README.md` with new studio features & test command
- [ ] Finalize `docs/IMPROVEMENT_ROADMAP.md` with Phase 2 Next-Step evolution plan
- [ ] Run full `python -m pytest -v`, verify clean `git status`, commit & push

---

## Phase 2: Next-Step Roadmap (v2.2+)

Once Milestones 1–5 are shipped, the recommended next evolution steps for OnePost are:

1. **Batch / Multi-Episode Queue**: Queue multiple highlight folders sequentially with shared prompt templates and staggered schedule offsets (`+2h`, `+1d`).
2. **Real-Time SSE Log Streaming**: Replace 750ms polling with `EventSource` (`/api/jobs/{id}/stream`) for zero-latency subprocess output and instant progress bars.
3. **Visual Cover Safe-Zone & Subtitle Burn Check**: Preview platform feed crop overlays (Douyin 3:4 UI safe area, Shipinhao feed card crop, Weibo 16:9 player) directly in the Poster Lightbox.
4. **Multi-Account Workspace Switcher**: Switch between multiple creator personas (`creator`, `brand_en`, `tech_cn`) from the top ribbon instead of editing `settings.json`.
5. **Automated Cookie Health Watcher**: Background periodic check with desktop/UI toast alerts before a session expires mid-batch.
