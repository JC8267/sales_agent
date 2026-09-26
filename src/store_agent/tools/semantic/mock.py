"""Deterministic synthetic semantic layer for local development and tests.

Numbers are generated from (store, date, department) seeds so every run agrees. A
scenario plants a recent Bedroom decline (fewer transactions, low availability) so the
diagnostic path has something real to find. Department transactions count receipts that
include that department, so they sum to more than store transactions.
"""

import random
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from zoneinfo import ZoneInfo

from store_agent.clock import Clock
from store_agent.config import SemanticCatalog
from store_agent.formatting import day
from store_agent.tools.semantic.calendar import resolve_period
from store_agent.tools.semantic.contract import (
    DataNotAvailable,
    SemanticQuery,
    SemanticResult,
    UnsupportedQuery,
)

# share of store sales, avg department basket ($), avg unit price ($), yearly growth
DEPT_PROFILE = {
    "Living Room": (0.19, 310, 95, 0.05),
    "Bedroom": (0.15, 240, 60, 0.06),
    "Kitchens": (0.14, 520, 40, 0.07),
    "Storage": (0.10, 70, 18, 0.02),
    "Textiles": (0.07, 45, 12, 0.03),
    "Decoration": (0.06, 35, 9, 0.04),
    "Lighting": (0.05, 48, 22, 0.03),
    "Dining": (0.06, 190, 45, 0.04),
    "Workspace": (0.06, 150, 55, 0.05),
    "Bathroom": (0.05, 85, 20, 0.03),
    "Children's": (0.04, 60, 14, 0.02),
    "Outdoor": (0.03, 140, 35, -0.02),
}
STORE_BASE_SALES = {"042": 170_000, "017": 140_000, "210": 190_000}
STORE_AOV = 86.0
DOW_FACTOR = [0.82, 0.78, 0.84, 0.92, 1.05, 1.38, 1.21]
HOUR_SHARE = dict(zip(range(10, 22), [0.04, 0.07, 0.09, 0.10, 0.10, 0.10, 0.10, 0.10, 0.10, 0.09, 0.07, 0.04]))
TREND_REF = date(2025, 1, 1)


@dataclass(frozen=True)
class Scenario:
    department: str = "Bedroom"
    first_day_back: int = 1  # relative to the store-local "today"
    last_day_back: int = 4
    sales_factor: float = 0.78  # via fewer transactions; basket size unchanged
    availability: float = 81.0


@dataclass
class _DeptDay:
    sales: float
    units: float
    transactions: float
    availability: float


class MockSemanticLayer:
    source = "mock"

    def __init__(self, catalog: SemanticCatalog, clock: Clock, scenario: Scenario | None = Scenario()):
        unknown = set(catalog.department_names) - set(DEPT_PROFILE)
        if unknown:
            raise ValueError(f"mock has no profile for departments: {sorted(unknown)}")
        self.catalog = catalog
        self.clock = clock
        self.scenario = scenario

    # -- public -----------------------------------------------------------------
    def today(self, store_id: str) -> date:
        tz = self.catalog.stores[store_id].timezone
        return self.clock.now().astimezone(ZoneInfo(tz)).date()

    def query(self, q: SemanticQuery) -> SemanticResult:
        if q.store_id not in self.catalog.stores:
            raise UnsupportedQuery(f"Unknown store {q.store_id}.")
        for m in q.metrics:
            if m not in self.catalog.metrics:
                raise UnsupportedQuery(f"Unknown metric '{m}'.")
        if "availability" in q.metrics and "hour" in q.dimensions:
            raise UnsupportedQuery("Availability isn't tracked by hour.")

        today = self.today(q.store_id)
        period = resolve_period(q.time_range, q.comparison, today, self.catalog)
        latest = today - timedelta(days=self.catalog.data_lag_days)
        if period.end > latest:
            raise DataNotAvailable(f"Sales data is only available through {day(latest)}.")

        dates = _dates(period.start, period.end)
        current = self._aggregate(q, dates, today, shift=0)
        rows = []
        if q.comparison:
            shift = (period.start - period.comparison_start).days
            previous = self._aggregate(q, _dates(period.comparison_start, period.comparison_end), today, shift=shift)
        for key, values in current.items():
            row = dict(zip(q.dimensions, key))
            for m in q.metrics:
                row[m] = values[m]
                if q.comparison:
                    c = previous.get(key, {}).get(m)
                    row[f"{m}_cmp"] = c
                    row[f"{m}_diff"] = None if c is None else round(values[m] - c, 2)
                    pct_ok = c not in (None, 0) and self.catalog.metrics[m].unit != "percent"
                    row[f"{m}_pct"] = round((values[m] / c - 1) * 100, 2) if pct_ok else None
            rows.append(row)

        if q.order_by:
            present = [r for r in rows if r.get(q.order_by) is not None]
            missing = [r for r in rows if r.get(q.order_by) is None]
            rows = sorted(present, key=lambda r: r[q.order_by], reverse=q.descending) + missing
        if q.limit:
            rows = rows[: q.limit]
        return SemanticResult(query=q, period=period, rows=rows, source=self.source)

    # -- generation -------------------------------------------------------------
    def _aggregate(self, q: SemanticQuery, dates: list[date], today: date, shift: int) -> dict[tuple, dict]:
        depts = q.filters.get("department") or list(DEPT_PROFILE)
        dept_level_txn = "department" in q.dimensions or "department" in q.filters
        acc: dict[tuple, dict] = defaultdict(lambda: defaultdict(float))
        for d in dates:
            shares = self._hour_shares(q.store_id, d) if "hour" in q.dimensions else {None: 1.0}
            key_date = (d + timedelta(days=shift)).isoformat()
            for h, share in shares.items():
                dims = {"hour": h, "date": key_date}
                for dept in depts:
                    f = self._dept_day(q.store_id, d, dept, today)
                    key = tuple(dept if dim == "department" else dims[dim] for dim in q.dimensions)
                    a = acc[key]
                    a["sales"] += f.sales * share
                    a["units"] += f.units * share
                    a["avail_sum"] += f.availability
                    a["avail_n"] += 1
                    if dept_level_txn:
                        a["transactions"] += f.transactions * share
                if not dept_level_txn:
                    key = tuple(dims[dim] for dim in q.dimensions)
                    acc[key]["transactions"] += self._store_transactions(q.store_id, d, today) * share

        out = {}
        for key, a in acc.items():
            sales = round(a["sales"], 2)
            txn = int(round(a["transactions"]))
            out[key] = {
                "sales": sales,
                "transactions": txn,
                "units": int(round(a["units"])),
                "aov": round(sales / txn, 2) if txn else None,
                "availability": round(a["avail_sum"] / a["avail_n"], 1) if a["avail_n"] else None,
            }
        return out

    def _dept_day(self, store: str, d: date, dept: str, today: date) -> _DeptDay:
        share, basket, unit_price, growth = DEPT_PROFILE[dept]
        rng = random.Random(f"{store}|{d.isoformat()}|{dept}")
        base = STORE_BASE_SALES.get(store, 150_000) * share * DOW_FACTOR[d.weekday()]
        trend = (1 + growth) ** ((d - TREND_REF).days / 364)
        sales = base * trend * min(1.15, max(0.85, 1 + rng.gauss(0, 0.05)))
        transactions = sales / (basket * (1 + rng.gauss(0, 0.03)))
        availability = min(99.5, max(88.0, 95.5 + rng.gauss(0, 1.0)))
        s = self.scenario
        if s and dept == s.department and s.first_day_back <= (today - d).days <= s.last_day_back:
            sales *= s.sales_factor
            transactions *= s.sales_factor
            availability = s.availability + rng.gauss(0, 0.5)
        units = sales / (unit_price * (1 + rng.gauss(0, 0.03)))
        return _DeptDay(sales, units, transactions, availability)

    def _store_transactions(self, store: str, d: date, today: date) -> float:
        rng = random.Random(f"{store}|{d.isoformat()}|store-aov")
        total = sum(self._dept_day(store, d, dept, today).sales for dept in DEPT_PROFILE)
        return total / (STORE_AOV * (1 + rng.gauss(0, 0.02)))

    def _hour_shares(self, store: str, d: date) -> dict[int, float]:
        rng = random.Random(f"{store}|{d.isoformat()}|hours")
        raw = {h: s * (1 + rng.gauss(0, 0.08)) for h, s in HOUR_SHARE.items()}
        total = sum(raw.values())
        return {h: v / total for h, v in raw.items()}


def _dates(start: date, end: date) -> list[date]:
    return [start + timedelta(days=i) for i in range((end - start).days + 1)]
