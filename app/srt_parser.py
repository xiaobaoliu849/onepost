"""Karaoke SRT / ASS / VTT subtitle parser and teaser-hook analyzer."""
from __future__ import annotations

import html
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


@dataclass
class SubtitleCue:
    index: int
    start_sec: float
    end_sec: float
    start_label: str
    end_label: str
    raw_text: str
    clean_text: str
    has_karaoke_tags: bool = False


def _parse_timestamp_to_sec(ts: str) -> float:
    ts = ts.strip().replace(",", ".")
    parts = ts.split(":")
    if len(parts) == 3:
        h, m, s = parts
        return int(h) * 3600 + int(m) * 60 + float(s)
    if len(parts) == 2:
        m, s = parts
        return int(m) * 60 + float(s)
    return float(parts[0])


def _format_sec(sec: float) -> str:
    sec = max(0.0, sec)
    m = int(sec // 60)
    s = int(sec % 60)
    h = m // 60
    m = m % 60
    if h > 0:
        return f"{h:02d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


_ASS_TAG_RE = re.compile(r"\{[^}]*\}")
_HTML_TAG_RE = re.compile(r"<[^>]+>")
_KARAOKE_HINT_RE = re.compile(
    r"(\\[kK][fo]?[0-9]+|<font\s+color=|<c\.|<span|<u\b|<b\b|\{\\1c&)",
    re.IGNORECASE,
)


def _strip_markup(text: str) -> tuple[str, bool]:
    has_karaoke = bool(_KARAOKE_HINT_RE.search(text))
    cleaned = text.replace("\\N", "\n").replace("\\n", "\n")
    cleaned = _ASS_TAG_RE.sub("", cleaned)
    cleaned = _HTML_TAG_RE.sub("", cleaned)
    cleaned = html.unescape(cleaned)
    lines = [re.sub(r"\s+", " ", ln).strip() for ln in cleaned.splitlines()]
    lines = [ln for ln in lines if ln]
    # Deduplicate adjacent identical lines often produced by word-by-word karaoke Roll-up SRTs
    deduped: list[str] = []
    for ln in lines:
        if not deduped or deduped[-1] != ln:
            deduped.append(ln)
    return " / ".join(deduped), has_karaoke


def parse_subtitle_file(path: Path) -> dict[str, Any]:
    """Parse .srt, .vtt, or .ass subtitle files (including word-highlighted Karaoke SRTs)."""
    raw = path.read_text(encoding="utf-8-sig", errors="replace")
    suffix = path.suffix.lower()

    cues: list[SubtitleCue] = []
    if suffix in (".ass", ".ssa"):
        cues = _parse_ass(raw)
    else:
        cues = _parse_srt_or_vtt(raw)

    # Collapse rapid karaoke word-step duplicates into readable sentences for AI & human review
    consolidated_lines = _consolidate_karaoke_cues(cues)
    teaser_lines = [
        item["text"] for item in consolidated_lines if item["start_sec"] <= 25.0
    ][:6]

    total_karaoke_cues = sum(1 for c in cues if c.has_karaoke_tags)
    is_karaoke = total_karaoke_cues > 0 or (len(cues) > len(consolidated_lines) * 1.6 and len(cues) > 15)

    full_transcript = "\n".join(
        f"[{item['time_label']}] {item['text']}" for item in consolidated_lines
    )

    return {
        "file_name": path.name,
        "file_path": str(path.resolve()),
        "format": suffix.lstrip("."),
        "is_karaoke": is_karaoke,
        "cue_count": len(cues),
        "sentence_count": len(consolidated_lines),
        "teaser_hook_text": " ".join(teaser_lines).strip(),
        "sentences": consolidated_lines[:120],
        "cues": [asdict(c) for c in cues[:800]],
        "transcript_excerpt": full_transcript[:6000],
    }


def _parse_srt_or_vtt(raw: str) -> list[SubtitleCue]:
    blocks = re.split(r"\r?\n\r?\n+", raw.strip())
    cues: list[SubtitleCue] = []
    time_re = re.compile(
        r"(\d{1,2}:\d{2}:\d{2}[,.]\d{1,3}|\d{1,2}:\d{2}[,.]\d{1,3})\s*-->\s*(\d{1,2}:\d{2}:\d{2}[,.]\d{1,3}|\d{1,2}:\d{2}[,.]\d{1,3})"
    )
    idx = 1
    for block in blocks:
        lines = [ln.strip("\r") for ln in block.splitlines() if ln.strip()]
        if not lines or lines[0].upper().startswith("WEBVTT"):
            continue
        time_line_idx = -1
        match = None
        for i, ln in enumerate(lines[:3]):
            m = time_re.search(ln)
            if m:
                time_line_idx = i
                match = m
                break
        if match is None:
            continue
        start_sec = _parse_timestamp_to_sec(match.group(1))
        end_sec = _parse_timestamp_to_sec(match.group(2))
        payload = "\n".join(lines[time_line_idx + 1 :]).strip()
        if not payload:
            continue
        clean_text, has_k = _strip_markup(payload)
        if not clean_text:
            continue
        cues.append(
            SubtitleCue(
                index=idx,
                start_sec=round(start_sec, 3),
                end_sec=round(end_sec, 3),
                start_label=_format_sec(start_sec),
                end_label=_format_sec(end_sec),
                raw_text=payload,
                clean_text=clean_text,
                has_karaoke_tags=has_k,
            )
        )
        idx += 1
    return cues


def _parse_ass(raw: str) -> list[SubtitleCue]:
    cues: list[SubtitleCue] = []
    idx = 1
    for line in raw.splitlines():
        if not line.startswith("Dialogue:"):
            continue
        parts = line[len("Dialogue:") :].split(",", 9)
        if len(parts) < 10:
            continue
        start_sec = _parse_timestamp_to_sec(parts[1])
        end_sec = _parse_timestamp_to_sec(parts[2])
        payload = parts[9].strip()
        clean_text, has_k = _strip_markup(payload)
        if not clean_text:
            continue
        cues.append(
            SubtitleCue(
                index=idx,
                start_sec=round(start_sec, 3),
                end_sec=round(end_sec, 3),
                start_label=_format_sec(start_sec),
                end_label=_format_sec(end_sec),
                raw_text=payload,
                clean_text=clean_text,
                has_karaoke_tags=has_k,
            )
        )
        idx += 1
    return cues


def _consolidate_karaoke_cues(cues: list[SubtitleCue]) -> list[dict[str, Any]]:
    """Karaoke SRTs often emit one cue per highlighted word with identical clean_text.
    Consolidate consecutive identical clean_text cues into single readable timeline items.
    """
    consolidated: list[dict[str, Any]] = []
    for cue in cues:
        norm = re.sub(r"\s+", " ", cue.clean_text).strip()
        if not norm:
            continue
        if consolidated and consolidated[-1]["text"] == norm:
            consolidated[-1]["end_sec"] = cue.end_sec
            continue
        consolidated.append(
            {
                "start_sec": cue.start_sec,
                "end_sec": cue.end_sec,
                "time_label": cue.start_label,
                "text": norm,
            }
        )
    return consolidated
