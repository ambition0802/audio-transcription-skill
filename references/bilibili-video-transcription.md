# Bilibili video transcription recipe

Use this when the source is a Bilibili video URL and the deliverable is a no-timestamp transcript plus summary.

## Successful command pattern

```bash
mkdir -p ~/Downloads/bilibili_transcripts/<BV_ID>
cd ~/Downloads/bilibili_transcripts/<BV_ID>
SKILL_DIR="${CODEX_HOME:-$HOME/.codex}/skills/audio-transcription"

uvx --from yt-dlp yt-dlp --dump-json --no-playlist '<BILIBILI_URL>' > info.json
python3 "$SKILL_DIR/scripts/transcription_postprocess.py" \
  build-metadata \
  --from-json info.json \
  --out info_manual.json \
  --require title \
  --require duration \
  --require id \
  --fail-on-missing

uvx --from yt-dlp yt-dlp \
  -f 'bestaudio/best' \
  --extract-audio --audio-format m4a --audio-quality 0 \
  --no-playlist \
  -o 'source.%(ext)s' \
  '<BILIBILI_URL>'

python3 "$SKILL_DIR/scripts/transcription_postprocess.py" \
  verify-audio source.m4a \
  --report audio_verification.json

mlx_whisper source.m4a \
  --model mlx-community/whisper-large-v3-mlx \
  --language zh \
  --task transcribe \
  --output-dir . \
  --output-name transcript_whisper_large_v3_mlx \
  --output-format all \
  --verbose False \
  --initial-prompt '这是一个中文 B 站视频。请使用简体中文，保留必要英文术语。'
```

Use `mlx-community/whisper-large-v3-turbo` only when the user explicitly chooses speed over accuracy.

## Create deliverables from JSON

Prefer generating final deliverables from structured JSON segments rather than trusting line formatting in the raw txt. Use the bundled helper instead of retyping ad hoc Python:

```bash
python3 "$SKILL_DIR/scripts/transcription_postprocess.py" \
  emit-deliverables transcript_clean.json \
  --metadata info_manual.json \
  --out-dir . \
  --source-kind whisper
```

## Package and verify

```bash
python3 "$SKILL_DIR/scripts/transcription_postprocess.py" \
  verify-package \
  --out transcript_summary_package.zip \
  --report package_report.json
```

## Fallback: browser-captured `.m4s` when `yt-dlp` gets HTTP 412

Sometimes Bilibili rejects `yt-dlp --dump-json` with HTTP 412 while the video page itself still loads in a browser. In that case:

1. Open the video in the Browser plugin and inspect loaded media resources. Prefer `pageAssets.list()` when available because some browser automation contexts do not expose `performance.getEntriesByType` reliably. Save the full media inventory:
   ```js
   const pageAssetsCap = await tab.capabilities.get("pageAssets");
   const inv = await pageAssetsCap.list();
   const m4sAssets = inv.assets.filter(a => a.url.includes(".m4s"));
   // Save m4sAssets to media_urls.json in the working directory.
   ```
   A browser console/performance fallback is:
   ```js
   [...new Set(performance.getEntriesByType('resource')
     .map(e => e.name)
     .filter(u => u.includes('.m4s')))]
   ```
2. Select the audio-only resource with the helper. It prefers common Bilibili PC DASH audio IDs such as `*-30216.m4s` and avoids common video IDs such as `100022/100023`:
   ```bash
   python3 "$SKILL_DIR/scripts/transcription_postprocess.py" \
     select-bilibili-audio media_urls.json \
     --out audio_url.txt \
     --report audio_selection.json
   ```
3. Download the chosen URL with Bilibili headers:
   ```bash
   curl -L --fail --retry 3 \
     -H 'Referer: https://www.bilibili.com/video/<BV_ID>/' \
     -H 'User-Agent: Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36' \
     -o source.m4s "$(cat audio_url.txt)"
   ffmpeg -y -v error -i source.m4s -c copy source.m4a
   python3 "$SKILL_DIR/scripts/transcription_postprocess.py" \
     verify-audio source.m4a \
     --report audio_verification.json
   ```
   Add `--expected-duration <SECONDS> --fail-on-warning` when the expected full duration is known.
4. Create `info_manual.json` from page title/uploader/duration/BV/cid if metadata extraction failed, and note the fallback in metadata. Omit fields that are not available:
   ```bash
   python3 "$SKILL_DIR/scripts/transcription_postprocess.py" \
     build-metadata \
     --out info_manual.json \
     --title '<PAGE_TITLE>' \
     --uploader '<UPLOADER>' \
     --source-url '<BILIBILI_URL>' \
     --bv-id '<BV_ID>' \
     --fallback 'yt-dlp metadata failed; metadata patched from loaded Bilibili page'
   ```
   Add `--cid <CID>` and `--duration <SECONDS>` when those values are known.
5. Continue with `mlx_whisper` as usual.

## Optional Bilibili subtitle transcript source

When Bilibili exposes complete subtitles, prefer them as the primary transcript source unless the user explicitly asks for Whisper/local transcription. If coverage is incomplete or quality is questionable, use them only as a correction source for Whisper names, stock tickers, and damaged regions. Do not block transcription if the subtitle route fails.

1. In `pageAssets.list()`, look for `x/v2/subtitle/web/view`. Download the response with Bilibili `Referer` and desktop `User-Agent`.
2. The response may be `application/octet-stream` rather than JSON. Extract the embedded `//subtitle.bilibili.com/...auth_key=...` URL with the helper:
   ```bash
   python3 "$SKILL_DIR/scripts/transcription_postprocess.py" \
     extract-subtitle-url subtitle_view.bin \
     --out subtitle_body_url.txt \
     --report subtitle_extract.json \
     --allow-missing
   ```
3. If the subtitle body can be downloaded and decoded, verify that its first/last timestamps cover the video duration. If coverage is complete, generate the transcript deliverables from Bilibili subtitles and note the source in metadata.
4. If the user requested Whisper, or if subtitle coverage/quality is incomplete, compare the subtitles against Whisper output around uncertain terms and hallucination-prone regions.
5. If the subtitle URL is expired, TLS fails, or the body is not decodable, record the failure in metadata/notes and continue with Whisper output.

## Long-video Whisper hallucination mitigation

Inspect the first 3-5 minutes, a mid-video sample, and the final 1-2 minutes before packaging. If the transcript opening, middle, or tail collapses into repeated one-character/short-token loops such as `你。`, `!`, or repeated Chinese characters, rerun with previous-text conditioning disabled:

```bash
python3 "$SKILL_DIR/scripts/transcription_postprocess.py" \
  inspect-transcript transcript_whisper_large_v3_mlx.json \
  --duration <SECONDS> \
  --report hallucination_report.json
```

```bash
mlx_whisper source.m4a \
  --model mlx-community/whisper-large-v3-mlx \
  --language zh \
  --task transcribe \
  --output-dir . \
  --output-name transcript_noprev \
  --output-format all \
  --verbose False \
  --condition-on-previous-text False \
  --compression-ratio-threshold 2.4 \
  --logprob-threshold -1.0 \
  --no-speech-threshold 0.6 \
  --initial-prompt '这是一个中文 B 站视频。请使用简体中文，保留必要英文术语。'
```

Use the `transcript_noprev.json` output as the source for final cleaned `.md/.txt/.srt/.vtt/.json` deliverables when it fixes the loop. Still inspect first/last segment timestamps and the tail before delivery.

If only a bounded region remains bad, cut and retranscribe that slice:

```bash
ffmpeg -y -v error -ss <START_SECONDS> -t <DURATION_SECONDS> -i source.m4a -ar 16000 -ac 1 slice.wav
mlx_whisper slice.wav \
  --model mlx-community/whisper-large-v3-mlx \
  --language zh \
  --task transcribe \
  --output-dir . \
  --output-name slice_transcript \
  --output-format all \
  --verbose False \
  --condition-on-previous-text False \
  --compression-ratio-threshold 2.4 \
  --logprob-threshold -1.0 \
  --no-speech-threshold 0.6 \
  --initial-prompt '这是一个中文 B 站视频。请使用简体中文，保留必要英文术语。'
```

Merge the slice over the damaged region, offset timestamps by `START_SECONDS` when needed, then regenerate final `.json/.txt/.srt/.vtt` files.

```bash
python3 "$SKILL_DIR/scripts/transcription_postprocess.py" \
  merge-slice \
  --base transcript_noprev.json \
  --slice slice_transcript.json \
  --start <START_SECONDS> \
  --end <END_SECONDS> \
  --out transcript_clean.json

python3 "$SKILL_DIR/scripts/transcription_postprocess.py" \
  emit-deliverables transcript_clean.json \
  --metadata info_manual.json \
  --out-dir .
```

## Finance/stock-market videos

For Chinese finance or stock-market videos, use a richer prompt:

```text
这是一个中文财经/股票视频。请使用简体中文，保留 A股、美股、指数、公司代码、仓位、估值、流动性、风险偏好、机构、散户、政策、财报、AI、半导体、科技股、马斯克、特朗普等必要术语。
```

When summarizing, create `summary_arguments.md` if the user asks for arguments/evidence. Include one-sentence overview, core summary, an argument/evidence table, and uncertainty notes for tickers, names, and English terms that may be misrecognized.

## Pitfalls

- `yt-dlp` may warn that higher video qualities require Bilibili login/cookies; this is usually irrelevant for audio transcription if `bestaudio` downloads successfully.
- For long livestream-style videos, transcript openings, middles, or tails may contain repeated phrases or one-token loops. Inspect beginning/middle/tail samples; remove only obvious loops, not uncertain content.
- If `yt-dlp` metadata fails with HTTP 412 but browser playback succeeds, don't stop: capture the `.m4s` audio URL from browser resource entries and remux it.
- If summarizing a long transcript manually, read it in chunks before writing the summary; avoid summarizing only the beginning.
