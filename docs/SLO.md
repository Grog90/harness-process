# SLO процесса

Пороги заданы в `harness/slo.py` и проверяются командой:

```powershell
python -m harness slo
python -m harness slo --strict   # exit 2 при нарушении
python -m harness --json slo
```

| SLO | Порог | Откуда |
|---|---|---|
| `acceptance_rate` | ≥ 0.5 | принятые / все change |
| `rework_rate` | ≤ 0.5 | попытки |
| `verification_pass` | ≥ 0.9 | успешные / все verification |
| `open_findings` | ≤ 10 | открытые находки |

Пустая выборка — `n/a`, а не «успех»: нельзя пройти SLO, не имея данных.
`--strict` возвращает 2 только при реальном нарушении, не при `n/a`.

## Evals

`python -m harness evals run --cases <dir> --verifier <cmd>` измеряет сам
верификатор (пропуски/ложные отказы/неполные проверки). Это отдельный контроль;
SLO его не заменяет.
