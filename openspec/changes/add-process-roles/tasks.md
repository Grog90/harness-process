# Tasks

- [x] 1.1 Роли политики и права: `policies/policy.json` (analyst, qa, spec/privileged/mcp), `harness/policy.py` (R-WORK-3, R-TOOL-2, QA-исключение), `harness/hooks.py`; проверка `python -m unittest tests.test_parity -v`
- [x] 1.2 Роль из брифа: `harness/brief.py` (actor_role), `harness/specbrief.py` (actor_role), `harness/fsm.py` (PROCESS_ROLES, область прав), `schemas/brief.schema.json`; проверка `python -m unittest tests.test_changefsm tests.test_harness -v`
- [x] 1.3 Guard и GAP: `plugins/harness-guard/guard-core.mjs`, `plugins/harness-guard/index.js` (ask на pre-execute), `harness/gap.py`, `harness/cli.py` (gap ask/clear/show); проверка `python -m unittest tests.test_plugin_guard tests.test_parity -v`
- [x] 1.4 Правила и проверки: `harness/selftest.py`, `policies/rules.json`, `docs/RULES_MATRIX.md`; проверка `python -m harness selftest && python -m harness coverage`
- [x] 1.5 Пресеты и скиллы: `plugins/process-presets/cordis.patch.yml`, `skills/process-ba/SKILL.md`, `skills/process-sa/SKILL.md`, `skills/process-dev/SKILL.md`, `skills/process-qa/SKILL.md`, `skills/process-verifier/SKILL.md`; проверка `python -m unittest tests.test_skills_contract -v`
