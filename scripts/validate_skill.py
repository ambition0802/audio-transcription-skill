#!/usr/bin/env python3
"""Lightweight repository-local validation for a Codex skill folder."""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path


NAME_RE = re.compile(r"^[a-z0-9-]{1,63}$")


def parse_frontmatter(text: str) -> dict[str, str]:
    if not text.startswith("---\n"):
        raise ValueError("SKILL.md must start with YAML frontmatter")
    end = text.find("\n---\n", 4)
    if end == -1:
        raise ValueError("SKILL.md frontmatter must be closed with ---")
    fields: dict[str, str] = {}
    for raw_line in text[4:end].splitlines():
        if not raw_line.strip():
            continue
        if ":" not in raw_line:
            raise ValueError(f"Invalid frontmatter line: {raw_line}")
        key, value = raw_line.split(":", 1)
        value = value.strip().strip('"').strip("'")
        fields[key.strip()] = value
    return fields


def validate_skill(root: Path) -> list[str]:
    errors: list[str] = []
    skill = root / "SKILL.md"
    if not skill.exists():
        return ["Missing SKILL.md"]
    try:
        fields = parse_frontmatter(skill.read_text(encoding="utf-8"))
    except Exception as exc:
        return [str(exc)]
    name = fields.get("name", "")
    description = fields.get("description", "")
    if not name:
        errors.append("Frontmatter is missing name")
    elif not NAME_RE.match(name):
        errors.append("Frontmatter name must use lowercase letters, digits, and hyphens")
    if not description:
        errors.append("Frontmatter is missing description")
    elif len(description) < 40:
        errors.append("Frontmatter description is too short to explain trigger intent")
    for folder in ("scripts", "references"):
        path = root / folder
        if path.exists() and not path.is_dir():
            errors.append(f"{folder} exists but is not a directory")
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", nargs="?", type=Path, default=Path("."))
    args = parser.parse_args(argv)
    errors = validate_skill(args.root)
    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print("Skill is valid.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
