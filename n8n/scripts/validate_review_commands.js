const fs = require('fs');

const inputPath = process.argv[2];
const expectActions = process.argv.includes('--actions');

if (!inputPath) {
  console.error('Usage: node validate_review_commands.js <workflow.json> [--actions]');
  process.exit(1);
}

const parsed = JSON.parse(fs.readFileSync(inputPath, 'utf8'));
const workflow = parsed && parsed.data ? parsed.data : parsed;

if (!workflow || !Array.isArray(workflow.nodes)) {
  throw new Error('Workflow nodes not found');
}

function findNode(name) {
  return workflow.nodes.find((item) => item.name === name);
}

const structuredNode = findNode('Code: Structured Sheet Lookup');
const normalizeNode = findNode('Code: Normalize Payload');
const snapshotNode = findNode('Postgres: Review Queue Snapshot');
const prepNode = findNode('Code: Review Queue Action Prep');
const actionNode = findNode('Postgres: Review Queue Action');

if (!structuredNode?.parameters?.jsCode) throw new Error('Code: Structured Sheet Lookup missing');
if (!normalizeNode?.parameters?.jsCode) throw new Error('Code: Normalize Payload missing');

const normalizeCode = normalizeNode.parameters.jsCode;
const structuredCode = structuredNode.parameters.jsCode;
const snapshotQuery = String(snapshotNode?.parameters?.query || '');
const actionPrepCode = String(prepNode?.parameters?.jsCode || '');
const actionQuery = String(actionNode?.parameters?.query || '');

const checks = [
  ['normalize regex has review commands', expectActions
    ? /review_\(today\|pending\|candidates\|stats\|high\|help\|next\|take\|apply\|verify\|close\)/.test(normalizeCode)
    : /review_\(today\|pending\|candidates\|stats\|high\|help\|next\)/.test(normalizeCode)],
  ['structured regex has review commands', expectActions
    ? /review_\(today\|pending\|candidates\|stats\|high\|help\|next\|take\|apply\|verify\|close\)/.test(structuredCode)
    : /review_\(today\|pending\|candidates\|stats\|high\|help\|next\)/.test(structuredCode)],
  ['review help builder present', structuredCode.includes('function buildReviewHelp()')],
  ['review next builder present', structuredCode.includes('function buildReviewNext(')],
  ['review next uses approved state', structuredCode.includes("['pending', 'triage', 'approved', 'in_work', 'applied']")],
  ['review queue source present', structuredCode.includes("structured_source: 'review_queue'")],
];

if (expectActions) {
  checks.push(
    ['review action builder present', structuredCode.includes('function buildReviewAction(')],
    ['review apply help present', structuredCode.includes('/review_apply <ref> - отметить как применено')],
    ['review verify help present', structuredCode.includes('/review_verify <ref> - отметить как проверено')],
    ['review action route includes apply/verify', structuredCode.includes("reviewCommand.kind === 'take' || reviewCommand.kind === 'apply' || reviewCommand.kind === 'verify' || reviewCommand.kind === 'close'")],
    ['review action prep node present', !!prepNode],
    ['review action node present', !!actionNode],
    ['review action prep regex includes apply/verify', /review_\(take\|apply\|verify\|close\)/.test(actionPrepCode)],
    ['review snapshot uses explicit columns', snapshotQuery.includes('COALESCE(queue_status') && snapshotQuery.includes('COALESCE(owner, review_owner')],
    ['review action updates explicit queue_status', actionQuery.includes('queue_status = CASE cmd.action_kind')],
    ['review action updates status for compatibility', actionQuery.includes('status = CASE cmd.action_kind')],
    ['review action supports apply state', actionQuery.includes("WHEN 'apply' THEN 'applied'")],
    ['review action supports verify state', actionQuery.includes("WHEN 'verify' THEN 'verified'")]
  );
}

const failed = checks.filter(([, ok]) => !ok);

for (const [label, ok] of checks) {
  console.log(`${ok ? 'OK' : 'FAIL'}: ${label}`);
}

if (failed.length) {
  process.exit(2);
}
