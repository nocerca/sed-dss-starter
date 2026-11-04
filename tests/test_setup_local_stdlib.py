"""Runnable with unittest and Python's standard library only."""

import tempfile
import unittest
from pathlib import Path

from scripts.setup_local import prepare_settings


class SetupTests(unittest.TestCase):
    def test_preserves_settings_and_existing_keys(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / ".env"
            target.write_text("LLM_PROVIDER=none\nALLOW_EXTERNAL_LLM=false\nOPENCLAW_GATEWAY_TOKEN=existing\n", encoding="utf-8")
            self.assertEqual(prepare_settings(root), ["MODEL_BRIDGE_API_KEY", "IDEMPOTENCY_SECRET"])
            content = target.read_text(encoding="utf-8")
            self.assertIn("OPENCLAW_GATEWAY_TOKEN=existing\n", content)
            self.assertIn("ALLOW_EXTERNAL_LLM=false\n", content)
            self.assertEqual(prepare_settings(root), [])
            self.assertEqual(target.read_text(encoding="utf-8"), content)

    def test_creates_random_independent_keys_from_template(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / ".env.example").write_text("OPENCLAW_GATEWAY_TOKEN=\nMODEL_BRIDGE_API_KEY=\n", encoding="utf-8")
            self.assertEqual(len(prepare_settings(root)), 3)
            values = dict(line.split("=", 1) for line in (root / ".env").read_text().splitlines())
            self.assertGreaterEqual(len(values["OPENCLAW_GATEWAY_TOKEN"]), 32)
            self.assertNotEqual(values["OPENCLAW_GATEWAY_TOKEN"], values["MODEL_BRIDGE_API_KEY"])


if __name__ == "__main__":
    unittest.main()
