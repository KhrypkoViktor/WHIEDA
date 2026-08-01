const fs = require('fs');

const inputPath = process.argv[2];
const outputPath = process.argv[3];

if (!inputPath || !outputPath) {
  console.error('Usage: node patch_review_freeform_feedback.js <input.json> <output.json>');
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
      "const isFeedbackCommand = /^(fail|correct|style|missing|ошибка|не\\s*так|неверно|исправь|исправить|дополни|добавь|уточни)\\s*[:\\-]/i.test(trimmedText);\nconst isAskCommand = /^\\/ask(?:@\\w+)?(?:\\s|$)/i.test(trimmedText);\nconst mentionsBot = botUsername ? lowerText.includes('@' + botUsername) : false;\nconst replyUser = msg?.reply_to_message?.from;\nconst isReplyToThisBot = !!replyUser?.is_bot && (!botUsername || normalizeBotUsername(replyUser.username) === botUsername);\nconst shouldReply = isPrivateChat || isFeedbackCommand || isAskCommand || mentionsBot || isReplyToThisBot;",
      [
        "const isFeedbackCommand = /^(fail|correct|style|missing|ошибка|не\\s*так|неверно|исправь|исправить|дополни|добавь|уточни|стиль)\\s*[:\\-]/i.test(trimmedText);",
        "const isAskCommand = /^\\/ask(?:@\\w+)?(?:\\s|$)/i.test(trimmedText);",
        "const mentionsBot = botUsername ? lowerText.includes('@' + botUsername) : false;",
        "const replyUser = msg?.reply_to_message?.from;",
        "const isReplyToThisBot = !!replyUser?.is_bot && (!botUsername || normalizeBotUsername(replyUser.username) === botUsername);",
        "const naturalFeedbackSignals = /не\\s+то|не\\s+так|сух|слишком\\s+длин|слишком\\s+корот|живее|бит(ая|ый)|ссылка\\s+не\\s+работ|фото\\s+не\\s+то|ответ\\s+сух|исправ|добав|уточни|ошибк|неверн/i;",
        "const isNaturalFeedback = isReplyToThisBot && naturalFeedbackSignals.test(trimmedText) && !isAskCommand && !mentionsBot;",
        "const shouldReply = isPrivateChat || isFeedbackCommand || isNaturalFeedback || isAskCommand || mentionsBot || isReplyToThisBot;",
      ].join('\n'),
      'normalize payload freeform feedback detection',
    );

    code = mustReplace(
      code,
      "    display_name: displayName,\n    raw_payload: item,\n  }\n}];",
      [
        "    display_name: displayName,",
        "    is_feedback_candidate: isFeedbackCommand || isNaturalFeedback,",
        "    feedback_input_mode: isFeedbackCommand ? 'explicit' : (isNaturalFeedback ? 'natural_reply' : null),",
        "    raw_payload: item,",
        "  }",
        "}];",
      ].join('\n'),
      'normalize payload output flags',
    );

    node.parameters.jsCode = code;
  }

  if (
    (node.name === 'Code: Structured Sheet Lookup' || node.name === 'Code: Structured Resource Lookup')
    && typeof node?.parameters?.jsCode === 'string'
  ) {
    let code = node.parameters.jsCode;
    code = mustReplace(
      code,
      "const isFeedbackCommand = /^(fail|correct|style|missing|ошибка|не\\s*так|неверно|исправь|исправить|дополни|добавь|уточни)[:\\s-]/i.test(userText);\nif (isFeedbackCommand) {\n  return [{ json: envelope }];\n}",
      [
        "const isFeedbackCommand = /^(fail|correct|style|missing|ошибка|не\\s*так|неверно|исправь|исправить|дополни|добавь|уточни|стиль)[:\\s-]/i.test(userText);",
        "const isNaturalFeedback = envelope.is_feedback_candidate === true && envelope.feedback_input_mode === 'natural_reply';",
        "if (isFeedbackCommand || isNaturalFeedback) {",
        "  return [{ json: envelope }];",
        "}",
      ].join('\n'),
      `${node.name} freeform feedback bypass`,
    );
    node.parameters.jsCode = code;
  }

  if (node.name === 'Code: Validate Dify Response' && typeof node?.parameters?.jsCode === 'string') {
    let code = node.parameters.jsCode;

    code = mustReplace(
      code,
      "  const feedbackMatch = userText.match(/^(fail|correct|style|missing|ошибка|не\\s*так|неверно|исправь|исправить|дополни|добавь|уточни)[:\\s-]+(.+)/i);\n  if (feedbackMatch) {\n    const rawFeedbackType = feedbackMatch[1].toLowerCase();\n    const feedbackText = feedbackMatch[2].trim();\n    let feedbackType = rawFeedbackType;\n    if (/^(ошибка|не\\s*так|неверно)$/i.test(rawFeedbackType)) feedbackType = 'fail';\n    else if (/^(исправь|исправить)$/i.test(rawFeedbackType)) feedbackType = 'correct';\n    else if (/^(дополни|добавь|уточни)$/i.test(rawFeedbackType)) feedbackType = 'missing';\n    return {\n      answer_text: \"Спасибо, замечание записал на проверку.\",\n      action: 'reply',\n      answer_mode: 'feedback_ack',\n      confidence: 1,\n      knowledge_gap: true,\n      gap_reason: 'User feedback: ' + feedbackType,\n      feedback_type: feedbackType,\n      feedback_text: feedbackText,\n      state_updates: [],\n      knowledge_candidates: [],\n      task_candidates: [],\n      followup_questions: [],\n      needs_human_review: false\n    };\n  }",
      [
        "  function inferFeedbackType(text) {",
        "    const raw = String(text || '').toLowerCase();",
        "    if (/сух|живее|тон|подач|слишком\\s+длин|слишком\\s+корот|вкус|формулировк/.test(raw)) return 'style';",
        "    if (/добав|уточни|не\\s+хватает|не\\s+раскрыт|подробн/.test(raw)) return 'missing';",
        "    if (/исправ|замени|лучше\\s+так|правильн/.test(raw)) return 'correct';",
        "    return 'fail';",
        "  }",
        "",
        "  function buildFeedbackAck(feedbackType, feedbackText, mode = 'feedback_ack') {",
        "    return {",
        "      answer_text: \"Спасибо, замечание записал на проверку.\",",
        "      action: 'reply',",
        "      answer_mode: mode,",
        "      confidence: 1,",
        "      knowledge_gap: true,",
        "      gap_reason: 'User feedback: ' + feedbackType,",
        "      feedback_type: feedbackType,",
        "      feedback_text: feedbackText,",
        "      state_updates: [],",
        "      knowledge_candidates: [],",
        "      task_candidates: [],",
        "      followup_questions: [],",
        "      needs_human_review: false",
        "    };",
        "  }",
        "",
        "  const feedbackMatch = userText.match(/^(fail|correct|style|missing|ошибка|не\\s*так|неверно|исправь|исправить|дополни|добавь|уточни|стиль)[:\\s-]+(.+)/i);",
        "  if (feedbackMatch) {",
        "    const rawFeedbackType = feedbackMatch[1].toLowerCase();",
        "    const feedbackText = feedbackMatch[2].trim();",
        "    let feedbackType = rawFeedbackType;",
        "    if (/^(ошибка|не\\s*так|неверно)$/i.test(rawFeedbackType)) feedbackType = 'fail';",
        "    else if (/^(исправь|исправить)$/i.test(rawFeedbackType)) feedbackType = 'correct';",
        "    else if (/^(дополни|добавь|уточни)$/i.test(rawFeedbackType)) feedbackType = 'missing';",
        "    else if (/^стиль$/i.test(rawFeedbackType)) feedbackType = 'style';",
        "    return buildFeedbackAck(feedbackType, feedbackText);",
        "  }",
        "",
        "  if (item.is_feedback_candidate === true && item.feedback_input_mode === 'natural_reply') {",
        "    const feedbackText = String(item.user_message_text || item.message_text || '').trim();",
        "    const feedbackType = inferFeedbackType(feedbackText);",
        "    return buildFeedbackAck(feedbackType, feedbackText, 'feedback_ack_natural');",
        "  }",
      ].join('\n'),
      'validate freeform feedback policy override',
    );

    node.parameters.jsCode = code;
  }
}

fs.writeFileSync(outputPath, JSON.stringify([workflow], null, 2));
console.log(outputPath);
