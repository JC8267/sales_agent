"""Router evaluation over evals/datasets/router_cases.jsonl.

Runs each case through the real runtime (mock data, fake models) so routing is measured
with slot extraction, conversation context, and authorization in the loop.
Usage: uv run python evals/run_router_eval.py [--as u-anna] [--json]
"""

import argparse
import json
import statistics
import sys
from datetime import datetime
from pathlib import Path

from store_agent.app import build_app
from store_agent.channels.console import ConsoleChannel
from store_agent.clock import AdjustableClock
from store_agent.contracts import TIER_ORDER, Tier

DATASET = Path(__file__).parent / "datasets" / "router_cases.jsonl"
EVAL_NOW = datetime.fromisoformat("2026-09-26T14:00:00-04:00")


def load_cases(path: Path = DATASET) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def evaluate(cases: list[dict], user: str = "u-anna") -> dict:
    results = []
    for case in cases:
        app = build_app(":memory:", AdjustableClock(EVAL_NOW))
        channel = ConsoleChannel(app.clock, app.refs)
        for msg in case.get("history", []):
            app.runtime.handle(channel.to_request(user, msg))
        response = app.runtime.handle(channel.to_request(user, case["query"]))
        trace = app.traces.get(response.request_id)
        tier = Tier(trace.reasoning_tier) if trace.reasoning_tier else Tier.NONE
        lo = Tier(case.get("minimum_reasoning_tier", "NONE"))
        hi = Tier(case.get("maximum_reasoning_tier", "DEEP"))
        results.append(
            {
                "category": case["category"],
                "query": case["query"],
                "expected": case["expected_capability"],
                "actual": trace.capability,
                "tier": tier.value,
                "capability_ok": trace.capability == case["expected_capability"],
                "tier_ok": TIER_ORDER.index(lo) <= TIER_ORDER.index(tier) <= TIER_ORDER.index(hi),
                "unnecessary_deep": tier == Tier.DEEP and hi != Tier.DEEP,
                "too_weak": TIER_ORDER.index(tier) < TIER_ORDER.index(lo),
                "router": trace.router,
                "confidence": trace.router_confidence,
                "router_ms": trace.router_latency_ms or 0.0,
                "cost_usd": trace.cost_usd,
            }
        )
    n = len(results)
    lat = sorted(r["router_ms"] for r in results)
    return {
        "cases": n,
        "capability_accuracy": sum(r["capability_ok"] for r in results) / n,
        "tier_accuracy": sum(r["tier_ok"] for r in results) / n,
        "unnecessary_deep_rate": sum(r["unnecessary_deep"] for r in results) / n,
        "incorrect_fast_rate": sum(r["too_weak"] for r in results) / n,
        "router_latency_ms_p50": statistics.median(lat),
        "router_latency_ms_p95": lat[min(n - 1, int(0.95 * n))],
        "cost_per_request_usd": sum(r["cost_usd"] for r in results) / n,
        "stage2_rate": sum(1 for r in results if r["router"] and r["router"] != "rules" and r["router"] != "policy") / n,
        "failures": [r for r in results if not (r["capability_ok"] and r["tier_ok"])],
    }


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except AttributeError:
        pass
    p = argparse.ArgumentParser()
    p.add_argument("--as", dest="user", default="u-anna")
    p.add_argument("--json", action="store_true")
    args = p.parse_args()
    report = evaluate(load_cases(), args.user)
    if args.json:
        print(json.dumps(report, indent=2))
        return
    for k, v in report.items():
        if k != "failures":
            print(f"{k:28s} {v:.3f}" if isinstance(v, float) else f"{k:28s} {v}")
    for f in report["failures"]:
        print(f"FAIL [{f['category']}] {f['query']!r}: expected {f['expected']}, got {f['actual']} @ {f['tier']} ({f['router']}, {f['confidence']})")


if __name__ == "__main__":
    main()
