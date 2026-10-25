"""Тесты проверки конфигурации: то, что видно только в живой среде."""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from harness import tmpdir  # noqa: E402

from harness import doctor  # noqa: E402


class DoctorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = tmpdir.scratch(ROOT, "harness-doctor-")
        os.makedirs(os.path.join(self.root, "policies"), exist_ok=True)
        with open(os.path.join(self.root, "policies", "policy.json"), "w", encoding="utf-8") as fh:
            json.dump({"version": "1", "roles": {"human": {"tools": ["*"]}},
                       "product_globs": [], "protected_globs": []}, fh)
        self.home = tmpdir.scratch(ROOT, "harness-doctor-home-")
        self.profile = os.path.join(self.home, "profiles", "desktop")
        os.makedirs(self.profile, exist_ok=True)
        self._old = {k: os.environ.get(k) for k in ("DSH_HOME", "DSH_PROFILE_DIR", "DSH_PERMISSION_MODE")}
        os.environ["DSH_HOME"] = self.home
        os.environ["DSH_PROFILE_DIR"] = self.profile
        os.environ.pop("DSH_PERMISSION_MODE", None)

    def tearDown(self) -> None:
        for key, value in self._old.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        shutil.rmtree(self.root, ignore_errors=True)
        shutil.rmtree(self.home, ignore_errors=True)

    def _write_profile(self, bundles: list[str]) -> None:
        with open(os.path.join(self.profile, "package.json"), "w", encoding="utf-8") as fh:
            json.dump({"name": "p", "dsh": {"profile": {"bundles": bundles}},
                       "dependencies": {}}, fh)

    def _checks(self) -> dict[str, dict]:
        return {c["id"]: c for c in doctor.run(self.root)["checks"]}

    def test_missing_policy_fails(self) -> None:
        os.unlink(os.path.join(self.root, "policies", "policy.json"))
        self.assertFalse(self._checks()["policy"]["ok"])

    def test_installed_plugin_detected(self) -> None:
        self._write_profile(["@harness/dsh-guard"])
        self.assertTrue(self._checks()["plugin"]["ok"])

    def test_plugin_absent_is_reported(self) -> None:
        self._write_profile(["@deepseek-ai/dsh-base"])
        self.assertFalse(self._checks()["plugin"]["ok"])

    def test_unknown_sandbox_mode_is_warning(self) -> None:
        self._write_profile(["@harness/dsh-guard"])
        self.assertFalse(self._checks()["sandbox"]["ok"])
        os.environ["DSH_PERMISSION_MODE"] = "workspace-write"
        try:
            self.assertTrue(self._checks()["sandbox"]["ok"])
        finally:
            os.environ.pop("DSH_PERMISSION_MODE", None)

    def test_stale_scope_is_reported(self) -> None:
        from harness.fsm import Engine
        from harness.policy import Policy
        from harness.state import Store

        self._write_profile([])
        os.makedirs(os.path.join(self.root, ".harness"), exist_ok=True)
        store = Store(os.path.join(self.root, ".harness", "state.db"))
        try:
            store.create_task("T1", policy_version="1.0.0")
            Engine(store, Policy.load(self.root), self.root).apply("T1", "cancel", {})
        finally:
            store.close()
        with open(os.path.join(self.root, ".harness", "guard-scope.json"), "w", encoding="utf-8") as fh:
            json.dump({"role": "worker", "allowed_files": ["src/a.py"]}, fh)
        check = self._checks()["scope"]
        self.assertFalse(check["ok"])
        self.assertIn("висит", check["title"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
