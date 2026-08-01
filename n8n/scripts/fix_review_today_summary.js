const fs = require('fs');

const inputPath = process.argv[2];
const outputPath = process.argv[3];

if (!inputPath || !outputPath) {
  console.error('Usage: node fix_review_today_summary.js <input.json> <output.json>');
  process.exit(1);
}

const parsed = JSON.parse(fs.readFileSync(inputPath, 'utf8'));
const workflow = parsed && parsed.data ? parsed.data : parsed;

if (!workflow || !Array.isArray(workflow.nodes)) {
  throw new Error('Workflow nodes not found');
}

const node = workflow.nodes.find((item) => item.name === 'Code: Structured Sheet Lookup');
if (!node || typeof node.parameters?.jsCode !== 'string') {
  throw new Error('Code: Structured Sheet Lookup jsCode not found');
}

const before = [
  "  const pendingCount = filtered.filter((row) => ['pending', 'triage', 'in_work', 'applied'].includes(row.queue_status)).length;",
  "  const candidateCount = filtered.filter((row) => row.queue_status === 'candidate' || row.trust_level === 'candidate').length;",
  "  const highCount = filtered.filter((row) => row.priority === 'high').length;",
].join('\n');

const after = [
  "  const summaryRows = command.kind === 'today' ? selected : filtered;",
  "  const pendingCount = summaryRows.filter((row) => ['pending', 'triage', 'in_work', 'applied'].includes(row.queue_status)).length;",
  "  const candidateCount = summaryRows.filter((row) => row.queue_status === 'candidate' || row.trust_level === 'candidate').length;",
  "  const highCount = summaryRows.filter((row) => row.priority === 'high').length;",
].join('\n');

if (!node.parameters.jsCode.includes(before)) {
  throw new Error('Target block for review_today summary fix not found');
}

node.parameters.jsCode = node.parameters.jsCode.replace(before, after);
fs.writeFileSync(outputPath, JSON.stringify(parsed, null, 2));
console.log(outputPath);
