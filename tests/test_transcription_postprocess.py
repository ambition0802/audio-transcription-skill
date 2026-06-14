from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures"
sys.path.insert(0, str(ROOT / "scripts"))

import transcription_postprocess as tp  # noqa: E402


class TranscriptionPostprocessTests(unittest.TestCase):
    def run_cli(self, args: list[str]) -> str:
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = tp.main(args)
        self.assertEqual(code, 0)
        return out.getvalue()

    def test_select_bilibili_audio_prefers_audio_track(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "audio_url.txt"
            report = Path(tmp) / "audio_selection.json"
            self.run_cli([
                "select-bilibili-audio",
                str(FIXTURES / "media_assets.json"),
                "--out",
                str(out),
                "--report",
                str(report),
            ])
            self.assertIn("-30216.m4s", out.read_text(encoding="utf-8"))
            data = json.loads(report.read_text(encoding="utf-8"))
            self.assertEqual(data["chosen"]["name"], "audio-30216.m4s")

    def test_extract_subtitle_url_from_json_response(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "subtitle_body_url.txt"
            self.run_cli([
                "extract-subtitle-url",
                str(FIXTURES / "sample_bilibili_subtitle_response.json"),
                "--out",
                str(out),
            ])
            url = out.read_text(encoding="utf-8").strip()
            self.assertTrue(url.startswith("https://subtitle.bilibili.com/"))
            self.assertIn("auth_key=", url)

    def test_inspect_subtitle_reports_complete_coverage(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            report = Path(tmp) / "subtitle_report.json"
            self.run_cli([
                "inspect-subtitle",
                str(FIXTURES / "sample_subtitle.json"),
                "--duration",
                "10",
                "--report",
                str(report),
                "--fail-on-incomplete",
            ])
            data = json.loads(report.read_text(encoding="utf-8"))
            self.assertTrue(data["usable_as_primary"])
            self.assertGreaterEqual(data["coverage_ratio"], 0.95)

    def test_emit_deliverables_and_package(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out_dir = Path(tmp)
            self.run_cli([
                "emit-deliverables",
                str(FIXTURES / "sample_segments.json"),
                "--out-dir",
                str(out_dir),
                "--source-kind",
                "fixture",
            ])
            self.assertTrue((out_dir / "transcript_clean.srt").exists())
            self.assertTrue((out_dir / "transcript_no_timestamps.md").exists())

            package = out_dir / "transcript_package.zip"
            report = out_dir / "package_report.json"
            self.run_cli([
                "verify-package",
                "--base-dir",
                str(out_dir),
                "--include",
                "transcript_clean.json",
                "transcript_clean.srt",
                "transcript_no_timestamps.md",
                "--out",
                str(package),
                "--report",
                str(report),
                "--require",
            ])
            self.assertTrue(package.exists())
            with zipfile.ZipFile(package) as zf:
                self.assertIn("transcript_clean.json", zf.namelist())

    def test_merge_slice_replaces_bounded_region(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "merged.json"
            self.run_cli([
                "merge-slice",
                "--base",
                str(FIXTURES / "sample_segments.json"),
                "--slice",
                str(FIXTURES / "sample_slice.json"),
                "--start",
                "2",
                "--end",
                "4",
                "--out",
                str(out),
            ])
            data = json.loads(out.read_text(encoding="utf-8"))
            texts = [segment["text"] for segment in data["segments"]]
            self.assertIn("这是修复后的中间段。", texts)
            self.assertNotIn("这里需要替换。", texts)


if __name__ == "__main__":
    unittest.main()
