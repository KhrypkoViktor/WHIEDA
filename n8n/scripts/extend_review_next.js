const fs = require('fs');

const inputPath = process.argv[2];
const outputPath = process.argv[3];

if (!inputPath || !outputPath) {
  console.error('Usage: node extend_review_next.js <input.json> <output.json>');
  process.exit(1);
}

const parsed = JSON.parse(fs.readFileSync(inputPath, 'utf8'));
const workflow = parsed && parsed.data ? parsed.data : parsed;

if (!workflow || !Array.isArray(workflow.nodes)) {
  throw new Error('Workflow nodes not found');
}

function getNode(name) {
  const node = workflow.nodes.find((item) => item.name === name);
  if (!node || typeof node.parameters?.jsCode !== 'string') {
    throw new Error(`Node not found or invalid: ${name}`);
  }
  return node;
}

function replaceOrThrow(source, before, after, label) {
  if (!source.includes(before)) {
    throw new Error(`Pattern not found: ${label}`);
  }
  return source.replace(before, after);
}

const normalizeNode = getNode('Code: Normalize Payload');
normalizeNode.parameters.jsCode = replaceOrThrow(
  normalizeNode.parameters.jsCode,
  "/^\\/review_(today|pending|candidates|stats|high|help)(?:@\\w+)?(?:\\s|$)/i",
  "/^\\/review_(today|pending|candidates|stats|high|help|next)(?:@\\w+)?(?:\\s|$)/i",
  'normalize review command regex',
);

const structuredNode = getNode('Code: Structured Sheet Lookup');
let code = structuredNode.parameters.jsCode;

code = replaceOrThrow(
  code,
  "/^\\/review_(today|pending|candidates|stats|high|help)(?:@\\w+)?(?:\\s+(.*))?$/i",
  "/^\\/review_(today|pending|candidates|stats|high|help|next)(?:@\\w+)?(?:\\s+(.*))?$/i",
  'structured review command regex',
);

code = replaceOrThrow(
  code,
  "function buildReviewReport(command, envelope) {\n  const trusted = envelope.trusted_reviewer === true;\n  if (!trusted) {\n    return 'Эта команда доступна только trusted reviewers.';\n  }\n",
  "function buildReviewNext(command, envelope) {\n  const trusted = envelope.trusted_reviewer === true;\n  if (!trusted) {\n    return 'Эта команда доступна только trusted reviewers.';\n  }\n\n  const rows = rowsFromReviewSnapshot();\n  const filtered = rows.filter((row) => matchesFilter(row, command.filter));\n  const actionable = filtered\n    .filter((row) => ['pending', 'triage', 'in_work', 'applied'].includes(row.queue_status))\n    .sort((a, b) => {\n      const pr = priorityRank(a.priority) - priorityRank(b.priority);\n      if (pr !== 0) return pr;\n      return String(b.created_at || '').localeCompare(String(a.created_at || ''));\n    });\n\n  const next = actionable[0] || null;\n  if (!next) {\n    return command.filter ? 'По этому фильтру открытых задач сейчас нет.' : 'Открытых задач сейчас нет.';\n  }\n\n  return [\n    'Review next:',\n    'priority: ' + (next.priority || 'no-priority'),\n    'owner: ' + (next.owner || 'unassigned'),\n    'type: ' + (next.review_type || next.target_layer || 'review'),\n    'text: ' + shortText(next.short_text || next.source_title, 140),\n    next.external_username ? ('from: ' + next.external_username) : null,\n    next.source_ref ? ('ref: ' + next.source_ref) : null,\n  ].filter(Boolean).join('\\n');\n}\n\nfunction buildReviewReport(command, envelope) {\n  const trusted = envelope.trusted_reviewer === true;\n  if (!trusted) {\n    return 'Эта команда доступна только trusted reviewers.';\n  }\n",
  'insert buildReviewNext',
);

code = replaceOrThrow(
  code,
  "    '/review_high - высокий приоритет',\n    '/review_stats media - фильтр по слову или роли',\n",
  "    '/review_high - высокий приоритет',\n    '/review_next - следующая открытая задача',\n    '/review_stats media - фильтр по слову или роли',\n",
  'extend help text',
);

code = replaceOrThrow(
  code,
  "if (reviewCommand) {\n  const answerText = reviewCommand.kind === 'help'\n    ? buildReviewHelp()\n    : buildReviewReport(reviewCommand, envelope);\n",
  "if (reviewCommand) {\n  const answerText = reviewCommand.kind === 'help'\n    ? buildReviewHelp()\n    : reviewCommand.kind === 'next'\n      ? buildReviewNext(reviewCommand, envelope)\n      : buildReviewReport(reviewCommand, envelope);\n",
  'route next command',
);

structuredNode.parameters.jsCode = code;
fs.writeFileSync(outputPath, JSON.stringify(parsed, null, 2));
console.log(outputPath);
