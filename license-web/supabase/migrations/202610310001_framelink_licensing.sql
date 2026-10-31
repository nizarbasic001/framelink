-- FrameLink license management schema. Run once in Supabase SQL Editor.
create extension if not exists pgcrypto;

create table if not exists public.admin_users (
  user_id uuid primary key references auth.users(id) on delete cascade,
  email text not null unique,
  created_at timestamptz not null default now()
);

create table if not exists public.customers (
  id uuid primary key default gen_random_uuid(),
  name text not null,
  email text not null unique,
  account_status text not null default 'active' check (account_status in ('active','deactivated')),
  created_at timestamptz not null default now()
);

create table if not exists public.licenses (
  id uuid primary key default gen_random_uuid(),
  key_hash text not null unique,
  customer_id uuid not null references public.customers(id) on delete restrict,
  license_type text not null check (license_type in ('monthly','yearly','lifetime')),
  status text not null default 'active' check (status in ('active','deactivated','revoked')),
  expires_at timestamptz,
  max_devices integer not null default 1 check (max_devices between 1 and 100),
  created_at timestamptz not null default now(),
  activated_at timestamptz,
  constraint lifetime_has_no_expiration check (license_type <> 'lifetime' or expires_at is null)
);

create table if not exists public.devices (
  id uuid primary key default gen_random_uuid(),
  license_id uuid not null references public.licenses(id) on delete cascade,
  fingerprint_hash text not null,
  computer_name text not null default 'Computer',
  activated_at timestamptz not null default now(),
  last_verification timestamptz,
  active boolean not null default true,
  unique (license_id, fingerprint_hash)
);

create table if not exists public.activity_logs (
  id bigint generated always as identity primary key,
  user_id uuid references public.customers(id) on delete set null,
  license_id uuid references public.licenses(id) on delete set null,
  action text not null,
  details jsonb not null default '{}'::jsonb,
  occurred_at timestamptz not null default now()
);

create table if not exists public.app_settings (
  setting_key text primary key,
  setting_value text not null,
  updated_at timestamptz not null default now()
);

insert into public.app_settings(setting_key, setting_value) values
  ('software_name','FrameLink'), ('buy_license_url',''), ('support_email',''),
  ('trial_duration','15'), ('default_device_limit','1')
on conflict (setting_key) do nothing;

create index if not exists licenses_customer_id_idx on public.licenses(customer_id);
create index if not exists licenses_status_expiry_idx on public.licenses(status, expires_at);
create index if not exists devices_license_active_idx on public.devices(license_id, active);
create index if not exists activity_logs_occurred_idx on public.activity_logs(occurred_at desc);

-- Serialize device registration per license so simultaneous activations cannot
-- exceed the configured maximum device count.
create or replace function public.register_framelink_device(
  p_license_id uuid, p_fingerprint_hash text, p_computer_name text
) returns table(license_id uuid, customer_id uuid, license_type text, expires_at timestamptz, activated_at timestamptz)
language plpgsql security definer set search_path = public
as $$
declare
  lic public.licenses%rowtype;
  cust_status text;
  device_row public.devices%rowtype;
  active_count integer;
  device_exists boolean;
  stamp timestamptz := now();
begin
  select l, c.account_status into lic, cust_status
    from public.licenses l join public.customers c on c.id = l.customer_id
    where l.id = p_license_id for update of l;
  if not found then raise exception 'LICENSE_NOT_FOUND'; end if;
  if lic.status <> 'active' or cust_status <> 'active' then raise exception 'LICENSE_INACTIVE'; end if;
  if lic.expires_at is not null and lic.expires_at <= stamp then raise exception 'LICENSE_EXPIRED'; end if;
  select * into device_row from public.devices d where d.license_id=p_license_id and d.fingerprint_hash=p_fingerprint_hash for update;
  device_exists := found;
  if not device_exists or not device_row.active then
    select count(*) into active_count from public.devices d where d.license_id=p_license_id and d.active;
    if active_count >= lic.max_devices then raise exception 'DEVICE_LIMIT'; end if;
    if device_exists then
      update public.devices set active=true, computer_name=left(p_computer_name,120), last_verification=stamp where id=device_row.id;
    else
      insert into public.devices(license_id,fingerprint_hash,computer_name,activated_at,last_verification,active)
        values(p_license_id,p_fingerprint_hash,left(p_computer_name,120),stamp,stamp,true);
    end if;
  else
    update public.devices set computer_name=left(p_computer_name,120), last_verification=stamp where id=device_row.id;
  end if;
  if lic.activated_at is null then
    update public.licenses set activated_at=stamp,
      expires_at=case lic.license_type when 'monthly' then stamp + interval '30 days' when 'yearly' then stamp + interval '365 days' else null end
      where id=p_license_id returning * into lic;
  end if;
  return query select lic.id, lic.customer_id, lic.license_type, lic.expires_at, lic.activated_at;
end;
$$;
revoke all on function public.register_framelink_device(uuid,text,text) from public, anon, authenticated;
grant execute on function public.register_framelink_device(uuid,text,text) to service_role;

create or replace function public.is_framelink_admin()
returns boolean language sql stable security definer set search_path = public, auth
as $$ select exists(select 1 from public.admin_users a where a.user_id = auth.uid()) $$;

alter table public.admin_users enable row level security;
alter table public.customers enable row level security;
alter table public.licenses enable row level security;
alter table public.devices enable row level security;
alter table public.activity_logs enable row level security;
alter table public.app_settings enable row level security;

drop policy if exists "admins manage admin users" on public.admin_users;
create policy "admins manage admin users" on public.admin_users for all to authenticated
  using (public.is_framelink_admin()) with check (public.is_framelink_admin());
drop policy if exists "admins manage customers" on public.customers;
create policy "admins manage customers" on public.customers for all to authenticated
  using (public.is_framelink_admin()) with check (public.is_framelink_admin());
drop policy if exists "admins manage licenses" on public.licenses;
create policy "admins manage licenses" on public.licenses for all to authenticated
  using (public.is_framelink_admin()) with check (public.is_framelink_admin());
drop policy if exists "admins manage devices" on public.devices;
create policy "admins manage devices" on public.devices for all to authenticated
  using (public.is_framelink_admin()) with check (public.is_framelink_admin());
drop policy if exists "admins read activity logs" on public.activity_logs;
create policy "admins read activity logs" on public.activity_logs for select to authenticated
  using (public.is_framelink_admin());
drop policy if exists "admins manage settings" on public.app_settings;
create policy "admins manage settings" on public.app_settings for all to authenticated
  using (public.is_framelink_admin()) with check (public.is_framelink_admin());

-- The desktop activation/verification endpoints use server-side service-role access.
-- No customer data or license records are exposed to anon/authenticated clients directly.
revoke all on public.customers, public.licenses, public.devices, public.activity_logs, public.app_settings from anon;
grant select, insert, update, delete on public.customers, public.licenses, public.devices, public.app_settings to authenticated;
grant select on public.activity_logs to authenticated;
grant select on public.admin_users to authenticated;
grant usage, select on sequence public.activity_logs_id_seq to service_role;

-- Add an initial admin through Supabase Auth, then insert that user's Auth UUID and email:
-- insert into public.admin_users(user_id,email) values ('AUTH-USER-UUID','admin@example.com');
