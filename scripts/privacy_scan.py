#!/usr/bin/env python3
"""Small secret/privacy scanner for this public skill repository."""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path


BILI_COOKIE_NAMES = "SESS" + "DATA|bili_" + "jct|Dede" + "UserID"
AUTH_HEADER = "Author" + "ization"
COOKIE_HEADER = "Cook" + "ie"

PATTERNS = {
    "openai_key": re.compile(r"sk-[A-Za-z0-9_-]{20,}"),
    "github_pat": re.compile(r"github_pat_[A-Za-z0-9_]{20,}"),
    "github_token": re.compile(r"gh[pousr]_[A-Za-z0-9_]{20,}"),
    "hf_token": re.compile(r"hf_[A-Za-z0-9]{20,}"),
    "aws_key": re.compile(r"AKIA[0-9A-Z]{16}"),
    "google_key": re.compile(r"AIza[0-9A-Za-z_-]{35}"),
    "private_key": re.compile(r"-----BEGIN (?:RSA|OPENSSH|EC|DSA|PRIVATE) KEY-----"),
    "bilibili_cookie": re.compile(rf"(?:{BILI_COOKIE_NAMES})=[^;\s]+"),
    "bearer_header": re.compile(rf"{AUTH_HEADER}:\s*Bearer\s+\S+", re.I),
    "cookie_header": re.compile(rf"{COOKIE_HEADER}:\s*\S+=\S+", re.I),
    "local_user_path": re.compile(r"/Users/[^/\s)]+"),
}

SKIP_DIRS = {".git", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache"}
SKIP_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".zip", ".m4a", ".m4s", ".mp3", ".mp4", ".wav"}


def iter_files(root: Path):
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        if path.match("scripts/privacy_scan.py"):
            continue
        if path.suffix.lower() in SKIP_SUFFIXES:
            continue
        yield path


def scan(root: Path) -> list[tuple[Path, int, str]]:
    findings: list[tuple[Path, int, str]] = []
    for path in iter_files(root):
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except UnicodeDecodeError:
            continue
        for lineno, line in enumerate(lines, 1):
            for name, pattern in PATTERNS.items():
                if pattern.search(line):
                    findings.append((path, lineno, name))
    return findings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", nargs="?", type=Path, default=Path("."))
    args = parser.parse_args(argv)
    findings = scan(args.root)
    if findings:
        for path, lineno, name in findings:
            print(f"{path}:{lineno}: possible {name}", file=sys.stderr)
        return 1
    print("No obvious secrets or private local paths found.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
