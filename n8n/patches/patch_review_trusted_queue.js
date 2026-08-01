const fs = require('fs');

const inputPath = process.argv[2];
const outputPath = process.argv[3];

if (!inputPath || !outputPath) {
  console.error('Usage: node patch_review_trusted_queue.js <input.json> <output.json>');
  process.exit(1);
}

const parsed = JSON.parse(fs.readFileSync(inputPath, 'utf8'));
const workflow = Array.isArray(parsed) ? parsed[0] : parsed;

if (!workflow || !Array.isArray(workflow.nodes)) {
  throw new Error('Workflow nodes not found');
}

function mustReplace(source, searchValue, replaceValue, label) {
  if (!source.includes(searchValue)) {
    throw new Error(`Pattern not found: ${label}`);
  }
  return source.replace(searchValue, replaceValue);
}

for (const node of workflow.nodes) {
  if (node.name === 'Code: Normalize Payload' && typeof node?.parameters?.jsCode === 'string') {
    let code = node.parameters.jsCode;

    code = mustReplace(
      code,
      "function normalizeBotUsername(value) {\n  return String(value || '').replace(/^@/, '').toLowerCase();\n}\n\nconst botUsername = normalizeBotUsername(ADVISOR_BOT_USERNAME);",
      [
        "function normalizeBotUsername(value) {",
        "  return String(value || '').replace(/^@/, '').toLowerCase();",
        "}",
        "",
        "const TRUSTED_REVIEWERS = [",
        "  { username: 'sunraysword', role: 'super_admin', display: 'Viktor Khripko', telegram_user_id: null },",
        "  { username: 'onlineelena', role: 'business', display: 'Elena Datskevich', telegram_user_id: null },",
        "];",
        "",
        "function resolveTrustedReviewer(username, externalUserId) {",
        "  const normalized = normalizeBotUsername(username);",
        "  return TRUSTED_REVIEWERS.find((reviewer) => {",
        "    const byUserId = reviewer.telegram_user_id && String(reviewer.telegram_user_id) === String(externalUserId || '');",
        "    const byUsername = reviewer.username && reviewer.username === normalized;",
        "    return !!(byUserId || byUsername);",
        "  }) || null;",
        "}",
        "",
        "const botUsername = normalizeBotUsername(ADVISOR_BOT_USERNAME);",
      ].join('\n'),
      'insert trusted reviewers config',
    );

    code = mustReplace(
      code,
      "const displayName = msg?.from?.first_name ?? null;\n\nconst errors = [];",
      [
        "const displayName = msg?.from?.first_name ?? null;",
        "const externalUsername = normalizeBotUsername(msg?.from?.username || '');",
        "const trustedReviewer = resolveTrustedReviewer(externalUsername, externalUserId);",
        "const isTrustedReviewer = !!trustedReviewer;",
        "",
        "const errors = [];",
      ].join('\n'),
      'trusted reviewer resolution',
    );

    code = mustReplace(
      code,
      "    display_name: displayName,\n    is_feedback_candidate: isFeedbackCommand || isNaturalFeedback,\n    feedback_input_mode: isFeedbackCommand ? 'explicit' : (isNaturalFeedback ? 'natural_reply' : null),\n    raw_payload: item,\n  }\n}];",
      [
        "    display_name: displayName,",
        "    external_username: externalUsername || null,",
        "    trusted_reviewer: isTrustedReviewer,",
        "    trusted_reviewer_role: trustedReviewer?.role || null,",
        "    trusted_reviewer_name: trustedReviewer?.display || null,",
        "    is_feedback_candidate: isFeedbackCommand || isNaturalFeedback,",
        "    feedback_input_mode: isFeedbackCommand ? 'explicit' : (isNaturalFeedback ? 'natural_reply' : null),",
        "    raw_payload: item,",
        "  }",
        "}];",
      ].join('\n'),
      'trusted reviewer output fields',
    );

    node.parameters.jsCode = code;
  }

  if (node.name === 'Code: Merge Envelope + Session' && typeof node?.parameters?.jsCode === 'string') {
    let code = node.parameters.jsCode;
    code = mustReplace(
      code,
      "    user_message_text: envelope.user_message_text ?? envelope.message_text ?? '',\n    user_id: userId,\n    conversation_id: conversationId,\n    session_error: conversationId ? null : 'Missing conversation_id from Postgres: Upsert User + Conversation'\n  }\n}];",
      [
        "    user_message_text: envelope.user_message_text ?? envelope.message_text ?? '',",
        "    user_id: userId,",
        "    conversation_id: conversationId,",
        "    session_error: conversationId ? null : 'Missing conversation_id from Postgres: Upsert User + Conversation',",
        "    trusted_reviewer: envelope.trusted_reviewer === true,",
        "    trusted_reviewer_role: envelope.trusted_reviewer_role ?? null,",
        "    trusted_reviewer_name: envelope.trusted_reviewer_name ?? null,",
        "    external_username: envelope.external_username ?? null,",
        "  }",
        "}];",
      ].join('\n'),
      'merge trusted reviewer fields',
    );
    node.parameters.jsCode = code;
  }

  if (node.name === 'Code: Validate Dify Response' && typeof node?.parameters?.jsCode === 'string') {
    let code = node.parameters.jsCode;

    code = mustReplace(
      code,
      "function classifyReviewMeta(base, answerText, gapReason, difySystemError) {\n  const feedbackType = String(base.feedback_type || '').toLowerCase();",
      [
        "function classifyReviewMeta(base, answerText, gapReason, difySystemError) {",
        "  const feedbackType = String(base.feedback_type || '').toLowerCase();",
        "  const trustedReviewer = item.trusted_reviewer === true;",
        "  const trustedReviewerRole = String(item.trusted_reviewer_role || '');",
      ].join('\n'),
      'trusted reviewer vars in classifier',
    );

    code = mustReplace(
      code,
      "  const isFeedback = base.answer_mode === 'feedback_ack';\n",
      [
        "  const isFeedback = base.answer_mode === 'feedback_ack' || base.answer_mode === 'feedback_ack_natural';",
      ].join('\n'),
      'extended feedback ack modes',
    );

    code = mustReplace(
      code,
      "  return { source_type: isFeedback ? 'feedback' : 'knowledge_gap', review_type: 'content_task', target_layer: 'dify_knowledge', priority: 'low', owner: 'content' };\n}",
      [
        "  return { source_type: isFeedback ? 'feedback' : 'knowledge_gap', review_type: 'content_task', target_layer: 'dify_knowledge', priority: 'low', owner: 'content' };",
        "}",
        "",
        "function deriveReviewQueueMeta(base, reviewMeta) {",
        "  const trustedReviewer = item.trusted_reviewer === true;",
        "  const role = String(item.trusted_reviewer_role || '').toLowerCase();",
        "  const highRisk = reviewMeta.priority === 'high' || reviewMeta.review_type === 'runtime_bug' || reviewMeta.review_type === 'medical_review';",
        "  const queueStatus = trustedReviewer || highRisk ? 'pending' : 'candidate';",
        "  const trustLevel = trustedReviewer ? 'trusted' : 'candidate';",
        "  const reviewerOwner = trustedReviewer",
        "    ? (role === 'super_admin' ? 'super_admin' : role || reviewMeta.owner)",
        "    : reviewMeta.owner;",
        "  return {",
        "    queue_status: queueStatus,",
        "    trust_level: trustLevel,",
        "    reviewer_owner: reviewerOwner,",
        "  };",
        "}",
      ].join('\n'),
      'insert queue derivation',
    );

    code = mustReplace(
      code,
      "  const reviewMeta = classifyReviewMeta(base, answerText, gapReason, difySystemError);\n\n  const route = difySystemError",
      [
        "  const reviewMeta = classifyReviewMeta(base, answerText, gapReason, difySystemError);",
        "  const queueMeta = deriveReviewQueueMeta(base, reviewMeta);",
        "",
        "  const route = difySystemError",
      ].join('\n'),
      'compute queue meta',
    );

    code = mustReplace(
      code,
      "      review_owner: reviewMeta.owner,\n      validation_error: difySystemError ? (gapReason ?? 'Dify response invalid') : null,\n      dify_response: {\n        ...base,\n        review_source_type: reviewMeta.source_type,\n        review_type: reviewMeta.review_type,\n        target_layer: reviewMeta.target_layer,\n        review_priority: reviewMeta.priority,\n        review_owner: reviewMeta.owner,\n      }\n    }\n  }];\n}",
      [
        "      review_owner: queueMeta.reviewer_owner,",
        "      review_queue_status: queueMeta.queue_status,",
        "      review_trust_level: queueMeta.trust_level,",
        "      trusted_reviewer: item.trusted_reviewer === true,",
        "      trusted_reviewer_role: item.trusted_reviewer_role ?? null,",
        "      trusted_reviewer_name: item.trusted_reviewer_name ?? null,",
        "      validation_error: difySystemError ? (gapReason ?? 'Dify response invalid') : null,",
        "      dify_response: {",
        "        ...base,",
        "        review_source_type: reviewMeta.source_type,",
        "        review_type: reviewMeta.review_type,",
        "        target_layer: reviewMeta.target_layer,",
        "        review_priority: reviewMeta.priority,",
        "        review_owner: queueMeta.reviewer_owner,",
        "        review_queue_status: queueMeta.queue_status,",
        "        review_trust_level: queueMeta.trust_level,",
        "        trusted_reviewer: item.trusted_reviewer === true,",
        "        trusted_reviewer_role: item.trusted_reviewer_role ?? null,",
        "        trusted_reviewer_name: item.trusted_reviewer_name ?? null,",
        "      }",
        "    }",
        "  }];",
        "}",
      ].join('\n'),
      'attach queue meta to output',
    );

    node.parameters.jsCode = code;
  }

  if (node.name === 'Postgres: Write Review Queue' && typeof node?.parameters?.query === 'string') {
    let query = node.parameters.query;
    query = mustReplace(
      query,
      "  '{{ JSON.stringify({\n    kind: $json.review_source_type ?? ($json.dify_response?.answer_mode === 'feedback_ack' ? 'feedback' : 'knowledge_gap'),\n    review_type: $json.review_type || null,\n    target_layer: $json.target_layer || null,\n    priority: $json.review_priority || null,\n    owner: $json.review_owner || null,\n    feedback_type: $json.feedback_type || $json.dify_response?.feedback_type || null,\n    feedback_text: $json.feedback_text || $json.dify_response?.feedback_text || null,\n    gap_reason: $json.gap_reason || $json.dify_response?.gap_reason || 'unspecified',\n    user_text: $json.user_message_text || '',\n    bot_answer: $json.reply_text || $json.message_text || '',\n    conversation_id: $json.conversation_id,\n    project_id: $json.project_id || 'whieda'\n  }).replace(/'/g, '') }}'::jsonb,\n  'pending'",
      [
        "  '{{ JSON.stringify({",
        "    kind: $json.review_source_type ?? ($json.dify_response?.answer_mode === 'feedback_ack' ? 'feedback' : 'knowledge_gap'),",
        "    review_type: $json.review_type || null,",
        "    target_layer: $json.target_layer || null,",
        "    priority: $json.review_priority || null,",
        "    owner: $json.review_owner || null,",
        "    queue_status: $json.review_queue_status || 'pending',",
        "    trust_level: $json.review_trust_level || 'candidate',",
        "    trusted_reviewer: $json.trusted_reviewer === true,",
        "    trusted_reviewer_role: $json.trusted_reviewer_role || null,",
        "    trusted_reviewer_name: $json.trusted_reviewer_name || null,",
        "    external_username: $json.external_username || null,",
        "    feedback_type: $json.feedback_type || $json.dify_response?.feedback_type || null,",
        "    feedback_text: $json.feedback_text || $json.dify_response?.feedback_text || null,",
        "    gap_reason: $json.gap_reason || $json.dify_response?.gap_reason || 'unspecified',",
        "    user_text: $json.user_message_text || '',",
        "    bot_answer: $json.reply_text || $json.message_text || '',",
        "    conversation_id: $json.conversation_id,",
        "    project_id: $json.project_id || 'whieda'",
        "  }).replace(/'/g, '') }}'::jsonb,",
        "  '{{ String($json.review_queue_status || 'pending').replace(/'/g, '') }}'",
      ].join('\n'),
      'review queue payload and status',
    );
    node.parameters.query = query;
  }
}

fs.writeFileSync(outputPath, JSON.stringify([workflow], null, 2));
console.log(outputPath);
