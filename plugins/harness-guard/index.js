/**
 * Host-плагин: ограждение инструментов решениями harness.
 *
 * Точка крепления — `ctx.tools.guard()`: синхронная проверка, которую нельзя
 * обойти другим плагином (guard может только запретить; разрешение другого
 * guard'а не отменяет запрет). Именно этого не хватало CLI-версии: там агент
 * мог просто не пойти через шлюз.
 *
 * Откуда берутся права:
 *   1. `.harness/guard-scope.json` — публикует управляющий процесс при диспетче
 *      и снимает при вердикте/таймауте. Файл защищён политикой (R-PROT-1).
 *   2. Переменные окружения HARNESS_* — запасной вариант для headless-прогонов.
 *
 * Поведение при отсутствии политики: плагин НЕ падает при активации (иначе он
 * ломает профиль), но и не разрешает ничего — все вызовы получают отказ
 * R-HOOK-ERR с указанием, где искали. Это fail-closed без побочного ущерба.
 */

import fs from 'node:fs';
import path from 'node:path';
import { decide, loadPolicy } from './guard-core.mjs';

export const name = 'harness-guard';
export const inject = ['tools'];

/** Кэш области прав по mtime: чтение на каждый инструмент, но без лишних syscall-нагрузок. */
const scopeCache = { mtimeMs: -1, value: null };
/** Кэш политики: путь + mtime, чтобы правка policy.json подхватывалась без перезапуска. */
const policyCache = { key: '', mtimeMs: -1, value: null };

function hasPolicy(dir) {
  try {
    return fs.statSync(path.join(dir, 'policies', 'policy.json')).isFile();
  } catch {
    return false;
  }
}

/** Ищет корень проекта: конфиг → env → текущий каталог → вверх по дереву. */
function resolveRoot(config) {
  const tried = [];
  const candidates = [];
  if (config?.root) candidates.push(path.resolve(config.root));
  if (process.env.HARNESS_ROOT) candidates.push(path.resolve(process.env.HARNESS_ROOT));
  candidates.push(path.resolve(process.cwd()));

  for (const candidate of candidates) {
    tried.push(candidate);
    if (hasPolicy(candidate)) return { root: candidate, tried };
  }

  let dir = path.resolve(process.cwd());
  for (let depth = 0; depth < 4; depth += 1) {
    const parent = path.dirname(dir);
    if (parent === dir) break;
    dir = parent;
    tried.push(dir);
    if (hasPolicy(dir)) return { root: dir, tried };
  }
  return { root: null, tried };
}

function readPolicy(root) {
  const file = path.join(root, 'policies', 'policy.json');
  const stat = fs.statSync(file);
  if (policyCache.key !== file || policyCache.mtimeMs !== stat.mtimeMs) {
    policyCache.value = loadPolicy(file);
    policyCache.key = file;
    policyCache.mtimeMs = stat.mtimeMs;
  }
  return policyCache.value;
}

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
  const resolved = resolveRoot(config);

  const guard = (exec) => {
    try {
      if (!resolved.root) {
        const tried = resolved.tried.slice(0, 5).join(', ');
        return `R-HOOK-ERR: harness-guard не нашёл policies/policy.json (искал: ${tried})`;
      }
      const root = resolved.root;
      const policy = readPolicy(root);
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
