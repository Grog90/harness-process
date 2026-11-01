/**
 * Провайдер распознавания русской речи для голосового ввода DSH.
 *
 * Регистрируется в `ctx.speechToText` рядом со встроенным SenseVoice и добавляет
 * язык «ru». Модель — GigaAM v3 (Sber), официально сконвертированная k2-fsa в
 * формат NeMo CTC для sherpa-onnx: один ONNX-файл плюс tokens.txt, без Python и
 * без облака. Native-рантайм берётся из `sherpa-onnx-node`, которую и так ставит
 * голосовой бандл DSH.
 *
 * Инференс живёт в дочернем процессе (`worker.js`): вызов sherpa-onnx
 * синхронный, и в процессе Host он заблокировал бы UI на всё время распознавания.
 *
 * Регистрация намеренно не бросает исключений: проблема с путями или рантаймом
 * превращается в состояние подготовки `failed` с текстом причины, которое видно
 * в настройках голосового ввода, а не в «мёртвый» ряд плагина.
 */

import { spawn } from 'node:child_process';
import { appendFileSync, createWriteStream, readdirSync } from 'node:fs';
import { mkdir, rename, rm, stat } from 'node:fs/promises';
import { createRequire } from 'node:module';
import { homedir } from 'node:os';
import { dirname, isAbsolute, join } from 'node:path';
import { fileURLToPath } from 'node:url';

export const name = 'speech-to-text-gigaam';
export const inject = ['speechToText'];

/**
 * Модель и ревизия закреплены: содержимое репозитория может измениться.
 * Взят end-to-end вариант v3: он отдаёт текст сразу с пунктуацией, заглавными
 * и нормализацией чисел, поэтому второй проход через LLM не нужен.
 */
const MODEL_REPO = 'csukuangfj/sherpa-onnx-nemo-ctc-punct-giga-am-v3-russian-2025-12-16';
const MODEL_REVISION = '4fb5407ff028a69fec516cdf4c10fac9ddea7c16';

/** Файлы, без которых распознавание невозможно, в порядке подготовки. */
const ASSETS = [
  { file: 'tokens.txt', label: 'tokens.txt', minBytes: 64 },
  { file: 'model.int8.onnx', label: 'model.int8.onnx', minBytes: 50 * 1024 * 1024 },
];

/** Языковые подсказки провайдера; сервис сверяет с ними выбор пользователя. */
const LANGUAGES = ['ru', 'auto'];

const PROVIDER_ID = 'gigaam-ru';

const DEFAULT_SOURCES = ['https://huggingface.co', 'https://hf-mirror.com'];

const DEFAULTS = {
  threads: 4,
  prepareTimeoutMs: 60 * 60 * 1000,
  inferenceTimeoutMs: 120 * 1000,
  wakeTimeoutMs: 60 * 1000,
};

/** Каталог DSH: сначала переменная окружения, затем привычный ~/.dsh. */
function dshHome() {
  const fromEnv = process.env.DSH_HOME;
  if (typeof fromEnv === 'string' && fromEnv !== '') return fromEnv;
  return join(homedir(), '.dsh');
}

/**
 * Каталоги профиля: `DSH_PROFILE_DIR` может отсутствовать в окружении Host
 * (его выставляет плагин оболочек), поэтому собираем кандидатов сами.
 * @returns {string[]}
 */
function profileDirectories() {
  const candidates = [];
  const direct = process.env.DSH_PROFILE_DIR;
  if (typeof direct === 'string' && direct !== '') candidates.push(direct);
  const profile = process.env.DSH_PROFILE;
  if (typeof profile === 'string' && profile !== '') {
    candidates.push(join(dshHome(), 'profiles', profile));
  }
  return candidates;
}

/**
 * Пакеты профилей, найденные на диске: `DSH_PROFILE_DIR` в Host не выставлен,
 * поэтому идём от каталога DSH к `profiles/<имя>/package.json`.
 * @returns {string[]}
 */
function discoveredProfilePackages() {
  const found = [];
  try {
    for (const entry of readdirSync(join(dshHome(), 'profiles'), { withFileTypes: true })) {
      if (!entry.isDirectory()) continue;
      found.push(join(dshHome(), 'profiles', entry.name, 'package.json'));
    }
  } catch {
    // Нет каталога профилей — обойдёмся другими кандидатами.
  }
  return found;
}

/**
 * Пишет диагностику рядом с моделью: при разборе установки это единственный
 * способ увидеть окружение Host, не поднимая отдельный сервер.
 * @param {string} root
 * @param {string} message
 */
function diagnose(root, message) {
  try {
    appendFileSync(join(root, 'plugin.log'), `${new Date().toISOString()} ${message}\n`);
  } catch {
    // Каталог может быть недоступен — диагностика не должна мешать работе.
  }
}

/**
 * Ищет entry-point `sherpa-onnx-node`.
 *
 * Бандл подключён как `link:` на рабочую папку, а pnpm кладёт зависимости
 * такого пакета в `node_modules` профиля, поэтому bare-спецификатор от самого
 * плагина не резолвится. Перебираем профили, затем собственный путь, затем
 * обычный поиск вверх по дереву каталогов.
 * @param {string} root
 * @returns {string} абсолютный путь к модулю
 */
function resolveSherpa(root) {
  const attempts = [];
  const bases = [
    ...profileDirectories().map((dir) => join(dir, 'package.json')),
    fileURLToPath(import.meta.url),
  ];

  // Профили находим и без переменных окружения: Host их не выставляет.
  for (const base of discoveredProfilePackages()) {
    bases.push(base);
  }

  for (const base of bases) {
    try {
      return createRequire(base).resolve('sherpa-onnx-node');
    } catch (error) {
      attempts.push(`${base}: ${error instanceof Error ? error.code ?? error.message : String(error)}`);
    }
  }

  // Последний шанс: ручной подъём по дереву от каталога плагина.
  let directory = dirname(fileURLToPath(import.meta.url));
  for (let depth = 0; depth < 8; depth += 1) {
    const packageFile = join(directory, 'node_modules', 'sherpa-onnx-node', 'package.json');
    try {
      if (createRequire(packageFile)(packageFile).name === 'sherpa-onnx-node') {
        return createRequire(packageFile).resolve('sherpa-onnx-node');
      }
    } catch {
      // Продолжаем подниматься.
    }
    const parent = dirname(directory);
    if (parent === directory) break;
    directory = parent;
  }

  diagnose(root, `sherpa-onnx-node не найден; попытки: ${attempts.join(' | ')}`);
  throw new Error(
    `Не удалось найти sherpa-onnx-node. Убедитесь, что зависимости бандла установлены (${attempts.join('; ')})`,
  );
}

/**
 * Скачивает один файл с прогрессом, минуя память: тело ответа пишется на диск.
 * @param {string} url
 * @param {string} destination
 * @param {AbortSignal} signal
 * @param {(bytes: number, total?: number) => void} onProgress
 */
async function download(url, destination, signal, onProgress) {
  const response = await fetch(url, { signal, redirect: 'follow' });
  if (!response.ok || response.body === null) {
    throw new Error(`HTTP ${response.status} для ${url}`);
  }
  const total = Number(response.headers.get('content-length')) || undefined;
  const partial = `${destination}.part`;
  await rm(partial, { force: true });
  const stream = createWriteStream(partial);
  let written = 0;
  try {
    for await (const chunk of response.body) {
      if (signal.aborted) throw new Error('Загрузка отменена');
      written += chunk.length;
      if (!stream.write(chunk)) {
        await new Promise((resolve) => stream.once('drain', resolve));
      }
      onProgress(written, total);
    }
    await new Promise((resolve, reject) => {
      stream.end((error) => (error ? reject(error) : resolve()));
    });
    await rename(partial, destination);
  } catch (error) {
    stream.destroy();
    await rm(partial, { force: true });
    throw error;
  }
}

/**
 * Подготовка модели: проверка кэша, загрузка недостающего, прогрев воркера.
 * Реализует `SpeechPreparation` из сервиса: snapshot/subscribe/prepare/cancel.
 */
class Preparation {
  constructor(options) {
    this.dataRoot = options.dataRoot;
    this.sources = options.sources;
    this.prepareTimeoutMs = options.prepareTimeoutMs;
    this.onReady = options.onReady;
    this.listeners = new Set();
    this.state = options.state ?? { phase: 'unprepared' };
    this.probe = options.probe;
    this.task = null;
    this.controller = null;
  }

  snapshot() {
    return this.state;
  }

  subscribe(listener) {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  }

  /** @param {{downloadSource?: string}} [options] */
  prepare(options = {}) {
    if (this.state.phase === 'failed') {
      // Повтор после ошибки: даём шанс исправить окружение и попробовать снова.
      this.state = { phase: 'unprepared' };
    }
    if (this.task !== null) return;
    const source = options.downloadSource;
    if (source !== undefined && !this.sources.includes(source)) {
      this.#set({ phase: 'failed', message: `Источник ${source} не входит в список разрешённых` });
      return;
    }
    this.controller = new AbortController();
    const deadline = setTimeout(() => this.controller.abort(), this.prepareTimeoutMs);
    this.task = this.#run(source ?? this.sources[0], this.controller.signal)
      .catch((error) => {
        if (this.controller.signal.aborted) {
          this.#set({ phase: 'unprepared' });
          return;
        }
        const message = error instanceof Error ? error.message : String(error);
        diagnose(this.dataRoot, `prepare failed: ${message}`);
        this.#set({ phase: 'failed', message });
      })
      .finally(() => {
        clearTimeout(deadline);
        this.task = null;
        this.controller = null;
      });
  }

  async cancel() {
    this.controller?.abort();
    await this.task;
  }

  /** Проверка диска и рантайма без загрузки модели и без запуска воркера. */
  async inspect() {
    try {
      diagnose(this.dataRoot, `runtime: ${resolveSherpa(this.dataRoot)}`);
    } catch (error) {
      this.#set({
        phase: 'failed',
        message: error instanceof Error ? error.message : String(error),
      });
      return;
    }

    // Диагностический прогрев: проверяем, что воркер принимает конфигурацию и
    // поднимает модель. Стоит один раз при загрузке плагина.
    if (this.probe !== undefined) {
      try {
        await this.probe();
        diagnose(this.dataRoot, 'probe: worker готов');
      } catch (error) {
        const message = error instanceof Error ? error.message : String(error);
        diagnose(this.dataRoot, `probe failed: ${message}`);
        this.#set({ phase: 'failed', message: `Воркер не поднимает модель: ${message}` });
        return;
      }
    }

    for (const asset of ASSETS) {
      try {
        const info = await stat(join(this.dataRoot, asset.file));
        if (info.size < asset.minBytes) {
          this.#set({ phase: 'unprepared' });
          return;
        }
      } catch {
        this.#set({ phase: 'unprepared' });
        return;
      }
    }
    this.#set({ phase: 'ready' });
  }

  async #run(source, signal) {
    await mkdir(this.dataRoot, { recursive: true });

    for (const asset of ASSETS) {
      const target = join(this.dataRoot, asset.file);
      let present = false;
      try {
        present = (await stat(target)).size >= asset.minBytes;
      } catch {
        present = false;
      }
      if (present) continue;

      const url = `${source.replace(/\/$/, '')}/${MODEL_REPO}/resolve/${MODEL_REVISION}/${asset.file}`;
      this.#set({
        phase: 'downloading',
        resource: asset.label,
        completedBytes: 0,
        step: 'model',
      });
      await download(url, target, signal, (completedBytes, totalBytes) => {
        if (signal.aborted) return;
        this.#set({
          phase: 'downloading',
          resource: asset.label,
          completedBytes,
          totalBytes,
          step: 'model',
        });
      });
      const size = (await stat(target)).size;
      if (size < asset.minBytes) {
        throw new Error(`${asset.label}: файл повреждён (${size} байт)`);
      }
    }

    this.#set({ phase: 'loading', startedAt: Date.now(), step: 'load' });
    await this.onReady();
    this.#set({ phase: 'ready' });
  }

  #set(state) {
    this.state = state;
    if (state.phase === 'ready' || state.phase === 'failed') {
      diagnose(this.dataRoot, `state: ${JSON.stringify(state)}`);
    }
    for (const listener of this.listeners) {
      try {
        listener();
      } catch {
        // Наблюдатель не должен ломать подготовку.
      }
    }
  }
}

/** Один живой воркер: спавн по требованию, перезапуск после падения. */
class Worker {
  constructor(options) {
    this.paths = options.paths;
    this.dataRoot = options.dataRoot;
    this.threads = options.threads;
    this.timeoutMs = options.inferenceTimeoutMs;
    this.wakeTimeoutMs = options.wakeTimeoutMs;
    this.child = null;
    this.pending = new Map();
    this.starting = null;
  }

  async ensure() {
    if (this.child !== null) return;
    if (this.starting !== null) return this.starting;
    this.starting = this.#spawn().finally(() => {
      this.starting = null;
    });
    return this.starting;
  }

  async #spawn() {
    const sherpa = resolveSherpa(this.dataRoot);
    diagnose(this.dataRoot, `sherpa-onnx-node: ${sherpa}`);

    // Конфигурация уходит первой строкой в stdin: при спавне из Electron
    // аргумент командной строки с JSON и обратными слешами терялся.
    const config = JSON.stringify({
      model: this.paths.model,
      tokens: this.paths.tokens,
      sherpa,
      threads: this.threads,
      logPath: join(this.dataRoot, 'plugin.log'),
    });
    diagnose(this.dataRoot, `spawn: ${config}`);

    const child = spawn(
      process.execPath,
      [fileURLToPath(new URL('./worker.js', import.meta.url))],
      {
        stdio: ['pipe', 'pipe', 'pipe'],
        windowsHide: true,
        env: { ...process.env, ELECTRON_RUN_AS_NODE: '1' },
      },
    );

    child.stdin.write(`${config}\n`);

    let buffer = '';
    let stderr = '';
    let settleReady;
    const ready = new Promise((resolve, reject) => {
      settleReady = { resolve, reject };
    });

    const timer = setTimeout(() => {
      settleReady.reject(new Error(`Воркер не поднялся за ${this.wakeTimeoutMs} мс`));
      child.kill();
    }, this.wakeTimeoutMs);

    child.stderr?.on('data', (chunk) => {
      stderr = (stderr + chunk.toString()).slice(-4000);
      diagnose(this.dataRoot, `worker stderr: ${chunk.toString().trim()}`);
    });

    child.stdout?.on('data', (chunk) => {
      buffer += chunk.toString();
      let index;
      while ((index = buffer.indexOf('\n')) !== -1) {
        const line = buffer.slice(0, index).trim();
        buffer = buffer.slice(index + 1);
        if (line === '') continue;
        let message;
        try {
          message = JSON.parse(line);
        } catch {
          continue;
        }
        if (message.ready === true) {
          clearTimeout(timer);
          settleReady.resolve();
          continue;
        }
        const waiter = this.pending.get(message.id);
        if (waiter === undefined) continue;
        this.pending.delete(message.id);
        if (typeof message.error === 'string') waiter.reject(new Error(message.error));
        else waiter.resolve(message);
      }
    });

    child.once('error', (error) => {
      clearTimeout(timer);
      settleReady.reject(error);
    });

    child.once('exit', (code) => {
      clearTimeout(timer);
      this.child = null;
      const reason = new Error(
        `Воркер распознавания завершился (код ${code})${stderr ? `: ${stderr.trim()}` : ''}`,
      );
      settleReady.reject(reason);
      for (const waiter of this.pending.values()) waiter.reject(reason);
      this.pending.clear();
    });

    await ready;
    this.child = child;
  }

  /**
   * @param {Uint8Array} audio канонический WAV от моста
   * @param {AbortSignal} signal
   */
  async transcribe(audio, signal) {
    await this.ensure();
    const child = this.child;
    if (child === null) throw new Error('Воркер не запустился');

    const id = `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
    const payload = JSON.stringify({ id, wav: Buffer.from(audio).toString('base64') });

    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        this.pending.delete(id);
        this.kill();
        reject(new Error(`Распознавание не уложилось в ${this.timeoutMs} мс`));
      }, this.timeoutMs);

      const onAbort = () => {
        clearTimeout(timer);
        this.pending.delete(id);
        this.kill();
        reject(new Error('Распознавание отменено'));
      };
      signal.addEventListener('abort', onAbort, { once: true });

      this.pending.set(id, {
        resolve: (message) => {
          clearTimeout(timer);
          signal.removeEventListener('abort', onAbort);
          resolve(message);
        },
        reject: (error) => {
          clearTimeout(timer);
          signal.removeEventListener('abort', onAbort);
          reject(error);
        },
      });

      child.stdin.write(`${payload}\n`);
    });
  }

  kill() {
    const child = this.child;
    this.child = null;
    if (child === null) return;
    try {
      child.stdin.end();
      child.kill();
    } catch {
      // Процесс мог уже завершиться.
    }
  }

  dispose() {
    this.kill();
    for (const waiter of this.pending.values()) {
      waiter.reject(new Error('Провайдер остановлен'));
    }
    this.pending.clear();
  }
}

/**
 * Регистрирует провайдера и проверяет кэш модели на диске.
 * @param {import('@deepseek-ai/cordis').Context} ctx
 * @param {Record<string, unknown>} config
 */
export function apply(ctx, config = {}) {
  const settings = { ...DEFAULTS, ...config };
  const configured = typeof settings.dataRoot === 'string' && isAbsolute(settings.dataRoot);
  const dataRoot = configured ? settings.dataRoot : join(dshHome(), 'speech-to-text', 'gigaam');

  const initializationError = configured
    ? null
    : new Error(`dataRoot должен быть абсолютным путём, получено: ${String(settings.dataRoot)}`);

  diagnose(
    dataRoot,
    `apply: dataRoot=${dataRoot}, DSH_PROFILE_DIR=${String(process.env.DSH_PROFILE_DIR)}, ` +
      `DSH_PROFILE=${String(process.env.DSH_PROFILE)}, DSH_HOME=${String(process.env.DSH_HOME)}`,
  );

  const paths = {
    model: join(dataRoot, 'model.int8.onnx'),
    tokens: join(dataRoot, 'tokens.txt'),
  };

  const worker = new Worker({
    paths,
    dataRoot,
    threads: settings.threads,
    inferenceTimeoutMs: settings.inferenceTimeoutMs,
    wakeTimeoutMs: settings.wakeTimeoutMs,
  });

  const preparation = new Preparation({
    dataRoot,
    sources: settings.modelSources ?? DEFAULT_SOURCES,
    prepareTimeoutMs: settings.prepareTimeoutMs,
    onReady: () => worker.ensure(),
    probe: async () => {
      await worker.ensure();
      // Не убиваем воркер, если в этот момент уже началась запись: пусть
      // остаётся тёплым до первого реального запроса.
      if (worker.pending.size === 0) worker.kill();
    },
    state:
      initializationError === null
        ? undefined
        : { phase: 'failed', message: initializationError.message },
  });

  ctx.effect(() => {
    const unregister = ctx.speechToText.register({
      info: {
        id: PROVIDER_ID,
        name: 'GigaAM v3 · русский (локально, с пунктуацией)',
        location: 'host-local',
        languages: LANGUAGES,
        downloadSources: settings.modelSources ?? DEFAULT_SOURCES,
        setupEstimate: {
          recommendedDiskBytes: 260 * 1024 * 1024,
          expectedMemoryBytes: 700 * 1024 * 1024,
          minimumMinutes: 1,
          maximumMinutes: 20,
        },
      },
      preparation,
      transcribe: async (input, signal) => {
        if (preparation.snapshot().phase !== 'ready') {
          throw new Error('Модель GigaAM ещё не подготовлена: откройте настройки голосового ввода');
        }
        const result = await worker.transcribe(input.audio, signal);
        return {
          text: typeof result.text === 'string' ? result.text : '',
          audioSeconds: Number(result.audioSeconds) || 0,
          inferenceSeconds: Number(result.inferenceSeconds) || 0,
        };
      },
    });

    if (initializationError === null) void preparation.inspect();

    return async () => {
      const removing = unregister();
      worker.dispose();
      await removing;
    };
  });
}
