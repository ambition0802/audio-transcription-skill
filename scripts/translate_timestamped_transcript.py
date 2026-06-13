#!/usr/bin/env python3
"""Translate timestamped transcript lines while preserving timestamp prefixes.

Default engine is Argos Translate. Run it with:

  uvx --from argostranslate python translate_timestamped_transcript.py \
    transcript_timestamped.txt --out transcript_timestamped_zh.txt

Use --engine identity for parser/cache smoke tests without a translation model.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path


TIMESTAMP_RE = re.compile(r"^(\[[^\]]+\]\s*)(.*)$")
DEFAULT_KEEP_TERMS = (
    "Tesla",
    "SpaceX",
    "Grok",
    "Neuralink",
    "Starship",
    "AGI",
    "FSD",
    "OpenAI",
    "ChatGPT",
)
DEFAULT_REPLACEMENTS = (
    ("奇特", "奇点"),
    ("星际迷航", "《星际迷航》"),
    ("终结者", "《终结者》"),
)


def read_json(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, data: object) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def parse_line(line: str, require_timestamp: bool) -> tuple[str, str]:
    match = TIMESTAMP_RE.match(line)
    if match:
        return match.group(1), match.group(2)
    if require_timestamp and line.strip():
        raise ValueError(f"Line does not start with a timestamp prefix: {line[:120]}")
    return "", line


def make_out_path(src: Path, to_code: str) -> Path:
    stem = src.stem
    if stem.endswith("_timestamped"):
        stem += f"_{to_code}"
    else:
        stem += f"_{to_code}"
    return src.with_name(stem + src.suffix)


def load_cache(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    data = read_json(path)
    if isinstance(data, dict):
        return {str(k): str(v) for k, v in data.items()}
    raise SystemExit(f"Translation cache must be a JSON object: {path}")


def flush_outputs(out_path: Path, cache_path: Path, out_lines: list[str], cache: dict[str, str]) -> None:
    out_path.write_text("\n".join(out_lines) + ("\n" if out_lines else ""), encoding="utf-8")
    write_json(cache_path, cache)


def protect_terms(text: str, terms: list[str]) -> tuple[str, dict[str, str]]:
    protected = text
    mapping: dict[str, str] = {}
    for idx, term in enumerate(sorted(set(terms), key=len, reverse=True)):
        if not term:
            continue
        placeholder = f"ZXKEEPTERM{idx}XZ"
        pattern = re.compile(re.escape(term))
        if pattern.search(protected):
            protected = pattern.sub(placeholder, protected)
            mapping[placeholder] = term
    return protected, mapping


def restore_terms(text: str, mapping: dict[str, str]) -> str:
    restored = text
    for placeholder, term in mapping.items():
        restored = restored.replace(placeholder, term)
        restored = re.sub(r"\s*".join(map(re.escape, placeholder)), term, restored)
    return restored


def apply_replacements(text: str, replacements: list[tuple[str, str]]) -> str:
    out = text
    for old, new in replacements:
        out = out.replace(old, new)
    return out


def load_argos_translator(from_code: str, to_code: str, install_model: bool):
    try:
        import argostranslate.package
        import argostranslate.translate
    except ImportError as exc:
        raise SystemExit(
            "argostranslate is not installed. Run with: "
            "uvx --from argostranslate python translate_timestamped_transcript.py ..."
        ) from exc

    def find_translation():
        langs = argostranslate.translate.get_installed_languages()
        src = next((lang for lang in langs if lang.code == from_code), None)
        dst = next((lang for lang in langs if lang.code == to_code), None)
        if not src or not dst:
            return None
        return src.get_translation(dst)

    translation = find_translation()
    if translation:
        return translation.translate
    if not install_model:
        raise SystemExit(f"Argos model {from_code}->{to_code} is not installed")

    argostranslate.package.update_package_index()
    package = next(
        (
            pkg
            for pkg in argostranslate.package.get_available_packages()
            if pkg.from_code == from_code and pkg.to_code == to_code
        ),
        None,
    )
    if package is None:
        raise SystemExit(f"No Argos package found for {from_code}->{to_code}")
    argostranslate.package.install_from_path(package.download())
    translation = find_translation()
    if not translation:
        raise SystemExit(f"Installed Argos package but translation {from_code}->{to_code} is still unavailable")
    return translation.translate


def sample_indices(lines: list[str]) -> list[int]:
    nonempty = [idx for idx, line in enumerate(lines) if line.strip()]
    if not nonempty:
        return []
    picks = {nonempty[0], nonempty[len(nonempty) // 2], nonempty[-1]}
    return sorted(picks)


def cmd_translate(args: argparse.Namespace) -> int:
    if not args.input.exists():
        raise SystemExit(f"Missing input transcript: {args.input}")
    lines = args.input.read_text(encoding="utf-8").splitlines()
    out_path = args.out or make_out_path(args.input, args.to_code)
    cache_path = args.cache or args.input.with_name(f"translation_cache_{args.from_code}_{args.to_code}_argos.json")
    report_path = args.report or out_path.with_suffix(".report.json")
    keep_terms = list(DEFAULT_KEEP_TERMS) + list(args.keep_term or [])
    replacements = list(DEFAULT_REPLACEMENTS)
    for item in args.replace or []:
        if "=" not in item:
            raise SystemExit(f"--replace expects OLD=NEW, got: {item}")
        old, new = item.split("=", 1)
        replacements.append((old, new))

    cache = load_cache(cache_path)
    if args.engine == "identity":
        translate = lambda text: text
    else:
        translate = load_argos_translator(args.from_code, args.to_code, not args.no_install)

    out_lines: list[str] = []
    stats = {"translated": 0, "cache_hits": 0, "empty": 0, "unchanged": 0}
    for idx, line in enumerate(lines, 1):
        prefix, body = parse_line(line, args.require_timestamp)
        if not body.strip():
            out_lines.append(prefix + body)
            stats["empty"] += 1
        elif body in cache:
            out_lines.append(prefix + cache[body])
            stats["cache_hits"] += 1
        else:
            protected, mapping = protect_terms(body, keep_terms)
            translated = translate(protected)
            translated = restore_terms(translated, mapping)
            translated = apply_replacements(translated, replacements)
            translated = translated.strip()
            if translated == body:
                stats["unchanged"] += 1
            cache[body] = translated
            out_lines.append(prefix + translated)
            stats["translated"] += 1
        if args.flush_every and idx % args.flush_every == 0:
            flush_outputs(out_path, cache_path, out_lines, cache)
            print(f"translated {idx}/{len(lines)} lines", file=sys.stderr, flush=True)

    if len(out_lines) != len(lines):
        raise SystemExit(f"Line count mismatch: input={len(lines)} output={len(out_lines)}")
    flush_outputs(out_path, cache_path, out_lines, cache)

    samples = []
    for idx in sample_indices(lines):
        samples.append({"line": idx + 1, "source": lines[idx], "target": out_lines[idx]})
    report = {
        "input": str(args.input),
        "output": str(out_path),
        "cache": str(cache_path),
        "engine": args.engine,
        "from_code": args.from_code,
        "to_code": args.to_code,
        "line_count": len(lines),
        "stats": stats,
        "samples": samples,
    }
    write_json(report_path, report)
    print(json.dumps({"output": str(out_path), "lines": len(lines), "report": str(report_path), **stats}, ensure_ascii=False))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--cache", type=Path)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--from-code", default="en")
    parser.add_argument("--to-code", default="zh")
    parser.add_argument("--engine", choices=("argos", "identity"), default="argos")
    parser.add_argument("--no-install", action="store_true", help="Fail if the Argos language package is not already installed.")
    parser.add_argument("--require-timestamp", action="store_true")
    parser.add_argument("--flush-every", type=int, default=100)
    parser.add_argument("--keep-term", action="append")
    parser.add_argument("--replace", action="append", help="Post-process replacement in OLD=NEW form; can be repeated.")
    parser.set_defaults(func=cmd_translate)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
