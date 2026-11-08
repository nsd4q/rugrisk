"""Static data for the scanner. Program IDs are data, not logic: extend freely.
Double-check IDs against Solscan before relying on a new one."""
import re

SOL_ADDR = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")

# Token accounts owned by these addresses are treated as burned supply.
BURN_OWNERS = {
    "1nc1nerator11111111111111111111111111111111",
    "11111111111111111111111111111111",
}

# Pool authority PDAs that own LP vault token accounts (not real holders).
LP_AUTHORITIES = {
    "5Q544fKrFoe6tsEbD7S8EmxGTJYAKtTVhAW5Q5pge4j1",  # Raydium AMM v4
    "GpMZbSM2GgvTKHJirzeGfMFoaZ8UR2X7F4v8vHTvxFbL",  # Raydium CPMM
}

# If the owner of a holder account is itself owned by one of these programs,
# the "holder" is a pool / bonding curve, not a person.
DEX_PROGRAMS = {
    "675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8",  # Raydium AMM v4
    "CPMMoo8L3F4NbTegBCKVNunggL7H1ZpdTHKxQB5qKP1C",  # Raydium CPMM
    "CAMMCzo5YL8w4VFF8KVHrK22GGUsp5VTaW7grrKgrWqK",  # Raydium CLMM
    "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P",   # pump.fun bonding curve
    "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA",   # PumpSwap
    "whirLbMiicVdio4qvUfM5KAg6Ct8VwpYzGff3uctyCc",   # Orca Whirlpool
    "LBUZKhRxPF3XUpBCjp4YzTKgLccjZhTSDM9YuVaPwxo",   # Meteora DLMM
    "Eo7WjKq67rjJQSZxS6z3YkapzY3eMj6Xy8X5EQVn5UaB",  # Meteora Dynamic AMM
    "cpamdpZCGKUy5JxQXB4dcpGPiikHawvSWAd6mEn1sGG",   # Meteora DAMM v2
}

SYSTEM_PROGRAM = "11111111111111111111111111111111"
WSOL = "So11111111111111111111111111111111111111112"

# Pool programs whose LP layout we can decode (see lp.py)
RAYDIUM_AMM_V4 = "675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8"
RAYDIUM_CPMM = "CPMMoo8L3F4NbTegBCKVNunggL7H1ZpdTHKxQB5qKP1C"
PUMPSWAP = "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA"
PUMPFUN_CURVE = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"

# Programs that custody LP tokens for a lock period. Verify IDs on Solscan; a wrong
# ID only means a locker goes unrecognised (shown as "program-controlled").
LOCKER_PROGRAMS = {
    "LockrWmn6K5twhz3y9w1dQERbmgSaRkfnTeTKbpofwE": "Raydium LP Locker",
    "strmRqUCoQUgGUan5YhzUZa6KqdzwX5L6FpUxfmKg5m": "Streamflow",
    "LocpQgucEQHbqNABEYvBvwoxCPsSbG91A1QaQhQQqjn": "Jupiter Lock",
}

# ---- scoring thresholds (tweak after testing against real rugs) ----
TOP10_START_PCT = 40      # concentration scoring starts here...
TOP10_FULL_PCT = 80       # ...and reaches max points here
LARGEST_HOLDER_PCT = 15
MIN_LIQUIDITY_USD = 5000
MIN_AGE_MINUTES = 10
HIGH_FEE_BPS = 500        # 5% transfer fee


def short(addr: str) -> str:
    return addr if len(addr) < 12 else f"{addr[:4]}…{addr[-4:]}"


# LP verdict thresholds (percent of the pool's original LP supply)
LP_SECURE_PCT = 90        # burned/locked share needed for a clean result
LP_WALLET_FAIL_PCT = 50   # share held by ordinary wallets that counts as "can rug"
