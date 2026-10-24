"""Watchdog как отдельный процесс.

Пока таймер живёт в том же процессе, что и агент, падение управляющего слоя
означает, что полномочия никто не отзовёт. Поэтому проверка сроков вынесена в
самостоятельный цикл: он читает общее состояние (SQLite) и переводит просроченные
диспетчи в `TIMED_OUT`.

Смерть исполнителя не маскируется под нормальное завершение: событие
`dispatch_death` синтезируется отдельно и остаётся в журнале, даже если сам
исполнитель ничего не прислал.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Callable

from .fsm import Engine, check_watchdog
from .state import Store, now


@dataclass
class Revocation:
    task_id: str
    attempt: int
    deadline: float
    overdue_s: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "attempt": self.attempt,
            "deadline": self.deadline,
            "overdue_s": round(self.overdue_s, 1),
        }


class Watchdog:
    def __init__(self, store: Store, engine: Engine | None = None):
        self.engine = engine
        # состояние читаем и пишем из одного места: иначе отзыв и проверка
        # срока могут смотреть в разные файлы
        self.store = engine.store if engine is not None else store

    def candidates(self) -> list[dict[str, Any]]:
        """Задачи, которые формально ещё RUNNING, но срок уже истёк."""
        result = []
        for task in self.store.list_tasks():
            if task["state"] != "RUNNING" or not task.get("deadline_ts"):
                continue
            if now() > float(task["deadline_ts"]):
                result.append(task)
        return result

    def sweep(self, *, on_revoke: Callable[[Revocation], None] | None = None) -> list[Revocation]:
        """Один проход: отзывает всё просроченное. Идемпотентен по построению."""
        revoked: list[Revocation] = []
        eng = self.engine
        if eng is None:
            raise RuntimeError("watchdog без движка не может менять состояние")
        for task in self.candidates():
            task_id = task["task_id"]
            attempt = int(task["attempt"])
            deadline = float(task["deadline_ts"])
            out = check_watchdog(eng, task_id)
            if out is None:
                continue
            # отдельное событие смерти: «успешное завершение» и «мы отозвали
            # полномочия» не должны выглядеть одинаково
            self.store.append_event(
                f"death-{task_id}-{attempt}",
                task_id,
                "dispatch_death",
                {"attempt": attempt, "deadline": deadline, "synthetic": True},
                {"ok": True, "state": "TIMED_OUT"},
            )
            self.store.log(
                task_id,
                f"watchdog: попытка {attempt} отозвана, просрочка {round(now() - deadline, 1)}с",
                attempt=attempt,
            )
            rev = Revocation(task_id, attempt, deadline, now() - deadline)
            revoked.append(rev)
            if on_revoke:
                on_revoke(rev)
        return revoked

    def serve(self, interval: float = 15.0, cycles: int | None = None,
              emit: Callable[[dict[str, Any]], None] | None = None) -> int:
        """Цикл сторожа. Возвращает число сделанных проходов.

        Ожидание здесь — часть управляющего процесса, а не действие агента:
        оно наблюдаемо, ограничено и отменяемо (Ctrl+C).
        """
        done = 0
        try:
            while cycles is None or done < cycles:
                revoked = self.sweep()
                payload = {
                    "cycle": done + 1,
                    "at": round(now(), 3),
                    "revoked": [r.to_dict() for r in revoked],
                }
                if emit:
                    emit(payload)
                done += 1
                if cycles is not None and done >= cycles:
                    break
                time.sleep(max(1.0, interval))
        except KeyboardInterrupt:
            pass
        return done

# Точки контроля, которые реально существуют в коде (для отчёта о покрытии матрицы).
CONTROL_POINTS = {
    "watchdog.sweep": Watchdog.sweep,
    "watchdog.serve": Watchdog.serve,
}
