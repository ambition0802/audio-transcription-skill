# Timestamped transcript translation

Use when a user asks to translate an existing `transcript_timestamped.txt` while preserving timestamps.

## Pattern from successful session

Input shape:

```text
[00:00:00.000 --> 00:00:04.400] My concern isn't the long run, it's the next three to seven years.
```

Output shape keeps the timestamp prefix exactly and translates only the text:

```text
[00:00:00.000 --> 00:00:04.400] 我关心的不是长远，而是接下来的三到七年。
```

## Argos Translate one-off workflow

Use the bundled script instead of retyping translation/cache logic. It preserves the timestamp prefix exactly, translates only the text body, installs the Argos English → Chinese package when missing, caches translated lines, writes partial output, checks line count, and emits a sample report:

```bash
SKILL_DIR="${CODEX_HOME:-$HOME/.codex}/skills/audio-transcription"
uvx --from argostranslate python \
  "$SKILL_DIR/scripts/translate_timestamped_transcript.py" \
  transcript_timestamped.txt \
  --out transcript_timestamped_zh.txt \
  --cache translation_cache_en_zh_argos.json \
  --report transcript_timestamped_zh.report.json \
  --require-timestamp
```

If the Argos model is already installed and network should not be used, add `--no-install`. For parser-only smoke tests, use `--engine identity`.

## Practical safeguards

- Inspect `transcript_timestamped_zh.report.json` before returning; it contains line count, cache stats, and beginning/middle/end samples.
- Add `--keep-term TERM` for domain names that should remain in English.
- Add `--replace OLD=NEW` for domain-specific post-processing terms not covered by the defaults.

## Caveats

Argos output is a machine translation and may be rough. Tell the user it is suitable as a first-pass Chinese version, not a polished human translation. For publication-quality output, do a second LLM/human editing pass on the translated file while preserving timestamps.
