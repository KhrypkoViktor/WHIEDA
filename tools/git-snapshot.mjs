/**
 * Git snapshot for PROCESS/<task-id>/STATE.md and the worktree registry.
 *
 * STATE.md is context, not a second Git. Everything here is read from Git at
 * the moment of writing, so a stale STATE can always be re-derived instead of
 * being trusted. Nothing is deleted, moved or checked out: this tool only
 * reads.
 *
 * A worktree's owner is deliberately NOT guessed. A branch name and a commit
 * author say who typed, not whose slice it is, and acting on that guess is how
 * someone else's unfinished work gets discarded. Status is "unknown" until a
 * person says otherwise.
 *
 * Usage:
 *   node tools/git-snapshot.mjs --repo <path> [--repo <path>...] [--base <ref>]
 *   node tools/git-snapshot.mjs --repo D:/Projects/WHIEDA --repo D:/Projects/WHIEDA/03_Website/wwc-best --base origin/master
 *
 * Prints Markdown to stdout.
 */

import { execFileSync } from 'node:child_process';

const args = process.argv.slice(2);
const repos = [];
let base = '';
for (let i = 0; i < args.length; i += 1) {
  if (args[i] === '--repo') repos.push(args[i + 1]);
  if (args[i] === '--base') base = args[i + 1];
}
if (!repos.length) {
  console.error('usage: node tools/git-snapshot.mjs --repo <path> [--repo <path>...] [--base <ref>]');
  process.exit(2);
}

function git(cwd, argv, { allowFail = false } = {}) {
  try {
    return execFileSync('git', argv, { cwd, encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'] }).trim();
  } catch (error) {
    if (allowFail) return '';
    throw new Error(`git ${argv.join(' ')} in ${cwd}: ${error.message}`);
  }
}

/** `git worktree list --porcelain` in records, without inventing fields. */
function worktrees(repo) {
  const out = git(repo, ['worktree', 'list', '--porcelain'], { allowFail: true });
  const list = [];
  let current = null;
  for (const line of out.split('\n')) {
    if (line.startsWith('worktree ')) {
      if (current) list.push(current);
      current = { path: line.slice('worktree '.length).trim(), head: '', branch: '', detached: false };
    } else if (line.startsWith('HEAD ') && current) {
      current.head = line.slice('HEAD '.length).trim();
    } else if (line.startsWith('branch ') && current) {
      current.branch = line.slice('branch '.length).trim().replace('refs/heads/', '');
    } else if (line === 'detached' && current) {
      current.detached = true;
    }
  }
  if (current) list.push(current);
  return list;
}

function describe(repo, wt, baseRef) {
  // A worktree can be gone from disk while Git still lists it; say so rather
  // than failing the whole snapshot.
  const exists = git(wt.path, ['rev-parse', '--is-inside-work-tree'], { allowFail: true }) === 'true';
  if (!exists) return { ...wt, missing: true, dirty: 'n/a', unique: 'n/a', subject: '' };

  const status = git(wt.path, ['status', '--porcelain'], { allowFail: true });
  const dirtyCount = status ? status.split('\n').filter(Boolean).length : 0;
  const subject = git(wt.path, ['log', '-1', '--format=%s'], { allowFail: true });
  const date = git(wt.path, ['log', '-1', '--format=%ci'], { allowFail: true });

  let unique = 'база не задана';
  if (baseRef) {
    const count = git(repo, ['rev-list', '--count', `${baseRef}..${wt.head}`], { allowFail: true });
    unique = count === '' ? 'базы нет в этом repo' : count;
  }
  return { ...wt, missing: false, dirty: dirtyCount, unique, subject, date };
}

const lines = [];
lines.push(`<!-- Сгенерировано tools/git-snapshot.mjs — не редактировать вручную. -->`);
lines.push(`Снимок: ${new Date().toISOString()}`);
if (base) lines.push(`База сравнения: \`${base}\``);
lines.push('');

for (const repo of repos) {
  const root = git(repo, ['rev-parse', '--show-toplevel'], { allowFail: true });
  if (!root) {
    lines.push(`## ${repo}`, '', '**Не Git-репозиторий или недоступен.**', '');
    continue;
  }
  const branch = git(repo, ['branch', '--show-current'], { allowFail: true }) || '(detached)';
  const head = git(repo, ['rev-parse', 'HEAD'], { allowFail: true });
  const status = git(repo, ['status', '--porcelain'], { allowFail: true });
  const dirty = status ? status.split('\n').filter(Boolean).length : 0;
  const staged = git(repo, ['diff', '--cached', '--stat'], { allowFail: true });

  lines.push(`## ${root}`, '');
  lines.push(`- branch: \`${branch}\``);
  lines.push(`- HEAD: \`${head}\``);
  lines.push(`- незакоммиченных путей: ${dirty}`);
  lines.push(`- в индексе: ${staged ? staged.split('\n').filter(Boolean).length + ' строк diff --cached --stat' : 'пусто'}`);
  // rev-parse echoes a full 40-char SHA back whether or not the object exists,
  // so verify it resolves to a real commit in THIS repo before claiming a base.
  let baseHere = '';
  if (base) {
    baseHere = git(repo, ['rev-parse', '--verify', '--quiet', `${base}^{commit}`], { allowFail: true });
    lines.push(baseHere
      ? `- база \`${base}\`: \`${baseHere}\``
      : `- база \`${base}\`: **не найдена в этом repo** — сравнение не проводится`);
  }
  lines.push('');

  const wts = worktrees(repo);
  lines.push(`### Worktree (${wts.length})`, '');
  lines.push('| Путь | Branch / HEAD | Незакоммичено | Уникальных commits от базы | Последний commit | Владелец задачи |');
  lines.push('|---|---|---|---|---|---|');
  for (const wt of wts.map((w) => describe(repo, w, baseHere))) {
    const ref = wt.branch ? `\`${wt.branch}\`` : `detached \`${wt.head.slice(0, 7)}\``;
    const note = wt.missing ? '**нет на диске**' : `${wt.dirty}`;
    const subject = wt.missing ? '' : `${wt.date?.slice(0, 10) || ''} ${(wt.subject || '').slice(0, 60)}`;
    lines.push(`| \`${wt.path}\` | ${ref} | ${note} | ${wt.unique} | ${subject} | unknown |`);
  }
  lines.push('');
}

lines.push('---');
lines.push('');
lines.push('«Владелец задачи» намеренно остаётся `unknown`, пока его не назовёт человек:');
lines.push('имя ветки и автор commit говорят, кто печатал, а не чей это срез.');
lines.push('Ничего из перечисленного не удалять по возрасту или по числу commits —');
lines.push('сначала проверить незакоммиченное, уникальные commits и ссылки релизов.');

console.log(lines.join('\n'));
