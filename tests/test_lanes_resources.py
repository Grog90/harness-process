"""Ресурсы лейна: слоты, уникальность, освобождение и передача в проверки.

Проверки идут без docker: интересен контракт (какие переменные и аргументы), а не
наличие демона на машине.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from unittest import mock

from harness import checks, lanes
from harness.state import Store


def make_root(tmp: str) -> str:
    root = os.path.join(tmp, "repo")
    os.makedirs(os.path.join(root, ".harness"))
    return root


class SlotTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = make_root(self._tmp.name)
        self.store = Store(os.path.join(self.root, ".harness", "state.db"))
        patcher = mock.patch.object(lanes, "port_free", return_value=True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self) -> None:
        self.store.close()
        self._tmp.cleanup()

    def _worktree(self, task_id: str) -> str:
        path = lanes.lane_path(self.root, task_id)
        os.makedirs(path, exist_ok=True)
        return path

    def test_formula_is_stable(self) -> None:
        first = lanes.resources_for(self.root, 0)
        second = lanes.resources_for(self.root, 2)
        self.assertEqual(first["port_base"], 18000)
        self.assertEqual(second["port_base"], 18020)
        self.assertTrue(first["test_db"].endswith(os.path.join("lanes", "0", "test.db")))
        self.assertTrue(second["tmp"].endswith(os.path.join("lanes", "2", "tmp")))

    def test_two_lanes_get_different_slots(self) -> None:
        self._worktree("T-1")
        self._worktree("T-2")
        self.assertEqual(lanes.reserve_slot(self.store, self.root, "T-1"), 0)
        self.assertEqual(lanes.reserve_slot(self.store, self.root, "T-2"), 1)

    def test_busy_slot_is_not_handed_out_twice(self) -> None:
        self._worktree("T-1")
        self._worktree("T-2")
        first = lanes.reserve_slot(self.store, self.root, "T-1")
        second = lanes.reserve_slot(self.store, self.root, "T-2")
        self.assertNotEqual(first, second)
        self.assertEqual(self.store.slot_owner(first), "T-1")

    def test_released_slot_is_reused(self) -> None:
        self._worktree("T-1")
        lanes.reserve_slot(self.store, self.root, "T-1")
        lanes.release(self.store, "T-1")
        self._worktree("T-2")
        self.assertEqual(lanes.reserve_slot(self.store, self.root, "T-2"), 0)

    def test_stale_lane_does_not_hold_slot(self) -> None:
        self._worktree("OLD")
        lanes.reserve_slot(self.store, self.root, "OLD")
        os.rename(lanes.lane_path(self.root, "OLD"), os.path.join(self.root, "gone"))
        self._worktree("NEW")
        self.assertEqual(lanes.reserve_slot(self.store, self.root, "NEW"), 0)

    def test_show_without_lane_is_explained(self) -> None:
        with self.assertRaises(lanes.LaneError) as ctx:
            lanes.show(self.root, self.store, "NOPE")
        self.assertEqual(ctx.exception.rule, "R-WAVE-1")

    def test_show_matches_issued(self) -> None:
        self._worktree("T-1")
        slot = lanes.reserve_slot(self.store, self.root, "T-1")
        shown = lanes.show(self.root, self.store, "T-1")
        self.assertEqual(shown["slot"], slot)
        self.assertEqual(shown["port_base"], 18000 + slot * 10)


class ChecksEnvTest(unittest.TestCase):
    def test_no_lane_means_no_variables(self) -> None:
        self.assertEqual(checks.lane_env(None), {})
        self.assertNotIn("HARNESS_LANE_INDEX", checks._env())

    def test_lane_variables_are_added_on_top(self) -> None:
        resources = {"slot": 3, "port_base": 18030, "test_db": "/x/test.db", "tmp": "/x/tmp"}
        env = checks._env(resources)
        self.assertEqual(env["HARNESS_LANE_INDEX"], "3")
        self.assertEqual(env["HARNESS_LANE_PORT_BASE"], "18030")
        self.assertEqual(env["HARNESS_TEST_DB"], "/x/test.db")
        self.assertEqual(env["LANG"], "C.UTF-8")

    def test_docker_gets_explicit_e_flags(self) -> None:
        captured: dict[str, list[str]] = {}

        def fake_run(cmd, **kwargs):  # noqa: ANN001, ANN003
            captured["cmd"] = list(cmd)
            return subprocess.CompletedProcess(cmd, 0, "", "")

        resources = {"slot": 2, "port_base": 18020, "test_db": "/x/test.db", "tmp": "/x/tmp"}
        with mock.patch.object(checks.shutil, "which", return_value="docker"), \
                mock.patch.object(checks.subprocess, "run", side_effect=fake_run):
            checks._execute("true", "/tree", 5, True, {"image": "img"}, resources)

        cmd = captured["cmd"]
        self.assertIn("-e", cmd)
        self.assertIn("HARNESS_LANE_PORT_BASE=18020", cmd)

    def test_command_receives_its_own_port_base(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = os.path.join(tmp, "repo")
            os.makedirs(root)
            brief = {
                "task_id": "T-1",
                "acceptance": {"check_commands": ["python -c \"import os;print(os.environ.get('HARNESS_LANE_PORT_BASE'))\""],
                               "criteria": [{"id": "AC-1", "statement": "порт", "check": "python -c pass"}]},
            }
            resources = {"slot": 1, "port_base": 18010, "test_db": os.path.join(tmp, "t.db"),
                         "tmp": os.path.join(tmp, "t")}
            report = checks.run(root, brief, task_id="T-1", sandbox="copy", lane_resources=resources)
            self.assertIn("18010", report["results"][0]["stdout_tail"])
            self.assertEqual(report["lane"]["HARNESS_LANE_INDEX"], "1")

    def test_without_lane_command_sees_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = os.path.join(tmp, "repo")
            os.makedirs(root)
            brief = {
                "task_id": "T-2",
                "acceptance": {"check_commands": ["python -c \"import os;print(os.environ.get('HARNESS_LANE_INDEX'))\""],
                               "criteria": [{"id": "AC-1", "statement": "нет", "check": "python -c pass"}]},
            }
            report = checks.run(root, brief, task_id="T-2", sandbox="copy")
            self.assertIn("None", report["results"][0]["stdout_tail"])


if __name__ == "__main__":
    unittest.main()
