/**
 * Host-плагин: ограждение инструментов решениями harness.
 *
 * Точка крепления — `ctx.tools.guard()`: синхронная проверка, которую нельзя
 * обойти другим плагином (guard может только запретить и не может разрешить
 * то, что запретил другой guard). Именно этого не хватало CLI-версии: там агент
 * мог просто не пойти через шлюз.
 *
 * Откуда берутся права:
 *   1. `.harness/guard-scope.json` — публикует управляющий процесс при диспетче
 *      и снимает при вердикте/таймауте. Это единственный источник, который агент
 *      не может подделать: файл защищён политикой (R-PROT-1).
 *   2. Переменные окружения HARNESS_* — запасной вариант для headless-прогонов.
 *
 * Всё, что требует состояния (автомат, бюджет, вердикты), остаётся в Python:
 * guard отвечает только за «можно ли это действие», и отвечает быстро.
 */

import fs from 'node:fs';
import path from 'node:path';
import { decide, loadPolicy } from './guard-core.mjs';

export const name = 'harness-guard';
export const inject = ['tools'];

/** Кэш области прав по mtime: чтение на каждый инструмент, но без лишних syscall-нагрузок. */
const scopeCache = { mtimeMs: -1, value: null };

function readScope(root) {
  const file = path.join(root, '.harness', 'guard-scope.json');
  try {
    const stat = fs.statSync(file);
    if (stat.mtimeMs !== scopeCache.mtimeMs) {
      scopeCache.mtimeMs = stat.mtimeMs;
      scopeCache.value = JSON.parse(fs.readFileSync(file, 'utf8'));
    }
    return scopeCache.value;
  } catch {
    scopeCache.mtimeMs = -1;
    scopeCache.value = null;
    return null;
  }
}

function envAllowedFiles() {
  const raw = process.env.HARNESS_ALLOWED_FILES;
  if (!raw) return [];
  try {
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}

export function apply(ctx, config) {
  const root = path.resolve(config?.root || process.env.HARNESS_ROOT || process.cwd());
  let policy;
  try {
    policy = loadPolicy(path.join(root, 'policies', 'policy.json'));
  } catch (error) {
    // Без политики плагин не должен молча разрешать: он честно падает при загрузке.
    throw new Error(`harness-guard: не удалось загрузить политику из ${root}: ${error.message}`);
  }

  const guard = (exec) => {
    try {
      const scope = readScope(root);
      const role = scope?.role || config?.role || process.env.HARNESS_ROLE || 'worker';
      const allowedFiles = Array.isArray(scope?.allowed_files)
        ? scope.allowed_files
        : config?.allowedFiles || envAllowedFiles();

      const decision = decide(policy, root, {
        role,
        tool: exec?.name,
        args: exec?.arguments || {},
        allowed_files: allowedFiles,
      });
      if (decision.decision === 'deny') {
        return `${decision.rule}: ${decision.reason}`;
      }
      return undefined;
    } catch (error) {
      // fail-closed: сбой проверки закрывает действие, а не открывает его
      return `R-HOOK-ERR: harness-guard не смог проверить вызов (${error.message})`;
    }
  };

  // guard() сам регистрирует эффект на контексте плагина: при выгрузке
  // плагина регистрация снимается вместе с ним
  return ctx.tools.guard(guard);
}
