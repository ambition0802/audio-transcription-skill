from __future__ import annotations

import contextlib
import io
import json
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import ensure_runtime_dependencies as deps  # noqa: E402


def write_executable(directory: Path, name: str) -> None:
    path = directory / name
    path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


class EnsureRuntimeDependenciesTests(unittest.TestCase):
    def run_cli(self, args: list[str]) -> tuple[int, str]:
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = deps.main(args)
        return code, out.getvalue()

    def test_check_only_succeeds_with_fake_transcribe_tools(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bin_dir = Path(tmp)
            for command in ["ffmpeg", "ffprobe", "uv", "uvx", "mlx_whisper"]:
                write_executable(bin_dir, command)
            report = bin_dir / "dependency_report.json"
            with (
                mock.patch.dict(os.environ, {"PATH": str(bin_dir)}, clear=True),
                mock.patch.object(deps, "common_bin_paths", return_value=[]),
            ):
                code, output = self.run_cli(["--profile", "transcribe", "--check-only", "--report", str(report)])

            self.assertEqual(code, 0, output)
            data = json.loads(report.read_text(encoding="utf-8"))
            self.assertTrue(data["ok"])
            self.assertEqual([item["status"] for item in data["results"]], ["ok", "ok", "ok"])

    def test_dry_run_plans_missing_transcribe_installs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bin_dir = Path(tmp)
            write_executable(bin_dir, "brew")
            report = bin_dir / "dependency_report.json"
            with (
                mock.patch.dict(os.environ, {"PATH": str(bin_dir)}, clear=True),
                mock.patch.object(deps, "common_bin_paths", return_value=[]),
            ):
                code, output = self.run_cli(["--profile", "transcribe", "--install", "--dry-run", "--report", str(report)])

            self.assertEqual(code, 0, output)
            data = json.loads(report.read_text(encoding="utf-8"))
            statuses = {item["name"]: item["status"] for item in data["results"]}
            self.assertEqual(statuses["ffmpeg"], "planned")
            self.assertEqual(statuses["uv"], "planned")
            self.assertEqual(statuses["mlx-whisper"], "planned")

    def test_bilibili_profile_includes_yt_dlp(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bin_dir = Path(tmp)
            for command in ["ffmpeg", "ffprobe", "uv", "uvx", "mlx_whisper"]:
                write_executable(bin_dir, command)
            report = bin_dir / "dependency_report.json"
            with (
                mock.patch.dict(os.environ, {"PATH": str(bin_dir)}, clear=True),
                mock.patch.object(deps, "common_bin_paths", return_value=[]),
            ):
                code, output = self.run_cli(["--profile", "bilibili", "--install", "--dry-run", "--report", str(report)])

            self.assertEqual(code, 0, output)
            data = json.loads(report.read_text(encoding="utf-8"))
            yt_dlp = [item for item in data["results"] if item["name"] == "yt-dlp"]
            self.assertEqual(yt_dlp[0]["status"], "planned")


if __name__ == "__main__":
    unittest.main()
