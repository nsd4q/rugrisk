"""Risk score: 0-100, higher = riskier. Unknown checks add 0 points but lower `coverage`."""

# Max points each check can contribute (used for coverage, not for capping).
MAX_POINTS = {
    "mint_authority": 25,
    "freeze_authority": 20,
    "lp_status": 20,
    "honeypot_sim": 40,
    "top10_concentration": 20,
    "largest_holder": 10,
    "token_extensions": 30,
    "liquidity": 10,
    "metadata_mutable": 5,
    "token_age": 5,
}

LEVELS = [(75, "critical"), (50, "high"), (25, "medium"), (0, "low")]
ORDER = {"low": 0, "medium": 1, "high": 2, "critical": 3}


def level_for(score: int) -> str:
    for threshold, name in LEVELS:
        if score >= threshold:
            return name
    return "low"


def summarize(checks: list[dict]) -> dict:
    score = min(100, sum(c["points"] for c in checks))
    level = level_for(score)

    # A confirmed honeypot (sells blocked) is the worst case: force CRITICAL.
    blocked = any(c.get("severity") == "blocker" and c["status"] == "fail" for c in checks)
    if blocked:
        score, level = max(score, 75), "critical"

    # A failed "critical" check (active mint/freeze authority, permanent
    # delegate...) means at least HIGH, whatever the arithmetic says.
    if any(c.get("severity") == "critical" and c["status"] == "fail" for c in checks):
        if ORDER[level] < ORDER["high"]:
            level = "high"

    verified = sum(MAX_POINTS.get(c["id"], 0) for c in checks if c["status"] != "unknown")
    coverage = round(verified / sum(MAX_POINTS.values()), 2)
    unverified = [c["id"] for c in checks if c["status"] == "unknown"]
    return {"score": score, "level": level, "coverage": coverage, "unverified": unverified}
