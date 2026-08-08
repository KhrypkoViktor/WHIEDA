-- RLS for legacy leads / referral tables (apply after whieda_website_leads + wwc_leads P0.1).

begin;

alter table if exists website_leads enable row level security;
alter table if exists website_lead_owner_history enable row level security;
alter table if exists website_content_register enable row level security;
alter table if exists referral_profiles enable row level security;
alter table if exists lead_actors enable row level security;
alter table if exists lead_actor_roles enable row level security;
alter table if exists service_locations enable row level security;
alter table if exists lead_delivery_attempts enable row level security;
alter table if exists website_lead_status_history enable row level security;
alter table if exists website_lead_watchers enable row level security;
alter table if exists lead_visibility_rules enable row level security;
alter table if exists website_events enable row level security;
alter table if exists referral_agreements enable row level security;

drop policy if exists website_leads_tenant_isolation on website_leads;
create policy website_leads_tenant_isolation on website_leads
  using (tenant_id = platform_current_tenant_id())
  with check (tenant_id = platform_current_tenant_id());

drop policy if exists website_lead_owner_history_tenant_isolation on website_lead_owner_history;
create policy website_lead_owner_history_tenant_isolation on website_lead_owner_history
  using (tenant_id = platform_current_tenant_id())
  with check (tenant_id = platform_current_tenant_id());

drop policy if exists website_content_register_tenant_isolation on website_content_register;
create policy website_content_register_tenant_isolation on website_content_register
  using (tenant_id = platform_current_tenant_id())
  with check (tenant_id = platform_current_tenant_id());

drop policy if exists referral_profiles_tenant_isolation on referral_profiles;
create policy referral_profiles_tenant_isolation on referral_profiles
  using (tenant_id = platform_current_tenant_id())
  with check (tenant_id = platform_current_tenant_id());

drop policy if exists lead_actors_tenant_isolation on lead_actors;
create policy lead_actors_tenant_isolation on lead_actors
  using (tenant_id = platform_current_tenant_id())
  with check (tenant_id = platform_current_tenant_id());

drop policy if exists lead_actor_roles_tenant_isolation on lead_actor_roles;
create policy lead_actor_roles_tenant_isolation on lead_actor_roles
  using (tenant_id = platform_current_tenant_id())
  with check (tenant_id = platform_current_tenant_id());

drop policy if exists service_locations_tenant_isolation on service_locations;
create policy service_locations_tenant_isolation on service_locations
  using (tenant_id = platform_current_tenant_id())
  with check (tenant_id = platform_current_tenant_id());

drop policy if exists lead_delivery_attempts_tenant_isolation on lead_delivery_attempts;
create policy lead_delivery_attempts_tenant_isolation on lead_delivery_attempts
  using (tenant_id = platform_current_tenant_id())
  with check (tenant_id = platform_current_tenant_id());

drop policy if exists website_lead_status_history_tenant_isolation on website_lead_status_history;
create policy website_lead_status_history_tenant_isolation on website_lead_status_history
  using (tenant_id = platform_current_tenant_id())
  with check (tenant_id = platform_current_tenant_id());

drop policy if exists website_lead_watchers_tenant_isolation on website_lead_watchers;
create policy website_lead_watchers_tenant_isolation on website_lead_watchers
  using (tenant_id = platform_current_tenant_id())
  with check (tenant_id = platform_current_tenant_id());

drop policy if exists lead_visibility_rules_tenant_isolation on lead_visibility_rules;
create policy lead_visibility_rules_tenant_isolation on lead_visibility_rules
  using (tenant_id = platform_current_tenant_id())
  with check (tenant_id = platform_current_tenant_id());

drop policy if exists website_events_tenant_isolation on website_events;
create policy website_events_tenant_isolation on website_events
  using (tenant_id = platform_current_tenant_id())
  with check (tenant_id = platform_current_tenant_id());

drop policy if exists referral_agreements_tenant_isolation on referral_agreements;
create policy referral_agreements_tenant_isolation on referral_agreements
  using (tenant_id = platform_current_tenant_id())
  with check (tenant_id = platform_current_tenant_id());

create index if not exists idx_referral_profiles_tenant_ref
  on referral_profiles (tenant_id, ref_code)
  where enabled = true;

create index if not exists idx_website_leads_tenant_idempotency
  on website_leads (tenant_id, idempotency_key);

commit;
