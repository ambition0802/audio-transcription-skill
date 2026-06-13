# Xiaoyuzhou + MLX Whisper session note

Use this reference for 小宇宙/Xiaoyuzhou podcast transcription requests, especially on Apple Silicon Macs.

## Successful extraction pattern

For a page such as:

```text
https://www.xiaoyuzhoufm.com/episode/<EPISODE_ID>
```

DOM inspection found the playable audio URL:

```js
Array.from(document.querySelectorAll('audio,source')).map(e => ({
  tag: e.tagName,
  src: e.src,
  html: e.outerHTML.slice(0, 300)
}))
```

Result shape:

```json
[{"tag":"AUDIO","src":"https://media.xyzcdn.net/.../....m4a"}]
```

`web_extract` may fail against Xiaoyuzhou; a browser snapshot/console can still access the rendered page and media element.

## Download + verify

```bash
mkdir -p ~/Downloads/xiaoyuzhou_transcripts/<episode_id>
cd ~/Downloads/xiaoyuzhou_transcripts/<episode_id>
SKILL_DIR="${CODEX_HOME:-$HOME/.codex}/skills/audio-transcription"
curl -L --fail --retry 3 -o episode.m4a '<media.xyzcdn.net m4a URL>'
python3 "$SKILL_DIR/scripts/transcription_postprocess.py" \
  verify-audio episode.m4a \
  --report audio_verification.json
```

Long podcast episodes can be multiple hours; keep the transcription running in the background and poll until completion.

## MLX Whisper command

```bash
uvx --from mlx-whisper mlx_whisper episode.m4a \
  --model mlx-community/whisper-large-v3-mlx \
  --language zh \
  --task transcribe \
  --output-dir . \
  --output-name transcript_whisper_large_v3_mlx \
  --output-format all \
  --verbose True \
  --initial-prompt '这是一集中文科技播客，主题是 AI Agent、Language Agent、OpenAI、ChatGPT、Semantic Parsing、世界模型、GUI、CLI。主持人 <HOST_NAME>，嘉宾 <GUEST_NAME>。请保留中英文术语。'
```

Use `mlx-community/whisper-large-v3-turbo` only when the user explicitly prioritizes speed over accuracy.

For a long episode, run in the background and poll/wait until exit. The first `uvx` run may download packages such as `mlx-metal`, `torch`, `numba`, etc.

## Cleanup pattern

Inspect the generated JSON for repetition/hallucination issues with the bundled helper:

```bash
python3 "$SKILL_DIR/scripts/transcription_postprocess.py" \
  inspect-transcript transcript_whisper_large_v3_mlx.json \
  --duration <SECONDS> \
  --report hallucination_report.json
```

In the captured case, Whisper repeated `我们的公众号是一档由语言及世界` during outro silence/music. Remove only the obvious repeated hallucination loop after the genuine closing sentence, then regenerate clean txt/srt/json/markdown deliverables.

Generate deliverables from the cleaned JSON:

```bash
python3 "$SKILL_DIR/scripts/transcription_postprocess.py" \
  emit-deliverables transcript_clean.json \
  --metadata info_manual.json \
  --out-dir .

python3 "$SKILL_DIR/scripts/transcription_postprocess.py" \
  verify-package \
  --out transcript_package.zip \
  --report package_report.json
```

## Deliverables used

- `transcript.md` — title, source, model note, caveat, show-note time axis, timestamped transcript.
- `transcript_timestamped.txt` — `[HH:MM:SS] text` lines.
- `transcript_clean.srt` — cleaned subtitles.
- `transcript_clean.json` — cleaned segment JSON.
- `transcript_package.zip` — zip for sharing in Feishu.

Final response should include exact local paths and attach useful files via `MEDIA:/absolute/path` when in Feishu.
