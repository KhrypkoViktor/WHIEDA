const fs = require('fs');

const inputPath = process.argv[2];
const outputPath = process.argv[3];

if (!inputPath || !outputPath) {
  console.error('Usage: node patch_review_reports.js <input.json> <output.json>');
  process.exit(1);
}

const parsed = JSON.parse(fs.readFileSync(inputPath, 'utf8'));
const workflow = parsed && parsed.data ? parsed.data : (Array.isArray(parsed) ? parsed[0] : parsed);

if (!workflow || !Array.isArray(workflow.nodes) || !workflow.connections) {
  throw new Error('Workflow structure not found');
}

function mustFindNode(name) {
  const node = workflow.nodes.find((item) => item.name === name);
  if (!node) throw new Error(`Node not found: ${name}`);
  return node;
}

function mustReplace(source, searchValue, replaceValue, label) {
  if (!source.includes(searchValue)) {
    throw new Error(`Pattern not found: ${label}`);
  }
  return source.replace(searchValue, replaceValue);
}

const normalizeNode = mustFindNode('Code: Normalize Payload');
if (typeof normalizeNode?.parameters?.jsCode !== 'string') {
  throw new Error('Code: Normalize Payload jsCode not found');
}

let normalizeCode = normalizeNode.parameters.jsCode;
normalizeCode = mustReplace(
  normalizeCode,
  "const isAskCommand = /^\\/ask(?:@\\w+)?(?:\\s|$)/i.test(trimmedText);\nconst mentionsBot = botUsername ? lowerText.includes('@' + botUsername) : false;",
  [
    "const isAskCommand = /^\\/ask(?:@\\w+)?(?:\\s|$)/i.test(trimmedText);",
    "const isReviewCommand = /^\\/review_(today|pending|candidates)(?:@\\w+)?(?:\\s|$)/i.test(trimmedText);",
    "const mentionsBot = botUsername ? lowerText.includes('@' + botUsername) : false;",
  ].join('\n'),
  'add review command detection',
);
normalizeCode = mustReplace(
  normalizeCode,
  "const shouldReply = isPrivateChat || isFeedbackCommand || isNaturalFeedback || isAskCommand || mentionsBot || isReplyToThisBot;",
  "const shouldReply = isPrivateChat || isFeedbackCommand || isNaturalFeedback || isAskCommand || isReviewCommand || mentionsBot || isReplyToThisBot;",
  'extend shouldReply with review command',
);
normalizeNode.parameters.jsCode = normalizeCode;

const structuredNode = mustFindNode('Code: Structured Sheet Lookup');
if (typeof structuredNode?.parameters?.jsCode !== 'string') {
  throw new Error('Code: Structured Sheet Lookup jsCode not found');
}

let structuredCode = structuredNode.parameters.jsCode;
structuredCode = mustReplace(
  structuredCode,
  "if (isFeedbackCommand || isNaturalFeedback) {\n  return [{ json: envelope }];\n}\n\nfunction normalize(value) {",
  [
    "if (isFeedbackCommand || isNaturalFeedback) {",
    "  return [{ json: envelope }];",
    "}",
    "",
    "function getReviewCommand(text) {",
    "  const match = String(text || '').trim().match(/^\\/review_(today|pending|candidates)(?:@\\w+)?(?:\\s+(.*))?$/i);",
    "  if (!match) return null;",
    "  return {",
    "    kind: String(match[1] || '').toLowerCase(),",
    "    filter: String(match[2] || '').trim(),",
    "  };",
    "}",
    "",
    "function normalize(value) {",
  ].join('\n'),
  'insert review command parser',
);

structuredCode = mustReplace(
  structuredCode,
  "function isActive(value) {\n  return String(value ?? '').toLowerCase() === 'true' || value === true;\n}\n\nfunction money(value) {",
  [
    "function isActive(value) {",
    "  return String(value ?? '').toLowerCase() === 'true' || value === true;",
    "}",
    "",
    "function parseDate(value) {",
    "  const parsed = new Date(value || '');",
    "  return Number.isNaN(parsed.getTime()) ? null : parsed;",
    "}",
    "",
    "function priorityRank(value) {",
    "  switch (String(value || '').toLowerCase()) {",
    "    case 'high': return 0;",
    "    case 'medium': return 1;",
    "    case 'low': return 2;",
    "    default: return 3;",
    "  }",
    "}",
    "",
    "function shortText(value, max = 72) {",
    "  const text = String(value || '').replace(/\\s+/g, ' ').trim();",
    "  return text.length > max ? text.slice(0, max - 3) + '...' : text;",
    "}",
    "",
    "function rowsFromReviewSnapshot() {",
    "  return $items('Postgres: Review Queue Snapshot')",
    "    .flatMap((item) => Array.isArray(item.json.review_rows) ? item.json.review_rows : [item.json])",
    "    .filter(Boolean)",
    "    .map((row) => ({",
    "      client_id: String(row.client_id || PROJECT_ID),",
    "      source_ref: row.source_ref || '',",
    "      source_title: row.source_title || '',",
    "      queue_status: String(row.queue_status || row.status || '').toLowerCase(),",
    "      trust_level: String(row.trust_level || '').toLowerCase(),",
    "      owner: String(row.owner || '').toLowerCase(),",
    "      priority: String(row.priority || '').toLowerCase(),",
    "      review_type: String(row.review_type || '').toLowerCase(),",
    "      target_layer: String(row.target_layer || '').toLowerCase(),",
    "      external_username: String(row.external_username || '').toLowerCase(),",
    "      trusted_reviewer_name: String(row.trusted_reviewer_name || ''),",
    "      short_text: String(row.short_text || row.source_title || '').trim(),",
    "      created_at: row.created_at || null,",
    "    }))",
    "    .filter((row) => row.client_id === PROJECT_ID);",
    "}",
    "",
    "function formatRoleCounts(rows) {",
    "  const counts = new Map();",
    "  for (const row of rows) {",
    "    const key = row.owner || 'unassigned';",
    "    counts.set(key, (counts.get(key) || 0) + 1);",
    "  }",
    "  return [...counts.entries()]",
    "    .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))",
    "    .slice(0, 4)",
    "    .map(([key, value]) => key + ' ' + value)",
    "    .join(', ');",
    "}",
    "",
    "function matchesFilter(row, filter) {",
    "  if (!filter) return true;",
    "  const needle = normalize(filter);",
    "  return [row.owner, row.review_type, row.target_layer, row.external_username, row.short_text]",
    "    .map((value) => normalize(value))",
    "    .some((value) => value.includes(needle));",
    "}",
    "",
    "function buildReviewReport(command, envelope) {",
    "  const trusted = envelope.trusted_reviewer === true;",
    "  if (!trusted) {",
    "    return 'Эта команда доступна только trusted reviewers.';",
    "  }",
    "",
    "  const rows = rowsFromReviewSnapshot();",
    "  const filtered = rows.filter((row) => matchesFilter(row, command.filter));",
    "  const now = new Date();",
    "  const todayStart = new Date(now.getFullYear(), now.getMonth(), now.getDate());",
    "",
    "  let selected = filtered;",
    "  let title = 'Review queue';",
    "",
    "  if (command.kind === 'today') {",
    "    title = 'Review today';",
    "    selected = filtered.filter((row) => {",
    "      const created = parseDate(row.created_at);",
    "      return created && created >= todayStart;",
    "    });",
    "  } else if (command.kind === 'pending') {",
    "    title = 'Review pending';",
    "    selected = filtered.filter((row) => ['pending', 'triage', 'in_work', 'applied'].includes(row.queue_status));",
    "  } else if (command.kind === 'candidates') {",
    "    title = 'Review candidates';",
    "    selected = filtered.filter((row) => row.queue_status === 'candidate' || row.trust_level === 'candidate');",
    "  }",
    "",
    "  selected = selected.sort((a, b) => {",
    "    const pr = priorityRank(a.priority) - priorityRank(b.priority);",
    "    if (pr !== 0) return pr;",
    "    return String(b.created_at || '').localeCompare(String(a.created_at || ''));",
    "  });",
    "",
    "  const summaryRows = command.kind === 'today' ? selected : filtered;",
    "  const pendingCount = summaryRows.filter((row) => ['pending', 'triage', 'in_work', 'applied'].includes(row.queue_status)).length;",
    "  const candidateCount = summaryRows.filter((row) => row.queue_status === 'candidate' || row.trust_level === 'candidate').length;",
    "  const highCount = summaryRows.filter((row) => row.priority === 'high').length;",
    "  const roleSummary = formatRoleCounts(selected);",
    "  const lines = [];",
    "",
    "  lines.push(title + ': ' + selected.length);",
    "  if (command.kind === 'today') {",
    "    lines.push('Открытых: ' + pendingCount + '. Кандидатов: ' + candidateCount + '. High: ' + highCount + '.');",
    "  }",
    "  if (roleSummary) {",
    "    lines.push('По ролям: ' + roleSummary + '.');",
    "  }",
    "",
    "  if (!selected.length) {",
    "    lines.push(command.filter ? 'По этому фильтру сейчас пусто.' : 'Сейчас пусто.');",
    "    return lines.join('\\n');",
    "  }",
    "",
    "  for (const [index, row] of selected.slice(0, 7).entries()) {",
    "    const parts = [",
    "      row.priority || 'no-priority',",
    "      row.owner || 'unassigned',",
    "      row.review_type || row.target_layer || 'review',",
    "    ];",
    "    lines.push((index + 1) + '. ' + parts.join(' | ') + ' | ' + shortText(row.short_text || row.source_title));",
    "  }",
    "",
    "  if (selected.length > 7) {",
    "    lines.push('Еще: ' + (selected.length - 7) + '.');",
    "  }",
    "",
    "  return lines.join('\\n');",
    "}",
    "",
    "function money(value) {",
  ].join('\n'),
  'insert review report helpers',
);

structuredCode = mustReplace(
  structuredCode,
  "const normalizedText = normalize(userText);\n\nfunction parseContext(value) {",
  [
    "const normalizedText = normalize(userText);",
    "const reviewCommand = getReviewCommand(userText);",
    "",
    "if (reviewCommand) {",
    "  const answerText = buildReviewReport(reviewCommand, envelope);",
    "  return [{",
    "    json: {",
    "      ...envelope,",
    "      structured_hit: true,",
    "      structured_source: 'review_queue',",
    "      structured_match: {",
    "        alias: 'review_command',",
    "        sku: null,",
    "        canonical_name: '/review_' + reviewCommand.kind,",
    "      },",
    "      dify_raw: '',",
    "      dify_response: {",
    "        answer_text: answerText,",
    "        action: 'reply',",
    "        answer_mode: 'direct_review_report',",
    "        confidence: 1,",
    "        knowledge_gap: false,",
    "        gap_reason: null,",
    "        state_updates: [],",
    "        knowledge_candidates: [],",
    "        task_candidates: [],",
    "        followup_questions: [],",
    "        needs_human_review: false,",
    "      },",
    "      answer_text: answerText,",
    "      reply_text: answerText,",
    "      action: 'reply',",
    "      answer_mode: 'direct_review_report',",
    "      confidence: 1,",
    "      knowledge_gap: false,",
    "      needs_human_review: false,",
    "      route: 'answer',",
    "    },",
    "  }];",
    "}",
    "",
    "function parseContext(value) {",
  ].join('\n'),
  'insert review command handling',
);
structuredNode.parameters.jsCode = structuredCode;

const reviewSnapshotNodeName = 'Postgres: Review Queue Snapshot';
if (!workflow.nodes.some((item) => item.name === reviewSnapshotNodeName)) {
  workflow.nodes.push({
    parameters: {
      operation: 'executeQuery',
      query: [
        "SELECT COALESCE(json_agg(row_to_json(rows)), '[]'::json) AS review_rows",
        'FROM (',
        '  SELECT',
        '    client_id,',
        '    source_ref,',
        '    source_title,',
        '    status,',
        "    COALESCE(source_payload->>'queue_status', CASE WHEN COALESCE(source_payload->>'trusted_reviewer', 'false') = 'true' THEN 'pending' ELSE CASE WHEN status = 'pending' THEN 'pending' ELSE 'candidate' END END) AS queue_status,",
        "    COALESCE(source_payload->>'trust_level', CASE WHEN COALESCE(source_payload->>'trusted_reviewer', 'false') = 'true' THEN 'trusted' ELSE 'candidate' END) AS trust_level,",
        "    COALESCE(source_payload->>'owner', '') AS owner,",
        "    COALESCE(source_payload->>'priority', '') AS priority,",
        "    COALESCE(source_payload->>'review_type', '') AS review_type,",
        "    COALESCE(source_payload->>'target_layer', '') AS target_layer,",
        "    COALESCE(source_payload->>'external_username', '') AS external_username,",
        "    COALESCE(source_payload->>'trusted_reviewer_name', '') AS trusted_reviewer_name,",
        "    COALESCE(source_payload->>'trusted_reviewer_role', '') AS trusted_reviewer_role,",
        "    COALESCE(source_payload->>'feedback_text', source_payload->>'gap_reason', source_payload->>'user_text', source_title, '') AS short_text,",
        '    created_at',
        '  FROM advisor_review_queue',
        "  WHERE client_id = 'whieda'",
        '  ORDER BY created_at DESC',
        '  LIMIT 120',
        ') rows;',
      ].join('\n'),
      options: {},
    },
    id: 'whieda-review-queue-snapshot',
    name: reviewSnapshotNodeName,
    type: 'n8n-nodes-base.postgres',
    typeVersion: 2.6,
    position: [-9192, -976],
    credentials: {
      postgres: {
        id: 'RmjHh3rdZri7axzq',
        name: 'advisor-dev-postgres',
      },
    },
  });
}

const resourceLinksConn = workflow.connections['Postgres: Resource Links'];
if (!resourceLinksConn?.main?.[0]?.[0]) {
  throw new Error('Postgres: Resource Links connection not found');
}
resourceLinksConn.main[0][0].node = reviewSnapshotNodeName;

workflow.connections[reviewSnapshotNodeName] = {
  main: [[{ node: 'Code: Structured Resource Lookup', type: 'main', index: 0 }]],
};

fs.writeFileSync(outputPath, JSON.stringify(workflow, null, 2));
console.log(outputPath);
