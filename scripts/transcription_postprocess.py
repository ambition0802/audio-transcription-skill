#!/usr/bin/env python3
"""Deterministic helpers for the audio-transcription skill.

Subcommands:
  select-bilibili-audio   Pick the best audio .m4s URL from Browser pageAssets JSON.
  extract-subtitle-url    Extract subtitle.bilibili.com URL from Bilibili subtitle response.
  inspect-subtitle        Check Bilibili subtitle body coverage and empty-line ratio.
  verify-audio            Inspect an audio file with ffprobe and validate duration/streams.
  build-metadata          Build or merge an info_manual.json-style metadata file.
  inspect-transcript      Flag likely Whisper hallucination/repetition regions.
  merge-slice             Replace a damaged time range with a separately transcribed slice.
  apply-corrections       Apply reviewed term corrections with inline provenance labels.
  emit-deliverables       Generate txt/md/srt/vtt/json deliverables from segment JSON.
  verify-package          Verify deliverables, compute hashes, and create a zip package.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import math
import re
import subprocess
import sys
import zipfile
import zlib
from collections import Counter
from pathlib import Path
from urllib.parse import urlparse


DEFAULT_MODEL = "mlx-community/whisper-large-v3-mlx"
AUDIO_IDS = ("30280", "30232", "30216")
VIDEO_IDS = ("100022", "100023", "100050", "100051", "100052")

DEFAULT_PACKAGE_FILES = (
    "transcript.md",
    "transcript_timestamped.txt",
    "transcript_no_timestamps.md",
    "transcript_no_timestamps.txt",
    "transcript_clean.json",
    "transcript_clean.srt",
    "transcript_clean.vtt",
    "summary.md",
    "summary_arguments.md",
    "info.json",
    "info_manual.json",
    "hallucination_report.json",
    "transcript_corrections.json",
    "correction_report.json",
    "audio_verification.json",
    "transcript_speaker_labeled.md",
    "transcript_speaker_labeled.txt",
    "transcript_speaker_labeled.srt",
    "transcript_speaker_labeled.json",
    "transcript_speaker_labeled_pyannote.md",
    "transcript_speaker_labeled_pyannote.txt",
    "transcript_speaker_labeled_pyannote.srt",
    "transcript_speaker_labeled_pyannote.json",
    "diarization_quality_report.json",
)


def read_json(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, data: object) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def human_size(num: int | float) -> str:
    value = float(num)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{value:.1f}{unit}" if unit != "B" else f"{int(value)}B"
        value /= 1024
    return f"{value:.1f}TB"


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def line_count(path: Path) -> int | None:
    try:
        with path.open("r", encoding="utf-8") as f:
            return sum(1 for _ in f)
    except UnicodeDecodeError:
        return None


def file_metric(path: Path) -> dict:
    stat = path.stat()
    metric = {
        "path": str(path),
        "name": path.name,
        "size_bytes": stat.st_size,
        "size_human": human_size(stat.st_size),
        "sha256": sha256_file(path),
    }
    lines = line_count(path)
    if lines is not None:
        metric["line_count"] = lines
    return metric


def normalize_assets(data: object) -> list[dict]:
    if isinstance(data, list):
        return [x for x in data if isinstance(x, dict)]
    if isinstance(data, dict):
        assets = data.get("assets")
        if isinstance(assets, list):
            return [x for x in assets if isinstance(x, dict)]
        if isinstance(data.get("inventory"), dict) and isinstance(data["inventory"].get("assets"), list):
            return [x for x in data["inventory"]["assets"] if isinstance(x, dict)]
    return []


def m4s_basename(url: str) -> str:
    return Path(urlparse(url).path).name


def score_bilibili_audio(asset: dict) -> tuple[int, dict]:
    url = str(asset.get("url") or "")
    name = str(asset.get("name") or m4s_basename(url))
    host = urlparse(url).netloc
    basename = name or m4s_basename(url)
    sources = asset.get("sources") or []
    source_count = len(sources) if isinstance(sources, list) else 0

    score = 0
    reasons: list[str] = []
    if ".m4s" not in url and ".m4s" not in name:
        return -1000, {"reasons": ["not-m4s"], "source_count": source_count}
    if "bilivideo.com" in host:
        score += 40
        reasons.append("bilivideo-host")
    if "data.bilibili.com" in host:
        score -= 80
        reasons.append("telemetry-url")
    for audio_id in AUDIO_IDS:
        if f"-{audio_id}.m4s" in basename or f"-{audio_id}.m4s" in url:
            score += 200
            reasons.append(f"audio-id-{audio_id}")
    for video_id in VIDEO_IDS:
        if f"-{video_id}.m4s" in basename or f"-{video_id}.m4s" in url:
            score -= 120
            reasons.append(f"video-id-{video_id}")
    score += min(source_count, 30)
    if source_count:
        reasons.append(f"source-count-{source_count}")
    return score, {"reasons": reasons, "source_count": source_count, "basename": basename, "host": host}


def cmd_select_bilibili_audio(args: argparse.Namespace) -> int:
    data = read_json(args.media_assets)
    assets = normalize_assets(data)
    candidates = []
    for asset in assets:
        score, detail = score_bilibili_audio(asset)
        url = str(asset.get("url") or "")
        if score > -1000 and url:
            candidates.append(
                {
                    "score": score,
                    "name": asset.get("name") or detail.get("basename"),
                    "url": url,
                    **detail,
                }
            )
    candidates.sort(key=lambda x: x["score"], reverse=True)
    if not candidates:
        raise SystemExit(f"No .m4s candidates found in {args.media_assets}")
    chosen = candidates[0]
    args.out.write_text(chosen["url"] + "\n", encoding="utf-8")
    report = {"chosen": chosen, "candidates": candidates[:20], "candidate_count": len(candidates)}
    if args.report:
        write_json(args.report, report)
    print(json.dumps({"chosen_name": chosen["name"], "score": chosen["score"], "out": str(args.out)}, ensure_ascii=False))
    return 0


def recursive_strings(obj: object) -> list[str]:
    vals: list[str] = []
    if isinstance(obj, str):
        vals.append(obj)
    elif isinstance(obj, dict):
        for value in obj.values():
            vals.extend(recursive_strings(value))
    elif isinstance(obj, list):
        for value in obj:
            vals.extend(recursive_strings(value))
    return vals


def find_subtitle_url_bytes(data: bytes) -> str | None:
    patterns = [
        rb"https?://subtitle\.bilibili\.com/.*?auth_key=\d+-[a-f0-9]{32}-0-[a-f0-9]{32}",
        rb"//subtitle\.bilibili\.com/.*?auth_key=\d+-[a-f0-9]{32}-0-[a-f0-9]{32}",
    ]
    for pat in patterns:
        match = re.search(pat, data, re.S)
        if match:
            url = match.group(0).decode("latin1")
            return url if url.startswith("http") else "https:" + url
    return None


def cmd_extract_subtitle_url(args: argparse.Namespace) -> int:
    raw = args.response.read_bytes()
    url = find_subtitle_url_bytes(raw)
    if not url:
        try:
            obj = json.loads(raw.decode("utf-8"))
        except Exception:
            obj = None
        if obj is not None:
            for value in recursive_strings(obj):
                if "subtitle.bilibili.com" in value and "auth_key=" in value:
                    url = value if value.startswith("http") else "https:" + value
                    break
    report = {"found": bool(url), "url": url, "response_size": len(raw)}
    if args.report:
        write_json(args.report, report)
    if not url:
        if args.allow_missing:
            print(json.dumps(report, ensure_ascii=False))
            return 0
        raise SystemExit(
            f"No subtitle body URL found in {args.response}. "
            "Save the raw x/v2/subtitle/web/view response and retry, or continue with Whisper."
        )
    args.out.write_text(url + "\n", encoding="utf-8")
    print(json.dumps({"out": str(args.out), "url_length": len(url)}, ensure_ascii=False))
    return 0


def cmd_verify_audio(args: argparse.Namespace) -> int:
    if not args.audio.exists():
        raise SystemExit(f"Missing audio file: {args.audio}")
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration,size,format_name:stream=index,codec_type,codec_name,channels,sample_rate,duration",
        "-of",
        "json",
        str(args.audio),
    ]
    try:
        proc = subprocess.run(cmd, check=True, capture_output=True, text=True)
    except FileNotFoundError as exc:
        raise SystemExit("ffprobe not found. Install ffmpeg first, for example: brew install ffmpeg") from exc
    except subprocess.CalledProcessError as exc:
        raise SystemExit(f"ffprobe failed for {args.audio}: {exc.stderr.strip()}") from exc

    info = json.loads(proc.stdout)
    fmt = info.get("format") or {}
    streams = [s for s in info.get("streams", []) if isinstance(s, dict)]
    audio_streams = [s for s in streams if s.get("codec_type") == "audio"]
    duration = float(fmt.get("duration") or 0.0)
    size = int(float(fmt.get("size") or args.audio.stat().st_size or 0))
    warnings: list[str] = []
    ok = True
    if duration <= 0:
        ok = False
        warnings.append("duration-not-positive")
    if size <= 0:
        ok = False
        warnings.append("size-not-positive")
    if not audio_streams:
        ok = False
        warnings.append("no-audio-stream")
    duration_gap = None
    if args.expected_duration is not None:
        duration_gap = float(args.expected_duration) - duration
        if abs(duration_gap) > args.duration_tolerance:
            ok = False
            warnings.append("duration-outside-tolerance")

    report = {
        "audio": str(args.audio),
        "ok": ok,
        "warnings": warnings,
        "duration_seconds": duration,
        "size_bytes": size,
        "size_human": human_size(size),
        "format_name": fmt.get("format_name"),
        "audio_streams": audio_streams,
        "expected_duration_seconds": args.expected_duration,
        "duration_gap_seconds": duration_gap,
        "duration_tolerance_seconds": args.duration_tolerance,
        "sha256": sha256_file(args.audio),
    }
    if args.report:
        write_json(args.report, report)
    print(json.dumps({"ok": ok, "duration_seconds": duration, "size_human": human_size(size), "warnings": warnings}, ensure_ascii=False))
    return 0 if ok or not args.fail_on_warning else 2


def cmd_build_metadata(args: argparse.Namespace) -> int:
    metadata: dict = {}
    if args.from_json:
        if not args.from_json.exists():
            raise SystemExit(f"Missing metadata source: {args.from_json}")
        source = read_json(args.from_json)
        if not isinstance(source, dict):
            raise SystemExit(f"Metadata source must be a JSON object: {args.from_json}")
        metadata.update(source)

    field_map = {
        "id": args.id,
        "bv_id": args.bv_id,
        "cid": args.cid,
        "title": args.title,
        "uploader": args.uploader,
        "source_url": args.source_url,
        "webpage_url": args.source_url,
        "duration": args.duration,
        "duration_seconds": args.duration,
        "transcription_model": args.model,
        "source_kind": args.source_kind,
        "metadata_note": args.note,
    }
    for key, value in field_map.items():
        if value is not None and value != "":
            metadata[key] = value
    if args.fallback:
        notes = metadata.get("notes")
        if isinstance(notes, list):
            notes.append(args.fallback)
        elif notes:
            notes = [str(notes), args.fallback]
        else:
            notes = [args.fallback]
        metadata["notes"] = notes
        metadata["metadata_source"] = "manual-browser-fallback"
    if metadata.get("duration") is None and metadata.get("duration_seconds") is not None:
        metadata["duration"] = metadata["duration_seconds"]
    if metadata.get("duration_seconds") is None and metadata.get("duration") is not None:
        metadata["duration_seconds"] = metadata["duration"]
    if not metadata.get("source_url") and metadata.get("webpage_url"):
        metadata["source_url"] = metadata["webpage_url"]
    if not metadata.get("webpage_url") and metadata.get("source_url"):
        metadata["webpage_url"] = metadata["source_url"]
    if not metadata.get("id") and metadata.get("bvid"):
        metadata["id"] = metadata["bvid"]
    if not metadata.get("bv_id") and metadata.get("bvid"):
        metadata["bv_id"] = metadata["bvid"]
    if not metadata.get("bvid") and metadata.get("bv_id"):
        metadata["bvid"] = metadata["bv_id"]
    if not metadata.get("uploader") and metadata.get("uploader_observed_on_page"):
        metadata["uploader"] = metadata["uploader_observed_on_page"]
    metadata.setdefault("transcription_model", DEFAULT_MODEL)
    metadata.setdefault("review_note", "Machine transcript; proper nouns and technical terms may need human review.")

    missing = [name for name in args.require if not metadata.get(name)]
    report = {"out": str(args.out), "metadata": metadata, "missing_required": missing, "ok": not missing}
    write_json(args.out, metadata)
    if args.report:
        write_json(args.report, report)
    print(json.dumps({"out": str(args.out), "ok": not missing, "missing_required": missing}, ensure_ascii=False))
    return 0 if not missing or not args.fail_on_missing else 2


def load_segments(path: Path, include_empty: bool = False) -> tuple[dict, list[dict]]:
    data = read_json(path)
    metadata: dict = {}
    segments: list[dict] = []
    if isinstance(data, dict) and isinstance(data.get("metadata"), dict):
        metadata = dict(data["metadata"])
    if isinstance(data, dict) and isinstance(data.get("segments"), list):
        segments = [dict(s) for s in data["segments"]]
    elif isinstance(data, dict) and isinstance(data.get("body"), list):
        for item in data["body"]:
            if not isinstance(item, dict):
                continue
            text = item.get("content") or item.get("text") or ""
            start = item.get("from", item.get("start"))
            end = item.get("to", item.get("end"))
            if start is None or end is None:
                continue
            segments.append({"start": float(start), "end": float(end), "text": str(text).strip()})
    else:
        raise SystemExit(f"Unsupported transcript JSON shape: {path}")
    clean = []
    for seg in segments:
        text = str(seg.get("text") or "").strip()
        if not text and not include_empty:
            continue
        clean.append({"start": float(seg.get("start", 0.0)), "end": float(seg.get("end", 0.0)), "text": text, **{k: v for k, v in seg.items() if k not in {"start", "end", "text"}}})
    return metadata, clean


def interval_union_seconds(segments: list[dict]) -> float:
    intervals = sorted((float(s["start"]), float(s["end"])) for s in segments if float(s["end"]) > float(s["start"]))
    if not intervals:
        return 0.0
    total = 0.0
    cur_start, cur_end = intervals[0]
    for start, end in intervals[1:]:
        if start <= cur_end:
            cur_end = max(cur_end, end)
        else:
            total += cur_end - cur_start
            cur_start, cur_end = start, end
    total += cur_end - cur_start
    return total


def cmd_inspect_subtitle(args: argparse.Namespace) -> int:
    metadata, segments = load_segments(args.subtitle, include_empty=True)
    segment_count = len(segments)
    empty_count = sum(1 for s in segments if not str(s.get("text") or "").strip())
    empty_ratio = empty_count / segment_count if segment_count else 1.0
    first_start = min((float(s["start"]) for s in segments), default=None)
    last_end = max((float(s["end"]) for s in segments), default=None)
    coverage_seconds = interval_union_seconds(segments)
    expected_duration = args.duration or metadata.get("duration_seconds") or metadata.get("duration")
    coverage_ratio = None
    duration_gap = None
    if expected_duration:
        expected_duration = float(expected_duration)
        coverage_ratio = coverage_seconds / expected_duration if expected_duration > 0 else 0.0
        duration_gap = expected_duration - float(last_end or 0.0)

    warnings: list[str] = []
    ok = True
    if segment_count == 0:
        ok = False
        warnings.append("no-subtitle-segments")
    if empty_ratio > args.max_empty_ratio:
        ok = False
        warnings.append("too-many-empty-segments")
    if first_start is not None and first_start > args.start_tolerance:
        ok = False
        warnings.append("subtitle-starts-too-late")
    if expected_duration is not None:
        if coverage_ratio is not None and coverage_ratio < args.min_coverage:
            ok = False
            warnings.append("coverage-ratio-too-low")
        if duration_gap is not None and duration_gap > args.end_tolerance:
            ok = False
            warnings.append("subtitle-ends-too-early")
    else:
        warnings.append("expected-duration-missing")

    report = {
        "subtitle": str(args.subtitle),
        "ok": ok,
        "usable_as_primary": ok,
        "warnings": warnings,
        "segment_count": segment_count,
        "empty_count": empty_count,
        "empty_ratio": round(empty_ratio, 4),
        "first_start": first_start,
        "last_end": last_end,
        "expected_duration": expected_duration,
        "duration_gap_seconds": duration_gap,
        "coverage_seconds": round(coverage_seconds, 3),
        "coverage_ratio": round(coverage_ratio, 4) if coverage_ratio is not None else None,
        "thresholds": {
            "min_coverage": args.min_coverage,
            "start_tolerance": args.start_tolerance,
            "end_tolerance": args.end_tolerance,
            "max_empty_ratio": args.max_empty_ratio,
        },
    }
    if args.report:
        write_json(args.report, report)
    print(
        json.dumps(
            {
                "ok": ok,
                "segments": segment_count,
                "coverage_ratio": report["coverage_ratio"],
                "duration_gap_seconds": duration_gap,
                "warnings": warnings,
            },
            ensure_ascii=False,
        )
    )
    if not ok and args.fail_on_incomplete:
        return 2
    return 0


def repetition_metrics(text: str) -> dict:
    chars = [c for c in text.strip() if not c.isspace()]
    if not chars:
        return {"length": 0, "max_char_ratio": 0.0, "unique_ratio": 1.0, "compression_ratio": 1.0}
    counts = Counter(chars)
    max_char_ratio = max(counts.values()) / len(chars)
    unique_ratio = len(counts) / len(chars)
    raw = text.encode("utf-8", errors="ignore")
    compression_ratio = len(zlib.compress(raw)) / max(1, len(raw))
    return {
        "length": len(chars),
        "max_char_ratio": round(max_char_ratio, 4),
        "unique_ratio": round(unique_ratio, 4),
        "compression_ratio": round(compression_ratio, 4),
        "top_char": counts.most_common(1)[0][0],
    }


def flag_segment(seg: dict) -> list[str]:
    text = str(seg.get("text") or "")
    start = float(seg.get("start", 0.0))
    end = float(seg.get("end", start))
    dur = max(0.0, end - start)
    m = repetition_metrics(text)
    flags: list[str] = []
    if m["length"] >= 80 and m["max_char_ratio"] >= 0.35:
        flags.append("high-single-char-repeat")
    if m["length"] >= 120 and m["unique_ratio"] <= 0.12:
        flags.append("low-unique-char-ratio")
    if m["length"] >= 160 and m["compression_ratio"] <= 0.28:
        flags.append("highly-compressible-text")
    if dur >= 25 and m["length"] >= 180 and (m["max_char_ratio"] >= 0.25 or m["unique_ratio"] <= 0.18):
        flags.append("long-block-repetition")
    return flags


def cmd_inspect_transcript(args: argparse.Namespace) -> int:
    metadata, segments = load_segments(args.transcript)
    issues = []
    for idx, seg in enumerate(segments):
        flags = flag_segment(seg)
        if flags:
            issues.append(
                {
                    "index": idx,
                    "start": seg["start"],
                    "end": seg["end"],
                    "flags": flags,
                    "metrics": repetition_metrics(seg["text"]),
                    "text_preview": seg["text"][:160],
                }
            )
    first = segments[0] if segments else None
    last = segments[-1] if segments else None
    duration = args.duration or metadata.get("duration_seconds")
    duration_gap = None
    if duration and last:
        duration_gap = float(duration) - float(last["end"])
    report = {
        "transcript": str(args.transcript),
        "segment_count": len(segments),
        "first_segment": first,
        "last_segment": last,
        "expected_duration": duration,
        "duration_gap_seconds": duration_gap,
        "issue_count": len(issues),
        "issues": issues,
    }
    if args.report:
        write_json(args.report, report)
    print(json.dumps({"segments": len(segments), "issues": len(issues), "duration_gap_seconds": duration_gap}, ensure_ascii=False))
    if issues and args.fail_on_issues:
        return 2
    return 0


def dedupe_segments(segments: list[dict]) -> list[dict]:
    out: list[dict] = []
    for seg in sorted(segments, key=lambda s: (float(s["start"]), float(s["end"]))):
        text = str(seg.get("text") or "").strip()
        if not text:
            continue
        item = {**seg, "text": text, "start": float(seg["start"]), "end": float(seg["end"])}
        if out and out[-1]["text"] == item["text"] and abs(out[-1]["end"] - item["start"]) <= 0.05:
            out[-1]["end"] = item["end"]
            continue
        out.append(item)
    return out


def cmd_merge_slice(args: argparse.Namespace) -> int:
    base_meta, base = load_segments(args.base)
    slice_meta, slice_segments = load_segments(args.slice)
    start = float(args.start)
    end = float(args.end)
    offset = float(args.offset if args.offset is not None else start)
    merged = [s for s in base if not (start <= float(s["start"]) < end)]
    for seg in slice_segments:
        s = dict(seg)
        s["start"] = float(s["start"]) + offset
        s["end"] = float(s["end"]) + offset
        if s["end"] <= start or s["start"] >= end:
            continue
        s["start"] = max(s["start"], start)
        s["end"] = min(s["end"], end)
        merged.append(s)
    merged = dedupe_segments(merged)
    metadata = {**base_meta, **({"slice_metadata": slice_meta} if slice_meta else {})}
    payload = {"metadata": metadata, "segments": merged}
    write_json(args.out, payload)
    print(json.dumps({"out": str(args.out), "segments": len(merged), "replaced_start": start, "replaced_end": end}, ensure_ascii=False))
    return 0


CORRECTION_LABELS = {
    "confirmed": "校订",
    "probable": "疑似校订",
}


def correction_annotation(original: str, replacement: str, status: str) -> str:
    label = CORRECTION_LABELS[status]
    return f"{replacement}〔{label}；原转写：{original}〕"


def correction_target_indexes(entry: dict, segments: list[dict]) -> list[int]:
    has_index = "segment_index" in entry
    has_time = "start" in entry or "end" in entry
    if has_index and has_time:
        raise ValueError("use segment_index or start/end, not both")
    if has_index:
        index = entry["segment_index"]
        if not isinstance(index, int) or isinstance(index, bool):
            raise ValueError("segment_index must be an integer")
        if index < 0 or index >= len(segments):
            raise ValueError(f"segment_index {index} is out of range")
        return [index]
    if not has_time:
        return list(range(len(segments)))

    start = float(entry.get("start", 0.0))
    end = float(entry.get("end", math.inf))
    if not math.isfinite(start) or start < 0:
        raise ValueError("start must be a finite non-negative number")
    if end <= start:
        raise ValueError("end must be greater than start")
    return [
        index
        for index, segment in enumerate(segments)
        if float(segment["end"]) > start and float(segment["start"]) < end
    ]


def cmd_apply_corrections(args: argparse.Namespace) -> int:
    metadata, segments = load_segments(args.transcript)
    manifest = read_json(args.corrections)
    if not isinstance(manifest, dict) or manifest.get("version") != 1:
        raise SystemExit("Correction manifest must be an object with version: 1")
    entries = manifest.get("corrections")
    if not isinstance(entries, list):
        raise SystemExit("Correction manifest must contain a corrections array")

    results: list[dict] = []
    errors: list[str] = []
    seen_ids: set[str] = set()
    applied = 0
    already_applied = 0

    for position, raw_entry in enumerate(entries, 1):
        fallback_id = f"correction-{position:03d}"
        if not isinstance(raw_entry, dict):
            errors.append(f"{fallback_id}: entry must be an object")
            continue
        entry = dict(raw_entry)
        correction_id = str(entry.get("id") or fallback_id).strip()
        result = {"id": correction_id}
        results.append(result)
        if not correction_id:
            result.update({"state": "error", "error": "id must not be empty"})
            errors.append(f"{fallback_id}: id must not be empty")
            continue
        if correction_id in seen_ids:
            result.update({"state": "error", "error": "duplicate id"})
            errors.append(f"{correction_id}: duplicate id")
            continue
        seen_ids.add(correction_id)

        original = entry.get("original")
        replacement = entry.get("replacement")
        status = entry.get("status", "probable")
        expected_matches = entry.get("expected_matches", 1)
        try:
            if not isinstance(original, str) or not original:
                raise ValueError("original must be a non-empty string")
            if not isinstance(replacement, str) or not replacement:
                raise ValueError("replacement must be a non-empty string")
            if original == replacement:
                raise ValueError("original and replacement must differ")
            if any(mark in replacement for mark in ("〔", "〕")):
                raise ValueError("replacement must not contain annotation brackets")
            if status not in CORRECTION_LABELS:
                raise ValueError("status must be confirmed or probable")
            if not isinstance(expected_matches, int) or isinstance(expected_matches, bool) or expected_matches < 1:
                raise ValueError("expected_matches must be a positive integer")
            indexes = correction_target_indexes(entry, segments)
        except (TypeError, ValueError) as exc:
            result.update({"state": "error", "error": str(exc)})
            errors.append(f"{correction_id}: {exc}")
            continue

        annotated = correction_annotation(original, replacement, status)
        annotated_matches = 0
        raw_matches = 0
        for index in indexes:
            text = str(segments[index]["text"])
            annotated_matches += text.count(annotated)
            raw_matches += text.replace(annotated, "").count(original)

        result.update(
            {
                "status": status,
                "original": original,
                "replacement": replacement,
                "annotation": annotated,
                "target_segment_count": len(indexes),
                "expected_matches": expected_matches,
                "raw_matches": raw_matches,
                "annotated_matches": annotated_matches,
                "reason": entry.get("reason", ""),
            }
        )
        if annotated_matches == expected_matches and raw_matches == 0:
            result["state"] = "already-applied"
            already_applied += expected_matches
            continue
        if annotated_matches:
            message = "target contains a mixture of annotated and unannotated matches"
            result.update({"state": "error", "error": message})
            errors.append(f"{correction_id}: {message}")
            continue
        if raw_matches != expected_matches:
            message = f"expected {expected_matches} exact match(es), found {raw_matches}"
            result.update({"state": "error", "error": message})
            errors.append(f"{correction_id}: {message}")
            continue

        for index in indexes:
            segments[index]["text"] = str(segments[index]["text"]).replace(original, annotated)
        result["state"] = "applied"
        applied += raw_matches

    report = {
        "ok": not errors,
        "transcript": str(args.transcript),
        "corrections": str(args.corrections),
        "manifest_sha256": sha256_file(args.corrections),
        "out": str(args.out),
        "dry_run": args.dry_run,
        "entry_count": len(entries),
        "applied_matches": applied,
        "already_applied_matches": already_applied,
        "error_count": len(errors),
        "errors": errors,
        "results": results,
    }
    if args.report:
        write_json(args.report, report)
    if errors:
        print(json.dumps({"ok": False, "errors": len(errors), "report": str(args.report) if args.report else None}, ensure_ascii=False))
        return 2

    if not args.dry_run:
        status_counts = Counter(str(entry.get("status", "probable")) for entry in entries if isinstance(entry, dict))
        metadata["correction_note"] = "疑似错词已原位校订；〔校订/疑似校订；原转写：...〕保留机器转写以便复核。"
        metadata["transcript_corrections"] = {
            "manifest": args.corrections.name,
            "manifest_sha256": report["manifest_sha256"],
            "entry_count": len(entries),
            "confirmed_count": status_counts.get("confirmed", 0),
            "probable_count": status_counts.get("probable", 0),
        }
        write_json(args.out, {"metadata": metadata, "segments": segments})
    print(
        json.dumps(
            {
                "ok": True,
                "out": None if args.dry_run else str(args.out),
                "applied_matches": applied,
                "already_applied_matches": already_applied,
            },
            ensure_ascii=False,
        )
    )
    return 0


def ts(sec: float, sep: str = ".") -> str:
    sec = max(0.0, float(sec))
    total = int(math.floor(sec))
    ms = int(round((sec - total) * 1000))
    if ms == 1000:
        total += 1
        ms = 0
    h = total // 3600
    m = (total % 3600) // 60
    s = total % 60
    return f"{h:02d}:{m:02d}:{s:02d}{sep}{ms:03d}"


def paragraphize(segments: list[dict], max_chars: int = 220) -> str:
    paras: list[str] = []
    buf = ""
    for seg in segments:
        text = seg["text"].strip()
        if not buf:
            buf = text
        elif len(buf) < max_chars and not re.search(r"[。！？!?]$", buf):
            buf += " " + text
        else:
            paras.append(buf)
            buf = text
    if buf:
        paras.append(buf)
    return "\n\n".join(paras).strip() + "\n"


def cmd_emit_deliverables(args: argparse.Namespace) -> int:
    metadata, segments = load_segments(args.transcript)
    if args.metadata and args.metadata.exists():
        extra = read_json(args.metadata)
        if isinstance(extra, dict):
            metadata = {**metadata, **extra}
    if args.title:
        metadata["title"] = args.title
    if args.source_url:
        metadata["source_url"] = args.source_url
    metadata.setdefault("transcription_model", args.model or DEFAULT_MODEL)
    metadata.setdefault("review_note", "Machine transcript; proper nouns and technical terms may need human review.")
    metadata["source_kind"] = args.source_kind
    segments = dedupe_segments(segments)

    out_dir = args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    payload = {"metadata": metadata, "segments": segments}
    write_json(out_dir / "transcript_clean.json", payload)

    timestamped = "\n".join(f"[{ts(s['start'])} - {ts(s['end'])}] {s['text']}" for s in segments) + "\n"
    (out_dir / "transcript_timestamped.txt").write_text(timestamped, encoding="utf-8")

    plain = paragraphize(segments)
    (out_dir / "transcript_no_timestamps.txt").write_text(plain, encoding="utf-8")
    header = (
        f"# {metadata.get('title', 'Audio transcript')}\n\n"
        f"来源：{metadata.get('source_url', '')}\n"
        f"转录/字幕来源：{metadata.get('source_kind', '')}\n"
        f"转录模型：{metadata.get('transcription_model', '')}\n"
        f"说明：{metadata.get('review_note', '')}\n"
        + (f"校订标记：{metadata['correction_note']}\n" if metadata.get("correction_note") else "")
        + "\n---\n\n"
    )
    (out_dir / "transcript_no_timestamps.md").write_text(header + plain, encoding="utf-8")

    srt_lines: list[str] = []
    for idx, seg in enumerate(segments, 1):
        srt_lines += [str(idx), f"{ts(seg['start'], ',')} --> {ts(seg['end'], ',')}", seg["text"], ""]
    (out_dir / "transcript_clean.srt").write_text("\n".join(srt_lines), encoding="utf-8")

    vtt_lines = ["WEBVTT", ""]
    for seg in segments:
        vtt_lines += [f"{ts(seg['start'])} --> {ts(seg['end'])}", html.escape(seg["text"]), ""]
    (out_dir / "transcript_clean.vtt").write_text("\n".join(vtt_lines), encoding="utf-8")

    print(json.dumps({"out_dir": str(out_dir), "segments": len(segments)}, ensure_ascii=False))
    return 0


def cmd_verify_package(args: argparse.Namespace) -> int:
    base = args.base_dir
    if args.include:
        include_paths = [Path(p) for p in args.include]
    else:
        include_paths = [Path(name) for name in DEFAULT_PACKAGE_FILES]

    existing: list[Path] = []
    missing: list[str] = []
    for item in include_paths:
        path = item if item.is_absolute() else base / item
        if path.exists() and path.is_file():
            existing.append(path)
        else:
            missing.append(str(item))

    if args.require and missing:
        raise SystemExit(f"Missing required deliverables: {', '.join(missing)}")
    if not existing:
        raise SystemExit("No deliverable files found to verify/package")

    package_path = args.out if args.out.is_absolute() else base / args.out
    package_path.parent.mkdir(parents=True, exist_ok=True)
    if package_path in existing:
        existing = [p for p in existing if p != package_path]
    with zipfile.ZipFile(package_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in existing:
            try:
                arcname = str(path.relative_to(base))
            except ValueError:
                arcname = path.name
            zf.write(path, arcname)

    file_metrics = [file_metric(path) for path in existing]
    package_metric = file_metric(package_path)
    report = {
        "base_dir": str(base),
        "package": package_metric,
        "files": file_metrics,
        "missing": missing,
        "file_count": len(file_metrics),
    }
    if args.report:
        write_json(args.report, report)
    print(
        json.dumps(
            {
                "package": str(package_path),
                "package_size": package_metric["size_human"],
                "file_count": len(file_metrics),
                "missing_count": len(missing),
                "sha256": package_metric["sha256"],
            },
            ensure_ascii=False,
        )
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("select-bilibili-audio")
    p.add_argument("media_assets", type=Path)
    p.add_argument("--out", type=Path, default=Path("audio_url.txt"))
    p.add_argument("--report", type=Path)
    p.set_defaults(func=cmd_select_bilibili_audio)

    p = sub.add_parser("extract-subtitle-url")
    p.add_argument("response", type=Path)
    p.add_argument("--out", type=Path, default=Path("subtitle_body_url.txt"))
    p.add_argument("--report", type=Path)
    p.add_argument("--allow-missing", action="store_true")
    p.set_defaults(func=cmd_extract_subtitle_url)

    p = sub.add_parser("inspect-subtitle")
    p.add_argument("subtitle", type=Path)
    p.add_argument("--duration", type=float)
    p.add_argument("--min-coverage", type=float, default=0.95)
    p.add_argument("--start-tolerance", type=float, default=5.0)
    p.add_argument("--end-tolerance", type=float, default=5.0)
    p.add_argument("--max-empty-ratio", type=float, default=0.05)
    p.add_argument("--report", type=Path)
    p.add_argument("--fail-on-incomplete", action="store_true")
    p.set_defaults(func=cmd_inspect_subtitle)

    p = sub.add_parser("verify-audio")
    p.add_argument("audio", type=Path)
    p.add_argument("--expected-duration", type=float)
    p.add_argument("--duration-tolerance", type=float, default=3.0)
    p.add_argument("--report", type=Path)
    p.add_argument("--fail-on-warning", action="store_true")
    p.set_defaults(func=cmd_verify_audio)

    p = sub.add_parser("build-metadata")
    p.add_argument("--from-json", type=Path)
    p.add_argument("--out", type=Path, default=Path("info_manual.json"))
    p.add_argument("--report", type=Path)
    p.add_argument("--id")
    p.add_argument("--bv-id")
    p.add_argument("--cid")
    p.add_argument("--title")
    p.add_argument("--uploader")
    p.add_argument("--source-url")
    p.add_argument("--duration", type=float)
    p.add_argument("--model", default=DEFAULT_MODEL)
    p.add_argument("--source-kind", default="whisper")
    p.add_argument("--note")
    p.add_argument("--fallback")
    p.add_argument("--require", action="append", default=[])
    p.add_argument("--fail-on-missing", action="store_true")
    p.set_defaults(func=cmd_build_metadata)

    p = sub.add_parser("inspect-transcript")
    p.add_argument("transcript", type=Path)
    p.add_argument("--duration", type=float)
    p.add_argument("--report", type=Path)
    p.add_argument("--fail-on-issues", action="store_true")
    p.set_defaults(func=cmd_inspect_transcript)

    p = sub.add_parser("merge-slice")
    p.add_argument("--base", type=Path, required=True)
    p.add_argument("--slice", type=Path, required=True)
    p.add_argument("--start", type=float, required=True)
    p.add_argument("--end", type=float, required=True)
    p.add_argument("--offset", type=float)
    p.add_argument("--out", type=Path, required=True)
    p.set_defaults(func=cmd_merge_slice)

    p = sub.add_parser("apply-corrections")
    p.add_argument("transcript", type=Path)
    p.add_argument("--corrections", type=Path, required=True)
    p.add_argument("--out", type=Path, default=Path("transcript_corrected.json"))
    p.add_argument("--report", type=Path, default=Path("correction_report.json"))
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(func=cmd_apply_corrections)

    p = sub.add_parser("emit-deliverables")
    p.add_argument("transcript", type=Path)
    p.add_argument("--out-dir", type=Path, default=Path("."))
    p.add_argument("--metadata", type=Path)
    p.add_argument("--title")
    p.add_argument("--source-url")
    p.add_argument("--model", default=DEFAULT_MODEL)
    p.add_argument("--source-kind", default="whisper")
    p.set_defaults(func=cmd_emit_deliverables)

    p = sub.add_parser("verify-package")
    p.add_argument("--base-dir", type=Path, default=Path("."))
    p.add_argument("--include", nargs="+")
    p.add_argument("--out", type=Path, default=Path("transcript_package.zip"))
    p.add_argument("--report", type=Path, default=Path("package_report.json"))
    p.add_argument("--require", action="store_true")
    p.set_defaults(func=cmd_verify_package)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
