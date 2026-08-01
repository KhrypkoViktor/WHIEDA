const PROJECT_ID = 'whieda';

function mergeEnvelopeJson() {
  try {
    return $('Code: Merge Website API Session').first().json;
  } catch (error) {
    return $('Code: Merge Envelope + Session').first().json;
  }
}
const mergeEnvelope = mergeEnvelopeJson();
const envelope = { ...mergeEnvelope, ...$input.first().json };
const userText = String(envelope.message_text ?? '').trim();
const isFeedbackCommand = /^(fail|correct|style|missing|ошибка|не\s*так|неверно|исправь|исправить|дополни|добавь|уточни|стиль)[:\s-]/i.test(userText);
const isNaturalFeedback = envelope.is_feedback_candidate === true && envelope.feedback_input_mode === 'natural_reply';
if (isFeedbackCommand || isNaturalFeedback) {
  return [{ json: envelope }];
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

function isActive(value) {
  return String(value ?? '').toLowerCase() === 'true' || value === true;
}

function hasResourceIntent(text) {
  return /видео|ссылка|материал|подробнее|статья|инструкция|покажи|обзор|ютуб|youtube|pdf|фото|картин/i.test(text);
}

function wantsPhotoIntent(text) {
  return /фото|фотк|фотограф|картин|изображен/i.test(text);
}

function wantsVideoIntent(text) {
  return /видео|ютуб|youtube|обзор/i.test(text);
}

function wantsDocumentIntent(text) {
  return /pdf|презент|документ|инструкц|файл/i.test(text);
}

function isPhotoResource(row) {
  const type = normalize(row.resource_type);
  return type === 'image' || type === 'photo' || type === 'picture' || type === 'img';
}

function isVideoResource(row) {
  const type = normalize(row.resource_type);
  const blob = normalize([row.resource_type, row.title, row.url, row.topic].filter(Boolean).join(' '));
  return type === 'video' || blob.includes('youtube') || blob.includes('youtu be') || blob.includes('rutube') || blob.includes('video');
}

function isDocumentResource(row) {
  const type = normalize(row.resource_type);
  const blob = normalize([row.resource_type, row.title, row.url, row.topic].filter(Boolean).join(' '));
  return type === 'pdf' || type === 'document' || blob.includes('.pdf') || blob.includes('презентац') || blob.includes('инструкц');
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

function fallbackPhotoBySku(sku) {
  return null;
}

function buildPhotoCaption(row) {
  const raw = String(row.canonical_name || row.title || 'Фото').trim();
  return raw.length > 1024 ? raw.slice(0, 1021) + '...' : raw;
}

function hasProMarker(value) {
  return /\bpro\b|\bпро\b/i.test(String(value || ''));
}

function scoreResource(row, normalizedText) {
  const terms = [
    row.alias,
    row.canonical_name,
    row.topic,
    row.title,
  ].map(normalize).filter(Boolean);

  let score = Number(row.priority ?? 0);
  for (const term of terms) {
    if (!term) continue;
    if (normalizedText === term) {
      score += 200;
      continue;
    }
    if (normalizedText.startsWith(term + ' ') || normalizedText.endsWith(' ' + term)) {
      score += 120;
    }
    if (normalizedText.includes(term)) {
      score += Math.min(term.length, 80);
      continue;
    }

    const tokens = term.split(' ').filter((token) => token.length > 2);
    if (!tokens.length) continue;
    const matchedTokens = tokens.filter((token) => normalizedText.includes(token));
    if (matchedTokens.length >= 2) {
      score += matchedTokens.length * 25;
    }
  }

  const queryHasPro = hasProMarker(normalizedText);
  const rowHasPro = hasProMarker([row.alias, row.canonical_name, row.title, row.topic].filter(Boolean).join(' '));
  if (rowHasPro && !queryHasPro) score -= 120;
  if (!rowHasPro && queryHasPro) score -= 40;

  return score;
}

function parseContext(value) {
  if (!value) return {};
  if (typeof value === 'object') return value;
  try { return JSON.parse(value); } catch { return {}; }
}

const normalizedText = normalize(userText);
const conversationContext = parseContext($('Postgres: Load Conversation Context').first()?.json?.context);
const resources = $items('Postgres: Resource Links')
  .flatMap((item) => Array.isArray(item.json.resources) ? item.json.resources : [item.json])
  .filter((row) => String(row.project_id ?? row.client_id ?? '') === PROJECT_ID)
  .filter((row) => isActive(row.active))
  .filter((row) => row.url);

let matches = resources
  .map((row) => ({ row, score: scoreResource(row, normalizedText) }))
  .filter((entry) => entry.score > Number(entry.row.priority ?? 0))
  .sort((a, b) => b.score - a.score || Number(b.row.priority ?? 0) - Number(a.row.priority ?? 0))
  .slice(0, 3)
  .map((entry) => entry.row);

if (!matches.length && wantsPhotoIntent(userText) && conversationContext.last_product_sku) {
  matches = resources
    .filter((row) => String(row.sku ?? '') === String(conversationContext.last_product_sku ?? ''))
    .sort((a, b) => Number(b.priority ?? 0) - Number(a.priority ?? 0))
    .slice(0, 3);
}

const shouldAttachResources = hasResourceIntent(userText);
const photoIntent = wantsPhotoIntent(userText);
const videoIntent = wantsVideoIntent(userText);
const documentIntent = wantsDocumentIntent(userText);
const contextResourceIntent = photoIntent || videoIntent || documentIntent || /материал|обучен|инструкц|обзор|ссылк/i.test(normalizedText);

if (!matches.length && contextResourceIntent && conversationContext.last_product_sku) {
  matches = resources
    .filter((row) => String(row.sku ?? '') === String(conversationContext.last_product_sku ?? ''))
    .sort((a, b) => Number(b.priority ?? 0) - Number(a.priority ?? 0))
    .slice(0, 3);
}

const photoMatch = photoIntent ? matches.find((row) => isPhotoResource(row)) : null;
const fallbackPhoto = photoIntent
  ? fallbackPhotoBySku(conversationContext.last_product_sku || photoMatch?.sku || envelope.last_product_sku)
  : null;
const telegramPhoto = fallbackPhoto || (photoMatch ? {
  photo_url: toTelegramPhotoUrl(photoMatch.url),
  caption: null,
  resource_id: photoMatch.resource_id,
  title: photoMatch.title,
  canonical_name: photoMatch.canonical_name || photoMatch.title || 'Фото',
  sku: photoMatch.sku || null,
} : null);

if (telegramPhoto) {
  const answerText = 'Отправляю фото: ' + (telegramPhoto.canonical_name || telegramPhoto.title || 'Фото');
  return [{
    json: {
      ...envelope,
      structured_hit: true,
      structured_source: 'postgres_cache',
      structured_resources_hit: true,
      structured_resources: shouldAttachResources ? matches : [],
      structured_resources_context: matches.map((row) => ({
        title: row.title,
        url: row.url,
        resource_type: row.resource_type,
        canonical_name: row.canonical_name,
        topic: row.topic,
        audience: row.audience,
      })),
      structured_match: {
        alias: photoMatch?.alias || telegramPhoto.canonical_name,
        sku: telegramPhoto.sku || null,
        canonical_name: telegramPhoto.canonical_name || telegramPhoto.title || 'Фото',
      },
      telegram_photo_url: telegramPhoto.photo_url,
      telegram_photo_caption: telegramPhoto.caption,
      telegram_photo_resource_id: telegramPhoto.resource_id,
      telegram_media_mode: 'photo',
      dify_raw: '',
      dify_response: {
        answer_text: answerText,
        action: 'reply',
        answer_mode: 'direct_structured_photo',
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
      answer_mode: 'direct_structured_photo',
      confidence: 1,
      knowledge_gap: false,
      needs_human_review: false,
      route: 'answer',
    },
  }];
}

const linkedResource = videoIntent
  ? matches.find((row) => isVideoResource(row))
  : (documentIntent ? matches.find((row) => isDocumentResource(row)) : matches.find((row) => !isPhotoResource(row)));

if (linkedResource && shouldAttachResources) {
  const label = videoIntent ? 'видео' : (documentIntent ? 'материал' : 'ссылка');
  const title = String(linkedResource.title || linkedResource.canonical_name || 'материал').trim();
  const answerText = `Вот ${label} по товару: ${title}\n${String(linkedResource.url || '').trim()}`;
  return [{
    json: {
      ...envelope,
      structured_hit: true,
      structured_source: 'postgres_cache',
      structured_resources_hit: true,
      structured_resources: matches,
      structured_resources_context: matches.map((row) => ({
        title: row.title,
        url: row.url,
        resource_type: row.resource_type,
        canonical_name: row.canonical_name,
        topic: row.topic,
        audience: row.audience,
      })),
      structured_match: {
        alias: linkedResource.alias || linkedResource.canonical_name || title,
        sku: linkedResource.sku || null,
        canonical_name: linkedResource.canonical_name || title,
      },
      telegram_photo_url: null,
      telegram_photo_caption: null,
      telegram_photo_resource_id: null,
      telegram_media_mode: 'message',
      dify_raw: '',
      dify_response: {
        answer_text: answerText,
        action: 'reply',
        answer_mode: 'direct_structured_resource',
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
      answer_mode: 'direct_structured_resource',
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
    structured_resources_hit: matches.length > 0,
    structured_resources: shouldAttachResources ? matches : [],
    structured_resources_context: matches.map((row) => ({
      title: row.title,
      url: row.url,
      resource_type: row.resource_type,
      canonical_name: row.canonical_name,
      topic: row.topic,
      audience: row.audience,
    })),
    telegram_photo_url: telegramPhoto?.photo_url || null,
    telegram_photo_caption: telegramPhoto?.caption || null,
    telegram_photo_resource_id: telegramPhoto?.resource_id || null,
    telegram_media_mode: telegramPhoto ? 'photo' : 'message',
  },
}];
