# WHIEDA: Company and Business Knowledge Pack V1

## Goal

Prepare a reviewed, traceable source pack so the advisor can later answer simple
questions about WHIEDA itself: who the company is, what it does, people,
production, events and the partner path. This task collects and structures
facts. It does not change any user-facing answer yet.

## Scope

Create only:

- `qa/company_knowledge/`
- `backend/platform-api/docs/COMPANY_KNOWLEDGE_PACK_LOCAL_REPORT.md`
- focused tests in `backend/platform-api/tests/test_company_knowledge_pack.py`

Read-only source candidates:

- `n8n/live-exports/structured-master/<latest-valid>/`
- `RAG/`
- existing `qa/` corpora and approved docs under `backend/platform-api/docs/`
- `D:\Obsidian\WHIEDA` only when it is available locally.

Do not edit `app/**`, `n8n/**`, `postgres/**`, Google Sheets, Telegram, runtime
Postgres, product cards or deployment files.

## Output

### 1. Fact candidate TSV

`qa/company_knowledge/COMPANY_FACTS_CANDIDATES_V1.tsv`

Columns:

`fact_id, topic, question_examples, fact_text, answer_scope, source_kind,
source_ref, source_locator, provenance_status, confidence, owner_status,
notes`

Topics are exactly:

- `company_overview`
- `history`
- `leadership`
- `production`
- `technology`
- `partner_business`
- `events`
- `contacts`

Rules:

- one factual claim per row;
- every row has a concrete local source reference and locator (page, heading,
  row, or line); 
- no source means `owner_status=needs_owner_input`, never an invented fact;
- retain the source wording where it matters; do not turn a marketing slogan
  into a historical or technical fact;
- do not create health or treatment claims.

### 2. Leaders candidate TSV

`qa/company_knowledge/LEADERS_CANDIDATES_V1.tsv`

Columns:

`leader_id, display_name, role, region, public_bio, source_ref,
source_locator, provenance_status, owner_status, notes`

Only include people explicitly described in a source as public representatives,
speakers or leaders. No guessing of role, city or biography.

### 3. Advisor question pack

`qa/company_knowledge/COMPANY_QUESTION_CANDIDATES_V1.jsonl`

At least 45 questions over all eight topics. Each line includes:

`case_id, user_text, expected_topic, expected_fact_ids, source_ref,
acceptance_status`

`accepted` requires one or more candidate facts with
`provenance_status=verified`. Everything else is `pending_owner`.

### 4. Owner request sheet

`qa/company_knowledge/COMPANY_INFORMATION_REQUEST_FOR_OWNER.md`

Write it in very plain Russian. For each missing block, ask for exactly what is
needed and show a copy-paste template. Example:

```
1. Год основания компании:
2. Страна и город производства:
3. Официальная страница или PDF:
```

Do not ask vague questions such as “give more information”.

### 5. Builder, lint and report

- `build_company_knowledge_pack.py`
- `run_company_knowledge_pack.py --offline`
- `lab/` implementation as needed
- report with fact counts by topic and status, leaders, question counts,
  exact missing inputs, and source files that could not be read.

The offline runner must fail if an `accepted` fact lacks a source locator or if
an accepted question points to a non-accepted fact.

## Acceptance

```bash
python qa/company_knowledge/build_company_knowledge_pack.py
python qa/company_knowledge/run_company_knowledge_pack.py --offline
python -m pytest backend/platform-api/tests/test_company_knowledge_pack.py -q
```

The report must say plainly what is actually found and what is missing. A large
empty template is not a successful pack.

## Deliverable boundary

This is a data-preparation block for the next advisor layer. It must not claim
that company information is already available in Telegram, and it must not
publish anything.
