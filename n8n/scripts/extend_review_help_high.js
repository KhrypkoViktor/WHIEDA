const fs = require('fs');

const inputPath = process.argv[2];
const outputPath = process.argv[3];

if (!inputPath || !outputPath) {
  console.error('Usage: node extend_review_help_high.js <input.json> <output.json>');
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
  "/^\\/review_(today|pending|candidates|stats)(?:@\\w+)?(?:\\s|$)/i",
  "/^\\/review_(today|pending|candidates|stats|high|help)(?:@\\w+)?(?:\\s|$)/i",
  'normalize review command regex',
);

const structuredNode = getNode('Code: Structured Sheet Lookup');
let code = structuredNode.parameters.jsCode;

code = replaceOrThrow(
  code,
  "/^\\/review_(today|pending|candidates|stats)(?:@\\w+)?(?:\\s+(.*))?$/i",
  "/^\\/review_(today|pending|candidates|stats|high|help)(?:@\\w+)?(?:\\s+(.*))?$/i",
  'structured review command regex',
);

code = replaceOrThrow(
  code,
  "function buildReviewReport(command, envelope) {\n  const trusted = envelope.trusted_reviewer === true;\n  if (!trusted) {\n    return 'Эта команда доступна только trusted reviewers.';\n  }\n",
  "function buildReviewHelp() {\n  return [\n    'Review commands:',\n    '/review_stats - общая сводка',\n    '/review_today - что пришло сегодня',\n    '/review_pending - открытые',\n    '/review_candidates - кандидаты',\n    '/review_high - высокий приоритет',\n    '/review_stats media - фильтр по слову или роли',\n  ].join('\\n');\n}\n\nfunction buildReviewReport(command, envelope) {\n  const trusted = envelope.trusted_reviewer === true;\n  if (!trusted) {\n    return 'Эта команда доступна только trusted reviewers.';\n  }\n",
  'insert review help builder',
);

code = replaceOrThrow(
  code,
  "  } else if (command.kind === 'stats') {\n    title = 'Review stats';\n    selected = filtered;\n  }\n",
  "  } else if (command.kind === 'stats') {\n    title = 'Review stats';\n    selected = filtered;\n  } else if (command.kind === 'high') {\n    title = 'Review high';\n    selected = filtered.filter((row) => row.priority === 'high');\n  }\n",
  'insert high branch',
);

code = replaceOrThrow(
  code,
  "const normalizedText = normalize(userText);\nconst reviewCommand = getReviewCommand(userText);\n\nif (reviewCommand) {\n  const answerText = buildReviewReport(reviewCommand, envelope);\n",
  "const normalizedText = normalize(userText);\nconst reviewCommand = getReviewCommand(userText);\n\nif (reviewCommand) {\n  const answerText = reviewCommand.kind === 'help'\n    ? buildReviewHelp()\n    : buildReviewReport(reviewCommand, envelope);\n",
  'route help command',
);

structuredNode.parameters.jsCode = code;
fs.writeFileSync(outputPath, JSON.stringify(parsed, null, 2));
console.log(outputPath);
