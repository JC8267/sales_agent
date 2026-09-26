"""Deterministic contribution math, so no model ever computes shares or deltas itself."""

from typing import Any


def with_contribution_shares(rows: list[dict[str, Any]], metric: str) -> list[dict[str, Any]]:
    """Adds share_of_losses_pct to declining rows and share_of_gains_pct to growing rows:
    each row's diff as a percent of the summed diffs with the same sign."""
    diff_key = f"{metric}_diff"
    losses = sum(r[diff_key] for r in rows if (r.get(diff_key) or 0) < 0)
    gains = sum(r[diff_key] for r in rows if (r.get(diff_key) or 0) > 0)
    out = []
    for r in rows:
        r = dict(r)
        d = r.get(diff_key) or 0
        if d < 0 and losses:
            r["share_of_losses_pct"] = round(d / losses * 100, 1)
        elif d > 0 and gains:
            r["share_of_gains_pct"] = round(d / gains * 100, 1)
        out.append(r)
    return out
