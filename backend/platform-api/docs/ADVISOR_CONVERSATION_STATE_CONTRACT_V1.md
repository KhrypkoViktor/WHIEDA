# Advisor Conversation State Contract V1

Local Core advisor multi-turn reliability contract. Reuses `platform_session_context.context` JSONB — no second session table.

## Context fields

| Field | Type | Purpose |
|---|---|---|
| `last_product_sku` | string \| null | Resolved SKU for follow-ups |
| `last_product_name` | string \| null | Display name for user-visible answers |
| `last_intent` | string \| null | Last routed intent label |
| `clarification_pending` | boolean | True while awaiting user selection |
| `clarification_candidates` | array (max 3) | Safe SKU/name candidates only |
| `last_compare_left_sku` | string \| null | Active comparison left side |
| `last_compare_right_sku` | string \| null | Active comparison right side |

Basket draft is not persisted unless an existing basket design is active; cart queries remain stateless per request.

## Transition rules

1. **Product card** (`structured_card`, `structured_product_detail`) sets `last_product_sku` and `last_product_name`.
2. **Price / photo / video / certificate** after a card resolve against stored product when the utterance omits a product name.
3. **Clarification** (`clarification` + gap kinds `ambiguous_product`, `unknown_followup`, etc.) stores candidates but does not set a selected product until the user names one.
4. **Valid selection** resolves exactly one candidate and then saves it as last product.
5. **Invalid selection** keeps clarification state and returns one guided clarification (no random product).
6. **New explicit product** in the utterance replaces previous product context.
7. Context never crosses tenant or `session_id`.
8. Missing/expired context yields an honest guided question (`unknown_followup` / No Blind Zone), never an invented product.

## Response envelope (user-visible)

Required: non-empty `answer_text`, `answer_mode`, stable `product`, `media`, `context`, optional `gap_kind`, `next_steps`.

Photo delivery (Telegram): photo first without caption, then full text; text still sends if photo fails.

## No Blind Zone

Unknown, unsupported, medical-boundary, and missing-resource paths use existing `app/advisor/gap.py` kinds only. Prohibited user fragments include: «не знаю», «нет в базе», «передам на проверку», English legacy review phrases, «не смог обработать».

## Verification

- Corpus: `qa/conversation_reliability/whieda_conversation_flows_v1.jsonl`
- Runner: `qa/conversation_reliability/run_conversation_reliability.py`
- Lab gate: `backend/platform-api/scripts/run_local_core_lab.py --e2e --conversation-reliability`
