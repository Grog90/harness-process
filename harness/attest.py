"""Проверка подписанного релиза: независимая от финализатора команда.

Читает только git и allowed_signers; не доверяет состоянию harness.
"""

from __future__ import annotations

import os
import subprocess
from typing import Any


class AttestError(RuntimeError):
    def __init__(self, rule: str, message: str, **details: Any):
        super().__init__(message)
        self.rule = rule
        self.details = details


def _git(base: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", base, *args], capture_output=True,
                          text=True, encoding="utf-8", errors="replace")


def verify(root: str, ref: str = "HEAD", allowed_signers: str | None = None) -> dict[str, Any]:
    root = os.path.abspath(root)
    if not os.path.isdir(os.path.join(root, ".git")):
        raise AttestError("R-ATTEST-1", "не git-репозиторий", root=root)
    sha = _git(root, "rev-parse", ref).stdout.strip()
    if not sha:
        raise AttestError("R-ATTEST-1", f"нет такого ref: {ref}")
    if allowed_signers:
        signers = os.path.expanduser(allowed_signers)
        if not os.path.isabs(signers):
            signers = os.path.join(root, signers)
    else:
        signers = _git(root, "config", "--local", "--get", "gpg.ssh.allowedSignersFile").stdout.strip()
        if signers and not os.path.isabs(signers):
            signers = os.path.join(root, signers)
        if not signers:
            candidate = os.path.join(root, ".github", "allowed_signers")
            signers = candidate if os.path.isfile(candidate) else ""
    signers = os.path.normpath(signers) if signers else ""
    if not signers or not os.path.isfile(signers):
        raise AttestError("R-ATTEST-1", "allowed_signers не найден", path=signers or None)
    proc = _git(root, "-c", "gpg.format=ssh", "-c",
                "gpg.ssh.allowedSignersFile=" + signers, "verify-commit", sha)
    ok = proc.returncode == 0
    return {
        "ok": ok,
        "rule": "R-ATTEST-1",
        "ref": ref,
        "sha": sha,
        "signers": signers,
        "detail": (proc.stderr or proc.stdout).strip()[:500],
    }


CONTROL_POINTS = {"attest.verify": verify}
