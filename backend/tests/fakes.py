"""Test doubles + builders for on-chain account bytes (layouts verified against public SDK sources)."""
import base64

from scanner import b58
from scanner.constants import (
    PUMPSWAP, RAYDIUM_AMM_V4, RAYDIUM_CPMM, PUMPFUN_CURVE, SYSTEM_PROGRAM,
)

TOKEN = "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263"   # BONK mint (valid base58 shape)
WSOL = "So11111111111111111111111111111111111111112"
LPMINT = "LPmint1111111111111111111111111111111111111"[:43]
POOL = "Pool11111111111111111111111111111111111111"[:43]
RAYDIUM_AUTH = "5Q544fKrFoe6tsEbD7S8EmxGTJYAKtTVhAW5Q5pge4j1"   # a real PDA
LOCKER_PDA = "GpMZbSM2GgvTKHJirzeGfMFoaZ8UR2X7F4v8vHTvxFbL"     # a real PDA (CPMM authority)


def wallet(i: int) -> str:
    """Deterministic, real on-curve ed25519 address."""
    import hashlib
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives import serialization as ser
    seed = hashlib.sha256(f"wallet-{i}".encode()).digest()
    pub = Ed25519PrivateKey.from_private_bytes(seed).public_key().public_bytes(
        ser.Encoding.Raw, ser.PublicFormat.Raw)
    return b58.encode(pub)


def pk(addr: str) -> bytes:
    return b58.decode(addr).rjust(32, b"\0")


def pumpswap_pool(base=TOKEN, quote=WSOL, lp_mint=LPMINT, lp_supply=1_000_000):
    d = bytearray(244)
    d[11:43] = pk("1" * 32)
    d[43:75] = pk(base)
    d[75:107] = pk(quote)
    d[107:139] = pk(lp_mint)
    d[203:211] = lp_supply.to_bytes(8, "little")
    return bytes(d)


def raydium_v4_pool(coin=TOKEN, pc=WSOL, lp_mint=LPMINT, lp_amount=1_000_000):
    d = bytearray(752)
    d[400:432] = pk(coin)
    d[432:464] = pk(pc)
    d[464:496] = pk(lp_mint)
    d[720:728] = lp_amount.to_bytes(8, "little")
    return bytes(d)


def cpmm_pool(mint0=WSOL, mint1=TOKEN, lp_mint=LPMINT, lp_supply=1_000_000, lp_decimals=9):
    d = bytearray(637)
    d[136:168] = pk(lp_mint)
    d[168:200] = pk(mint0)
    d[200:232] = pk(mint1)
    d[330] = lp_decimals
    d[336:344] = lp_supply.to_bytes(8, "little")
    return bytes(d)


def token_acct(owner):
    return {"data": {"parsed": {"info": {"owner": owner}}}}


class FakeRpc:
    """Minimal in-memory Solana RPC. `mints` maps address -> parsed mint info;
    `largest` maps mint -> [(token_account, owner, amount)]; `accounts` maps owner -> program."""
    is_helius = False

    def __init__(self):
        self.mints, self.largest, self.owner_program, self.raw = {}, {}, {}, {}
        self.balances, self.sim = {}, {"err": None, "logs": []}
        self.sim_calls = 0

    def get_mint(self, a):
        if a not in self.mints:
            raise RuntimeError("Account not found on-chain")
        return self.mints[a], "spl-token"

    def get_raw_account(self, a):
        return self.raw.get(a)

    def largest_accounts(self, mint):
        return [{"address": ta, "amount": str(amt)} for ta, _, amt in self.largest.get(mint, [])]

    def multiple_accounts(self, addrs):
        out = []
        for a in addrs:
            hit = next(((o) for m in self.largest.values() for ta, o, _ in m if ta == a), None)
            if hit:
                out.append(token_acct(hit))
            elif a in self.owner_program:
                out.append({"owner": self.owner_program[a]})
            elif b58.address_on_curve(a):
                out.append({"owner": SYSTEM_PROGRAM})   # ordinary wallet
            else:
                out.append(None)                          # PDA with no account data
        return out

    def get_balance(self, a):
        return self.balances.get(a, 5_000_000_000)

    def simulate(self, tx):
        self.sim_calls += 1
        return self.sim

    def get_asset(self, a):
        return {"mutable": False}
