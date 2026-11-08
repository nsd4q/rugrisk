import hashlib

from scanner import b58
from tests.fakes import LOCKER_PDA, RAYDIUM_AUTH, WSOL, wallet


def test_roundtrip_and_known_lengths():
    assert b58.decode(WSOL).rjust(32, b"\0")[-1] == 1          # WSOL mint ends ...0x01
    assert b58.encode(b58.decode(WSOL)) == WSOL
    assert b58.encode(bytes(32)) == "1" * 32
    for i in range(50):
        raw = hashlib.sha256(str(i).encode()).digest()
        assert b58.decode(b58.encode(raw)) == raw


def test_real_wallets_are_on_curve():
    for i in range(200):
        assert b58.address_on_curve(wallet(i)), i


def test_real_pdas_are_off_curve():
    # Raydium AMM authority and CPMM authority are program-derived addresses
    assert not b58.address_on_curve(RAYDIUM_AUTH)
    assert not b58.address_on_curve(LOCKER_PDA)


def test_garbage_is_safe():
    assert not b58.address_on_curve("0OIl-not-base58")
    assert not b58.is_on_curve(b"short")
