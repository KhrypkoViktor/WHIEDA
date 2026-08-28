-- Owner-approved solution bundles used by the structured advisor runtime.
-- The live WHIEDA table predates the platform apply chain; this migration makes
-- fresh staging and future tenants reproduce the same contract.

create table if not exists advisor_structured_solution_bundles (
  client_id text not null,
  bundle_id text not null,
  bundle_name text not null,
  aliases text,
  sku_groups text,
  component_names text,
  primary_pain text,
  desired_outcome text,
  positioning text,
  do_not_claim text,
  price_mode text,
  active boolean not null default false,
  priority integer not null default 0,
  notes text,
  source_updated_at timestamptz not null default now(),
  primary key (client_id, bundle_id)
);

create index if not exists advisor_structured_solution_bundles_active_idx
  on advisor_structured_solution_bundles (client_id, active, priority desc, bundle_id);
