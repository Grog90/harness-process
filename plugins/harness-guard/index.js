/**
 * Host-плагин: ограждение инструментов решениями harness.
 *
 * Точка крепления — `ctx.tools.guard()`: синхронная проверка, которую нельзя
 * обойти другим плагином (guard может только запретить; чужой запрет не отменить).
 *
 * Чьи права применяются (первое найденное):
 *   1. `.harness/guard-scope.json` — активная задача: роль и allowed_files
 *      публикует управляющий процесс при диспетче, снимает при вердикте.
 *   2. `config.role` из патча.
 *   3. `HARNESS_ROLE` из окружения.
 *   4. `human` — интерактивная сессия без задачи. Это осознанный дефолт: между
 *      задачами агент работает как руки человека, а не как воркер. Рабочие роли
 *      включаются только активной задачей, чтобы профиль не превращался в кирпич.
 *
 * Где ищется политика (первое найденное):
 *   `config.root` → `HARNESS_ROOT` → указатель `~/.dsh/harness-root` →
 *   текущий каталог → до четырёх уровней вверх.
 *
 * Если политика не найдена: чтение разрешено, запись и команды запрещены с
 * `R-HOOK-ERR`. Так сессия остаётся осматриваемой, но не даёт менять файлы
 * в неизвестных правилах.
 */

import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { decide, loadPolicy, canonicalTool } from './guard-core.mjs';

export const name = 'harness-guard';
export const inject = ['tools'];

const MUTATING = new Set(['write_file', 'bash']);

const scopeCache = { mtimeMs: -1, value: null };
const policyCache = { key: '', mtimeMs: -1, value: null };

function hasPolicy(dir) {
  try {
    return fs.statSync(path.join(dir, 'policies', 'policy.json')).isFile();
  } catch {
    return false;
  }
}

/** Указатель на корень проекта: один файл с путём, без переменных окружения. */
function readPointer() {
  const home = process.env.DSH_HOME || path.join(os.homedir(), '.dsh');
  const file = path.join(home, 'harness-root');
  try {
    const value = fs.readFileSync(file, 'utf8').trim();
    return value ? path.resolve(value) : null;
  } catch {
    return null;
  }
}

function resolveRoot(config) {
  const tried = [];
  const candidates = [];
  if (config?.root) candidates.push(path.resolve(config.root));
  if (process.env.HARNESS_ROOT) candidates.push(path.resolve(process.env.HARNESS_ROOT));
  const pointer = readPointer();
  if (pointer) candidates.push(pointer);
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

function readGap(root) {
  try {
    return JSON.parse(fs.readFileSync(path.join(root, '.harness', 'gap.json'), 'utf8'));
  } catch {
    return null;
  }
}

export function apply(ctx, config) {
  const resolved = resolveRoot(config);

  const guard = (exec) => {    const tool = canonicalTool(exec?.name);
    try {
      if (!resolved.root) {
        // политики нет: осматривать можно, менять — нет
        if (!MUTATING.has(tool)) return undefined;
        const tried = resolved.tried.slice(0, 5).join(', ');
        return `R-HOOK-ERR: harness-guard не нашёл policies/policy.json (искал: ${tried}). `
          + 'Укажите корень проекта: harness --root <проект> plugin prepare или HARNESS_ROOT';
      }

      const root = resolved.root;
      const policy = readPolicy(root);
      const scope = readScope(root);
      const agentId = String(exec?.agent?.id || '');
      // Роль зависит от агента, а не от профиля: оркестратор (primary) не пишет
      // продукт, дочерние агенты получают роль фазы (worker/verifier).
      let role = 'human';
      if (scope) {
        const primary = String(scope.primary || '');
        role = agentId && primary && agentId === primary
          ? 'orchestrator'
          : String(scope.role || 'worker');
      } else if (config?.role || process.env.HARNESS_ROLE) {
        role = config?.role || process.env.HARNESS_ROLE;
      }
      const allowedFiles = Array.isArray(scope?.allowed_files)
        ? scope.allowed_files
        : config?.allowedFiles || envAllowedFiles();
      const services = Array.isArray(scope?.services) ? scope.services : [];

      const decision = decide(policy, root, {
        role,
        tool: exec?.name,
        args: exec?.arguments || {},
        allowed_files: allowedFiles,
        services,
        scoped: Boolean(scope),
        // без активной задачи дочерний агент не наследует права человека
        is_child: Boolean(exec?.parent) || (Boolean(scope) && Boolean(agentId) && agentId !== String(scope.primary || '')),
      });
      if (decision.decision === 'deny') {
        return `${decision.rule}: ${decision.reason}`;
      }
      return undefined;
    } catch (error) {
      if (!MUTATING.has(tool)) return undefined;
      return `R-HOOK-ERR: harness-guard не смог проверить вызов (${error.message})`;
    }
  };

  const disposeGuard = ctx.tools.guard(guard);

  // GAP → ask: пишущий вызов при открытом вопросе спрашивает человека, а не
  // проходит молча. Guard (ниже по конвейеру) всё равно проверит права.
  const disposeAsk = typeof ctx.on === 'function'
    ? ctx.on('tools/pre-execute', async (exec, next) => {
      try {
        if (!resolved.root) return next();
        const gap = readGap(resolved.root);
        if (!gap) return next();
        const tool = canonicalTool(exec?.name);
        if (!MUTATING.has(tool)) return next();
        const question = String(gap.question || 'открытый вопрос');
        return {
          kind: 'ask',
          reason: `R-GAP-2: ${question}`,
          displayReason: { en: `Open question: ${question}`, ru: `Открытый вопрос: ${question}` },
        };
      } catch {
        return next();
      }
    })
    : null;

  return () => {
    if (typeof disposeAsk === 'function') disposeAsk();
    disposeGuard();
  };
}
