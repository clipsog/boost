# Video Booster

Mobile-friendly web UI to boost TikTok videos via [Zefame API v2](https://zefame.com/api/v2) — with an approval queue for **@the.clips.og**.

## Deploy online (Render)

1. Push this repo to GitHub
2. Create a **Web Service** on [Render](https://render.com) connected to the repo
3. Set **Environment variable** `ZEFAME_API_KEY`
4. Deploy — open the URL on your phone

Or use the included `render.yaml` blueprint.

```bash
# Production start (local test)
gunicorn app:app --bind 0.0.0.0:5050 --workers 1 --threads 4 --timeout 120
```

## Setup (local)

```bash
cd ~/Projects/zefame-client
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # if needed; edit ZEFAME_API_KEY
```

## Web UI

### Approval queue

Automatically loads videos from **@the.clips.og** posted in the last **24 hours** that have **not** been full-boosted yet. Posts inside that window are never auto-hidden by age/calendar rules—only real boost history or high public stats (infer) mark them done.

- Videos younger than **1 hour** appear as *Waiting*
- After 1 hour, they move to *Ready for approval*
- Nothing is boosted until you click **Approve boost** (full pack)
- **Boost all ready** runs full packs one video at a time for every ready item

### Manual boost

Choose a boost pack:

- **Full pack:** views tiered by current video likes — 0 → 480 · 1–7 → 541 · 8–19 → 650 · ≥20 → 750; always sends 10–14 likes (1086)
- **Lower boost:** 300 views (953) + 10 likes (1086)
- **Views only:** custom views (953, min 100)
- **Likes only:** custom likes (1086, min 10)

```bash
source .venv/bin/activate
pip install -r requirements.txt
python app.py
```

Open http://127.0.0.1:5050, paste the video URL, and click **Send boost**.

Override defaults in `.env`:

```
ZEFAME_VIEWS_SERVICE=953
ZEFAME_VIEWS_QUANTITY=542
ZEFAME_LOW_VIEWS_QUANTITY=300
ZEFAME_LIKES_SERVICE=1086
ZEFAME_LIKES_QUANTITY=10
ZEFAME_FULL_LIKES_MIN=10
ZEFAME_FULL_LIKES_MAX=14
ZEFAME_FULL_VIEWS_LOW=480
ZEFAME_FULL_VIEWS_MID=541
ZEFAME_FULL_VIEWS_HIGH=650
ZEFAME_FULL_VIEWS_ULTRA=750
ZEFAME_VIEWS_ONLY_QUANTITY=100
ZEFAME_LIKES_ONLY_QUANTITY=12
ZEFAME_LIKES_MIN=10
ZEFAME_QUEUE_PROFILE=the.clips.og
ZEFAME_QUEUE_LOOKBACK_HOURS=24
ZEFAME_QUEUE_MIN_AGE_HOURS=1
ZEFAME_ASSUMED_BOOST_HOURS=8
ZEFAME_ASSUMED_BOOST_ET_CUTOFF_HOUR=17
```

Posts **≥8 hours** old or from a **previous Eastern calendar day** are auto-marked as already boosted. **After 5:00 PM ET**, same-day posts from earlier that day are marked too; before 5 PM, today’s posts stay in **Ready** (once ≥1h old) until you boost them.

### Boost history on Render

Production starts with an empty history unless you ship one. This repo includes `data/boost_history_seed.json` (copied from your local boosts). On first boot, if no runtime file exists yet, that seed is loaded automatically.

To sync newer local history after deploy:

```bash
export BOOST_ADMIN_SECRET=your-secret   # same value as on Render
python scripts/push_history.py --url https://your-app.onrender.com
```

Set `BOOST_ADMIN_SECRET` on Render (see `render.yaml`) so the import endpoint accepts updates.

## CLI

```bash
python cli.py balance
python cli.py services
python cli.py order <service_id> <url> <quantity>
python cli.py status <order_id>
```

## Python

```python
from zefame_client import ZefameClient

api = ZefameClient()
print(api.balance())
print(api.services())
order = api.add_order(service=1, link="https://example.com/post", quantity=100)
print(api.order_status(order["order"]))
```

## Kokoro TTS (text-to-speech)

Local [Kokoro-82M](https://huggingface.co/hexgrad/Kokoro-82M) UI — type text, pick a voice, preview word highlights, export MP3/SRT/MP4.

**Prerequisites:** `espeak-ng` and `ffmpeg`.

```bash
brew install espeak-ng ffmpeg   # macOS
source .venv/bin/activate
pip install -r requirements-tts.txt
python tts_app.py
```

Open http://127.0.0.1:5051

- 54 voices across 8 languages
- Speed control (0.5×–2×)
- **Live karaoke preview** — active word highlights in sync with audio
- Download **MP3**, word-timed **SRT**, or finished **MP4** (captions burned in)
- First run downloads the model (~350 MB) from Hugging Face

### Word-level captions (Method 2)

Kokoro attaches `start_ts` / `end_ts` to each token during synthesis. The app uses those exact timings (no Whisper re-transcription):

1. **Live preview** — 4 words on screen, active word in yellow
2. **SRT export** — one entry per word for CapCut / Premiere / Resolve
3. **MP4 export** — 1080×1920 video with the same highlight style baked in

Minimal Python example:

```python
from kokoro_engine import synthesize_with_timings, to_srt

result = synthesize_with_timings(
    "Power is not about shouting. It is about silence.",
    voice="am_michael",
    speed=0.85,
)
for word in result.words:
    print(word.start, word.end, word.text)

open("captions.srt", "w").write(to_srt(result.words))
```

### Caption video from the CLI

Generate a finished MP4 locally (saved to your project folder):

```bash
python generate_caption_video.py \
  "Power is not about shouting. It is about silence." \
  -o output.mp4 \
  --voice am_michael \
  --speed 0.85 \
  --srt captions.srt

# Optional: loop a background clip under the captions
python generate_caption_video.py -f script.txt -o output.mp4 --background dark_loop.mp4
```
