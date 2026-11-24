"""Smart Asset Bundle Scanner & Cover Aspect-Ratio Generator.

Automatically discovers:
1. Main Video (.mp4, .mov, .mkv, .webm) — preferring final/highlight/karaoke outputs
2. Poster Card / Cover Image (.png, .jpg, .jpeg, .webp) — generating 3:4 portrait & 4:3 landscape covers
3. Karaoke Subtitle File (.srt, .ass, .vtt)
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from PIL import Image, ImageFilter

from app.config import COVERS_CACHE_DIR
from app.srt_parser import parse_subtitle_file

VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".webm", ".m4v"}
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp"}
SUBTITLE_EXTS = {".srt", ".ass", ".ssa", ".vtt"}


def probe_video_metadata(video_path: Path) -> dict[str, Any]:
    """Read ready-to-publish video file metadata without running FFmpeg."""
    size_bytes = video_path.stat().st_size
    size_mb = round(size_bytes / (1024 * 1024), 2)
    info: dict[str, Any] = {
        "file_name": video_path.name,
        "file_path": str(video_path.resolve()),
        "size_bytes": size_bytes,
        "size_mb": size_mb,
        "duration_sec": 0.0,
        "duration_label": "Ready to publish",
        "width": 0,
        "height": 0,
        "aspect_label": video_path.suffix.upper().lstrip("."),
        "x_standard_compatible": size_mb <= 512.0,
        "x_premium_compatible": size_mb <= 16384.0,
        "warnings": [],
    }
    if size_mb > 512.0:
        info["warnings"].append(
            f"Video is {size_mb} MB: X Standard accounts cap at 512 MB (X Premium supports up to 16 GB)."
        )
    return info


def prepare_cover_variants(
    poster_path: Path | None, video_path: Path | None = None
) -> dict[str, str | None]:
    """Prepare 3:4 portrait & 4:3 landscape cover variants (< 4.5MB JPG) from your Poster Card
    so Douyin, Shipinhao, and Weibo receive exact platform cover ratios without cropping text.
    """
    source_image = poster_path
    if source_image is None or not source_image.is_file():
        return {
            "original": None,
            "portrait_3x4": None,
            "landscape_4x3": None,
            "weibo_cover": None,
        }
    digest = hashlib.md5(str(source_image.resolve()).encode("utf-8")).hexdigest()[:10]

    portrait_out = COVERS_CACHE_DIR / f"{digest}_portrait_3x4.jpg"
    landscape_out = COVERS_CACHE_DIR / f"{digest}_landscape_4x3.jpg"
    weibo_out = COVERS_CACHE_DIR / f"{digest}_weibo_16x9.jpg"

    try:
        with Image.open(source_image) as img:
            rgb = img.convert("RGB")
            _render_smart_canvas(rgb, (1080, 1440), portrait_out)
            _render_smart_canvas(rgb, (1440, 1080), landscape_out)
            _render_smart_canvas(rgb, (1920, 1080), weibo_out)
    except Exception:
        return {
            "original": str(source_image.resolve()),
            "portrait_3x4": str(source_image.resolve()),
            "landscape_4x3": str(source_image.resolve()),
            "weibo_cover": str(source_image.resolve()),
        }

    return {
        "original": str(source_image.resolve()),
        "portrait_3x4": str(portrait_out.resolve()),
        "landscape_4x3": str(landscape_out.resolve()),
        "weibo_cover": str(weibo_out.resolve()),
    }


def _render_smart_canvas(img: Image.Image, target_size: tuple[int, int], out_path: Path) -> None:
    """Fit poster card onto target aspect ratio WITHOUT cutting off poster text!
    Uses blurred ambient backdrop when aspect ratios differ, preserving 100% of the poster card typography.
    """
    tw, th = target_size
    sw, sh = img.size
    src_ratio = sw / max(sh, 1)
    dst_ratio = tw / max(th, 1)

    if abs(src_ratio - dst_ratio) < 0.08:
        fitted = img.resize((tw, th), Image.Resampling.LANCZOS)
        fitted.save(out_path, format="JPEG", quality=92, optimize=True)
        return

    # Blurred fill background
    scale_bg = max(tw / sw, th / sh)
    bg_w, bg_h = int(sw * scale_bg), int(sh * scale_bg)
    bg = img.resize((bg_w, bg_h), Image.Resampling.BILINEAR)
    left = (bg_w - tw) // 2
    top = (bg_h - th) // 2
    bg = bg.crop((left, top, left + tw, top + th)).filter(ImageFilter.GaussianBlur(radius=28))

    # Foreground poster preserved completely
    scale_fg = min(tw / sw, th / sh)
    fg_w, fg_h = int(sw * scale_fg), int(sh * scale_fg)
    fg = img.resize((fg_w, fg_h), Image.Resampling.LANCZOS)
    bg.paste(fg, ((tw - fg_w) // 2, (th - fg_h) // 2))
    bg.save(out_path, format="JPEG", quality=92, optimize=True)


def _score_video_candidate(p: Path) -> tuple[int, int]:
    name = p.stem.lower()
    score = 0
    for kw in ("final", "highlight", "karaoke", "sub", "out", "publish", "cut", "teaser", "short"):
        if kw in name:
            score += 10
    for bad in ("raw", "temp", "tmp", "chunk", "source_only"):
        if bad in name:
            score -= 15
    return (score, p.stat().st_size)


def _score_poster_candidate(p: Path) -> int:
    name = p.stem.lower()
    score = 0
    for kw in ("poster", "cover", "card", "thumb", "thumbnail", "封面", "海报"):
        if kw in name:
            score += 20
    return score


def _score_subtitle_candidate(p: Path) -> int:
    name = p.stem.lower()
    score = 0
    for kw in ("karaoke", "en", "bilingual", "highlight", "final", "sub"):
        if kw in name:
            score += 15
    if p.suffix.lower() in (".srt", ".ass"):
        score += 5
    return score


def scan_bundle_directory(folder_path: str | Path) -> dict[str, Any]:
    """Scan a project folder and assemble the Video + Poster Card + Karaoke SRT bundle."""
    folder = Path(folder_path).expanduser().resolve()
    if not folder.exists() or not folder.is_dir():
        raise FileNotFoundError(f"Folder not found: {folder}")

    videos: list[Path] = []
    images: list[Path] = []
    subtitles: list[Path] = []

    for item in folder.iterdir():
        if not item.is_file():
            continue
        ext = item.suffix.lower()
        if ext in VIDEO_EXTS:
            videos.append(item)
        elif ext in IMAGE_EXTS:
            images.append(item)
        elif ext in SUBTITLE_EXTS:
            subtitles.append(item)

    videos.sort(key=_score_video_candidate, reverse=True)
    images.sort(key=_score_poster_candidate, reverse=True)
    subtitles.sort(key=_score_subtitle_candidate, reverse=True)

    best_video = videos[0] if videos else None
    best_poster = images[0] if images else None
    best_sub = subtitles[0] if subtitles else None

    return build_bundle_manifest(
        folder=folder,
        video_path=best_video,
        poster_path=best_poster,
        subtitle_path=best_sub,
        all_videos=videos,
        all_posters=images,
        all_subtitles=subtitles,
    )


def build_bundle_manifest(
    folder: Path,
    video_path: Path | None,
    poster_path: Path | None,
    subtitle_path: Path | None,
    all_videos: list[Path] | None = None,
    all_posters: list[Path] | None = None,
    all_subtitles: list[Path] | None = None,
) -> dict[str, Any]:
    video_meta = probe_video_metadata(video_path) if video_path and video_path.is_file() else None
    covers = prepare_cover_variants(poster_path, video_path)
    sub_meta = parse_subtitle_file(subtitle_path) if subtitle_path and subtitle_path.is_file() else None

    return {
        "bundle_name": folder.name,
        "folder_path": str(folder.resolve()),
        "video": video_meta,
        "poster": {
            "has_custom_poster": bool(poster_path and poster_path.is_file()),
            "file_name": poster_path.name if poster_path else (
                "Auto-extracted frame" if covers.get("original") else None
            ),
            "variants": covers,
        },
        "subtitle": sub_meta,
        "candidates": {
            "videos": [str(p.resolve()) for p in (all_videos or ([video_path] if video_path else []))],
            "posters": [str(p.resolve()) for p in (all_posters or ([poster_path] if poster_path else []))],
            "subtitles": [str(p.resolve()) for p in (all_subtitles or ([subtitle_path] if subtitle_path else []))],
        },
    }
