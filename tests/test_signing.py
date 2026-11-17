"""L2: подпись коммитов — проверки конфигурации доверия и HEAD."""

from __future__ import annotations

import os
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class SigningTests(unittest.TestCase):
    def _read(self, rel: str) -> str:
        with open(os.path.join(ROOT, rel), encoding="utf-8") as fh:
            return fh.read()

    def test_head_signature_is_valid(self) -> None:
        import subprocess

        if not os.path.isdir(os.path.join(ROOT, ".git")):
            self.skipTest("not a git checkout")
        ref = os.environ.get("HARNESS_SIGNING_REF", "HEAD")
        proc = subprocess.run(
            ["git", "-c", "gpg.format=ssh",
             "-c", "gpg.ssh.allowedSignersFile=" + os.path.join(ROOT, ".github", "allowed_signers"),
             "verify-commit", ref],
            cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_no_private_keys_in_tracked_tree(self) -> None:
        import subprocess

        if not os.path.isdir(os.path.join(ROOT, ".git")):
            self.skipTest("not a git checkout")
        files = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True,
                               text=True, encoding="utf-8", errors="replace").stdout.splitlines()
        marker = "BEGIN OPENSSH " + "PRIVATE KEY"
        for rel in files:
            path = os.path.join(ROOT, rel)
            try:
                with open(path, encoding="utf-8") as fh:
                    if marker in fh.read():
                        self.fail(f"приватный ключ в дереве: {rel}")
            except (OSError, UnicodeDecodeError):
                continue

    def test_allowed_signers_has_public_key_only(self) -> None:
        text = self._read(".github/allowed_signers").strip()
        self.assertIn("ssh-ed25519 ", text)
        self.assertNotIn("PRIVATE KEY", text)
        self.assertNotIn("OPENSSH PRIVATE", text)

    def test_ci_verifies_commit(self) -> None:
        workflow = self._read(".github/workflows/ci.yml")
        self.assertIn("git verify-commit", workflow)
        self.assertIn("allowed_signers", workflow)
        self.assertIn("fetch-depth: 0", workflow)
        self.assertIn("FETCH_HEAD:.github/allowed_signers", workflow)
        self.assertIn("rev-list", workflow)
        self.assertIn("verify-commit", workflow)
        self.assertIn("github.event.before", workflow)
        self.assertIn("pull_request", workflow)
        self.assertIn("HARNESS_SIGNING_REF", workflow)
        self.assertIn("pull_request.head.sha", workflow)

    def test_docs_cover_rotation_and_github(self) -> None:
        doc = self._read("docs/SIGNING.md")
        for word in ("Ротация", "GitHub", "verify-commit", "harness_signing"):
            self.assertIn(word, doc)


if __name__ == "__main__":
    unittest.main(verbosity=2)
