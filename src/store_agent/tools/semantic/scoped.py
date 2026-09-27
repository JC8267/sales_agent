import time

from store_agent.config import SemanticCatalog
from store_agent.observability.tracing import ToolCallRecord, Trace
from store_agent.runtime.evidence import EvidenceLedger
from store_agent.security.authorization import AuthorizedScope, require_store
from store_agent.tools.semantic.contract import DataNotAvailable, SemanticLayer, SemanticLayerError, SemanticQuery, SemanticResult, UnsupportedQuery


class SemanticTool:
    """The only path from the agent to the semantic layer for one request: enforces the
    caller's store scope, validates vocabulary, and records trace + evidence."""

    def __init__(self, layer: SemanticLayer, catalog: SemanticCatalog, scope: AuthorizedScope, trace: Trace, ledger: EvidenceLedger):
        self.layer, self.catalog, self.scope, self.trace, self.ledger = layer, catalog, scope, trace, ledger

    def query(self, q: SemanticQuery, purpose: str) -> tuple[str, SemanticResult]:
        require_store(self.scope, q.store_id)
        bad_metrics = [m for m in q.metrics if m not in self.catalog.metrics]
        bad_depts = [d for d in q.filters.get("department", []) if d not in self.catalog.department_names]
        if bad_metrics or bad_depts:
            raise UnsupportedQuery(f"Unknown metric or department: {', '.join(bad_metrics + bad_depts)}")

        args = q.model_dump(mode="json")
        t0 = time.perf_counter()
        try:
            result = self.layer.query(q)
            if not result.rows or any(row.get(m) is None for row in result.rows for m in q.metrics):
                raise DataNotAvailable("Requested data is unavailable for this store and period.")
        except SemanticLayerError as e:
            self.trace.tool_calls.append(ToolCallRecord(name=purpose, args=args, latency_ms=_ms(t0), status="error", error=str(e)))
            raise
        eid = self.ledger.add(purpose, args, result.rows, result.period)
        self.trace.tool_calls.append(ToolCallRecord(name=purpose, args=args, latency_ms=_ms(t0), status="ok", evidence_id=eid))
        return eid, result


def _ms(t0: float) -> float:
    return round((time.perf_counter() - t0) * 1000, 2)
