const fs = require('fs');

const inputPath = process.argv[2];
const outputPath = process.argv[3];
const workflowId = process.argv[4] || 'advisor-whieda-phase1';

if (!inputPath || !outputPath) {
  console.error('Usage: node apply_workflow_nodes_patch.js <workflow.json> <output.sql> [workflowId]');
  process.exit(1);
}

const parsed = JSON.parse(fs.readFileSync(inputPath, 'utf8'));
const workflow = Array.isArray(parsed) ? parsed[0] : parsed;

if (!workflow || !Array.isArray(workflow.nodes)) {
  throw new Error('Workflow nodes not found in input JSON');
}

const nodesJson = JSON.stringify(workflow.nodes);
const escaped = nodesJson.replace(/'/g, "''");

const sql = [
  'BEGIN;',
  `UPDATE workflow_entity`,
  `SET nodes = '${escaped}'::json, "updatedAt" = now()`,
  `WHERE id = '${workflowId.replace(/'/g, "''")}';`,
  'COMMIT;',
  '',
].join('\n');

fs.writeFileSync(outputPath, sql);
console.log(outputPath);
