const item = $input.first().json;
const response = item.dify_response;

const userText = String(
  item.user_message_text ??
  item.raw_payload?.body?.message?.text ??
  item.raw_payload?.message?.text ??
  item.message_text ??
  ''
).toLowerCase();
const projectId = String(item.project_id ?? 'whieda').toLowerCase();
const syntheticTest = item.raw_payload?.body?.whieda_synthetic_test === true || item.raw_payload?.whieda_synthetic_test === true;

function stableIndex(seed, size) {
  const text = String(seed || '');
  let hash = 0;
  for (let i = 0; i < text.length; i++) hash = ((hash * 31) + text.charCodeAt(i)) >>> 0;
  return size > 0 ? hash % size : 0;
}

function humanFallbackText(reason = '') {
  const variants = [
    'Не хочу здесь гадать. Лучше передам вопрос на уточнение.',
    'По этому месту у меня пока нет подтвержденного ответа. Отмечу на проверку.',
    'Здесь лучше не фантазировать. Передам вопрос человеку для уточнения.',
    'Тут нужна точная проверка, поэтому не буду угадывать и отправлю на разбор.',
    'Общий смысл я понимаю, но этот нюанс лучше подтвердить отдельно.',
    'В утвержденных материалах этого пока не вижу. Помечу на доработку.'
  ];
  return variants[stableIndex(item.idempotency_key || item.message_text || reason, variants.length)];
}

function whiedaCorePriceOverride() {
  if (projectId !== 'whieda') return null;
  const q = String(userText || '').toLowerCase();
  const s = (...codes) => String.fromCharCode(...codes);
  const any = (...words) => words.some((word) => q.includes(word));
  const ru = {
    skolko: s(1089,1082,1086,1083,1100,1082),
    tsena: s(1094,1077,1085),
    stoit: s(1089,1090,1086,1080,1090),
    aktivator: s(1072,1082,1090,1080,1074,1072,1090,1086,1088),
    pro: s(1087,1088,1086),
    bagua: s(1073,1072,1075,1091,1072),
    sauna: s(1089,1072,1091,1085),
    ventun: s(1074,1101,1085,1090,1091,1085),
    ven: s(1074,1077,1085),
    tun: s(1090,1091,1085),
    rasskazhi: s(1088,1072,1089,1089,1082,1072,1078,1080),
    chto: s(1095,1090,1086),
    delaet: s(1076,1077,1083,1072,1077,1090)
  };

  const asksPrice = any(ru.skolko, ru.tsena, ru.stoit, 'price', 'cost');
  const hasActivator = any(ru.aktivator);
  const hasPro = any('pro', ru.pro);
  const hasBagua = any(ru.bagua, s(1073,1072,45,1075,1091,1072)) || (any(ru.sauna) && any(s(1075,1091,1072)));

  let answer = null;
  if (asksPrice && hasActivator && hasPro) {
    answer = s(1040,1082,1090,1080,1074,1072,1090,1086,1088,32,1082,1083,1077,1090,1086,1082,32,80,82,79,58,32,1088,1086,1079,1085,1080,1094,1072,32,54,53,32,48,48,48,32,8381,32,47,32,49,32,55,53,48,32,66,89,78,32,40,53,48,48,32,87,36,41,44,32,1087,1072,1088,1090,1085,1077,1088,1089,1082,1072,1103,32,52,48,32,48,48,48,32,8381,32,47,32,49,32,48,53,48,32,66,89,78,32,40,51,48,48,32,87,36,41,46);
  } else if (asksPrice && hasActivator) {
    answer = s(1040,1082,1090,1080,1074,1072,1090,1086,1088,32,1082,1083,1077,1090,1086,1082,58,32,1088,1086,1079,1085,1080,1094,1072,32,53,48,32,48,48,48,32,8381,32,47,32,49,32,55,53,48,32,66,89,78,32,40,53,48,48,32,87,36,41,44,32,1087,1072,1088,1090,1085,1077,1088,1089,1082,1072,1103,32,51,48,32,48,48,48,32,8381,32,47,32,49,32,48,53,48,32,66,89,78,32,40,51,48,48,32,87,36,41,46,32,1059,32,87,72,73,69,68,65,32,1077,1089,1090,1100,32,1089,1090,1072,1088,1072,1103,32,1080,32,1085,1086,1074,1072,1103,32,1084,1086,1076,1077,1083,1100,59,32,1086,1085,1080,32,1086,1090,1083,1080,1095,1072,1102,1090,1089,1103,32,1094,1077,1085,1086,1081,32,1080,32,1074,1085,1077,1096,1085,1080,1084,32,1074,1080,1076,1086,1084,46);
  } else if (asksPrice && hasBagua) {
    answer = s(1052,1080,1085,1080,1089,1072,1091,1085,1072,32,1041,1040,45,1043,1059,1040,58,32,1088,1086,1079,1085,1080,1094,1072,32,54,56,32,48,48,48,32,8381,32,47,32,49,32,55,53,48,32,66,89,78,32,40,53,48,48,32,87,36,41,44,32,1087,1072,1088,1090,1085,1077,1088,1089,1082,1072,1103,32,52,53,32,48,48,48,32,8381,32,47,32,49,32,48,53,48,32,66,89,78,32,40,51,48,48,32,87,36,41,46);
  } else if (!asksPrice && hasActivator && any(ru.rasskazhi, ru.chto, ru.delaet)) {
    answer = s(1040,1082,1090,1080,1074,1072,1090,1086,1088,32,1082,1083,1077,1090,1086,1082,32,87,72,73,69,68,65,32,45,32,1101,1090,1086,32,1087,1088,1080,1073,1086,1088,32,1076,1083,1103,32,1072,1087,1087,1072,1088,1072,1090,1085,1086,1081,32,1087,1086,1076,1076,1077,1088,1078,1082,1080,46,32,1042,32,1084,1072,1090,1077,1088,1080,1072,1083,1072,1093,32,87,72,73,69,68,65,32,1086,1085,32,1086,1087,1080,1089,1072,1085,32,1082,1072,1082,32,1087,1088,1080,1073,1086,1088,32,1089,32,1090,1077,1088,1072,1075,1077,1088,1094,1077,1074,1099,1084,32,1074,1086,1079,1076,1077,1081,1089,1090,1074,1080,1077,1084,44,32,1082,1086,1090,1086,1088,1099,1081,32,1080,1089,1087,1086,1083,1100,1079,1091,1102,1090,32,1076,1083,1103,32,1087,1088,1086,1075,1088,1077,1074,1072,44,32,1084,1077,1088,1080,1076,1080,1072,1085,1086,1074,32,1080,32,1083,1086,1082,1072,1083,1100,1085,1099,1093,32,1079,1086,1085,46,32,1071,32,1084,1086,1075,1091,32,1088,1072,1089,1089,1082,1072,1079,1072,1090,1100,32,1086,1090,1083,1080,1095,1080,1103,32,1089,1090,1072,1088,1086,1081,32,1080,32,1085,1086,1074,1086,1081,32,1084,1086,1076,1077,1083,1080,32,1080,1083,1080,32,1076,1072,1090,1100,32,1082,1088,1072,1090,1082,1091,1102,32,1080,1085,1089,1090,1088,1091,1082,1094,1080,1102,32,1087,1086,32,1087,1088,1080,1084,1077,1085,1077,1085,1080,1102,46);
  } else if (any('wentong', ru.ventun) || (any(ru.ven) && any(ru.tun))) {
    answer = s(1042,1101,1085,1090,1091,1085,32,49,46,48,32,40,87,101,110,116,111,110,103,41,32,45,32,1087,1088,1080,1073,1086,1088,32,87,72,73,69,68,65,46,32,1042,32,1084,1072,1090,1077,1088,1080,1072,1083,1072,1093,32,1086,1085,32,1086,1087,1080,1089,1072,1085,32,1082,1072,1082,32,1082,1086,1084,1073,1080,1085,1072,1094,1080,1103,32,1048,1050,45,1080,1079,1083,1091,1095,1077,1085,1080,1103,32,1076,1072,1083,1100,1085,1077,1075,1086,32,1089,1087,1077,1082,1090,1088,1072,44,32,76,69,68,45,1090,1077,1088,1072,1087,1080,1080,44,32,1080,1086,1085,1080,1079,1072,1094,1080,1080,32,1080,32,1085,1072,1075,1088,1077,1074,1072,1090,1077,1083,1100,1085,1086,1075,1086,32,1087,1086,1103,1089,1072,46,32,1063,1072,1097,1077,32,1074,1089,1077,1075,1086,32,1077,1075,1086,32,1089,1074,1103,1079,1099,1074,1072,1102,1090,32,1089,32,1084,1080,1082,1088,1086,1094,1080,1088,1082,1091,1083,1103,1094,1080,1077,1081,44,32,1088,1072,1089,1089,1083,1072,1073,1083,1077,1085,1080,1077,1084,44,32,1088,1072,1073,1086,1090,1086,1081,32,1089,32,34,1093,1086,1083,1086,1076,1086,1084,32,1080,32,1074,1083,1072,1075,1086,1081,34,32,1074,32,1083,1086,1075,1080,1082,1077,32,1058,1050,1052,46);
  }
  if (!answer) return null;
  return { answer_text: answer, action: 'reply', answer_mode: 'direct_answer', confidence: 1, knowledge_gap: false, gap_reason: null, state_updates: [], knowledge_candidates: [], task_candidates: [], followup_questions: [], needs_human_review: false };
}

function policyOverride() {
  function stableIndex(seed, size) {
    const text = String(seed || '');
    let hash = 0;
    for (let i = 0; i < text.length; i++) hash = ((hash * 31) + text.charCodeAt(i)) >>> 0;
    return size > 0 ? hash % size : 0;
  }

  function humanFallbackText(reason = '') {
    const variants = [
  "Не хочу сейчас гадать. Лучше передам это на уточнение команде.",
  "У меня пока нет подтверждённого ответа по этому вопросу. Отмечу его на проверку.",
  "Здесь лучше не фантазировать. Передам вопрос человеку для уточнения."
];
    return variants[stableIndex(item.idempotency_key || item.message_text || reason, variants.length)];
  }

  function inferFeedbackType(text) {
    const raw = String(text || '').toLowerCase();
    if (/сух|живее|тон|подач|слишком\s+длин|слишком\s+корот|вкус|формулировк/.test(raw)) return 'style';
    if (/добав|уточни|не\s+хватает|не\s+раскрыт|подробн/.test(raw)) return 'missing';
    if (/исправ|замени|лучше\s+так|правильн/.test(raw)) return 'correct';
    return 'fail';
  }

  function buildFeedbackAck(feedbackType, feedbackText, mode = 'feedback_ack') {
    return {
      answer_text: "Спасибо, замечание записал на проверку.",
      action: 'reply',
      answer_mode: mode,
      confidence: 1,
      knowledge_gap: true,
      gap_reason: 'User feedback: ' + feedbackType,
      feedback_type: feedbackType,
      feedback_text: feedbackText,
      state_updates: [],
      knowledge_candidates: [],
      task_candidates: [],
      followup_questions: [],
      needs_human_review: false
    };
  }

  const feedbackMatch = userText.match(/^(fail|correct|style|missing|ошибка|не\s*так|неверно|исправь|исправить|дополни|добавь|уточни|стиль)[:\s-]+(.+)/i);
  if (feedbackMatch) {
    const rawFeedbackType = feedbackMatch[1].toLowerCase();
    const feedbackText = feedbackMatch[2].trim();
    let feedbackType = rawFeedbackType;
    if (/^(ошибка|не\s*так|неверно)$/i.test(rawFeedbackType)) feedbackType = 'fail';
    else if (/^(исправь|исправить)$/i.test(rawFeedbackType)) feedbackType = 'correct';
    else if (/^(дополни|добавь|уточни)$/i.test(rawFeedbackType)) feedbackType = 'missing';
    else if (/^стиль$/i.test(rawFeedbackType)) feedbackType = 'style';
    return buildFeedbackAck(feedbackType, feedbackText);
  }

  if (item.is_feedback_candidate === true && item.feedback_input_mode === 'natural_reply') {
    const feedbackText = String(item.user_message_text || item.message_text || '').trim();
    const feedbackType = inferFeedbackType(feedbackText);
    return buildFeedbackAck(feedbackType, feedbackText, 'feedback_ack_natural');
  }

  if (/^\/(start|help)(?:\s|$)/i.test(userText)) {
    return {
      answer_text: "Привет. Я WHIEDA Advisor. В группах можно писать с упоминанием @whieda_advisor_bot, реплаем на сообщение бота или через /ask. Если ответ неверный или неполный, пиши так: Ошибка: ..., Не так: ..., Исправь: ..., Добавь: ...",
      action: 'reply',
      answer_mode: 'start_help',
      confidence: 1,
      knowledge_gap: false,
      gap_reason: null,
      state_updates: [],
      knowledge_candidates: [],
      task_candidates: [],
      followup_questions: [],
      needs_human_review: false
    };
  }

  if (/guarantee.*(?:income|earn|profit|result)|guaranteed.*(?:income|earn|profit|result)|zero\s+risk|no\s+risk|risk[-\s]?free/i.test(userText)) {
    return {
      answer_text: "Я не могу обещать гарантированный доход, результат или отсутствие риска. Такой вопрос нужно передать человеку.",
      action: 'escalate',
      answer_mode: 'escalate',
      confidence: 1,
      knowledge_gap: false,
      gap_reason: null,
      state_updates: [],
      knowledge_candidates: [],
      task_candidates: [],
      followup_questions: [],
      needs_human_review: true
    };
  }

  return null;
}

const PROJECT_CANONICAL_ANSWERS = [];

function canonicalAnswerOverride() {
  for (const rule of PROJECT_CANONICAL_ANSWERS) {
    if (!rule || rule.enabled === false) continue;
    const matchAll = Array.isArray(rule.matchAll) ? rule.matchAll : [];
    const matchAny = Array.isArray(rule.matchAny) ? rule.matchAny : [];
    const allMatch = matchAll.every((pattern) => new RegExp(pattern, 'i').test(userText));
    const anyMatch = matchAny.length === 0 || matchAny.some((pattern) => new RegExp(pattern, 'i').test(userText));
    if (!allMatch || !anyMatch) continue;

    const response = rule.response && typeof rule.response === 'object' ? rule.response : {};
    return {
      answer_text: String(response.answer_text || ''),
      action: response.action || 'reply',
      answer_mode: response.answer_mode || 'direct_answer',
      confidence: Number(response.confidence ?? 1),
      knowledge_gap: !!response.knowledge_gap,
      gap_reason: response.gap_reason ?? null,
      state_updates: Array.isArray(response.state_updates) ? response.state_updates : [],
      knowledge_candidates: Array.isArray(response.knowledge_candidates) ? response.knowledge_candidates : [],
      task_candidates: Array.isArray(response.task_candidates) ? response.task_candidates : [],
      followup_questions: Array.isArray(response.followup_questions) ? response.followup_questions : [],
      needs_human_review: !!response.needs_human_review
    };
  }

  return null;
}

function responseGuard(text) {
  if (
    /do not (?:promise|guarantee|say|call|give|handle|estimate|confuse|present)|not confirmed|forbidden claim|forbidden_claim|approved sources|knowledge gap summary|system prompt|internal note|this needs human review|i have flagged (?:it|this)|flagged (?:it|this) for (?:the )?team/i.test(text)
  ) {
    return {
      answer_text: humanFallbackText(),
      action: 'reply',
      answer_mode: 'fallback',
      confidence: 1,
      knowledge_gap: true,
      gap_reason: 'Response contained internal instruction fragment.',
      state_updates: [],
      knowledge_candidates: [],
      task_candidates: [],
      followup_questions: [],
      needs_human_review: false
    };
  }
  return null;
}

function classifyReviewMeta(base, answerText, gapReason, difySystemError) {
  const feedbackType = String(base.feedback_type || '').toLowerCase();
  const trustedReviewer = item.trusted_reviewer === true;
  const trustedReviewerRole = String(item.trusted_reviewer_role || '');
  const feedbackText = String(base.feedback_text || '');
  const raw = [feedbackText, userText, gapReason, answerText].filter(Boolean).join(' ').toLowerCase();
  const has = (regex) => regex.test(raw);
  const isFeedback = base.answer_mode === 'feedback_ack' || base.answer_mode === 'feedback_ack_natural';
  if (has(/опасн|противопоказ|беремен|лактац|диагноз|лечит|лечение|онколог|давлен|варикоз|врач|медицин|обостр|инсульт|инфаркт/)) {
    return { source_type: isFeedback ? 'feedback' : 'knowledge_gap', review_type: 'medical_review', target_layer: 'medical_safety', priority: 'high', owner: 'medical' };
  }

  if (has(/фото|картин|изображен|ссылк|pdf|презент|документ|video|видео|media|resource|drive|файл/)) {
    return { source_type: isFeedback ? 'feedback' : 'knowledge_gap', review_type: 'media_issue', target_layer: 'resource_links', priority: 'medium', owner: 'admin' };
  }

  if (has(/цен|стоим|pv|w\$|sku|артикул|партнер|повторк|повторн|рознич|первичк|регистрац/)) {
    return { source_type: isFeedback ? 'feedback' : 'knowledge_gap', review_type: 'structured_data_issue', target_layer: 'products_prices', priority: 'high', owner: 'admin' };
  }

  if (has(/алиас|синоним|не находит|не нашел|не нашла|не понимает|поиск/)) {
    return { source_type: isFeedback ? 'feedback' : 'knowledge_gap', review_type: 'structured_data_issue', target_layer: 'product_aliases', priority: 'medium', owner: 'admin' };
  }

  if (has(/бандл|связк|комплект|набор|что взять|для энергии|для иммунитета|для сустав/)) {
    return { source_type: isFeedback ? 'feedback' : 'knowledge_gap', review_type: 'content_task', target_layer: 'solution_bundles', priority: 'medium', owner: 'business' };
  }

  if (has(/сухо|слишком длин|не продает|слабо|канцеляр|тон|подач|первое сообщ|второе сообщ|вкус|лидер/)) {
    return { source_type: isFeedback ? 'feedback' : 'knowledge_gap', review_type: 'business_voice_review', target_layer: 'voice_rules', priority: 'medium', owner: 'business' };
  }

  if (has(/дубл|два раза|завис|не ответил|долго думал|ошибка workflow|роутинг|падает|не сработал|не отправил/)) {
    return { source_type: isFeedback ? 'feedback' : 'knowledge_gap', review_type: 'runtime_bug', target_layer: 'n8n_workflow', priority: 'high', owner: 'dev' };
  }

  if (feedbackType === 'style') {
    return { source_type: 'feedback', review_type: 'business_voice_review', target_layer: 'voice_rules', priority: 'medium', owner: 'business' };
  }

  if (feedbackType === 'correct' || feedbackType === 'fail') {
    return { source_type: 'feedback', review_type: 'answer_error', target_layer: 'product_cards', priority: 'medium', owner: 'content' };
  }

  if (feedbackType === 'missing') {
    return { source_type: 'feedback', review_type: 'content_task', target_layer: 'dify_knowledge', priority: 'medium', owner: 'content' };
  }

  if (difySystemError) {
    return { source_type: 'knowledge_gap', review_type: 'runtime_bug', target_layer: 'n8n_workflow', priority: 'high', owner: 'dev' };
  }

  if (base.knowledge_gap) {
    return { source_type: 'knowledge_gap', review_type: 'knowledge_gap', target_layer: 'dify_knowledge', priority: 'medium', owner: 'content' };
  }

  return { source_type: isFeedback ? 'feedback' : 'knowledge_gap', review_type: 'content_task', target_layer: 'dify_knowledge', priority: 'low', owner: 'content' };
}

function deriveReviewQueueMeta(base, reviewMeta) {
  if (syntheticTest) {
    return { queue_status: 'ignored_test', trust_level: 'system_test', reviewer_owner: 'dev' };
  }
  const trustedReviewer = item.trusted_reviewer === true;
  const highRisk = reviewMeta.priority === 'high' || reviewMeta.review_type === 'runtime_bug' || reviewMeta.review_type === 'medical_review';
  const queueStatus = trustedReviewer || highRisk ? 'pending' : 'candidate';
  const trustLevel = trustedReviewer ? 'trusted' : 'candidate';
  return {
    queue_status: queueStatus,
    trust_level: trustLevel,
    reviewer_owner: reviewMeta.owner,
  };
}

function responseRewrite(text) {
  const deepAnswer = item.deep_requested === true || String(response?.answer_mode || '') === 'deep_internal_library';
  if (deepAnswer && !/(^|\n)\s*Источник\s*:/im.test(text)) {
    return text.trim() + '\n\nИсточник: внутренний материал WHIEDA; точную ссылку могу подобрать отдельно.';
  }
  return text;
}

function pack(base, reason = null) {
  const rawAnswerText = typeof base.answer_text === 'string' ? base.answer_text.trim() : '';
  const difyReturnedInvalidOutput = rawAnswerText === 'Dify returned no valid llm_txt output.';
  const answerText = difyReturnedInvalidOutput ? humanFallbackText('dify_invalid_output') : rawAnswerText;
  const confidence = Number(base.confidence ?? 0);
  const knowledgeGap = !!base.knowledge_gap;
  const needsHumanReview = !!base.needs_human_review;
  const gapReason = reason ?? base.gap_reason ?? null;

  const difySystemError = !!reason
    || String(gapReason ?? '').startsWith('Dify output missing')
    || String(gapReason ?? '').includes('not valid JSON')
    || difyReturnedInvalidOutput;

  const reviewMeta = classifyReviewMeta(base, answerText, gapReason, difySystemError);
  const queueMeta = deriveReviewQueueMeta(base, reviewMeta);

  const route = difySystemError
    ? 'invalid'
    : needsHumanReview
      ? 'escalate'
      : knowledgeGap
        ? 'gap_true'
        : 'answer';
  return [{
    json: {
      ...item,
      user_message_text: item.user_message_text ?? item.raw_payload?.body?.message?.text ?? item.raw_payload?.message?.text ?? '',
      message_text: answerText || humanFallbackText(gapReason || 'empty_answer'),
      reply_text: answerText || humanFallbackText(gapReason || 'empty_answer'),
      action: base.action ?? 'reply',
      answer_mode: base.answer_mode ?? (difySystemError ? 'fallback' : 'direct_answer'),
      confidence,
      knowledge_gap: knowledgeGap,
      gap_reason: gapReason,
      state_updates: Array.isArray(base.state_updates) ? base.state_updates : [],
      knowledge_candidates: Array.isArray(base.knowledge_candidates) ? base.knowledge_candidates : [],
      task_candidates: Array.isArray(base.task_candidates) ? base.task_candidates : [],
      followup_questions: Array.isArray(base.followup_questions) ? base.followup_questions : [],
      needs_human_review: needsHumanReview,
      route,
      feedback_type: base.feedback_type ?? null,
      feedback_text: base.feedback_text ?? null,
      review_source_type: reviewMeta.source_type,
      review_type: reviewMeta.review_type,
      target_layer: reviewMeta.target_layer,
      review_priority: reviewMeta.priority,
      review_owner: queueMeta.reviewer_owner,
      review_queue_status: queueMeta.queue_status,
      review_trust_level: queueMeta.trust_level,
      synthetic_test: syntheticTest,
      trusted_reviewer: item.trusted_reviewer === true,
      trusted_reviewer_role: item.trusted_reviewer_role ?? null,
      trusted_reviewer_name: item.trusted_reviewer_name ?? null,
      validation_error: difySystemError ? (gapReason ?? 'Dify response invalid') : null,
      dify_response: {
        ...base,
        review_source_type: reviewMeta.source_type,
        review_type: reviewMeta.review_type,
        target_layer: reviewMeta.target_layer,
        review_priority: reviewMeta.priority,
        review_owner: queueMeta.reviewer_owner,
        review_queue_status: queueMeta.queue_status,
        review_trust_level: queueMeta.trust_level,
        synthetic_test: syntheticTest,
        trusted_reviewer: item.trusted_reviewer === true,
        trusted_reviewer_role: item.trusted_reviewer_role ?? null,
        trusted_reviewer_name: item.trusted_reviewer_name ?? null,
      }
    }
  }];
}

function fallback(reason) {
  return pack({
    answer_text: humanFallbackText(reason),
    action: 'reply',
    answer_mode: 'fallback',
    confidence: 0,
    knowledge_gap: true,
    gap_reason: reason,
    state_updates: [],
    knowledge_candidates: [],
    task_candidates: [],
    followup_questions: [],
    needs_human_review: true
  }, reason);
}

if (item.session_error) return fallback(item.session_error);
if (!response || typeof response !== 'object') return fallback('Missing parsed dify_response from Normalize Dify Response');

const override = policyOverride();
if (override) return pack(override);

if (item.structured_hit === true) return pack(response);
if (override) return pack(override);

const canonicalOverride = canonicalAnswerOverride();
if (canonicalOverride) return pack(canonicalOverride);

const answerText = typeof response.answer_text === 'string' ? response.answer_text.trim() : '';
if (!answerText) return fallback('Dify response missing answer_text');

const outputOverride = responseGuard(answerText);
if (outputOverride) return pack(outputOverride);

const rewritten = responseRewrite(answerText);
if (rewritten !== answerText) response.answer_text = rewritten;

return pack(response);
