"""L2: подпись коммитов — статическая проверка конфигурации доверия."""

from __future__ import annotations

import os
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class SigningTests(unittest.TestCase):
    def _read(self, rel: str) -> str:
        with open(os.path.join(ROOT, rel), encoding="utf-8") as fh:
            return fh.read()

    def test_allowed_signers_has_public_key_only(self) -> None:
        text = self._read(".github/allowed_signers").strip()
        self.assertIn("ssh-ed25519 ", text)
        self.assertNotIn("PRIVATE KEY", text)
        self.assertNotIn("OPENSSH PRIVATE", text)

    def test_ci_verifies_commit(self) -> None:
        workflow = self._read(".github/workflows/ci.yml")
        self.assertIn("git verify-commit", workflow)
        self.assertIn("allowed_signers", workflow)

    def test_docs_cover_rotation_and_github(self) -> None:
        doc = self._read("docs/SIGNING.md")
        for word in ("Ротация", "GitHub", "verify-commit", "harness_signing"):
            self.assertIn(word, doc)


if __name__ == "__main__":
    unittest.main(verbosity=2)
