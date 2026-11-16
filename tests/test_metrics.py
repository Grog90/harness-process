"""Метрики и доска считаются из состояния, а не из отдельного отчёта."""

from __future__ import annotations

import json
import os
import shutil
import threading
import unittest
import urllib.request

from harness import metrics, tmpdir
from harness.state import Store, new_id

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SHA = "c" * 40


class MetricsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root = tmpdir.scratch(ROOT, "harness-metrics-")
        self.store = Store(os.path.join(self.root, ".harness", "state.db"))
        self.store.create_change("chg-m", owner="<script>alert(1)</script>", spec_ref=SHA,
                                 risk_class="L1")
        self.store.append_change_event(new_id("cevt"), "chg-m", "ready", {}, {"to": "READY"})
        self.store.append_change_event(new_id("cevt"), "chg-m", "accept", {}, {"to": "ACCEPTED"})
        self.store.create_task("T-1", policy_version="1.0.0", change_id="chg-m")
        self.store.create_task("T-2", policy_version="1.0.0", change_id="chg-m")

    def tearDown(self) -> None:
        self.store.close()
        shutil.rmtree(self.root, ignore_errors=True)

    def test_collect_counts_and_lead_time(self) -> None:
        data = metrics.collect(self.store, self.root)
        self.assertEqual(data["summary"]["changes"], 1)
        self.assertEqual(data["summary"]["tasks"], 2)
        self.assertEqual(data["changes"][0]["change_id"], "chg-m")
        self.assertEqual(data["changes"][0]["reviews"], 0)
        self.assertIsNotNone(data["lead_time"]["avg_ready_to_accept_hours"])
        self.assertEqual(data["lead_time"]["samples"], 1)

    def test_verification_file_is_seen(self) -> None:
        base = os.path.join(self.root, "openspec", "changes", "chg-m")
        os.makedirs(base, exist_ok=True)
        report = {
            "change_id": "chg-m", "revision": 1, "spec_ref": SHA,
            "environment": "local", "runner": "unittest",
            "services": [{"name": "repo", "sha": SHA}],
            "results": {
                "baseline": {"command": "ci", "tests_ran": 2, "result": "pass", "failures": []},
                "candidate": {"command": "ci", "tests_ran": 3, "result": "pass", "failures": []},
            },
            "diff": {"new_failures": []},
        }
        with open(os.path.join(base, "verification.json"), "w", encoding="utf-8") as fh:
            json.dump(report, fh)
        data = metrics.collect(self.store, self.root)
        self.assertEqual(data["verification"]["reports"], 1)
        self.assertEqual(data["verification"]["ok"], 1)

    def test_board_escapes_and_names_change(self) -> None:
        data = metrics.board_data(self.store, self.root)
        page = metrics.render_html(data)
        self.assertIn("chg-m", page)
        self.assertNotIn("<script>alert(1)</script>", page)
        self.assertIn("&lt;script&gt;", page)

    def test_markdown_has_queue_and_change(self) -> None:
        text = metrics.render_md(metrics.collect(self.store, self.root))
        self.assertIn("Очередь", text)
        self.assertIn("chg-m", text)

    def test_live_server_serves_fresh_html_and_json(self) -> None:
        server = metrics.make_server(self.store, self.root, port=0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_address[1]}"
        try:
            page = urllib.request.urlopen(base + "/", timeout=10).read().decode("utf-8")
            self.assertIn("chg-m", page)
            self.assertIn('http-equiv="refresh"', page)
            payload = json.loads(urllib.request.urlopen(base + "/metrics.json", timeout=10).read())
            self.assertEqual(payload["summary"]["changes"], 1)
            health = urllib.request.urlopen(base + "/healthz", timeout=10).read().decode()
            self.assertEqual(health, "ok\n")
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)


if __name__ == "__main__":
    unittest.main(verbosity=2)
