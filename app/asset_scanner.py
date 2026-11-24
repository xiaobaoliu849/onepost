"""Smart Asset Bundle Scanner & Cover Aspect-Ratio Generator.

Automatically discovers:
1. Main Video (.mp4, .mov, .mkv, .webm) — preferring final/highlight/karaoke outputs
2. Poster Card / Cover Image (.png, .jpg, .jpeg, .webp) — generating 3:4 portrait & 4:3 landscape covers
3. Karaoke Subtitle File (.srt, .ass, .vtt)
"""
from __future__ import annotations

import hashlib
import math
from pathlib import Path
from typing import Any

from PIL import Image, ImageFilter

from app.config import COVERS_CACHE_DIR
from app.srt_parser import parse_subtitle_file

VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".webm", ".m4v"}
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp"}
SUBTITLE_EXTS = {".srt", ".ass", ".ssa", ".vtt"}

MAX_WEIBO_COVER_BYTES = 4_500_000


def _format_duration_label(sec: float) -> str:
    if sec <= 0:
        return "Ready to publish"
    total = int(round(sec))
    h = total // 3600
    m = (total % 3600) // 60
    s = total % 60
    if h > 0:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def _infer_aspect_label(width: int, height: int, fallback: str) -> str:
    if width <= 0 or height <= 0:
        return fallback
    ratio = width / height
    known = [
        (16 / 9, "16:9"),
        (9 / 16, "9:16"),
        (4 / 3, "4:3"),
        (3 / 4, "3:4"),
        (1.0, "1:1"),
    ]
    for target, label in known:
        if abs(ratio - target) < 0.06:
            return label
    g = math.gcd(width, height)
    rw, rh = width // g, height // g
    if rw <= 32 and rh <= 32:
        return f"{rw}:{rh}"
    return f"{width}x{height}"


def probe_video_metadata(video_path: Path) -> dict[str, Any]:
    """Read ready-to-publish video file metadata (dimensions, FPS, duration) without running FFmpeg."""
    size_bytes = video_path.stat().st_size
    size_mb = round(size_bytes / (1024 * 1024), 2)
    width = 0
    height = 0
    fps = 0.0
    duration_sec = 0.0

    try:
        import cv2  # type: ignore

        cap = cv2.VideoCapture(str(video_path))
        try:
            if cap.isOpened():
                width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
                height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
                fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
                frames = float(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0.0)
                if fps > 0 and frames > 0:
                    duration_sec = round(frames / fps, 2)
        finally:
            cap.release()
    except Exception:
        pass

    ext_label = video_path.suffix.upper().lstrip(".")
    aspect_label = _infer_aspect_label(width, height, ext_label)
    duration_label = _format_duration_label(duration_sec)
    x_std_ok = size_mb <= 512.0 and (duration_sec <= 140.0 or duration_sec == 0.0)

    info: dict[str, Any] = {
        "file_name": video_path.name,
        "file_path": str(video_path.resolve()),
        "size_bytes": size_bytes,
        "size_mb": size_mb,
        "duration_sec": duration_sec,
        "duration_label": duration_label,
        "width": width,
        "height": height,
        "fps": round(fps, 2) if fps > 0 else 0.0,
        "aspect_label": aspect_label,
        "x_standard_compatible": x_std_ok,
        "x_premium_compatible": size_mb <= 16384.0,
        "warnings": [],
    }
    if size_mb > 512.0:
        info["warnings"].append(
            f"Video is {size_mb} MB: X Standard accounts cap at 512 MB (X Premium supports up to 16 GB)."
        )
    if duration_sec > 140.0:
        info["warnings"].append(
            f"Video is {duration_label} ({int(round(duration_sec))}s): X Standard caps at 2:20 (140s); enable X Premium for 3–5m+ videos."
        )
    return info


def prepare_cover_variants(
    poster_path: Path | None, video_path: Path | None = None
) -> dict[str, str | None]:
    """Prepare 3:4 portrait, 4:3 landscape & 16:9 Weibo cover variants (< 4.5MB JPG) from your Poster Card
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

    resolved = source_image.resolve()
    stat = resolved.stat()
    cache_key = f"{resolved}|{stat.st_mtime_ns}|{stat.st_size}"
    digest = hashlib.md5(cache_key.encode("utf-8")).hexdigest()[:12]

    portrait_out = COVERS_CACHE_DIR / f"{digest}_portrait_3x4.jpg"
    landscape_out = COVERS_CACHE_DIR / f"{digest}_landscape_4x3.jpg"
    weibo_out = COVERS_CACHE_DIR / f"{digest}_weibo_16x9.jpg"

    # Skip re-rendering if cached covers for this exact file version already exist
    if all(p.is_file() and p.stat().st_size > 0 for p in (portrait_out, landscape_out, weibo_out)):
        return {
            "original": str(resolved),
            "portrait_3x4": str(portrait_out.resolve()),
            "landscape_4x3": str(landscape_out.resolve()),
            "weibo_cover": str(weibo_out.resolve()),
        }

    try:
        with Image.open(resolved) as img:
            rgb = img.convert("RGB")
            _render_smart_canvas(rgb, (1080, 1440), portrait_out)
            _render_smart_canvas(rgb, (1440, 1080), landscape_out)
            _render_smart_canvas(rgb, (1920, 1080), weibo_out)
    except Exception:
        return {
            "original": str(resolved),
            "portrait_3x4": str(resolved),
            "landscape_4x3": str(resolved),
            "weibo_cover": str(resolved),
        }

    return {
        "original": str(resolved),
        "portrait_3x4": str(portrait_out.resolve()),
        "landscape_4x3": str(landscape_out.resolve()),
        "weibo_cover": str(weibo_out.resolve()),
    }


def _save_capped_jpeg(img: Image.Image, out_path: Path) -> None:
    """Save JPEG keeping file size under 4.5 MB (Weibo requires < 5 MB)."""
    for q in (92, 84, 75, 65):
        img.save(out_path, format="JPEG", quality=q, optimize=True)
        if out_path.stat().st_size <= MAX_WEIBO_COVER_BYTES:
            return


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
        _save_capped_jpeg(fitted, out_path)
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
    _save_capped_jpeg(bg, out_path)


def score_video_candidate(p: Path) -> tuple[int, int]:
    name = p.stem.lower()
    score = 0
    for kw in ("final", "highlight", "karaoke", "sub", "out", "publish", "cut", "teaser", "short"):
        if kw in name:
            score += 10
    for bad in ("raw", "temp", "tmp", "chunk", "source_only"):
        if bad in name:
            score -= 15
    return (score, p.stat().st_size)


def score_poster_candidate(p: Path) -> int:
    name = p.stem.lower()
    score = 0
    for kw in ("poster", "cover", "card", "thumb", "thumbnail", "封面", "海报"):
        if kw in name:
            score += 20
    return score


def score_subtitle_candidate(p: Path) -> int:
    name = p.stem.lower()
    score = 0
    for kw in ("karaoke", "en", "bilingual", "highlight", "final", "sub"):
        if kw in name:
            score += 15
    if p.suffix.lower() in (".srt", ".ass"):
        score += 5
    return score


_score_video_candidate = score_video_candidate
_score_poster_candidate = score_poster_candidate
_score_subtitle_candidate = score_subtitle_candidate


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
