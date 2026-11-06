# RugRisk

A self-hosted **Solana memecoin screener and rug-risk scanner**. Run it on your own machine:

- **Screener**: live table of new tokens with filters (age, liquidity, volume, buy %, risk score).
- **Risk scanner**: paste any token address and get a 0-100 risk score with plain-English reasons.
- **Free-tier friendly**: runs on a free Supabase project, free RPC/API keys, and your own computer.

> **Disclaimer:** RugRisk produces *risk indicators*, not guarantees or financial advice. A low score
> can still rug. Checks marked `?` could not be verified and add no points, so they can make a token
> look safer than it is. Do your own research.

## What it checks

| Check | Max pts | What it looks at |
|---|---|---|
| Sell simulation (honeypot) | 40 | Simulates a real holder selling a small slice via Jupiter + `simulateTransaction` |
| Mint authority | 25 | Can the dev still mint unlimited supply? |
| Freeze authority | 20 | Can the dev freeze holders' tokens? |
| LP burn / lock | 20 | Is liquidity burned or locked, or can the owner pull it? |
| Token-2022 extensions | 30 | Permanent delegate, transfer hook, high transfer fee, frozen-by-default, pausable |
| Top-10 wallet concentration | 20 | Share of supply held by 10 wallets (pools and burn addresses excluded) |
| Largest single wallet | 10 | Over 15% of supply |
| Liquidity | 10 | Under $5k |
| Metadata mutable | 5 | Needs a Helius RPC key |
| Token age | 5 | Pair under 10 minutes old |

Levels: 0-24 **low**, 25-49 **medium**, 50-74 **high**, 75+ **critical**. A failed critical check
(active mint/freeze authority, permanent delegate, LP held by wallets) forces at least **high**.
A confirmed honeypot (sells blocked) forces **critical**.

### How the LP check works

1. Read the pool account; its owning program identifies the layout (PumpSwap, Raydium AMM v4,
   Raydium CPMM, or the pump.fun bonding curve).
2. Decode the LP mint and the pool's own record of LP supply. **Every decoder validates its layout**
   by checking that the token's mint appears where the layout says it should. A mismatch is reported
   as *unverified*, never guessed.
3. Look at who holds the LP mint: burn addresses, known lockers, program-controlled accounts
   (PDAs), ordinary wallets. LP burned with the SPL `burn` instruction shrinks the mint supply but
   not the pool's record, so `(recorded - current) / recorded` is the share burned.

Concentrated-liquidity pools (Raydium CLMM, Orca Whirlpool, Meteora DLMM/DAMM) have no fungible LP
token and are reported as unverifiable. Lock *expiry* is not checked.

### How the sell simulation works

It picks a real, ordinary holder wallet, asks Jupiter for an assembled swap that sells a small slice
of the token for SOL from that wallet, and runs it through the RPC's `simulateTransaction`. Nothing
is signed or sent. Frozen accounts and reverting hooks show up as failures; slippage reverts on a
tiny amount suggest a sell tax.

**Limits:** it proves *one* wallet could sell *right now*. It cannot see blacklists aimed at other
wallets or rules that change later. Failures for unrelated reasons (no route yet, rate limit, holder
out of SOL) are reported as *unverified*.

## Architecture

```
frontend/ (static HTML/JS)  --reads-->  Supabase (Postgres + Auth + RLS)  <--writes--  Python workers
        |                                                                               ingest.py   (new pairs + prices)
        +--POST /scan (JWT)-->  Python API (FastAPI)  --calls-->  Solana RPC, DexScreener, Jupiter
                                                                autoscan.py (scores new tokens)
```

- The browser only ever holds the **publishable** key; row-level security makes it read-only on token data.
- The **secret** key lives in `backend/.env` and is never sent to the browser.

## Setup (about 15 minutes)

You need: Python 3.10+, a free [Supabase](https://supabase.com) project, and (recommended) free keys
from [Helius](https://helius.dev) and [Jupiter](https://portal.jup.ag).

### 1. Database

In Supabase: **SQL Editor**, run `supabase/migrations/001_init.sql`, then `002_refund.sql`.
Both are safe to re-run. (If you see a pg_cron notice, enable the `pg_cron` extension under
Database > Extensions and re-run section 10 of the first file; it prunes old data hourly.)

For quick testing, go to **Authentication > Providers > Email** and turn off "Confirm email".
Supabase's built-in email sender is heavily rate limited, so configure custom SMTP before
inviting other people.

### 2. Keys

From **Settings > API Keys** copy your project URL, the **publishable** key and the **secret** key.

```bash
cd backend
cp .env.example .env          # then edit .env: URL, both keys, Helius/Jupiter keys

cd ../frontend/js
cp config.example.js config.js   # then edit: URL and PUBLISHABLE key only
```

`.env` and `config.js` are git-ignored. **Never commit your secret key or put it in `config.js`.**

### 3. Run

```bash
./scripts/run-local.sh        # creates a venv, installs deps, starts all four processes
```

Open <http://localhost:8000>. Or run the pieces yourself from `backend/` with the venv active:

```bash
python ingest.py                          # fills the screener
python autoscan.py                        # scores new tokens
python -m uvicorn api.main:app --port 8080   # on-demand scans
python -m http.server 8000                # run this one inside frontend/
```

### 4. Verify your setup

```bash
cd backend && source .venv/bin/activate
python selfcheck.py                  # config, Supabase lockdown, RPC, data APIs, full scan of BONK
python selfcheck.py --address <mint> # also scan a specific token
python scan.py <mint>                # one-off scan from the command line (add --save to store it)
```

`selfcheck.py` confirms the database is locked down (the publishable key must not be able to write),
that every external API answers, and that a full scan runs. Failures print the reason; warnings mean
a feature will run in reduced mode (for example, no Jupiter key).

## Using it

- **Screener** (`/`): filter and sort new tokens; click a risk badge for the full report.
- **Scan** (`/scan.html`): paste an address. Tokens the screener already scored load instantly with no
  login. Anything else needs a free account (Sign up on the login page) and runs a fresh scan.
  Free accounts get 10 fresh scans per day (change it in the `plan_limits` table).

## Free-tier notes

- **Supabase 500 MB:** old tokens are pruned hourly (36 h) unless watchlisted. Raise `MIN_LIQUIDITY_USD`
  to store fewer junk tokens.
- **Supabase 5 GB egress:** the screener fetches named columns only and polls every 30 s, pausing in
  background tabs.
- **RPC credits:** a full scan makes roughly 10-15 RPC calls. Set `ENABLE_LP_CHECK=0` and/or
  `ENABLE_HONEYPOT_SIM=0` to cut that, or raise `AUTOSCAN_DELAY_S`.
- Supabase pauses idle free projects after a week; the workers' regular writes keep it active.

## Known limitations

- Pool layouts and program IDs (`backend/scanner/constants.py`, `lp.py`) come from public SDK sources
  and are validated at runtime, but programs upgrade. If the LP check reports "layout didn't match",
  that pool type needs an update: open an issue with the pool address.
- Locker program IDs may be incomplete; unrecognised lockers show up as "program-controlled".
- The Raydium CPMM pool-recorded LP supply offset is cross-checked against the LP mint's decimals but
  cannot be fully validated; treat CPMM "burned" results as estimates.
- No deployer history or bundle detection yet. Solana only.

## Development

```bash
cd backend
pip install -r requirements.txt -r requirements-dev.txt
python -m pytest -q
```

The test suite covers the scoring rules, the byte-level pool decoders (built from documented layouts),
LP burn/lock math, honeypot classification, the full scan pipeline against a fake chain, and the API
(auth, quotas, refunds, CORS, rate limits). External services are mocked, so run `selfcheck.py` to
test against the real network.

Adding a check: write a function in `scanner/checks.py` that returns result dicts
(`id`, `status`, `points`, `detail`), register it in `scanner/engine.py`, and add its `id` to
`MAX_POINTS` (`scoring.py`) and to `MAX_POINTS`/`LABELS` in `frontend/js/scan.js`.
A check that can't get its data must return `unknown`, never `pass`.

## Security

- Never commit `.env` or share your secret key. If it leaks, rotate it in Supabase immediately.
- Token names are untrusted input (scam tokens contain HTML): the frontend only uses `textContent`.
- Report vulnerabilities privately to the maintainers rather than in a public issue.

## License

MIT, see [LICENSE](LICENSE).
