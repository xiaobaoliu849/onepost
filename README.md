# OnePost

**One click, post everywhere.** Publish a finished video, its poster card, and its karaoke subtitles to **Douyin (抖音)**, **WeChat Channels / Shipinhao (视频号)**, **Weibo (微博)**, and **X** from one local web studio.

OnePost only handles **publishing**. It does not cut, transcode, or translate video. You bring the finished files and OnePost:

- picks up the video (`.mp4/.mov/.mkv`), poster card (`.png/.jpg/.webp`), and karaoke subtitle (`.srt/.ass/.vtt`) from a folder
- frames the poster card into `3:4`, `4:3`, and `16:9` covers, using a blurred backdrop so no title text gets cropped
- reads the subtitles (including the 00:00 teaser hook) and writes a title, description, and hashtags for each platform's rules
- publishes through **your real Chrome login**, so no headless QR codes get flagged by risk control

## Quick start (Windows)

```powershell
git clone https://github.com/xiaobaoliu849/onepost.git
cd onepost
pip install -r requirements.txt
start.bat            # opens http://127.0.0.1:8765
```

### 1. Log in once, in real Chrome
Click **Open Real Chrome & Sync** in the top bar (or double-click `open_chrome.bat`). This opens your installed Google Chrome with a dedicated, persistent profile (`data/studio_chrome_profile`) and remote debugging on port `9333`. Log in to Douyin / Shipinhao / Weibo / X there **normally**, then click **Sync Chrome Login Now**.

While that Chrome window stays open, OnePost publishes **inside it** (via CDP). It opens a new tab, uploads, and closes only that tab.

Another way to connect: paste cookies from any logged-in Chrome (Cookie-Editor JSON, or DevTools → Network → *Copy as cURL*) in the **Paste Cookies** tab.

### 2. Load a bundle and publish
Paste your output folder path (or drag the files in), review the four platform cards, click **Preflight Check**, then **Publish Selected**.

### CLI
```powershell
python publish_cli.py --status
python publish_cli.py --folder "D:\douyin_video\ep01" --dry-run
python publish_cli.py --folder "D:\douyin_video\ep01" --prompt "focus on the teaser hook" --platforms douyin,shipinhao,weibo,x
```

## Network
- **Douyin / Shipinhao / Weibo**: proxy environment variables are stripped, so domestic accounts always publish from your direct CN IP.
- **X**: uses system / TUN routing by default. If you use a port proxy instead, set it in **Settings** (for example `http://127.0.0.1:7890`).

## AI copywriting (optional)
In **Settings**, add any OpenAI-compatible endpoint (DeepSeek, Qwen, OpenAI, Ollama). With no key, OnePost writes drafts from the subtitle text using a built-in template.

## Privacy
Everything under `data/` (settings, API keys, Chrome profile) and all platform cookies are **gitignored** and stay on your machine.

## Layout
```
app/
  main.py            FastAPI server + REST API
  publisher.py       multi-platform job orchestrator (split-network routing)
  chrome_bridge.py   real Chrome on CDP :9333, login sync, cookie import
  x_uploader.py      X publisher (browser session or official API v2)
  ai_copywriter.py   per-platform copy from the karaoke SRT
  asset_scanner.py   bundle discovery + poster cover framing
  srt_parser.py      SRT / ASS / VTT + karaoke + teaser hook parsing
  qr_login.py        headless QR login (fallback)
  multipost_bridge.py optional MultiPost-Extension bridge
static/              web studio UI
vendor/social-auto-upload/   Douyin / Shipinhao / Weibo engine (MIT, patched)
publish_cli.py       one-command CLI
```

## Credits
- [dreammis/social-auto-upload](https://github.com/dreammis/social-auto-upload) (MIT). Vendored and patched to use local Chrome and to attach to a live Chrome session over CDP.
- [leaperone/MultiPost-Extension](https://github.com/leaperone/MultiPost-Extension) (optional bridge)

## License
MIT
