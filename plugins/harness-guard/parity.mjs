/**
 * Проверка паритета: те же случаи через JS-решатель плагина.
 *
 * Читает JSON {policy_path, root, cases[]} из stdin, печатает массив решений.
 * Используется тестом tests/test_parity.py, который сравнивает их с ответами
 * Python-ядра (harness/hooks.py + policy.py).
 */

import fs from 'node:fs';
import { decide, loadPolicy } from './guard-core.mjs';

const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const policy = loadPolicy(input.policy_path);

const results = input.cases.map((item) => {
  try {
    return decide(policy, input.root, item);
  } catch (error) {
    return { decision: 'deny', rule: 'R-HOOK-ERR', reason: String(error.message || error) };
  }
});

process.stdout.write(JSON.stringify(results));
