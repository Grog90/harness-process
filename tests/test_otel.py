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


class OtelTracesTest(unittest.TestCase):
    def _events(self) -> list[dict]:
        return [
            {"event_id": "e1", "type": "dispatch", "created_at": 100.0, "result": {"ok": True, "state": "RUNNING"}},
            {"event_id": "e2", "type": "submit", "created_at": 101.0, "result": {"ok": False}},
            {"event_id": "e3", "type": "commit", "created_at": 102.0, "result": {"ok": True}},
        ]

    def test_spans_have_trace_and_status(self) -> None:
        payload = otel.build_traces(self._events(), "T-1", service_name="h")
        spans = payload["resourceSpans"][0]["scopeSpans"][0]["spans"]
        self.assertEqual(len(spans), 3)
        self.assertEqual(len({s["traceId"] for s in spans}), 1)
        self.assertEqual(len(spans[0]["traceId"]), 32)
        self.assertEqual(len(spans[0]["spanId"]), 16)
        self.assertEqual(spans[0]["status"]["code"], 1)
        self.assertEqual(spans[1]["status"]["code"], 2)
        self.assertEqual(spans[0]["startTimeUnixNano"], "100000000000")
        self.assertEqual(spans[0]["endTimeUnixNano"], "101000000000")

    def test_empty_events_still_shape(self) -> None:
        payload = otel.build_traces([], "T-2")
        self.assertEqual(payload["resourceSpans"][0]["scopeSpans"][0]["spans"], [])

    def test_unset_status_and_equal_times(self) -> None:
        payload = otel.build_traces([
            {"event_id": "a", "type": "x", "created_at": 100.0},
            {"event_id": "b", "type": "y", "created_at": 100.0},
        ], "T-3")
        spans = payload["resourceSpans"][0]["scopeSpans"][0]["spans"]
        self.assertEqual(spans[0]["status"]["code"], 0)
        self.assertGreater(int(spans[1]["endTimeUnixNano"]), int(spans[1]["startTimeUnixNano"]))

    def test_cli_separates_metrics_and_traces_files(self) -> None:
        import subprocess
        import sys
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            metrics = os.path.join(tmp, "metrics.json")
            traces = os.path.join(tmp, "traces.json")
            proc = subprocess.run(
                [sys.executable, "-m", "harness", "--root", ROOT, "otel",
                 "--traces-task", "T-X", "--out", metrics, "--traces-out", traces],
                cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace",
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            with open(metrics, encoding="utf-8") as fh:
                m = json.load(fh)
            with open(traces, encoding="utf-8") as fh:
                t = json.load(fh)
            self.assertIn("resourceMetrics", m)
            self.assertNotIn("resourceSpans", m)
            self.assertIn("resourceSpans", t)
            self.assertNotIn("resourceMetrics", t)

    def test_cli_traces_without_sink_is_denied(self) -> None:
        import subprocess
        import sys

        proc = subprocess.run(
            [sys.executable, "-m", "harness", "--root", ROOT, "otel", "--traces-task", "T-X"],
            cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("traces-out", proc.stderr)

    def test_trace_id_is_stable(self) -> None:
        self.assertEqual(otel.trace_id("T-1"), otel.trace_id("T-1"))
        self.assertNotEqual(otel.trace_id("T-1"), otel.trace_id("T-2"))


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
