const fs = require('fs');
const path = require('path');

const workspaceRoot = path.resolve(__dirname, '..');
const workflowPath = path.join(workspaceRoot, 'live-workflow-split.json');
const sqlPath = path.join(workspaceRoot, 'live-workflow-update.sql');

const raw = fs.readFileSync(workflowPath, 'utf8');
const jsonStart = raw.indexOf('{');
const parsed = JSON.parse(raw.slice(jsonStart));
const workflow = Array.isArray(parsed) ? parsed[0] : parsed;

const sqlEscape = (value) => String(value ?? '').replace(/'/g, "''");

const name = sqlEscape(workflow.name);
const nodes = sqlEscape(JSON.stringify(workflow.nodes));
const connections = sqlEscape(JSON.stringify(workflow.connections ?? {}));
const settings = sqlEscape(JSON.stringify(workflow.settings ?? {}));
const meta = sqlEscape(JSON.stringify(workflow.meta ?? {}));
const workflowId = sqlEscape(workflow.id);

const sql = `update workflow_entity
set name = '${name}',
    nodes = '${nodes}'::json,
    connections = '${connections}'::json,
    settings = '${settings}'::json,
    meta = '${meta}'::json,
    active = true,
    "updatedAt" = now()
where id = '${workflowId}';

update workflow_history
set nodes = '${nodes}'::json,
    connections = '${connections}'::json
where "workflowId" = '${workflowId}';
`;

fs.writeFileSync(sqlPath, sql, 'utf8');
