# Inline transcript corrections

Use this workflow after transcript inspection and before `emit-deliverables`. Do not hand-edit each output format.

## Manifest

Save reviewed corrections as `transcript_corrections.json`:

```json
{
  "version": 1,
  "corrections": [
    {
      "id": "term-001",
      "original": "韩武G",
      "replacement": "寒武纪",
      "status": "probable",
      "expected_matches": 1,
      "start": 120.0,
      "end": 180.0,
      "reason": "上下文在讨论国产 AI 芯片公司"
    }
  ]
}
```

- Use `status: "confirmed"` only when audio, subtitles, or a reliable source resolves the term. The output is `寒武纪〔校订；原转写：韩武G〕`.
- Use `status: "probable"` when the replacement is still contextual inference. The output is `寒武纪〔疑似校订；原转写：韩武G〕`.
- Use `segment_index` for a single exact segment, or `start`/`end` for an overlapping time range. Omit all three only when every exact occurrence has the same meaning.
- Set `expected_matches` explicitly when more than one exact occurrence should be changed. The default is `1`.
- Keep evidence in `reason`; the script records it in the audit report instead of bloating the transcript.

The model may identify candidates and research terminology, but it must only write this structured manifest. Exact matching, replacement, annotation, validation, and report generation belong to the script.

## Apply and verify

```bash
python3 "$SKILL_DIR/scripts/transcription_postprocess.py" \
  apply-corrections transcript_clean.json \
  --corrections transcript_corrections.json \
  --out transcript_corrected.json \
  --report correction_report.json
```

The command is idempotent for already annotated matches. It fails without writing the output transcript when an ID is duplicated, a scope is invalid, or an exact match count differs from `expected_matches`.

Generate every final format from `transcript_corrected.json`:

```bash
python3 "$SKILL_DIR/scripts/transcription_postprocess.py" \
  emit-deliverables transcript_corrected.json \
  --metadata info_manual.json \
  --out-dir .
```

Keep `transcript_corrections.json` and `correction_report.json` in the package. A separate uncertainty list may be generated from the report for navigation, but it must not replace inline annotations.
