"""Тесты активной области прав: публикация, чтение, снятие и перезапись.

`harness.scope` — единственная точка, через которую диспетч публикует роль и
`allowed_files` для плагина-ограждения. Здесь проверяется контракт этой точки:
файл `.harness/guard-scope.json` появляется, читается теми же значениями,
перезаписывается новыми и удаляется без ошибок.
"""

from __future__ import annotations

import os
import shutil
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from harness import scope  # noqa: E402
from harness import tmpdir  # noqa: E402


class ScopeTests(unittest.TestCase):
    """Контракт записи, чтения и очистки области прав."""

    def setUp(self) -> None:
        self.root = tmpdir.scratch(ROOT, "scope-test-")

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def test_write_then_read_roundtrip(self) -> None:
        """Запись создаёт guard-scope.json, и чтение возвращает те же значения."""
        path = scope.write_scope(
            self.root,
            task_id="T-1",
            role="worker",
            allowed_files=["tests/test_scope.py", "evidence/verdict.json"],
            attempt=2,
        )
        self.assertEqual(path, scope.scope_path(self.root))
        self.assertTrue(os.path.isfile(path), "файл области прав должен существовать")

        data = scope.read_scope(self.root)
        self.assertIsNotNone(data)
        assert data is not None  # для проверяющего типов
        self.assertEqual(data["task_id"], "T-1")
        self.assertEqual(data["role"], "worker")
        self.assertEqual(data["allowed_files"],
                         ["tests/test_scope.py", "evidence/verdict.json"])
        self.assertEqual(data["attempt"], 2)

    def test_clear_removes_file_and_read_returns_none(self) -> None:
        """Очистка удаляет файл; после неё чтение возвращает None."""
        scope.write_scope(self.root, task_id="T-2", role="worker",
                          allowed_files=["a.py"], attempt=1)
        self.assertTrue(os.path.isfile(scope.scope_path(self.root)))

        scope.clear_scope(self.root)
        self.assertFalse(os.path.isfile(scope.scope_path(self.root)))
        self.assertIsNone(scope.read_scope(self.root))

    def test_read_missing_returns_none(self) -> None:
        """Чтение на корне без файла области прав не падает, а даёт None."""
        self.assertFalse(os.path.exists(scope.scope_path(self.root)))
        self.assertIsNone(scope.read_scope(self.root))

    def test_write_overwrites_previous_scope(self) -> None:
        """Повторная запись перезаписывает прежнюю область новыми значениями."""
        scope.write_scope(self.root, task_id="T-OLD", role="worker",
                          allowed_files=["old.py"], attempt=1)
        scope.write_scope(self.root, task_id="T-NEW", role="human",
                          allowed_files=["new.py", "other.py"], attempt=7)

        data = scope.read_scope(self.root)
        self.assertIsNotNone(data)
        assert data is not None
        self.assertEqual(data["task_id"], "T-NEW")
        self.assertEqual(data["role"], "human")
        self.assertEqual(data["allowed_files"], ["new.py", "other.py"])
        self.assertEqual(data["attempt"], 7)


if __name__ == "__main__":
    unittest.main(verbosity=2)
