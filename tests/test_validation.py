import json
from datetime import date

from store_agent.app import build_app
from store_agent.clock import AdjustableClock
from store_agent.config import load_settings
from store_agent.models.fake import FakeProvider
from store_agent.models.types import ProviderResponse
from store_agent.runtime.evidence import EvidenceLedger
from store_agent.tools.semantic.contract import Period
from store_agent.validation.numeric import validate_text

from conftest import SAT_AFTERNOON, Chat

CATALOG = load_settings().catalog
PERIOD = Period(
    start=date(2026, 9, 25), end=date(2026, 9, 25), label="yesterday (Fri Sep 25)", comparison="last_year",
    comparison_start=date(2025, 9, 26), comparison_end=date(2025, 9, 26), comparison_label="the same day last year",
)


def ledger(**row):
    lg = EvidenceLedger(CATALOG)
    lg.add("test", {}, [row or {"sales": 184291.0, "sales_cmp": 171303.0, "sales_diff": 12988.0, "sales_pct": 7.58}], PERIOD)
    return lg


def check(text, lg=None, stores=frozenset({"042"})):
    return validate_text(text, lg or ledger(), stores)


def test_correct_rounded_numbers_pass():
    assert check("Sales were $184.3K, up 7.6% vs last year (+$13.0K).").passed
    assert check("Sales were $184K, up 7.58% on Fri Sep 25 vs Sep 26, 2025.").passed


def test_wrong_percentage_is_caught():
    r = check("Sales increased 17.6% vs last year.")
    assert not r.passed and "17.6%" in r.violations[0]


def test_wrong_direction_is_caught():
    r = check("Sales were down 7.6% vs last year.")
    assert not r.passed and "wrong direction" in r.violations[0]
    assert not check("Sales were $184.3K (-$13.0K).").passed


def test_out_of_scope_store_is_caught():
    assert not check("Store 017 sales were $184.3K.").passed


def test_last_year_mention_requires_a_last_year_comparison():
    lg = EvidenceLedger(CATALOG)
    lg.add("test", {}, [{"sales": 184291.0}], Period(start=date(2026, 9, 25), end=date(2026, 9, 25), label="yesterday"))
    assert not validate_text("Sales were $184.3K, better than last year.", lg, {"042"}).passed


def test_dates_must_come_from_retrieved_periods():
    assert not check("Sales on Thu Sep 24 were $184.3K.").passed


def test_times_counts_and_small_integers_are_ignored():
    r = check("Your top 5 departments arrive every weekday at 8:00 AM, starting Mon Sep 28 2026-09-28.", ledger(sales=1.0))
    assert r.violations == ["date 'Mon Sep 28' is not in the data that was retrieved"]  # only the date is checked


class LyingNarrator(FakeProvider):
    def _narrate(self, task):
        return ProviderResponse(text=task.context["draft"].replace("down", "up"))


class LyingAnalyst(FakeProvider):
    def _agent_step(self, task):
        resp = super()._agent_step(task)
        if resp.text:
            answer = json.loads(resp.text)
            answer["answer"] = "Bedroom sales fell 55.5% because of the weather."
            resp.text = json.dumps(answer)
        return resp


def test_general_answers_may_describe_capabilities_but_not_state_figures(chat):
    r, t = chat.ask("hello")
    assert t.capability == "GENERAL" and r.source == "general"
    assert "compared with last year" in r.text and t.validation["passed"]
    assert not validate_text("Your store did $184.3K.", EvidenceLedger(CATALOG), {"042"}, check_periods=False).passed


def test_bad_narration_falls_back_to_verified_draft(tmp_path):
    app = build_app(tmp_path / "t.db", AdjustableClock(SAT_AFTERNOON), providers={"fake": LyingNarrator()})
    chat = Chat(app)
    chat.ask("What were sales yesterday?")
    r, t = chat.ask("Compare that with last year.")
    assert "down" in r.text and r.status == "ok"  # verified draft, not the lie
    assert any("narration failed validation" in n for n in t.notes)
    assert len([m for m in t.model_calls if m.purpose == "narrate"]) == 2  # original + one retry


def test_unsupported_diagnosis_degrades_to_verified_numbers(tmp_path):
    app = build_app(tmp_path / "t.db", AdjustableClock(SAT_AFTERNOON), providers={"fake": LyingAnalyst()})
    r, t = Chat(app).ask("Why was Bedroom down yesterday?")
    assert r.status == "degraded"
    assert r.text.startswith("I couldn't finish the full analysis")
    assert "55.5%" not in r.text and t.validation["passed"]
