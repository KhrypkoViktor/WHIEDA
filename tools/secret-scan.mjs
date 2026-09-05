/**
 * Locate secret-shaped content without ever printing it.
 *
 * The inventory report says private keys and passwords sit in historical files.
 * Reading them to "confirm" would spread them further - into a terminal buffer,
 * a log, a conversation. So this reports only where and what kind, never the
 * value.
 *
 * It also separates the two cases that matter. A line that reads its value from
 * the environment holds no secret; a line with the value written into it does,
 * and if that file is tracked by Git the value is already in history. Deleting
 * the file does not remove it: it has to be treated as disclosed and rotated.
 *
 * Usage:
 *   node tools/secret-scan.mjs [--root <path>]
 *
 * Exit 1 when literal values are found, so it can gate a package.
 */

import { execFileSync } from 'node:child_process';
import { readFileSync, readdirSync, statSync } from 'node:fs';
import path from 'node:path';

const args = process.argv.slice(2);
const rootArg = args.indexOf('--root');
const root = path.resolve(rootArg === -1 ? '.' : args[rootArg + 1]);

// Third-party sources are not our secrets: a library's own docstrings and test
// URLs match every pattern here and would bury the real findings under noise.
const SKIP_DIRS = new Set([
  '.git', 'node_modules', 'dist', '.astro', '.cache', '__pycache__',
  'venv', '.venv', 'site-packages', '.tox', 'vendor', '.pytest_cache',
]);
const TEXT_EXT = new Set([
  '.md', '.txt', '.json', '.yml', '.yaml', '.env', '.conf', '.cfg', '.ini',
  '.js', '.mjs', '.cjs', '.ts', '.py', '.sh', '.ps1', '.sql', '.tsv', '.csv',
  '.pem', '.key', '.astro',
]);
const MAX_BYTES = 2 * 1024 * 1024;

/** Patterns describe the SHAPE of a secret. Matches are counted, never shown. */
const PATTERNS = [
  { type: 'приватный ключ (PEM)', re: /-----BEGIN (?:RSA |OPENSSH |EC |DSA |PGP )?PRIVATE KEY-----/ },
  { type: 'пароль в присваивании', re: /(?:password|passwd|pwd|пароль)\s*[:=]\s*["']?[^\s"',;]{6,}/i },
  { type: 'токен или секрет', re: /(?:api[_-]?key|apikey|secret|token|access[_-]?token)\s*[:=]\s*["']?[A-Za-z0-9_\-.]{16,}/i },
  { type: 'токен Telegram-бота', re: /\b\d{8,10}:[A-Za-z0-9_-]{30,}\b/ },
  { type: 'AWS access key id', re: /\bAKIA[0-9A-Z]{16}\b/ },
  { type: 'строка подключения с паролем', re: /\b(?:postgres|postgresql|mysql|mongodb|redis|amqp):\/\/[^\s:@]+:[^\s@]+@/i },
  { type: 'basic-auth в URL', re: /https?:\/\/[^\s:@/]+:[^\s@/]+@/ },
];

/** A filesystem path is not a secret: HTPASSWD = '/etc/nginx/.htpasswd'. */
const FILE_PATH_VALUE = /[:=]\s*["'][/\][^"']*["']/;

/** Placeholders and documentation examples are not secrets. */
const PLACEHOLDER = /(?:example|placeholder|your[_-]?|<[^>]+>|\*{4,}|xxx+|changeme|dummy|sample|%\(|\{\{)/i;

/**
 * `linkToken: result.data.link_token` is a property being copied, not a secret.
 * A value that is a dotted identifier or a bare variable is a reference; only a
 * quoted string or a bare high-entropy run is a literal worth reporting.
 */
// An unquoted value that is a plain identifier - PASSWORD, config.pwd,
// creds['pass'] - is a reference. A secret written into code is quoted;
// `password=PASSWORD` in a connect() call carries nothing.
const REFERENCE_VALUE = /[:=]\s*[A-Za-z_$][A-Za-z0-9_$]*(?:\.[A-Za-z_$][A-Za-z0-9_$]*)*(?:\[[^\]]*\])?\s*(?:[,;)\]}]|$)/;

/** Reading the value from the environment means the file holds nothing. */
const FROM_ENVIRONMENT = /(?:\$env:|process\.env|os\.environ|getenv|ENV\[|Read-Host|secrets\.|vault|\$\{[A-Za-z_]+\})/i;

function walk(dir, out) {
  let entries;
  try {
    entries = readdirSync(dir, { withFileTypes: true });
  } catch {
    return out;
  }
  for (const entry of entries) {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) {
      if (SKIP_DIRS.has(entry.name)) continue;
      walk(full, out);
    } else if (entry.isFile()) {
      const ext = path.extname(entry.name).toLowerCase();
      if (!TEXT_EXT.has(ext) && !entry.name.startsWith('.env')) continue;
      try {
        if (statSync(full).size > MAX_BYTES) continue;
      } catch {
        continue;
      }
      out.push(full);
    }
  }
  return out;
}

function isTracked(file) {
  try {
    execFileSync('git', ['ls-files', '--error-unmatch', file], {
      cwd: root,
      stdio: ['ignore', 'ignore', 'ignore'],
    });
    return true;
  } catch {
    return false;
  }
}

const findings = new Map();

for (const file of walk(root, [])) {
  let text;
  try {
    text = readFileSync(file, 'utf8');
  } catch {
    continue;
  }
  const lines = text.split('\n');
  for (let i = 0; i < lines.length; i += 1) {
    const line = lines[i];
    if (line.length > 4000) continue;
    for (const { type, re } of PATTERNS) {
      if (!re.test(line)) continue;
      if (PLACEHOLDER.test(line)) continue;
      const literal = !FROM_ENVIRONMENT.test(line) && !REFERENCE_VALUE.test(line) && !FILE_PATH_VALUE.test(line);
      const rel = path.relative(root, file).replace(/\\/g, '/');
      const key = [rel, type, literal].join(' | ');
      if (!findings.has(key)) findings.set(key, { rel, type, literal, lines: [] });
      findings.get(key).lines.push(i + 1);
      break;
    }
  }
}

const grouped = [...findings.values()].sort((a, b) => a.rel.localeCompare(b.rel));
const literals = grouped.filter((entry) => entry.literal);
const fromEnvironment = grouped.filter((entry) => !entry.literal);

if (!grouped.length) {
  console.log('Совпадений по форме секретов не найдено.');
  process.exit(0);
}

console.log('Найдено содержимое, похожее на секреты. Значения намеренно не выводятся.');
console.log('');
console.log('## Литеральные значения — кандидаты на ротацию');
console.log('');
console.log('| Файл | Тип | Строки | В Git |');
console.log('|---|---|---|---|');

let trackedCount = 0;
for (const entry of literals) {
  const tracked = isTracked(path.join(root, entry.rel));
  if (tracked) trackedCount += 1;
  const shown = entry.lines.slice(0, 5).join(', ');
  const rest = entry.lines.length > 5 ? ` … (+${entry.lines.length - 5})` : '';
  console.log(`| \`${entry.rel}\` | ${entry.type} | ${shown}${rest} | ${tracked ? '**да**' : 'нет'} |`);
}

console.log('');
console.log(`Литеральных мест: ${literals.length}, из них файлов в Git: ${trackedCount}.`);
console.log(`Ещё ${fromEnvironment.length} мест читают значение из окружения или из другой переменной — там секрета нет.`);

if (trackedCount) {
  console.log('');
  console.log('Файл в Git означает, что значение уже в истории репозитория.');
  console.log('Удаление файла его оттуда не убирает: секрет считается раскрытым');
  console.log('и подлежит ротации у владельца доступа, а не сокрытию.');
}

process.exit(literals.length ? 1 : 0);
