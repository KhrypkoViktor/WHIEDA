-- Occupant rows for local shared-staging canary proof. Not APPLY_ORDER. No NSP catalog.
begin;

insert into advisor_structured_products (
  client_id, sku, canonical_name, retail_price_rub, retail_price_byn, retail_prices
) values (
  'whieda', 'OCCUPANT-KEEP', 'WHIEDA occupant marker', null, null,
  '[{"kind":"retail","amount":"1.00","currency":"USD"}]'::jsonb
)
on conflict (client_id, sku) do update
set canonical_name = excluded.canonical_name,
    retail_prices = excluded.retail_prices;

commit;
