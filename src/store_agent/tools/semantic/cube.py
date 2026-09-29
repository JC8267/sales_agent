"""Opt-in Cube REST adapter; member names and credentials come from deployment config."""

import json
import math
import os
import time
from datetime import timedelta
from http.client import HTTPException
from urllib.error import URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
from zoneinfo import ZoneInfo

from store_agent.clock import Clock
from store_agent.config import CubeConfig, SemanticCatalog
from store_agent.tools.semantic.calendar import resolve_period
from store_agent.tools.semantic.contract import DataNotAvailable, SemanticLayerUnavailable, SemanticQuery, SemanticResult, UnsupportedQuery


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None  # Never forward the authorization header to a redirected endpoint.


class CubeSemanticLayer:
    source = "cube"

    def __init__(self, config: CubeConfig, catalog: SemanticCatalog, clock: Clock):
        url = urlsplit(config.api_url)
        if url.scheme != "https" or not url.hostname or url.username or url.password or url.query or url.fragment:
            raise ValueError("Cube api_url must be an HTTPS base URL without credentials, query, or fragment.")
        if not config.store_member.strip() or not config.time_member.strip() or not config.measures:
            raise ValueError("Cube requires store_member, time_member, and measure mappings.")
        if any(not value.strip() for value in [*config.measures.values(), *config.dimensions.values()]):
            raise ValueError("Cube member mappings must not be empty.")
        if not os.environ.get(config.token_env, "").strip():
            raise ValueError("Cube authorization environment variable is not set.")
        self.config, self.catalog, self.clock = config, catalog, clock
        self._opener = build_opener(_NoRedirect())

    def query(self, q: SemanticQuery) -> SemanticResult:
        if q.store_id not in self.catalog.stores:
            raise UnsupportedQuery("Unknown store.")
        if q.comparison or any(d != "department" for d in q.dimensions):
            # ponytail: defer comparison/calendar and time breakdowns until Cube definitions are confirmed.
            raise UnsupportedQuery("Cube comparisons and date/hour breakdowns are not configured yet.")
        if any(m not in self.catalog.metrics or m not in self.config.measures for m in q.metrics):
            raise UnsupportedQuery("Requested Cube measure is not configured.")
        if any(d != "department" or d not in self.config.dimensions for d in [*q.dimensions, *q.filters]):
            raise UnsupportedQuery("Requested Cube dimension is not configured.")
        if any(d not in self.catalog.department_names for d in q.filters.get("department", [])):
            raise UnsupportedQuery("Unknown department.")
        if q.order_by and q.order_by not in q.metrics:
            raise UnsupportedQuery("Cube ordering requires a requested metric.")
        if q.limit and q.limit > 10_000:
            raise UnsupportedQuery("Cube queries are limited to 10,000 rows.")

        timezone = self.catalog.stores[q.store_id].timezone
        today = self.clock.now().astimezone(ZoneInfo(timezone)).date()
        period = resolve_period(q.time_range, None, today, self.catalog)
        if period.end > today - timedelta(days=self.catalog.data_lag_days):
            raise DataNotAvailable("Requested period includes data that is not available yet.")
        filters = [{"member": self.config.store_member, "operator": "equals", "values": [q.store_id]}]
        for dimension, values in q.filters.items():
            if not values:
                raise UnsupportedQuery("Cube filters must contain at least one value.")
            filters.append({"member": self.config.dimensions[dimension], "operator": "equals", "values": values})
        query = {
            "measures": [self.config.measures[m] for m in q.metrics],
            "dimensions": [self.config.dimensions[d] for d in q.dimensions],
            "timeDimensions": [{"dimension": self.config.time_member, "dateRange": [period.start.isoformat(), period.end.isoformat()]}],
            "timezone": timezone,
            "filters": filters,
            "limit": q.limit or 10_001,
            "order": {self.config.measures[q.order_by]: "desc" if q.descending else "asc"} if q.order_by else {},
        }
        data = self._load(query)
        if len(data) > 10_000:
            raise DataNotAvailable("Cube result exceeds the row limit; narrow the request.")
        rows = []
        try:
            for item in data:
                row = {d: item[self.config.dimensions[d]] for d in q.dimensions}
                if any(not isinstance(v, str) or not v for v in row.values()):
                    raise ValueError("Invalid dimension")
                for metric in q.metrics:
                    raw = item[self.config.measures[metric]]
                    if raw is None:
                        raise DataNotAvailable("Requested Cube measure is unavailable.")
                    if isinstance(raw, bool) or not isinstance(raw, (str, int, float)):
                        raise ValueError("Invalid measure")
                    value = float(raw)
                    if not math.isfinite(value):
                        raise ValueError("Non-finite measure")
                    row[metric] = value
                rows.append(row)
        except (KeyError, TypeError, ValueError, OverflowError):
            raise SemanticLayerUnavailable("Cube returned an invalid result.") from None
        return SemanticResult(query=q, period=period, rows=rows, source=self.source)

    def _load(self, query: dict) -> list[dict]:
        token = os.environ.get(self.config.token_env, "").strip()
        if not token:
            raise SemanticLayerUnavailable("Cube authorization is unavailable.")
        request = Request(
            self.config.api_url.rstrip("/") + "/load",
            data=json.dumps({"query": query}).encode("utf-8"),
            headers={"Authorization": token, "Content-Type": "application/json"},
            method="POST",
        )
        try:
            for attempt in range(3):
                with self._opener.open(request, timeout=self.config.timeout_s) as response:
                    payload = json.load(response)
                if not isinstance(payload, dict):
                    break
                if payload.get("error") == "Continue wait":
                    if attempt < 2:
                        time.sleep(1)
                        continue
                    break
                if payload.get("error") or not isinstance(payload.get("data"), list):
                    break
                return payload["data"]
        except (URLError, OSError, HTTPException, ValueError):
            pass
        # Do not expose Cube error bodies, URLs, or authorization values to employees/traces.
        raise SemanticLayerUnavailable("Cube data service is unavailable or returned an invalid response.") from None
