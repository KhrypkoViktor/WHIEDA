const fs = require('fs');

const inputPath = process.argv[2];
const outputPath = process.argv[3];

if (!inputPath || !outputPath) {
  console.error('Usage: node patch_review_loop_classifier.js <input.json> <output.json>');
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
  if (node.name === 'Code: Validate Dify Response' && typeof node?.parameters?.jsCode === 'string') {
    let code = node.parameters.jsCode;

    code = mustReplace(
      code,
      "function responseRewrite(text) {\n  return text;\n}\n\nfunction pack(base, reason = null) {",
      [
        "function classifyReviewMeta(base, answerText, gapReason, difySystemError) {",
        "  const feedbackType = String(base.feedback_type || '').toLowerCase();",
        "  const feedbackText = String(base.feedback_text || '');",
        "  const raw = [feedbackText, userText, gapReason, answerText].filter(Boolean).join(' ').toLowerCase();",
        "  const has = (regex) => regex.test(raw);",
        "  const isFeedback = base.answer_mode === 'feedback_ack';",
        "",
        "  if (has(/опасн|противопоказ|беремен|лактац|диагноз|лечит|лечение|онколог|давлен|варикоз|врач|медицин|обостр|инсульт|инфаркт/)) {",
        "    return { source_type: isFeedback ? 'feedback' : 'knowledge_gap', review_type: 'medical_review', target_layer: 'medical_safety', priority: 'high', owner: 'medical' };",
        "  }",
        "",
        "  if (has(/фото|картин|изображен|ссылк|pdf|презент|документ|video|видео|media|resource|drive|файл/)) {",
        "    return { source_type: isFeedback ? 'feedback' : 'knowledge_gap', review_type: 'media_issue', target_layer: 'resource_links', priority: 'medium', owner: 'admin' };",
        "  }",
        "",
        "  if (has(/цен|стоим|pv|w\\$|sku|артикул|партнер|повторк|повторн|рознич|первичк|регистрац/)) {",
        "    return { source_type: isFeedback ? 'feedback' : 'knowledge_gap', review_type: 'structured_data_issue', target_layer: 'products_prices', priority: 'high', owner: 'admin' };",
        "  }",
        "",
        "  if (has(/алиас|синоним|не находит|не нашел|не нашла|не понимает|поиск/)) {",
        "    return { source_type: isFeedback ? 'feedback' : 'knowledge_gap', review_type: 'structured_data_issue', target_layer: 'product_aliases', priority: 'medium', owner: 'admin' };",
        "  }",
        "",
        "  if (has(/бандл|связк|комплект|набор|что взять|для энергии|для иммунитета|для сустав/)) {",
        "    return { source_type: isFeedback ? 'feedback' : 'knowledge_gap', review_type: 'content_task', target_layer: 'solution_bundles', priority: 'medium', owner: 'business' };",
        "  }",
        "",
        "  if (has(/сухо|слишком длин|не продает|слабо|канцеляр|тон|подач|первое сообщ|второе сообщ|вкус|лидер/)) {",
        "    return { source_type: isFeedback ? 'feedback' : 'knowledge_gap', review_type: 'business_voice_review', target_layer: 'voice_rules', priority: 'medium', owner: 'business' };",
        "  }",
        "",
        "  if (has(/дубл|два раза|завис|не ответил|долго думал|ошибка workflow|роутинг|падает|не сработал|не отправил/)) {",
        "    return { source_type: isFeedback ? 'feedback' : 'knowledge_gap', review_type: 'runtime_bug', target_layer: 'n8n_workflow', priority: 'high', owner: 'dev' };",
        "  }",
        "",
        "  if (feedbackType === 'style') {",
        "    return { source_type: 'feedback', review_type: 'business_voice_review', target_layer: 'voice_rules', priority: 'medium', owner: 'business' };",
        "  }",
        "",
        "  if (feedbackType === 'correct' || feedbackType === 'fail') {",
        "    return { source_type: 'feedback', review_type: 'answer_error', target_layer: 'product_cards', priority: 'medium', owner: 'content' };",
        "  }",
        "",
        "  if (feedbackType === 'missing') {",
        "    return { source_type: 'feedback', review_type: 'content_task', target_layer: 'dify_knowledge', priority: 'medium', owner: 'content' };",
        "  }",
        "",
        "  if (difySystemError) {",
        "    return { source_type: 'knowledge_gap', review_type: 'runtime_bug', target_layer: 'n8n_workflow', priority: 'high', owner: 'dev' };",
        "  }",
        "",
        "  if (base.knowledge_gap) {",
        "    return { source_type: 'knowledge_gap', review_type: 'knowledge_gap', target_layer: 'dify_knowledge', priority: 'medium', owner: 'content' };",
        "  }",
        "",
        "  return { source_type: isFeedback ? 'feedback' : 'knowledge_gap', review_type: 'content_task', target_layer: 'dify_knowledge', priority: 'low', owner: 'content' };",
        "}",
        "",
        "function responseRewrite(text) {",
        "  return text;",
        "}",
        "",
        "function pack(base, reason = null) {",
      ].join('\n'),
      'insert review classifier',
    );

    code = mustReplace(
      code,
      "  const difySystemError = !!reason\n    || String(gapReason ?? '').startsWith('Dify output missing')\n    || String(gapReason ?? '').includes('not valid JSON')\n    || answerText === 'Dify returned no valid llm_txt output.';\n\n  const route = difySystemError\n    ? 'invalid'\n    : needsHumanReview\n      ? 'escalate'\n      : knowledgeGap\n        ? 'gap_true'\n        : 'answer';\n",
      [
        "  const difySystemError = !!reason",
        "    || String(gapReason ?? '').startsWith('Dify output missing')",
        "    || String(gapReason ?? '').includes('not valid JSON')",
        "    || answerText === 'Dify returned no valid llm_txt output.';",
        "",
        "  const reviewMeta = classifyReviewMeta(base, answerText, gapReason, difySystemError);",
        "",
        "  const route = difySystemError",
        "    ? 'invalid'",
        "    : needsHumanReview",
        "      ? 'escalate'",
        "      : knowledgeGap",
        "        ? 'gap_true'",
        "        : 'answer';",
      ].join('\n'),
      'compute review meta in pack',
    );

    code = mustReplace(
      code,
      "      route,\n      validation_error: difySystemError ? (gapReason ?? 'Dify response invalid') : null,\n      dify_response: base\n    }\n  }];\n}",
      [
        "      route,",
        "      feedback_type: base.feedback_type ?? null,",
        "      feedback_text: base.feedback_text ?? null,",
        "      review_source_type: reviewMeta.source_type,",
        "      review_type: reviewMeta.review_type,",
        "      target_layer: reviewMeta.target_layer,",
        "      review_priority: reviewMeta.priority,",
        "      review_owner: reviewMeta.owner,",
        "      validation_error: difySystemError ? (gapReason ?? 'Dify response invalid') : null,",
        "      dify_response: {",
        "        ...base,",
        "        review_source_type: reviewMeta.source_type,",
        "        review_type: reviewMeta.review_type,",
        "        target_layer: reviewMeta.target_layer,",
        "        review_priority: reviewMeta.priority,",
        "        review_owner: reviewMeta.owner,",
        "      }",
        "    }",
        "  }];",
        "}",
      ].join('\n'),
      'attach review meta to output',
    );

    node.parameters.jsCode = code;
  }

  if (node.name === 'Postgres: Write Review Queue' && typeof node?.parameters?.query === 'string') {
    node.parameters.query = mustReplace(
      node.parameters.query,
      "'{{ String($json.dify_response?.answer_mode === 'feedback_ack' ? 'feedback' : 'knowledge_gap').replace(/'/g, '') }}',\n  '{{ String($json.idempotency_key ?? '').replace(/'/g, '') }}',\n  '{{ String(($json.user_message_text || $json.message_text || '').slice(0, 100)).replace(/'/g, '') }}',\n  '{{ JSON.stringify({kind: $json.dify_response?.answer_mode === 'feedback_ack' ? 'feedback' : 'knowledge_gap', feedback_type: $json.dify_response?.feedback_type || null, feedback_text: $json.dify_response?.feedback_text || null, gap_reason: $json.dify_response?.gap_reason || 'unspecified', user_text: $json.user_message_text || '', conversation_id: $json.conversation_id, project_id: $json.project_id || 'whieda'}).replace(/'/g, '') }}'::jsonb,\n  'pending'",
      [
        "'{{ String($json.review_source_type ?? ($json.dify_response?.answer_mode === 'feedback_ack' ? 'feedback' : 'knowledge_gap')).replace(/'/g, '') }}',",
        "  '{{ String($json.idempotency_key ?? '').replace(/'/g, '') }}',",
        "  '{{ String(($json.user_message_text || $json.message_text || '').slice(0, 100)).replace(/'/g, '') }}',",
        "  '{{ JSON.stringify({",
        "    kind: $json.review_source_type ?? ($json.dify_response?.answer_mode === 'feedback_ack' ? 'feedback' : 'knowledge_gap'),",
        "    review_type: $json.review_type || null,",
        "    target_layer: $json.target_layer || null,",
        "    priority: $json.review_priority || null,",
        "    owner: $json.review_owner || null,",
        "    feedback_type: $json.feedback_type || $json.dify_response?.feedback_type || null,",
        "    feedback_text: $json.feedback_text || $json.dify_response?.feedback_text || null,",
        "    gap_reason: $json.gap_reason || $json.dify_response?.gap_reason || 'unspecified',",
        "    user_text: $json.user_message_text || '',",
        "    bot_answer: $json.reply_text || $json.message_text || '',",
        "    conversation_id: $json.conversation_id,",
        "    project_id: $json.project_id || 'whieda'",
        "  }).replace(/'/g, '') }}'::jsonb,",
        "  'pending'",
      ].join('\n'),
      'extend review queue payload',
    );
  }
}

fs.writeFileSync(outputPath, JSON.stringify([workflow], null, 2));
console.log(outputPath);
