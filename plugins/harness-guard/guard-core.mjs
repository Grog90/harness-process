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

/** realpath с fallback на существующего родителя (для ещё не созданного файла). */
function realish(p) {
  try {
    return fs.realpathSync(p);
  } catch {
    try {
      return path.join(fs.realpathSync(path.dirname(p)), path.basename(p));
    } catch {
      return p;
    }
  }
}

/**
 * Путь с учётом сервисных корней: `service:<name>/<rel>` или обычный rel.
 * Абсолютный путь вне главного корня не прощается — он должен лежать под
 * объявленным сервисом; иначе остаётся outside-root.
 */
export function normalizeTarget(root, raw, services) {
  const rel = normalizePath(root, raw);
  if (rel !== '<outside-root>') return rel;
  const target = realish(path.resolve(root, String(raw)));
  for (const service of services || []) {
    const name = String(service.name || '');
    if (!name) continue;
    const base = realish(path.resolve(root, String(service.path || '')));
    const inner = path.relative(base, target).replace(/\\/g, '/');
    if (inner === '') return `service:${name}/`;
    if (!inner.startsWith('../') && inner !== '..' && !path.isAbsolute(inner)) {
      return `service:${name}/${inner}`;
    }
  }
  return '<outside-root>';
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

  if (roleName !== 'human' && roleName !== 'system') {
    const privileged = policy.privileged_tools || [];
    const prefixes = policy.privileged_prefixes || [];
    if (privileged.includes(tool) || prefixes.some((p) => tool.startsWith(p))) {
      return deny('R-TOOL-2', `инструмент '${tool}' привилегированный: рабочие роли его не получают`);
    }
  }

  const tools = role.tools || [];
  if (tool.startsWith('mcp__') && matchesAny(tool, policy.mcp_allow_globs || [])) {
    // разрешённый MCP-инструмент: читающие поисковые серверы
  } else if (!tools.includes('*') && !tools.includes(tool)
             && !tools.some((t) => t.endsWith('*') && tool.startsWith(t.slice(0, -1)))) {
    return deny('R-WORK-2', `инструмент '${tool}' не разрешён роли '${roleName}'`);
  }

  // Субагент без активной задачи не наследует права человека: иначе достаточно
  // отменить задачу, чтобы дочерний агент молча получил полный доступ.
  if (req.is_child && !req.scoped && (WRITE_TOOLS.has(tool) || tool === 'bash')) {
    return deny(
      'R-ROLE-1',
      'нет активной области прав: субагент не получает права человека вне задачи',
    );
  }

  if (tool === 'bash') {
    const bashDecision = checkBash(policy, root, roleName, role, req);
    if (bashDecision) return bashDecision;
    return allow('R-ORCH-1');
  }

  const rawPath = args.path || args.file_path || args.filePath;
  // Чтение защищённых путей разрешено: R-PROT-1 ограничивает ЗАПИСЬ, иначе агент
  // не может прочитать даже политику, по которой его же и проверяют.
  if (rawPath && WRITE_TOOLS.has(tool)) {
    const decision = checkWrite(policy, root, roleName, role, tool, rawPath,
                                req.allowed_files, req.services || []);
    if (decision) return decision;
  }
  return allow('R-PROT-1');
}

function checkWrite(policy, root, roleName, role, tool, rawPath, allowedFiles, services) {
  const rel = normalizeTarget(root, rawPath, services);
  if (rel === '<outside-root>') {
    return deny('R-PROT-1', `путь вне рабочего корня: '${rawPath}'`);
  }

  if (rel.startsWith('service:')) {
    const rest = rel.slice('service:'.length);
    const name = rest.split('/')[0];
    const inner = rest.includes('/') ? rest.slice(name.length + 1) : '';
    const service = (services || []).find((s) => String(s.name) === name);
    if (!service) return deny('R-PROT-1', `сервис '${name}' не объявлен в change`);
    if (!role.can_write_services) {
      const rule = roleName === 'verifier' ? 'R-VER-1' : 'R-PROT-1';
      return deny(rule, `${rel} — код сервиса; роль '${roleName}' не пишет в сервисы`);
    }
    if (!service.write) {
      return deny('R-PROT-1', `сервис '${name}' не открыт для записи (read-only)`);
    }
    if (!inner || inner.split('/').includes('..')) {
      return deny('R-PROT-1', `недопустимый путь внутри сервиса: '${rel}'`);
    }
    const scope = allowedFiles || [];
    if (scope.length === 0) {
      return deny('R-WORK-1', 'воркер запущен без allowed_files: писать некуда — это дефект брифа');
    }
    if (!matchesAny(rel, scope)) {
      return deny('R-WORK-1', `${rel} вне allowed_files брифа`);
    }
    // Сервис внутри главного корня не обходит protected/spec/product: те же
    // правила проверяются по главному относительному пути.
    const rootReal = realish(path.resolve(root));
    const baseReal = realish(path.resolve(root, String(service.path || '')));
    if (baseReal === rootReal || baseReal.startsWith(rootReal + path.sep)) {
      const targetReal = realish(path.join(baseReal, inner));
      const mainRel = path.relative(rootReal, targetReal).replace(/\\/g, '/');
      if (mainRel && !mainRel.startsWith('..') && !path.isAbsolute(mainRel)) {
        if (matchesAny(mainRel, policy.spec_globs || []) && roleName !== 'human'
            && roleName !== 'system' && !role.can_write_specs) {
          return deny('R-WORK-3', `${mainRel} — каталог спеки; роль '${roleName}' не пишет план`);
        }
        if (matchesAny(mainRel, policy.protected_globs || []) && roleName !== 'system'
            && !matchesAny(mainRel, role.can_write || [])) {
          return deny('R-PROT-1', `${mainRel} — защищённый путь, запись ролью '${roleName}' запрещена`);
        }
        if (matchesAny(mainRel, policy.product_globs || []) && !role.can_write_product
            && !matchesAny(mainRel, role.product_write_globs || [])) {
          return deny('R-PROT-1', `${mainRel} — продуктовый код; роль '${roleName}' не имеет права записи`);
        }
      }
    }
    return null;
  }

  const protectedGlobs = policy.protected_globs || [];
  if (matchesAny(rel, policy.spec_globs || []) && roleName !== 'human' && roleName !== 'system'
      && !role.can_write_specs) {
    return deny('R-WORK-3', `${rel} — каталог спеки; роль '${roleName}' не пишет план`);
  }
  if (matchesAny(rel, protectedGlobs) && roleName !== 'system') {
    if (!matchesAny(rel, role.can_write || [])) {
      return deny('R-PROT-1', `${rel} — защищённый путь, запись ролью '${roleName}' запрещена`);
    }
  }

  if (matchesAny(rel, policy.product_globs || []) && !role.can_write_product) {
    if (!matchesAny(rel, role.product_write_globs || [])) {
      const rule = roleName === 'orchestrator' ? 'R-ORCH-1'
        : (roleName === 'analyst' || roleName === 'qa' ? 'R-PROT-1' : 'R-VER-1');
      return deny(rule, `${rel} — продуктовый код; роль '${roleName}' не имеет права записи`);
    }
  }

  if (role.write_scope_from_brief && WRITE_TOOLS.has(tool)) {
    const scope = allowedFiles || [];
    if (scope.length === 0) {
      return deny('R-WORK-1', 'воркер запущен без allowed_files: писать некуда — это дефект брифа');
    }
    if (!matchesAny(rel, scope)) {
      return deny('R-WORK-1', `${rel} вне allowed_files брифа`);
    }
    const writeGlobs = role.can_write || [];
    if (writeGlobs.length > 0 && !matchesAny(rel, writeGlobs)) {
      return deny('R-PROT-1', `${rel} не входит в разрешённую область записи роли '${roleName}'`);
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

// Разрушительный git: подкоманда ищется в любом месте строки, потому что между
// `git` и `reset` могут стоять опции (`-C dir`, `--worktree`).
const DESTRUCTIVE_GIT = [
  [/\bgit\b[^\n]*?\bpush\b[^\n]*?(?:--force\b|\s-f\b)/i, 'git push --force'],
  [/\bgit\b[^\n]*?\breset\s+--hard\b/i, 'git reset --hard'],
  [/\bgit\b[^\n]*?\bclean\s+-[a-z]*f/i, 'git clean -f'],
  [/\bgit\b[^\n]*?\bcheckout\s+--\s+\./i, 'git checkout -- .'],
  [/\bgit\b[^\n]*?\brestore\s+\./i, 'git restore .'],
  [/\bgit\b[^\n]*?\bbranch\s+-D/, 'git branch -D'],
];
const NAMED_TREE = /\bgit\s+(-C\s+\S+|--worktree[= ]\S+|--git-dir[= ]\S+)/i;

function checkBash(policy, root, roleName, role, req) {
  const command = String((req.args && (req.args.command || req.args.cmd)) || '');
  if (!command) return null;

  const namedTree = NAMED_TREE.test(command);
  for (const [rx, label] of DESTRUCTIVE_GIT) {
    if (!rx.test(command)) continue;
    if (!namedTree) {
      return deny('R-GIT-1', `${label} без явно названного дерева: работа может быть потеряна`);
    }
    return advise('R-GIT-1', `${label}: дерево названо, но работу надо сохранить до отката`);
  }

  const candidates = [];
  // `>` внутри кавычек — текст, а не перенаправление
  const scan = command.replace(/'[^']*'|"[^"]*"/g, "''");
  let m;
  REDIRECT_RE.lastIndex = 0;
  while ((m = REDIRECT_RE.exec(scan)) !== null) {
    const tgt = m[1];
    if (['/dev/null', '&1', '&2', 'NUL', 'nul'].includes(tgt)) continue;
    candidates.push(tgt);
  }
  const sed = SED_INPLACE_RE.exec(scan);
  if (sed) candidates.push(sed[2]);
  const cp = CP_MV_RE.exec(scan);
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
