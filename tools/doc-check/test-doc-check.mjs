/**
 * Tests for the doc-check.
 *
 * A checker that cannot fail is decoration. Each fixture below plants one
 * deliberate defect and asserts the exit code, so "green" means the check
 * actually looked rather than that nothing was wrong.
 *
 * Run: node --test tools/doc-check/test-doc-check.mjs
 */

import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import path from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));
const checker = path.join(here, 'check-docs.mjs');

function run(fixture) {
  const result = spawnSync(
    process.execPath,
    [checker, '--manifest', path.join(here, 'fixtures', fixture, 'manifest.json')],
    { encoding: 'utf8' },
  );
  return { code: result.status, out: `${result.stdout}${result.stderr}` };
}

test('всё на месте — проверка проходит без ошибок и предупреждений', () => {
  const { code, out } = run('ok');
  assert.equal(code, 0, out);
  assert.match(out, /Ошибок: 0\. Предупреждений: 0\./);
});

test('ссылка в никуда останавливает проверку', () => {
  const { code, out } = run('missing-active');
  assert.equal(code, 1, out);
  assert.match(out, /CONTRACT_THAT_NEVER_EXISTED\.md/);
  assert.match(out, /Ошибок: 1\./);
  // Именно это сообщение удерживает от «починки» возвратом файла из архива.
  assert.match(out, /не повод вернуть файл из 99_Archive/);
});

test('исторический документ — предупреждение, а не ошибка', () => {
  const { code, out } = run('historical');
  assert.equal(code, 0, out);
  assert.match(out, /WARN {2}GUIDE\.md -> OLD_GATE_TZ_V1\.md/);
  assert.match(out, /исполнено и архивировано/);
  assert.match(out, /Ошибок: 0\. Предупреждений: 1\./);
});

test('отсутствие самого обязательного документа — ошибка', () => {
  const { code, out } = run('missing-doc');
  assert.equal(code, 1, out);
  assert.match(out, /GUIDE\.md: активный обязательный документ отсутствует/);
});

test('шаблонный путь вида PROCESS/<task-id>/STATE.md не проверяется как файл', () => {
  // Иначе проверка краснела бы всегда и её научились бы игнорировать.
  const { out } = run('ok');
  assert.doesNotMatch(out, /<task-id>/);
});
