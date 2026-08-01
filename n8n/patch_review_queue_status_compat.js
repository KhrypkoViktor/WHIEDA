const fs = require('fs');

const inputPath = process.argv[2];
const outputPath = process.argv[3];

if (!inputPath || !outputPath) {
  console.error('Usage: node patch_review_queue_status_compat.js <input.json> <output.json>');
  process.exit(1);
}

const parsed = JSON.parse(fs.readFileSync(inputPath, 'utf8'));
const workflow = parsed && parsed.data ? parsed.data : parsed;

if (!workflow || !Array.isArray(workflow.nodes)) {
  throw new Error('Workflow nodes not found');
}

const node = workflow.nodes.find((item) => item.name === 'Postgres: Write Review Queue');
if (!node || typeof node?.parameters?.query !== 'string') {
  throw new Error('Postgres: Write Review Queue node not found');
}

const search = "  '{{ String($json.review_queue_status || 'pending').replace(/'/g, '') }}'";
const replace = "  '{{ String(($json.review_queue_status === 'candidate' ? 'pending' : ($json.review_queue_status || 'pending'))).replace(/'/g, '') }}'";

if (!node.parameters.query.includes(search)) {
  throw new Error('Expected status expression not found');
}

node.parameters.query = node.parameters.query.replace(search, replace);

fs.writeFileSync(outputPath, JSON.stringify(workflow, null, 2));
console.log(outputPath);
