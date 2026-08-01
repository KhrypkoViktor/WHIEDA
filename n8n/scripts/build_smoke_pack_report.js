const fs = require('fs');
const path = require('path');

const casesPath = process.argv[2];
const summariesDir = process.argv[3];
const outputPath = process.argv[4];

if (!casesPath || !summariesDir || !outputPath) {
  console.error('Usage: node build_smoke_pack_report.js <cases.json> <summariesDir> <output.md>');
  process.exit(1);
}

const cases = JSON.parse(fs.readFileSync(casesPath, 'utf8'));

function safeJson(filePath) {
  try {
    return JSON.parse(fs.readFileSync(filePath, 'utf8'));
  } catch {
    return null;
  }
}

function listJsonFiles(dirPath) {
  const entries = fs.readdirSync(dirPath, { withFileTypes: true });
  return entries
    .filter((entry) => entry.isFile() && entry.name.endsWith('.json'))
    .map((entry) => path.join(dirPath, entry.name));
}

function pickSummaryObject(payload) {
  if (!payload || typeof payload !== 'object') {
    return null;
  }
  if (payload.normalize_payload || payload.validate_dify_response) {
    return payload;
  }
  if (payload.data && typeof payload.data === 'object' && (payload.data.normalize_payload || payload.data.validate_dify_response)) {
    return payload.data;
  }
  return null;
}

function extractText(summary) {
  return String(
    summary?.normalize_payload?.message_text ??
    summary?.merge_envelope_session?.message_text ??
    summary?.validate_dify_response?.user_message_text ??
    ''
  ).trim();
}

function extractAnswer(summary) {
  return summary?.validate_dify_response || {};
}

function detectSignals(summary) {
  const answer = extractAnswer(summary);
  const answerText = String(answer.answer_text || answer.reply_text || answer.message_text || '');
  const answerMode = String(answer.answer_mode || '');
  const route = String(answer.route || '');
  const telegramMediaMode = String(answer.telegram_media_mode || '');
  const knowledgeGap = answer.knowledge_gap === true;
  const validationError = answer.validation_error || null;
  const photoUrl = answer.telegram_photo_url || null;

  const detectors = [];
  if (!answerText.trim()) detectors.push('empty_answer');
  if (validationError) detectors.push('validation_error');
  if (route !== 'answer' && route !== 'escalate' && route !== 'fallback') detectors.push('unknown_route');
  if (telegramMediaMode === 'photo' && !photoUrl) detectors.push('photo_without_url');
  if (knowledgeGap && answerMode.includes('direct_structured')) detectors.push('structured_gap_conflict');

  return {
    answerText,
    answerMode,
    route,
    telegramMediaMode,
    knowledgeGap,
    validationError,
    photoUrl,
    detectors,
  };
}

function findBestSummary(prompt, summaries) {
  const normalized = String(prompt || '').trim().toLowerCase();
  const exact = summaries.find((item) => extractText(item.summary).trim().toLowerCase() === normalized);
  if (exact) return exact;

  return summaries.find((item) => extractText(item.summary).trim().toLowerCase().includes(normalized));
}

const summaries = listJsonFiles(summariesDir)
  .map((filePath) => ({ filePath, raw: safeJson(filePath) }))
  .map((item) => ({ ...item, summary: pickSummaryObject(item.raw) }))
  .filter((item) => item.summary);

const results = cases.map((testCase) => {
  const match = findBestSummary(testCase.prompt, summaries);
  if (!match) {
    return {
      ...testCase,
      status: 'FAIL',
      reason: 'summary_not_found',
      file: null,
      signals: null,
    };
  }

  const signals = detectSignals(match.summary);
  const failedChecks = [];

  if (testCase.expectedRoute && signals.route !== testCase.expectedRoute) {
    failedChecks.push(`route=${signals.route || 'none'}`);
  }
  if (testCase.expectedAnswerModeIncludes && !signals.answerMode.includes(testCase.expectedAnswerModeIncludes)) {
    failedChecks.push(`answer_mode=${signals.answerMode || 'none'}`);
  }
  if (typeof testCase.expectKnowledgeGap === 'boolean' && signals.knowledgeGap !== testCase.expectKnowledgeGap) {
    failedChecks.push(`knowledge_gap=${String(signals.knowledgeGap)}`);
  }
  if (typeof testCase.expectPhoto === 'boolean') {
    const hasPhoto = signals.telegramMediaMode === 'photo';
    if (hasPhoto !== testCase.expectPhoto) {
      failedChecks.push(`photo=${String(hasPhoto)}`);
    }
  }
  for (const detector of signals.detectors) {
    failedChecks.push(`detector:${detector}`);
  }

  return {
    ...testCase,
    status: failedChecks.length ? 'FAIL' : 'PASS',
    reason: failedChecks.join(', '),
    file: path.basename(match.filePath),
    signals,
  };
});

const passed = results.filter((item) => item.status === 'PASS').length;
const failed = results.length - passed;
const passRate = results.length ? ((passed / results.length) * 100).toFixed(1) : '0.0';

const lines = [
  '# WHIEDA Smoke Pack Report',
  '',
  `- cases: ${results.length}`,
  `- passed: ${passed}`,
  `- failed: ${failed}`,
  `- pass_rate: ${passRate}%`,
  '',
  '## Results',
  '',
];

for (const result of results) {
  lines.push(`- ${result.id}: ${result.status}`);
  lines.push(`  prompt: ${result.prompt}`);
  lines.push(`  file: ${result.file || 'not found'}`);
  lines.push(`  reason: ${result.reason || 'ok'}`);
  if (result.signals) {
    lines.push(`  route: ${result.signals.route || 'none'}`);
    lines.push(`  answer_mode: ${result.signals.answerMode || 'none'}`);
    lines.push(`  knowledge_gap: ${String(result.signals.knowledgeGap)}`);
    lines.push(`  media_mode: ${result.signals.telegramMediaMode || 'none'}`);
  }
  lines.push('');
}

const detectorCounts = new Map();
for (const result of results) {
  if (!result.signals) continue;
  for (const detector of result.signals.detectors) {
    detectorCounts.set(detector, (detectorCounts.get(detector) || 0) + 1);
  }
}

lines.push('## Detectors');
lines.push('');
if (detectorCounts.size === 0) {
  lines.push('- no detector hits');
} else {
  for (const [detector, count] of detectorCounts.entries()) {
    lines.push(`- ${detector}: ${count}`);
  }
}
lines.push('');

fs.writeFileSync(outputPath, lines.join('\n'));
console.log(outputPath);
