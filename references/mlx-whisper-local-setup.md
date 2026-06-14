# MLX Whisper local setup notes

Session-derived setup notes for stable local transcription on the user's Apple Silicon Mac.

## Stable install

Use `uv tool install` when the user wants a persistent command instead of one-off `uvx` execution:

```bash
uv tool install mlx-whisper
which mlx_whisper
readlink ~/.local/bin/mlx_whisper
uv tool list
mlx_whisper --help
```

Observed installed state:

```text
mlx_whisper_path=$HOME/.local/bin/mlx_whisper
mlx_whisper_link=$HOME/.local/share/uv/tools/mlx-whisper/bin/mlx_whisper
uv_tool_dir=$HOME/.local/share/uv/tools
mlx-whisper v0.4.3
```

## uvx cache cleanup

`uvx --from mlx-whisper mlx_whisper ...` creates a temporary cached environment under `~/.cache/uv/archive-v0/<id>/`. It is safe to keep (faster future uvx runs) or delete (recover disk space) once a stable `uv tool install` exists.

Observed cleanup example:

```bash
rm -rf ~/.cache/uv/archive-v0/Q2nB-dn5ETsvbLpBRKTiY ~/.cache/uv/simple-v21/pypi/mlx-whisper.rkyv
mlx_whisper --help
```

The removed archive was about `872M`; global `mlx_whisper` still worked afterwards.

## Recommended command template

```bash
mlx_whisper input.m4a \
  --model mlx-community/whisper-large-v3-mlx \
  --language zh \
  --task transcribe \
  --output-dir ./out \
  --output-name transcript \
  --output-format all \
  --verbose True \
  --initial-prompt '这是一集中文科技播客，主题是 AI Agent、Language Agent、OpenAI、ChatGPT。请保留中英文术语。'
```

Use `mlx-community/whisper-large-v3-turbo` only when the user explicitly prioritizes speed over accuracy.

## Bundled post-processing helper

Use the skill helper for deterministic repeated tasks instead of retyping one-off Python:

```bash
SKILL_DIR="${CODEX_HOME:-$HOME/.codex}/skills/audio-transcription"
python3 "$SKILL_DIR/scripts/transcription_postprocess.py" --help
python3 "$SKILL_DIR/scripts/transcription_postprocess.py" verify-audio episode.m4a --report audio_verification.json
python3 "$SKILL_DIR/scripts/transcription_postprocess.py" inspect-transcript transcript_whisper_large_v3_mlx.json --report hallucination_report.json
python3 "$SKILL_DIR/scripts/transcription_postprocess.py" emit-deliverables transcript_clean.json --metadata info_manual.json --out-dir .
python3 "$SKILL_DIR/scripts/transcription_postprocess.py" verify-package --out transcript_package.zip --report package_report.json
uvx --from argostranslate python "$SKILL_DIR/scripts/translate_timestamped_transcript.py" transcript_timestamped.txt --out transcript_timestamped_zh.txt --require-timestamp
```

## External documentation pattern

When the user asks to publish setup notes to an external document system, create a Markdown source first, then use the platform-specific upload tool only if it is available in the runtime. If authentication fails, ask the user to reauthenticate or provide a shareable destination.
