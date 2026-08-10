const PROJECT_ID = 'whieda';
const SHEET_ID = '1Lm6ucw1oo0HQjvN2ZuxIGs2vK1lehw93jwqff7ldbz4';
const FALLBACK_UPDATED_AT = new Date().toISOString();
const GIDS = {
  products: '1035748906',
  aliases: '2001001',
  resources: '2001005',
  productCards: '2001006',
  productDetails: '2001008',
  usersAccess: '161104189',
  structureOwners: '86214234',
  events: '1444298798',
  communityResources: '947236678',
};

function exportUrl(gid) {
  return `https://docs.google.com/spreadsheets/d/${SHEET_ID}/export?format=tsv&gid=${gid}`;
}

function parseTsv(text) {
  const source = String(text || '').replace(/^\uFEFF/, '').replace(/\r/g, '');
  const rows = [];
  let row = [];
  let cell = '';
  let quoted = false;

  // Google exports line breaks inside a quoted TSV cell literally. Splitting by
  // newline loses both paragraphs and every column after the first break.
  for (let index = 0; index < source.length; index += 1) {
    const char = source[index];
    if (char === '"') {
      if (quoted && source[index + 1] === '"') {
        cell += '"';
        index += 1;
      } else {
        quoted = !quoted;
      }
    } else if (char === '\t' && !quoted) {
      row.push(cell);
      cell = '';
    } else if (char === '\n' && !quoted) {
      row.push(cell);
      if (row.some((value) => value !== '')) rows.push(row);
      row = [];
      cell = '';
    } else {
      cell += char;
    }
  }
  row.push(cell);
  if (row.some((value) => value !== '')) rows.push(row);
  if (!rows.length) return [];

  const headers = rows[0].map((value) => String(value || '').trim());
  return rows.slice(1).map((cols) => {
    const row = {};
    for (let i = 0; i < headers.length; i++) {
      row[headers[i]] = String(cols[i] ?? '').trim();
    }
    return row;
  }).filter((row) => Object.values(row).some((value) => String(value || '').trim() !== ''));
}

function pick(row, names) {
  for (const name of names) {
    if (Object.prototype.hasOwnProperty.call(row, name)) return row[name];
  }
  return '';
}

function asText(value) {
  const text = String(value ?? '').trim();
  return text === '' || text === '-' ? null : text;
}

function asInt(value, fallback = 0) {
  const text = String(value ?? '').trim().replace(',', '.');
  if (text === '') return fallback;
  const n = Number(text);
  return Number.isFinite(n) ? Math.trunc(n) : fallback;
}

function asBool(value, fallback = true) {
  const text = String(value ?? '').trim().toLowerCase();
  if (!text) return fallback;
  if (['true', '1', 'yes', 'y', 'да'].includes(text)) return true;
  if (['false', '0', 'no', 'n', 'нет'].includes(text)) return false;
  return fallback;
}

function sqlValue(value) {
  if (value === null || value === undefined) return 'NULL';
  if (typeof value === 'boolean') return value ? 'TRUE' : 'FALSE';
  if (typeof value === 'number') return Number.isFinite(value) ? String(value) : 'NULL';
  // n8n/Postgres treats a raw dollar sign in an interpolated query as a parameter marker.
  // Rebuild it in SQL so texts such as W$ stay intact after the sync.
  return "'" + String(value).replace(/'/g, "''").replace(/\$/g, "' || chr(36) || '") + "'";
}

function valuesSql(rows, columns) {
  if (!rows.length) return null;
  return rows.map((row) => `(${columns.map((column) => sqlValue(row[column])).join(', ')})`).join(',\n');
}

function makeSyntheticId(parts) {
  const raw = parts
    .map((part) => String(part ?? '').trim().toLowerCase())
    .filter(Boolean)
    .join('|');
  if (!raw) return null;
  return raw
    .replace(/https?:\/\//g, '')
    .replace(/[^a-z0-9а-яё|_-]+/gi, '-')
    .replace(/-+/g, '-')
    .replace(/\|+/g, '|')
    .replace(/^[-|]+|[-|]+$/g, '')
    .slice(0, 180);
}

function dedupeByKeys(rows, keyColumns) {
  const map = new Map();
  for (const row of rows) {
    const key = keyColumns.map((column) => String(row[column] ?? '')).join('||');
    if (!key || key.includes('||null') || key.startsWith('null')) continue;
    map.set(key, row);
  }
  return Array.from(map.values());
}

function bodyFromNode(nodeName) {
  const item = $items(nodeName)?.[0]?.json;
  if (item == null) return '';
  if (typeof item === 'string') return item;
  if (typeof item.body === 'string') return item.body;
  if (typeof item.data === 'string') return item.data;
  if (typeof item.response === 'string') return item.response;
  if (typeof item.text === 'string') return item.text;
  if (typeof item.body === 'object' && item.body !== null) return JSON.stringify(item.body);
  return JSON.stringify(item);
}

const productsRaw = bodyFromNode('HTTP: Products TSV');
const aliasesRaw = bodyFromNode('HTTP: Aliases TSV');
const resourcesRaw = bodyFromNode('HTTP: Resources TSV');
const productCardsRaw = bodyFromNode('HTTP: Product Cards TSV');
const productDetailsRaw = bodyFromNode('HTTP: Product Details TSV');
const productComparisonsRaw = bodyFromNode('HTTP: Product Comparisons TSV');
const usersAccessRaw = bodyFromNode('HTTP: Users Access TSV');
const structureOwnersRaw = bodyFromNode('HTTP: Structure Owners TSV');
const businessObjectionsRaw = bodyFromNode('HTTP: Business Objections TSV');
const businessFaqRaw = bodyFromNode('HTTP: Business FAQ TSV');
const promotionsRaw = bodyFromNode('HTTP: Promotions TSV');
const recommendationRulesRaw = bodyFromNode('HTTP: Product Recommendation Rules TSV');
const starterBasketTemplatesRaw = bodyFromNode('HTTP: Starter Basket Templates TSV');
const intentRegistryRaw = bodyFromNode('HTTP: Intent Registry TSV');
const clarificationPromptsRaw = bodyFromNode('HTTP: Clarification Prompts TSV');
const capabilityResponsesRaw = bodyFromNode('HTTP: Capability Responses TSV');
const canonicalQuestionsRaw = bodyFromNode('HTTP: Canonical Questions TSV');
const eventsRaw = bodyFromNode('HTTP: Events TSV');
const communityResourcesRaw = bodyFromNode('HTTP: Community Resources TSV');
const partnersRefRaw = bodyFromNode('HTTP: Partners Ref TSV');

const products = parseTsv(productsRaw).map((row) => ({
  client_id: PROJECT_ID,
  sku: asText(pick(row, ['sku', 'SKU'])),
  canonical_name: asText(pick(row, ['canonical_name', 'name', 'Название'])),
  category: asText(pick(row, ['category', 'Категория'])),
  retail_price_rub: asText(pick(row, ['retail_price_rub', 'retail_price', 'price_rub', 'розничная цена', 'розничная_цена'])),
  retail_w: asText(pick(row, ['retail_w', 'retail_ws', 'retail_w$', 'w$', 'W$ retail'])),
  retail_price_byn: asText(pick(row, ['retail_price_byn', 'розничная цена byn', 'retail_byn'])),
  partner_price_rub: asText(pick(row, ['partner_price_rub', 'partner_price', 'партнерская цена', 'partner_rub'])),
  partner_w: asText(pick(row, ['partner_w', 'partner_ws', 'partner_w$', 'W$ partner'])),
  partner_price_byn: asText(pick(row, ['partner_price_byn', 'партнерская цена byn', 'partner_byn'])),
  partner_points: asText(pick(row, ['partner_points', 'pv', 'PV'])),
})).filter((row) => row.sku && row.canonical_name);

const aliases = parseTsv(aliasesRaw).map((row) => ({
  client_id: PROJECT_ID,
  alias: asText(pick(row, ['alias', 'алиас'])),
  canonical_sku: asText(pick(row, ['canonical_sku', 'sku', 'canonical sku'])),
  canonical_name: asText(pick(row, ['canonical_name', 'canonical name', 'name'])),
  match_type: asText(pick(row, ['match_type', 'match type'])) || 'contains',
  priority: asInt(pick(row, ['priority']), 100),
  active: asBool(pick(row, ['active']), true),
  answer_scope: asText(pick(row, ['answer_scope', 'answer scope'])),
  notes: asText(pick(row, ['notes', 'comment'])),
  updated_at: asText(pick(row, ['updated_at', 'updated at'])),
})).filter((row) => row.alias && row.canonical_sku);

const resources = parseTsv(resourcesRaw).map((row) => ({
  client_id: PROJECT_ID,
  resource_id: asText(pick(row, ['resource_id', 'resource id'])) || makeSyntheticId([
    pick(row, ['sku']),
    pick(row, ['resource_type', 'resource type', 'type']),
    pick(row, ['title']),
    pick(row, ['url', 'link']),
  ]),
  sku: asText(pick(row, ['sku'])),
  canonical_name: asText(pick(row, ['canonical_name', 'canonical name', 'name'])),
  alias: asText(pick(row, ['alias'])),
  topic: asText(pick(row, ['topic'])),
  resource_type: asText(pick(row, ['resource_type', 'resource type', 'type'])),
  title: asText(pick(row, ['title'])),
  url: asText(pick(row, ['url', 'link'])),
  source_owner: asText(pick(row, ['source_owner', 'source owner'])),
  language: asText(pick(row, ['language', 'lang'])),
  priority: asInt(pick(row, ['priority']), 100),
  active: asBool(pick(row, ['active']), true),
  audience: asText(pick(row, ['audience'])),
  notes: asText(pick(row, ['notes', 'comment'])),
  updated_at: asText(pick(row, ['updated_at', 'updated at'])),
})).filter((row) => row.url && (row.sku || row.canonical_name || row.alias));

const productCards = parseTsv(productCardsRaw).map((row) => ({
  client_id: PROJECT_ID,
  sku: asText(pick(row, ['sku'])),
  canonical_name: asText(pick(row, ['canonical_name', 'canonical name', 'name'])),
  short_name: asText(pick(row, ['short_name', 'short name'])),
  category: asText(pick(row, ['category'])),
  what_it_is: asText(pick(row, ['what_it_is', 'what it is'])),
  who_asks_about_it: asText(pick(row, ['who_asks_about_it', 'who asks about it'])),
  common_use_cases: asText(pick(row, ['common_use_cases', 'common use cases'])),
  how_to_use_short: asText(pick(row, ['how_to_use_short', 'how to use short'])),
  what_to_expect_soft: asText(pick(row, ['what_to_expect_soft', 'what to expect soft'])),
  contraindications_short: asText(pick(row, ['contraindications_short', 'contraindications short'])),
  do_not_claim: asText(pick(row, ['do_not_claim', 'do not claim'])),
  when_to_escalate: asText(pick(row, ['when_to_escalate', 'when to escalate'])),
  price_answer_mode: asText(pick(row, ['price_answer_mode', 'price answer mode'])),
  primary_image_url: asText(pick(row, ['primary_image_url', 'primary image url'])),
  source_status: asText(pick(row, ['source_status', 'source status'])),
  notes: asText(pick(row, ['notes', 'comment'])),
  source_updated_at: asText(pick(row, ['source_updated_at', 'source updated at'])) || FALLBACK_UPDATED_AT,
})).filter((row) => row.sku && row.canonical_name);

const productDetails = parseTsv(productDetailsRaw).map((row) => ({
  client_id: PROJECT_ID,
  detail_id: asText(pick(row, ['detail_id', 'detail id'])) || makeSyntheticId([
    pick(row, ['sku']),
    pick(row, ['topic']),
    pick(row, ['title']),
  ]),
  sku: asText(pick(row, ['sku'])),
  topic: asText(pick(row, ['topic'])),
  title: asText(pick(row, ['title'])),
  answer_text: asText(pick(row, ['answer_text', 'answer text'])),
  source_id: asText(pick(row, ['source_id', 'source id'])),
  source_url: asText(pick(row, ['source_url', 'source url'])),
  source_locator: asText(pick(row, ['source_locator', 'source locator'])),
  evidence_type: asText(pick(row, ['evidence_type', 'evidence type'])),
  review_status: asText(pick(row, ['review_status', 'review status'])) || 'draft',
  audience: asText(pick(row, ['audience'])) || 'partner',
  active: asBool(pick(row, ['active']), true),
  priority: asInt(pick(row, ['priority']), 100),
  owner_state: asText(pick(row, ['owner_state', 'owner state'])) || 'PROPOSED_CHANGE',
  content_hash: asText(pick(row, ['content_hash', 'content hash'])),
  updated_at: asText(pick(row, ['updated_at', 'updated at'])) || FALLBACK_UPDATED_AT,
  notes: asText(pick(row, ['notes', 'comment'])),
})).filter((row) => row.detail_id && row.sku && row.topic && row.answer_text);

const productComparisons = parseTsv(productComparisonsRaw).map((row) => ({
  client_id: PROJECT_ID,
  comparison_id: asText(pick(row, ['comparison_id', 'comparison id'])) || makeSyntheticId([
    pick(row, ['left_sku', 'left sku']),
    pick(row, ['right_sku', 'right sku']),
    pick(row, ['title']),
  ]),
  left_sku: asText(pick(row, ['left_sku', 'left sku'])),
  right_sku: asText(pick(row, ['right_sku', 'right sku'])),
  title: asText(pick(row, ['title'])),
  answer_text: asText(pick(row, ['answer_text', 'answer text'])),
  why_choose_left: asText(pick(row, ['why_choose_left', 'why choose left'])),
  why_choose_right: asText(pick(row, ['why_choose_right', 'why choose right'])),
  important_note: asText(pick(row, ['important_note', 'important note'])),
  source_id: asText(pick(row, ['source_id', 'source id'])),
  source_status: asText(pick(row, ['source_status', 'source status'])) || 'draft',
  review_status: asText(pick(row, ['review_status', 'review status'])) || 'draft',
  active: asBool(pick(row, ['active']), true),
  priority: asInt(pick(row, ['priority']), 100),
  updated_at: asText(pick(row, ['updated_at', 'updated at'])) || FALLBACK_UPDATED_AT,
  notes: asText(pick(row, ['notes', 'comment'])),
})).filter((row) => row.comparison_id && row.left_sku && row.right_sku && row.answer_text);

const usersAccess = parseTsv(usersAccessRaw).map((row) => ({
  client_id: PROJECT_ID,
  telegram_user_id: asText(pick(row, ['telegram_user_id'])),
  display_name: asText(pick(row, ['display_name'])),
  username: asText(pick(row, ['username'])),
  role: asText(pick(row, ['role'])) || 'partner',
  access_status: asText(pick(row, ['access_status'])) || 'candidate',
  subscription_status: asText(pick(row, ['subscription_status'])) || 'off',
  last_seen_at: asText(pick(row, ['last_seen_at'])),
  notes: asText(pick(row, ['notes'])),
  updated_at: asText(pick(row, ['updated_at'])) || FALLBACK_UPDATED_AT,
  structure_code: asText(pick(row, ['structure_code'])) || 'general',
  structure_owner: asText(pick(row, ['structure_owner'])) || 'Виктор Хрипко',
  alert_recipient: asText(pick(row, ['alert_recipient'])) || 'Виктор Хрипко',
  requested_structure_code: asText(pick(row, ['requested_structure_code'])) || 'general',
  ownership_status: asText(pick(row, ['ownership_status'])) || 'candidate',
  assigned_by: asText(pick(row, ['assigned_by'])),
  ownership_updated_at: asText(pick(row, ['ownership_updated_at'])),
})).filter((row) => row.telegram_user_id);

const structureOwners = parseTsv(structureOwnersRaw).map((row) => ({
  client_id: PROJECT_ID,
  structure_code: asText(pick(row, ['structure_code'])),
  leader_name: asText(pick(row, ['leader_name'])),
  leader_username: asText(pick(row, ['leader_username'])),
  leader_telegram_user_id: asText(pick(row, ['leader_telegram_user_id'])),
  default_alert_recipient: asText(pick(row, ['default_alert_recipient'])),
  invite_link: asText(pick(row, ['invite_link'])),
  status: asText(pick(row, ['status'])) || 'prepared_not_routed',
  parent_structure_code: asText(pick(row, ['parent_structure_code'])),
  owner_status: asText(pick(row, ['owner_status'])) || 'prepared',
})).filter((row) => row.structure_code);

const businessObjections = parseTsv(businessObjectionsRaw).map((row) => ({
  client_id: PROJECT_ID,
  objection_id: asText(pick(row, ['objection_id', 'objection id'])),
  title: asText(pick(row, ['title'])),
  aliases: asText(pick(row, ['aliases'])),
  first_reply: asText(pick(row, ['first_reply', 'first reply'])),
  clarify: asText(pick(row, ['clarify'])),
  confirmed_answer_rule: asText(pick(row, ['confirmed_answer_rule', 'confirmed answer rule'])),
  next_step: asText(pick(row, ['next_step', 'next step'])),
  do_not_say: asText(pick(row, ['do_not_say', 'do not say'])),
  source_id: asText(pick(row, ['source_id', 'source id'])),
  review_status: asText(pick(row, ['review_status', 'review status'])) || 'draft',
  active: asBool(pick(row, ['active']), false),
  priority: asInt(pick(row, ['priority']), 100),
  owner_state: asText(pick(row, ['owner_state', 'owner state'])) || 'PROPOSED_CHANGE',
  updated_at: asText(pick(row, ['updated_at', 'updated at'])) || FALLBACK_UPDATED_AT,
  notes: asText(pick(row, ['notes', 'comment'])),
})).filter((row) => row.objection_id && row.title && row.first_reply);

const businessFaq = parseTsv(businessFaqRaw).map((row) => ({
  client_id: PROJECT_ID,
  faq_id: asText(pick(row, ['faq_id', 'faq id'])),
  title: asText(pick(row, ['title'])),
  aliases: asText(pick(row, ['aliases'])),
  answer_text: asText(pick(row, ['answer_text', 'answer text'])),
  do_not_say: asText(pick(row, ['do_not_say', 'do not say'])),
  source_id: asText(pick(row, ['source_id', 'source id'])),
  review_status: asText(pick(row, ['review_status', 'review status'])) || 'draft',
  active: asBool(pick(row, ['active']), false),
  priority: asInt(pick(row, ['priority']), 100),
  owner_state: asText(pick(row, ['owner_state', 'owner state'])) || 'PROPOSED_CHANGE',
  updated_at: asText(pick(row, ['updated_at', 'updated at'])) || FALLBACK_UPDATED_AT,
  notes: asText(pick(row, ['notes', 'comment'])),
})).filter((row) => row.faq_id && row.title && row.answer_text);

const promotions = parseTsv(promotionsRaw).map((row) => ({
  client_id: PROJECT_ID,
  promotion_id: asText(pick(row, ['promotion_id', 'promotion id'])),
  tenant_id: asText(pick(row, ['tenant_id', 'tenant id'])) || 'by',
  title: asText(pick(row, ['title', 'название'])),
  short_text: asText(pick(row, ['short_text', 'short text', 'кратко'])),
  full_text: asText(pick(row, ['full_text', 'full text', 'полное описание'])),
  country: asText(pick(row, ['country', 'страна'])) || 'Belarus',
  city: asText(pick(row, ['city', 'город'])),
  starts_at: asText(pick(row, ['starts_at', 'starts at', 'действует с'])),
  ends_at: asText(pick(row, ['ends_at', 'ends at', 'действует до'])),
  timezone: asText(pick(row, ['timezone', 'часовой пояс'])) || 'Europe/Minsk',
  promotion_type: asText(pick(row, ['promotion_type', 'promotion type', 'тип акции'])),
  product_ids: asText(pick(row, ['product_ids', 'product ids', 'товары'])),
  min_amount: asText(pick(row, ['min_amount', 'min amount', 'минимальная сумма'])),
  min_pv: asText(pick(row, ['min_pv', 'min pv', 'минимальный pv'])),
  benefit_text: asText(pick(row, ['benefit_text', 'benefit text', 'выгода'])),
  source_url: asText(pick(row, ['source_url', 'source url', 'источник'])),
  image_url: asText(pick(row, ['image_url', 'image url', 'изображение'])),
  priority: asInt(pick(row, ['priority']), 100),
  status: asText(pick(row, ['status', 'статус'])) || 'draft',
  owner: asText(pick(row, ['owner', 'ответственный'])),
  updated_at: asText(pick(row, ['updated_at', 'updated at'])),
})).filter((row) => row.promotion_id && row.title && row.starts_at && row.ends_at);

const recommendationRules = parseTsv(recommendationRulesRaw).map((row) => ({
  client_id: PROJECT_ID,
  product_id: asText(pick(row, ['product_id', 'product id', 'sku'])),
  tenant_id: asText(pick(row, ['tenant_id', 'tenant id'])) || 'by',
  registration_enabled: asBool(pick(row, ['registration_enabled', 'registration enabled']), true),
  availability_status: asText(pick(row, ['availability_status', 'availability status'])) || 'available',
  universality_score: asInt(pick(row, ['universality_score', 'universality score']), 0),
  popularity_score: asInt(pick(row, ['popularity_score', 'popularity score']), 0),
  demo_score: asInt(pick(row, ['demo_score', 'demo score']), 0),
  gift_score: asInt(pick(row, ['gift_score', 'gift score']), 0),
  resale_score: asInt(pick(row, ['resale_score', 'resale score']), 0),
  personal_use_score: asInt(pick(row, ['personal_use_score', 'personal use score']), 0),
  explanation_difficulty: asInt(pick(row, ['explanation_difficulty', 'explanation difficulty']), 0),
  price_sensitivity: asInt(pick(row, ['price_sensitivity', 'price sensitivity']), 0),
  business_priority: asInt(pick(row, ['business_priority', 'business priority']), 0),
  goal_tags: asText(pick(row, ['goal_tags', 'goal tags'])),
  audience_tags: asText(pick(row, ['audience_tags', 'audience tags'])),
  reason_short: asText(pick(row, ['reason_short', 'reason short'])),
  avoid_when: asText(pick(row, ['avoid_when', 'avoid when'])),
  status: asText(pick(row, ['status'])) || 'draft',
  owner: asText(pick(row, ['owner'])),
  updated_at: asText(pick(row, ['updated_at', 'updated at'])),
})).filter((row) => row.product_id);

const starterBasketTemplates = parseTsv(starterBasketTemplatesRaw).map((row) => ({
  client_id: PROJECT_ID,
  template_id: asText(pick(row, ['template_id', 'template id'])),
  tenant_id: asText(pick(row, ['tenant_id', 'tenant id'])) || 'by',
  title: asText(pick(row, ['title', 'название'])),
  goal: asText(pick(row, ['goal', 'цель'])) || 'balanced',
  budget_min: asText(pick(row, ['budget_min', 'budget min'])),
  budget_max: asText(pick(row, ['budget_max', 'budget max'])),
  target_pv_min: asText(pick(row, ['target_pv_min', 'target pv min'])),
  target_pv_max: asText(pick(row, ['target_pv_max', 'target pv max'])),
  required_product_ids: asText(pick(row, ['required_product_ids', 'required product ids'])),
  preferred_product_ids: asText(pick(row, ['preferred_product_ids', 'preferred product ids'])),
  excluded_product_ids: asText(pick(row, ['excluded_product_ids', 'excluded product ids'])),
  description: asText(pick(row, ['description', 'описание'])),
  priority: asInt(pick(row, ['priority']), 100),
  status: asText(pick(row, ['status'])) || 'draft',
  owner: asText(pick(row, ['owner'])),
  updated_at: asText(pick(row, ['updated_at', 'updated at'])),
})).filter((row) => row.template_id && row.title);

const intentRegistry = parseTsv(intentRegistryRaw).map((row) => ({
  client_id: PROJECT_ID,
  intent_id: asText(pick(row, ['intent_id', 'intent id'])),
  domain: asText(pick(row, ['domain'])),
  // n8n's SQL expression field treats a trailing "$" in this service registry
  // inconsistently. It is only a label/examples layer, so normalize it here.
  name_ru: asText(pick(row, ['name_ru', 'name ru']))?.replace(/\$/g, ''),
  examples: asText(pick(row, ['examples']))?.replace(/\$/g, ''),
  required_slots: asText(pick(row, ['required_slots', 'required slots'])),
  route: asText(pick(row, ['route'])),
  source_table: asText(pick(row, ['source_table', 'source table'])),
  clarification_key: asText(pick(row, ['clarification_key', 'clarification key'])),
  priority: asInt(pick(row, ['priority']), 100),
  enabled: asBool(pick(row, ['enabled']), true),
  owner_state: asText(pick(row, ['owner_state', 'owner state'])) || 'PROPOSED_CHANGE',
  updated_at: asText(pick(row, ['updated_at', 'updated at'])) || FALLBACK_UPDATED_AT,
})).filter((row) => row.intent_id && row.route);

const clarificationPrompts = parseTsv(clarificationPromptsRaw).map((row) => ({
  client_id: PROJECT_ID,
  clarification_key: asText(pick(row, ['clarification_key', 'clarification key'])),
  reason: asText(pick(row, ['reason'])),
  prompt_text: asText(pick(row, ['prompt_text', 'prompt text'])),
  button_options: asText(pick(row, ['button_options', 'button options'])),
  max_attempts: asInt(pick(row, ['max_attempts', 'max attempts']), 2),
  fallback_menu: asText(pick(row, ['fallback_menu', 'fallback menu'])),
  enabled: asBool(pick(row, ['enabled']), true),
  owner_state: asText(pick(row, ['owner_state', 'owner state'])) || 'PROPOSED_CHANGE',
  updated_at: asText(pick(row, ['updated_at', 'updated at'])) || FALLBACK_UPDATED_AT,
})).filter((row) => row.clarification_key && row.prompt_text);

const capabilityResponses = parseTsv(capabilityResponsesRaw).map((row) => ({
  client_id: PROJECT_ID,
  response_id: asText(pick(row, ['response_id', 'response id'])),
  intent_id: asText(pick(row, ['intent_id', 'intent id'])),
  answer_text: asText(pick(row, ['answer_text', 'answer text'])),
  enabled: asBool(pick(row, ['enabled']), true),
  owner_state: asText(pick(row, ['owner_state', 'owner state'])) || 'PROPOSED_CHANGE',
  updated_at: asText(pick(row, ['updated_at', 'updated at'])) || FALLBACK_UPDATED_AT,
  notes: asText(pick(row, ['notes', 'comment'])),
})).filter((row) => row.response_id && row.intent_id && row.answer_text);

const canonicalQuestions = parseTsv(canonicalQuestionsRaw).map((row) => ({
  client_id: PROJECT_ID,
  question_id: asText(pick(row, ['question_id', 'question id'])),
  intent_id: asText(pick(row, ['intent_id', 'intent id'])),
  entity_id: asText(pick(row, ['entity_id', 'entity id'])),
  canonical_question: asText(pick(row, ['canonical_question', 'canonical question'])),
  real_examples: asText(pick(row, ['real_examples', 'real examples'])),
  answer_key: asText(pick(row, ['answer_key', 'answer key'])),
  frequency: asInt(pick(row, ['frequency']), 0),
  source_id: asText(pick(row, ['source_id', 'source id'])),
  status: asText(pick(row, ['status'])) || 'candidate',
  owner_state: asText(pick(row, ['owner_state', 'owner state'])) || 'PROPOSED_CHANGE',
  updated_at: asText(pick(row, ['updated_at', 'updated at'])) || FALLBACK_UPDATED_AT,
})).filter((row) => row.question_id && row.canonical_question);

const events = parseTsv(eventsRaw).map((row) => ({
  client_id: PROJECT_ID,
  event_id: asText(pick(row, ['event_id', 'event id'])),
  tenant_id: asText(pick(row, ['tenant_id', 'tenant id'])) || 'by',
  title: asText(pick(row, ['title', 'название'])),
  event_type: asText(pick(row, ['event_type', 'event type', 'тип'])),
  description: asText(pick(row, ['description', 'описание'])),
  starts_at: asText(pick(row, ['starts_at', 'starts at', 'начало'])),
  ends_at: asText(pick(row, ['ends_at', 'ends at', 'конец'])),
  timezone: asText(pick(row, ['timezone', 'часовой пояс'])) || 'Europe/Minsk',
  country: asText(pick(row, ['country', 'страна'])) || 'Беларусь',
  city: asText(pick(row, ['city', 'город'])),
  address: asText(pick(row, ['address', 'адрес'])),
  online_url: asText(pick(row, ['online_url', 'online url', 'ссылка'])),
  contact: asText(pick(row, ['contact', 'контакт', 'спикер'])),
  audience_segment: asText(pick(row, ['audience_segment', 'audience segment', 'аудитория'])),
  leader_id: asText(pick(row, ['leader_id', 'leader id'])),
  image_url: asText(pick(row, ['image_url', 'image url'])),
  source_url: asText(pick(row, ['source_url', 'source url'])),
  reminder_offsets: asText(pick(row, ['reminder_offsets', 'reminder offsets'])),
  status: asText(pick(row, ['status', 'статус'])) || 'draft',
  owner: asText(pick(row, ['owner', 'ответственный'])),
  updated_at: asText(pick(row, ['updated_at', 'updated at'])) || FALLBACK_UPDATED_AT,
  recurrence_rule: asText(pick(row, ['recurrence_rule', 'recurrence rule', 'повторение'])),
})).filter((row) => row.event_id && row.title && row.starts_at);

const communityResources = parseTsv(communityResourcesRaw).map((row) => ({
  client_id: PROJECT_ID,
  resource_id: asText(pick(row, ['resource_id', 'resource id'])),
  tenant_id: asText(pick(row, ['tenant_id', 'tenant id'])) || 'by',
  leader_id: asText(pick(row, ['leader_id', 'leader id'])),
  title: asText(pick(row, ['title', 'название'])),
  category: asText(pick(row, ['category', 'категория'])),
  description: asText(pick(row, ['description', 'описание'])),
  url: asText(pick(row, ['url', 'link', 'ссылка'])),
  platform: asText(pick(row, ['platform', 'платформа'])),
  country: asText(pick(row, ['country', 'страна'])),
  city: asText(pick(row, ['city', 'город'])),
  audience: asText(pick(row, ['audience', 'аудитория'])),
  topic_tags: asText(pick(row, ['topic_tags', 'topic tags', 'темы'])),
  access_level: asText(pick(row, ['access_level', 'access level'])) || 'public',
  priority: asInt(pick(row, ['priority']), 100),
  is_official: asBool(pick(row, ['is_official', 'is official']), false),
  status: asText(pick(row, ['status', 'статус'])) || 'draft',
  last_checked_at: asText(pick(row, ['last_checked_at', 'last checked at'])),
  owner: asText(pick(row, ['owner', 'ответственный'])),
  updated_at: asText(pick(row, ['updated_at', 'updated at'])) || FALLBACK_UPDATED_AT,
  notes: asText(pick(row, ['notes', 'comment', 'примечания'])),
})).filter((row) => row.resource_id && row.title && row.url);

const productsDeduped = dedupeByKeys(products, ['client_id', 'sku']);
const aliasesDeduped = dedupeByKeys(aliases, ['client_id', 'alias', 'canonical_sku']);
const resourcesDeduped = dedupeByKeys(resources, ['client_id', 'resource_id']);
const productCardsDeduped = dedupeByKeys(productCards, ['client_id', 'sku']);

// P0 circuit breaker: these master layers must never be replaced by a blank
// or obviously truncated Google TSV export. Throwing here happens before any
// Postgres node is reached, leaving the last known-good runtime cache intact.
const criticalLayerMinimums = [
  ['Products', productsDeduped.length, 20],
  ['Product aliases', aliasesDeduped.length, 60],
  ['Resource links', resourcesDeduped.length, 25],
  ['Product cards', productCardsDeduped.length, 10],
];
for (const [layerName, actualRows, minimumRows] of criticalLayerMinimums) {
  if (actualRows < minimumRows) {
    throw new Error(`Structured sync aborted before runtime writes: ${layerName} has ${actualRows} rows, minimum is ${minimumRows}. Keep last-known-good cache and inspect the Google Sheet export.`);
  }
}

const productDetailsDeduped = dedupeByKeys(productDetails, ['client_id', 'detail_id']);
const productComparisonsDeduped = dedupeByKeys(productComparisons, ['client_id', 'comparison_id']);
const usersAccessDeduped = dedupeByKeys(usersAccess, ['client_id', 'telegram_user_id']);
const structureOwnersDeduped = dedupeByKeys(structureOwners, ['client_id', 'structure_code']);
const businessObjectionsDeduped = dedupeByKeys(businessObjections, ['client_id', 'objection_id']);
const businessFaqDeduped = dedupeByKeys(businessFaq, ['client_id', 'faq_id']);
const promotionsDeduped = dedupeByKeys(promotions, ['client_id', 'promotion_id']);
const recommendationRulesDeduped = dedupeByKeys(recommendationRules, ['client_id', 'product_id']);
const starterBasketTemplatesDeduped = dedupeByKeys(starterBasketTemplates, ['client_id', 'template_id']);
const intentRegistryDeduped = dedupeByKeys(intentRegistry, ['client_id', 'intent_id']);
const clarificationPromptsDeduped = dedupeByKeys(clarificationPrompts, ['client_id', 'clarification_key']);
const capabilityResponsesDeduped = dedupeByKeys(capabilityResponses, ['client_id', 'response_id']);
const canonicalQuestionsDeduped = dedupeByKeys(canonicalQuestions, ['client_id', 'question_id']);
const eventsDeduped = dedupeByKeys(events, ['client_id', 'event_id']);
const communityResourcesDeduped = dedupeByKeys(communityResources, ['client_id', 'resource_id']);

const productsColumns = ['client_id', 'sku', 'canonical_name', 'category', 'retail_price_rub', 'retail_w', 'retail_price_byn', 'partner_price_rub', 'partner_w', 'partner_price_byn', 'partner_points'];
const aliasesColumns = ['client_id', 'alias', 'canonical_sku', 'canonical_name', 'match_type', 'priority', 'active', 'answer_scope', 'notes', 'updated_at'];
const resourcesColumns = ['client_id', 'resource_id', 'sku', 'canonical_name', 'alias', 'topic', 'resource_type', 'title', 'url', 'source_owner', 'language', 'priority', 'active', 'audience', 'notes', 'updated_at'];
const productCardsColumns = ['client_id', 'sku', 'canonical_name', 'short_name', 'category', 'what_it_is', 'who_asks_about_it', 'common_use_cases', 'how_to_use_short', 'what_to_expect_soft', 'contraindications_short', 'do_not_claim', 'when_to_escalate', 'price_answer_mode', 'primary_image_url', 'source_status', 'notes', 'source_updated_at'];
const productDetailsColumns = ['client_id', 'detail_id', 'sku', 'topic', 'title', 'answer_text', 'source_id', 'source_url', 'source_locator', 'evidence_type', 'review_status', 'audience', 'active', 'priority', 'owner_state', 'content_hash', 'updated_at', 'notes'];
const productComparisonsColumns = ['client_id', 'comparison_id', 'left_sku', 'right_sku', 'title', 'answer_text', 'why_choose_left', 'why_choose_right', 'important_note', 'source_id', 'source_status', 'review_status', 'active', 'priority', 'updated_at', 'notes'];
const usersAccessColumns = ['client_id', 'telegram_user_id', 'display_name', 'username', 'role', 'access_status', 'subscription_status', 'last_seen_at', 'notes', 'updated_at', 'structure_code', 'structure_owner', 'alert_recipient', 'requested_structure_code', 'ownership_status', 'assigned_by', 'ownership_updated_at'];
const structureOwnersColumns = ['client_id', 'structure_code', 'leader_name', 'leader_username', 'leader_telegram_user_id', 'default_alert_recipient', 'invite_link', 'status', 'parent_structure_code', 'owner_status'];
const businessObjectionsColumns = ['client_id', 'objection_id', 'title', 'aliases', 'first_reply', 'clarify', 'confirmed_answer_rule', 'next_step', 'do_not_say', 'source_id', 'review_status', 'active', 'priority', 'owner_state', 'updated_at', 'notes'];
const businessFaqColumns = ['client_id', 'faq_id', 'title', 'aliases', 'answer_text', 'do_not_say', 'source_id', 'review_status', 'active', 'priority', 'owner_state', 'updated_at', 'notes'];
const promotionsColumns = ['client_id', 'promotion_id', 'tenant_id', 'title', 'short_text', 'full_text', 'country', 'city', 'starts_at', 'ends_at', 'timezone', 'promotion_type', 'product_ids', 'min_amount', 'min_pv', 'benefit_text', 'source_url', 'image_url', 'priority', 'status', 'owner', 'updated_at'];
const recommendationRulesColumns = ['client_id', 'product_id', 'tenant_id', 'registration_enabled', 'availability_status', 'universality_score', 'popularity_score', 'demo_score', 'gift_score', 'resale_score', 'personal_use_score', 'explanation_difficulty', 'price_sensitivity', 'business_priority', 'goal_tags', 'audience_tags', 'reason_short', 'avoid_when', 'status', 'owner', 'updated_at'];
const starterBasketTemplatesColumns = ['client_id', 'template_id', 'tenant_id', 'title', 'goal', 'budget_min', 'budget_max', 'target_pv_min', 'target_pv_max', 'required_product_ids', 'preferred_product_ids', 'excluded_product_ids', 'description', 'priority', 'status', 'owner', 'updated_at'];
const intentRegistryColumns = ['client_id', 'intent_id', 'domain', 'name_ru', 'examples', 'required_slots', 'route', 'source_table', 'clarification_key', 'priority', 'enabled', 'owner_state', 'updated_at'];
const clarificationPromptsColumns = ['client_id', 'clarification_key', 'reason', 'prompt_text', 'button_options', 'max_attempts', 'fallback_menu', 'enabled', 'owner_state', 'updated_at'];
const capabilityResponsesColumns = ['client_id', 'response_id', 'intent_id', 'answer_text', 'enabled', 'owner_state', 'updated_at', 'notes'];
const canonicalQuestionsColumns = ['client_id', 'question_id', 'intent_id', 'entity_id', 'canonical_question', 'real_examples', 'answer_key', 'frequency', 'source_id', 'status', 'owner_state', 'updated_at'];
const eventsColumns = ['client_id', 'event_id', 'tenant_id', 'title', 'event_type', 'description', 'starts_at', 'ends_at', 'timezone', 'country', 'city', 'address', 'online_url', 'contact', 'audience_segment', 'leader_id', 'image_url', 'source_url', 'reminder_offsets', 'status', 'owner', 'updated_at', 'recurrence_rule'];
const communityResourcesColumns = ['client_id', 'resource_id', 'tenant_id', 'leader_id', 'title', 'category', 'description', 'url', 'platform', 'country', 'city', 'audience', 'topic_tags', 'access_level', 'priority', 'is_official', 'status', 'last_checked_at', 'owner', 'updated_at', 'notes'];

const productDetailsTableSql = `CREATE TABLE IF NOT EXISTS advisor_structured_product_details (
  client_id text NOT NULL,
  detail_id text NOT NULL,
  sku text NOT NULL,
  topic text NOT NULL,
  title text,
  answer_text text NOT NULL,
  source_id text,
  source_url text,
  source_locator text,
  evidence_type text,
  review_status text,
  audience text,
  active boolean NOT NULL DEFAULT true,
  priority integer NOT NULL DEFAULT 100,
  owner_state text,
  content_hash text,
  updated_at text,
  notes text,
  PRIMARY KEY (client_id, detail_id)
);`;

const productComparisonsTableSql = `CREATE TABLE IF NOT EXISTS advisor_structured_product_comparisons (
  client_id text NOT NULL,
  comparison_id text NOT NULL,
  left_sku text NOT NULL,
  right_sku text NOT NULL,
  title text,
  answer_text text NOT NULL,
  why_choose_left text,
  why_choose_right text,
  important_note text,
  source_id text,
  source_status text,
  review_status text,
  active boolean NOT NULL DEFAULT true,
  priority integer NOT NULL DEFAULT 100,
  updated_at text,
  notes text,
  PRIMARY KEY (client_id, comparison_id)
);`;

const usersAccessTableSql = `CREATE TABLE IF NOT EXISTS advisor_structured_users_access (
  client_id text NOT NULL,
  telegram_user_id text NOT NULL,
  display_name text,
  username text,
  role text,
  access_status text NOT NULL DEFAULT 'candidate',
  subscription_status text NOT NULL DEFAULT 'off',
  last_seen_at text,
  notes text,
  updated_at text,
  structure_code text NOT NULL DEFAULT 'general',
  structure_owner text,
  alert_recipient text,
  requested_structure_code text,
  ownership_status text,
  assigned_by text,
  ownership_updated_at text,
  PRIMARY KEY (client_id, telegram_user_id)
);`;

const structureOwnersTableSql = `CREATE TABLE IF NOT EXISTS advisor_structured_structure_owners (
  client_id text NOT NULL,
  structure_code text NOT NULL,
  leader_name text,
  leader_username text,
  leader_telegram_user_id text,
  default_alert_recipient text,
  invite_link text,
  status text,
  parent_structure_code text,
  owner_status text,
  PRIMARY KEY (client_id, structure_code)
);`;

const businessObjectionsTableSql = `CREATE TABLE IF NOT EXISTS advisor_structured_business_objections (
  client_id text NOT NULL,
  objection_id text NOT NULL,
  title text NOT NULL,
  aliases text,
  first_reply text NOT NULL,
  clarify text,
  confirmed_answer_rule text,
  next_step text,
  do_not_say text,
  source_id text,
  review_status text,
  active boolean NOT NULL DEFAULT false,
  priority integer NOT NULL DEFAULT 100,
  owner_state text,
  updated_at text,
  notes text,
  PRIMARY KEY (client_id, objection_id)
);`;

const businessFaqTableSql = `CREATE TABLE IF NOT EXISTS advisor_structured_business_faq (
  client_id text NOT NULL,
  faq_id text NOT NULL,
  title text NOT NULL,
  aliases text,
  answer_text text NOT NULL,
  do_not_say text,
  source_id text,
  review_status text,
  active boolean NOT NULL DEFAULT false,
  priority integer NOT NULL DEFAULT 100,
  owner_state text,
  updated_at text,
  notes text,
  PRIMARY KEY (client_id, faq_id)
);`;

const promotionsTableSql = `CREATE TABLE IF NOT EXISTS advisor_promotions (
  client_id text NOT NULL,
  promotion_id text NOT NULL,
  tenant_id text NOT NULL DEFAULT 'by',
  title text NOT NULL,
  short_text text,
  full_text text,
  country text,
  city text,
  starts_at timestamptz NOT NULL,
  ends_at timestamptz NOT NULL,
  timezone text,
  promotion_type text,
  product_ids text,
  min_amount text,
  min_pv text,
  benefit_text text,
  source_url text,
  image_url text,
  priority integer NOT NULL DEFAULT 100,
  status text NOT NULL DEFAULT 'draft',
  owner text,
  updated_at text,
  PRIMARY KEY (client_id, promotion_id)
);
CREATE INDEX IF NOT EXISTS advisor_promotions_active_dates_idx
  ON advisor_promotions (client_id, tenant_id, status, starts_at, ends_at);`;

const recommendationRulesTableSql = `CREATE TABLE IF NOT EXISTS advisor_product_recommendation_rules (
  client_id text NOT NULL,
  product_id text NOT NULL,
  tenant_id text NOT NULL DEFAULT 'by',
  registration_enabled boolean NOT NULL DEFAULT true,
  availability_status text NOT NULL DEFAULT 'available',
  universality_score integer NOT NULL DEFAULT 0,
  popularity_score integer NOT NULL DEFAULT 0,
  demo_score integer NOT NULL DEFAULT 0,
  gift_score integer NOT NULL DEFAULT 0,
  resale_score integer NOT NULL DEFAULT 0,
  personal_use_score integer NOT NULL DEFAULT 0,
  explanation_difficulty integer NOT NULL DEFAULT 0,
  price_sensitivity integer NOT NULL DEFAULT 0,
  business_priority integer NOT NULL DEFAULT 0,
  goal_tags text,
  audience_tags text,
  reason_short text,
  avoid_when text,
  status text NOT NULL DEFAULT 'draft',
  owner text,
  updated_at text,
  PRIMARY KEY (client_id, product_id)
);`;

const starterBasketTemplatesTableSql = `CREATE TABLE IF NOT EXISTS advisor_starter_basket_templates (
  client_id text NOT NULL,
  template_id text NOT NULL,
  tenant_id text NOT NULL DEFAULT 'by',
  title text NOT NULL,
  goal text NOT NULL DEFAULT 'balanced',
  budget_min text,
  budget_max text,
  target_pv_min text,
  target_pv_max text,
  required_product_ids text,
  preferred_product_ids text,
  excluded_product_ids text,
  description text,
  priority integer NOT NULL DEFAULT 100,
  status text NOT NULL DEFAULT 'draft',
  owner text,
  updated_at text,
  PRIMARY KEY (client_id, template_id)
);`;

const eventsTableSql = `CREATE TABLE IF NOT EXISTS advisor_whieda_events (
  client_id text NOT NULL,
  event_id text NOT NULL,
  tenant_id text NOT NULL DEFAULT 'by',
  title text NOT NULL,
  event_type text,
  description text,
  starts_at timestamptz NOT NULL,
  ends_at timestamptz,
  timezone text,
  country text,
  city text,
  address text,
  online_url text,
  contact text,
  audience_segment text,
  leader_id text,
  image_url text,
  source_url text,
  reminder_offsets text,
  status text NOT NULL DEFAULT 'draft',
  owner text,
  updated_at text,
  recurrence_rule text,
  PRIMARY KEY (client_id, event_id)
);
CREATE INDEX IF NOT EXISTS advisor_whieda_events_active_idx
  ON advisor_whieda_events (client_id, tenant_id, status, starts_at);`;

const communityResourcesTableSql = `CREATE TABLE IF NOT EXISTS advisor_whieda_community_resources (
  client_id text NOT NULL,
  resource_id text NOT NULL,
  tenant_id text NOT NULL DEFAULT 'by',
  leader_id text,
  title text NOT NULL,
  category text,
  description text,
  url text NOT NULL,
  platform text,
  country text,
  city text,
  audience text,
  topic_tags text,
  access_level text NOT NULL DEFAULT 'public',
  priority integer NOT NULL DEFAULT 100,
  is_official boolean NOT NULL DEFAULT false,
  status text NOT NULL DEFAULT 'draft',
  last_checked_at text,
  owner text,
  updated_at text,
  notes text,
  PRIMARY KEY (client_id, resource_id)
);`;

const eventsReminderBridgeSql = `CREATE TABLE IF NOT EXISTS advisor_scheduled_events (
  client_id text NOT NULL,
  event_id text NOT NULL,
  created_by_user_id text NOT NULL,
  audience text NOT NULL,
  text_body text NOT NULL,
  meeting_at timestamptz NOT NULL,
  status text NOT NULL DEFAULT 'draft',
  created_at timestamptz NOT NULL DEFAULT now(),
  confirmed_at timestamptz,
  cancelled_at timestamptz,
  PRIMARY KEY (client_id, event_id)
);
CREATE TABLE IF NOT EXISTS advisor_scheduled_event_jobs (
  client_id text NOT NULL,
  event_id text NOT NULL,
  reminder_key text NOT NULL,
  scheduled_at timestamptz NOT NULL,
  status text NOT NULL DEFAULT 'draft',
  draft_id text,
  dispatched_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (client_id, event_id, reminder_key)
);

WITH source_events AS (
  SELECT
    e.client_id,
    e.event_id AS source_event_id,
    CASE
      WHEN upper(COALESCE(e.recurrence_rule, '')) LIKE '%FREQ=WEEKLY%' THEN
        e.starts_at + GREATEST(0, CEIL(EXTRACT(EPOCH FROM (now() - e.starts_at)) / 604800.0))::integer * interval '7 days'
      ELSE e.starts_at
    END AS meeting_at,
    CASE WHEN lower(COALESCE(e.audience_segment, '')) IN ('candidates', 'leaders', 'partners') THEN lower(e.audience_segment) ELSE 'partners' END AS audience,
    concat_ws(E'\\n', e.title, NULLIF(e.description, ''), NULLIF(e.address, ''), CASE WHEN e.contact IS NULL OR e.contact = '' THEN NULL ELSE 'Спикер: ' || e.contact END, NULLIF(e.online_url, '')) AS text_body
  FROM advisor_whieda_events e
  WHERE e.client_id = ${sqlValue(PROJECT_ID)}
    AND lower(e.status) = 'confirmed'
), instances AS (
  SELECT *, 'sheet-' || source_event_id || '-' || to_char(meeting_at AT TIME ZONE 'Europe/Minsk', 'YYYYMMDDHH24MI') AS runtime_event_id
  FROM source_events
  WHERE meeting_at > now()
), cancelled AS (
  UPDATE advisor_scheduled_events se
  SET status = 'cancelled', cancelled_at = now()
  WHERE se.client_id = ${sqlValue(PROJECT_ID)}
    AND se.event_id LIKE 'sheet-%'
    AND se.status IN ('draft', 'confirmed')
    AND NOT EXISTS (SELECT 1 FROM instances i WHERE i.client_id = se.client_id AND i.runtime_event_id = se.event_id)
  RETURNING se.client_id, se.event_id
), upsert_events AS (
  INSERT INTO advisor_scheduled_events (client_id, event_id, created_by_user_id, audience, text_body, meeting_at, status, confirmed_at)
  SELECT client_id, runtime_event_id, 'sheet-sync', audience, text_body, meeting_at, 'confirmed', now()
  FROM instances
  ON CONFLICT (client_id, event_id) DO UPDATE SET
    audience = EXCLUDED.audience,
    text_body = EXCLUDED.text_body,
    meeting_at = EXCLUDED.meeting_at,
    status = 'confirmed',
    cancelled_at = NULL
  RETURNING client_id, event_id, meeting_at
), jobs AS (
  INSERT INTO advisor_scheduled_event_jobs (client_id, event_id, reminder_key, scheduled_at, status)
  SELECT e.client_id, e.event_id, d.reminder_key, e.meeting_at - d.delay, 'pending'
  FROM upsert_events e
  CROSS JOIN (VALUES ('24h', interval '24 hours'), ('1h', interval '1 hour')) AS d(reminder_key, delay)
  WHERE e.meeting_at - d.delay > now()
  ON CONFLICT (client_id, event_id, reminder_key) DO UPDATE SET
    scheduled_at = EXCLUDED.scheduled_at,
    status = CASE WHEN advisor_scheduled_event_jobs.status = 'dispatched' THEN 'dispatched' ELSE 'pending' END
  RETURNING client_id, event_id, reminder_key
)
UPDATE advisor_scheduled_event_jobs j
SET status = 'cancelled'
FROM cancelled c
WHERE j.client_id = c.client_id
  AND j.event_id = c.event_id
  AND j.status IN ('draft', 'pending');`;

const intentRegistryTableSql = `CREATE TABLE IF NOT EXISTS advisor_structured_intent_registry (
  client_id text NOT NULL,
  intent_id text NOT NULL,
  domain text,
  name_ru text,
  examples text,
  required_slots text,
  route text NOT NULL,
  source_table text,
  clarification_key text,
  priority integer NOT NULL DEFAULT 100,
  enabled boolean NOT NULL DEFAULT true,
  owner_state text,
  updated_at text,
  PRIMARY KEY (client_id, intent_id)
);`;

const clarificationPromptsTableSql = `CREATE TABLE IF NOT EXISTS advisor_structured_clarification_prompts (
  client_id text NOT NULL,
  clarification_key text NOT NULL,
  reason text,
  prompt_text text NOT NULL,
  button_options text,
  max_attempts integer NOT NULL DEFAULT 2,
  fallback_menu text,
  enabled boolean NOT NULL DEFAULT true,
  owner_state text,
  updated_at text,
  PRIMARY KEY (client_id, clarification_key)
);`;

const capabilityResponsesTableSql = `CREATE TABLE IF NOT EXISTS advisor_structured_capability_responses (
  client_id text NOT NULL,
  response_id text NOT NULL,
  intent_id text NOT NULL,
  answer_text text NOT NULL,
  enabled boolean NOT NULL DEFAULT true,
  owner_state text,
  updated_at text,
  notes text,
  PRIMARY KEY (client_id, response_id)
);`;

const canonicalQuestionsTableSql = `CREATE TABLE IF NOT EXISTS advisor_structured_canonical_questions (
  client_id text NOT NULL,
  question_id text NOT NULL,
  intent_id text,
  entity_id text,
  canonical_question text NOT NULL,
  real_examples text,
  answer_key text,
  frequency integer NOT NULL DEFAULT 0,
  source_id text,
  status text,
  owner_state text,
  updated_at text,
  PRIMARY KEY (client_id, question_id)
);`;

function keyTupleSql(row, keyColumns) {
  return `(${keyColumns.map((column) => sqlValue(row[column])).join(', ')})`;
}

function reconcileAndReplace(tableName, columns, keyColumns, rows) {
  if (!rows.length) {
    return `DELETE FROM ${tableName} WHERE client_id = ${sqlValue(PROJECT_ID)};`;
  }
  const keyTuple = keyColumns.length === 1 ? keyColumns[0] : `(${keyColumns.join(', ')})`;
  const incomingKeys = rows.map((row) => keyTupleSql(row, keyColumns)).join(',\n');
  return `DELETE FROM ${tableName}
WHERE client_id = ${sqlValue(PROJECT_ID)}
  AND ${keyTuple} NOT IN (
${incomingKeys}
  );

DELETE FROM ${tableName}
WHERE client_id = ${sqlValue(PROJECT_ID)}
  AND ${keyTuple} IN (
${incomingKeys}
  );

INSERT INTO ${tableName} (${columns.join(', ')})
VALUES
${valuesSql(rows, columns)};`.trim();
}

function partnersDisplayMode(pageMode) {
  const mode = String(pageMode || '').trim().toLowerCase();
  return (mode === 'anonymous_ref' || mode === 'anonymous') ? 'anonymous' : 'named';
}

function buildPartnersRuntimeSql(rows) {
  const tenantId = 'whieda';
  const actorValues = [];
  const roleValues = [];
  const profileValues = [];

  for (const row of rows) {
    const actorId = asText(pick(row, ['partner_id']));
    if (!actorId) continue;
    const displayName = asText(pick(row, ['display_name'])) || actorId;
    const ownerActorId = asText(pick(row, ['owner_actor_id'])) || actorId;
    const siteType = String(pick(row, ['site_type']) || '').trim().toLowerCase();
    let telegramUsername = asText(pick(row, ['telegram_username']));
    let telegramChatId = asText(pick(row, ['telegram_chat_id']));
    if (telegramUsername) telegramUsername = telegramUsername.replace(/^@/, '');
    if (ownerActorId !== actorId || siteType === 'platform_root') {
      telegramUsername = null;
      telegramChatId = null;
    }
    const active = asBool(pick(row, ['active']), true);
    actorValues.push(`(${sqlValue(actorId)}, ${sqlValue(tenantId)}, ${sqlValue(displayName)}, ${sqlValue(telegramChatId)}, ${sqlValue(telegramUsername)}, ${active ? 'true' : 'false'})`);

    const refCode = asText(pick(row, ['ref_code']));
    const pageMode = asText(pick(row, ['page_mode'])) || 'standard_ref';
    const planStatus = String(pick(row, ['plan_status']) || '').trim().toLowerCase();
    const enabled = active && (planStatus === '' || planStatus === 'active');
    const country = asText(pick(row, ['country'])) || 'GLOBAL';
    if (refCode) {
      const roleActorId = siteType === 'platform_root' ? ownerActorId : actorId;
      if (enabled) {
        roleValues.push(`(${sqlValue(tenantId)}, ${sqlValue(roleActorId)}, 'referral_owner', ${sqlValue(country)}, ${sqlValue('')}, true)`);
      }
      const publicProfile = {
        partner_id: actorId,
        display_name: displayName,
        page_mode: pageMode,
        plan_code: pick(row, ['plan_code']),
        plan_status: pick(row, ['plan_status']),
        leads_access: pick(row, ['leads_access']),
        public_site_url: pick(row, ['public_site_url', 'ref_url']),
        site_type: pick(row, ['site_type']),
        focus_group: String(pick(row, ['focus_group']) || '').trim().toUpperCase() === 'TRUE',
        access_tier: pick(row, ['access_tier']),
        watcher_actor_id: pick(row, ['watcher_actor_id']),
        owner_actor_id: ownerActorId,
      };
      profileValues.push(`(${sqlValue(refCode)}, ${sqlValue(tenantId)}, ${sqlValue(ownerActorId)}, ${sqlValue(partnersDisplayMode(pageMode))}, ${sqlValue(JSON.stringify(publicProfile))}::jsonb, ${sqlValue(country)}, NULL, ${enabled ? 'true' : 'false'})`);
    }
  }

  if (!actorValues.length) return 'SELECT 1;';

  let sql = `INSERT INTO lead_actors (actor_id, tenant_id, display_name, telegram_chat_id, telegram_username, active)
VALUES
  ${actorValues.join(',\n  ')}
ON CONFLICT (actor_id) DO UPDATE
SET display_name = excluded.display_name,
    telegram_chat_id = CASE
      WHEN excluded.telegram_chat_id IS NULL THEN lead_actors.telegram_chat_id
      WHEN EXISTS (
        SELECT 1 FROM lead_actors la
        WHERE la.tenant_id = excluded.tenant_id
          AND la.telegram_chat_id = excluded.telegram_chat_id
          AND la.actor_id <> excluded.actor_id
      ) THEN lead_actors.telegram_chat_id
      ELSE excluded.telegram_chat_id
    END,
    telegram_username = coalesce(excluded.telegram_username, lead_actors.telegram_username),
    active = excluded.active,
    updated_at = now();`;

  if (roleValues.length) {
    sql += `

INSERT INTO lead_actor_roles (tenant_id, actor_id, role, country_code, region_code, active)
VALUES
  ${roleValues.join(',\n  ')}
ON CONFLICT (tenant_id, actor_id, role, country_code, region_code) DO UPDATE
SET active = excluded.active;`;
  }

  if (profileValues.length) {
    sql += `

INSERT INTO referral_profiles (
  ref_code, tenant_id, owner_id, display_mode, public_profile, country_code, region_code, enabled
)
VALUES
  ${profileValues.join(',\n  ')}
ON CONFLICT (ref_code) DO UPDATE
SET owner_id = excluded.owner_id,
    display_mode = excluded.display_mode,
    public_profile = excluded.public_profile,
    country_code = excluded.country_code,
    enabled = excluded.enabled,
    profile_version = referral_profiles.profile_version + 1,
    updated_at = now();`;
  }

  return sql.trim();
}

const partnersRefRows = parseTsv(partnersRefRaw).filter((row) => asText(pick(row, ['partner_id'])));
const queryPartnersRuntime = buildPartnersRuntimeSql(partnersRefRows);

return [{
  json: {
    project_id: PROJECT_ID,
    sync_started_at: new Date().toISOString(),
    rows_products: productsDeduped.length,
    rows_aliases: aliasesDeduped.length,
    rows_resources: resourcesDeduped.length,
    rows_product_cards: productCardsDeduped.length,
    rows_product_details: productDetailsDeduped.length,
    rows_product_comparisons: productComparisonsDeduped.length,
    rows_users_access: usersAccessDeduped.length,
    rows_structure_owners: structureOwnersDeduped.length,
    rows_business_objections: businessObjectionsDeduped.length,
    rows_business_faq: businessFaqDeduped.length,
    rows_promotions: promotionsDeduped.length,
    rows_recommendation_rules: recommendationRulesDeduped.length,
    rows_starter_basket_templates: starterBasketTemplatesDeduped.length,
    rows_intent_registry: intentRegistryDeduped.length,
    rows_clarification_prompts: clarificationPromptsDeduped.length,
    rows_capability_responses: capabilityResponsesDeduped.length,
    rows_canonical_questions: canonicalQuestionsDeduped.length,
    rows_events: eventsDeduped.length,
    rows_community_resources: communityResourcesDeduped.length,
    rows_partners_ref: partnersRefRows.length,
    query_products: reconcileAndReplace('advisor_structured_products', productsColumns, ['client_id', 'sku'], productsDeduped),
    query_aliases: reconcileAndReplace('advisor_structured_aliases', aliasesColumns, ['client_id', 'alias', 'canonical_sku'], aliasesDeduped),
    query_resources: reconcileAndReplace('advisor_structured_resources', resourcesColumns, ['client_id', 'resource_id'], resourcesDeduped),
    query_product_cards: reconcileAndReplace('advisor_structured_product_cards', productCardsColumns, ['client_id', 'sku'], productCardsDeduped),
    query_product_details: productDetailsTableSql + '\n\n' + reconcileAndReplace('advisor_structured_product_details', productDetailsColumns, ['client_id', 'detail_id'], productDetailsDeduped),
    query_product_comparisons: productComparisonsTableSql + '\n\n' + reconcileAndReplace('advisor_structured_product_comparisons', productComparisonsColumns, ['client_id', 'comparison_id'], productComparisonsDeduped),
    query_users_access: usersAccessTableSql + '\n\n' + reconcileAndReplace('advisor_structured_users_access', usersAccessColumns, ['client_id', 'telegram_user_id'], usersAccessDeduped),
    query_structure_owners: structureOwnersTableSql + '\n\n' + reconcileAndReplace('advisor_structured_structure_owners', structureOwnersColumns, ['client_id', 'structure_code'], structureOwnersDeduped),
    query_business_objections: businessObjectionsTableSql + '\n\n' + reconcileAndReplace('advisor_structured_business_objections', businessObjectionsColumns, ['client_id', 'objection_id'], businessObjectionsDeduped),
    query_business_faq: businessFaqTableSql + '\n\n' + reconcileAndReplace('advisor_structured_business_faq', businessFaqColumns, ['client_id', 'faq_id'], businessFaqDeduped),
    query_promotions: promotionsTableSql + '\n\n' + reconcileAndReplace('advisor_promotions', promotionsColumns, ['client_id', 'promotion_id'], promotionsDeduped),
    query_recommendation_rules: recommendationRulesTableSql + '\n\n' + reconcileAndReplace('advisor_product_recommendation_rules', recommendationRulesColumns, ['client_id', 'product_id'], recommendationRulesDeduped),
    query_starter_basket_templates: starterBasketTemplatesTableSql + '\n\n' + reconcileAndReplace('advisor_starter_basket_templates', starterBasketTemplatesColumns, ['client_id', 'template_id'], starterBasketTemplatesDeduped),
    query_events: eventsTableSql + '\n\n' + reconcileAndReplace('advisor_whieda_events', eventsColumns, ['client_id', 'event_id'], eventsDeduped) + '\n\n' + eventsReminderBridgeSql,
    query_community_resources: communityResourcesTableSql + '\n\n' + reconcileAndReplace('advisor_whieda_community_resources', communityResourcesColumns, ['client_id', 'resource_id'], communityResourcesDeduped),
    query_intent_registry: intentRegistryTableSql + '\n\n' + reconcileAndReplace('advisor_structured_intent_registry', intentRegistryColumns, ['client_id', 'intent_id'], intentRegistryDeduped),
    query_clarification_prompts: clarificationPromptsTableSql + '\n\n' + reconcileAndReplace('advisor_structured_clarification_prompts', clarificationPromptsColumns, ['client_id', 'clarification_key'], clarificationPromptsDeduped),
    query_capability_responses: capabilityResponsesTableSql + '\n\n' + reconcileAndReplace('advisor_structured_capability_responses', capabilityResponsesColumns, ['client_id', 'response_id'], capabilityResponsesDeduped),
    query_canonical_questions: canonicalQuestionsTableSql + '\n\n' + reconcileAndReplace('advisor_structured_canonical_questions', canonicalQuestionsColumns, ['client_id', 'question_id'], canonicalQuestionsDeduped),
    query_partners_runtime: queryPartnersRuntime,
  },
}];
