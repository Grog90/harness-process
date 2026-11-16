"""Активная область прав для плагина-ограждения.

Роль и `allowed_files` обязаны приходить от управляющего процесса, а не из
окружения, которое агент может выставить сам. Здесь они публикуются в файл,
который читает guard плагина: запись делает диспетч, снятие — вердикт или сторож.

Файл описывает не «роль профиля», а **карту ролей на время задачи**:

* `primary` — идентификатор агента-оркестратора: он не пишет продукт, но пишет
  документацию и выдаёт задачи;
* `role` — роль остальных агентов (любой, кто не `primary`): во время работы это
  `worker` с `allowed_files` брифа, во время проверки — `verifier`, который может
  писать только в `evidence/`.

Без активной задачи файла нет: тогда первичный агент работает как `human`, а
дочерние агенты не получают прав вовсе. Это закрывает случай «задачу отменили, а
воркер молча унаследовал права человека».

Файл намеренно вне `allowed_files` и защищён политикой (`R-PROT-1`), поэтому
рабочая роль не может расширить себе область.
"""

from __future__ import annotations

import json
import os
from typing import Any

SCOPE_REL = os.path.join(".harness", "guard-scope.json")

PHASE_RUNNING = "running"
PHASE_VERIFYING = "verifying"

# Что вправе писать проверяющий: только артефакты, не код.
VERIFIER_SCOPE = ["evidence/**"]


def scope_path(root: str) -> str:
    return os.path.join(root, SCOPE_REL)


def write_scope(root: str, *, task_id: str, role: str, allowed_files: list[str],
                attempt: int | None = None, primary: str | None = None,
                phase: str = PHASE_RUNNING,
                services: list[dict[str, Any]] | None = None) -> str:
    path = scope_path(root)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    payload = {
        "task_id": task_id,
        "role": role,
        "phase": phase,
        "primary": primary or "",
        "allowed_files": list(allowed_files),
        "services": list(services or []),
        "attempt": attempt,
    }
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(payload, fh, ensure_ascii=False)
        fh.write("\n")
    os.replace(tmp, path)
    return path


def read_scope(root: str) -> dict[str, Any] | None:
    try:
        with open(scope_path(root), encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError):
        return None


def publish_verifying(root: str, *, task_id: str, attempt: int | None = None,
                      primary: str | None = None) -> str:
    """Проверка — отдельная фаза: дочерние агенты становятся проверяющими."""
    current = read_scope(root) or {}
    return write_scope(
        root,
        task_id=task_id,
        role="verifier",
        allowed_files=list(VERIFIER_SCOPE),
        attempt=attempt if attempt is not None else current.get("attempt"),
        primary=(primary or current.get("primary") or "") or None,
        phase=PHASE_VERIFYING,
        services=list(current.get("services") or []),
    )


def clear_scope(root: str) -> None:
    try:
        os.unlink(scope_path(root))
    except OSError:
        pass


def role_for(scope: dict[str, Any] | None, agent_id: str | None) -> tuple[str | None, bool]:
    """Роль конкретного агента и признак «есть активная задача».

    Возвращает (`role`, `scoped`). Без задачи первичный агент — `human`,
    дочерний — `None` (писать ему нечего и нечем).
    """
    if not scope:
        return ("human" if not agent_id else "human"), False
    primary = str(scope.get("primary") or "")
    if agent_id and primary and agent_id == primary:
        return "orchestrator", True
    return str(scope.get("role") or "worker"), True


# Точки контроля, которые реально существуют в коде (для отчёта о покрытии матрицы).
CONTROL_POINTS = {
    "scope.write": write_scope,
    "scope.verifying": publish_verifying,
}
