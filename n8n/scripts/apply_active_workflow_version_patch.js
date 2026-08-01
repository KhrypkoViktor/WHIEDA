const fs = require('fs');

const inputPath = process.argv[2];
const outputPath = process.argv[3];
const workflowId = process.argv[4];
const versionId = process.argv[5];

if (!inputPath || !outputPath || !workflowId || !versionId) {
  console.error('Usage: node apply_active_workflow_version_patch.js <workflow.json> <output.sql> <workflowId> <versionId>');
  process.exit(1);
}

const parsed = JSON.parse(fs.readFileSync(inputPath, 'utf8'));
const workflow = Array.isArray(parsed) ? parsed[0] : parsed;

if (!workflow || !Array.isArray(workflow.nodes)) {
  throw new Error('Workflow nodes not found in input JSON');
}

const nodesJson = JSON.stringify(workflow.nodes).replace(/'/g, "''");

const sql = [
  'BEGIN;',
  `UPDATE workflow_entity`,
  `SET nodes = '${nodesJson}'::json, "updatedAt" = now()`,
  `WHERE id = '${workflowId.replace(/'/g, "''")}';`,
  '',
  `UPDATE workflow_history`,
  `SET nodes = '${nodesJson}'::json, "updatedAt" = now()`,
  `WHERE "versionId" = '${versionId.replace(/'/g, "''")}';`,
  'COMMIT;',
  '',
].join('\n');

fs.writeFileSync(outputPath, sql);
console.log(outputPath);
