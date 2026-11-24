"""AI Multi-Platform Copywriting Engine driven by Karaoke SRT + Teaser Hook."""
from __future__ import annotations

import json
import re
from typing import Any

import httpx


SYSTEM_PROMPT = """You are an elite bilingual short-video & highlight-reel publishing strategist for Chinese & Global social media.
Given a 3-5 minute highlight video's Karaoke SRT transcript (including the Teaser Hook at 00:00), generate viral, platform-native metadata for 4 platforms in strict JSON format.

Platform Constraints & Best Practices:
1. "douyin" (抖音):
   - "title": Magnetic Chinese hook title, MUST be <= 28 characters. Highlight the conflict/insight from the teaser hook.
   - "description": Engaging 2-3 line Chinese caption encouraging comments & saves (do not repeat hashtags inside description if listed in tags).
   - "tags": Array of 4-5 trending Chinese topic words WITHOUT '#' prefix (e.g. ["英语听力", "深度访谈", "中英双语", "认知提升"]).
2. "shipinhao" (微信视频号):
   - "short_title": REQUIRED concise Chinese short title, strictly 6-16 characters (no emojis, only space/punctuation allowed by WeChat Channels).
   - "title": Warm, curiosity-driven Chinese headline <= 28 characters.
   - "description": Thoughtful Chinese summary suitable for WeChat ecosystem sharing & forward-to-Moments.
   - "tags": Array of 3-5 Chinese topic tags WITHOUT '#'.
3. "weibo" (新浪微博):
   - "title": Video headline strictly <= 28 characters.
   - "description": Rich Chinese commentary post with 2-3 timestamped highlight takeaways and 2 double-hashtag topics like #双语精选# #海外视野# embedded naturally.
   - "tags": Array of 3-4 Chinese tags WITHOUT '#'.
4. "x" (X / Twitter):
   - "text": High-signal English post hook + 2 crisp bullet takeaways + 1-2 relevant hashtags. Keep total length <= 270 characters so it works on BOTH Standard and X Premium accounts!
   - "long_note": Optional extended English thread/commentary (300-600 chars) if X Premium long-post is enabled.

Return ONLY valid JSON with keys: "douyin", "shipinhao", "weibo", "x".
"""


async def generate_platform_copy(
    bundle: dict[str, Any],
    settings: dict[str, Any],
    user_hint: str = "",
) -> dict[str, Any]:
    """Generate tailored metadata for Douyin, Shipinhao, Weibo, and X.
    Uses configured OpenAI-compatible LLM if an API key is provided; otherwise falls back to
    an intelligent local SRT-hook synthesis engine so the workflow ALWAYS works out-of-the-box!
    """
    ai_cfg = settings.get("ai", {})
    api_key = (ai_cfg.get("api_key") or "").strip()
    base_url = (ai_cfg.get("base_url") or "https://api.deepseek.com/v1").rstrip("/")
    model = (ai_cfg.get("model") or "deepseek-chat").strip()

    sub = bundle.get("subtitle") or {}
    video = bundle.get("video") or {}
    bundle_name = bundle.get("bundle_name") or (video.get("file_name") or "Highlight Video")
    teaser = sub.get("teaser_hook_text") or ""
    transcript = sub.get("transcript_excerpt") or ""

    # Infer readable duration from SRT timeline if video duration wasn't probed
    raw_dur = video.get("duration_sec") or 0.0
    if not raw_dur and sub.get("sentences"):
        raw_dur = float(sub["sentences"][-1].get("end_sec") or 0.0)
    if raw_dur >= 45:
        mins = max(1, round(raw_dur / 60))
        duration_en = f"{mins}-min"
        duration_cn = f"{mins}分钟"
    else:
        duration_en = "3-5 min"
        duration_cn = "3-5分钟"

    user_prompt = (
        f"Project / Video Name: {bundle_name}\n"
        f"Duration: {duration_en}\n"
        f"Teaser Hook (00:00 - 00:25): {teaser or 'N/A'}\n"
        f"Creator Hint / Custom Angle: {user_hint or 'Highlight English karaoke subtitles + teaser hook insight'}\n\n"
        f"Karaoke Subtitle Transcript Excerpt:\n{transcript[:4500] or '(No subtitle file attached - infer from title & hint)'}\n"
    )

    if api_key:
        try:
            payload = await _call_llm_json(base_url, api_key, model, user_prompt)
            normalized = _normalize_copy_payload(payload, ai_cfg)
            normalized["source"] = f"ai:{model}"
            normalized["raw_prompt"] = user_prompt
            return normalized
        except Exception as exc:
            fallback = _heuristic_copy_from_srt(bundle_name, teaser, sub, duration_cn, duration_en, user_hint, ai_cfg)
            fallback["source"] = "smart_template_fallback"
            fallback["ai_warning"] = f"LLM API call failed ({exc}); generated smart draft from SRT instead."
            fallback["raw_prompt"] = user_prompt
            return fallback

    draft = _heuristic_copy_from_srt(bundle_name, teaser, sub, duration_cn, duration_en, user_hint, ai_cfg)
    draft["source"] = "smart_srt_draft"
    draft["raw_prompt"] = user_prompt
    return draft


async def _call_llm_json(base_url: str, api_key: str, model: str, user_prompt: str) -> dict[str, Any]:
    url = f"{base_url}/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    body = {
        "model": model,
        "temperature": 0.65,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
    }
    # Use trust_env=False only if needed? No, allow normal env or direct call
    async with httpx.AsyncClient(timeout=45.0) as client:
        resp = await client.post(url, headers=headers, json=body)
        resp.raise_for_status()
        data = resp.json()
    content = data["choices"][0]["message"]["content"]
    return _extract_json_object(content)


def _extract_json_object(text: str) -> dict[str, Any]:
    cleaned = text.strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start != -1 and end != -1 and end > start:
        return json.loads(cleaned[start : end + 1])
    return json.loads(cleaned)


def _heuristic_copy_from_srt(
    bundle_name: str,
    teaser: str,
    sub: dict[str, Any],
    duration_cn: str,
    duration_en: str,
    user_hint: str,
    ai_cfg: dict[str, Any],
) -> dict[str, Any]:
    """Build clean, platform-compliant bilingual drafts directly from the Karaoke SRT & Teaser Hook."""
    clean_topic = re.sub(r"[_\-]+", " ", PathSafeStem(bundle_name)).strip()
    if len(clean_topic) < 3:
        clean_topic = "精选深度精华片段"

    sentences = sub.get("sentences") or []
    hook_en = teaser[:130].strip() if teaser else (user_hint or clean_topic)
    highlights = []
    if len(sentences) >= 3:
        step = max(1, len(sentences) // 3)
        for idx in (0, min(step, len(sentences) - 1), min(step * 2, len(sentences) - 1)):
            s = sentences[idx]
            highlights.append(f"[{s['time_label']}] {s['text'][:68]}")

    cn_title_base = (user_hint.strip() or clean_topic)[:16]
    douyin_title = f"高能精剪：{cn_title_base}｜双语字幕"[:28]
    shipinhao_short = re.sub(r"[^\w\u4e00-\u9fff ]", "", f"精剪 {cn_title_base}").strip()[:15]
    if len(shipinhao_short) < 6:
        shipinhao_short = (shipinhao_short + " 深度精华版")[:15]

    bullet_block = "\n".join(f"• {h}" for h in highlights[:3]) if highlights else ""
    weibo_desc = (
        f"#双语精剪# #英语卡拉OK字幕# 【{cn_title_base}｜{duration_cn}核心高光】\n"
        f"片头已提取最抓人的高能悬念，搭配逐词高亮卡拉OK英文字幕，沉浸式听懂原声精髓！\n"
        f"{bullet_block}".strip()
    )

    x_hook = hook_en if len(hook_en) <= 170 else hook_en[:167] + "..."
    x_text = (
        f"🔥 {x_hook}\n\n"
        f"Cut down to the sharpest {duration_en} highlights with live word-by-word karaoke captions. 👇 #Highlights #Bilingual"
    )
    if len(x_text) > 275:
        x_text = x_text[:272] + "..."

    payload = {
        "douyin": {
            "title": douyin_title,
            "description": f"开头高能预警！{duration_cn}浓缩全片精华，配逐词高亮英文字幕，建议收藏反复看。",
            "tags": ["中英双语", "英语口语", "深度精选", "卡拉OK字幕", "认知提升"],
        },
        "shipinhao": {
            "short_title": shipinhao_short,
            "title": douyin_title,
            "description": f"{cn_title_base}（{duration_cn}精华版）｜片头前置全片最精彩观点，搭配动态高亮英文字幕，欢迎转发收藏。",
            "tags": ["双语字幕", "深度访谈", "英语学习", "认知升级"],
        },
        "weibo": {
            "title": f"{cn_title_base}｜双语高光精剪"[:28],
            "description": weibo_desc,
            "tags": ["双语精剪", "英语学习", "海外视野"],
        },
        "x": {
            "text": x_text,
            "long_note": (
                f"🔥 {hook_en}\n\n"
                f"Key moments in this {duration_en} highlight cut:\n"
                f"{bullet_block}\n\n"
                "Synced with karaoke-highlighted subtitles for effortless watching."
            ).strip(),
        },
    }
    return _normalize_copy_payload(payload, ai_cfg)


def PathSafeStem(name: str) -> str:
    return re.sub(r"\.[a-zA-Z0-9]{2,4}$", "", name)


def _normalize_copy_payload(raw: dict[str, Any], ai_cfg: dict[str, Any]) -> dict[str, Any]:
    sig_cn = (ai_cfg.get("brand_signature_cn") or "").strip()
    sig_en = (ai_cfg.get("brand_signature_en") or "").strip()

    dy = raw.get("douyin") or {}
    sp = raw.get("shipinhao") or {}
    wb = raw.get("weibo") or {}
    xp = raw.get("x") or {}

    def clean_tags(tags: Any) -> list[str]:
        if isinstance(tags, str):
            tags = [t.strip() for t in re.split(r"[,，#]+", tags) if t.strip()]
        if not isinstance(tags, list):
            return []
        return [str(t).strip().lstrip("#") for t in tags if str(t).strip().lstrip("#")][:6]

    short_t = str(sp.get("short_title") or sp.get("title") or "深度双语精华解读").strip()
    short_t = re.sub(r"[^\w\u4e00-\u9fff ]", "", short_t).strip()
    if len(short_t) < 6:
        short_t = (short_t + " 精选高光版")[:16]
    elif len(short_t) > 16:
        short_t = short_t[:16]

    x_text = str(xp.get("text") or "").strip()
    if sig_en and len(x_text) + len(sig_en) + 2 <= 278:
        x_text = f"{x_text}\n{sig_en}"

    wb_desc = str(wb.get("description") or "").strip()
    if sig_cn:
        wb_desc = f"{wb_desc}\n{sig_cn}".strip()

    return {
        "douyin": {
            "title": str(dy.get("title") or "")[:30],
            "description": (str(dy.get("description") or "") + (f"\n{sig_cn}" if sig_cn else "")).strip(),
            "tags": clean_tags(dy.get("tags")),
        },
        "shipinhao": {
            "short_title": short_t,
            "title": str(sp.get("title") or dy.get("title") or "")[:30],
            "description": (str(sp.get("description") or "") + (f"\n{sig_cn}" if sig_cn else "")).strip(),
            "tags": clean_tags(sp.get("tags")),
        },
        "weibo": {
            "title": str(wb.get("title") or dy.get("title") or "")[:30],
            "description": wb_desc,
            "tags": clean_tags(wb.get("tags")),
        },
        "x": {
            "text": x_text,
            "long_note": str(xp.get("long_note") or x_text).strip(),
        },
    }
