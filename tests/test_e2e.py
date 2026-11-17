"""Multi-service e2e на копиях сервисов."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from harness import e2e  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class E2ETests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = tempfile.mkdtemp(prefix="e2e-root-")
        self.api = os.path.join(self.root, "services", "api")
        self.web = os.path.join(self.root, "services", "web")
        for path, name in ((self.api, "api"), (self.web, "web")):
            os.makedirs(path, exist_ok=True)
            with open(os.path.join(path, f"{name}.txt"), "w", encoding="utf-8") as fh:
                fh.write(name)

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def test_parse_services(self) -> None:
        out = e2e.parse_services(["api=services/api@abc", "web=services/web"])
        self.assertEqual(out[0]["name"], "api")
        self.assertEqual(out[0]["sha"], "abc")
        with self.assertRaises(e2e.E2EError):
            e2e.parse_services(["bad"])
        with self.assertRaises(e2e.E2EError):
            e2e.parse_services([])

    def test_run_passes_env_and_records_report(self) -> None:
        code = (
            "import os,sys;"
            "assert os.path.isdir(os.environ['HARNESS_E2E_API_DIR']);"
            "assert os.path.isdir(os.environ['HARNESS_E2E_WEB_DIR']);"
            "print(os.environ['HARNESS_E2E_API_DIR'])"
        )
        command = f'"{sys.executable}" -c "{code}"'
        services = [
            {"name": "api", "path": self.api, "sha": ""},
            {"name": "web", "path": self.web, "sha": ""},
        ]
        out = e2e.run(self.root, "chg-x", command, services)
        self.assertTrue(out["ok"], out)
        self.assertEqual(out["exit_code"], 0)
        self.assertEqual(len(out["services"]), 2)
        self.assertTrue(os.path.isfile(out["report"]))
        with open(out["report"], encoding="utf-8") as fh:
            report = json.load(fh)
        self.assertEqual(report["change"], "chg-x")
        self.assertEqual(report["services"][0]["name"], "api")
        self.assertTrue(out["services"][0]["dirty"])

    def test_run_failure_is_reported(self) -> None:
        command = f'"{sys.executable}" -c "raise SystemExit(3)"'
        services = [{"name": "api", "path": self.api, "sha": ""}]
        out = e2e.run(self.root, "chg-y", command, services)
        self.assertFalse(out["ok"])
        self.assertEqual(out["exit_code"], 3)

    def test_sha_is_archived_not_worktree(self) -> None:
        repo = os.path.join(self.root, "repo")
        os.makedirs(repo, exist_ok=True)
        run = lambda *args: subprocess.run(["git", "-C", repo, *args], capture_output=True,
                                           text=True, encoding="utf-8", errors="replace")
        run("init")
        run("config", "user.email", "t@example.com")
        run("config", "user.name", "t")
        with open(os.path.join(repo, "version.txt"), "w", encoding="utf-8") as fh:
            fh.write("committed")
        run("add", "version.txt")
        run("commit", "-m", "one")
        sha = run("rev-parse", "HEAD").stdout.strip()
        with open(os.path.join(repo, "version.txt"), "w", encoding="utf-8") as fh:
            fh.write("dirty")
        code = "import os;print(open(os.path.join(os.environ['HARNESS_E2E_API_DIR'],'version.txt')).read())"
        command = f'"{sys.executable}" -c "{code}"'
        out = e2e.run(self.root, "chg-sha", command,
                      [{"name": "api", "path": repo, "sha": sha}])
        self.assertTrue(out["ok"], out)
        self.assertIn("committed", out["stdout_tail"])
        self.assertEqual(out["services"][0]["sha"], sha)

    def test_timeout_marks_result(self) -> None:
        code = "import time;time.sleep(5)"
        command = f'"{sys.executable}" -c "{code}"'
        out = e2e.run(self.root, "chg-t", command,
                      [{"name": "api", "path": self.api, "sha": ""}], timeout=1)
        self.assertFalse(out["ok"])
        self.assertTrue(out["timed_out"])

    def test_timeout_kills_child_tree(self) -> None:
        marker = os.path.join(self.root, "child.txt")
        child = os.path.join(self.root, "child.py")
        with open(child, "w", encoding="utf-8") as fh:
            fh.write("import time,pathlib\n"
                     "time.sleep(3)\n"
                     f"pathlib.Path(r'{marker}').write_text('x')\n")
        slow = os.path.join(self.root, "slow.py")
        with open(slow, "w", encoding="utf-8") as fh:
            fh.write("import subprocess,sys,time\n"
                     f"subprocess.Popen([sys.executable, r'{child}'])\n"
                     "time.sleep(10)\n")
        command = f'"{sys.executable}" "{slow}"'
        out = e2e.run(self.root, "chg-kill", command,
                      [{"name": "api", "path": self.api, "sha": ""}], timeout=1)
        self.assertTrue(out["timed_out"], out)
        import time as _time
        _time.sleep(4)
        self.assertFalse(os.path.exists(marker), "дочерний процесс пережил таймаут")

    def test_bad_names_and_duplicates_denied(self) -> None:
        for bad in ("bad-name=x", "with space=x", "..=x"):
            with self.assertRaises(e2e.E2EError):
                e2e.parse_services([bad])
        with self.assertRaises(e2e.E2EError):
            e2e.parse_services(["api=x", "api=y"])

    def test_worker_role_denied(self) -> None:
        env = dict(os.environ, HARNESS_ROLE="worker")
        proc = subprocess.run(
            [sys.executable, "-m", "harness", "--root", ROOT, "e2e", "run",
             "--change", "chg", "--command", "echo x", "--service", "api=services/api"],
            cwd=ROOT, env=env, capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("R-CAP-1", proc.stderr)

    def test_missing_service_denied(self) -> None:
        with self.assertRaises(e2e.E2EError):
            e2e.run(self.root, "chg-z", "echo x",
                    [{"name": "api", "path": os.path.join(self.root, "nope"), "sha": ""}])

    def test_bad_change_id_denied(self) -> None:
        with self.assertRaises(e2e.E2EError):
            e2e.run(self.root, "../x", "echo x", [{"name": "api", "path": self.api, "sha": ""}])


if __name__ == "__main__":
    unittest.main(verbosity=2)
