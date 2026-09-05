/**
 * Local doc-check: do the documents an agent is told to read actually exist,
 * and do their links resolve?
 *
 * The reading list in the canon named nine documents that are not in this
 * repository at all. An agent that follows the list honestly walks into a wall,
 * and the usual "fix" is to drag a superseded document back out of the archive
 * so the check goes green. That is worse than the missing link: it makes an
 * obsolete contract look current.
 *
 * So this check works from an explicit manifest, not from a scan:
 *   - a document listed as `active` must exist, and its links must resolve;
 *   - a link to something listed in `knownMissing` is a WARNING with the reason,
 *     never an error, and never a reason to restore a file;
 *   - anything else that fails to resolve is an ERROR.
 *
 * It reads only the documents the manifest names. It never walks 99_Archive,
 * never needs credentials, and never touches the network.
 *
 * Usage:
 *   node tools/doc-check/check-docs.mjs
 *   node tools/doc-check/check-docs.mjs --manifest <path> --root <repo root>
 *
 * Exit 1 on any error, 0 when only warnings remain.
 */

import { existsSync, readFileSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));
const args = process.argv.slice(2);
const argOf = (name, fallback) => {
  const i = args.indexOf(name);
  return i === -1 ? fallback : args[i + 1];
};

const manifestPath = path.resolve(argOf('--manifest', path.join(here, 'manifest.json')));
const manifest = JSON.parse(readFileSync(manifestPath, 'utf8'));
const root = path.resolve(argOf('--root', path.join(path.dirname(manifestPath), manifest.root || '.')));

const errors = [];
const warnings = [];

const knownMissing = new Map(
  (manifest.knownMissing || []).map((entry) => [entry.path.replace(/\\/g, '/'), entry.reason || 'помечен как исторический']),
);

const ignorePrefixes = manifest.ignoreLinkPrefixes || [];
// `PROCESS/<task-id>/STATE.md` is a template, not a path. Checking it would
// fail forever and teach everyone to ignore the checker.
const isPlaceholder = (target) => target.includes('<') || target.includes('>');
const shouldIgnore = (target) => isPlaceholder(target)
  || ignorePrefixes.some((prefix) => target.startsWith(prefix));

/**
 * Root documents legitimately point at files that live in the website repo
 * (`src/styles/global.css`, `scripts/sync-runtime-assets.mjs`). Those are two
 * separate repositories, so a target is searched under every declared root.
 * When it resolves in none of them the message says where we looked, because
 * "missing" and "present in a different worktree" are different problems and
 * only one of them is fixed by writing the file.
 */
const searchRoots = (manifest.searchRoots || ['.']).map((entry) => path.resolve(root, entry));

/**
 * Markdown links plus backticked repo paths. Both forms are used in these
 * documents, and a broken backticked path misleads exactly as much as a broken
 * link — the reader still cannot find the file.
 */
function targetsIn(text) {
  const found = new Set();
  for (const match of text.matchAll(/\[[^\]]*\]\(([^)\s]+)\)/g)) found.add(match[1]);
  for (const match of text.matchAll(/`([^`\n]+?\.(?:md|json|mjs|js|astro|css|tsv|csv|conf))`/g)) found.add(match[1]);
  return [...found];
}

/** A backticked path may be repo-relative or doc-relative; accept either. */
function resolves(target, docDir) {
  const clean = target.split('#')[0].split('?')[0].trim();
  if (!clean) return true;
  const bare = clean.replace(/^\.\//, '');
  const candidates = [path.resolve(docDir, clean)];
  for (const base of searchRoots) {
    candidates.push(path.resolve(base, clean), path.resolve(base, bare));
  }
  return candidates.some((candidate) => existsSync(candidate));
}

for (const doc of manifest.documents || []) {
  const rel = doc.path.replace(/\\/g, '/');
  const abs = path.resolve(root, rel);

  if (!existsSync(abs)) {
    if (doc.status === 'historical') {
      warnings.push(`${rel}: исторический документ отсутствует — ${doc.reason || 'причина не указана'}`);
    } else {
      errors.push(`${rel}: активный обязательный документ отсутствует`);
    }
    continue;
  }
  if (doc.status !== 'active' || !doc.checkLinks) continue;

  const text = readFileSync(abs, 'utf8');
  const docDir = path.dirname(abs);
  for (const target of targetsIn(text)) {
    if (shouldIgnore(target)) continue;
    if (resolves(target, docDir)) continue;

    const key = target.split('#')[0].replace(/^\.\//, '');
    const reason = knownMissing.get(key) || knownMissing.get(path.posix.basename(key));
    if (reason) warnings.push(`${rel} -> ${target}: ${reason}`);
    else errors.push(`${rel} -> ${target}: не найден ни под одним из корней (${manifest.searchRoots.join(', ')})`);
  }
}

for (const warning of warnings) console.log(`WARN  ${warning}`);
for (const error of errors) console.error(`ОШИБКА ${error}`);

console.log(
  `\nПроверено документов: ${(manifest.documents || []).length}. `
  + `Ошибок: ${errors.length}. Предупреждений: ${warnings.length}.`,
);

if (errors.length) {
  console.error(
    '\nСсылка не разрешается — это не повод вернуть файл из 99_Archive.\n'
    + 'Либо документ действительно нужен и его надо восстановить осознанно,\n'
    + 'либо ссылку надо убрать, либо внести в knownMissing с причиной.',
  );
  process.exit(1);
}
