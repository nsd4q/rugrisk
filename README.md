# RugRisk

A self-hosted **Solana memecoin screener and rug-risk scanner**. Run it on your own machine:

- **Screener**: live table of Solana tokens with filters for **min age** (5m to 1 week), **market cap** range, liquidity, volume and buy %.
- **Watchlist**: star any token (from the Screener or the Scan page); starred tokens are kept and refreshed continuously.
- **Risk scanner**: click **Scan** on a row, or paste any token address, to get a 0-100 risk score with plain-English reasons. Scans run on demand.
- **Runs locally, no accounts**: everything runs on your own computer with a free Supabase project and free API keys. There is no login and no hosted service.

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
your browser (http://localhost:8000)
   |  reads (publishable key, read-only)             +-- ingest.py    finds new pairs, keeps prices fresh
   v                                                 |   (autoscan.py: optional background scorer)
Supabase (free project: Postgres + RLS) <--writes----+
   ^
   |  POST /scan, POST/DELETE /watchlist (no login, localhost only)
Python API (FastAPI, localhost:8080) --> Solana RPC, DexScreener, Jupiter
```

- The browser only ever holds the **publishable** key; row-level security makes it read-only.
- The **secret** key lives in `backend/.env` and is never sent to the browser.
- The scan API has **no login**: it is designed to listen on `localhost` only. **Never expose
  ports 8000 or 8080 to the internet** (no port forwarding, tunnels or `--host 0.0.0.0`).

## Setup (about 15 minutes)

You need: Python 3.10+, a free [Supabase](https://supabase.com) project, and (recommended) free keys
from [Helius](https://helius.dev) and [Jupiter](https://portal.jup.ag).

### 1. Database

In Supabase: **SQL Editor > New query**, paste the contents of `supabase/migrations/001_init.sql`
and **Run**. It is safe to re-run. (If you see a pg_cron notice, enable the `pg_cron` extension
under Database > Extensions and re-run section 7 of the file; it prunes old data hourly.)

Upgrading from an earlier version? Run the new file again: it is safe to repeat and adds the
watchlist column and the 8-day retention. If that version had user accounts, you can optionally run
`supabase/optional/remove_accounts.sql` to remove the unused account tables.

### 2. Keys

From **Settings > API Keys** copy your project URL, the **publishable** key and the **secret** key.

```bash
cd backend
cp .env.example .env             # then edit .env: project URL + SECRET key (+ Helius/Jupiter keys)

cd ../frontend/js
cp config.example.js config.js   # then edit: project URL + PUBLISHABLE key only
```

`.env` and `config.js` are git-ignored. **Never commit your secret key or put it in `config.js`.**

### 3. Install dependencies

From the project root (creates a private virtual environment in `backend/.venv`):

```bash
# macOS / Linux
python3 -m venv backend/.venv
backend/.venv/bin/python -m pip install -r backend/requirements.txt

# Windows (PowerShell)
python -m venv backend\.venv
.\backend\.venv\Scripts\python -m pip install -r backend\requirements.txt
```

### 4. Verify your setup

```bash
backend/.venv/bin/python backend/selfcheck.py                    # Windows: .\backend\.venv\Scripts\python backend\selfcheck.py
backend/.venv/bin/python backend/selfcheck.py --address <mint>   # also scan a specific token
backend/.venv/bin/python backend/scan.py <mint>                  # one-off scan (add --save to store it)
```

`selfcheck.py` confirms the database is locked down (the key in `frontend/js/config.js` must not be
able to write), that every external API answers, and that a full scan runs. Failures print the
reason; warnings mean a feature will run in reduced mode (for example, no Jupiter key).

### 5. Run

```bash
python3 scripts/run_local.py     # Windows: python scripts\run_local.py   (works on every OS)
```

Open <http://localhost:8000>. Press Ctrl+C to stop. (`scripts/run-local.sh` does the same on
macOS/Linux.) The processes can also be started separately from `backend/`: `ingest.py`,
`python -m uvicorn api.main:app --port 8080`, and `python -m http.server 8000 --bind 127.0.0.1`
inside `frontend/`.

The **background risk scorer** (`autoscan.py`) is optional and off by default: the screener no longer
shows risk, and scoring every new token uses up your RPC quota. Add `--autoscan` to the start command
if you want every new token scored in the background.

Step-by-step guides for Windows, macOS and Linux (including editing the two settings files in
VS Code or VSCodium) are in [`docs/`](docs/): **RugRisk-Install-and-Run-Guide.docx** and
**RugRisk-GitHub-Publishing-Guide.docx**.

## Using it

- **Screener** (`/`): filter and sort tokens.
  - **Min age**: Any, 5 min, 15 min, 1 hour, 6 hours, 24 hours or 1 week. It hides tokens *younger* than
    the value, so "24 hours" shows tokens that have lasted at least a day.
  - **Market cap**: a min and/or max in dollars. Tokens with no known market cap are hidden while it is set.
  - Plus min liquidity, min 1h volume, min buy % and sorting, with one-click presets.
  - Click the **star** on a row to add it to your watchlist; click **Scan** for a risk report.
- **Watchlist** (`/watchlist.html`): every starred token in one table. Starred tokens are never deleted by
  the cleanup and their prices are refreshed every round. Click a star to remove a token.
- **Scan** (`/scan.html`): paste an address, or arrive from a screener row's **Scan** link (which starts a
  scan automatically if there is no recent report). Recent results are reused for 3 minutes. You can add
  the token to your watchlist from this page too.

Stars are saved by the local API into your own Supabase project, so the browser itself stays read-only.

## Free-tier notes

- **Supabase 500 MB:** tokens are pruned hourly after about 8 days (starred ones are kept). Raise `MIN_LIQUIDITY_USD`
  to store fewer junk tokens.
- **Supabase 5 GB egress:** the screener fetches named columns only and polls every 30 s, pausing in
  background tabs. Running it just for yourself uses a tiny fraction of the quota.
- **RPC credits:** a full scan makes roughly 10-15 RPC calls and only happens when you ask for one. Set
  `ENABLE_LP_CHECK=0` and/or `ENABLE_HONEYPOT_SIM=0` to cut that.
- **DexScreener:** prices of tokens under 24 h old refresh every ~20 s; older tokens refresh in rotation
  (`REFRESH_OLD_PER_LOOP` per round, stalest first); starred tokens refresh every round.
- Supabase pauses idle free projects after a week; the workers' regular writes keep it active.

## Known limitations

- The 24 hour and 1 week age filters only list tokens RugRisk has been tracking since launch (it keeps them
  about 8 days), so they fill up as it runs. Prices of older tokens can lag by an hour or more when many
  thousands are tracked; star the ones you care about.
- A token DexScreener stops listing for 24 h+ is shown with liquidity $0 (it no longer trades).
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
LP burn/lock math, honeypot classification, the full scan pipeline against a fake chain, the API
(caching, error codes, CORS, rate limit), and the self-check script. External services are mocked, so run `selfcheck.py` to
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
