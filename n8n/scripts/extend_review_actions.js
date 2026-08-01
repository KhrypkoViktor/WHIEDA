const fs = require('fs');

const inputPath = process.argv[2];
const outputPath = process.argv[3];

if (!inputPath || !outputPath) {
  console.error('Usage: node extend_review_actions.js <input.json> <output.json>');
  process.exit(1);
}

const parsed = JSON.parse(fs.readFileSync(inputPath, 'utf8'));
const workflow = parsed && parsed.data ? parsed.data : parsed;

if (!workflow || !Array.isArray(workflow.nodes) || !workflow.connections) {
  throw new Error('Workflow structure not found');
}

function getNode(name) {
  const node = workflow.nodes.find((item) => item.name === name);
  if (!node) {
    throw new Error(`Node not found: ${name}`);
  }
  return node;
}

function replaceRegexOrThrow(source, pattern, replacement, label) {
  if (!pattern.test(source)) {
    throw new Error(`Pattern not found: ${label}`);
  }
  return source.replace(pattern, replacement);
}

function upsertHelpLines(code) {
  const lines = [
    "    '/review_high - высокий приоритет',",
    "    '/review_next - следующая открытая задача',",
    "    '/review_take <ref> - взять задачу в работу',",
    "    '/review_apply <ref> - отметить как применено',",
    "    '/review_verify <ref> - отметить как проверено',",
    "    '/review_close <ref> - закрыть задачу',",
    "    '/review_stats media - фильтр по слову или роли',",
  ].join('\n');

  return replaceRegexOrThrow(
    code,
    / {4}'\/review_high - высокий приоритет',[\s\S]*? {4}'\/review_stats media - фильтр по слову или роли',/,
    lines,
    'review help lines',
  );
}

const normalizeNode = getNode('Code: Normalize Payload');
normalizeNode.parameters.jsCode = replaceRegexOrThrow(
  normalizeNode.parameters.jsCode,
  /\/\^\\\/review_\((?:today\|pending\|candidates\|stats\|high\|help\|next(?:\|take\|close)?)\)\(\?:@\\w\+\)\?\(\?:\\s\|\$\)\/i/,
  "/^\\/review_(today|pending|candidates|stats|high|help|next|take|apply|verify|close)(?:@\\w+)?(?:\\s|$)/i",
  'normalize review command regex',
);

const structuredNode = getNode('Code: Structured Sheet Lookup');
let structuredCode = structuredNode.parameters.jsCode;

structuredCode = replaceRegexOrThrow(
  structuredCode,
  /\/\^\\\/review_\((?:today\|pending\|candidates\|stats\|high\|help\|next(?:\|take\|close)?)\)\(\?:@\\w\+\)\?\(\?:\\s\+\(\.\*\)\)\?\$\/i/,
  "/^\\/review_(today|pending|candidates|stats|high|help|next|take|apply|verify|close)(?:@\\w+)?(?:\\s+(.*))?$/i",
  'structured review command regex',
);

structuredCode = replaceRegexOrThrow(
  structuredCode,
  /function buildReviewNext\(command, envelope\) \{[\s\S]*?\n\}\n\nfunction buildReviewAction/,
  [
    "function buildReviewNext(command, envelope) {",
    "  const trusted = envelope.trusted_reviewer === true;",
    "  if (!trusted) {",
    "    return 'Эта команда доступна только trusted reviewers.';",
    "  }",
    "",
    "  const rows = rowsFromReviewSnapshot();",
    "  const filtered = rows.filter((row) => matchesFilter(row, command.filter));",
    "  const actionable = filtered",
    "    .filter((row) => ['pending', 'triage', 'approved', 'in_work', 'applied'].includes(row.queue_status))",
    "    .sort((a, b) => {",
    "      const pr = priorityRank(a.priority) - priorityRank(b.priority);",
    "      if (pr !== 0) return pr;",
    "      return String(b.created_at || '').localeCompare(String(a.created_at || ''));",
    "    });",
    "",
    "  const next = actionable[0] || null;",
    "  if (!next) {",
    "    return command.filter ? 'По этому фильтру открытых задач сейчас нет.' : 'Открытых задач сейчас нет.';",
    "  }",
    "",
    "  return [",
    "    'Review next:',",
    "    'priority: ' + (next.priority || 'no-priority'),",
    "    'owner: ' + (next.owner || 'unassigned'),",
    "    'type: ' + (next.review_type || next.target_layer || 'review'),",
    "    'status: ' + (next.queue_status || 'pending'),",
    "    'text: ' + shortText(next.short_text || next.source_title, 140),",
    "    next.external_username ? ('from: ' + next.external_username) : null,",
    "    next.source_ref ? ('ref: ' + next.source_ref) : null,",
    "  ].filter(Boolean).join('\\n');",
    "}",
    "",
    "function buildReviewAction",
  ].join('\n'),
  'rewrite buildReviewNext',
);

structuredCode = replaceRegexOrThrow(
  structuredCode,
  /function buildReviewAction\(command, envelope\) \{[\s\S]*?\n\}\n\nfunction buildReviewReport/,
  [
    "function buildReviewAction(command, envelope) {",
    "  const trusted = envelope.trusted_reviewer === true;",
    "  if (!trusted) {",
    "    return 'Эта команда доступна только trusted reviewers.';",
    "  }",
    "",
    "  const ref = String(command.filter || '').trim();",
    "  if (!ref) {",
    "    const usage = {",
    "      take: 'Нужен ref: /review_take <source_ref>',",
    "      apply: 'Нужен ref: /review_apply <source_ref>',",
    "      verify: 'Нужен ref: /review_verify <source_ref>',",
    "      close: 'Нужен ref: /review_close <source_ref>',",
    "    };",
    "    return usage[command.kind] || 'Нужен ref review item.';",
    "  }",
    "",
    "  const rows = $items('Postgres: Review Queue Action')",
    "    .map((item) => item.json || {})",
    "    .filter((row) => row && row.action_kind);",
    "",
    "  const row = rows[0] || null;",
    "  if (!row || row.action_applied !== true) {",
    "    return 'Задача не найдена или действие не применилось.';",
    "  }",
    "",
    "  const titles = {",
    "    take: 'Взято в работу',",
    "    apply: 'Отмечено как применено',",
    "    verify: 'Отмечено как проверено',",
    "    close: 'Закрыто',",
    "  };",
    "",
    "  return [",
    "    (titles[row.action_kind] || 'Обновлено') + ':',",
    "    'owner: ' + (row.owner || 'unassigned'),",
    "    'type: ' + (row.review_type || row.target_layer || 'review'),",
    "    'status: ' + (row.queue_status || row.status || 'updated'),",
    "    'text: ' + shortText(row.short_text || row.source_title, 140),",
    "    row.external_username ? ('from: ' + row.external_username) : null,",
    "    row.source_ref ? ('ref: ' + row.source_ref) : null,",
    "  ].filter(Boolean).join('\\n');",
    "}",
    "",
    "function buildReviewReport",
  ].join('\n'),
  'rewrite buildReviewAction',
);

structuredCode = upsertHelpLines(structuredCode);

structuredCode = replaceRegexOrThrow(
  structuredCode,
  /if \(reviewCommand\) \{[\s\S]*?const answerText = reviewCommand\.kind === 'help'[\s\S]*?buildReviewReport\(reviewCommand, envelope\)[\s\S]*?;\n/,
  [
    "if (reviewCommand) {",
    "  const answerText = reviewCommand.kind === 'help'",
    "    ? buildReviewHelp()",
    "    : (reviewCommand.kind === 'next'",
    "      ? buildReviewNext(reviewCommand, envelope)",
    "      : (reviewCommand.kind === 'take' || reviewCommand.kind === 'apply' || reviewCommand.kind === 'verify' || reviewCommand.kind === 'close')",
    "        ? buildReviewAction(reviewCommand, envelope)",
    "        : buildReviewReport(reviewCommand, envelope));",
  ].join('\n'),
  'route review commands',
);

structuredNode.parameters.jsCode = structuredCode;

const snapshotNode = getNode('Postgres: Review Queue Snapshot');
snapshotNode.parameters.query = [
  "SELECT COALESCE(json_agg(row_to_json(rows)), '[]'::json) AS review_rows",
  'FROM (',
  '  SELECT',
  '    client_id,',
  '    source_ref,',
  '    source_title,',
  '    source_type,',
  '    status,',
  "    COALESCE(queue_status, source_payload->>'queue_status', status) AS queue_status,",
  "    COALESCE(trust_level, source_payload->>'trust_level', CASE WHEN COALESCE(source_payload->>'trusted_reviewer', 'false') = 'true' THEN 'trusted' ELSE 'candidate' END) AS trust_level,",
  "    COALESCE(owner, review_owner, source_payload->>'owner', source_payload->>'review_owner', '') AS owner,",
  "    COALESCE(priority, review_priority, source_payload->>'priority', source_payload->>'review_priority', '') AS priority,",
  "    COALESCE(review_type, source_payload->>'review_type', '') AS review_type,",
  "    COALESCE(target_layer, source_payload->>'target_layer', '') AS target_layer,",
  "    COALESCE(external_username, source_payload->>'external_username', '') AS external_username,",
  "    COALESCE(trusted_reviewer_name, source_payload->>'trusted_reviewer_name', '') AS trusted_reviewer_name,",
  "    COALESCE(trusted_reviewer_role, source_payload->>'trusted_reviewer_role', '') AS trusted_reviewer_role,",
  "    COALESCE(",
  '      feedback_text,',
  '      gap_reason,',
  '      user_text,',
  "      source_payload->>'feedback_text',",
  "      source_payload->>'gap_reason',",
  "      source_payload->>'user_text',",
  '      source_title,',
  "      ''",
  '    ) AS short_text,',
  '    taken_by,',
  '    taken_at,',
  '    applied_by,',
  '    applied_at,',
  '    verified_by,',
  '    verified_at,',
  '    closed_by,',
  '    closed_at,',
  '    triaged_at,',
  '    created_at',
  '  FROM advisor_review_queue',
  "  WHERE client_id = 'whieda'",
  '  ORDER BY created_at DESC',
  '  LIMIT 120',
  ') rows;',
].join('\n');

const actionPrepNode = getNode('Code: Review Queue Action Prep');
actionPrepNode.parameters.jsCode = [
  "const envelope = { ...$('Code: Merge Envelope + Session').first().json, ...$input.first().json };",
  "const text = String(envelope.message_text ?? '').trim();",
  "const match = text.match(/^\\/review_(take|apply|verify|close)(?:@\\w+)?(?:\\s+(.*))?$/i);",
  "const actionKind = match ? String(match[1] || '').toLowerCase() : null;",
  "const sourceRef = match ? String(match[2] || '').trim() : '';",
  'function esc(value) {',
  "  return String(value ?? '').replace(/'/g, \"''\");",
  '}',
  'return [{',
  '  json: {',
  '    ...$input.first().json,',
  '    action_kind: actionKind,',
  '    source_ref: sourceRef,',
  '    source_ref_sql: esc(sourceRef),',
  "    actor_username_sql: esc(envelope.external_username || ''),",
  "    actor_role_sql: esc(envelope.trusted_reviewer_role || ''),",
  "    actor_name_sql: esc(envelope.trusted_reviewer_name || ''),",
  "    trusted_sql: envelope.trusted_reviewer === true ? 'TRUE' : 'FALSE',",
  '  },',
  '}];',
].join('\n');

const actionNode = getNode('Postgres: Review Queue Action');
actionNode.parameters.query = [
  'WITH cmd AS (',
  '  SELECT',
  "    NULLIF('{{ $json.action_kind || '' }}', '') AS action_kind,",
  "    NULLIF('{{ $json.source_ref_sql || '' }}', '') AS source_ref,",
  "    '{{ $json.actor_username_sql || '' }}' AS actor_username,",
  "    '{{ $json.actor_role_sql || '' }}' AS actor_role,",
  "    '{{ $json.actor_name_sql || '' }}' AS actor_name,",
  "    {{ $json.trusted_sql || 'FALSE' }} AS trusted",
  '),',
  'updated AS (',
  '  UPDATE advisor_review_queue q',
  '  SET',
  '    queue_status = CASE cmd.action_kind',
  "      WHEN 'take' THEN 'in_work'",
  "      WHEN 'apply' THEN 'applied'",
  "      WHEN 'verify' THEN 'verified'",
  "      WHEN 'close' THEN 'closed'",
  '      ELSE COALESCE(q.queue_status, q.status)',
  '    END,',
  '    status = CASE cmd.action_kind',
  "      WHEN 'take' THEN 'in_work'",
  "      WHEN 'apply' THEN 'applied'",
  "      WHEN 'verify' THEN 'verified'",
  "      WHEN 'close' THEN 'closed'",
  '      ELSE q.status',
  '    END,',
  "    owner = CASE WHEN cmd.action_kind = 'take' AND COALESCE(q.owner, '') = '' THEN nullif(cmd.actor_role, '') ELSE q.owner END,",
  "    review_owner = CASE WHEN cmd.action_kind = 'take' AND COALESCE(q.review_owner, '') = '' THEN nullif(cmd.actor_role, '') ELSE q.review_owner END,",
  "    taken_by = CASE WHEN cmd.action_kind = 'take' THEN nullif(cmd.actor_username, '') ELSE q.taken_by END,",
  "    taken_at = CASE WHEN cmd.action_kind = 'take' THEN now() ELSE q.taken_at END,",
  "    triaged_at = CASE WHEN cmd.action_kind = 'take' THEN COALESCE(q.triaged_at, now()) ELSE q.triaged_at END,",
  "    applied_by = CASE WHEN cmd.action_kind = 'apply' THEN nullif(cmd.actor_username, '') ELSE q.applied_by END,",
  "    applied_at = CASE WHEN cmd.action_kind = 'apply' THEN now() ELSE q.applied_at END,",
  "    verified_by = CASE WHEN cmd.action_kind = 'verify' THEN nullif(cmd.actor_username, '') ELSE q.verified_by END,",
  "    verified_at = CASE WHEN cmd.action_kind = 'verify' THEN now() ELSE q.verified_at END,",
  "    closed_by = CASE WHEN cmd.action_kind = 'close' THEN nullif(cmd.actor_username, '') ELSE q.closed_by END,",
  "    closed_at = CASE WHEN cmd.action_kind = 'close' THEN now() ELSE q.closed_at END,",
  '    updated_at = now(),',
  '    source_payload = jsonb_strip_nulls(',
  '      COALESCE(q.source_payload, \'{}\'::jsonb) || jsonb_build_object(',
  "        'queue_status', CASE cmd.action_kind",
  "          WHEN 'take' THEN 'in_work'",
  "          WHEN 'apply' THEN 'applied'",
  "          WHEN 'verify' THEN 'verified'",
  "          WHEN 'close' THEN 'closed'",
  "          ELSE COALESCE(q.queue_status, q.status)",
  '        END,',
  "        'owner', COALESCE(q.owner, q.review_owner, q.source_payload->>'owner', q.source_payload->>'review_owner'),",
  "        'priority', COALESCE(q.priority, q.review_priority, q.source_payload->>'priority', q.source_payload->>'review_priority'),",
  "        'taken_by_username', CASE WHEN cmd.action_kind = 'take' THEN nullif(cmd.actor_username, '') ELSE q.source_payload->>'taken_by_username' END,",
  "        'taken_by_role', CASE WHEN cmd.action_kind = 'take' THEN nullif(cmd.actor_role, '') ELSE q.source_payload->>'taken_by_role' END,",
  "        'taken_by_name', CASE WHEN cmd.action_kind = 'take' THEN nullif(cmd.actor_name, '') ELSE q.source_payload->>'taken_by_name' END,",
  "        'taken_at', CASE WHEN cmd.action_kind = 'take' THEN to_char(now() at time zone 'utc', 'YYYY-MM-DD\"T\"HH24:MI:SS\"Z\"') ELSE q.source_payload->>'taken_at' END,",
  "        'triaged_at', CASE WHEN cmd.action_kind = 'take' THEN to_char(coalesce(q.triaged_at, now()) at time zone 'utc', 'YYYY-MM-DD\"T\"HH24:MI:SS\"Z\"') ELSE q.source_payload->>'triaged_at' END,",
  "        'applied_by_username', CASE WHEN cmd.action_kind = 'apply' THEN nullif(cmd.actor_username, '') ELSE q.source_payload->>'applied_by_username' END,",
  "        'applied_by_role', CASE WHEN cmd.action_kind = 'apply' THEN nullif(cmd.actor_role, '') ELSE q.source_payload->>'applied_by_role' END,",
  "        'applied_by_name', CASE WHEN cmd.action_kind = 'apply' THEN nullif(cmd.actor_name, '') ELSE q.source_payload->>'applied_by_name' END,",
  "        'applied_at', CASE WHEN cmd.action_kind = 'apply' THEN to_char(now() at time zone 'utc', 'YYYY-MM-DD\"T\"HH24:MI:SS\"Z\"') ELSE q.source_payload->>'applied_at' END,",
  "        'verified_by_username', CASE WHEN cmd.action_kind = 'verify' THEN nullif(cmd.actor_username, '') ELSE q.source_payload->>'verified_by_username' END,",
  "        'verified_by_role', CASE WHEN cmd.action_kind = 'verify' THEN nullif(cmd.actor_role, '') ELSE q.source_payload->>'verified_by_role' END,",
  "        'verified_by_name', CASE WHEN cmd.action_kind = 'verify' THEN nullif(cmd.actor_name, '') ELSE q.source_payload->>'verified_by_name' END,",
  "        'verified_at', CASE WHEN cmd.action_kind = 'verify' THEN to_char(now() at time zone 'utc', 'YYYY-MM-DD\"T\"HH24:MI:SS\"Z\"') ELSE q.source_payload->>'verified_at' END,",
  "        'closed_by_username', CASE WHEN cmd.action_kind = 'close' THEN nullif(cmd.actor_username, '') ELSE q.source_payload->>'closed_by_username' END,",
  "        'closed_by_role', CASE WHEN cmd.action_kind = 'close' THEN nullif(cmd.actor_role, '') ELSE q.source_payload->>'closed_by_role' END,",
  "        'closed_by_name', CASE WHEN cmd.action_kind = 'close' THEN nullif(cmd.actor_name, '') ELSE q.source_payload->>'closed_by_name' END,",
  "        'closed_at', CASE WHEN cmd.action_kind = 'close' THEN to_char(now() at time zone 'utc', 'YYYY-MM-DD\"T\"HH24:MI:SS\"Z\"') ELSE q.source_payload->>'closed_at' END",
  '      )',
  '    )',
  '  FROM cmd',
  "  WHERE cmd.trusted = TRUE",
  '    AND cmd.action_kind IS NOT NULL',
  '    AND cmd.source_ref IS NOT NULL',
  "    AND q.client_id = 'whieda'",
  '    AND q.source_ref = cmd.source_ref',
  '  RETURNING',
  '    q.client_id,',
  '    q.source_type,',
  '    q.source_ref,',
  '    q.source_title,',
  '    q.status,',
  '    q.queue_status,',
  "    COALESCE(q.owner, q.review_owner, '') AS owner,",
  "    COALESCE(q.priority, q.review_priority, '') AS priority,",
  "    COALESCE(q.review_type, '') AS review_type,",
  "    COALESCE(q.target_layer, '') AS target_layer,",
  "    COALESCE(q.external_username, '') AS external_username,",
  "    COALESCE(q.feedback_text, q.gap_reason, q.user_text, q.source_title, '') AS short_text,",
  '    cmd.action_kind,',
  '    TRUE AS action_applied',
  ')',
  'SELECT * FROM updated',
  'UNION ALL',
  'SELECT',
  "  'whieda' AS client_id,",
  "  'telegram_feedback' AS source_type,",
  '  cmd.source_ref,',
  "  '' AS source_title,",
  "  '' AS status,",
  "  '' AS queue_status,",
  "  '' AS owner,",
  "  '' AS priority,",
  "  '' AS review_type,",
  "  '' AS target_layer,",
  "  '' AS external_username,",
  "  '' AS short_text,",
  '  cmd.action_kind,',
  '  FALSE AS action_applied',
  'FROM cmd',
  'WHERE NOT EXISTS (SELECT 1 FROM updated);',
].join('\n');

fs.writeFileSync(outputPath, JSON.stringify(parsed, null, 2));
console.log(outputPath);
