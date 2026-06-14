#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable


OK_STATUSES = {"ok", "installed", "planned", "skipped"}


@dataclass
class CheckResult:
    name: str
    status: str
    message: str
    paths: dict[str, str] = field(default_factory=dict)
    install_commands: list[list[str]] = field(default_factory=list)


class DependencyContext:
    def __init__(self, *, install: bool, dry_run: bool, verbose: bool):
        self.install = install
        self.dry_run = dry_run
        self.verbose = verbose
        self.results: list[CheckResult] = []
        self.planned_commands: set[str] = set()
        self.extra_path = os.pathsep.join(str(path) for path in common_bin_paths())
        self.env = os.environ.copy()
        existing_path = self.env.get("PATH", "")
        self.env["PATH"] = os.pathsep.join(part for part in [existing_path, self.extra_path] if part)

    def which(self, command: str) -> str | None:
        return shutil.which(command, path=self.env.get("PATH"))

    def add_result(self, result: CheckResult) -> None:
        self.results.append(result)
        if self.verbose:
            print(f"{result.name}: {result.status} - {result.message}")

    def run(self, command: list[str]) -> subprocess.CompletedProcess[str]:
        command_text = shell_join(command)
        if self.dry_run:
            self.planned_commands.add(command_text)
            return subprocess.CompletedProcess(command, 0, "", "")
        if self.verbose:
            print(f"+ {command_text}")
        return subprocess.run(
            command,
            check=False,
            env=self.env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

    def command_planned(self, command: str) -> bool:
        return command in self.planned_commands


def common_bin_paths() -> list[Path]:
    home = Path.home()
    return [
        home / ".local" / "bin",
        home / "Library" / "Python" / f"{sys.version_info.major}.{sys.version_info.minor}" / "bin",
        Path("/opt/homebrew/bin"),
        Path("/usr/local/bin"),
    ]


def shell_join(command: list[str]) -> str:
    return " ".join(sh_quote(part) for part in command)


def sh_quote(value: str) -> str:
    if not value:
        return "''"
    safe = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_+-=/:.,@%"
    if all(char in safe for char in value):
        return value
    return "'" + value.replace("'", "'\"'\"'") + "'"


def command_paths(ctx: DependencyContext, commands: list[str]) -> dict[str, str]:
    paths = {}
    for command in commands:
        path = ctx.which(command)
        if path:
            paths[command] = path
    return paths


def sanitize_local_path(value: str) -> str:
    home = str(Path.home())
    if home and value == home:
        return "$HOME"
    if home and value.startswith(home + os.sep):
        return "$HOME" + value[len(home):]
    return value


def sanitize_for_report(value):
    if isinstance(value, str):
        return sanitize_local_path(value)
    if isinstance(value, list):
        return [sanitize_for_report(item) for item in value]
    if isinstance(value, dict):
        return {key: sanitize_for_report(item) for key, item in value.items()}
    return value


def result_to_report(result: CheckResult) -> dict[str, object]:
    return {
        "name": result.name,
        "status": result.status,
        "message": result.message,
        "paths": sanitize_for_report(result.paths),
        "install_commands": sanitize_for_report(result.install_commands),
    }


def ensure_ffmpeg(ctx: DependencyContext) -> None:
    commands = ["ffmpeg", "ffprobe"]
    paths = command_paths(ctx, commands)
    if len(paths) == len(commands):
        ctx.add_result(CheckResult("ffmpeg", "ok", "ffmpeg and ffprobe are available.", paths))
        return

    missing = [command for command in commands if command not in paths]
    if not ctx.install:
        ctx.add_result(CheckResult("ffmpeg", "missing", f"Missing commands: {', '.join(missing)}.", paths))
        return

    brew = ctx.which("brew")
    if not brew:
        ctx.add_result(
            CheckResult(
                "ffmpeg",
                "failed",
                "ffmpeg is missing and Homebrew was not found. Install Homebrew or provide ffmpeg/ffprobe on PATH.",
                paths,
            )
        )
        return

    install_command = [brew, "install", "ffmpeg"]
    if ctx.dry_run:
        ctx.run(install_command)
        planned_paths = paths.copy()
        for command in missing:
            planned_paths[command] = "<planned>"
        ctx.add_result(
            CheckResult(
                "ffmpeg",
                "planned",
                "Would install ffmpeg with Homebrew.",
                planned_paths,
                [install_command],
            )
        )
        return

    completed = ctx.run(install_command)
    paths = command_paths(ctx, commands)
    if completed.returncode == 0 and len(paths) == len(commands):
        ctx.add_result(CheckResult("ffmpeg", "installed", "Installed ffmpeg with Homebrew.", paths, [install_command]))
    else:
        message = "Failed to install ffmpeg with Homebrew."
        if completed.stderr.strip():
            message += f" stderr: {completed.stderr.strip()[-500:]}"
        ctx.add_result(CheckResult("ffmpeg", "failed", message, paths, [install_command]))


def ensure_uv(ctx: DependencyContext) -> None:
    commands = ["uv", "uvx"]
    paths = command_paths(ctx, commands)
    if len(paths) == len(commands):
        ctx.add_result(CheckResult("uv", "ok", "uv and uvx are available.", paths))
        return

    missing = [command for command in commands if command not in paths]
    if not ctx.install:
        ctx.add_result(CheckResult("uv", "missing", f"Missing commands: {', '.join(missing)}.", paths))
        return

    install_command = [sys.executable, "-m", "pip", "install", "--user", "uv"]
    if ctx.dry_run:
        ctx.run(install_command)
        planned_paths = paths.copy()
        for command in missing:
            planned_paths[command] = "<planned>"
        ctx.add_result(
            CheckResult("uv", "planned", "Would install uv with pip --user.", planned_paths, [install_command])
        )
        return

    completed = ctx.run(install_command)
    paths = command_paths(ctx, commands)
    if completed.returncode == 0 and len(paths) == len(commands):
        ctx.add_result(CheckResult("uv", "installed", "Installed uv with pip --user.", paths, [install_command]))
    else:
        message = "Failed to install uv with pip --user."
        if completed.stderr.strip():
            message += f" stderr: {completed.stderr.strip()[-500:]}"
        ctx.add_result(CheckResult("uv", "failed", message, paths, [install_command]))


def ensure_uv_tool(ctx: DependencyContext, *, command: str, package: str, name: str) -> None:
    path = ctx.which(command)
    if path:
        ctx.add_result(CheckResult(name, "ok", f"{command} is available.", {command: path}))
        return

    if not ctx.install:
        ctx.add_result(CheckResult(name, "missing", f"{command} is missing.", {}))
        return

    uv = ctx.which("uv")
    uv_planned = any(result.name == "uv" and result.status == "planned" for result in ctx.results)
    if not uv and not uv_planned:
        ctx.add_result(CheckResult(name, "failed", f"Cannot install {package}: uv is missing.", {}))
        return

    install_command = [uv or "uv", "tool", "install", package]
    if ctx.dry_run:
        ctx.run(install_command)
        ctx.add_result(
            CheckResult(
                name,
                "planned",
                f"Would install {package} with uv tool install.",
                {command: "<planned>"},
                [install_command],
            )
        )
        return

    completed = ctx.run(install_command)
    path = ctx.which(command)
    if completed.returncode == 0 and path:
        ctx.add_result(
            CheckResult(name, "installed", f"Installed {package} with uv tool install.", {command: path}, [install_command])
        )
    else:
        message = f"Failed to install {package} with uv tool install."
        if completed.stderr.strip():
            message += f" stderr: {completed.stderr.strip()[-500:]}"
        ctx.add_result(CheckResult(name, "failed", message, {}, [install_command]))


def ensure_mlx_whisper(ctx: DependencyContext) -> None:
    ensure_uv_tool(ctx, command="mlx_whisper", package="mlx-whisper", name="mlx-whisper")


def ensure_yt_dlp(ctx: DependencyContext) -> None:
    ensure_uv_tool(ctx, command="yt-dlp", package="yt-dlp", name="yt-dlp")


def ensure_hf_token(ctx: DependencyContext) -> None:
    if os.environ.get("HF_TOKEN") or os.environ.get("HUGGINGFACE_TOKEN"):
        ctx.add_result(CheckResult("hf-token", "ok", "Hugging Face token is present in the environment."))
    else:
        ctx.add_result(
            CheckResult(
                "hf-token",
                "skipped",
                "No HF_TOKEN/HUGGINGFACE_TOKEN in environment; diarization script may still read an explicit token file or shell rc.",
            )
        )


PROFILE_CHECKS: dict[str, list[Callable[[DependencyContext], None]]] = {
    "base": [ensure_ffmpeg, ensure_uv],
    "transcribe": [
        ensure_ffmpeg,
        ensure_uv,
        ensure_mlx_whisper,
    ],
    "bilibili": [
        ensure_ffmpeg,
        ensure_uv,
        ensure_yt_dlp,
        ensure_mlx_whisper,
    ],
    "translate": [ensure_uv],
    "diarize": [ensure_ffmpeg, ensure_uv, ensure_hf_token],
}


def selected_checks(profiles: list[str]) -> list[Callable[[DependencyContext], None]]:
    checks: list[Callable[[DependencyContext], None]] = []
    seen: set[str] = set()
    expanded = ["base", "transcribe", "bilibili", "translate", "diarize"] if "all" in profiles else profiles
    for profile in expanded:
        for check in PROFILE_CHECKS[profile]:
            key = check.__name__
            if key not in seen:
                checks.append(check)
                seen.add(key)
    return checks


def write_report(path: Path, ctx: DependencyContext, profiles: list[str], ok: bool) -> None:
    payload = {
        "ok": ok,
        "profiles": profiles,
        "platform": {
            "system": platform.system(),
            "machine": platform.machine(),
            "python": sys.version.split()[0],
        },
        "path_hints": [str(path) for path in common_bin_paths()],
        "results": [result_to_report(result) for result in ctx.results],
    }
    payload["path_hints"] = sanitize_for_report(payload["path_hints"])
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def print_summary(ctx: DependencyContext, ok: bool) -> None:
    print("Runtime dependency preflight:", "ok" if ok else "failed")
    for result in ctx.results:
        location = ""
        if result.paths:
            rendered = ", ".join(f"{key}={sanitize_local_path(value)}" for key, value in sorted(result.paths.items()))
            location = f" ({rendered})"
        print(f"- {result.name}: {result.status}{location}")
        if result.status not in OK_STATUSES:
            print(f"  {result.message}")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Check and optionally install runtime dependencies for the audio-transcription skill."
    )
    parser.add_argument(
        "--profile",
        action="append",
        choices=["base", "transcribe", "bilibili", "translate", "diarize", "all"],
        default=None,
        help="Dependency profile to check. Repeatable. Defaults to transcribe.",
    )
    parser.add_argument("--install", action="store_true", help="Install missing dependencies when possible.")
    parser.add_argument("--check-only", action="store_true", help="Only check dependencies; do not install.")
    parser.add_argument("--dry-run", action="store_true", help="Print/report planned installs without running them.")
    parser.add_argument("--report", type=Path, help="Write a JSON report to this path.")
    parser.add_argument("--verbose", action="store_true", help="Print commands and per-step messages.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    profiles = args.profile or ["transcribe"]
    install = args.install and not args.check_only
    ctx = DependencyContext(install=install, dry_run=args.dry_run, verbose=args.verbose)

    for check in selected_checks(profiles):
        check(ctx)

    ok = all(result.status in OK_STATUSES for result in ctx.results)
    if args.report:
        write_report(args.report, ctx, profiles, ok)
    print_summary(ctx, ok)
    if args.report:
        print(f"Report: {args.report}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
