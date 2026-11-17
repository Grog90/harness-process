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

    def test_client_same_origin_path(self) -> None:
        client = self._read("client.js")
        self.assertIn("'/harness-panel/metrics.json'", client)
        self.assertNotIn("DEFAULT_URL = 'http://127.0.0.1:8765", client)

    def test_host_proxy_route(self) -> None:
        host = self._read("index.js")
        self.assertIn("ctx.webServer.register", host)
        self.assertIn("/harness-panel/metrics.json", host)
        self.assertIn("HARNESS_BOARD_URL", host)
        self.assertIn("AbortSignal.timeout", host)

    def test_client_reads_attempts_and_active_states(self) -> None:
        client = self._read("client.js")
        self.assertIn("attempts.rework_rate", client)
        self.assertIn("VERIFY_BLOCKED", client)
        self.assertIn(".sort(", client)
        self.assertIn("15000", client)

    def test_server_sends_cors(self) -> None:
        with open(os.path.join(ROOT, "harness", "metrics.py"), encoding="utf-8") as fh:
            source = fh.read()
        self.assertIn("Access-Control-Allow-Origin", source)

    def test_offline_state_and_icon(self) -> None:
        client = self._read("client.js")
        self.assertIn("доска offline", client)
        self.assertTrue(os.path.isfile(os.path.join(PANEL, "icon.svg")))
        self.assertTrue(os.path.isfile(os.path.join(PANEL, "docs", "DETAIL.md")))


if __name__ == "__main__":
    unittest.main(verbosity=2)
