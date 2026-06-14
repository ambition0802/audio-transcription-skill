from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import diarize_pyannote_merge as diarize_script  # noqa: E402


class DiarizePyannoteMergeTests(unittest.TestCase):
    def test_parse_anchor(self) -> None:
        self.assertEqual(diarize_script.parse_anchor("1.5,3"), (1.5, 3.0))
        with self.assertRaises(Exception):
            diarize_script.parse_anchor("3,1")

    def test_load_token_prefers_environment(self) -> None:
        with patch.dict(os.environ, {"HF_TOKEN": "env-token"}, clear=False):
            self.assertEqual(diarize_script.load_token(), "env-token")

    def test_load_token_reads_explicit_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            token_file = Path(tmp) / "hf_token.txt"
            token_file.write_text("file-token\n", encoding="utf-8")
            with patch.dict(os.environ, {"HF_TOKEN": "", "HUGGINGFACE_TOKEN": ""}, clear=False):
                self.assertEqual(diarize_script.load_token(token_file), "file-token")

    def test_load_token_reads_shell_rc_without_printing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            (home / ".zshrc").write_text("export HF_TOKEN=rc-token\n", encoding="utf-8")
            with patch.dict(os.environ, {"HF_TOKEN": "", "HUGGINGFACE_TOKEN": ""}, clear=False):
                with patch("pathlib.Path.home", return_value=home):
                    self.assertEqual(diarize_script.load_token(), "rc-token")


if __name__ == "__main__":
    unittest.main()
