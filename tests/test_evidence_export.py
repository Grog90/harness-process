"""Экспорт evidence в доверенный каталог."""

from __future__ import annotations

import os
import json
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from harness import evidence_export as exp  # noqa: E402


class EvidenceExportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = tempfile.mkdtemp(prefix="ev-root-")
        self.dest = tempfile.mkdtemp(prefix="ev-dest-")
        self.src = os.path.join(self.root, "evidence", "change-x")
        os.makedirs(self.src, exist_ok=True)
        with open(os.path.join(self.src, "manifest.json"), "w", encoding="utf-8") as fh:
            fh.write('{"ok": true}\n')

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)
        shutil.rmtree(self.dest, ignore_errors=True)

    def test_export_and_verify(self) -> None:
        out = exp.export(self.root, self.dest, change="x")
        self.assertTrue(out["ok"])
        self.assertEqual(out["total"], 1)
        check = exp.verify(self.dest)
        self.assertTrue(check["ok"], check)
        self.assertEqual(check["files"], 1)

    def test_tamper_detected(self) -> None:
        exp.export(self.root, self.dest, change="x")
        with open(os.path.join(self.dest, "manifest.json"), "w", encoding="utf-8") as fh:
            fh.write("tampered\n")
        check = exp.verify(self.dest)
        self.assertFalse(check["ok"])
        self.assertIn("хэш не совпал", " ".join(check["problems"]))

    def test_source_dest_same_or_nested_denied(self) -> None:
        with self.assertRaises(exp.ExportError):
            exp.export(self.root, self.src, change="x")
        nested = os.path.join(self.src, "export")
        with self.assertRaises(exp.ExportError):
            exp.export(self.root, nested, change="x")

    def test_missing_file_in_copy_detected(self) -> None:
        exp.export(self.root, self.dest, change="x")
        os.unlink(os.path.join(self.dest, "manifest.json"))
        check = exp.verify(self.dest)
        self.assertFalse(check["ok"])
        self.assertIn("нет файла", " ".join(check["problems"]))

    def test_manifest_traversal_denied(self) -> None:
        exp.export(self.root, self.dest, change="x")
        manifest_path = os.path.join(self.dest, exp.MANIFEST)
        with open(manifest_path, encoding="utf-8") as fh:
            data = json.load(fh)
        data["files"][0]["path"] = "../outside"
        with open(manifest_path, "w", encoding="utf-8") as fh:
            json.dump(data, fh)
        check = exp.verify(self.dest)
        self.assertFalse(check["ok"])
        self.assertIn("недопустимый путь", " ".join(check["problems"]))

    def test_missing_source_denied(self) -> None:
        with self.assertRaises(exp.ExportError) as ctx:
            exp.export(self.root, self.dest, change="missing")
        self.assertEqual(ctx.exception.rule, "R-EVID-1")

    def test_missing_manifest_denied(self) -> None:
        with self.assertRaises(exp.ExportError):
            exp.verify(self.dest)


if __name__ == "__main__":
    unittest.main(verbosity=2)
