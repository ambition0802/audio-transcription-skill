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

    def test_apply_corrections_annotates_and_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            transcript = root / "transcript.json"
            corrections = root / "transcript_corrections.json"
            corrected = root / "transcript_corrected.json"
            report = root / "correction_report.json"
            transcript.write_text(
                json.dumps(
                    {
                        "metadata": {"title": "测试逐字稿"},
                        "segments": [
                            {"start": 0, "end": 10, "text": "我们讨论韩武G和马拉西西。"},
                            {"start": 10, "end": 20, "text": "另一个韩武G保持原样。"},
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            corrections.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "corrections": [
                            {
                                "id": "company",
                                "original": "韩武G",
                                "replacement": "寒武纪",
                                "status": "probable",
                                "expected_matches": 1,
                                "start": 0,
                                "end": 10,
                                "reason": "上下文为国产 AI 芯片公司",
                            },
                            {
                                "id": "component",
                                "original": "马拉西西",
                                "replacement": "MLCC",
                                "status": "confirmed",
                                "expected_matches": 1,
                            },
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            self.run_cli([
                "apply-corrections",
                str(transcript),
                "--corrections",
                str(corrections),
                "--out",
                str(corrected),
                "--report",
                str(report),
            ])
            data = json.loads(corrected.read_text(encoding="utf-8"))
            self.assertEqual(
                data["segments"][0]["text"],
                "我们讨论寒武纪〔疑似校订；原转写：韩武G〕和MLCC〔校订；原转写：马拉西西〕。",
            )
            self.assertEqual(data["segments"][1]["text"], "另一个韩武G保持原样。")
            self.assertTrue(json.loads(report.read_text(encoding="utf-8"))["ok"])

            corrected_twice = root / "transcript_corrected_twice.json"
            self.run_cli([
                "apply-corrections",
                str(corrected),
                "--corrections",
                str(corrections),
                "--out",
                str(corrected_twice),
                "--report",
                str(report),
            ])
            second_data = json.loads(corrected_twice.read_text(encoding="utf-8"))
            self.assertEqual(second_data["segments"], data["segments"])
            second_report = json.loads(report.read_text(encoding="utf-8"))
            self.assertEqual(second_report["already_applied_matches"], 2)

            deliverables = root / "deliverables"
            self.run_cli([
                "emit-deliverables",
                str(corrected_twice),
                "--out-dir",
                str(deliverables),
            ])
            for name in (
                "transcript_clean.json",
                "transcript_timestamped.txt",
                "transcript_no_timestamps.md",
                "transcript_no_timestamps.txt",
                "transcript_clean.srt",
                "transcript_clean.vtt",
            ):
                content = (deliverables / name).read_text(encoding="utf-8")
                self.assertIn("寒武纪", content, name)
                self.assertIn("原转写", content, name)

    def test_apply_corrections_rejects_count_mismatch_without_output(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            transcript = root / "transcript.json"
            corrections = root / "transcript_corrections.json"
            corrected = root / "transcript_corrected.json"
            report = root / "correction_report.json"
            transcript.write_text(
                json.dumps(
                    {"segments": [{"start": 0, "end": 10, "text": "韩武G"}]},
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            corrections.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "corrections": [
                            {
                                "original": "韩武G",
                                "replacement": "寒武纪",
                                "status": "probable",
                                "expected_matches": 2,
                            }
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            with contextlib.redirect_stdout(io.StringIO()):
                code = tp.main([
                    "apply-corrections",
                    str(transcript),
                    "--corrections",
                    str(corrections),
                    "--out",
                    str(corrected),
                    "--report",
                    str(report),
                ])
            self.assertEqual(code, 2)
            self.assertFalse(corrected.exists())
            report_data = json.loads(report.read_text(encoding="utf-8"))
            self.assertFalse(report_data["ok"])
            self.assertEqual(report_data["error_count"], 1)


if __name__ == "__main__":
    unittest.main()
