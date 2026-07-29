const PROJECT_ID = 'whieda';

const envelope = { ...$('Code: Merge Envelope + Session').first().json, ...$input.first().json };
const userText = String(envelope.message_text ?? '').trim();
const isFeedbackCommand = /^(fail|correct|style|missing|ошибка|не\s*так|неверно|исправь|исправить|дополни|добавь|уточни|стиль)[:\s-]/i.test(userText);
const isNaturalFeedback = envelope.is_feedback_candidate === true && envelope.feedback_input_mode === 'natural_reply';
if (isFeedbackCommand || isNaturalFeedback) {
  return [{ json: envelope }];
}

function getReviewCommand(text) {
  const match = String(text || '').trim().match(/^\/(review_(today|pending|candidates|stats|high|help|next|take|apply|verify|close)|gap_report)(?:@\w+)?(?:\s+(.*))?$/i);
  if (!match) return null;
  return {
    kind: String(match[2] || '').toLowerCase() || 'gap_report',
    filter: String(match[3] || '').trim(),
  };
}

function normalize(value) {
  return String(value ?? '')
    .toLowerCase()
    .replace(/ё/g, 'е')
    .replace(/["'«»“”„]/g, '')
    .replace(/[^a-zа-я0-9$]+/gi, ' ')
    .replace(/\s+/g, ' ')
    .trim();
}

function normalizeCompact(value) {
  return normalize(value).replace(/\s+/g, '');
}

function levenshteinDistance(a, b, maxDistance = 2) {
  const left = String(a || '');
  const right = String(b || '');
  const leftLen = left.length;
  const rightLen = right.length;
  if (Math.abs(leftLen - rightLen) > maxDistance) return maxDistance + 1;
  const dp = Array.from({ length: rightLen + 1 }, (_, i) => i);
  for (let i = 1; i <= leftLen; i++) {
    let prev = dp[0];
    dp[0] = i;
    let rowMin = dp[0];
    for (let j = 1; j <= rightLen; j++) {
      const temp = dp[j];
      const cost = left[i - 1] === right[j - 1] ? 0 : 1;
      dp[j] = Math.min(
        dp[j] + 1,
        dp[j - 1] + 1,
        prev + cost,
      );
      prev = temp;
      if (dp[j] < rowMin) rowMin = dp[j];
    }
    if (rowMin > maxDistance) return maxDistance + 1;
  }
  return dp[rightLen];
}

function fuzzySingleWordMatch(text, alias) {
  const phrase = normalize(alias);
  if (!phrase || phrase.includes(' ')) return false;
  if (phrase.length < 6) return false;
  const tokens = normalize(text).split(' ').filter(Boolean);
  return tokens.some((token) => token.length >= 5 && levenshteinDistance(token, phrase, 1) <= 1);
}

function isActive(value) {
  return String(value ?? '').toLowerCase() === 'true' || value === true;
}

function parseDate(value) {
  const parsed = new Date(value || '');
  return Number.isNaN(parsed.getTime()) ? null : parsed;
}

function priorityRank(value) {
  switch (String(value || '').toLowerCase()) {
    case 'high': return 0;
    case 'medium': return 1;
    case 'low': return 2;
    default: return 3;
  }
}

function shortText(value, max = 72) {
  const text = String(value || '').replace(/\s+/g, ' ').trim();
  return text.length > max ? text.slice(0, max - 3) + '...' : text;
}

function optionalNodeItems(name) {
  try {
    return $items(name) || [];
  } catch {
    return [];
  }
}

function rowsFromReviewSnapshot() {
  return optionalNodeItems('Postgres: Review Queue Snapshot')
    .flatMap((item) => Array.isArray(item.json.review_rows) ? item.json.review_rows : [item.json])
    .filter(Boolean)
    .map((row) => ({
      client_id: String(row.client_id || PROJECT_ID),
      source_type: String(row.source_type || '').toLowerCase(),
      source_ref: String(row.source_ref || ''),
      source_ref: row.source_ref || '',
      source_title: row.source_title || '',
      queue_status: String(row.queue_status || row.status || '').toLowerCase(),
      trust_level: String(row.trust_level || '').toLowerCase(),
      owner: String(row.owner || '').toLowerCase(),
      priority: String(row.priority || '').toLowerCase(),
      review_type: String(row.review_type || '').toLowerCase(),
      target_layer: String(row.target_layer || '').toLowerCase(),
      external_username: String(row.external_username || '').toLowerCase(),
      trusted_reviewer_name: String(row.trusted_reviewer_name || ''),
      short_text: String(row.short_text || row.source_title || '').trim(),
      created_at: row.created_at || null,
    }))
    .filter((row) => row.client_id === PROJECT_ID);
}

function formatRoleCounts(rows) {
  const counts = new Map();
  for (const row of rows) {
    const key = row.owner || 'unassigned';
    counts.set(key, (counts.get(key) || 0) + 1);
  }
  return [...counts.entries()]
    .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))
    .slice(0, 4)
    .map(([key, value]) => key + ' ' + value)
    .join(', ');
}

function normalizeRoleAlias(value) {
  const key = normalize(value);
  const aliases = {
    admin: 'admin',
    media: 'admin',
    business: 'business',
    biz: 'business',
    voice: 'business',
    content: 'content',
    card: 'content',
    cards: 'content',
    medical: 'medical',
    doctor: 'medical',
    doctors: 'medical',
    dev: 'dev',
    tech: 'dev',
    trusted: 'trusted',
    candidate: 'candidate',
    high: 'high',
    urgent: 'high',
    critical: 'high',
  };
  return aliases[key] || key;
}

function matchesFilter(row, filter) {
  if (!filter) return true;
  const needle = normalizeRoleAlias(filter);
  if (['admin', 'business', 'content', 'medical', 'dev', 'super_admin', 'unassigned'].includes(needle)) {
    return row.owner === needle;
  }
  if (needle === 'trusted') {
    return row.trust_level === 'trusted';
  }
  if (needle === 'candidate') {
    return row.queue_status === 'candidate' || row.trust_level === 'candidate';
  }
  if (needle === 'high') {
    return row.priority === 'high';
  }
  return [row.owner, row.review_type, row.target_layer, row.external_username, row.short_text]
    .map((value) => normalize(value))
    .some((value) => value.includes(needle));
}

function buildReviewHelp() {
  return [
    'Review commands:',
    '/review_stats - общая сводка',
    '/review_today - что пришло сегодня',
    '/review_pending - открытые',
    '/review_candidates - кандидаты',
    '/review_high - высокий приоритет',
    '/review_next - следующая открытая задача',
    '/review_take <ref> - взять задачу в работу',
    '/review_apply <ref> - отметить как применено',
    '/review_verify <ref> - отметить как проверено',
    '/review_close <ref> - закрыть задачу',
    '/review_pending admin - только админ',
    '/review_pending business - только бизнес',
    '/review_pending content - только контент',
    '/review_stats media - фильтр по слову или роли',
  ].join('\n');
}

function buildReviewNext(command, envelope) {
  const trusted = envelope.trusted_reviewer === true;
  if (!trusted) {
    return 'Эта команда доступна только trusted reviewers.';
  }

  const rows = rowsFromReviewSnapshot();
  const filtered = rows.filter((row) => matchesFilter(row, command.filter));
  const actionable = filtered
    .filter((row) => ['pending', 'triage', 'approved', 'in_work', 'applied'].includes(row.queue_status))
    .sort((a, b) => {
      const pr = priorityRank(a.priority) - priorityRank(b.priority);
      if (pr !== 0) return pr;
      return String(b.created_at || '').localeCompare(String(a.created_at || ''));
    });

  const next = actionable[0] || null;
  if (!next) {
    return command.filter ? 'По этому фильтру открытых задач сейчас нет.' : 'Открытых задач сейчас нет.';
  }

  return [
    'Review next:',
    'priority: ' + (next.priority || 'no-priority'),
    'owner: ' + (next.owner || 'unassigned'),
    'type: ' + (next.review_type || next.target_layer || 'review'),
    'status: ' + (next.queue_status || 'pending'),
    'text: ' + shortText(next.short_text || next.source_title, 140),
    next.external_username ? ('from: ' + next.external_username) : null,
    next.source_ref ? ('ref: ' + next.source_ref) : null,
  ].filter(Boolean).join('\n');
}

function buildReviewAction(command, envelope) {
  const trusted = envelope.trusted_reviewer === true;
  if (!trusted) {
    return 'Эта команда доступна только trusted reviewers.';
  }

  const ref = String(command.filter || '').trim();
  if (!ref) {
    const usage = {
      take: 'Нужен ref: /review_take <source_ref>',
      apply: 'Нужен ref: /review_apply <source_ref>',
      verify: 'Нужен ref: /review_verify <source_ref>',
      close: 'Нужен ref: /review_close <source_ref>',
    };
    return usage[command.kind] || 'Нужен ref review item.';
  }

  const rows = $items('Postgres: Review Queue Action')
    .map((item) => item.json || {})
    .filter((row) => row && row.action_kind);

  const row = rows[0] || null;
  if (!row || row.action_applied !== true) {
    return 'Задача не найдена или действие не применилось.';
  }

  const titles = {
    take: 'Взято в работу',
    apply: 'Отмечено как применено',
    verify: 'Отмечено как проверено',
    close: 'Закрыто',
  };

  return [
    (titles[row.action_kind] || 'Обновлено') + ':',
    'owner: ' + (row.owner || 'unassigned'),
    'type: ' + (row.review_type || row.target_layer || 'review'),
    'status: ' + (row.queue_status || row.status || 'updated'),
    'text: ' + shortText(row.short_text || row.source_title, 140),
    row.external_username ? ('from: ' + row.external_username) : null,
    row.source_ref ? ('ref: ' + row.source_ref) : null,
  ].filter(Boolean).join('\n');
}

function buildReviewReport(command, envelope) {
  const trusted = envelope.trusted_reviewer === true;
  if (!trusted) {
    return 'Эта команда доступна только trusted reviewers.';
  }

  const rows = rowsFromReviewSnapshot();
  const filtered = rows.filter((row) => matchesFilter(row, command.filter));
  const now = new Date();
  const todayStart = new Date(now.getFullYear(), now.getMonth(), now.getDate());

  let selected = filtered;
  let title = 'Review queue';

  if (command.kind === 'today') {
    title = 'Review today';
    selected = filtered.filter((row) => {
      const created = parseDate(row.created_at);
      return created && created >= todayStart;
    });
  } else if (command.kind === 'pending') {
    title = 'Review pending';
    selected = filtered.filter((row) => ['pending', 'triage', 'in_work', 'applied'].includes(row.queue_status));
  } else if (command.kind === 'candidates') {
    title = 'Review candidates';
    selected = filtered.filter((row) => row.queue_status === 'candidate' || row.trust_level === 'candidate');
  } else if (command.kind === 'stats') {
    title = 'Review stats';
    selected = filtered;
  } else if (command.kind === 'high') {
    title = 'Review high';
    selected = filtered.filter((row) => row.priority === 'high');
  }

  selected = selected.sort((a, b) => {
    const pr = priorityRank(a.priority) - priorityRank(b.priority);
    if (pr !== 0) return pr;
    return String(b.created_at || '').localeCompare(String(a.created_at || ''));
  });

  const summaryRows = command.kind === 'today' ? selected : filtered;
  const pendingCount = summaryRows.filter((row) => ['pending', 'triage', 'in_work', 'applied'].includes(row.queue_status)).length;
  const candidateCount = summaryRows.filter((row) => row.queue_status === 'candidate' || row.trust_level === 'candidate').length;
  const highCount = summaryRows.filter((row) => row.priority === 'high').length;
  const roleSummary = formatRoleCounts(selected);
  const lines = [];

  lines.push(title + ': ' + selected.length);
  if (command.kind === 'today' || command.kind === 'stats') {
    lines.push('Открытых: ' + pendingCount + '. Кандидатов: ' + candidateCount + '. High: ' + highCount + '.');
  }
  if (roleSummary) {
    lines.push('По ролям: ' + roleSummary + '.');
  }

  if (!selected.length) {
    lines.push(command.filter ? 'По этому фильтру сейчас пусто.' : 'Сейчас пусто.');
    return lines.join('\n');
  }

  if (command.kind === 'stats') {
    return lines.join('\n');
  }

  for (const [index, row] of selected.slice(0, 7).entries()) {
    const parts = [
      row.priority || 'no-priority',
      row.owner || 'unassigned',
      row.review_type || row.target_layer || 'review',
    ];
    lines.push((index + 1) + '. ' + parts.join(' | ') + ' | ' + shortText(row.short_text || row.source_title));
  }

  if (selected.length > 7) {
    lines.push('Еще: ' + (selected.length - 7) + '.');
  }

  return lines.join('\n');
}

function money(value) {
  const raw = String(value ?? '').trim();
  if (!raw || raw === '-') return null;
  const n = Number(raw.replace(',', '.'));
  if (!Number.isFinite(n)) return null;
  return n.toLocaleString('ru-RU');
}

function hasPriceIntent(text) {
  return /цен|стоим|сколько|прайс|руб|byn|бел|w\$|pv|партнер|повторк|повторн\w*\s+покуп|рознич|первичк|регистрац|основн\w*\s+цен/i.test(text);
}

function wantsPartnerPrice(text) {
  return /партнер|повторк|повторн\w*\s+покуп/i.test(text);
}

function wantsRetailPrice(text) {
  return /рознич|первичк|регистрац|основн\w*\s+цен/i.test(text);
}

function buildGapReport(envelope) {
  if (envelope.trusted_reviewer !== true) {
    return 'Эта команда доступна только проверенным участникам команды.';
  }

  const gaps = rowsFromReviewSnapshot()
    .filter((row) => row.source_type === 'knowledge_gap' || row.review_type === 'knowledge_gap')
    .filter((row) => ['pending', 'triage', 'in_work', 'applied'].includes(row.queue_status));

  const grouped = new Map();
  for (const row of gaps) {
    const key = normalize(row.short_text || row.source_title || 'неизвестный вопрос');
    if (!key) continue;
    const current = grouped.get(key) || { text: row.short_text || row.source_title, count: 0, priority: row.priority || 'medium', owner: row.owner || 'content' };
    current.count += 1;
    grouped.set(key, current);
  }

  const rows = [...grouped.values()]
    .sort((left, right) => right.count - left.count || priorityRank(left.priority) - priorityRank(right.priority))
    .slice(0, 5);

  function displayText(value) {
    return String(value || '')
      .replace(/^Unknown product request:\s*/i, 'Не распознан запрос: ')
      .replace(/^Missing approved product card for\s*/i, 'Нет утвержденной карточки для: ')
      .replace(/^Недостаточно подтвержденной информации в базе\.?$/i, 'Недостаточно подтвержденной информации в базе.')
      .trim();
  }

  const ownerNames = { content: 'контент', admin: 'админ', business: 'бизнес', medical: 'врач', dev: 'разработка' };
  if (!rows.length) return '<b>WHIEDA: точки роста</b>\n\nСейчас нет открытых незакрытых вопросов. Очередь чистая.';

  const total = gaps.length;
  const high = gaps.filter((row) => row.priority === 'high').length;
  const lines = [
    '<b>WHIEDA: точки роста</b>',
    'За период в очереди: <b>' + total + '</b> открытых сигналов' + (high ? ', из них <b>' + high + '</b> срочных' : '') + '.',
    '',
    '<b>Что улучшить в первую очередь</b>',
  ];
  for (const [index, row] of rows.entries()) {
    lines.push((index + 1) + '. ' + escapeHtml(shortText(displayText(row.text), 130)) + '\n   Повторений: <b>' + row.count + '</b> | ответственный: <b>' + (ownerNames[row.owner] || escapeHtml(row.owner || 'контент')) + '</b>');
  }
  lines.push('');
  lines.push('Каждая строка остается в очереди до проверки и закрытия.');
  return lines.join('\n');
}

function hasCompareIntent(text) {
  return /сравн|разниц|отлич|чем\s+отлич/i.test(text);
}

function hasProMarker(value) {
  return /(?:^|\s)pro(?:\s|$)|(?:^|\s)про(?:\s|$)/i.test(String(value || ''));
}

function buildPriceAnswer(product, matchedAlias, options = {}) {
  const retailRub = money(product.retail_price_rub);
  const retailByn = money(product.retail_price_byn);
  const retailW = money(product.retail_w);
  const partnerRub = money(product.partner_price_rub);
  const partnerByn = money(product.partner_price_byn);
  const partnerW = money(product.partner_w);
  const partnerPoints = money(product.partner_points);
  const name = product.canonical_name || matchedAlias.canonical_name;

  const retailOnly = !!options.retailOnly && !options.partnerOnly;
  const partnerOnly = !!options.partnerOnly && !options.retailOnly;

  function fmt3(a, aLabel, b, bLabel, c, cLabel) {
    return (a ? a + ' ' + aLabel : 'нет данных') + ' / ' + (b ? b + ' ' + bLabel : 'нет данных') + ' / ' + (c ? c + ' ' + cLabel : 'нет данных');
  }

  const retailLine = (retailRub || retailByn || retailW)
    ? 'Розничная цена: ' + fmt3(retailRub, '₽', retailByn, 'BYN', retailW, 'W' + '$') + '.'
    : null;

  const partnerLine = (partnerRub || partnerByn || partnerW)
    ? 'Для партнера: ' + fmt3(partnerRub, '₽', partnerByn, 'BYN', partnerW, 'W' + '$') + '.'
    : null;

  const pointsLine = partnerPoints ? 'Партнерские баллы: ' + partnerPoints + ' PV.' : null;
  const parts = [];

  if (retailOnly) {
    if (retailLine) parts.push(retailLine);
  } else if (partnerOnly) {
    if (partnerLine) parts.push(partnerLine);
    if (pointsLine) parts.push(pointsLine);
  } else {
    if (retailLine) parts.push(retailLine);
    if (partnerLine) parts.push(partnerLine);
    if (pointsLine) parts.push(pointsLine);
  }

  if (!parts.length) {
    return 'По товару "' + name + '" цена пока не заполнена в таблице. Передам на проверку.';
  }

  return name + ': ' + parts.join(' ');
}

function buildCompactPriceLine(product) {
  const retailRub = money(product?.retail_price_rub);
  const retailByn = money(product?.retail_price_byn);
  const partnerRub = money(product?.partner_price_rub);
  const partnerByn = money(product?.partner_price_byn);
  const partnerPoints = money(product?.partner_points);
  const chunks = [];
  if (retailRub || retailByn) {
    chunks.push('розница ' + [retailRub ? retailRub + ' ₽' : null, retailByn ? retailByn + ' BYN' : null].filter(Boolean).join(' / '));
  }
  if (partnerRub || partnerByn) {
    chunks.push('партнер ' + [partnerRub ? partnerRub + ' ₽' : null, partnerByn ? partnerByn + ' BYN' : null].filter(Boolean).join(' / '));
  }
  if (partnerPoints) {
    chunks.push(partnerPoints + ' PV');
  }
  return chunks.join('; ');
}

function hasDescriptionIntent(text) {
  return /расскажи|что\s+это|что\s+такое|что\s+за|подробнее|для\s+чего|для\s+кого|как\s+использ|как\s+примен|что\s+делает/i.test(text);
}

function isBareProductPrompt(text, bestAlias = '') {
  const normalized = normalize(text);
  const normalizedCompact = normalizeCompact(text);
  const alias = normalize(bestAlias);
  const aliasCompact = normalizeCompact(bestAlias);
  if (!normalized || !alias) return false;
  if (normalized === alias || normalizedCompact === aliasCompact) return true;
  const tokens = normalized.split(' ').filter(Boolean);
  if (tokens.length > 5) return false;
  return fuzzySingleWordMatch(normalized, alias);
}

function isAmbiguousShortAlias(text, bestAlias = '') {
  const normalized = normalize(text);
  const alias = normalize(bestAlias);
  if (!normalized || !alias) return false;
  const genericAliases = new Set(['активатор', 'пептид', 'пептиды', 'соевые пептиды']);
  return normalized === alias && genericAliases.has(alias);
}

function weakColorOrBeltClarification(text) {
  const value = normalize(text);
  if (!value) return null;

  // A colour together with "пояс" is never allowed to resolve to an elixir.
  if (/\bпояс(?:а|у|ом|е|ы)?\b/.test(value)) {
    // The approved plural alias is concrete enough for a direct product action:
    // "дай фото пояса" should send the Magnetic Belt photo, not loop on a question.
    if (/\bпояса\b/.test(value)) return null;
    return {
      alias: 'пояс', sku: 'T003', canonical_name: 'Магнитный пояс',
      direct: /\b(?:магнитн|турмалин)\w*\s+пояс/.test(value),
      answer: 'Вы про Магнитный пояс? Нужна цена, описание или применение?',
    };
  }

  const choices = [
    ['красн', 'F001-02', 'Эликсир Фохоу', 'красный эликсир Фохоу'],
    ['зелен', 'F003-02', 'Эликсир Саньцин', 'зелёный эликсир Саньцин'],
    ['син', 'F002-02', 'Эликсир 3 Драгоценности', 'синий эликсир «3 Драгоценности»'],
  ];
  for (const [stem, sku, canonicalName, label] of choices) {
    if (!new RegExp('\\b' + stem + '[а-я]*\\b').test(value)) continue;
    // Full colour aliases are confirmed product names and should answer directly.
    const direct = new RegExp('\\b' + stem + '[а-я]*\\s+(?:эликсир|банка|коробка|этикетка)').test(value);
    return {
      alias: stem, sku, canonical_name: canonicalName,
      direct,
      answer: `Вы про ${label}? Нужна цена, описание или применение?`,
    };
  }
  return null;
}

function isExplicitProductAsk(text) {
  return /^(расскажи|подскажи|что\s+это|что\s+такое|что\s+за|подробнее|дай\s+инфо|дай\s+информац|что\s+делает)\b/i.test(String(text || '').trim());
}

function isProductContextFollowup(text) {
  const value = normalize(text);
  return /^(расскажи подробнее|подробнее|подробней|как используют|как использовать|как применять|как работает|почему это интересно|для каких ситуаций|опыт людей|исследования|материалы и видео)$/.test(value);
}

function isDeepKnowledgeIntent(text) {
  return /глубок[а-яё]*\s+разбор|разбер[а-яё]*\s+подробн|терагерц|механизм|исследован|научн|доказател|источник|материал[а-яё]*\s+whieda|внутренн[а-яё]*\s+баз/i.test(String(text || ''));
}

function isDetailFollowupWithoutContext(text) {
  return /^(расскажи\s+подробнее|подробнее|подробн(ее|ей)|еще\s+подробнее)$/i.test(String(text || '').trim());
}

function detailTopicFromText(text) {
  const value = normalize(text);
  if (/как\s+(использ|примен)|применен|инструкц|режим/.test(value)) return 'usage';
  if (/когда\s+(смотр|берут)|ситуац|для\s+каких/.test(value)) return 'situations';
  if (/опыт|отзыв|люди\s+говорят|реальн/.test(value)) return 'experience';
  if (/как\s+(работает|устроен)|механизм|почему\s+работает/.test(value)) return 'mechanism';
  if (/исследован|научн|доказател/.test(value)) return 'research';
  if (/материал|видео|обучен|инструкц|ссылк/.test(value)) return 'materials';
  return null;
}

function isDetailsIntent(text) {
  return isDetailFollowupWithoutContext(text) || detailTopicFromText(text) !== null;
}

function buildDetailsMenu(name) {
  return [
    '<b>' + escapeHtml(name) + '</b> — что именно разобрать?',
    '• как использовать',
    '• для каких ситуаций смотрят',
    '• опыт людей',
    '• как работает',
    '• исследования',
    '• материалы и видео',
    '',
    'Напишите нужную тему.',
  ].join('\n');
}

function buildDetailFallback(card, topic, name) {
  const byTopic = {
    usage: {
      title: '🧭 <b>Как используют:</b>',
      text: cleanCardText(card?.how_to_use_short),
    },
    situations: {
      title: '✅ <b>Когда обычно смотрят:</b>',
      text: listItems(card?.common_use_cases, 8).map((item) => '• ' + escapeHtml(item)).join('\n'),
    },
  };
  const item = byTopic[topic];
  if (!item?.text) return null;
  return '<b>' + escapeHtml(name) + '</b>\n\n' + item.title + '\n' + escapeHtml(item.text);
}

function buildApprovedDetailAnswer(detail, name) {
  const title = String(detail?.title || '').trim();
  const text = String(detail?.answer_text || '').trim();
  const sourceUrl = String(detail?.source_url || '').trim();
  const sourceLocator = String(detail?.source_locator || '').trim();
  const parts = [
    '<b>' + escapeHtml(name) + '</b>',
    title ? '<b>' + escapeHtml(title) + '</b>' : null,
    escapeHtml(text),
  ].filter(Boolean);
  if (sourceUrl) {
    parts.push('Источник: ' + escapeHtml(sourceLocator || 'материал WHIEDA') + '\n' + escapeHtml(sourceUrl));
  }
  return parts.join('\n\n');
}

function structuredReply(envelope, matchedAlias, answerText, answerMode, extra = {}) {
  const match = {
    alias: matchedAlias?.alias || matchedAlias?.canonical_name || null,
    sku: matchedAlias?.canonical_sku || null,
    canonical_name: matchedAlias?.canonical_name || null,
  };
  return [{
    json: {
      ...envelope,
      structured_hit: true,
      structured_source: 'postgres_cache',
      structured_match: match,
      dify_raw: '',
      dify_response: {
        answer_text: answerText,
        action: 'reply',
        answer_mode: answerMode,
        confidence: 1,
        knowledge_gap: false,
        gap_reason: null,
        state_updates: [],
        knowledge_candidates: [],
        task_candidates: [],
        followup_questions: [],
        needs_human_review: false,
      },
      answer_text: answerText,
      reply_text: answerText,
      action: 'reply',
      answer_mode: answerMode,
      confidence: 1,
      knowledge_gap: false,
      needs_human_review: false,
      route: 'answer',
      ...extra,
    },
  }];
}

const subscriptionCommand = String(userText || '').trim().match(/^\/(subscribe|unsubscribe)(?:@\w+)?(?:\s|$)/i);
if (subscriptionCommand) {
  if (String(envelope.chat_type || '') !== 'private') {
    return structuredReply(
      envelope,
      null,
      'Подписка работает только в личном чате с ботом. Откройте его и отправьте эту команду там.',
      'direct_subscription_private_only',
    );
  }
  const isSubscribe = String(subscriptionCommand[1]).toLowerCase() === 'subscribe';
  return structuredReply(
    envelope,
    null,
    isSubscribe
      ? 'Готово. Вы подписаны на сообщения WHIEDA. Отписаться можно командой /unsubscribe.'
      : 'Готово. Подписка отключена. Вернуться можно командой /subscribe.',
    isSubscribe ? 'direct_subscription_on' : 'direct_subscription_off',
  );
}

const startCommand = String(userText || '').trim().match(/^\/start(?:@\w+)?(?:\s+([a-z0-9_-]{1,64}))?$/i);

// Do not use \b here: it does not create word boundaries around Cyrillic letters.
const medicalTreatmentRequest = /(как\s+лечить|вылечить|лечит\s+диагноз|лечение\s+диагноз|постав(?:ь|ить)\s+диагноз)/i.test(userText);
const medicalProcedureRequest = /(?:после|перед|при)\s+(?:операц|замен[аы]\s+хрустал|лазерн(?:ой|ая)\s+коррекц|химио(?:терапи)?|имплант)|(?:операц|замен[аы]\s+хрустал|лазерн(?:ой|ая)\s+коррекц|химио(?:терапи)?).{0,48}(?:можно|когда|срок|носить|использовать)/i.test(userText);
if (medicalTreatmentRequest || medicalProcedureRequest) {
  return structuredReply(
    envelope,
    null,
    medicalProcedureRequest
      ? 'После операции или другого медицинского вмешательства я не могу по чату определять срок и разрешать использование товара. Это нужно сверить с лечащим врачом. По WHIEDA могу дать утверждённую информацию о товаре, его инструкции, ограничениях, цене, фото и материалах.'
      : 'Я не могу подсказывать лечение диагноза или заменять назначение врача. По товарам WHIEDA могу дать только утверждённую информацию: как используют по инструкции, ограничения, цену, фото и материалы.',
    'direct_safety_limit',
    { safety_limited: true },
  );
}

function activeSubscriberCount(audience) {
  const row = optionalNodeItems('Postgres: Broadcast Snapshot')?.[0]?.json ?? {};
  const column = String(audience || 'partners').toLowerCase() + '_subscriber_count';
  const value = Number(row[column] ?? 0);
  return Number.isFinite(value) ? value : 0;
}

function currentActorAccess() {
  const row = optionalNodeItems('Postgres: Lookup User Access')?.[0]?.json ?? {};
  const snapshot = optionalNodeItems('Postgres: Broadcast Snapshot')?.[0]?.json ?? {};
  const structureCode = String(row.structure_code || 'general').trim() || 'general';
  return {
    role: String(row.role || '').toLowerCase(),
    structureCode,
    // Super-admin scope is computed in SQL from Structure_Owners. A leader
    // gets only their own code, so delivery can never cross structures.
    structureCodes: String(snapshot.structure_codes || structureCode).trim() || structureCode,
  };
}

function canManageBroadcasts(access) {
  return ['leader', 'admin', 'super_admin'].includes(access.role);
}

function broadcastReply(answerText, mode, action = '', draftId = '', textBody = '', audience = '', extra = {}) {
  const access = currentActorAccess();
  return structuredReply(envelope, null, answerText, mode, {
    broadcast_action: action || null,
    broadcast_draft_id: draftId || null,
    broadcast_text: textBody || null,
    broadcast_audience: audience || null,
    broadcast_structure_code: access.structureCodes,
    ...extra,
  });
}

const broadcastCommand = String(userText || '').trim().match(/^\/(broadcast|broadcast_send|broadcast_cancel)(?:@\w+)?(?:\s+([\s\S]+))?$/i);
if (broadcastCommand) {
  const action = String(broadcastCommand[1] || '').toLowerCase();
  const argument = String(broadcastCommand[2] || '').trim();
  const access = currentActorAccess();
  if (String(envelope.chat_type || '') !== 'private') {
    return broadcastReply('Управление рассылкой работает только в личном чате.', 'direct_broadcast_private_only');
  }
  if (!canManageBroadcasts(access)) {
    return broadcastReply('Эта команда доступна только администратору рассылки.', 'direct_broadcast_denied');
  }
  if (action === 'broadcast') {
    if (!argument) {
      return broadcastReply('Напишите так: /broadcast кандидаты ваш текст\nАудитории: кандидаты, партнёры, лидеры.', 'direct_broadcast_help');
    }
    const audienceMatch = argument.match(/^(кандидаты|кандидатам|candidates|партнёры|партнерам|partners|лидеры|лидерам|leaders)\s+([\s\S]+)$/i);
    if (!audienceMatch) {
      return broadcastReply('Сначала укажите аудиторию: кандидаты, партнёры или лидеры. Пример: /broadcast партнёры ваш текст', 'direct_broadcast_help');
    }
    const audienceMap = {
      'кандидаты': 'candidates', 'кандидатам': 'candidates', candidates: 'candidates',
      'партнёры': 'partners', 'партнерам': 'partners', partners: 'partners',
      'лидеры': 'leaders', 'лидерам': 'leaders', leaders: 'leaders',
    };
    const audience = audienceMap[String(audienceMatch[1]).toLowerCase()];
    const audienceLabel = { candidates: 'Кандидаты', partners: 'Партнёры', leaders: 'Лидеры' }[audience];
    const textBody = String(audienceMatch[2] || '').trim();
    if (!textBody) return broadcastReply('После аудитории добавьте текст рассылки.', 'direct_broadcast_help');
    const draftId = 'broadcast-' + String(envelope.idempotency_key || Date.now()).replace(/[^a-zA-Z0-9_-]/g, '-');
    const count = activeSubscriberCount(audience);
    const preview = [
      '<b>Черновик рассылки</b>',
      '',
      textBody,
      '',
      'Аудитория: <b>' + audienceLabel + '</b>.',
      'Получателей: <b>' + count + '</b>.',
      'Чтобы поставить в очередь: <code>/broadcast_send ' + draftId + '</code>',
      'Чтобы отменить: <code>/broadcast_cancel ' + draftId + '</code>',
    ].join('\n');
    return broadcastReply(preview, 'direct_broadcast_preview', 'create', draftId, textBody, audience);
  }
  if (!argument) {
    return broadcastReply('Укажите id черновика из предпросмотра.', 'direct_broadcast_help');
  }
  if (action === 'broadcast_send') {
    return broadcastReply('Черновик <code>' + escapeHtml(argument) + '</code> подтверждён и поставлен в очередь на отправку.', 'direct_broadcast_confirmed', 'confirm', argument);
  }
  return broadcastReply('Черновик <code>' + escapeHtml(argument) + '</code> отменён. Ничего не отправлено.', 'direct_broadcast_cancelled', 'cancel', argument);
}

const meetingCommand = String(userText || '').trim().match(/^\/(meeting|meeting_send|meeting_cancel|meeting_reschedule)(?:@\w+)?(?:\s+([\s\S]+))?$/i);
if (meetingCommand) {
  const action = String(meetingCommand[1] || '').toLowerCase();
  const argument = String(meetingCommand[2] || '').trim();
  const access = currentActorAccess();
  if (String(envelope.chat_type || '') !== 'private') return broadcastReply('Управление встречами работает только в личном чате.', 'direct_meeting_private_only');
  if (!canManageBroadcasts(access)) return broadcastReply('Эта команда доступна только администратору рассылки.', 'direct_meeting_denied');
  if (action === 'meeting_reschedule') {
    const move = argument.match(/^([a-z0-9_-]{1,128})\s+(\d{4}-\d{2}-\d{2})\s+(\d{2}:\d{2})$/i);
    if (!move) return broadcastReply('Напишите так: <code>/meeting_reschedule id-встречи 2026-08-06 18:00</code>\nВремя указывается по Москве. Напоминания пересчитаются автоматически.', 'direct_meeting_help');
    const meetingAt = move[2] + ' ' + move[3] + ':00 Europe/Moscow';
    return broadcastReply(
      'Встреча <code>' + escapeHtml(move[1]) + '</code> перенесена на <b>' + escapeHtml(move[2] + ' ' + move[3]) + ' (Москва)</b>. Напоминания пересчитаны.',
      'direct_meeting_rescheduled',
      'reschedule_meeting',
      move[1],
      '',
      '',
      { meeting_at: meetingAt },
    );
  }
  if (action === 'meeting') {
    const match = argument.match(/^(кандидаты|кандидатам|candidates|партнёры|партнерам|partners|лидеры|лидерам|leaders)\s+(\d{4}-\d{2}-\d{2})\s+(\d{2}:\d{2})\s*\|\s*([\s\S]+)$/i);
    if (!match) return broadcastReply('Напишите так: <code>/meeting партнёры 2026-07-16 19:00 | Встреча команды в Zoom</code>\nВремя указывается по Москве. Бот подготовит напоминания за 24 часа и за 1 час.', 'direct_meeting_help');
    const audienceMap = {
      'кандидаты': 'candidates', 'кандидатам': 'candidates', candidates: 'candidates',
      'партнёры': 'partners', 'партнерам': 'partners', partners: 'partners',
      'лидеры': 'leaders', 'лидерам': 'leaders', leaders: 'leaders',
    };
    const audience = audienceMap[String(match[1]).toLowerCase()];
    const meetingAt = match[2] + ' ' + match[3] + ':00 Europe/Moscow';
    const meetingText = String(match[4] || '').trim();
    const eventId = 'meeting-' + String(envelope.idempotency_key || Date.now()).replace(/[^a-zA-Z0-9_-]/g, '-');
    const audienceLabel = { candidates: 'Кандидаты', partners: 'Партнёры', leaders: 'Лидеры' }[audience];
    const count = activeSubscriberCount(audience);
    const preview = [
      '<b>Черновик встречи</b>', '', meetingText, '',
      'Время: <b>' + escapeHtml(match[2] + ' ' + match[3]) + ' (Москва)</b>.',
      'Аудитория: <b>' + audienceLabel + '</b>. Получателей: <b>' + count + '</b>.',
      'Напоминания: за 24 часа и за 1 час.',
      'Подтвердить: <code>/meeting_send ' + eventId + '</code>',
      'Отменить: <code>/meeting_cancel ' + eventId + '</code>',
    ].join('\n');
    return broadcastReply(preview, 'direct_meeting_preview', 'create_meeting', eventId, meetingText, audience, { meeting_at: meetingAt });
  }
  if (!argument) return broadcastReply('Укажите id черновика встречи.', 'direct_meeting_help');
  if (action === 'meeting_send') return broadcastReply('Встреча <code>' + escapeHtml(argument) + '</code> подтверждена. Напоминания поставлены в очередь.', 'direct_meeting_confirmed', 'confirm_meeting', argument);
  return broadcastReply('Встреча <code>' + escapeHtml(argument) + '</code> отменена. Напоминания не будут отправлены.', 'direct_meeting_cancelled', 'cancel_meeting', argument);
}

function isActivatorLikePrompt(text) {
  const normalized = normalize(text);
  if (!normalized) return false;
  return /\bактиватор\b/.test(normalized) || /\bативатор\b/.test(normalized);
}

function isSoyPeptideLikePrompt(text) {
  const normalized = normalize(text);
  if (!normalized) return false;
  return /(^|\s)пептид(ы)?($|\s)/.test(normalized)
    || normalized === 'соевые пептиды'
    || normalized === 'соевый пептид';
}

function normalizeMultiline(value) {
  return String(value || '')
    .replace(/\r/g, '')
    .split('\n')
    .map((line) => line.trim())
    .filter(Boolean)
    .join(' ');
}

function stripLeadEmoji(value) {
  return String(value || '').replace(/^[^\p{L}\p{N}]+/u, '').trim();
}

function toSentence(value) {
  const text = normalizeMultiline(value).replace(/\s+/g, ' ').trim();
  if (!text) return '';
  return text;
}

function firstListChunk(value, limit = 2) {
  const parts = String(value || '')
    .split(/[;\n]+/)
    .map((item) => item.trim())
    .filter(Boolean);
  return parts.slice(0, limit).join(', ');
}

function listItems(value, limit = null) {
  const items = String(value || '')
    .split(/[;\n]+/)
    .map((item) => stripBoilerplate(stripLeadEmoji(stripJunkArtifacts(item.trim()))))
    .map((item) => item.replace(/\.+$/, '').trim())
    .filter((item) => !/вопрос\s+как\s+лечить\s+диагноз/i.test(item))
    .filter((item) => !/подробные\s+режимы\s+давать\s+только/i.test(item))
    .filter(Boolean);
  if (limit == null) return items;
  return items.slice(0, limit);
}

function getProductResources(sku, resourceType = '') {
  const rows = rowsFromNode('Postgres: Runtime Snapshot', 'resources')
    .filter((row) => String(row.project_id ?? row.client_id ?? '') === PROJECT_ID)
    .filter((row) => String(row.sku ?? '') === String(sku ?? ''))
    .filter((row) => isActive(row.active));

  if (!resourceType) return rows;
  const type = normalize(resourceType);
  return rows.filter((row) => normalize(row.resource_type) === type);
}

function isPhotoResource(row) {
  const type = normalize(row?.resource_type);
  return type === 'image' || type === 'photo' || type === 'picture' || type === 'img';
}

function extractDriveFileId(url) {
  const value = String(url || '');
  const marker = '/d/';
  const markerIndex = value.indexOf(marker);
  if (markerIndex >= 0) {
    const tail = value.slice(markerIndex + marker.length);
    const fileId = tail.split(/[/?&#]/)[0];
    if (fileId) return fileId;
  }
  const idMatch = value.match(/[?&]id=([a-zA-Z0-9_-]+)/);
  if (idMatch) return idMatch[1];
  return null;
}

function toTelegramPhotoUrl(url) {
  const raw = String(url || '').trim();
  const fileId = extractDriveFileId(raw);
  if (fileId) return 'https://drive.google.com/uc?export=download&id=' + fileId;
  return raw;
}

function isGoogleDriveUrl(url) {
  return /drive\.google\.com/i.test(String(url || ''));
}

function fallbackPhotoBySku(sku, canonicalName = '') {
  return null;
}

function buildPhotoCaption(value) {
  const raw = String(value || 'Фото').trim();
  return raw.length > 1024 ? raw.slice(0, 1021) + '...' : raw;
}

function pickCardPhoto(card, matchedAlias, resources = []) {
  const canonicalName = card?.short_name || card?.canonical_name || matchedAlias?.canonical_name || 'Фото';
  const fallbackPhoto = fallbackPhotoBySku(card?.sku || matchedAlias?.canonical_sku, canonicalName);
  if (fallbackPhoto) return fallbackPhoto;

  const primaryImageUrl = String(card?.primary_image_url || '').trim();
  if (primaryImageUrl && !isGoogleDriveUrl(primaryImageUrl)) {
    return {
      photo_url: toTelegramPhotoUrl(primaryImageUrl),
      caption: null,
      resource_id: null,
      title: canonicalName,
      canonical_name: canonicalName,
      sku: card?.sku || matchedAlias?.canonical_sku || null,
    };
  }

  const photoResource = resources.find((row) => {
    if (!isPhotoResource(row)) return false;
    const url = String(row.url || '').trim();
    return !!url && !isGoogleDriveUrl(url);
  }) || resources.find((row) => isPhotoResource(row) && String(row.url || '').trim());
  if (photoResource) {
    return {
      photo_url: toTelegramPhotoUrl(photoResource.url),
      caption: null,
      resource_id: photoResource.resource_id || null,
      title: photoResource.title || canonicalName,
      canonical_name: photoResource.canonical_name || canonicalName,
      sku: photoResource.sku || card?.sku || matchedAlias?.canonical_sku || null,
    };
  }

  if (primaryImageUrl) {
    return {
      photo_url: toTelegramPhotoUrl(primaryImageUrl),
      caption: null,
      resource_id: null,
      title: canonicalName,
      canonical_name: canonicalName,
      sku: card?.sku || matchedAlias?.canonical_sku || null,
    };
  }

  return null;
}

function distinctByUrl(rows, limit = 10) {
  const seen = new Set();
  const result = [];
  for (const row of rows) {
    const url = String(row?.url || '').trim();
    if (!url || seen.has(url)) continue;
    seen.add(url);
    result.push(row);
    if (result.length >= limit) break;
  }
  return result;
}

function buildEscalateLine(value) {
  const text = shortenSoft(stripLeadEmoji(stripBoilerplate(toSentence(value))), 220);
  if (!text) return '';
  const duplicateSignals = /беремен|онколог|кардиостим|стент|температур|герпес|открытые глаза/i;
  if (duplicateSignals.test(text) && !/диагноз|леч|травм|отек/i.test(text)) return '';
  return text;
}

function shortenSoft(value, max = 180) {
  const text = toSentence(value);
  if (!text) return '';
  return text.length > max ? text.slice(0, max - 1).trim() + '…' : text;
}

function stripBoilerplate(value) {
  return String(value || '')
    .replace(/^если\s+просто:\s*/i, '')
    .replace(/^для\s+кого:\s*/i, '')
    .replace(/^кому\s+обычно\s+откликается:\s*/i, '')
    .replace(/^когда\s+обычно\s+смотрят:\s*/i, '')
    .replace(/^когда\s+обычно\s+спрашивают:\s*/i, '')
    .replace(/^чаще\s+всего\s+спрашивают\s+про:\s*/i, '')
    .replace(/^как\s+используют:\s*/i, '')
    .replace(/^как\s+использовать:\s*/i, '')
    .replace(/^почему\s+это\s+интересно:\s*/i, '')
    .replace(/^отличие\s+от[^:]*:\s*/i, '')
    .replace(/^важно:\s*/i, '')
    .replace(/^ограничения:\s*/i, '')
    .replace(/для простого ответа:[^.]+\.?/i, '')
    .replace(/подробные режимы давать только из утвержденной инструкции\.?/i, '')
    .replace(/\s+/g, ' ')
    .trim();
}

function stripJunkArtifacts(value) {
  return String(value || '')
    .replace(/\[\d+[^\]]*]/g, '')
    .replace(/\(\s*примечани[^\)]*\)/gi, '')
    .replace(/\s{2,}/g, ' ')
    .trim();
}

function cleanCardText(value) {
  return stripJunkArtifacts(stripBoilerplate(stripLeadEmoji(value)))
    .replace(/^(если\s+просто:\s*)+/i, '')
    .replace(/^(для\s+кого:\s*)+/i, '')
    .replace(/^(как\s+используют:\s*)+/i, '')
    .replace(/^(почему\s+это\s+интересно:\s*)+/i, '')
    .trim();
}

function escapeHtml(value) {
  return String(value || '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;');
}

function htmlTitle(label) {
  return '<b>' + escapeHtml(label) + '</b>';
}

function htmlBulletList(items) {
  return items.map((item) => '• ' + escapeHtml(item)).join('\n');
}

function cleanContraindications(items) {
  return items
    .map((item) => stripBoilerplate(stripLeadEmoji(stripJunkArtifacts(item))))
    .map((item) => item.replace(/\.+$/, '').trim())
    .filter((item) => !/вопрос\s+как\s+лечить\s+диагноз/i.test(item))
    .filter((item) => !/если\s+вопрос\s+про\s+диагноз/i.test(item))
    .filter(Boolean);
}

const DEMO_CARD_OVERRIDES_BY_SKU = {
  'M015-00': {
    what_it_is: 'Активатор клеток — это ваш универсальный домашний спасатель для локальной работы с зонами дискомфорта: зажатая шея, ноющая спина, колени, мышцы, связки и точки хронического напряжения. Его берут как самый понятный первый прибор по принципу: достал из коробки, включил и мягко поработал с местом, которое беспокоит.',
    who_asks_about_it: 'Для людей, которые устали жить на мазях, таблетках и временных пластырях. Для тех, кто хочет иметь под рукой понятный инструмент быстрой помощи телу дома, на даче или после нагрузки без долгой подготовки.',
    common_use_cases: 'заклинило шею после рабочего дня за компьютером; спина стала деревянной после сидения в машине или работы на грядках; ноет колено на погоду или после физической нагрузки; потянули связку или мышцу на тренировке; синяки, отеки и мелкие повреждения кожи проходят слишком медленно',
    how_to_use_short: 'Плавно обдувайте прибором проблемную зону теплым воздухом по инструкции, сохраняя комфортное расстояние до кожи. Используется локально, избегая прямого воздействия на открытые глаза и зоны с острыми повреждениями кожи.',
    what_to_expect_soft: 'В отличие от разогревающих мазей, которые дают только короткий внешний эффект, прибор воспринимается как полноценный домашний инструмент регулярной локальной процедуры. Базовая версия лаконична, надежна и полностью закрывает задачу первого универсального прибора.',
    contraindications_short: 'беременность; онкологические заболевания; наличие кардиостимулятора или стентов в зоне воздействия; высокая температура тела; активный герпес с лопнувшими пузырьками; открытые раны; тяжелые острые состояния',
  },
  'EU-N000031-25': {
    what_it_is: 'Активатор клеток PRO — это старшая версия клеточного активатора в стильном защитном кейсе. Это выбор для тех, кто хочет максимум удобства для семьи, поездок, выездных презентаций или регулярной работы с клиентами.',
    who_asks_about_it: 'Для партнеров, которые строят бизнес и хотят производить сильное впечатление на презентациях. Для продвинутых пользователей, ценящих премиальный дизайн, точечные настройки и максимальную защиту устройства.',
    common_use_cases: 'нужен солидный подарок для близких; часто путешествуете и нужен прочный кейс для перевозки; хочется раздельно регулировать силу обдува и температуру прогрева; нужен прибор, который визуально выглядит дорого и презентабельно',
    how_to_use_short: 'Используется как базовый активатор, но с расширенными возможностями: можно точнее настроить режим под человека, заблокировать кнопки от случайного нажатия и применять сменные насадки для более точечной работы. Насадки требуют аккуратного обращения.',
    what_to_expect_soft: 'PRO не про магию, а про уровень комфорта и удобства. Защитная фильтр-сетка, автопродувка, кейс, более гибкие настройки и премиальный внешний вид делают его сильнее как семейный и как презентационный прибор.',
    contraindications_short: 'беременность; онкология; кардиостимулятор; стенты; высокая температура; открытые повреждения кожи; герпес в активной фазе; воздействие непосредственно на глаза',
  },
  'EU-N000024-24': {
    what_it_is: 'Вэнтун — это ваша домашняя станция глубокого теплового восстановления через стопы и поясницу. Его чаще всего выбирают на вечер, когда ноги гудят, тело знобит, спину ломит от сидячей работы, а энергии уже не осталось.',
    who_asks_about_it: 'Для людей с сидячей или стоячей работой: офис, торговля, бьюти-сфера, дорога. Для тех, кто постоянно мерзнет, страдает от отеков ног и мечтает о полноценном глубоком прогреве тела без выхода из дома.',
    common_use_cases: 'ноги гудят и горят после тяжелого дня на ногах; мерзнут конечности и постоянно холодные руки или ноги; крутит суставы на погоду; хочется глубоко расслабиться и быстрее уснуть',
    how_to_use_short: 'Вы садитесь в кресло или на стул, ставите босые стопы на платформу, а тепловой пояс фиксируете на пояснице. Включаете прибор по инструкции и отдыхаете 20-30 минут в спокойном пассивном режиме.',
    what_to_expect_soft: 'Вэнтун воспринимается как глубокий вечерний ритуал восстановления. Он дает ощущение, будто тело заново отогрелось и разгрузилось: уходят тяжесть, застой и внутреннее напряжение после тяжелого дня.',
    contraindications_short: 'кардиостимулятор; беременность; высокая температура тела; острые воспалительные процессы; металлические импланты в ногах или позвоночнике',
  },
  'EU-N000021-24': {
    what_it_is: 'Magic FoHerb 3.0 — это домашний SPA-комбайн и рабочий аппаратный комплекс для мастера. Его ценят за глубокую проработку зажатых мышц, лимфодренажное воздействие и возможность получать мощный результат без физического износа рук.',
    who_asks_about_it: 'Для массажистов, мануальных и бьюти-мастеров, которые хотят добавить дорогую аппаратную услугу в работу. И для семей, которые хотят иметь дома серьезный физиотерапевтический прибор премиум-класса.',
    common_use_cases: 'спина зажата колом, шея не поворачивается; глубокие застойные отеки тела и конечностей; усталость рук мастера при плотном потоке клиентов; нужно быстро и глубоко расслабить мышцы после перегрузки',
    how_to_use_short: 'Перед сеансом прибор полностью заряжают. Работа идет в специальных токопроводящих перчатках поверх сухих резиновых. Контакт с телом начинают плавно. Основные зоны — спина, руки и ноги; лицо и шея только на минимальной мощности после обучения.',
    what_to_expect_soft: 'Главная фишка — глубина проработки без ощущения грубой силовой работы. Клиент чувствует плотное, глубокое воздействие, а мастер экономит руки и может работать дольше и стабильнее.',
    contraindications_short: 'беременность; кардиостимулятор; инсулиновая помпа; металлические импланты в зоне воздействия; эпилепсия; онкология; острые состояния',
  },
  'M014-00': {
    what_it_is: 'Минисауна Ба-Гуа — это ваш личный SPA-салон дома для мягкого глубокого прогрева и полной перезагрузки после тяжелого дня. Ее выбирают те, кто хочет хорошо пропотеть и снять напряжение, но не любит душные общественные бани.',
    who_asks_about_it: 'Для женщин и мужчин, которые заботятся о коже, фигуре и восстановлении. Для жителей города, живущих в стрессе и дефиците времени, которым нужен личный ритуал расслабления без поездок в SPA.',
    common_use_cases: 'хочется глубоко прогреться после холода; ощущение тяжести, забитости мышц и застоя жидкости в теле; тяжело переносится обычная баня; нужен личный ритуал расслабления и тишины',
    how_to_use_short: 'Кабина собирается за пару минут. Внутрь ставят табурет, заходят, застегивают молнию и садятся, оставляя голову снаружи. До процедуры лучше выпить теплой воды. Рекомендуемое время сеанса — 30-40 минут.',
    what_to_expect_soft: 'Ба-Гуа любят за ощущение мягкой, глубокой бани без удушливого пара. Тело хорошо прогревается, а голова остается снаружи, поэтому процедура переносится заметно комфортнее, чем обычная парилка.',
    contraindications_short: 'беременность; кардиостимулятор; онкология; тяжелая сердечная недостаточность; острые воспалительные процессы; высокая температура тела; выраженные проблемы с давлением',
  },
  'D014': {
    what_it_is: 'Компьютерные очки WHIEDA — это ваш ежедневный барьер защиты глаз от света мониторов, смартфонов и офисных ламп. Простой аксессуар, который помогает не доводить день до сухости, рези и тяжелой головы к вечеру.',
    who_asks_about_it: 'Для программистов, офисных сотрудников, студентов, геймеров, активных пользователей соцсетей и водителей, которые часто ездят вечером и ночью.',
    common_use_cases: 'за экраном больше 4-6 часов в день; к вечеру белки глаз краснеют и сохнут; ощущение песка в глазах после работы; тяжесть и тупая боль в области лба; встречные фары ночью сильно слепят',
    how_to_use_short: 'Просто надевайте очки при работе с цифровыми экранами. Они не требуют сложного ухода и не искажают привычную цветопередачу в повседневной работе.',
    what_to_expect_soft: 'Их ценят не за желтые стекла, а за ощущение более спокойных глаз к вечеру. Рабочий ритм остается тем же, но глаза меньше устают, а голова ощущается легче.',
    contraindications_short: 'не являются лечебным средством при тяжелых офтальмологических заболеваниях; при резком падении зрения; сильной глазной боли; травмах; гнойных процессах нужна отдельная проверка',
  },
  'D013': {
    what_it_is: 'Анионовые стельки — это ваш незаметный личный помощник внутри обуви для поддержки стоп, более мягкой ходьбы и меньшей усталости к вечеру. Вы просто живете обычный день, а стопы получают поддержку при каждом шаге.',
    who_asks_about_it: 'Для продавцов, курьеров, парикмахеров, спортсменов, любителей пеших прогулок и всех, у кого к вечеру горят стопы и ноет поясница от ходьбы.',
    common_use_cases: 'ноги гудят и отекают к вечеру; тяжесть и тупая боль в пояснице после рабочего дня; обычная обувь быстро стаптывается; хочется снизить ударную нагрузку на колени и позвоночник',
    how_to_use_short: 'Вложите стельки в повседневную, спортивную или рабочую обувь, при необходимости подрежьте под размер по контурным линиям на обороте. Подходят для ежедневного использования.',
    what_to_expect_soft: 'В отличие от мягких стелек масс-маркета, здесь ценят сочетание формы, ощущения опоры и ежедневного комфорта в ногах. Это не волшебство, а очень практичная вещь, которую замечаешь именно к концу длинного дня.',
    contraindications_short: 'открытые раны на стопах; мокнущие язвы; выраженные аллергические реакции кожи; тяжелая диабетическая стопа',
  },
  'T015': {
    what_it_is: 'Энергетический палантин — это ваш мягкий и стильный бронежилет от сквозняков, кондиционеров и мышечного напряжения в шее и плечах. Не мазь и не пластырь, а элегантный аксессуар, который можно носить в офисе, машине, самолете или дома.',
    who_asks_about_it: 'Для офисных сотрудников под кондиционерами, путешественников, людей с сидячей работой и всех, у кого к вечеру шея становится каменной и не хочет поворачиваться.',
    common_use_cases: 'шею и плечи регулярно продувает в машине или офисе; воротниковая зона зажата и гудит после компьютера; хочется мягкого тепла в поездке или перелете; нужен стильный аксессуар с реальной пользой для тела',
    how_to_use_short: 'Носите его как шарф, накидку или палантин, укутывая зону шеи, плеч или поясницы по мере необходимости в течение дня или во время сна.',
    what_to_expect_soft: 'Его любят за сочетание пользы и внешнего вида: можно выглядеть нормально и при этом получать деликатное тепло и ощущение расслабления в зоне шеи и плеч без жжения, запахов и следов на коже.',
    contraindications_short: 'высокая температура тела; острые травмы позвоночника; сильная непрекращающаяся боль требует отдельной проверки',
  },
};

function buildProductCardAnswer(card, product, matchedAlias, resources = []) {
  const effectiveCard = {
    ...card,
    ...(DEMO_CARD_OVERRIDES_BY_SKU[String(card?.sku || matchedAlias?.canonical_sku || '')] || {}),
  };
  const name = effectiveCard.short_name || effectiveCard.canonical_name || product?.canonical_name || matchedAlias?.canonical_name || 'Товар';
  const parts = [htmlTitle(name)];

  const whatItIs = shortenSoft(stripLeadEmoji(cleanCardText(toSentence(effectiveCard.what_it_is))), 360);
  const forWhom = shortenSoft(stripLeadEmoji(cleanCardText(toSentence(effectiveCard.who_asks_about_it))), 260);
  const useCases = listItems(effectiveCard.common_use_cases, null);
  const howToUse = shortenSoft(stripLeadEmoji(cleanCardText(effectiveCard.how_to_use_short)), 280);
  const whyInteresting = shortenSoft(stripLeadEmoji(cleanCardText(toSentence(effectiveCard.what_to_expect_soft))), 360);
  const contraindications = cleanContraindications(listItems(effectiveCard.contraindications_short, null));

  if (whatItIs) parts.push('🔥 ' + htmlTitle('Если просто:') + ' ' + escapeHtml(whatItIs));
  if (forWhom) parts.push('👥 ' + htmlTitle('Для кого:') + ' ' + escapeHtml(forWhom));
  if (useCases.length) parts.push('✅ ' + htmlTitle('Когда обычно смотрят:') + '\n' + htmlBulletList(useCases));
  if (howToUse) parts.push('🧭 ' + htmlTitle('Как используют:') + '\n' + escapeHtml(howToUse));
  if (whyInteresting) parts.push('🧠 ' + htmlTitle('Почему это интересно:') + '\n' + escapeHtml(whyInteresting));
  if (contraindications.length) {
    parts.push('⚠️ ' + htmlTitle('Ограничения:') + '\n' + escapeHtml(contraindications.join(', ') + '.'));
  }

  const hasCertificates = resources.some((row) => normalize(row.resource_type) === 'certificate' || normalize(row.topic) === 'certificates');
  parts.push(hasCertificates
    ? 'Могу дать <b>цену/PV</b>, <b>фото</b>, <b>видео</b>, <b>сертификаты</b> или <b>подробнее</b>.'
    : 'Могу дать <b>цену/PV</b>, <b>фото</b>, <b>видео</b> или <b>подробнее</b>.');
  return parts.filter(Boolean).join('\n\n');
}

function findProductBySku(products, sku) {
  return products.find((row) => String(row.sku ?? '') === String(sku ?? '')) || null;
}

function findCardBySku(productCards, sku) {
  return productCards.find((row) => String(row.sku ?? '') === String(sku ?? '')) || null;
}

function familyKey(value) {
  return normalize(value)
    // JavaScript \b is ASCII-oriented: do not use it for Russian product labels.
    .replace(/(?:^|\s)pro(?=\s|$)/g, ' ')
    .replace(/(?:^|\s)про(?=\s|$)/g, ' ')
    .replace(/(?:^|\s)комплект(?=\s|$)/g, ' ')
    .replace(/(?:^|\s)набор(?=\s|$)/g, ' ')
    .replace(/\s+/g, ' ')
    .trim();
}

function findRelatedProProduct(products, baseProduct) {
  if (!baseProduct) return null;
  const baseName = String(baseProduct.canonical_name || '');
  const baseFamily = familyKey(baseName);
  if (!baseFamily) return null;

  const candidates = products.filter((row) => {
    if (String(row.sku ?? '') === String(baseProduct.sku ?? '')) return false;
    const name = String(row.canonical_name || '');
    return hasProMarker(name) && familyKey(name).includes(baseFamily);
  });

  return candidates[0] || null;
}

function findRelatedBaseProduct(products, proProduct) {
  if (!proProduct) return null;
  const proName = String(proProduct.canonical_name || '');
  const proFamily = familyKey(proName);
  if (!proFamily) return null;

  const candidates = products.filter((row) => {
    if (String(row.sku ?? '') === String(proProduct.sku ?? '')) return false;
    const name = String(row.canonical_name || '');
    return !hasProMarker(name) && familyKey(name).includes(proFamily);
  });

  return candidates[0] || null;
}

function buildCompareAnswer(leftProduct, leftCard, rightProduct, rightCard) {
  const leftName = leftCard?.short_name || leftCard?.canonical_name || leftProduct?.canonical_name || 'Первый товар';
  const rightName = rightCard?.short_name || rightCard?.canonical_name || rightProduct?.canonical_name || 'Второй товар';

  const leftWhat = stripLeadEmoji(cleanCardText(toSentence(leftCard?.what_it_is || '')));
  const rightWhat = stripLeadEmoji(cleanCardText(toSentence(rightCard?.what_it_is || '')));
  const leftForWhom = stripLeadEmoji(cleanCardText(toSentence(leftCard?.who_asks_about_it || '')));
  const rightForWhom = stripLeadEmoji(cleanCardText(toSentence(rightCard?.who_asks_about_it || '')));
  const leftWhy = stripLeadEmoji(cleanCardText(toSentence(leftCard?.what_to_expect_soft || '')));
  const rightWhy = stripLeadEmoji(cleanCardText(toSentence(rightCard?.what_to_expect_soft || '')));

  const parts = [
    `${leftName} и ${rightName}`,
  ];

  if (leftWhat || rightWhat) {
    parts.push([
      `${leftName}: ${leftWhat || 'данные уточняются.'}`,
      `${rightName}: ${rightWhat || 'данные уточняются.'}`,
    ].join('\n'));
  }

  if (leftForWhom || rightForWhom) {
    parts.push([
      `Кому обычно ближе ${leftName}: ${leftForWhom || 'по ситуации.'}`,
      `Кому обычно ближе ${rightName}: ${rightForWhom || 'по ситуации.'}`,
    ].join('\n'));
  }

  if (leftWhy || rightWhy) {
    parts.push([
      `По ощущению ${leftName}: ${leftWhy || 'смотреть по задаче.'}`,
      `По ощущению ${rightName}: ${rightWhy || 'смотреть по задаче.'}`,
    ].join('\n'));
  }

  const leftPrice = buildCompactPriceLine(leftProduct);
  const rightPrice = buildCompactPriceLine(rightProduct);
  if (leftPrice || rightPrice) {
    parts.push([
      'По цене:',
      leftPrice ? `- ${leftName}: ${leftPrice}` : null,
      rightPrice ? `- ${rightName}: ${rightPrice}` : null,
    ].filter(Boolean).join('\n'));
  }

  parts.push('Могу сразу дать фото, видео или подробнее по любому из этих вариантов.');
  return parts.filter(Boolean).join('\n\n');
}

function parseContext(value) {
  if (!value) return {};
  if (typeof value === 'object') return value;
  try { return JSON.parse(value); } catch { return {}; }
}

const normalizedText = normalize(userText);
const reviewCommand = getReviewCommand(userText);
const conversationContext = parseContext($('Postgres: Load Conversation Context').first()?.json?.context);

if (reviewCommand) {
  const answerText = reviewCommand.kind === 'help'
    ? buildReviewHelp()
    : (reviewCommand.kind === 'gap_report'
      ? buildGapReport(envelope)
    : (reviewCommand.kind === 'next'
      ? buildReviewNext(reviewCommand, envelope)
      : (reviewCommand.kind === 'take' || reviewCommand.kind === 'apply' || reviewCommand.kind === 'verify' || reviewCommand.kind === 'close')
        ? buildReviewAction(reviewCommand, envelope)
        : buildReviewReport(reviewCommand, envelope)));  return [{
    json: {
      ...envelope,
      structured_hit: true,
      structured_source: 'review_queue',
      structured_match: {
        alias: 'review_command',
        sku: null,
        canonical_name: '/review_' + reviewCommand.kind,
      },
      dify_raw: '',
      dify_response: {
        answer_text: answerText,
        action: 'reply',
        answer_mode: 'direct_review_report',
        confidence: 1,
        knowledge_gap: false,
        gap_reason: null,
        state_updates: [],
        knowledge_candidates: [],
        task_candidates: [],
        followup_questions: [],
        needs_human_review: false,
      },
      answer_text: answerText,
      reply_text: answerText,
      action: 'reply',
      answer_mode: 'direct_review_report',
      confidence: 1,
      knowledge_gap: false,
      needs_human_review: false,
      route: 'answer',
    },
  }];
}

if (isDetailFollowupWithoutContext(userText) && !conversationContext.last_product_sku) {
  const answerText = 'Могу рассказать подробнее, но мне нужен сам товар. Напишите название: например, активатор клеток, Вэнтун, Ба-Гуа, палантин.';
  return [{
    json: {
      ...envelope,
      structured_hit: true,
      structured_source: 'postgres_cache',
      structured_match: null,
      dify_raw: '',
      dify_response: {
        answer_text: answerText,
        action: 'reply',
        answer_mode: 'direct_structured_clarify',
        confidence: 1,
        knowledge_gap: false,
        gap_reason: null,
        state_updates: [],
        knowledge_candidates: [],
        task_candidates: [],
        followup_questions: [],
        needs_human_review: false,
      },
      answer_text: answerText,
      reply_text: answerText,
      action: 'reply',
      answer_mode: 'direct_structured_clarify',
      confidence: 1,
      knowledge_gap: false,
      needs_human_review: false,
      route: 'answer',
    },
  }];
}

if (envelope.structured_hit === true) {
  return [{ json: envelope }];
}

function rowsFromNode(nodeName, arrayKey) {
  return optionalNodeItems(nodeName).flatMap((item) => {
    const json = item.json ?? {};
    if (Array.isArray(json[arrayKey])) return json[arrayKey];
    return [json];
  });
}

const aliases = rowsFromNode('Postgres: Runtime Snapshot', 'aliases')
  .filter((row) => String(row.project_id ?? row.client_id ?? '') === PROJECT_ID && isActive(row.active));

const products = rowsFromNode('Postgres: Runtime Snapshot', 'products')
  .filter((row) => String(row.project_id ?? row.client_id ?? '') === PROJECT_ID);

const productCards = rowsFromNode('Postgres: Runtime Snapshot', 'product_cards')
  .filter((row) => String(row.project_id ?? row.client_id ?? '') === PROJECT_ID);

const productDetails = rowsFromNode('Postgres: Runtime Snapshot', 'product_details')
  .filter((row) => String(row.project_id ?? row.client_id ?? '') === PROJECT_ID)
  .filter((row) => isActive(row.active))
  .filter((row) => /^(approved|owner_approved|verified)$/i.test(String(row.review_status ?? '')))
  .sort((left, right) => Number(right.priority ?? 0) - Number(left.priority ?? 0));

const productComparisons = rowsFromNode('Postgres: Runtime Snapshot', 'product_comparisons')
  .filter((row) => String(row.project_id ?? row.client_id ?? '') === PROJECT_ID)
  .filter((row) => isActive(row.active))
  .filter((row) => /^(approved|owner_approved|verified)$/i.test(String(row.review_status ?? '')))
  .sort((left, right) => Number(right.priority ?? 0) - Number(left.priority ?? 0));

const businessObjections = rowsFromNode('Postgres: Runtime Snapshot', 'business_objections')
  .filter((row) => String(row.project_id ?? row.client_id ?? '') === PROJECT_ID)
  .filter((row) => isActive(row.active))
  .filter((row) => /^(approved|owner_approved|verified)$/i.test(String(row.review_status ?? '')))
  .sort((left, right) => Number(right.priority ?? 0) - Number(left.priority ?? 0));

const businessFaq = rowsFromNode('Postgres: Runtime Snapshot', 'business_faq')
  .filter((row) => String(row.project_id ?? row.client_id ?? '') === PROJECT_ID)
  .filter((row) => isActive(row.active))
  .filter((row) => /^(approved|owner_approved|verified)$/i.test(String(row.review_status ?? '')))
  .sort((left, right) => Number(right.priority ?? 0) - Number(left.priority ?? 0));

const promotions = rowsFromNode('Postgres: Runtime Snapshot', 'promotions')
  .filter((row) => String(row.client_id ?? row.project_id ?? '') === PROJECT_ID)
  .filter((row) => String(row.tenant_id ?? 'by').trim().toLowerCase() === 'by')
  .filter((row) => String(row.status ?? '').trim().toLowerCase() === 'active')
  .filter((row) => {
    const now = Date.now();
    const startsAt = Date.parse(String(row.starts_at ?? ''));
    const endsAt = Date.parse(String(row.ends_at ?? ''));
    return Number.isFinite(startsAt) && Number.isFinite(endsAt) && startsAt <= now && now <= endsAt;
  })
  .sort((left, right) => Number(right.priority ?? 0) - Number(left.priority ?? 0) || Date.parse(String(left.ends_at)) - Date.parse(String(right.ends_at)));

const recommendationRules = rowsFromNode('Postgres: Runtime Snapshot', 'recommendation_rules')
  .filter((row) => String(row.client_id ?? row.project_id ?? '') === PROJECT_ID)
  .filter((row) => String(row.tenant_id ?? 'by').trim().toLowerCase() === 'by')
  .filter((row) => !/^(paused|disabled|archived)$/i.test(String(row.status ?? '')))
  .filter((row) => isActive(row.registration_enabled))
  .filter((row) => String(row.availability_status ?? 'available').trim().toLowerCase() === 'available');

const starterBasketTemplates = rowsFromNode('Postgres: Runtime Snapshot', 'starter_basket_templates')
  .filter((row) => String(row.client_id ?? row.project_id ?? '') === PROJECT_ID)
  .filter((row) => String(row.tenant_id ?? 'by').trim().toLowerCase() === 'by')
  .filter((row) => !/^(paused|disabled|archived)$/i.test(String(row.status ?? '')))
  .sort((left, right) => Number(right.priority ?? 0) - Number(left.priority ?? 0));

const events = rowsFromNode('Postgres: Runtime Snapshot', 'events')
  .filter((row) => String(row.client_id ?? row.project_id ?? '') === PROJECT_ID)
  .filter((row) => String(row.tenant_id ?? 'by').trim().toLowerCase() === 'by')
  .filter((row) => /^(confirmed|active)$/i.test(String(row.status ?? '')));

const communityResources = rowsFromNode('Postgres: Runtime Snapshot', 'community_resources')
  .filter((row) => String(row.client_id ?? row.project_id ?? '') === PROJECT_ID)
  .filter((row) => String(row.tenant_id ?? 'by').trim().toLowerCase() === 'by')
  .filter((row) => /^(active|confirmed)$/i.test(String(row.status ?? '')))
  .sort((left, right) => Number(right.priority ?? 0) - Number(left.priority ?? 0));

function isBasketIntent(text) {
  const value = String(text ?? '');
  return /(?:корзин|собери\s+(?:мне\s+)?(?:старт|набор)|подбери\s+(?:мне\s+)?(?:старт|набор)|что\s+(?:выгодно\s+)?взять|старт(?:овый)?\s+набор|при\s+регистрац)/i.test(value)
    || (!!conversationContext?.starter_basket && /^(?:а\s*)?(?:на\s+\d{2,5}|без\s+.+)$/i.test(value.trim()));
}

function parseBasketRequest(text) {
  const raw = String(text ?? '');
  const normalized = normalize(raw);
  const budgetMatch = raw.match(/(?:на|до|бюджет(?:ом)?\s*)?\s*(\d{2,5}(?:[ .,]\d+)?)\s*(?:byn|бел(?:орусских)?\s*руб(?:лей|ля|)?|руб(?:лей|ля|)?|р\b)/i)
    || (conversationContext?.starter_basket ? raw.match(/^(?:а\s*)?на\s+(\d{2,5})\s*$/i) : null);
  const pvMatch = raw.match(/(?:на|до|нужно|хочу)?\s*(\d{1,4})\s*(?:pv|пв)\b/i);
  const goals = [
    ['demo', /демонстрац|показать|пробовать/i],
    ['gift', /подар(?:ок|к)|дарить/i],
    ['resale', /продаж|перепродаж|заработ/i],
    ['personal', /себ[яе]|личн(?:ого|ое|ый)|домой/i],
    ['pv', /\b(?:pv|пв)\b/i],
  ];
  const goal = goals.find((item) => item[1].test(normalized))?.[0] || 'balanced';
  const excluded = [];
  for (const product of products) {
    const name = normalize(product.canonical_name);
    if (name && /(?:без|кроме|не\s+брать)\s+/.test(normalized) && new RegExp('(?:без|кроме|не\\s+брать)\\s+' + name.replace(/\s+/g, '\\s+')).test(normalized)) excluded.push(String(product.sku));
  }
  for (const alias of aliases) {
    const value = normalize(alias.alias);
    if (value && new RegExp('(?:без|кроме|не\\s+брать)\\s+' + value.replace(/\s+/g, '\\s+')).test(normalized)) excluded.push(String(alias.canonical_sku));
  }
  return {
    budgetByn: budgetMatch ? Number(String(budgetMatch[1]).replace(/\s/g, '').replace(',', '.')) : null,
    targetPv: pvMatch ? Number(pvMatch[1]) : null,
    goal,
    excluded,
  };
}

function basketScore(product, rule, goal) {
  const primary = Number(product.retail_price_byn ?? 0);
  const repeat = Number(product.partner_price_byn ?? 0);
  const futureDiscountRatio = primary > 0 && repeat > 0 ? Math.max(0, (primary - repeat) / primary) : 0.5;
  const registrationPriceScore = Math.round((1 - futureDiscountRatio) * 10);
  const goalScore = goal === 'demo' ? Number(rule.demo_score || 0)
    : goal === 'gift' ? Number(rule.gift_score || 0)
      : goal === 'resale' ? Number(rule.resale_score || 0)
        : goal === 'personal' ? Number(rule.personal_use_score || 0)
          : goal === 'pv' ? Number(rule.business_priority || 0)
            : (Number(rule.universality_score || 0) + Number(rule.personal_use_score || 0)) / 2;
  const promotionScore = promotions.some((promo) => String(promo.product_ids || '').split(',').map((item) => item.trim()).includes(String(product.sku))) ? 10 : 0;
  return goalScore * .35 + Number(rule.universality_score || 0) * .15 + Number(rule.business_priority || 0) * .15 + Number(rule.demo_score || 0) * .10 + Number(rule.gift_score || 0) * .05 + Number(rule.resale_score || 0) * .05 + registrationPriceScore * .10 + promotionScore * .05;
}

function formatBasketNumber(value) {
  const number = Number(value || 0);
  return Number.isFinite(number) ? number.toLocaleString('ru-RU', { maximumFractionDigits: 2 }) : '0';
}

function basketReply(envelope, request) {
  if (!request.budgetByn && !request.targetPv) {
    return structuredReply(envelope, null, 'Соберу стартовую корзину. На какой бюджет в BYN или какой PV ориентируемся?', 'direct_structured_basket_clarify', { basket_context: { goal: request.goal } });
  }
  const template = starterBasketTemplates.find((row) => String(row.goal || 'balanced') === request.goal) || starterBasketTemplates.find((row) => String(row.goal || '') === 'balanced');
  const preferred = new Set(String(template?.preferred_product_ids || '').split('|').map((item) => item.trim()).filter(Boolean));
  const excluded = new Set([...(request.excluded || []), ...String(template?.excluded_product_ids || '').split('|').map((item) => item.trim()).filter(Boolean)]);
  const candidates = recommendationRules
    .filter((rule) => !excluded.has(String(rule.product_id)))
    .map((rule) => ({ rule, product: products.find((product) => String(product.sku) === String(rule.product_id)) }))
    .filter((item) => item.product && Number(item.product.retail_price_byn || 0) > 0)
    .map((item) => ({ ...item, price: Number(item.product.retail_price_byn), pv: Number(item.product.partner_points || 0), score: basketScore(item.product, item.rule, request.goal) + (preferred.has(String(item.product.sku)) ? 2 : 0) }))
    .sort((left, right) => right.score - left.score || left.price - right.price);
  const selected = [];
  let total = 0;
  let totalPv = 0;
  for (const item of candidates) {
    if (request.budgetByn && total + item.price > request.budgetByn) continue;
    selected.push(item); total += item.price; totalPv += item.pv;
    if (selected.length >= 3) break;
    if (request.targetPv && totalPv >= request.targetPv) break;
  }
  if (!selected.length) {
    const lowest = candidates.slice().sort((left, right) => left.price - right.price)[0];
    if (!lowest) return structuredReply(envelope, null, 'Пока нет активных правил для стартовой корзины. Можно дать цену товара или собрать вариант после утверждения правил.', 'direct_structured_basket_empty');
    return structuredReply(envelope, null, `В заданный бюджет готовый вариант пока не помещается. Самый доступный из подходящих: ${lowest.product.canonical_name} - ${formatBasketNumber(lowest.price)} BYN, ${formatBasketNumber(lowest.pv)} PV.`, 'direct_structured_basket_budget');
  }
  const lines = selected.map((item, index) => `${index + 1}. <b>${escapeHtml(item.product.canonical_name)}</b> - ${formatBasketNumber(item.price)} BYN, ${formatBasketNumber(item.pv)} PV\n${escapeHtml(item.rule.reason_short || 'Подходит под выбранную цель.')}`);
  const promotionHint = promotions.filter((promo) => selected.some((item) => String(promo.product_ids || '').split(',').map((value) => value.trim()).includes(String(item.product.sku)))).map((promo) => promo.short_text || promo.benefit_text).filter(Boolean)[0];
  const heading = request.goal === 'balanced' ? 'Сбалансированный старт' : (template?.title || 'Стартовая корзина');
  const answerText = [`🛒 <b>${escapeHtml(heading)}</b>`, '', ...lines, '', `Итого: <b>${formatBasketNumber(total)} BYN, ${formatBasketNumber(totalPv)} PV.</b>`, promotionHint ? `Выгода по текущей акции: ${escapeHtml(String(promotionHint))}` : null, '', 'Могу пересобрать под другой бюджет, PV или цель.'].filter(Boolean).join('\n');
  return structuredReply(envelope, null, answerText, 'direct_structured_starter_basket', { basket_context: { budget_byn: request.budgetByn, target_pv: request.targetPv, goal: request.goal, template_id: template?.template_id || null, product_ids: selected.map((item) => item.product.sku) } });
}

function isPromotionIntent(text) {
  return /(?:акци[яи]|акцыи|скидк|спеццен|подар(?:ок|ки)|что\s+сейчас\s+дают|до\s+какого\s+числа)/i.test(String(text ?? ''));
}

function promotionMatchesText(row, text) {
  const normalized = normalize(text);
  const ignored = new Set(['акция', 'акции', 'акцыю', 'акцыи', 'скидка', 'скидки', 'подарок', 'подарки', 'товар', 'товары', 'сейчас', 'какие', 'какая', 'какой', 'есть', 'будет', 'можно', 'нужен', 'нужна', 'нужно', 'дайте', 'дай', 'скажи', 'скажите', 'покажи', 'по', 'на', 'для', 'что', 'это']);
  const tokens = normalized.split(' ')
    .filter((token) => token.length >= 4 && !ignored.has(token));
  if (/\bбэм\b/i.test(normalized)) tokens.push('бэм');
  if (!tokens.length) return false;
  const candidates = [row.title, row.short_text, row.full_text, row.product_ids]
    .map(normalize)
    .filter((value) => value.length >= 4);
  return candidates.some((value) => tokens.some((token) => value.includes(token)));
}

function formatPromotionEnd(value) {
  const date = new Date(String(value || ''));
  if (Number.isNaN(date.getTime())) return '';
  return date.toLocaleDateString('ru-RU', { day: '2-digit', month: '2-digit', year: 'numeric', timeZone: 'Europe/Minsk' });
}

function promotionReply(envelope, requested) {
  const list = requested.length ? requested : promotions.slice(0, 5);
  if (!list.length) {
    return structuredReply(envelope, null, 'Сейчас в структурированной базе нет активной акции для Беларуси. Проверьте раздел «Акции» в личном кабинете: условия и остатки подарков могут меняться.', 'direct_structured_promotion', { promotion_count: 0 });
  }
  const answerText = list.map((row) => {
    const parts = [`🔥 ${String(row.title || '').trim()}`];
    const brief = String(row.short_text || row.benefit_text || '').trim();
    if (brief) parts.push(brief);
    const until = formatPromotionEnd(row.ends_at);
    if (until) parts.push(`Действует до ${until}.`);
    if (String(row.source_url || '').trim()) parts.push(String(row.source_url).trim());
    return parts.join('\n');
  }).join('\n\n');
  return structuredReply(envelope, null, answerText, 'direct_structured_promotion', { promotion_count: list.length, promotion_ids: list.map((row) => row.promotion_id) });
}

function isCommunityIntent(text) {
  return /(?:канал|групп[ауые]|чат|сообществ|whieda\s*world\s*club)/i.test(String(text ?? ''));
}

function communityReply(envelope) {
  if (!communityResources.length) return structuredReply(envelope, null, 'Проверенных ссылок на сообщества пока нет.', 'direct_structured_community', { resource_count: 0 });
  const rows = communityResources.slice(0, 5);
  const answerText = rows.map((row) => [
    `📢 <b>${escapeHtml(String(row.title || 'Канал WHIEDA'))}</b>`,
    String(row.description || '').trim(),
    String(row.url || '').trim(),
  ].filter(Boolean).join('\n')).join('\n\n');
  return structuredReply(envelope, null, answerText, 'direct_structured_community', { resource_ids: rows.map((row) => row.resource_id) });
}

function isEventIntent(text) {
  return /(?:мероприят|встреч[ауеи]|презентаци|куда\s+(?:пойти|прийти)|когда\s+встреч)/i.test(String(text ?? ''));
}

function nextEventTimestamp(row) {
  const base = Date.parse(String(row.starts_at || ''));
  if (!Number.isFinite(base)) return null;
  if (!/FREQ=WEEKLY/i.test(String(row.recurrence_rule || ''))) return base >= Date.now() ? base : null;
  const week = 7 * 24 * 60 * 60 * 1000;
  const next = base + Math.max(0, Math.ceil((Date.now() - base) / week)) * week;
  return next;
}

function eventReply(envelope) {
  const upcoming = events.map((row) => ({ row, timestamp: nextEventTimestamp(row) }))
    .filter((item) => item.timestamp !== null)
    .sort((left, right) => left.timestamp - right.timestamp)
    .slice(0, 3);
  if (!upcoming.length) return structuredReply(envelope, null, 'Ближайших подтверждённых мероприятий пока нет.', 'direct_structured_event', { event_count: 0 });
  const answerText = upcoming.map(({ row, timestamp }) => {
    const when = new Date(timestamp).toLocaleString('ru-RU', { weekday: 'long', day: 'numeric', month: 'long', hour: '2-digit', minute: '2-digit', timeZone: row.timezone || 'Europe/Minsk' });
    return [
      `📅 <b>${escapeHtml(String(row.title || 'Мероприятие WHIEDA'))}</b>`,
      when.charAt(0).toUpperCase() + when.slice(1),
      String(row.address || '').trim(),
      row.contact ? `Спикер: ${escapeHtml(String(row.contact))}` : null,
      String(row.description || '').trim(),
      String(row.online_url || '').trim(),
    ].filter(Boolean).join('\n');
  }).join('\n\n');
  return structuredReply(envelope, null, answerText, 'direct_structured_event', { event_ids: upcoming.map((item) => item.row.event_id) });
}

const clarificationPrompts = rowsFromNode('Postgres: Runtime Snapshot', 'clarification_prompts')
  .filter((row) => String(row.client_id ?? row.project_id ?? '') === PROJECT_ID)
  .filter((row) => isActive(row.enabled ?? row.active))
  .filter((row) => /^(owner_approved_locked|owner_approved|approved|verified)$/i.test(String(row.owner_state ?? row.review_status ?? '')));

function clarificationText(key, fallback) {
  const row = clarificationPrompts.find((item) => String(item.clarification_key ?? '') === key);
  return String(row?.prompt_text || fallback || '').trim();
}

const capabilityResponses = rowsFromNode('Postgres: Runtime Snapshot', 'capability_responses')
  .filter((row) => String(row.client_id ?? row.project_id ?? '') === PROJECT_ID)
  .filter((row) => isActive(row.enabled ?? row.active))
  .filter((row) => /^(owner_approved_locked|owner_approved|approved|verified)$/i.test(String(row.owner_state ?? row.review_status ?? '')));

function capabilityText(intentId, fallback) {
  const row = capabilityResponses.find((item) => String(item.intent_id ?? '') === intentId);
  // Google TSV flattens real line breaks in a cell. The visible `⏎` marker lets
  // admins keep Telegram paragraphs in the Sheet without risking a broken TSV row.
  return String(row?.answer_text || fallback || '').replaceAll('⏎', '\n').trim();
}

function serviceIntentFromText(text) {
  const value = normalize(text);
  if (!value) return null;
  if (/^(привет|здравствуй|здравствуйте|добрый день|добрый вечер|доброго времени)$/.test(value)) return 'greeting';
  if (/^(как дела|как ты|как жизнь)$/.test(value)) return 'smalltalk_status';
  if (/^(что ты умеешь|что умеешь|что можешь|что ты можешь|какие у тебя возможности)$/.test(value)) return 'capabilities';
  if (/^(помощь|помоги|меню|команды|help)$/.test(value)) return 'help';
  return null;
}

const serviceIntent = startCommand ? 'greeting' : serviceIntentFromText(userText);
if (serviceIntent) {
  const fallback = {
    greeting: 'Привет! Я советник WHIEDA. Напишите, что вас интересует.',
    smalltalk_status: 'На связи. С чего начнём?',
    capabilities: 'Могу помочь с товарами, ценами, фото, видео, сравнением и бизнес-вопросами.',
    help: 'Напишите товар, цену, фото, видео, сравнение или бизнес-вопрос.',
  };
  return structuredReply(
    envelope,
    null,
    capabilityText(serviceIntent, fallback[serviceIntent]),
    startCommand ? 'direct_start' : 'direct_structured_service',
    { service_intent: serviceIntent },
  );
}

const coachCommand = getCoachCommand(userText);
if (coachCommand) {
  const response = coachReply(envelope, coachCommand);
  if (response) return response;
}

if (isBasketIntent(userText)) {
  return basketReply(envelope, parseBasketRequest(userText));
}

if (isPromotionIntent(userText)) {
  const requested = promotions.filter((row) => promotionMatchesText(row, userText)).slice(0, 5);
  return promotionReply(envelope, requested);
}

if (isEventIntent(userText)) {
  return eventReply(envelope);
}

if (isCommunityIntent(userText)) {
  return communityReply(envelope);
}

function findBusinessObjection(text) {
  const normalized = normalize(text);
  if (!normalized || hasPriceIntent(text)) return null;
  let bestMatch = null;
  for (const row of businessObjections) {
    if (!['true', '1', 'yes'].includes(String(row.active ?? '').trim().toLowerCase())) continue;
    for (const alias of String(row.aliases || '').split('|').map(normalize).filter((value) => value.length >= 4)) {
      if (!normalized.includes(alias)) continue;
      const score = alias.length + Number(row.priority || 0);
      if (!bestMatch || score > bestMatch.score) bestMatch = { row, score };
    }
  }
  return bestMatch?.row || null;
}

function businessObjectionReply(envelope, objection) {
  const answerText = String(objection.first_reply || '').trim();
  return [{
    json: {
      ...envelope,
      structured_hit: true,
      structured_source: 'postgres_cache',
      structured_match: null,
      dify_raw: '',
      dify_response: {
        answer_text: answerText,
        action: 'reply',
        answer_mode: 'direct_structured_business_objection',
        confidence: 1,
        knowledge_gap: false,
        gap_reason: null,
        state_updates: [],
        knowledge_candidates: [],
        task_candidates: [],
        followup_questions: [],
        needs_human_review: false,
      },
      answer_text: answerText,
      reply_text: answerText,
      action: 'reply',
      answer_mode: 'direct_structured_business_objection',
      confidence: 1,
      knowledge_gap: false,
      needs_human_review: false,
      route: 'answer',
    },
  }];
}

function findBusinessFaq(text) {
  const normalized = normalize(text);
  if (!normalized) return null;
  let bestMatch = null;
  for (const row of businessFaq) {
    if (!['true', '1', 'yes'].includes(String(row.active ?? '').trim().toLowerCase())) continue;
    for (const alias of String(row.aliases || '').split('|').map(normalize).filter((value) => value.length >= 4)) {
      const tokens = alias.split(' ').filter((token) => token.length >= 2);
      const matched = normalized.includes(alias) || (tokens.length >= 2 && tokens.every((token) => normalized.includes(token)));
      if (!matched) continue;
      const score = alias.length + Number(row.priority || 0) + (normalized.includes(alias) ? 20 : 0);
      if (!bestMatch || score > bestMatch.score) bestMatch = { row, score };
    }
  }
  return bestMatch?.row || null;
}

function businessFaqReply(envelope, faq) {
  const answerText = String(faq.answer_text || '').trim();
  return [{
    json: {
      ...envelope,
      structured_hit: true,
      structured_source: 'postgres_cache',
      structured_match: null,
      dify_raw: '',
      dify_response: {
        answer_text: answerText,
        action: 'reply',
        answer_mode: 'direct_structured_business_faq',
        confidence: 1,
        knowledge_gap: false,
        gap_reason: null,
        state_updates: [],
        knowledge_candidates: [],
        task_candidates: [],
        followup_questions: [],
        needs_human_review: false,
      },
      answer_text: answerText,
      reply_text: answerText,
      action: 'reply',
      answer_mode: 'direct_structured_business_faq',
      confidence: 1,
      knowledge_gap: false,
      needs_human_review: false,
      route: 'answer',
    },
  }];
}

function getCoachCommand(text) {
  const source = String(text || '').trim();
  if (/^\/?(?:коуч|coach)\s+(?:продолжить|дальше|текущий\s+день)\s*$/i.test(source)) {
    return { kind: 'first_week_continue' };
  }
  if (/^\/?(?:коуч|coach)\s+(?:готово|выполнил|выполнила|сделал|сделала)\s*$/i.test(source)) {
    return { kind: 'first_week_complete' };
  }
  const answerMatch = source.match(/^\/?(?:коуч|coach)\s+ответ\s+(\d+)\s+(.+)$/i);
  if (answerMatch) {
    return { kind: 'feedback', objectionNumber: Number(answerMatch[1]), draft: String(answerMatch[2]).trim() };
  }
  if (/^\/?(?:коуч|coach)\s+(?:старт|7\s*дней|первые\s+7\s+дней)\s*$/i.test(source)) {
    return { kind: 'first_week_menu' };
  }
  const dayMatch = source.match(/^\/?(?:коуч|coach)\s+день\s+([1-7])\s*$/i);
  if (dayMatch) return { kind: 'first_week_day', day: Number(dayMatch[1]) };
  const match = source.match(/^\/?(?:коуч|coach)(?:\s+возражения)?(?:\s+(\d+))?\s*$/i);
  if (!match) return null;
  return { kind: 'practice', objectionNumber: match[1] ? Number(match[1]) : null };
}

function coachReply(envelope, coachCommand) {
  const sorted = businessObjections
    .filter((row) => ['true', '1', 'yes'].includes(String(row.active ?? '').trim().toLowerCase()))
    .sort((left, right) => String(left.objection_id).localeCompare(String(right.objection_id)));
  const firstWeek = [
    ['День 1. Твоя опора', 'Выбери один товар, который знаешь лучше всего. Прочитай его карточку, цену и ограничения. Сформулируй одну честную личную причину, почему он тебе интересен.', 'Напиши: коуч день 2'],
    ['День 2. Короткое знакомство', 'Составь список из трёх людей, с которыми можно спокойно поговорить. Не продавай: спроси, какая тема им сейчас ближе - товар, самочувствие в быту или дополнительный доход.', 'Напиши: коуч день 3'],
    ['День 3. Один понятный материал', 'Отправь одному человеку только один подходящий материал: карточку, фото или видео. Не присылай длинный каталог и не обещай результат.', 'Напиши: коуч день 4'],
    ['День 4. Учимся слушать', 'Возьми один реальный вопрос или сомнение собеседника. Сначала признай его и задай уточняющий вопрос. Для тренировки можно написать: коуч.', 'Напиши: коуч день 5'],
    ['День 5. Разбираем продукт', 'Сравни два варианта только по утверждённым параметрам: задача, комплектация, цена и ограничения. Если данных нет, не угадывай.', 'Напиши: коуч день 6'],
    ['День 6. Бизнес без обещаний', 'Изучи PV, повторную покупку и условия маркетинг-плана. Твоя задача - объяснить механику, а не обещать человеку доход или срок.', 'Напиши: коуч день 7'],
    ['День 7. Подводим итоги', 'Отметь: с кем поговорил, какой вопрос повторялся и где не хватило ответа. Это и есть твой следующий учебный план. Неясные вопросы бот отправит в gap-очередь.', 'Дальше: коуч - тренировка возражений.'],
  ];
  const storedProgress = conversationContext?.coach_first_week && typeof conversationContext.coach_first_week === 'object'
    ? conversationContext.coach_first_week
    : {};
  const currentDay = Math.min(7, Math.max(1, Number(storedProgress.current_day || 1)));
  const completedThrough = Math.min(7, Math.max(0, Number(storedProgress.completed_through || 0)));
  const firstWeekResponse = (day, answerMode, prefix = null) => {
    const item = firstWeek[day - 1];
    const answerText = [prefix, item[0], '', item[1], '', item[2], '', 'Когда сделаешь - напиши: коуч готово.'].filter(Boolean).join('\n');
    return directCoachResponse(envelope, answerText, answerMode, {
      coach_first_week: {
        started: true,
        current_day: day,
        completed_through: Math.max(completedThrough, day - 1),
        updated_at: new Date().toISOString(),
      },
    });
  };
  if (coachCommand.kind === 'first_week_menu') {
    return firstWeekResponse(1, 'direct_coach_first_week_start', 'Первые 7 дней партнёра. Начинаем с первого шага.');
  }
  if (coachCommand.kind === 'first_week_continue') {
    if (!storedProgress.started) return firstWeekResponse(1, 'direct_coach_first_week_start', 'Маршрут ещё не начат.');
    return firstWeekResponse(currentDay, 'direct_coach_first_week_continue', `Продолжаем: день ${currentDay} из 7.`);
  }
  if (coachCommand.kind === 'first_week_complete') {
    if (!storedProgress.started) return firstWeekResponse(1, 'direct_coach_first_week_start', 'Сначала начни маршрут.');
    if (currentDay >= 7) {
      return directCoachResponse(envelope, 'Первые 7 дней завершены. Хорошая работа. Дальше можно перейти к тренировке возражений: напиши «коуч».', 'direct_coach_first_week_complete', {
        coach_first_week: {
          started: true,
          current_day: 7,
          completed_through: 7,
          finished: true,
          updated_at: new Date().toISOString(),
        },
      });
    }
    const nextDay = currentDay + 1;
    const item = firstWeek[nextDay - 1];
    const answerText = ['Отлично. Шаг отмечен.', '', item[0], '', item[1], '', item[2], '', 'Когда сделаешь - напиши: коуч готово.'].join('\n');
    return directCoachResponse(envelope, answerText, 'direct_coach_first_week_complete', {
      coach_first_week: {
        started: true,
        current_day: nextDay,
        completed_through: nextDay - 1,
        updated_at: new Date().toISOString(),
      },
    });
  }
  if (coachCommand.kind === 'first_week_day') {
    return firstWeekResponse(coachCommand.day, 'direct_coach_first_week_day');
  }
  if (!sorted.length) return null;
  const objectionNumber = coachCommand.objectionNumber;
  if (coachCommand.kind === 'feedback') {
    const objection = sorted[objectionNumber - 1];
    if (!objection) return directCoachResponse(envelope, 'Такого номера нет. Напиши «коуч», чтобы увидеть шесть ситуаций.', 'direct_coach_objections_clarify');
    return coachFeedbackReply(envelope, objection, coachCommand.draft);
  }

  if (!objectionNumber) {
    const menu = sorted.map((row, index) => `${index + 1}. ${String(row.title || '').trim()}`).join('\n');
    const answerText = [
      'Тренировка возражений',
      '',
      'Выбери ситуацию:',
      menu,
      '',
      'Напиши: коуч 1, коуч 2 и так далее. Я дам реплику клиента и ориентир для разбора.',
    ].join('\n');
    return directCoachResponse(envelope, answerText, 'direct_coach_objections_menu');
  }

  const objection = sorted[objectionNumber - 1];
  if (!objection) {
    return directCoachResponse(envelope, 'Такого номера нет. Напиши «коуч», чтобы увидеть шесть ситуаций.', 'direct_coach_objections_clarify');
  }

  const answerText = [
    `Тренировка: ${String(objection.title || '').trim()}`,
    '',
    `Клиент: «${String(objection.title || '').trim()}».`,
    '',
    'Твоя задача: спокойно ответь своими словами и задай один уточняющий вопрос. Не спорь и не обещай доход или результат.',
    '',
    `Ориентир для самопроверки: ${String(objection.first_reply || '').trim()}`,
    '',
    `Что выяснить дальше: ${String(objection.clarify || '').trim()}`,
    '',
    `Не делать: ${String(objection.do_not_say || '').trim()}`,
    '',
    `Когда ответишь, пришли одной строкой: коуч ответ ${objectionNumber} [твой текст].`,
  ].join('\n');
  return directCoachResponse(envelope, answerText, 'direct_coach_objection_practice');
}

function coachFeedbackReply(envelope, objection, draft) {
  const text = String(draft || '').trim();
  const lower = normalize(text);
  const hasQuestion = /\?|\b(что|какой|какая|какие|почему|как|сколько|когда)\b/i.test(text);
  const hasEmpathy = /(^| )(понимаю|согласен|справедлив|нормальный|логичный|верно)( |$)/i.test(lower);
  const hasIncomePromise = /\b(гарантир|заработа|доход|окупит|отобьет|без риска|точно получ)\b/i.test(lower);
  const hasArgumentativeTone = /\b(не пирамида|не развод|ты не прав|это ерунда|точно не так)\b/i.test(lower);
  const checks = [
    `${hasEmpathy ? '✓' : '—'} Сначала признал сомнение человека`,
    `${hasQuestion ? '✓' : '—'} Есть уточняющий вопрос`,
    `${hasIncomePromise ? '⚠️' : '✓'} Нет обещаний дохода, окупаемости или отсутствия риска`,
    `${hasArgumentativeTone ? '⚠️' : '✓'} Нет спора с человеком и категоричных заверений`,
  ];
  const improvements = [];
  if (!hasEmpathy) improvements.push('Начни с признания сомнения: «понимаю» или «нормальный вопрос».');
  if (!hasQuestion) improvements.push('Добавь один вопрос, чтобы понять настоящую причину сомнения.');
  if (hasIncomePromise) improvements.push('Убери обещание дохода или окупаемости: этого нельзя гарантировать.');
  if (hasArgumentativeTone) improvements.push('Не доказывай, что человек неправ. Сначала уточни, что именно его настораживает.');
  const answerText = [
    `Разбор: ${String(objection.title || '').trim()}`,
    '',
    'Твой ответ:',
    text,
    '',
    'Проверка:',
    ...checks,
    '',
    improvements.length ? `Что улучшить:\n- ${improvements.join('\n- ')}` : 'Хорошая основа. Теперь в живом разговоре не спеши с объяснением: сначала дождись ответа на свой вопрос.',
    '',
    `Ориентир по утверждённой базе: ${String(objection.first_reply || '').trim()}`,
    `Следующий шаг: ${String(objection.next_step || '').trim()}`,
  ].join('\n');
  return directCoachResponse(envelope, answerText, 'direct_coach_objection_feedback');
}

function directCoachResponse(envelope, answerText, answerMode, contextUpdates = null) {
  return [{
    json: {
      ...envelope,
      structured_hit: true,
      structured_source: 'postgres_cache',
      structured_match: null,
      context_updates: contextUpdates || {},
      dify_raw: '',
      dify_response: {
        answer_text: answerText,
        action: 'reply',
        answer_mode: answerMode,
        confidence: 1,
        knowledge_gap: false,
        gap_reason: null,
        state_updates: [],
        knowledge_candidates: [],
        task_candidates: [],
        followup_questions: [],
        needs_human_review: false,
      },
      answer_text: answerText,
      reply_text: answerText,
      action: 'reply',
      answer_mode: answerMode,
      confidence: 1,
      knowledge_gap: false,
      needs_human_review: false,
      route: 'answer',
    },
  }];
}

function findApprovedComparison(leftSku, rightSku) {
  const left = String(leftSku ?? '');
  const right = String(rightSku ?? '');
  return productComparisons.find((row) =>
    (String(row.left_sku ?? '') === left && String(row.right_sku ?? '') === right) ||
    (String(row.left_sku ?? '') === right && String(row.right_sku ?? '') === left)
  ) || null;
}

const aliasMatches = aliases
  .map((row) => {
    const alias = normalize(row.alias);
    const aliasCompact = normalizeCompact(row.alias);
    const normalizedCompact = normalizeCompact(userText);
    const exact = !!alias && (normalizedText === alias || normalizedCompact === aliasCompact);
    const partial = !!alias && (normalizedText.includes(alias) || normalizedCompact.includes(aliasCompact));
    const fuzzy = !!alias && fuzzySingleWordMatch(normalizedText, alias);
    const matchType = normalize(row.match_type || 'alias');
    // Weak aliases are only stored to document a clarification route. They
    // must not win a partial/fuzzy product match inside a longer sentence.
    const matched = matchType === 'weak' ? exact : (exact || partial || fuzzy);
    let score = Number(row.priority ?? 0);
    if (exact) {
      score += 10000 + alias.length;
    } else if (partial) {
      score += 2000 + alias.length;
    } else if (fuzzy) {
      score += 500 + alias.length;
    }
    return {
      row,
      alias,
      matched,
      priority: score,
    };
  })
  .filter((entry) => entry.matched);

function productNameMatchScore(canonicalName) {
  const alias = normalize(canonicalName);
  if (!alias) return { alias, matched: false, score: 0 };
  if (normalizedText.includes(alias)) return { alias, matched: true, score: 1000 + alias.length };

  const stopWords = new Set(['набор', 'комплект', 'косметики', 'продукции', 'whieda']);
  const tokens = alias.split(' ').filter((token) => token.length > 1 && !stopWords.has(token));
  if (!tokens.length) return { alias, matched: false, score: 0 };

  const matchedTokens = tokens.filter((token) => normalizedText.includes(token));
  const hasSpecificToken = matchedTokens.some((token) => /[a-z]/i.test(token) || /\d/.test(token) || token.length >= 5);
  const ratio = matchedTokens.length / tokens.length;
  const matched = hasSpecificToken && matchedTokens.length >= 2 && ratio >= 0.5;

  return { alias, matched, score: matchedTokens.length * 100 + Math.round(ratio * 10) };
}

const productNameMatches = products
  .map((row) => {
    const canonicalName = String(row.canonical_name ?? '');
    const match = productNameMatchScore(canonicalName);
    return {
      row: {
        alias: canonicalName,
        canonical_sku: row.sku,
        canonical_name: canonicalName,
      },
      alias: match.alias,
      matched: match.matched,
      priority: match.score,
    };
  })
  .filter((entry) => entry.matched);

const matches = [...aliasMatches, ...productNameMatches]
  .sort((a, b) => b.priority - a.priority || b.alias.length - a.alias.length);

const proMatches = hasProMarker(userText)
  ? matches.filter((entry) => hasProMarker([entry.row.alias, entry.row.canonical_name].filter(Boolean).join(' ')))
  : [];
let best = (proMatches[0] || matches[0])?.row;
const weakClarification = weakColorOrBeltClarification(userText);
if (weakClarification?.direct) best = { ...weakClarification, canonical_sku: weakClarification.sku };
const distinctMatchedProducts = matches
  .map((entry) => entry.row)
  .filter((row, index, list) => list.findIndex((item) => String(item.canonical_sku ?? '') === String(row.canonical_sku ?? '')) === index);

if (normalize(userText) === 'паста') {
  return structuredReply(envelope, null, 'Вы про <b>зубную пасту с экстрактом полыни</b> или <b>Пасту Цинфэн</b>?', 'direct_structured_clarify', {
    clarification_key: 'product_ambiguity_paste',
    clarification_options: ['Зубная паста с полынью', 'Паста Цинфэн'],
  });
}

// Explicit list calculation is deliberately separate from the auto-basket.
// It only sums current structured prices; it does not invent discounts or
// registration rules.
const cartListMatch = String(userText || '').match(/^(?:посчитай|рассчитай|считай|корзина)\s*[:\-]\s*(.+)$/i);
if (cartListMatch) {
  const names = String(cartListMatch[1]).split(/[,;+\n]/).map((value) => value.trim()).filter(Boolean);
  const selected = [];
  const missing = [];
  for (const name of names.slice(0, 12)) {
    const needle = normalize(name);
    const alias = aliases
      .filter((row) => normalize(row.alias) === needle || normalize(row.canonical_name) === needle)
      .sort((left, right) => Number(right.priority || 0) - Number(left.priority || 0))[0];
    const product = alias && products.find((row) => String(row.sku) === String(alias.canonical_sku));
    if (!product || selected.some((item) => String(item.sku) === String(product.sku))) {
      if (!product) missing.push(name);
      continue;
    }
    selected.push(product);
  }
  if (!selected.length) return structuredReply(envelope, null, 'Не узнал товары в списке. Напишите через запятую, например: <code>посчитай: активатор, БЭМ, Ба-Гуа</code>.', 'direct_structured_cart_clarify');
  const sum = (key) => selected.reduce((total, product) => total + Number(product[key] || 0), 0);
  const lines = selected.map((product, index) => `${index + 1}. <b>${escapeHtml(product.canonical_name)}</b> — ${formatBasketNumber(product.retail_price_byn)} BYN / ${formatBasketNumber(product.partner_price_byn)} BYN, ${formatBasketNumber(product.partner_points)} PV`);
  const answerText = [
    '🛒 <b>Расчёт списка</b>', '', ...lines, '',
    `Первичная/розничная: <b>${formatBasketNumber(sum('retail_price_byn'))} BYN</b>.`,
    `Повторная/партнёрская: <b>${formatBasketNumber(sum('partner_price_byn'))} BYN</b>.`,
    `Объём: <b>${formatBasketNumber(sum('partner_points'))} PV</b>.`,
    missing.length ? `Не распознал: ${escapeHtml(missing.join(', '))}.` : null,
    '', 'Могу убрать товар, добавить другой или подобрать более выгодный вход.',
  ].filter(Boolean).join('\n');
  return structuredReply(envelope, null, answerText, 'direct_structured_cart', { cart_context: { product_ids: selected.map((product) => product.sku) } });
}

// Business terms such as PV must win over a short accidental product alias.
const matchedBusinessFaq = findBusinessFaq(userText);
if (matchedBusinessFaq) {
  return businessFaqReply(envelope, matchedBusinessFaq);
}

if (!best && hasPriceIntent(userText) && conversationContext.last_product_sku) {
  const rememberedProduct = products.find((row) => String(row.sku ?? '') === String(conversationContext.last_product_sku ?? ''));
  if (rememberedProduct) {
    best = {
      alias: conversationContext.last_product_name || rememberedProduct.canonical_name,
      canonical_sku: rememberedProduct.sku,
      canonical_name: rememberedProduct.canonical_name,
    };
  }
}

if (best && hasPriceIntent(userText)) {
  const product = products.find((row) => String(row.sku ?? '') === String(best.canonical_sku ?? ''));

  if (product) {
    const answerText = buildPriceAnswer(product, best, {
      partnerOnly: wantsPartnerPrice(userText),
      retailOnly: wantsRetailPrice(userText),
    });

    return [{
      json: {
        ...envelope,
        structured_hit: true,
        structured_source: 'postgres_cache',
        structured_match: {
          alias: best.alias,
          sku: best.canonical_sku,
          canonical_name: best.canonical_name,
        },
        dify_raw: '',
        dify_response: {
          answer_text: answerText,
          action: 'reply',
          answer_mode: 'direct_structured',
          confidence: 1,
          knowledge_gap: false,
          gap_reason: null,
          state_updates: [],
          knowledge_candidates: [],
          task_candidates: [],
          followup_questions: [],
          needs_human_review: false,
        },
        answer_text: answerText,
        reply_text: answerText,
        action: 'reply',
        answer_mode: 'direct_structured',
        confidence: 1,
        knowledge_gap: false,
        needs_human_review: false,
        route: 'answer',
      },
    }];
  }
}

if (!best && hasPriceIntent(userText)) {
  return structuredReply(
    envelope,
    null,
    clarificationText('price_product_unknown', 'Цену какого товара посмотреть?'),
    'direct_structured_clarify',
    { clarification_key: 'price_product_unknown' },
  );
}

if (!best && conversationContext.last_product_sku && (isProductContextFollowup(userText) || isDetailsIntent(userText))) {
  const rememberedProduct = products.find((row) => String(row.sku ?? '') === String(conversationContext.last_product_sku ?? ''));
  if (rememberedProduct) {
    best = {
      alias: conversationContext.last_product_alias || conversationContext.last_product_name || rememberedProduct.canonical_name,
      canonical_sku: rememberedProduct.sku,
      canonical_name: rememberedProduct.canonical_name,
    };
  }
}

// A short "дай фото/видео" continues the last product conversation.
const mediaContextSku = conversationContext.last_product_sku || envelope.last_product_sku;
if (!best && /фото|фотк|фотограф|картин|изображен|видео|ютуб|youtube|обзор|сертифик|декларац|сгр|патент|халяль/i.test(userText) && mediaContextSku) {
  const rememberedProduct = findProductBySku(products, mediaContextSku);
  if (rememberedProduct) {
    best = {
      alias: conversationContext.last_product_alias || conversationContext.last_product_name || rememberedProduct.canonical_name,
      canonical_sku: rememberedProduct.sku,
      canonical_name: rememberedProduct.canonical_name,
    };
  }
}

const explicitPhotoIntent = /фото|фотк|фотограф|картин|изображен/i.test(userText);
const explicitVideoIntent = /видео|ютуб|youtube|обзор/i.test(userText);
const explicitCertificateIntent = /сертифик|декларац|сгр|патент|халяль/i.test(userText);
if (best && (explicitPhotoIntent || explicitVideoIntent || explicitCertificateIntent)) {
  const product = findProductBySku(products, best.canonical_sku);
  const card = findCardBySku(productCards, best.canonical_sku);
  const resources = getProductResources(best.canonical_sku);
  if (explicitPhotoIntent) {
    const photo = pickCardPhoto(card, best, resources);
    if (photo) {
      return structuredReply(envelope, best, 'Отправляю фото: ' + escapeHtml(photo.canonical_name || photo.title || best.canonical_name), 'direct_structured_photo', {
        telegram_photo_url: photo.photo_url,
        telegram_photo_caption: null,
        telegram_photo_resource_id: photo.resource_id || null,
        telegram_media_mode: 'photo',
      });
    }
  }
  if (explicitVideoIntent) {
    const video = resources.find((row) => {
      const type = normalize(row.resource_type);
      const blob = normalize([row.resource_type, row.title, row.url, row.topic].filter(Boolean).join(' '));
      return type === 'video' || blob.includes('youtube') || blob.includes('rutube') || blob.includes('video');
    });
    if (video) {
      const title = escapeHtml(String(video.title || video.canonical_name || 'видео').trim());
      return structuredReply(envelope, best, 'Вот видео по товару: ' + title + '\n' + String(video.url || '').trim(), 'direct_structured_resource', {
        resource_id: video.resource_id || null,
        resource_type: video.resource_type || 'video',
      });
    }
  }
  if (explicitCertificateIntent) {
    const certificates = resources
      .filter((row) => normalize(row.resource_type) === 'certificate' || normalize(row.topic) === 'certificates')
      .sort((left, right) => Number(right.priority || 0) - Number(left.priority || 0));
    if (certificates.length) {
      const lines = [`<b>${escapeHtml(String(best.canonical_name || product?.canonical_name || 'Товар'))}</b>`, '', '<b>Официальные документы:</b>'];
      for (const row of certificates.slice(0, 8)) lines.push('• ' + escapeHtml(String(row.title || 'Документ')) + '\n' + String(row.url || '').trim());
      if (certificates.length > 8) lines.push(`\nПоказаны первые 8 из ${certificates.length} документов.`);
      return structuredReply(envelope, best, lines.join('\n'), 'direct_structured_certificates', {
        resource_ids: certificates.map((row) => row.resource_id || null).filter(Boolean),
        resource_type: 'certificate',
      });
    }
    return structuredReply(envelope, best, `<b>${escapeHtml(String(best.canonical_name || product?.canonical_name || 'Товар'))}</b>\n\nВ структурированной базе пока нет официального документа по этому товару.`, 'direct_structured_certificates_missing', { resource_type: 'certificate' });
  }
}

const requestedDetailTopic = detailTopicFromText(userText);
// An explicit request for an internal/deep analysis must reach Dify even when
// the product has no pre-written SQL detail section yet.
if (best && isDetailsIntent(userText) && !isDeepKnowledgeIntent(userText)) {
  const product = findProductBySku(products, best.canonical_sku);
  const card = findCardBySku(productCards, best.canonical_sku);
  const canonicalName = String(best.canonical_name || product?.canonical_name || 'Товар').trim();
  const approvedDetail = requestedDetailTopic
    ? productDetails.find((row) => String(row.sku ?? '') === String(best.canonical_sku ?? '') && String(row.topic ?? '').toLowerCase() === requestedDetailTopic)
    : null;

  if (approvedDetail) {
    return structuredReply(
      envelope,
      best,
      buildApprovedDetailAnswer(approvedDetail, canonicalName),
      'direct_structured_detail',
      { detail_topic: requestedDetailTopic, detail_id: approvedDetail.detail_id || null },
    );
  }

  if (requestedDetailTopic === 'materials') {
    const materialResources = getProductResources(best.canonical_sku)
      .filter((row) => !isPhotoResource(row) && String(row.url || '').trim())
      .sort((left, right) => Number(right.priority || 0) - Number(left.priority || 0))
      .slice(0, 3);
    if (materialResources.length) {
      const lines = ['<b>' + escapeHtml(canonicalName) + '</b>', '', '<b>Материалы:</b>'];
      for (const resource of materialResources) {
        const title = escapeHtml(String(resource.title || resource.resource_type || 'материал').trim());
        lines.push('• ' + title + '\n' + String(resource.url || '').trim());
      }
      return structuredReply(envelope, best, lines.join('\n'), 'direct_structured_resource', {
        detail_topic: 'materials',
        resource_type: 'materials',
        resource_ids: materialResources.map((row) => row.resource_id || null).filter(Boolean),
      });
    }
    return structuredReply(
      envelope,
      best,
      '<b>' + escapeHtml(canonicalName) + '</b>\n\nПроверенных материалов по этому товару пока нет. Могу показать <b>цену/PV</b>, <b>фото</b>, <b>видео</b> или другую тему из меню.',
      'direct_structured_resource_pending',
      { detail_topic: 'materials', resource_type: 'materials' },
    );
  } else if (requestedDetailTopic) {
    const fallback = buildDetailFallback(card, requestedDetailTopic, canonicalName);
    if (fallback) {
      return structuredReply(envelope, best, fallback, 'direct_structured_detail', { detail_topic: requestedDetailTopic });
    }
    const answerText = '<b>' + escapeHtml(canonicalName) + '</b>\n\nПроверенный раздел по этой теме ещё готовится. Сейчас могу дать <b>материалы</b> или <b>видео</b>.';
    return structuredReply(envelope, best, answerText, 'direct_structured_detail_pending', { detail_topic: requestedDetailTopic });
  } else {
    return structuredReply(envelope, best, buildDetailsMenu(canonicalName), 'direct_structured_details_menu');
  }
}

if (hasCompareIntent(userText)) {
  let leftProduct = null;
  let rightProduct = null;
  let contextProduct = null;

  if (distinctMatchedProducts.length >= 2) {
    leftProduct = findProductBySku(products, distinctMatchedProducts[0]?.canonical_sku);
    rightProduct = findProductBySku(products, distinctMatchedProducts[1]?.canonical_sku);
  } else {
    const rememberedProduct = findProductBySku(products, conversationContext.last_product_sku);
    const currentProduct = best ? findProductBySku(products, best.canonical_sku) : null;
    const anchor = currentProduct || rememberedProduct;
    contextProduct = anchor;

    if (anchor) {
      if (hasProMarker(userText) || hasProMarker(anchor.canonical_name)) {
        if (hasProMarker(anchor.canonical_name)) {
          leftProduct = findRelatedBaseProduct(products, anchor);
          rightProduct = anchor;
        } else {
          leftProduct = anchor;
          rightProduct = findRelatedProProduct(products, anchor);
        }
      }
    }
  }

  if (leftProduct && rightProduct) {
    const leftCard = findCardBySku(productCards, leftProduct.sku);
    const rightCard = findCardBySku(productCards, rightProduct.sku);
    const approvedComparison = findApprovedComparison(leftProduct.sku, rightProduct.sku);
    const answerText = String(approvedComparison?.answer_text || '').trim() || buildCompareAnswer(leftProduct, leftCard, rightProduct, rightCard);

    return [{
      json: {
        ...envelope,
        structured_hit: true,
        structured_source: 'postgres_cache',
        structured_match: {
          alias: leftProduct.canonical_name + ' vs ' + rightProduct.canonical_name,
          // Keep the product the user was discussing as the follow-up anchor.
          // Otherwise a PRO -> comparison -> price chain jumps to the base version.
          sku: (contextProduct || rightProduct).sku,
          canonical_name: (contextProduct || rightProduct).canonical_name,
        },
        dify_raw: '',
        dify_response: {
          answer_text: answerText,
          action: 'reply',
          answer_mode: approvedComparison ? 'direct_structured_comparison_layer' : 'direct_structured_compare',
          confidence: 1,
          knowledge_gap: false,
          gap_reason: null,
          state_updates: [],
          knowledge_candidates: [],
          task_candidates: [],
          followup_questions: [],
          needs_human_review: false,
        },
        answer_text: answerText,
        reply_text: answerText,
        action: 'reply',
        answer_mode: approvedComparison ? 'direct_structured_comparison_layer' : 'direct_structured_compare',
        confidence: 1,
        knowledge_gap: false,
        needs_human_review: false,
        route: 'answer',
      },
    }];
  }
}

if (!best && isActivatorLikePrompt(userText) && (hasDescriptionIntent(userText) || isExplicitProductAsk(userText) || normalize(userText) === 'активатор' || normalize(userText) === 'ативатор')) {
  const answerText = clarificationText(
    'product_ambiguity_activator',
    'Вы про Активатор клеток или Активатор клеток PRO?',
  );
  return [{
    json: {
      ...envelope,
      structured_hit: true,
      structured_source: 'postgres_cache',
      structured_match: {
        alias: 'активатор',
        sku: 'M015-00',
        canonical_name: 'Активатор клеток',
      },
      dify_raw: '',
      dify_response: {
        answer_text: answerText,
        action: 'reply',
        answer_mode: 'direct_structured_clarify',
        confidence: 1,
        knowledge_gap: false,
        gap_reason: null,
        state_updates: [],
        knowledge_candidates: [],
        task_candidates: [],
        followup_questions: [],
        needs_human_review: false,
      },
      answer_text: answerText,
      reply_text: answerText,
      action: 'reply',
      answer_mode: 'direct_structured_clarify',
      confidence: 1,
      knowledge_gap: false,
      needs_human_review: false,
      route: 'answer',
    },
  }];
}

if ((!best && isSoyPeptideLikePrompt(userText)) || (best && String(best.canonical_sku || '') === 'F038-00' && ['пептид', 'пептиды', 'соевые пептиды'].includes(normalize(userText)))) {
  const answerText = 'Соевый пептид WHIEDA знаю. Это наш продукт для ежедневной нутрицевтической поддержки. Что вам сейчас важнее: коротко что это, цена, фото, видео или рассказать подробнее?';
  return [{
    json: {
      ...envelope,
      structured_hit: true,
      structured_source: 'postgres_cache',
      structured_match: {
        alias: best?.alias || 'пептид',
        sku: 'F038-00',
        canonical_name: 'Соевый пептид',
      },
      dify_raw: '',
      dify_response: {
        answer_text: answerText,
        action: 'reply',
        answer_mode: 'direct_structured_clarify',
        confidence: 1,
        knowledge_gap: false,
        gap_reason: null,
        state_updates: [],
        knowledge_candidates: [],
        task_candidates: [],
        followup_questions: [],
        needs_human_review: false,
      },
      answer_text: answerText,
      reply_text: answerText,
      action: 'reply',
      answer_mode: 'direct_structured_clarify',
      confidence: 1,
      knowledge_gap: false,
      needs_human_review: false,
      route: 'answer',
    },
  }];
}

if ((weakClarification && !weakClarification.direct) || (best && isAmbiguousShortAlias(userText, best.alias || best.canonical_name))) {
  const weakMatch = weakClarification || null;
  const selected = weakMatch || best;
  const canonicalName = String(selected.canonical_name || selected.alias || 'товар').trim();
  const answerText = weakMatch ? weakMatch.answer : (canonicalName === 'Соевый пептид'
    ? 'Соевый пептид WHIEDA знаю. Что вам сейчас важнее: коротко что это, цена, фото, видео или рассказать подробнее?'
    : clarificationText('product_ambiguity_activator', 'Вы про Активатор клеток или Активатор клеток PRO?'));
  return [{
    json: {
      ...envelope,
      structured_hit: true,
      structured_source: 'postgres_cache',
      structured_match: {
        alias: selected.alias,
        sku: selected.sku || selected.canonical_sku,
        canonical_name: selected.canonical_name,
      },
      dify_raw: '',
      dify_response: {
        answer_text: answerText,
        action: 'reply',
        answer_mode: 'direct_structured_clarify',
        confidence: 1,
        knowledge_gap: false,
        gap_reason: null,
        state_updates: [],
        knowledge_candidates: [],
        task_candidates: [],
        followup_questions: [],
        needs_human_review: false,
      },
      answer_text: answerText,
      reply_text: answerText,
      action: 'reply',
      answer_mode: 'direct_structured_clarify',
      confidence: 1,
      knowledge_gap: false,
      needs_human_review: false,
      route: 'answer',
    },
  }];
}

if (best && (hasDescriptionIntent(userText) || isBareProductPrompt(userText, best.alias || best.canonical_name))) {
  const product = products.find((row) => String(row.sku ?? '') === String(best.canonical_sku ?? '')) || null;
  const card = productCards.find((row) => String(row.sku ?? '') === String(best.canonical_sku ?? '')) || null;
  const cardResources = getProductResources(best.canonical_sku);

  if (card) {
    const answerText = buildProductCardAnswer(card, product, best, cardResources);
    const telegramPhoto = pickCardPhoto(card, best, cardResources);

    return [{
      json: {
        ...envelope,
        structured_hit: true,
        structured_source: 'postgres_cache',
        structured_match: {
          alias: best.alias,
          sku: best.canonical_sku,
          canonical_name: best.canonical_name,
        },
        dify_raw: '',
        dify_response: {
          answer_text: answerText,
          action: 'reply',
          answer_mode: 'direct_structured_card',
          confidence: 1,
          knowledge_gap: false,
          gap_reason: null,
          state_updates: [],
          knowledge_candidates: [],
          task_candidates: [],
          followup_questions: [],
          needs_human_review: false,
        },
        answer_text: answerText,
        reply_text: answerText,
        telegram_photo_url: telegramPhoto?.photo_url || null,
        telegram_photo_caption: telegramPhoto?.caption || null,
        telegram_photo_resource_id: telegramPhoto?.resource_id || null,
        telegram_media_mode: telegramPhoto ? 'photo_then_text_pending' : 'message',
        action: 'reply',
        answer_mode: 'direct_structured_card',
        confidence: 1,
        knowledge_gap: false,
        needs_human_review: false,
        route: 'answer',
      },
    }];
  }

  return [{
    json: {
      ...envelope,
      structured_hit: true,
      structured_source: 'postgres_cache',
      structured_match: {
        alias: best.alias,
        sku: best.canonical_sku,
        canonical_name: best.canonical_name,
      },
      dify_raw: '',
      dify_response: {
        answer_text: 'По этому товару пока нет утвержденной короткой карточки. Записал это на доработку.',
        action: 'reply',
        answer_mode: 'direct_structured_gap',
        confidence: 1,
        knowledge_gap: true,
        gap_reason: 'Missing approved product card for ' + (best.canonical_name || best.canonical_sku || 'product'),
        state_updates: [],
        knowledge_candidates: [],
        task_candidates: [],
        followup_questions: [],
        needs_human_review: false,
      },
      answer_text: 'По этому товару пока нет утвержденной короткой карточки. Записал это на доработку.',
      reply_text: 'По этому товару пока нет утвержденной короткой карточки. Записал это на доработку.',
      action: 'reply',
      answer_mode: 'direct_structured_gap',
      confidence: 1,
      knowledge_gap: true,
      needs_human_review: false,
      route: 'gap_true',
    },
  }];
}

const matchedBusinessObjection = findBusinessObjection(userText);
if (matchedBusinessObjection) {
  return businessObjectionReply(envelope, matchedBusinessObjection);
}

if (!best && hasDescriptionIntent(userText) && !isDeepKnowledgeIntent(userText)) {
  if (isDetailFollowupWithoutContext(userText)) {
    return [{
      json: {
        ...envelope,
        structured_hit: true,
        structured_source: 'postgres_cache',
        structured_match: null,
        dify_raw: '',
        dify_response: {
          answer_text: 'Могу рассказать подробнее, но мне нужен сам товар. Напишите название: например, активатор клеток, Вэнтун, Ба-Гуа, палантин.',
          action: 'reply',
          answer_mode: 'direct_structured_clarify',
          confidence: 1,
          knowledge_gap: false,
          gap_reason: null,
          state_updates: [],
          knowledge_candidates: [],
          task_candidates: [],
          followup_questions: [],
          needs_human_review: false,
        },
        answer_text: 'Могу рассказать подробнее, но мне нужен сам товар. Напишите название: например, активатор клеток, Вэнтун, Ба-Гуа, палантин.',
        reply_text: 'Могу рассказать подробнее, но мне нужен сам товар. Напишите название: например, активатор клеток, Вэнтун, Ба-Гуа, палантин.',
        action: 'reply',
        answer_mode: 'direct_structured_clarify',
        confidence: 1,
        knowledge_gap: false,
        needs_human_review: false,
        route: 'answer',
      },
    }];
  }
  return [{
    json: {
      ...envelope,
      structured_hit: true,
      structured_source: 'postgres_cache',
      structured_match: null,
      dify_raw: '',
      dify_response: {
        answer_text: 'Пока не поймал точный товар по этому запросу. Напишите название товара или его коротко: например, активатор, Вэнтун, очки, палантин, пептид.',
        action: 'reply',
        answer_mode: 'direct_structured_gap',
        confidence: 1,
        knowledge_gap: true,
        gap_reason: 'Unknown product request: ' + userText,
        state_updates: [],
        knowledge_candidates: [],
        task_candidates: [],
        followup_questions: [],
        needs_human_review: false,
      },
      answer_text: 'Пока не поймал точный товар по этому запросу. Напишите название товара или его коротко: например, активатор, Вэнтун, очки, палантин, пептид.',
      reply_text: 'Пока не поймал точный товар по этому запросу. Напишите название товара или его коротко: например, активатор, Вэнтун, очки, палантин, пептид.',
      action: 'reply',
      answer_mode: 'direct_structured_gap',
      confidence: 1,
      knowledge_gap: true,
      needs_human_review: false,
      route: 'gap_true',
    },
  }];
}

return [{
  json: {
    ...envelope,
    deep_requested: isDeepKnowledgeIntent(userText),
    structured_hit: false,
    structured_source: 'postgres_cache',
    structured_match: best ? {
      alias: best.alias,
      sku: best.canonical_sku,
      canonical_name: best.canonical_name,
    } : null,
  },
}];
