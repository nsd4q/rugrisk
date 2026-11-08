-- =====================================================================
-- RugRisk: database schema (Supabase, free-tier friendly)
--
--   tokens        screener table + watchlist (watchlisted_at), written only by the backend
--   token_scans   explainable scan history
--   worker_state  tiny key/value table for worker heartbeats
--
-- No user accounts: RugRisk runs locally for one person.
-- Run once in Supabase > SQL Editor. Safe to re-run.
--
-- Free-tier design:
--   * 500 MB database  -> compact column types, hourly pruning
--   * 5 GB egress/mo   -> small rows; the frontend selects named columns only
--   * 2M realtime msgs -> tokens is NOT in the realtime publication (the site polls)
-- =====================================================================


-- ---------------------------------------------------------------------
-- 1. TOKENS
--    fillfactor 70 lets frequent price updates reuse page space (HOT updates)
--    instead of bloating the table.
-- ---------------------------------------------------------------------
create table if not exists public.tokens (
  address          text primary key,
  chain            text not null default 'solana',
  pair_address     text,
  symbol           text,
  name             text,
  dex              text,
  image_url        text,

  pair_created_at  timestamptz,
  first_seen_at    timestamptz not null default now(),

  price_usd        float8,
  mcap             float8,
  fdv              float8,
  liquidity_usd    float8,
  volume_5m        float8,
  volume_1h        float8,
  volume_24h       float8,
  buys_1h          int,
  sells_1h         int,
  price_change_5m  real,
  price_change_1h  real,
  price_change_24h real,
  holders          int,

  -- computed by Postgres so the frontend can filter on it
  buy_ratio_1h     real generated always as (
    case when coalesce(buys_1h, 0) + coalesce(sells_1h, 0) > 0
         then coalesce(buys_1h, 0)::real / (coalesce(buys_1h, 0) + coalesce(sells_1h, 0))
    end
  ) stored,

  -- latest scan summary (full detail lives in token_scans)
  risk_score       smallint check (risk_score between 0 and 100),
  risk_level       text check (risk_level in ('low', 'medium', 'high', 'critical')),
  last_scanned_at  timestamptz,

  -- watchlist: set = starred. Starred tokens are never pruned and always get price refreshes.
  watchlisted_at   timestamptz,

  updated_at       timestamptz not null default now(),

  constraint tokens_address_chk check (
    (chain = 'solana' and address ~ '^[1-9A-HJ-NP-Za-km-z]{32,44}$')
    or (chain <> 'solana' and address ~ '^0x[0-9a-fA-F]{40}$')
  )
);

-- Databases created by an earlier version of this file get the column here (no-op otherwise)
alter table public.tokens add column if not exists watchlisted_at timestamptz;

alter table public.tokens set (
  fillfactor = 70,
  autovacuum_vacuum_scale_factor = 0.05,
  autovacuum_vacuum_threshold = 50
);

-- Minimal indexes on purpose: indexing columns that change every few seconds (price, volume)
-- would block HOT updates and bloat the table. The table is small, so scans are cheap.
create index if not exists tokens_created_idx
  on public.tokens (pair_created_at desc);

-- Watchlist lookups (starred rows only: tiny)
create index if not exists tokens_watchlist_idx
  on public.tokens (watchlisted_at desc)
  where watchlisted_at is not null;

-- Tiny queue index for the optional autoscan worker (only unscanned rows)
create index if not exists tokens_unscanned_idx
  on public.tokens (first_seen_at)
  where last_scanned_at is null;

-- Truncate untrusted text (scam tokens can have huge/hostile names) and stamp updated_at
create or replace function public.tokens_before_write()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
  new.symbol     := left(new.symbol, 32);
  new.name       := left(new.name, 64);
  new.dex        := left(new.dex, 32);
  new.image_url  := left(new.image_url, 300);
  new.updated_at := now();
  return new;
end;
$$;

drop trigger if exists tokens_before_write on public.tokens;
create trigger tokens_before_write
  before insert or update on public.tokens
  for each row execute function public.tokens_before_write();


-- ---------------------------------------------------------------------
-- 2. TOKEN SCANS
-- ---------------------------------------------------------------------
create table if not exists public.token_scans (
  id             bigint generated always as identity primary key,
  token_address  text not null references public.tokens(address) on delete cascade,
  score          smallint not null check (score between 0 and 100),
  level          text not null check (level in ('low', 'medium', 'high', 'critical')),
  checks         jsonb not null,  -- [{id,status,points,detail}]
  created_at     timestamptz not null default now(),
  constraint token_scans_size_chk check (octet_length(checks::text) < 8000)
);

create index if not exists token_scans_token_idx
  on public.token_scans (token_address, created_at desc);


-- ---------------------------------------------------------------------
-- 3. WORKER STATE (heartbeats). Backend only.
--    The regular writes also keep a free project from auto-pausing.
-- ---------------------------------------------------------------------
create table if not exists public.worker_state (
  key         text primary key,
  value       jsonb not null default '{}'::jsonb,
  updated_at  timestamptz not null default now()
);


-- ---------------------------------------------------------------------
-- 4. ROW LEVEL SECURITY + GRANTS
--    The website uses the PUBLISHABLE key (role `anon`): it may read tokens and scans and
--    nothing else. Only the secret key (backend) can write. Verify with: python selfcheck.py
-- ---------------------------------------------------------------------
alter table public.tokens       enable row level security;
alter table public.token_scans  enable row level security;
alter table public.worker_state enable row level security;

revoke all on public.tokens, public.token_scans, public.worker_state from anon, authenticated;
grant select on public.tokens, public.token_scans to anon, authenticated;
-- worker_state: no grants for anon/authenticated (secret key only)

drop policy if exists "tokens readable" on public.tokens;
create policy "tokens readable" on public.tokens
  for select using (true);

drop policy if exists "scans readable" on public.token_scans;
create policy "scans readable" on public.token_scans
  for select using (true);

-- Trigger functions must never be callable through the API
revoke execute on function public.tokens_before_write() from public, anon, authenticated;


-- ---------------------------------------------------------------------
-- 5. PRUNING (keeps you far below the 500 MB cap)
--    * tokens first seen more than p_token_hours ago (default 192 h = 8 days) are deleted,
--      EXCEPT watchlisted ones. Their scans cascade-delete.
--    * scans older than 24h are deleted, keeping the newest per token
--    The 8-day window is what makes the "min age: 1 week" filter useful.
-- ---------------------------------------------------------------------
create or replace function public.prune_old_data(p_token_hours int default 192)
returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
  n_tokens int;
  n_scans  int;
begin
  delete from tokens t
   where t.first_seen_at < now() - make_interval(hours => p_token_hours)
     and t.watchlisted_at is null;
  get diagnostics n_tokens = row_count;

  delete from token_scans s
   where s.created_at < now() - interval '24 hours'
     and exists (
       select 1 from token_scans n
        where n.token_address = s.token_address
          and n.created_at > s.created_at
     );
  get diagnostics n_scans = row_count;

  return jsonb_build_object('tokens', n_tokens, 'scans', n_scans);
end;
$$;

revoke execute on function public.prune_old_data(int) from public, anon, authenticated;
grant  execute on function public.prune_old_data(int) to service_role;


-- ---------------------------------------------------------------------
-- 6. USAGE REPORT (check your free-tier headroom any time)
--    select public.admin_db_report();
-- ---------------------------------------------------------------------
create or replace function public.admin_db_report()
returns jsonb
language sql
security definer
set search_path = public
as $$
  select jsonb_build_object(
    'db_size_mb', round(pg_database_size(current_database()) / 1048576.0, 1),
    'limit_mb', 500,
    'rows', jsonb_build_object(
      'tokens',      (select count(*) from tokens),
      'watchlisted', (select count(*) from tokens where watchlisted_at is not null),
      'token_scans', (select count(*) from token_scans)
    ),
    'table_mb', (
      select jsonb_object_agg(c.relname, round(pg_total_relation_size(c.oid) / 1048576.0, 2))
        from pg_class c
       where c.relnamespace = 'public'::regnamespace and c.relkind = 'r'
    )
  );
$$;

revoke execute on function public.admin_db_report() from public, anon, authenticated;
grant  execute on function public.admin_db_report() to service_role;


-- ---------------------------------------------------------------------
-- 7. SCHEDULED JOBS (pg_cron). Wrapped so the rest of the script still succeeds if pg_cron
--    isn't enabled yet. If you see the NOTICE, enable pg_cron in
--    Dashboard > Database > Extensions, then re-run this block.
-- ---------------------------------------------------------------------
do $$
begin
  create extension if not exists pg_cron;

  perform cron.schedule(
    'prune-old-data', '7 * * * *',
    $job$ select public.prune_old_data(192) $job$
  );

  -- cron's own run log grows forever otherwise
  perform cron.schedule(
    'clean-cron-log', '30 3 * * *',
    $job$ delete from cron.job_run_details where end_time < now() - interval '2 days' $job$
  );
exception when others then
  raise notice 'pg_cron setup skipped: %', sqlerrm;
end;
$$;


-- ---------------------------------------------------------------------
-- 8. REALTIME is intentionally NOT enabled for tokens: price updates every few seconds
--    would burn the 2M messages/month free quota. The website polls instead.
-- ---------------------------------------------------------------------
