"""Статика client-бандла панели harness."""

from __future__ import annotations

import json
import os
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PANEL = os.path.join(ROOT, "plugins", "harness-panel")


class PanelPluginTests(unittest.TestCase):
    def _read(self, rel: str) -> str:
        with open(os.path.join(PANEL, rel), encoding="utf-8") as fh:
            return fh.read()

    def test_manifest_declares_client(self) -> None:
        data = json.loads(self._read("package.json"))
        self.assertEqual(data["name"], "@local/harness-panel")
        self.assertIn("client", data["dsh"])
        self.assertEqual(data["dsh"]["client"]["platform"], "web")
        self.assertIn("./client", data["exports"])

    def test_patch_inserts_unique_id(self) -> None:
        patch = self._read("cordis.patch.yml")
        self.assertIn("harness-panel", patch)
        self.assertIn("@local/harness-panel", patch)

    def test_client_registers_slot_and_reads_metrics(self) -> None:
        client = self._read("client.js")
        self.assertIn("conversation.composer.dock", client)
        self.assertIn("harness-panel", client)
        self.assertIn("harnessBoardUrl", client)
        self.assertIn("/metrics.json", client)
        for forbidden in ("accept", "release", "commit"):
            self.assertNotIn("ctx." + forbidden, client)

    def test_offline_state_and_icon(self) -> None:
        client = self._read("client.js")
        self.assertIn("доска offline", client)
        self.assertTrue(os.path.isfile(os.path.join(PANEL, "icon.svg")))
        self.assertTrue(os.path.isfile(os.path.join(PANEL, "docs", "DETAIL.md")))


if __name__ == "__main__":
    unittest.main(verbosity=2)
