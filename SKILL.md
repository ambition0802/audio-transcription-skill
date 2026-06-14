---
name: audio-transcription
description: "Transcribe podcasts/audio files with Whisper/MLX on Apple Silicon; extract audio URLs, generate txt/srt/vtt/json, and package results."
---

# Audio Transcription

## When to use

Use when the user asks to get a transcript/文字稿/逐字稿 from a podcast episode, audio URL, local audio/video file, or a web page containing playable audio. This includes 小宇宙/Xiaoyuzhou pages and cases where the user explicitly says Whisper is acceptable.

## Default approach

- Respond in the user's language with direct, actionable paths and commands.
- Set `SKILL_DIR="${CODEX_HOME:-$HOME/.codex}/skills/audio-transcription"` before running bundled scripts from another working directory.
- On Apple Silicon Macs, prefer `mlx-whisper` for local transcription. For one-off jobs use `uvx --from mlx-whisper mlx_whisper`; if the user asks for a stable local setup, install globally with `uv tool install mlx-whisper` and verify `$HOME/.local/bin/mlx_whisper`.
- For all transcription tasks, default to the higher-accuracy local model `mlx-community/whisper-large-v3-mlx`; use `mlx-community/whisper-large-v3-turbo` only when the user explicitly prioritizes speed over accuracy.
- Use a domain-specific `--initial-prompt` for Chinese/English mixed technical podcasts, finance videos, company names, stock tickers, and technical terms.
- Use `scripts/transcription_postprocess.py` for deterministic Bilibili URL selection, subtitle URL extraction, subtitle coverage inspection, audio verification, metadata building, transcript inspection, slice merging, deliverable generation, and package verification instead of retyping ad hoc Python snippets.
- Use `scripts/translate_timestamped_transcript.py` when translating timestamped transcripts while preserving timestamp prefixes.
- Save deliverables under a concrete directory such as `~/Downloads/<source>_transcripts/<episode_id>/`; return exact paths and attach/upload files only when the runtime supports attachments.

## Workflow

1. **Find the audio source**
   - For ordinary pages, inspect the DOM for `<audio>`/`<source>` elements or network media URLs.
   - For Bilibili URLs, prefer `uvx --from yt-dlp yt-dlp --dump-json --no-playlist '<URL>' > info.json`, then download audio with `yt-dlp -f 'bestaudio/best' --extract-audio --audio-format m4a`; see `references/bilibili-video-transcription.md`.
   - If Bilibili `yt-dlp` hits HTTP 412 but the page loads in a browser, use Browser `pageAssets.list()` or the browser performance/resource list to capture `.m4s` URLs. Save the inventory as `media_urls.json`, then run `scripts/transcription_postprocess.py select-bilibili-audio media_urls.json --out audio_url.txt --report audio_selection.json`. Download the selected URL with Bilibili `Referer` + desktop `User-Agent`, remux to `.m4a`, verify with `verify-audio`, and build `info_manual.json` with `build-metadata` from loaded-page title/uploader/duration/BV/cid rather than blocking on `yt-dlp` JSON.
   - For Bilibili pages, also try to find `x/v2/subtitle/web/view` in page resources. Download the response, then run `scripts/transcription_postprocess.py extract-subtitle-url subtitle_view.bin --out subtitle_body_url.txt --report subtitle_extract.json --allow-missing`. If a subtitle body can be downloaded, run `inspect-subtitle subtitle_body.json --duration <SECONDS> --report subtitle_coverage.json`; prefer complete subtitle coverage as the primary transcript source unless the user explicitly asks for Whisper/local transcription. Use incomplete subtitles only to correct Whisper names/tickers/uncertain regions.
   - In a browser console, this is often enough:
     ```js
     Array.from(document.querySelectorAll('audio,source')).map(e => ({tag: e.tagName, src: e.src}))
     ```
   - If a page has an RSS feed, use the enclosure URL when available.

2. **Download and verify audio**
   ```bash
   mkdir -p ~/Downloads/audio_transcripts/<episode_id>
   cd ~/Downloads/audio_transcripts/<episode_id>
   curl -L --fail --retry 3 -o episode.m4a '<MEDIA_URL>'
   SKILL_DIR="${CODEX_HOME:-$HOME/.codex}/skills/audio-transcription"
   python3 "$SKILL_DIR/scripts/transcription_postprocess.py" \
     verify-audio episode.m4a \
     --report audio_verification.json
   ```
   Add `--expected-duration <SECONDS>` when the expected full duration is known.

3. **Check tools / install `mlx_whisper`**
   ```bash
   which ffmpeg || brew install ffmpeg
   which uvx || python3 -m pip install --user uv
   # One-off, no global command:
   uvx --from mlx-whisper mlx_whisper --help
   # Stable global CLI:
   uv tool install mlx-whisper
   which mlx_whisper
   uv tool list
   ```
   Notes:
   - `uvx` caches a temporary environment under `~/.cache/uv/archive-v0/...`; it is not a stable path and may consume hundreds of MB. After `uv tool install mlx-whisper`, old uvx cache directories can be removed if disk space matters, then re-run `mlx_whisper --help` to verify the global install still works.
   - `uv tool install mlx-whisper` installs the executable at `~/.local/bin/mlx_whisper`, typically symlinked to `~/.local/share/uv/tools/mlx-whisper/bin/mlx_whisper`.

4. **Run transcription**
   ```bash
   uvx --from mlx-whisper mlx_whisper episode.m4a \
     --model mlx-community/whisper-large-v3-mlx \
     --language zh \
     --task transcribe \
     --output-dir . \
     --output-name transcript_whisper_large_v3_mlx \
     --output-format all \
     --verbose True \
     --initial-prompt '<domain terms, speaker names, mixed Chinese/English terminology>'
   ```

5. **Validate and clean**
   - Check expected duration vs. final segment time.
   - Run `scripts/transcription_postprocess.py inspect-transcript transcript_whisper_large_v3_mlx.json --duration <SECONDS> --report hallucination_report.json`, then inspect the first 3-5 minutes, a mid-video sample, and the last 1-2 minutes of JSON/SRT. Do not only check the tail.
   - Watch for repeated one-character/short-token loops, 30-second blocks filled with repeated text, nonsense high-compression text, and repeated outro hallucinations after music/silence.
   - If a whole run has repeated-token hallucinations, rerun with `--condition-on-previous-text False --compression-ratio-threshold 2.4 --logprob-threshold -1.0 --no-speech-threshold 0.6`.
   - If only one region remains bad, cut that region with `ffmpeg -ss ... -t ...`, transcribe the slice with the default model, then run `scripts/transcription_postprocess.py merge-slice --base transcript_noprev.json --slice slice_transcript.json --start <START> --end <END> --out transcript_clean.json` and regenerate final deliverables with `emit-deliverables`.
   - Remove only obvious repeated hallucination loops; do not silently rewrite uncertain content.
   - Report that machine transcription still needs human review for names, paper titles, company names, and English terms.

6. **Optional: speaker labels / diarization**
   - If the user asks “which person said which sentence?”, explain that Whisper does transcription, not reliable speaker identity; add a separate diarization step and merge it with Whisper timestamps.
   - For two-person podcasts, prefer `pyannote/speaker-diarization-3.1` with `min_speakers=2`, `max_speakers=2`, then map clusters using known anchor regions (e.g. host intro/question vs. guest self-introduction).
   - `pyannote/speaker-diarization-3.1` is a gated Hugging Face model: the user must accept the model terms and provide `HF_TOKEN`; its dependency `pyannote/segmentation-3.0` may require accepting terms separately. Do not claim a high-quality speaker-labeled transcript without those prerequisites.
   - On Apple Silicon, run pyannote on MPS when available; CPU diarization on a 138-minute episode can run for >85 minutes, while MPS completed in minutes in-session.
   - Avoid relying solely on naive SpeechBrain ECAPA + agglomerative clustering for heavily edited Chinese interview podcasts; in this session it collapsed almost all segments into one speaker despite a good silhouette score. Treat that as an experimental fallback only, and verify anchor regions before delivery.
   - Also verify pyannote cluster quality before delivery: for long interviews with sponsor reads / AI voice demos, `min_speakers=2,max_speakers=2` can assign the whole main interview to one speaker and use the second cluster for ads or short inserted voices. Inspect raw speaker counts/durations and anchor ranges. If the two main speakers collapse, retry with `max_speakers=4` or produce a clearly labeled `text_inferred` speaker version rather than claiming pyannote identified the speakers.
   - See `references/speaker-diarization-podcasts.md` for a compact recipe and pitfalls; `scripts/diarize_pyannote_merge.py` is a reusable merge and diagnostics script for `transcript_clean.json` + `episode_16k.wav`.

7. **Create deliverables**
   - Generate transcript deliverables with `scripts/transcription_postprocess.py emit-deliverables transcript_clean.json --metadata info_manual.json --out-dir .`. If the user asks for “不要时间戳 / no timestamps”, return `transcript_no_timestamps.md` and `transcript_no_timestamps.txt`; keep timestamps only in optional `.srt/.vtt/.json` support files.
   - If the user asks to translate an existing timestamped transcript, preserve the timestamp prefix exactly and translate only the text body line-by-line. Use `scripts/translate_timestamped_transcript.py` for Argos setup, caching, partial writes, line-count verification, sample reports, and common term handling. See `references/timestamped-transcript-translation.md`.
   - For Chinese finance/stock-market videos, use an `--initial-prompt` that includes terms such as A股、美股、指数、公司代码、仓位、估值、流动性、风险偏好、财报、AI、半导体、科技股、马斯克、特朗普 when relevant. In summaries, keep uncertain tickers/names in an uncertainty section instead of forcing corrections.
   - For a summary request, create `summary.md` with: one-sentence overview, themed bullet sections, actionable conclusions, and transcription-uncertainty notes for likely misrecognized proper nouns/technical terms.
   - If the user asks to list all arguments and evidence, create `summary_arguments.md` with: one-sentence overview, core summary, an argument/evidence table, and uncertainty notes.
   - `transcript.md`: title/source/model note + show-note chapters if available + timestamped transcript.
   - `transcript_timestamped.txt`: simple timestamped plain text.
   - `transcript_clean.srt`: subtitle format.
   - `transcript_clean.json`: structured segments.
   - `transcript_clean.vtt`: WebVTT support file.
   - `transcript_package.zip` or `transcript_summary_package.zip`: package the useful files.
   - If translation is requested: `transcript_timestamped_zh.txt` (or language-specific suffix), plus any cache file if useful for resuming but usually exclude cache from the final package unless requested.
   - If diarization is requested and verified: `transcript_speaker_labeled.md/.txt/.srt/.json`.

8. **Verify files before final response**
   ```bash
   SKILL_DIR="${CODEX_HOME:-$HOME/.codex}/skills/audio-transcription"
   python3 "$SKILL_DIR/scripts/transcription_postprocess.py" \
     verify-package \
     --out transcript_package.zip \
     --report package_report.json
   ```

## Pitfalls

- Web extraction tools may fail on Xiaoyuzhou or report internal-network blocking; use a browser page snapshot/console to inspect the loaded DOM instead.
- `mlx-whisper --output-format all` can produce a large single-line JSON; use Python `json.loads` rather than line-based inspection.
- Chinese tech podcasts and finance videos often mix English terms, company names, and tickers; a good `--initial-prompt` materially improves terminology.
- Whisper may repeat text in the opening, middle, or closing sentence during silence/music/noisy speech. Always inspect the beginning, a middle sample, and the last 1–2 minutes before packaging.
- Long episodes should run as a background process and be polled until completion rather than stopping with “I’ll wait”.
- If using pyannote for diarization on Apple Silicon, do not leave it on CPU for long podcasts. Pin a compatible `pyannote.audio` 3.x stack when needed, use `use_auth_token`, and move the pipeline to MPS before calling it; see `references/speaker-diarization-podcasts.md`.

## References

- `references/xiaoyuzhou-mlx-whisper.md` — concrete Xiaoyuzhou episode extraction and MLX Whisper command pattern from a successful session.
- `references/bilibili-video-transcription.md` — Bilibili URL → `yt-dlp` audio download → MLX Whisper → no-timestamp transcript + summary/package workflow.
- `references/mlx-whisper-local-setup.md` — stable `uv tool install` setup, `uvx` cache cleanup, and local command templates.
- `references/speaker-diarization-podcasts.md` — speaker-labeling workflow, pyannote HF token requirements, and pitfalls from a failed naive ECAPA clustering attempt.
