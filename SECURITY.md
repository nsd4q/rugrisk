# Security

## Reporting a vulnerability

Please **do not** open a public issue for security problems. Use GitHub's private reporting:
**Security tab > Report a vulnerability** on this repository. Include what you found, how to
reproduce it, and the impact. We will respond as soon as we can.

## Scope notes

- RugRisk is designed to run **locally** and has no login. Its API and website listen on `localhost`
  only; exposing them to the internet is unsupported and unsafe.
- RugRisk is designed to be self-hosted. Your `backend/.env` (with the Supabase secret key) must
  never be committed or shared. If you leak it, rotate it in Supabase immediately.
- The frontend only ever uses the Supabase **publishable** key; database access is restricted by
  row-level security (see `supabase/migrations/001_init.sql`). Run `python selfcheck.py` to verify
  your own project's lockdown.
