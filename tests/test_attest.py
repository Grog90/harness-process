"""attest: независимая проверка подписи коммита."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from harness import attest  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def git(base: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", base, *args], capture_output=True,
                          text=True, encoding="utf-8", errors="replace")


class AttestTests(unittest.TestCase):
    def test_unsiged_commit_denied(self) -> None:
        root = tempfile.mkdtemp(prefix="attest-")
        self.addCleanup(shutil.rmtree, root, True)
        git(root, "init", "-q")
        git(root, "config", "--local", "user.name", "t")
        git(root, "config", "--local", "user.email", "t@t")
        open(os.path.join(root, "a.txt"), "w", encoding="utf-8").write("x\n")
        git(root, "add", "-A")
        git(root, "commit", "-q", "-m", "unsigned")
        allowed = os.path.join(root, "allowed")
        open(allowed, "w", encoding="utf-8").write("t ssh-ed25519 AAAA\n")
        git(root, "config", "--local", "gpg.ssh.allowedSignersFile", allowed)
        out = attest.verify(root)
        self.assertFalse(out["ok"])
        self.assertEqual(out["rule"], "R-ATTEST-1")

    def test_missing_signers_denied(self) -> None:
        root = tempfile.mkdtemp(prefix="attest-")
        self.addCleanup(shutil.rmtree, root, True)
        git(root, "init", "-q")
        with self.assertRaises(attest.AttestError):
            attest.verify(root)

    def test_explicit_allowed_signers_relative_to_root(self) -> None:
        if not os.path.isdir(os.path.join(ROOT, ".git")):
            self.skipTest("not a git checkout")
        out = attest.verify(ROOT, "HEAD", ".github/allowed_signers")
        self.assertTrue(out["ok"], out)
        self.assertTrue(os.path.isabs(out["signers"]))

    def test_explicit_allowed_signers_empty_denied(self) -> None:
        root = tempfile.mkdtemp(prefix="attest-")
        self.addCleanup(shutil.rmtree, root, True)
        git(root, "init", "-q")
        git(root, "config", "--local", "user.name", "t")
        git(root, "config", "--local", "user.email", "t@t")
        open(os.path.join(root, "a.txt"), "w", encoding="utf-8").write("x\n")
        git(root, "add", "-A")
        git(root, "commit", "-q", "-m", "unsigned")
        allowed = os.path.join(root, "allowed")
        open(allowed, "w", encoding="utf-8").write("t ssh-ed25519 AAAA\n")
        out = attest.verify(root, "HEAD", "allowed")
        self.assertFalse(out["ok"])

    def test_current_repo_is_signed(self) -> None:
        if not os.path.isdir(os.path.join(ROOT, ".git")):
            self.skipTest("not a git checkout")
        out = attest.verify(ROOT)
        self.assertTrue(out["ok"], out)


if __name__ == "__main__":
    unittest.main(verbosity=2)
