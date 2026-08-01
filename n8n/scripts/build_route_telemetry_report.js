const fs = require('fs');
const path = require('path');

const summariesDir = process.argv[2];
const outputPath = process.argv[3];

if (!summariesDir || !outputPath) {
  console.error('Usage: node build_route_telemetry_report.js <summariesDir> <output.md>');
  process.exit(1);
}

function listJsonFiles(dirPath) {
  return fs.readdirSync(dirPath, { withFileTypes: true })
    .filter((entry) => entry.isFile() && entry.name.endsWith('.json'))
    .map((entry) => path.join(dirPath, entry.name));
}

function safeJson(filePath) {
  try {
    return JSON.parse(fs.readFileSync(filePath, 'utf8'));
  } catch {
    return null;
  }
}

function pickSummaryObject(payload) {
  if (!payload || typeof payload !== 'object') return null;
  if (payload.normalize_payload || payload.validate_dify_response) return payload;
  if (payload.data && typeof payload.data === 'object' && (payload.data.normalize_payload || payload.data.validate_dify_response)) {
    return payload.data;
  }
  return null;
}

function bucketKey(value, fallback) {
  const text = String(value || '').trim();
  return text || fallback;
}

function increment(map, key) {
  map.set(key, (map.get(key) || 0) + 1);
}

function isNoLLM(answerMode) {
  const value = String(answerMode || '');
  return value.startsWith('direct_structured') || value === 'direct_review_report';
}

const summaries = listJsonFiles(summariesDir)
  .map((filePath) => ({ filePath, raw: safeJson(filePath) }))
  .map((item) => ({ ...item, summary: pickSummaryObject(item.raw) }))
  .filter((item) => item.summary);

const routeCounts = new Map();
const answerModeCounts = new Map();
const mediaModeCounts = new Map();
const reviewTypeCounts = new Map();

let total = 0;
let noLlm = 0;
let withPhoto = 0;
let withKnowledgeGap = 0;
let withValidationError = 0;

for (const item of summaries) {
  const answer = item.summary.validate_dify_response || {};
  const route = bucketKey(answer.route, 'missing_route');
  const answerMode = bucketKey(answer.answer_mode, 'missing_answer_mode');
  const mediaMode = bucketKey(answer.telegram_media_mode, 'message');
  const reviewType = bucketKey(answer.review_type, 'none');

  total += 1;
  increment(routeCounts, route);
  increment(answerModeCounts, answerMode);
  increment(mediaModeCounts, mediaMode);
  increment(reviewTypeCounts, reviewType);

  if (isNoLLM(answerMode)) noLlm += 1;
  if (mediaMode === 'photo') withPhoto += 1;
  if (answer.knowledge_gap === true) withKnowledgeGap += 1;
  if (answer.validation_error) withValidationError += 1;
}

function renderMap(title, map) {
  const lines = [`## ${title}`, ''];
  const rows = Array.from(map.entries()).sort((a, b) => b[1] - a[1]);
  if (!rows.length) {
    lines.push('- no data', '');
    return lines;
  }
  for (const [key, value] of rows) {
    lines.push(`- ${key}: ${value}`);
  }
  lines.push('');
  return lines;
}

const noLlmRate = total ? ((noLlm / total) * 100).toFixed(1) : '0.0';

const lines = [
  '# WHIEDA Route Telemetry',
  '',
  `- summaries: ${total}`,
  `- no_llm_count: ${noLlm}`,
  `- no_llm_rate: ${noLlmRate}%`,
  `- photo_count: ${withPhoto}`,
  `- knowledge_gap_count: ${withKnowledgeGap}`,
  `- validation_error_count: ${withValidationError}`,
  '',
  ...renderMap('Routes', routeCounts),
  ...renderMap('Answer Modes', answerModeCounts),
  ...renderMap('Media Modes', mediaModeCounts),
  ...renderMap('Review Types', reviewTypeCounts),
];

fs.writeFileSync(outputPath, lines.join('\n'));
console.log(outputPath);
