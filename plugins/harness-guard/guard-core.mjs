/**
 * Решатель для guard'а: те же правила, что в harness/policy.py и harness/hooks.py,
 * но на JavaScript и без побочных эффектов.
 *
 * Почему копия, а не вызов Python: `ctx.tools.guard()` синхронный, а спавн процесса
 * на каждый вызов инструмента — это десятки миллисекунд блокировки. Поэтому здесь
 * живёт только «горячая» часть: роли, пути, запреты. Всё, что требует состояния
 * (автомат, бюджет, вердикты), остаётся в Python и вызывается асинхронно.
 *
 * Паритет с Python проверяется тестом tests/test_parity.py на общей матрице случаев.
 */

import fs from 'node:fs';
import path from 'node:path';

/** Инструменты DSH → канонические имена правил. */
export const TOOL_ALIASES = {
  write: 'write_file',
  edit: 'write_file',
  str_replace_editor: 'write_file',
  create_file: 'write_file',
  apply_patch: 'write_file',
  read: 'read_file',
  glob: 'read_file',
  grep: 'read_file',
  pwsh: 'bash',
  bash: 'bash',
  terminal: 'bash',
};

export function canonicalTool(name) {
  const key = String(name || '');
  return TOOL_ALIASES[key] || key;
}

export function loadPolicy(policyPath) {
  const raw = fs.readFileSync(policyPath, 'utf8');
  const data = JSON.parse(raw);
  if (!data.roles || Object.keys(data.roles).length === 0) {
    throw new Error('в политике нет ни одной роли');
  }
  return data;
}

/** Приводит путь к виду «относительно корня, через слэши». Выход из корня помечается. */
export function normalizePath(root, raw) {
  if (!raw) throw new Error('пустой путь');
  let p = String(raw).replace(/\\/g, '/');
  const isAbs = p.startsWith('/') || /^[A-Za-z]:/.test(p);
  if (isAbs) {
    const rel = path.relative(path.resolve(root), path.resolve(p)).replace(/\\/g, '/');
    p = rel;
  }
  // аналог posix normpath
  const parts = [];
  for (const seg of p.split('/')) {
    if (!seg || seg === '.') continue;
    if (seg === '..') {
      if (parts.length === 0) return '<outside-root>';
      parts.pop();
      continue;
    }
    parts.push(seg);
  }
  const normalized = parts.join('/');
  return normalized === '' ? '.' : normalized;
}

/** Сопоставление с глобами: '*' захватывает '/', регистр не важен (как в Python). */
export function matchesAny(relPath, globs) {
  const low = String(relPath).toLowerCase();
  for (const g of globs || []) {
    const glob = String(g).toLowerCase();
    const rx = globToRegex(glob.endsWith('/') ? glob + '*' : glob);
    if (rx.test(low)) return true;
  }
  return false;
}

function globToRegex(glob) {
  let out = '^';
  for (const ch of glob) {
    if (ch === '*') out += '.*';
    else if (ch === '?') out += '.';
    else out += ch.replace(/[.+^${}()|[\]\\]/g, '\\$&');
  }
  return new RegExp(out + '$');
}

const WRITE_TOOLS = new Set(['write_file', 'edit_file', 'apply_patch', 'create_file']);

function allow(rule = '') {
  return { decision: 'allow', rule, reason: '' };
}
function deny(rule, reason) {
  return { decision: 'deny', rule, reason };
}
function advise(rule, reason) {
  return { decision: 'advise', rule, reason };
}

/**
 * Один вызов инструмента → решение.
 * @param {object} policy загруженный policies/policy.json
 * @param {string} root корень проекта
 * @param {{role?: string, claimed_role?: string, tool: string, args?: object, allowed_files?: string[]}} req
 */
export function decide(policy, root, req) {
  const roleName = req.role || '';
  const tool = canonicalTool(req.tool);
  const args = req.args || {};
  const claimed = req.claimed_role;

  if (!roleName) return deny('R-ROLE-1', 'роль не выдана запускающим процессом');
  if (claimed && claimed !== roleName) {
    return deny('R-ROLE-1', `заявленная роль '${claimed}' не совпадает с выданной '${roleName}'`);
  }
  const role = policy.roles[roleName];
  if (!role) return deny('R-ROLE-1', `неизвестная роль: '${roleName}'`);

  const tools = role.tools || [];
  if (!tools.includes('*') && !tools.includes(tool)) {
    return deny('R-WORK-2', `инструмент '${tool}' не разрешён роли '${roleName}'`);
  }

  if (tool === 'bash') {
    const bashDecision = checkBash(policy, root, roleName, role, req);
    if (bashDecision) return bashDecision;
    return allow('R-ORCH-1');
  }

  const rawPath = args.path || args.file_path || args.filePath;
  if (rawPath) {
    const decision = checkWrite(policy, root, roleName, role, tool, rawPath, req.allowed_files);
    if (decision) return decision;
  }
  return allow('R-PROT-1');
}

function checkWrite(policy, root, roleName, role, tool, rawPath, allowedFiles) {
  const rel = normalizePath(root, rawPath);
  if (rel === '<outside-root>') {
    return deny('R-PROT-1', `путь вне рабочего корня: '${rawPath}'`);
  }

  const protectedGlobs = policy.protected_globs || [];
  if (matchesAny(rel, protectedGlobs) && roleName !== 'system') {
    if (!matchesAny(rel, role.can_write || [])) {
      return deny('R-PROT-1', `${rel} — защищённый путь, запись ролью '${roleName}' запрещена`);
    }
  }

  if (matchesAny(rel, policy.product_globs || []) && !role.can_write_product) {
    const rule = roleName === 'orchestrator' ? 'R-ORCH-1' : 'R-VER-1';
    return deny(rule, `${rel} — продуктовый код; роль '${roleName}' не имеет права записи`);
  }

  if (role.write_scope_from_brief && WRITE_TOOLS.has(tool)) {
    const scope = allowedFiles || [];
    if (scope.length === 0) {
      return deny('R-WORK-1', 'воркер запущен без allowed_files: писать некуда — это дефект брифа');
    }
    if (!matchesAny(rel, scope)) {
      return deny('R-WORK-1', `${rel} вне allowed_files брифа`);
    }
    return null;
  }

  if (!matchesAny(rel, role.can_write || [])) {
    return deny('R-PROT-1', `${rel} не входит в разрешённую область записи роли '${roleName}'`);
  }
  return null;
}

const REDIRECT_RE = /(?:>>?|\btee\b(?:\s+-a)?)\s*([^\s;&|"']+)/gi;
const SED_INPLACE_RE = /sed\s+(-i\b|-i\.?\S*)\s+(?:-e\s+)?(?:'[^']*'|"[^"]*"|\S+)\s+([^\s;&|]+)/i;
const CP_MV_RE = /\b(?:cp|mv|install|rsync)\b[^;|&]*\s+([^\s;&|]+)\s*$/i;

function checkBash(policy, root, roleName, role, req) {
  const command = String((req.args && (req.args.command || req.args.cmd)) || '');
  if (!command) return null;

  const candidates = [];
  let m;
  REDIRECT_RE.lastIndex = 0;
  while ((m = REDIRECT_RE.exec(command)) !== null) {
    const tgt = m[1];
    if (['/dev/null', '&1', '&2', 'NUL', 'nul'].includes(tgt)) continue;
    candidates.push(tgt);
  }
  const sed = SED_INPLACE_RE.exec(command);
  if (sed) candidates.push(sed[2]);
  const cp = CP_MV_RE.exec(command);
  if (cp) candidates.push(cp[1]);

  for (const cand of candidates) {
    if (!cand || cand.startsWith('-') || cand.includes('$')) continue;
    const rel = normalizePath(root, cand);
    if (rel === '<outside-root>') return deny('R-PROT-1', `запись вне корня через shell: '${cand}'`);
    if (matchesAny(rel, policy.product_globs || [])) {
      const decision = checkWrite(policy, root, roleName, role, 'write_file', rel, req.allowed_files);
      if (decision) return deny(decision.rule, `обход через shell в ${rel}: ${decision.reason}`);
    }
  }
  return null;
}
