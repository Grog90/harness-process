"""Финализатор: предполётная проверка подписи."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from harness import signing  # noqa: E402


def git(base: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", base, *args], capture_output=True,
                          text=True, encoding="utf-8", errors="replace")


class SigningPreflightTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = tempfile.mkdtemp(prefix="sign-root-")
        git(self.root, "init", "-q")

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def test_signing_not_required_by_default(self) -> None:
        self.assertFalse(signing.required(self.root))
        self.assertIsNone(signing.context(self.root))

    def test_required_without_key_is_denied(self) -> None:
        git(self.root, "config", "--local", "commit.gpgsign", "true")
        self.assertTrue(signing.required(self.root))
        with self.assertRaises(signing.SigningError) as ctx:
            signing.context(self.root)
        self.assertEqual(ctx.exception.rule, "R-FINAL-2")

    def test_context_and_commit_args(self) -> None:
        key = os.path.join(self.root, "key")
        allowed = os.path.join(self.root, "allowed")
        open(key, "w", encoding="utf-8").write("key\n")
        open(allowed, "w", encoding="utf-8").write("harness@local ssh-ed25519 AAAA\n")
        git(self.root, "config", "--local", "commit.gpgsign", "true")
        git(self.root, "config", "--local", "gpg.format", "ssh")
        git(self.root, "config", "--local", "user.signingkey", key)
        git(self.root, "config", "--local", "gpg.ssh.allowedSignersFile", allowed)
        ctx = signing.context(self.root)
        self.assertEqual(ctx["format"], "ssh")
        self.assertEqual(ctx["key"], key)
        args = signing.commit_args(ctx, "harness")
        self.assertIn("user.signingkey=" + key, args)
        self.assertIn("user.email=harness@harness.local", args)

    def test_signed_commit_verifies(self) -> None:
        if not shutil.which("ssh-keygen"):
            self.skipTest("ssh-keygen недоступен")
        key = os.path.join(self.root, "sign_key")
        subprocess.run(["ssh-keygen", "-t", "ed25519", "-N", "", "-f", key, "-q"], check=True)
        pub = open(key + ".pub", encoding="utf-8").read().strip()
        allowed = os.path.join(self.root, "allowed")
        with open(allowed, "w", encoding="utf-8") as fh:
            fh.write("owner " + " ".join(pub.split()[:2]) + "\n")
        git(self.root, "config", "--local", "commit.gpgsign", "true")
        git(self.root, "config", "--local", "gpg.format", "ssh")
        git(self.root, "config", "--local", "user.signingkey", key)
        git(self.root, "config", "--local", "gpg.ssh.allowedSignersFile", allowed)
        with open(os.path.join(self.root, "a.txt"), "w", encoding="utf-8") as fh:
            fh.write("x\n")
        git(self.root, "add", "-A")
        ctx = signing.context(self.root)
        proc = subprocess.run(
            ["git", "-C", self.root, *signing.commit_args(ctx, "harness"),
             "commit", "-S", "-m", "signed"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        signing.verify(self.root, ctx)

    def test_env_forces_requirement(self) -> None:
        old = os.environ.get("HARNESS_REQUIRE_SIGNED")
        os.environ["HARNESS_REQUIRE_SIGNED"] = "1"
        try:
            self.assertTrue(signing.required(self.root))
        finally:
            if old is None:
                os.environ.pop("HARNESS_REQUIRE_SIGNED", None)
            else:
                os.environ["HARNESS_REQUIRE_SIGNED"] = old


if __name__ == "__main__":
    unittest.main(verbosity=2)
