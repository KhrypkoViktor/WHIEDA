const fs = require('fs');

const inputPath = process.argv[2];
const outputPath = process.argv[3];
const workflowId = process.argv[4];
const versionId = process.argv[5];

if (!inputPath || !outputPath || !workflowId || !versionId) {
  console.error('Usage: node apply_active_workflow_full_patch.js <workflow.json> <output.sql> <workflowId> <versionId>');
  process.exit(1);
}

const parsed = JSON.parse(fs.readFileSync(inputPath, 'utf8'));
const workflow = parsed && parsed.data ? parsed.data : (Array.isArray(parsed) ? parsed[0] : parsed);

if (!workflow || !Array.isArray(workflow.nodes) || !workflow.connections) {
  throw new Error('Workflow nodes/connections not found in input JSON');
}

const nodesJson = JSON.stringify(workflow.nodes).replace(/'/g, "''");
const connectionsJson = JSON.stringify(workflow.connections).replace(/'/g, "''");
const workflowIdEscaped = workflowId.replace(/'/g, "''");
const versionIdEscaped = versionId.replace(/'/g, "''");

const sql = [
  'BEGIN;',
  `UPDATE workflow_entity`,
  `SET nodes = '${nodesJson}'::json, connections = '${connectionsJson}'::json, "updatedAt" = now()`,
  `WHERE id = '${workflowIdEscaped}';`,
  '',
  `UPDATE workflow_history`,
  `SET nodes = '${nodesJson}'::json, connections = '${connectionsJson}'::json, "updatedAt" = now()`,
  `WHERE "versionId" = '${versionIdEscaped}';`,
  'COMMIT;',
  '',
].join('\n');

fs.writeFileSync(outputPath, sql);
console.log(outputPath);
