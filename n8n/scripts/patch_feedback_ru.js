const fs = require('fs');

const inputPath = process.argv[2];
const outputPath = process.argv[3];

if (!inputPath || !outputPath) {
  console.error('Usage: node patch_feedback_ru.js <input.json> <output.json>');
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
  if (!node?.parameters?.jsCode || typeof node.parameters.jsCode !== 'string') continue;

  if (node.name === 'Code: Normalize Payload') {
    node.parameters.jsCode = mustReplace(
      node.parameters.jsCode,
      "const isFeedbackCommand = /^(fail|correct|style|missing)\\s*:/i.test(trimmedText);",
      "const isFeedbackCommand = /^(fail|correct|style|missing|ошибка|не\\s*так|неверно|исправь|исправить|дополни|добавь|уточни)\\s*[:\\-]/i.test(trimmedText);",
      'normalize payload feedback regex',
    );
  }

  if (node.name === 'Code: Validate Dify Response') {
    let code = node.parameters.jsCode;

    code = mustReplace(
      code,
      "const feedbackMatch = userText.match(/^(fail|correct|style|missing)[:\\s-]+(.+)/i);",
      "const feedbackMatch = userText.match(/^(fail|correct|style|missing|ошибка|не\\s*так|неверно|исправь|исправить|дополни|добавь|уточни)[:\\s-]+(.+)/i);",
      'validate feedback regex',
    );

    code = mustReplace(
      code,
      "    const feedbackType = feedbackMatch[1].toLowerCase();\n    const feedbackText = feedbackMatch[2].trim();",
      [
        "    const rawFeedbackType = feedbackMatch[1].toLowerCase();",
        "    const feedbackText = feedbackMatch[2].trim();",
        "    let feedbackType = rawFeedbackType;",
        "    if (/^(ошибка|не\\s*так|неверно)$/i.test(rawFeedbackType)) feedbackType = 'fail';",
        "    else if (/^(исправь|исправить)$/i.test(rawFeedbackType)) feedbackType = 'correct';",
        "    else if (/^(дополни|добавь|уточни)$/i.test(rawFeedbackType)) feedbackType = 'missing';",
      ].join('\n'),
      'validate feedback type mapping',
    );

    code = mustReplace(
      code,
      "      answer_text: \"Привет. Я WHIEDA Advisor. Задавай вопросы по подтверждённой базе знаний WHIEDA. В группах упоминай @whieda_advisor_bot, отвечай реплаем на сообщение бота или используй /ask. Если ответ неверный, напиши: Fail: и правильную формулировку.\",",
      "      answer_text: \"Привет. Я WHIEDA Advisor. В группах можно писать с упоминанием @whieda_advisor_bot, реплаем на сообщение бота или через /ask. Если ответ неверный или неполный, пиши так: Ошибка: ..., Не так: ..., Исправь: ..., Добавь: ...\",",
      'validate help text',
    );

    code = mustReplace(
      code,
      "if (item.structured_hit === true) return pack(response);\n\nconst override = policyOverride();",
      "const override = policyOverride();\nif (override) return pack(override);\n\nif (item.structured_hit === true) return pack(response);",
      'validate override ordering',
    );

    node.parameters.jsCode = code;
  }

  if (node.name === 'Code: Structured Sheet Lookup' || node.name === 'Code: Structured Resource Lookup') {
    let code = node.parameters.jsCode;
    code = mustReplace(
      code,
      "const userText = String(envelope.message_text ?? '').trim();",
      [
        "const userText = String(envelope.message_text ?? '').trim();",
        "const isFeedbackCommand = /^(fail|correct|style|missing|ошибка|не\\s*так|неверно|исправь|исправить|дополни|добавь|уточни)[:\\s-]/i.test(userText);",
        "if (isFeedbackCommand) {",
        "  return [{ json: envelope }];",
        "}",
      ].join('\n'),
      `${node.name} feedback bypass`,
    );
    node.parameters.jsCode = code;
  }
}

fs.writeFileSync(outputPath, JSON.stringify([workflow], null, 2));
console.log(outputPath);
