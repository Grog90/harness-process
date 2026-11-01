/**
 * Дочерний процесс распознавания: держит модель GigaAM v3 в памяти и обслуживает
 * построчные JSON-запросы по stdin/stdout.
 *
 * Почему отдельный процесс: инференс sherpa-onnx синхронный и нативной сборкой
 * блокирует поток. В процессе Host он заморозил бы UI и все сессии на время
 * распознавания, поэтому модель живёт здесь.
 *
 * Протокол:
 *   -> {"model":"...","tokens":"...","sherpa":"...","threads":4}   (первая строка)
 *   <- {"ready":true}
 *   -> {"id":"...","wav":"<base64 WAV 16 kHz mono PCM16>"}
 *   <- {"id":"...","text":"...","audioSeconds":3.2,"inferenceSeconds":0.4}
 *   <- {"id":"...","error":"..."}
 *
 * Конфигурация идёт по stdin, а не в argv: при спавне из Electron аргумент с
 * JSON и обратными слешами терялся, и процесс получал пустой путь к модели.
 */

import { createRequire } from 'node:module';
import { appendFileSync } from 'node:fs';
import { createInterface } from 'node:readline';

let recognizer = null;
let queue = Promise.resolve();
let logPath = null;

/**
 * Диагностика воркера в тот же журнал, что пишет Host.
 * @param {string} message
 */
function log(message) {
  if (logPath === null) return;
  try {
    appendFileSync(logPath, `${new Date().toISOString()} worker: ${message}\n`);
  } catch {
    // Журнал не должен мешать распознаванию.
  }
}

/**
 * Достаёт PCM из WAV, который уже проверен мостом как канонический 16 кГц mono PCM16.
 * Разбираем чанки, а не смещение 44: некоторые кодировщики вставляют LIST/INFO.
 * @param {Buffer} wav
 * @returns {Float32Array} сэмплы в диапазоне [-1, 1]
 */
function pcm16(wav) {
  if (wav.length < 44) throw new Error('WAV too short');
  if (wav.toString('ascii', 0, 4) !== 'RIFF' || wav.toString('ascii', 8, 12) !== 'WAVE') {
    throw new Error('Not a RIFF/WAVE file');
  }
  let offset = 12;
  while (offset + 8 <= wav.length) {
    const id = wav.toString('ascii', offset, offset + 4);
    const size = wav.readUInt32LE(offset + 4);
    if (id === 'data') {
      const start = offset + 8;
      const length = Math.min(size, wav.length - start);
      const samples = new Float32Array(Math.floor(length / 2));
      for (let i = 0; i < samples.length; i += 1) {
        samples[i] = wav.readInt16LE(start + i * 2) / 32768;
      }
      return samples;
    }
    offset += 8 + size + (size % 2);
  }
  throw new Error('WAV has no data chunk');
}

/**
 * Загружает модель; вызывается один раз на процесс.
 * @param {Record<string, unknown>} config
 */
function load(config) {
  logPath = typeof config.logPath === 'string' ? config.logPath : null;
  log(
    `config model=${JSON.stringify(config.model)} tokens=${JSON.stringify(config.tokens)} ` +
      `sherpa=${JSON.stringify(config.sherpa)} threads=${JSON.stringify(config.threads)}`,
  );

  if (typeof config.model !== 'string' || config.model === '') {
    throw new Error('В конфигурации воркера нет пути к модели');
  }
  if (typeof config.tokens !== 'string' || config.tokens === '') {
    throw new Error('В конфигурации воркера нет пути к tokens.txt');
  }
  if (typeof config.sherpa !== 'string' || config.sherpa === '') {
    throw new Error('В конфигурации воркера нет пути к sherpa-onnx-node');
  }

  const sherpa = createRequire(import.meta.url)(config.sherpa);
  recognizer = new sherpa.OfflineRecognizer({
    featConfig: {
      sampleRate: 16000,
      // GigaAM v3 считает 64-мерные HTK-мел-признаки; метаданные ONNX уточняют это сами.
      featureDim: config.featureDim ?? 64,
    },
    modelConfig: {
      // GigaAM сконвертирована k2-fsa в формат NeMo CTC: один ONNX + tokens.txt.
      nemoCtc: { model: config.model },
      tokens: config.tokens,
      numThreads: config.threads ?? 4,
      provider: 'cpu',
      debug: 0,
    },
  });
}

/**
 * Распознаёт одну запись целиком.
 * @param {Buffer} wav
 * @returns {{text: string, audioSeconds: number, inferenceSeconds: number}}
 */
function transcribe(wav) {
  const samples = pcm16(wav);
  const audioSeconds = samples.length / 16000;
  if (samples.length === 0) return { text: '', audioSeconds: 0, inferenceSeconds: 0 };

  const started = performance.now();
  const stream = recognizer.createStream();
  stream.acceptWaveform({ sampleRate: 16000, samples });
  recognizer.decode(stream);
  const text = recognizer.getResult(stream).text.trim();

  return {
    text,
    audioSeconds,
    inferenceSeconds: (performance.now() - started) / 1000,
  };
}

/** Первая строка stdin — конфигурация, дальше идут запросы на распознавание. */
let configured = false;

const lines = createInterface({ input: process.stdin, crlfDelay: Infinity });

lines.on('line', (line) => {
  if (line.trim() === '') return;

  if (!configured) {
    configured = true;
    try {
      load(JSON.parse(line));
    } catch (error) {
      process.stderr.write(`${error instanceof Error ? error.message : String(error)}\n`);
      process.exit(2);
    }
    process.stdout.write('{"ready":true}\n');
    return;
  }

  queue = queue.then(() => {
    let request;
    try {
      request = JSON.parse(line);
    } catch {
      process.stdout.write(`${JSON.stringify({ id: null, error: 'Malformed request' })}\n`);
      return;
    }
    try {
      const result = transcribe(Buffer.from(request.wav ?? '', 'base64'));
      process.stdout.write(`${JSON.stringify({ id: request.id, ...result })}\n`);
    } catch (error) {
      process.stdout.write(
        `${JSON.stringify({ id: request.id, error: error instanceof Error ? error.message : String(error) })}\n`,
      );
    }
  });
});

lines.on('close', () => {
  queue.finally(() => process.exit(0));
});
