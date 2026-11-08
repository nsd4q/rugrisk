-- OPTIONAL clean-up for databases created by an EARLIER version of RugRisk that had user
-- accounts, scan quotas, watchlists and saved screens. Current versions don't use any of this.
-- Safe to skip: leftovers are harmless. Safe to re-run.

-- 1. the trigger that created a profile for every new sign-up
drop trigger if exists on_auth_user_created on auth.users;

-- 2. tables first (this also removes their triggers), then the functions
drop table if exists public.watchlist, public.saved_screens, public.scan_usage,
                     public.profiles, public.plan_limits cascade;

drop function if exists public.handle_new_user();
drop function if exists public.consume_scan(uuid);
drop function if exists public.refund_scan(uuid);
drop function if exists public.enforce_saved_screens_limit();
drop function if exists public.enforce_watchlist_limit();
