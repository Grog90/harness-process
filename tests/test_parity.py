"""Паритет решений: плагин (JS) и ядро (Python) должны отвечать одинаково.

Плагин держит копию «горячих» правил, потому что guard синхронный. Копия — это
риск дрейфа, поэтому она проверяется машинно: одна и та же матрица случаев
прогоняется через оба решателя, и любое расхождение валит тест.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from harness.hooks import HookContext, run as run_hooks  # noqa: E402
from harness.policy import Policy  # noqa: E402

PLUGIN = os.path.join(ROOT, "plugins", "harness-guard")
PARITY = os.path.join(PLUGIN, "parity.mjs")
NODE_CANDIDATES = (
    os.path.join(os.path.expanduser("~"), ".dsh", "dsh-runtimes", "dsh-primary-runtime",
                 "dependencies", "node", "bin", "node.exe"),
    shutil.which("node") if shutil.which("node") else "",
)


def find_node() -> str | None:
    for candidate in NODE_CANDIDATES:
        if candidate and os.path.isfile(candidate):
            return candidate
    return None


CASES: list[dict] = [
    # роль и заявленная роль
    {"role": "worker", "tool": "write_file", "args": {"path": "src/a.py"}, "allowed_files": ["src/a.py"]},
    {"role": "verifier", "claimed_role": "worker", "tool": "read_file", "args": {"path": "src/a.py"}},
    # границы записи
    {"role": "orchestrator", "tool": "write_file", "args": {"path": "docs/plan.md"}},
    {"role": "orchestrator", "tool": "write_file", "args": {"path": "src/a.py"}},
    {"role": "verifier", "tool": "write_file", "args": {"path": "src/a.py"}},
    {"role": "verifier", "tool": "write_file", "args": {"path": "evidence/verdict.json"}},
    {"role": "worker", "tool": "write_file", "args": {"path": "src/a.py"}, "allowed_files": ["src/a.py"]},
    {"role": "worker", "tool": "write_file", "args": {"path": "src/b.py"}, "allowed_files": ["src/a.py"]},
    {"role": "worker", "tool": "write_file", "args": {"path": "src/a.py"}, "allowed_files": []},
    {"role": "worker", "tool": "write_file", "args": {"path": "policies/policy.json"},
     "allowed_files": ["policies/**"]},
    {"role": "system", "tool": "write_file", "args": {"path": "policies/policy.json"}},
    # выход из корня
    {"role": "worker", "tool": "write_file", "args": {"path": "../secrets.txt"}, "allowed_files": ["**"]},
    {"role": "worker", "tool": "write_file", "args": {"path": "src/../../etc/passwd"}, "allowed_files": ["**"]},
    # инструменты
    {"role": "worker", "tool": "dispatch"},
    {"role": "worker", "tool": "bash", "args": {"command": "pytest -q"}},
    {"role": "worker", "tool": "bash", "args": {"command": "sleep 60"}},
    {"role": "worker", "tool": "bash", "args": {"command": "git reset --hard"}},
    {"role": "worker", "tool": "bash", "args": {"command": "git push --force origin main"}},
    {"role": "worker", "tool": "bash", "args": {"command": "git clean -fd"}},
    {"role": "worker", "tool": "bash", "args": {"command": "git branch -D feature"}},
    # `>` внутри кавычек — не перенаправление
    {"role": "worker", "tool": "bash", "args": {"command": "python -c \"print('a >> b')\""}, "allowed_files": ["src/a.py"]},
    {"role": "worker", "tool": "bash", "args": {"command": "git -C ../wt reset --hard"}},
    {"role": "orchestrator", "tool": "bash", "args": {"command": "echo hi >> src/a.py"}},
    {"role": "orchestrator", "tool": "bash", "args": {"command": "sed -i 's/a/b/' src/a.py"}},
    {"role": "orchestrator", "tool": "bash", "args": {"command": "echo hi >> docs/plan.md"}},
    # shell не пишет control-plane/protected: parity Python↔JS
    {"role": "worker", "tool": "bash", "args": {"command": "echo x > .harness/session-attach.json"}},
    {"role": "worker", "tool": "bash", "args": {"command": "echo x > policies/policy.json"}},
    # процессные роли: analyst пишет спеку, qa — тесты, worker — только код
    {"role": "analyst", "tool": "write_file", "args": {"path": "openspec/changes/x/spec.md"}},
    {"role": "analyst", "tool": "write_file", "args": {"path": "src/a.py"}},
    {"role": "qa", "tool": "write_file", "args": {"path": "tests/test_x.py"}, "allowed_files": ["tests/test_x.py"]},
    {"role": "qa", "tool": "write_file", "args": {"path": "src/a.py"}, "allowed_files": ["src/a.py"]},
    {"role": "worker", "tool": "write_file", "args": {"path": "openspec/changes/x/spec.md"},
     "allowed_files": ["openspec/changes/x/spec.md"]},
    # мультикорневая запись: только сервис с write=true и только в allowed_files
    {"role": "worker", "tool": "write_file", "args": {"path": "../svc/src/a.py"},
     "allowed_files": ["service:api/src/a.py"],
     "services": [{"name": "api", "path": "../svc", "write": True}]},
    {"role": "worker", "tool": "write_file", "args": {"path": "../svc/src/b.py"},
     "allowed_files": ["service:api/src/a.py"],
     "services": [{"name": "api", "path": "../svc", "write": True}]},
    {"role": "worker", "tool": "write_file", "args": {"path": "../svc/src/a.py"},
     "allowed_files": ["service:api/src/a.py"],
     "services": [{"name": "api", "path": "../svc", "write": False}]},
    {"role": "verifier", "tool": "write_file", "args": {"path": "../svc/src/a.py"},
     "allowed_files": ["service:api/src/a.py"],
     "services": [{"name": "api", "path": "../svc", "write": True}]},
    {"role": "worker", "tool": "write_file", "args": {"path": "../other/src/a.py"},
     "allowed_files": ["service:api/src/a.py"],
     "services": [{"name": "api", "path": "../svc", "write": True}]},
    # in-root сервис: protected и read-only не обходятся service-ключом/путём
    {"role": "worker", "tool": "write_file", "args": {"path": "service:root/policies/policy.json"},
     "allowed_files": ["service:root/policies/policy.json"],
     "services": [{"name": "root", "path": ".", "write": True}]},
    {"role": "worker", "tool": "write_file", "args": {"path": "services/api/src/a.py"},
     "allowed_files": ["service:api/src/a.py"],
     "services": [{"name": "api", "path": "services/api", "write": False}]},
    # привилегированные инструменты: человеку можно, рабочим — нет
    {"role": "worker", "tool": "plugin_manager"},
    {"role": "verifier", "tool": "plugin_manager"},
    {"role": "human", "tool": "plugin_manager"},
    {"role": "worker", "tool": "mcp__awgm__set_singbox_router_mode"},
    {"role": "worker", "tool": "mcp__xerj__xerj_search"},
    # делегирование: оркестратор раздаёт работу, исполнитель и проверяющий — нет
    {"role": "orchestrator", "tool": "subagent"},
    {"role": "orchestrator", "tool": "workflow"},
    {"role": "worker", "tool": "subagent"},
    {"role": "verifier", "tool": "workflow"},
]


class ParityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.node = find_node()
        if not self.node:
            self.skipTest("node не найден: паритет плагина не проверить")
        self.policy_path = os.path.join(ROOT, "policies", "policy.json")
        self.policy = Policy.load(ROOT)

    def _python_decision(self, case: dict) -> dict:
        ctx = HookContext(
            event="pretooluse",
            role=case["role"],
            claimed_role=case.get("claimed_role"),
            tool=case["tool"],
            path=(case.get("args") or {}).get("path"),
            command=(case.get("args") or {}).get("command"),
            target=(case.get("args") or {}).get("path"),
            allowed_files=list(case.get("allowed_files") or []),
            services=list(case.get("services") or []),
            policy=self.policy,
            root=ROOT,
        )
        result = run_hooks("pretooluse", ctx)
        return {"decision": result["decision"], "rule": result.get("rule", "")}

    def _js_decisions(self) -> list[dict]:
        payload = json.dumps({"policy_path": self.policy_path, "root": ROOT, "cases": CASES})
        proc = subprocess.run(
            [self.node, PARITY],
            input=payload,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=PLUGIN,
            timeout=60,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr[:500])
        return json.loads(proc.stdout)

    def test_js_and_python_agree(self) -> None:
        js = self._js_decisions()
        self.assertEqual(len(js), len(CASES))
        mismatches = []
        for case, js_decision in zip(CASES, js):
            py = self._python_decision(case)
            if py["decision"] != js_decision["decision"] or (
                py["decision"] == "deny" and py["rule"] != js_decision["rule"]
            ):
                mismatches.append(
                    f"{case['role']}/{case['tool']} {case.get('args')}: "
                    f"python={py} js={js_decision}"
                )
        self.assertEqual(mismatches, [], "\n".join(mismatches))

    def test_policy_file_is_the_single_source(self) -> None:
        """Плагин читает ту же политику, а не свою копию настроек."""
        with open(self.policy_path, encoding="utf-8") as fh:
            data = json.load(fh)
        self.assertIn("roles", data)
        self.assertIn("worker", data["roles"])
        self.assertIn("product_globs", data)


if __name__ == "__main__":
    unittest.main(verbosity=2)
