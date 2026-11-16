"""OTLP/HTTP JSON: форма ресурса, метрики и числовые типы."""

from __future__ import annotations

import json
import os
import shutil
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer

from harness import otel, tmpdir

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _data() -> dict:
    return {
        "root": ROOT,
        "summary": {
            "changes": 2, "changes_accepted": 1, "changes_archived": 0,
            "tasks": 3, "tasks_active": 1, "tasks_escalated": 0,
            "review_records": 4, "evidence_manifests": 1,
            "open_findings": 0, "unowned_findings": 0, "cost_total": 1.5,
        },
        "attempts": {"rework_rate": 0.5},
        "verification": {"reports": 1, "ok": 1, "zero_or_failed": 0},
    }


class OtelTest(unittest.TestCase):
    def test_payload_shape_and_names(self) -> None:
        payload = otel.build(_data(), service_name="harness-test", service_version="9.9",
                             git_sha="a" * 40, host="ci")
        resource = payload["resourceMetrics"][0]["resource"]["attributes"]
        attrs = {item["key"]: item["value"] for item in resource}
        self.assertEqual(attrs["service.name"], {"stringValue": "harness-test"})
        self.assertEqual(attrs["service.version"], {"stringValue": "9.9"})
        self.assertEqual(attrs["vcs.revision"], {"stringValue": "a" * 40})
        scope = payload["resourceMetrics"][0]["scopeMetrics"][0]
        names = {m["name"] for m in scope["metrics"]}
        self.assertIn("harness.changes", names)
        self.assertIn("harness.rework.rate", names)
        self.assertIn("harness.verification.ok", names)
        changes = next(m for m in scope["metrics"] if m["name"] == "harness.changes")
        self.assertEqual(changes["gauge"]["dataPoints"][0]["asInt"], "2")
        rate = next(m for m in scope["metrics"] if m["name"] == "harness.rework.rate")
        self.assertEqual(rate["gauge"]["dataPoints"][0]["asDouble"], 0.5)

    def test_send_posts_json_and_reads_status(self) -> None:
        captured: dict = {}

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802
                length = int(self.headers.get("Content-Length") or 0)
                captured["body"] = self.rfile.read(length)
                captured["type"] = self.headers.get("Content-Type")
                self.send_response(200)
                self.end_headers()

            def log_message(self, *args) -> None:
                return

        server = HTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            payload = otel.build(_data(), service_name="ci")
            result = otel.send(payload, f"http://127.0.0.1:{server.server_address[1]}/v1/metrics")
            self.assertEqual(result["status"], 200)
            self.assertEqual(captured["type"], "application/json")
            self.assertIn("resourceMetrics", json.loads(captured["body"].decode("utf-8")))
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

    def test_write_is_valid_json(self) -> None:
        root = tmpdir.scratch(ROOT, "harness-otel-")
        try:
            path = otel.write(otel.build(_data()), os.path.join(root, "otlp.json"))
            with open(path, encoding="utf-8") as fh:
                loaded = json.load(fh)
            self.assertIn("resourceMetrics", loaded)
        finally:
            shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
