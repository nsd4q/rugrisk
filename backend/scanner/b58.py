"""Base58 + ed25519 on-curve test, pure Python (no Solana SDK needed)."""

ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
_INDEX = {c: i for i, c in enumerate(ALPHABET)}


def encode(b: bytes) -> str:
    n = int.from_bytes(b, "big")
    out = ""
    while n:
        n, r = divmod(n, 58)
        out = ALPHABET[r] + out
    pad = len(b) - len(b.lstrip(b"\0"))
    return "1" * pad + out


def decode(s: str) -> bytes:
    n = 0
    for c in s:
        n = n * 58 + _INDEX[c]
    body = n.to_bytes((n.bit_length() + 7) // 8, "big") if n else b""
    pad = len(s) - len(s.lstrip("1"))
    return b"\0" * pad + body


_P = 2**255 - 19
_D = (-121665 * pow(121666, _P - 2, _P)) % _P


def is_on_curve(pub: bytes) -> bool:
    """True if the 32 bytes decompress to an ed25519 point (a normal wallet key).
    Program-derived addresses (PDAs) are, by construction, NOT on the curve."""
    if len(pub) != 32:
        return False
    y = int.from_bytes(pub, "little") & ((1 << 255) - 1)
    if y >= _P:
        return False
    u = (y * y - 1) % _P
    v = (_D * y * y + 1) % _P
    x2 = (u * pow(v, _P - 2, _P)) % _P
    return x2 == 0 or pow(x2, (_P - 1) // 2, _P) == 1


def address_on_curve(addr: str) -> bool:
    try:
        return is_on_curve(decode(addr))
    except KeyError:
        return False
