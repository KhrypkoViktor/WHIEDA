# Bundle candidate triage — local report

## Counts

- rows actually read: **25**
- expected staging rows: **51**
- source file: `qa\whieda_bundle_triage\fixtures\09_BUNDLE_CANDIDATES.tsv`
- ready_for_owner_review: **6**
- duplicate: **6**
- needs_product_mapping: **1**
- needs_source: **1**
- blocked_claim: **9**
- archive: **2**
- unknown catalog items: **14**
- active-bundle regression cases: **30**

## source_missing

source_missing: expected 51 advisor_bundle_staging_records; read 25 from qa\whieda_bundle_triage\fixtures\09_BUNDLE_CANDIDATES.tsv. No live Postgres dump in this worktree; extra rows were not invented.

## 10 highest-leverage owner questions

1. Где выгрузка 51 строки `advisor_bundle_staging_records`? В worktree есть только 25 строк `09_BUNDLE_CANDIDATES.tsv`; недостающие 26 не выдумывались.
2. Архивировать BUNDLE-0001/0002 (животные) или держать отдельным ветеринарным контуром?
3. Подтвердить вечный `blocked_raw` для схемы «Реанимация» (BUNDLE-0010/0015/0030) — официально осуждена.
4. Канон 3-этапной РОВ — BUNDLE-0016 (официальные дозы 12.12.2025)? Закрыть 0004/0009/0014/0029 как duplicate?
5. Канон совместимости приборов — BUNDLE-0022? Закрыть BUNDLE-0007 как duplicate?
6. Канон ЛОР/гайморит — BUNDLE-0017? Закрыть BUNDLE-0005 как duplicate?
7. Что за «чип из прокладки» (BUNDLE-0003/0017/0028) — отдельный SKU, расходник D003, или выкинуть из набора?
8. Маппить или выкинуть неясные позиции: Коэнзим Q10, водородная вода, Детокс Идеал-1, витамин D3, минералы?
9. BUNDLE-0008 (глаукома) и BUNDLE-0021 (в запросе названы глаукома/катаракта) остаются blocked?
10. BUNDLE-0018 (аденоиды у ребёнка / альтернатива операции) остаётся blocked_claim?

## Что не опубликовано

- Ни одна RAW-заготовка не переведена в `approved` и не отправлена в бот.
- Три уже активных runtime-набора не менялись.
- Нет SQL, import/publish, Google Sheets, live Postgres, Telegram, Docker, deploy.
- `qa/whieda_bundle_triage/` содержит только разметку и offline-проверки.
