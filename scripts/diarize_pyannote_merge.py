#!/usr/bin/env python3
from __future__ import annotations

"""Merge pyannote diarization with Whisper/MLX transcript_clean.json.

Expected files in the working directory:
  - episode_16k.wav (mono, 16 kHz; create with ffmpeg)
  - transcript_clean.json (Whisper JSON with segments)

Outputs:
  - pyannote_diarization.rttm
  - transcript_speaker_labeled_pyannote.{md,txt,srt,json}

Run on Apple Silicon with a pinned environment:
  uv run --offline \
    --with 'pyannote.audio==3.3.2' \
    --with 'torch==2.2.2' \
    --with 'torchaudio==2.2.2' \
    --with 'numpy<2' \
    --with 'huggingface_hub<0.25' \
    python diarize_pyannote_merge.py
"""

import argparse
import json
import os
import pathlib
import re
from collections import Counter, defaultdict

DEFAULT_HOST_NAME = os.environ.get("DIAR_HOST_NAME", "Host")
DEFAULT_GUEST_NAME = os.environ.get("DIAR_GUEST_NAME", "Guest")
DEFAULT_HOST_ANCHOR = os.environ.get("DIAR_HOST_ANCHOR", "0,120")
DEFAULT_GUEST_ANCHOR = os.environ.get("DIAR_GUEST_ANCHOR", "140,210")


def load_token(token_file: pathlib.Path | None = None) -> str | None:
    token = os.environ.get("HF_TOKEN") or os.environ.get("HUGGINGFACE_TOKEN")
    if token:
        return token.strip()
    if token_file:
        return token_file.read_text(encoding="utf-8").strip()
    # Non-interactive agent shells may not inherit the user's interactive env.
    # Read shell rc files without printing token values.
    for path in [
        pathlib.Path.home() / ".zshenv",
        pathlib.Path.home() / ".zprofile",
        pathlib.Path.home() / ".zshrc",
        pathlib.Path.home() / ".bash_profile",
        pathlib.Path.home() / ".bashrc",
    ]:
        if not path.exists():
            continue
        text = path.read_text(errors="ignore")
        match = re.search(
            r"^\s*export\s+(?:HF_TOKEN|HUGGINGFACE_TOKEN)=(?:[\"']?)([^\"'\n#\s]+)",
            text,
            re.M,
        )
        if match:
            return match.group(1).strip()
    return None


def parse_anchor(value: str) -> tuple[float, float]:
    parts = [p.strip() for p in value.split(",")]
    if len(parts) != 2:
        raise argparse.ArgumentTypeError("anchor must be START,END seconds")
    start, end = map(float, parts)
    if end <= start:
        raise argparse.ArgumentTypeError("anchor end must be greater than start")
    return start, end


def resolve_path(base: pathlib.Path, path: pathlib.Path) -> pathlib.Path:
    return path if path.is_absolute() else base / path


def write_json(path: pathlib.Path, data: object) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def ts(sec: float) -> str:
    sec = max(0, float(sec))
    h = int(sec // 3600)
    m = int(sec % 3600 // 60)
    s = int(sec % 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def srt_ts(sec: float) -> str:
    sec = max(0, float(sec))
    total = int(sec)
    ms = int(round((sec - total) * 1000))
    if ms == 1000:
        total += 1
        ms = 0
    h = total // 3600
    m = (total % 3600) // 60
    s = total % 60
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-dir", type=pathlib.Path, default=pathlib.Path.cwd())
    parser.add_argument("--audio", type=pathlib.Path, default=pathlib.Path("episode_16k.wav"))
    parser.add_argument("--transcript", type=pathlib.Path, default=pathlib.Path("transcript_clean.json"))
    parser.add_argument("--rttm", type=pathlib.Path, default=pathlib.Path("pyannote_diarization.rttm"))
    parser.add_argument("--out-prefix", type=pathlib.Path, default=pathlib.Path("transcript_speaker_labeled_pyannote"))
    parser.add_argument("--report", type=pathlib.Path, default=pathlib.Path("diarization_quality_report.json"))
    parser.add_argument("--host-name", default=DEFAULT_HOST_NAME)
    parser.add_argument("--guest-name", default=DEFAULT_GUEST_NAME)
    parser.add_argument("--host-anchor", type=parse_anchor, default=parse_anchor(DEFAULT_HOST_ANCHOR))
    parser.add_argument("--guest-anchor", type=parse_anchor, default=parse_anchor(DEFAULT_GUEST_ANCHOR))
    parser.add_argument("--min-speakers", type=int, default=2)
    parser.add_argument("--max-speakers", type=int, default=2)
    parser.add_argument("--hf-token-file", type=pathlib.Path, help="Optional file containing a Hugging Face token; env vars are preferred.")
    parser.add_argument("--diagnose-only", action="store_true", help="Require an existing RTTM and skip pyannote execution.")
    parser.add_argument("--fail-on-warning", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    base = args.base_dir
    audio = resolve_path(base, args.audio)
    transcript = resolve_path(base, args.transcript)
    rttm = resolve_path(base, args.rttm)
    out_prefix = resolve_path(base, args.out_prefix)
    report_path = resolve_path(base, args.report)
    out_json = out_prefix.with_suffix(".json")
    out_md = out_prefix.with_suffix(".md")
    out_txt = out_prefix.with_suffix(".txt")
    out_srt = out_prefix.with_suffix(".srt")

    if not audio.exists():
        raise SystemExit(f"Missing {audio}; create it with: ffmpeg -y -i episode.m4a -ac 1 -ar 16000 episode_16k.wav")
    if not transcript.exists():
        raise SystemExit(f"Missing {transcript}")

    print("loading transcript", flush=True)
    data = json.loads(transcript.read_text(encoding="utf-8"))
    segments = data["segments"]

    if not rttm.exists():
        if args.diagnose_only:
            raise SystemExit(f"Missing {rttm}; cannot diagnose without an RTTM")
        token = load_token(args.hf_token_file)
        if not token:
            raise SystemExit("HF_TOKEN not found in environment, --hf-token-file, or shell rc files")
        print("loading pyannote pipeline", flush=True)
        from pyannote.audio import Pipeline
        import torch

        # pyannote.audio 3.x uses use_auth_token; 4.x may use token but can pull community-1 assets.
        pipeline = Pipeline.from_pretrained("pyannote/speaker-diarization-3.1", use_auth_token=token)
        if torch.backends.mps.is_available():
            print("moving pipeline to MPS", flush=True)
            pipeline.to(torch.device("mps"))
        else:
            print("MPS not available; using CPU", flush=True)
        print("running diarization; this can take several minutes", flush=True)
        diarization = pipeline(str(audio), min_speakers=args.min_speakers, max_speakers=args.max_speakers)
        with rttm.open("w", encoding="utf-8") as f:
            diarization.write_rttm(f)
        print("wrote RTTM", flush=True)

    turns = []
    for line in rttm.read_text(encoding="utf-8").splitlines():
        parts = line.split()
        if len(parts) >= 8 and parts[0] == "SPEAKER":
            start = float(parts[3])
            dur = float(parts[4])
            turns.append({"start": start, "end": start + dur, "raw": parts[7]})
    turns.sort(key=lambda t: t["start"])
    raw_speakers = sorted({t["raw"] for t in turns})
    print("speaker turns", len(turns), raw_speakers, flush=True)
    if not turns:
        raise SystemExit(f"No SPEAKER turns found in {rttm}")

    def assign_raw(seg):
        a, b = float(seg["start"]), float(seg["end"])
        scores = {}
        for turn in turns:
            if turn["end"] <= a:
                continue
            if turn["start"] >= b:
                break
            ov = max(0.0, min(b, turn["end"]) - max(a, turn["start"]))
            if ov > 0:
                scores[turn["raw"]] = scores.get(turn["raw"], 0.0) + ov
        if scores:
            return max(scores, key=scores.get), scores
        mid = (a + b) / 2
        nearest = min(turns, key=lambda t: min(abs(mid - t["start"]), abs(mid - t["end"])))
        return nearest["raw"], {}

    for seg in segments:
        raw, scores = assign_raw(seg)
        seg["speaker_raw"] = raw
        seg["speaker_overlap_scores"] = scores

    def anchor_summary(start: float, end: float) -> dict:
        vals = []
        dur = defaultdict(float)
        for seg in segments:
            mid = (seg["start"] + seg["end"]) / 2
            if start <= mid <= end:
                vals.append(seg["speaker_raw"])
                dur[seg["speaker_raw"]] += max(0.0, float(seg["end"]) - float(seg["start"]))
        counts = Counter(vals)
        dominant = counts.most_common(1)[0][0] if counts else None
        return {
            "start": start,
            "end": end,
            "dominant_raw": dominant,
            "segment_counts": dict(counts),
            "segment_durations": {k: round(v, 3) for k, v in sorted(dur.items())},
        }

    host_anchor_summary = anchor_summary(*args.host_anchor)
    guest_anchor_summary = anchor_summary(*args.guest_anchor)
    host_raw = host_anchor_summary["dominant_raw"]
    guest_raw = guest_anchor_summary["dominant_raw"]
    warnings = []
    if host_raw == guest_raw:
        warnings.append("host-and-guest-anchors-map-to-same-raw-speaker")
        others = [r for r in sorted({s["speaker_raw"] for s in segments}) if r != host_raw]
        if others:
            guest_raw = others[0]
            warnings.append("guest-raw-speaker-fell-back-to-first-other-cluster")
    print("mapping host_raw=", host_raw, "guest_raw=", guest_raw, flush=True)

    for seg in segments:
        if seg["speaker_raw"] == host_raw:
            seg["speaker"] = args.host_name
        elif seg["speaker_raw"] == guest_raw:
            seg["speaker"] = args.guest_name
        else:
            seg["speaker"] = seg["speaker_raw"]

    turn_durations = defaultdict(float)
    for turn in turns:
        turn_durations[turn["raw"]] += max(0.0, turn["end"] - turn["start"])
    segment_durations = defaultdict(float)
    segment_counts = Counter()
    for seg in segments:
        raw = seg["speaker_raw"]
        segment_counts[raw] += 1
        segment_durations[raw] += max(0.0, float(seg["end"]) - float(seg["start"]))
    total_segment_duration = sum(segment_durations.values())
    if len(raw_speakers) < max(2, args.min_speakers):
        warnings.append("fewer-raw-speakers-than-requested")
    if total_segment_duration:
        largest_raw, largest_duration = max(segment_durations.items(), key=lambda kv: kv[1])
        largest_ratio = largest_duration / total_segment_duration
        if len(segment_durations) >= 2 and largest_ratio >= 0.85:
            warnings.append("one-raw-speaker-dominates-transcript")
    else:
        largest_raw, largest_ratio = None, 0.0
        warnings.append("no-segment-duration")

    groups = []
    cur = None
    for seg in segments:
        text = seg["text"].strip()
        sp = seg["speaker"]
        if not text:
            continue
        if cur is None or cur["speaker"] != sp or seg["start"] - cur["end"] > 2.5 or len(cur["text"]) > 520:
            cur = {"speaker": sp, "start": seg["start"], "end": seg["end"], "texts": [text], "text": text}
            groups.append(cur)
        else:
            cur["end"] = seg["end"]
            cur["texts"].append(text)
            cur["text"] = " ".join(cur["texts"])

    md = [
        "# 双人说话人标注版逐字稿（pyannote）",
        "",
        "- 方法：Whisper 转录 + pyannote/speaker-diarization-3.1 两人说话人分离 + 时间戳对齐。",
        f"- 说话人映射：host={args.host_name}, guest={args.guest_name}；可用 CLI 参数或 DIAR_* 环境变量调整锚点。",
        "- 注意：自动说话人标注；短促插话、重叠说话、片头预告剪辑处仍可能误标，重要引用建议人工核听。",
        "",
        "## 逐字稿",
        "",
    ]
    if warnings:
        md.insert(5, f"- 诊断警告：{', '.join(warnings)}。请检查 `diarization_quality_report.json`，必要时用更大的 `--max-speakers` 重跑。")
    for group in groups:
        md.append(f"[{ts(group['start'])}] **{group['speaker']}**：{group['text']}")
    out_md.write_text("\n\n".join(md) + "\n", encoding="utf-8")
    out_txt.write_text("\n".join(f"[{ts(g['start'])}] {g['speaker']}：{g['text']}" for g in groups) + "\n", encoding="utf-8")

    srt = []
    for i, group in enumerate(groups, 1):
        srt += [str(i), f"{srt_ts(group['start'])} --> {srt_ts(group['end'])}", f"{group['speaker']}：{group['text']}", ""]
    out_srt.write_text("\n".join(srt), encoding="utf-8")
    output_payload = {
        "method": "whisper + pyannote/speaker-diarization-3.1",
        "params": {"min_speakers": args.min_speakers, "max_speakers": args.max_speakers},
        "turns": turns,
        "segments": segments,
        "groups": groups,
        "mapping": {"host_raw": host_raw, "guest_raw": guest_raw, "host_name": args.host_name, "guest_name": args.guest_name},
        "warnings": warnings,
    }
    write_json(out_json, output_payload)
    quality_report = {
        "audio": str(audio),
        "transcript": str(transcript),
        "rttm": str(rttm),
        "params": {"min_speakers": args.min_speakers, "max_speakers": args.max_speakers},
        "raw_speakers": raw_speakers,
        "turn_count": len(turns),
        "turn_durations": {k: round(v, 3) for k, v in sorted(turn_durations.items())},
        "segment_counts": dict(segment_counts),
        "segment_durations": {k: round(v, 3) for k, v in sorted(segment_durations.items())},
        "largest_segment_duration_ratio": round(largest_ratio, 4),
        "largest_segment_duration_raw": largest_raw,
        "host_anchor": host_anchor_summary,
        "guest_anchor": guest_anchor_summary,
        "mapping": {"host_raw": host_raw, "guest_raw": guest_raw, "host_name": args.host_name, "guest_name": args.guest_name},
        "warnings": warnings,
        "recommendation": "retry with a larger --max-speakers or produce text-inferred labels" if warnings else "speaker labeling passed basic automatic diagnostics",
    }
    write_json(report_path, quality_report)
    print("groups", len(groups), flush=True)
    print("warnings", warnings, flush=True)
    print("wrote", out_md.name, out_txt.name, out_srt.name, out_json.name, report_path.name, flush=True)
    return 2 if warnings and args.fail_on_warning else 0


if __name__ == "__main__":
    raise SystemExit(main())
