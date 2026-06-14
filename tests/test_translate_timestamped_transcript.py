from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import translate_timestamped_transcript as translate_script  # noqa: E402


class TranslateTimestampedTranscriptTests(unittest.TestCase):
    def test_identity_engine_preserves_timestamped_lines(self) -> None:
        source_text = (
            "[00:00:00.000 --> 00:00:02.000] Hello Tesla.\n"
            "[00:00:02.000 --> 00:00:04.000] SpaceX and OpenAI.\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            src = tmp_path / "transcript_timestamped.txt"
            out = tmp_path / "transcript_timestamped_zh.txt"
            cache = tmp_path / "cache.json"
            report = tmp_path / "report.json"
            src.write_text(source_text, encoding="utf-8")
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                code = translate_script.main([
                    str(src),
                    "--engine",
                    "identity",
                    "--out",
                    str(out),
                    "--cache",
                    str(cache),
                    "--report",
                    str(report),
                    "--require-timestamp",
                    "--flush-every",
                    "0",
                ])
            self.assertEqual(code, 0)
            self.assertEqual(out.read_text(encoding="utf-8"), source_text)
            report_data = json.loads(report.read_text(encoding="utf-8"))
            self.assertEqual(report_data["line_count"], 2)
            self.assertEqual(report_data["stats"]["unchanged"], 2)


if __name__ == "__main__":
    unittest.main()
