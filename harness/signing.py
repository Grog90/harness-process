"""Подпись коммитов: единая настройка для finalizer и merge.

Подпись требуется, если в репозитории локально включён `commit.gpgsign=true`
или выставлен `HARNESS_REQUIRE_SIGNED=1`. Без ключа финализатор отказывает
(R-FINAL-2) до создания коммита; после коммита подпись проверяется.
"""

from __future__ import annotations

import os
import subprocess
from typing import Any


class SigningError(RuntimeError):
    def __init__(self, message: str, **details: Any):
        super().__init__(message)
        self.rule = "R-FINAL-2"
        self.details = details


def _git(base: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", base, *args], capture_output=True, text=True,
        encoding="utf-8", errors="replace",
    )


def _config(base: str, key: str) -> str:
    proc = _git(base, "config", "--local", "--get", key)
    return proc.stdout.strip() if proc.returncode == 0 else ""


def required(base: str) -> bool:
    if os.environ.get("HARNESS_REQUIRE_SIGNED") == "1":
        return True
    return _config(base, "commit.gpgsign").lower() == "true"


def context(root: str) -> dict[str, str] | None:
    """Настройки подписи или None, если подпись не требуется."""
    if not required(root):
        return None
    fmt = _config(root, "gpg.format") or "openpgp"
    key = _config(root, "user.signingkey")
    if not key:
        raise SigningError("commit.gpgsign=true, но user.signingkey не задан")
    allowed = _config(root, "gpg.ssh.allowedSignersFile")
    if not allowed and fmt == "ssh":
        candidate = os.path.join(root, ".github", "allowed_signers")
        if os.path.isfile(candidate):
            allowed = candidate
    return {"format": fmt, "key": key, "allowed": allowed}


def commit_args(ctx: dict[str, str] | None, author: str) -> list[str]:
    args = ["-c", "user.name=" + author, "-c", "user.email=" + author + "@harness.local"]
    if ctx:
        args += [
            "-c", "commit.gpgsign=true",
            "-c", "gpg.format=" + ctx["format"],
            "-c", "user.signingkey=" + ctx["key"],
        ]
    return args


def verify(base: str, ctx: dict[str, str] | None) -> None:
    """Проверяет HEAD; при ошибке — SigningError."""
    if not ctx:
        return
    args = ["git", "-C", base]
    if ctx.get("allowed"):
        args += ["-c", "gpg.ssh.allowedSignersFile=" + ctx["allowed"]]
    args += ["verify-commit", "HEAD"]
    proc = subprocess.run(args, capture_output=True, text=True,
                          encoding="utf-8", errors="replace")
    if proc.returncode != 0:
        raise SigningError(
            "коммит не прошёл verify-commit: " + (proc.stderr.strip()[:300] or "нет подписи"),
            base=base,
        )
