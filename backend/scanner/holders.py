"""Shared helper: who holds how much of a mint (works for tokens and LP mints)."""
from .b58 import address_on_curve


def fetch_owner_balances(rpc, mint: str) -> list[dict]:
    """Top token accounts of `mint`, aggregated by owner wallet.
    Returns [{owner, amount (raw int), program (owner-of-owner or None), on_curve}], largest first.
    Only the 20 largest token accounts are visible via RPC."""
    top = rpc.largest_accounts(mint)
    if not top:
        return []

    accts = rpc.multiple_accounts([t["address"] for t in top])
    by_owner: dict[str, int] = {}
    for t, a in zip(top, accts):
        try:
            owner = a["data"]["parsed"]["info"]["owner"]
        except (TypeError, KeyError):
            owner = t["address"]  # couldn't resolve: treat the account itself as the holder
        by_owner[owner] = by_owner.get(owner, 0) + int(t["amount"])

    owners = list(by_owner)
    owner_accts = rpc.multiple_accounts(owners)  # which program owns each owner account

    out = []
    for owner, acct in zip(owners, owner_accts):
        out.append({
            "owner": owner,
            "amount": by_owner[owner],
            "program": acct.get("owner") if acct else None,
            "on_curve": address_on_curve(owner),
        })
    out.sort(key=lambda h: h["amount"], reverse=True)
    return out
