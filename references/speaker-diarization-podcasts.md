# Speaker diarization for podcast transcripts

Use when a user asks to mark which host/guest said each sentence after a Whisper transcript exists.

## Recommended path

1. Keep Whisper/MLX output with segment timestamps (`transcript_clean.json`).
2. Run a real diarization model, preferably pyannote:
   - Model: `pyannote/speaker-diarization-3.1`
   - Params for two-person interview: `min_speakers=2`, `max_speakers=2`
   - Requirement: user must accept the gated model terms on Hugging Face and provide `HF_TOKEN`.
3. Merge diarization turns with Whisper segments by maximum time overlap or midpoint containment.
4. Map anonymous speaker clusters to names using anchors:
   - Host: intro, question prompts, known opening lines.
   - Guest: self-introduction / first long answer.
5. Export `transcript_speaker_labeled*.md/.txt/.srt/.json` and clearly state it is automatic, not manually proofread.

Use the bundled script for the pyannote run, merge, anchor mapping, and quality diagnostics:

```bash
SKILL_DIR="${CODEX_HOME:-$HOME/.codex}/skills/audio-transcription"
ffmpeg -y -i episode.m4a -ac 1 -ar 16000 episode_16k.wav

uv run --offline \
  --with 'pyannote.audio==3.3.2' \
  --with 'torch==2.2.2' \
  --with 'torchaudio==2.2.2' \
  --with 'numpy<2' \
  --with 'huggingface_hub<0.25' \
  python "$SKILL_DIR/scripts/diarize_pyannote_merge.py" \
    --audio episode_16k.wav \
    --transcript transcript_clean.json \
    --host-name '<HOST_NAME>' \
    --guest-name '<GUEST_NAME>' \
    --host-anchor '<START,END>' \
    --guest-anchor '<START,END>' \
    --min-speakers 2 \
    --max-speakers 2 \
    --report diarization_quality_report.json
```

Inspect `diarization_quality_report.json` before delivery. If warnings show anchor collapse or one raw speaker dominating the interview, retry with a larger `--max-speakers` or produce a clearly labeled `text_inferred` version instead of claiming audio-verified speaker IDs.

## Pitfalls observed

- Whisper itself does not identify speakers. Do not imply the initial transcript can know speakers without diarization or text-based inference.
- `pyannote/speaker-diarization-3.1` fails without HF authentication and accepted terms (`GatedRepoError 401`). Ask for/set `HF_TOKEN` before running. The pipeline may also fail on `pyannote/segmentation-3.0` (`403 Forbidden`) until the user accepts that dependency's terms separately.
- Version trap: with latest `pyannote.audio` 4.x, loading `speaker-diarization-3.1` may try to fetch gated `pyannote/speaker-diarization-community-1` PLDA assets. For the 3.1 pipeline, a reliable pinned environment was:
  ```bash
  uv run --offline \
    --with 'pyannote.audio==3.3.2' \
    --with 'torch==2.2.2' \
    --with 'torchaudio==2.2.2' \
    --with 'numpy<2' \
    --with 'huggingface_hub<0.25' \
    python diarize_pyannote_merge.py
  ```
  In this pinned stack, use `Pipeline.from_pretrained(..., use_auth_token=token)` rather than `token=token`.
- Background shells may not inherit the user's interactive `HF_TOKEN`. Check `test -n "$HF_TOKEN"`; if missing, the bundled script will also look for an `export HF_TOKEN=...` or `export HUGGINGFACE_TOKEN=...` line in common shell rc files without printing the token.
- Apple Silicon speed: move the pipeline to MPS (`pipeline.to(torch.device('mps'))`) when available. CPU diarization for a 138-minute episode ran >85 minutes without completing; MPS completed the same job in roughly several minutes.
- A naive fallback using `speechbrain/spkrec-ecapa-voxceleb` embeddings + agglomerative clustering may be misleading on edited podcasts: in one Xiaoyuzhou Chinese tech interview it assigned nearly every segment/window to one speaker, even with a seemingly decent silhouette score. Always inspect anchor ranges such as host question and guest self-intro before packaging.
- Pyannote can also fail silently on edited long-form interviews with sponsor reads, intro clips, or AI voice demos: `min_speakers=2,max_speakers=2` may cluster the whole main interview as one speaker and use the second speaker for ad/AI inserts. Verify raw speaker duration distribution and sample anchor windows before packaging. If Peter/Elon-style main speakers collapse, retry with `max_speakers=4`; if still collapsed, generate a `text_inferred` version using transcript context and label it clearly as text-inferred, not audio-verified diarization.
- Torchaudio may require `torchcodec` for loading compressed audio in newer environments. Convert with ffmpeg first or read a 16 kHz WAV via `soundfile`:
  ```bash
  ffmpeg -y -i episode.m4a -ac 1 -ar 16000 episode_16k.wav
  ```
- Edited intros may contain teaser clips from the guest before the actual interview; avoid using the first 1–2 minutes as the only voice anchor.
