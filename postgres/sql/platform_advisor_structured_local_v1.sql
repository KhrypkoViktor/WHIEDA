-- Local-only schema for Core HTTP E2E. Never apply to runtime/prod.
begin;

create table if not exists advisor_structured_products (
  client_id text not null, sku text not null, canonical_name text not null,
  category text, retail_price_rub numeric, retail_w numeric, retail_price_byn numeric,
  partner_price_rub numeric, partner_w numeric, partner_price_byn numeric, partner_points numeric,
  source_updated_at timestamptz not null default now(), primary key (client_id, sku)
);
create table if not exists advisor_structured_aliases (
  client_id text not null, alias text not null, canonical_sku text not null, canonical_name text not null,
  match_type text, priority integer not null default 0, active boolean not null default true,
  answer_scope text, notes text, updated_at text, source_updated_at timestamptz not null default now(),
  primary key (client_id, alias)
);
create table if not exists advisor_structured_product_cards (
  client_id text not null, sku text not null, canonical_name text not null, short_name text,
  what_it_is text, who_asks_about_it text, common_use_cases text, how_to_use_short text,
  what_to_expect_soft text, contraindications_short text, primary_image_url text,
  primary key (client_id, sku)
);
create table if not exists advisor_structured_resources (
  client_id text not null, resource_id text not null, sku text, canonical_name text, alias text, topic text,
  resource_type text not null default 'other', title text not null, url text not null, source_owner text,
  language text, priority integer not null default 0, active boolean not null default true, audience text,
  notes text, updated_at text, source_updated_at timestamptz not null default now(), primary key (client_id, resource_id)
);
create table if not exists advisor_structured_product_comparisons (
  client_id text not null, comparison_id text not null, title text not null, answer_text text not null,
  left_sku text not null, right_sku text not null, priority integer not null default 0, active boolean not null default true,
  primary key (client_id, comparison_id)
);
create table if not exists advisor_structured_product_details (
  client_id text not null, detail_id text not null, sku text not null, topic text, title text, answer_text text,
  priority integer not null default 0, active boolean not null default true, primary key (client_id, detail_id)
);
create table if not exists advisor_structured_capability_responses (
  client_id text not null, response_id text not null, intent_id text not null, answer_text text not null,
  enabled boolean not null default true, primary key (client_id, response_id)
);
create table if not exists advisor_structured_clarification_prompts (
  client_id text not null, clarification_key text not null, prompt_text text not null,
  enabled boolean not null default true, primary key (client_id, clarification_key)
);
create table if not exists advisor_structured_business_faq (
  client_id text not null, faq_id text not null, title text, answer_text text, aliases text,
  priority integer not null default 0, active boolean not null default true, primary key (client_id, faq_id)
);
create table if not exists advisor_structured_business_objections (
  client_id text not null, objection_id text not null, title text, aliases text, first_reply text, clarify text,
  next_step text, do_not_say text, priority integer not null default 0, active boolean not null default true,
  primary key (client_id, objection_id)
);
create table if not exists advisor_structured_canonical_questions (
  client_id text not null, question_id text not null, intent_id text, entity_id text, canonical_question text,
  real_examples text, answer_key text, frequency integer not null default 0, status text not null default 'approved',
  primary key (client_id, question_id)
);
commit;
