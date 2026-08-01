const fs = require('fs');

const inputPath = process.argv[2];
const outputPath = process.argv[3];

if (!inputPath || !outputPath) {
  console.error('Usage: node extend_review_stats.js <input.json> <output.json>');
  process.exit(1);
}

const parsed = JSON.parse(fs.readFileSync(inputPath, 'utf8'));
const workflow = parsed && parsed.data ? parsed.data : parsed;

if (!workflow || !Array.isArray(workflow.nodes)) {
  throw new Error('Workflow nodes not found');
}

function findNode(name) {
  const node = workflow.nodes.find((item) => item.name === name);
  if (!node || typeof node.parameters?.jsCode !== 'string') {
    throw new Error(`${name} jsCode not found`);
  }
  return node;
}

function replaceOrThrow(source, before, after, label) {
  if (!source.includes(before)) {
    throw new Error(`Pattern not found: ${label}`);
  }
  return source.replace(before, after);
}

const normalizeNode = findNode('Code: Normalize Payload');
normalizeNode.parameters.jsCode = replaceOrThrow(
  normalizeNode.parameters.jsCode,
  "/^\\/review_(today|pending|candidates)(?:@\\w+)?(?:\\s|$)/i",
  "/^\\/review_(today|pending|candidates|stats)(?:@\\w+)?(?:\\s|$)/i",
  'normalize review command regex',
);

const structuredNode = findNode('Code: Structured Sheet Lookup');
let code = structuredNode.parameters.jsCode;

code = replaceOrThrow(
  code,
  "/^\\/review_(today|pending|candidates)(?:@\\w+)?(?:\\s+(.*))?$/i",
  "/^\\/review_(today|pending|candidates|stats)(?:@\\w+)?(?:\\s+(.*))?$/i",
  'structured review command regex',
);

code = replaceOrThrow(
  code,
  "  } else if (command.kind === 'candidates') {\n    title = 'Review candidates';\n    selected = filtered.filter((row) => row.queue_status === 'candidate' || row.trust_level === 'candidate');\n  }\n",
  "  } else if (command.kind === 'candidates') {\n    title = 'Review candidates';\n    selected = filtered.filter((row) => row.queue_status === 'candidate' || row.trust_level === 'candidate');\n  } else if (command.kind === 'stats') {\n    title = 'Review stats';\n    selected = filtered;\n  }\n",
  'stats branch',
);

code = replaceOrThrow(
  code,
  "  if (command.kind === 'today') {\n    lines.push('Открытых: ' + pendingCount + '. Кандидатов: ' + candidateCount + '. High: ' + highCount + '.');\n  }\n",
  "  if (command.kind === 'today' || command.kind === 'stats') {\n    lines.push('Открытых: ' + pendingCount + '. Кандидатов: ' + candidateCount + '. High: ' + highCount + '.');\n  }\n",
  'stats summary line',
);

code = replaceOrThrow(
  code,
  "  if (!selected.length) {\n    lines.push(command.filter ? 'По этому фильтру сейчас пусто.' : 'Сейчас пусто.');\n    return lines.join('\\n');\n  }\n",
  "  if (!selected.length) {\n    lines.push(command.filter ? 'По этому фильтру сейчас пусто.' : 'Сейчас пусто.');\n    return lines.join('\\n');\n  }\n\n  if (command.kind === 'stats') {\n    return lines.join('\\n');\n  }\n",
  'stats early return',
);

structuredNode.parameters.jsCode = code;
fs.writeFileSync(outputPath, JSON.stringify(parsed, null, 2));
console.log(outputPath);
