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


def _detect_hint_mode(user_hint: str) -> str:
    low = (user_hint or "").lower()
    if any(k in low for k in ("反直觉", "悬念", "hook", "viral", "高能", "讨论")):
        return "viral_hook"
    if any(k in low for k in ("干货", "认知", "insight", "deep", "收藏", "提炼")):
        return "deep_insight"
    if any(k in low for k in ("卡拉ok", "karaoke", "口语", "双语", "bilingual", "听力")):
        return "bilingual_karaoke"
    if any(k in low for k in ("极简", "克制", "minimal", "crisp", "密度")):
        return "crisp_minimal"
    return "custom" if user_hint.strip() else "default"


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

    mode = _detect_hint_mode(user_hint)
    hint_clean = re.sub(r"\s+", " ", user_hint).strip()
    cn_title_base = (hint_clean if mode == "custom" and len(hint_clean) <= 22 else clean_topic)[:16]
    bullet_block = "\n".join(f"• {h}" for h in highlights[:3]) if highlights else ""

    if mode == "viral_hook":
        douyin_title = f"反常识真相：{cn_title_base}"[:28]
        douyin_desc = f"前15秒直接抛结论！为什么大多数人都做反了？{duration_cn}原声高能对谈，你怎么看？评论区聊聊。"
        douyin_tags = ["认知颠覆", "高能片段", "深度访谈", "思维碰撞", "中英双语"]
        sp_short = f"反直觉 {cn_title_base}"
        sp_title = f"开头15秒悬念：{cn_title_base}"[:28]
        sp_desc = f"【{duration_cn}高能悬念版】把最反直觉的观点放在最前面，转发给同样关注这个话题的朋友。"
        wb_title = f"前15秒高能反转｜{cn_title_base}"[:28]
        wb_lead = f"#高能反转# #深度观点# 【{cn_title_base}｜开头即高潮】前15秒信息量拉满，看完有被震撼到："
        x_lead = f"Wait for the opening twist: {hook_en}"
        x_tail = f"The first 15s of this {duration_en} cut flip the usual narrative. Thoughts? #Insight #Highlights"
    elif mode == "deep_insight":
        douyin_title = f"建议收藏：{cn_title_base}核心干货"[:28]
        douyin_desc = f"{duration_cn}纯干货浓缩！没有废话，建议点赞收藏，反复拆解里面的底层逻辑与关键洞察。"
        douyin_tags = ["认知提升", "干货分享", "底层逻辑", "深度精选", "思维模型"]
        sp_short = f"干货 {cn_title_base}"
        sp_title = f"{cn_title_base}｜{duration_cn}认知干货"[:28]
        sp_desc = f"{cn_title_base}（{duration_cn}核心精炼）：拆解最值得反复回看的三大关键结论，推荐收藏慢看。"
        wb_title = f"{cn_title_base}｜核心认知干货笔记"[:28]
        wb_lead = f"#认知提升# #干货笔记# 【{cn_title_base}｜{duration_cn}重点提炼】值得做笔记的三段高密度输出："
        x_lead = f"Core takeaways from {clean_topic}: {hook_en}"
        x_tail = f"Distilled into {duration_en} of pure signal—bookmark for later. #DeepDive #Takeaways"
    elif mode == "bilingual_karaoke":
        douyin_title = f"逐词跟读：{cn_title_base}原声精听"[:28]
        douyin_desc = f"全程逐词高亮卡拉OK英文字幕！跟着{duration_cn}原声磨耳朵，地道表达和语调一次拿捏。"
        douyin_tags = ["英语口语", "卡拉OK字幕", "中英双语", "英语精听", "地道表达"]
        sp_short = f"双语精听 {cn_title_base}"
        sp_title = f"双语卡拉OK精听：{cn_title_base}"[:28]
        sp_desc = f"{cn_title_base}｜搭配逐词高亮英文字幕，{duration_cn}沉浸式练听力、学地道表达。"
        wb_title = f"{cn_title_base}｜双语逐词精听"[:28]
        wb_lead = f"#英语卡拉OK字幕# #地道口语# 【{cn_title_base}｜{duration_cn}原声精听】边听边跟读的高光片段："
        x_lead = f"Synced word-by-word karaoke captions: {hook_en}"
        x_tail = f"{duration_en} highlight cut with live highlighted captions for effortless listening. #Bilingual #Captions"
    elif mode == "crisp_minimal":
        douyin_title = f"{cn_title_base}｜{duration_cn}高光"[:28]
        douyin_desc = f"{duration_cn}直入主题。只留最核心的一段原声与观点。"
        douyin_tags = ["极简精选", "原声高光", "双语字幕", "重点速览"]
        sp_short = f"速览 {cn_title_base}"
        sp_title = f"{cn_title_base}（{duration_cn}精剪）"[:28]
        sp_desc = f"{cn_title_base}｜{duration_cn}直击重点，配同步英文字幕。"
        wb_title = f"{cn_title_base}｜{duration_cn}速览"[:28]
        wb_lead = f"#精选速览# #双语字幕# 【{cn_title_base}】{duration_cn}核心看点："
        x_lead = hook_en
        x_tail = f"({duration_en} cut) #Highlights"
    else:
        angle_note = f"｜聚焦：{hint_clean[:18]}" if hint_clean else ""
        douyin_title = f"高能精剪：{cn_title_base}｜双语字幕"[:28]
        douyin_desc = f"开头高能预警！{duration_cn}浓缩全片精华{angle_note}，配逐词高亮英文字幕，建议收藏反复看。"
        douyin_tags = ["中英双语", "英语口语", "深度精选", "卡拉OK字幕", "认知提升"]
        sp_short = f"精剪 {cn_title_base}"
        sp_title = douyin_title
        sp_desc = f"{cn_title_base}（{duration_cn}精华版）{angle_note}｜片头前置全片最精彩观点，搭配动态高亮英文字幕，欢迎转发收藏。"
        wb_title = f"{cn_title_base}｜双语高光精剪"[:28]
        wb_lead = f"#双语精剪# #英语卡拉OK字幕# 【{cn_title_base}｜{duration_cn}核心高光】{angle_note}\n片头已提取最抓人的高能悬念，搭配逐词高亮卡拉OK英文字幕："
        x_lead = f"🔥 {hook_en}"
        x_tail = (
            f"{hint_clean[:70]} — {duration_en} cut with live karaoke captions. 👇 #Highlights #Bilingual"
            if hint_clean
            else f"Cut down to the sharpest {duration_en} highlights with live word-by-word karaoke captions. 👇 #Highlights #Bilingual"
        )

    shipinhao_short = re.sub(r"[^\w\u4e00-\u9fff ]", "", sp_short).strip()[:15]
    if len(shipinhao_short) < 6:
        shipinhao_short = (shipinhao_short + " 深度精华版")[:15]

    weibo_desc = f"{wb_lead}\n{bullet_block}".strip()

    x_hook = x_lead if len(x_lead) <= 165 else x_lead[:162] + "..."
    x_text = f"{x_hook}\n\n{x_tail}".strip()
    if len(x_text) > 275:
        x_text = x_text[:272] + "..."

    payload = {
        "douyin": {
            "title": douyin_title,
            "description": douyin_desc,
            "tags": douyin_tags,
        },
        "shipinhao": {
            "short_title": shipinhao_short,
            "title": sp_title,
            "description": sp_desc,
            "tags": ["双语字幕", "深度访谈", "英语学习", "认知升级"],
        },
        "weibo": {
            "title": wb_title,
            "description": weibo_desc,
            "tags": ["双语精剪", "英语学习", "海外视野"],
        },
        "x": {
            "text": x_text,
            "long_note": (
                f"{x_hook}\n\n"
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
