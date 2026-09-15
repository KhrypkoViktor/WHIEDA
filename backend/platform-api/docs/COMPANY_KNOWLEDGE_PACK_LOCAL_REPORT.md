# Company Knowledge Pack — Local Report

Mode: read-only data preparation. **Not wired into Telegram/runtime.**

- Snapshot: `20260811T172219Z`
- Facts: 44
- Leaders: 10
- Questions: 48 (40 accepted, 8 pending_owner)
- Verified accepted facts: 34
- Needs owner input: 10
- Lint: PASS

## Facts by topic

- `company_overview`: 7
- `contacts`: 9
- `events`: 2
- `history`: 1
- `leadership`: 4
- `partner_business`: 14
- `production`: 4
- `technology`: 3

## Facts by owner_status

- `accepted`: 34
- `needs_owner_input`: 10

## Facts by provenance

- `company_stated`: 7
- `missing`: 3
- `verified`: 34

## What is found vs missing

- **Found (verified):** business FAQ, objections rule OBJ-03, partners/contacts, events, platform scope from product direction.
- **Missing (owner input):** founding year/history, corporate email/phone, owner-approved production layer.
- **Pending review:** Obsidian Pack 9 company/production claims marked `company_stated`.

## Artifacts

- `qa/company_knowledge/COMPANY_FACTS_CANDIDATES_V1.tsv`
- `qa/company_knowledge/LEADERS_CANDIDATES_V1.tsv`
- `qa/company_knowledge/COMPANY_QUESTION_CANDIDATES_V1.jsonl`
- `qa/company_knowledge/COMPANY_INFORMATION_REQUEST_FOR_OWNER.md`
- `qa/company_knowledge/COMPANY_PUBLIC_REPLY_CANDIDATES_V1.tsv`
- `qa/company_knowledge/PARTNER_NETWORK_CONTACTS_CANDIDATES_V1.tsv`

## Reproduce

```bash
python qa/company_knowledge/build_company_knowledge_pack.py
python qa/company_knowledge/run_company_knowledge_pack.py --offline
python -m pytest backend/platform-api/tests/test_company_knowledge_pack.py -q
```
