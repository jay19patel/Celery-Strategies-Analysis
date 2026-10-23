"""The written summary, and the boundary around it.

This is the only feature in the system that sends anything off the machine, so most of these tests
are about what it must NOT send and when it must NOT run. Nothing here touches the network: the
Mistral client is replaced, because a test that needs an API key is a test nobody runs.

The useful failure to keep in mind: none of this breaks loudly. A digest that quietly grows an
account balance still produces a perfectly good summary, and the only sign would be a field in
somebody else's logs.
"""

from __future__ import annotations

import json

import pytest

from tradebuddy import provenance, tbai
from tradebuddy.mistral import MistralError

ANSWER = {
    "headline": "BTC is grinding up into a wall of resting stops.",
    "state": "trending up",
    "summary": "Price is 1.2% up on the day with realised vol at 29%.",
    "levels": ["84,500 is the nearest pool, 3 highs stacked there"],
    "flow": "Buyers have the edge on estimated delta, which is approximate.",
    "watch": ["A close back under the point of control"],
    "confidence": "medium",
}


def context(**kw):
    return {"price": 84_000.0, "change_24h_pct": 1.2, "rv_24h": 0.29, "atr_pct": 0.3, **kw}


def structure(**kw):
    return {
        "ready": True,
        "nearby": {"above": [{"price": 84_500, "label": "3x highs", "kind_label": "resting stops",
                              "distance_pct": 0.6, "confluence": 2}],
                   "below": [], "inside": []},
        "profile": {"source": "1m", "session": {"poc": 83_900, "value_low": 83_000, "value_high": 84_200}},
        "flow": {"pressure": "buyers", "cvd_move_pct": 0.4},
        "liquidity": {"sweeps": [{"direction": "high", "level": 84_480, "bar_time": 1_700_000_000}]},
        **kw,
    }


# =====================================================================================
# what leaves the machine
# =====================================================================================


def test_the_digest_sends_only_the_fields_on_the_allowlist():
    """THE test. Every other guarantee here is a preference; this one is why the feature is allowed
    to exist at all.

    An allowlist, not a search for words like "position": `range_position_24h` is where price sits
    in the day's range and is perfectly fine to send, while `position_size` would not be. A word
    blocklist cannot tell those apart, so the gate is the set of keys itself."""
    out = tbai.digest("BTCUSD", context(), [], {"ready": True, "summary": []}, structure())
    assert set(out) <= tbai.ALLOWED, f"not on the allowlist: {sorted(set(out) - tbai.ALLOWED)}"
    assert "price" in out and "above" in out  # and it is not empty by accident


def test_a_field_added_to_the_structure_payload_does_not_leak_by_itself():
    """`digest` picks fields by name rather than forwarding payloads. Someone will add a field to
    `market_structure` one day, and it must not start leaving the machine because of that."""
    s = structure()
    s["account_balance"] = 91_234.5
    s["nearby"]["above"][0]["my_position_size"] = 7
    out = tbai.digest("BTCUSD", context(), [], None, s)
    blob = json.dumps(out)
    assert set(out) <= tbai.ALLOWED
    assert "91234" not in blob and "account_balance" not in blob
    assert "my_position_size" not in blob


def test_the_digest_survives_every_input_being_missing():
    """The analyst, the outlook and the structure scan each fail independently. None of them
    failing should throw here — it should mean a thinner digest and a model told it is thin."""
    out = tbai.digest("BTCUSD")
    assert out["symbol"] == "BTCUSD"
    assert out["price"] is None
    assert "above" not in out  # nothing claimed about structure that was never read
    json.dumps(out)  # and it is still serialisable


def test_order_flow_is_handed_over_marked_as_an_estimate():
    """It is inferred from candles, not read off a tape. A model given it unmarked will write about
    it as though it were measured."""
    out = tbai.digest("BTCUSD", context(), [], None, structure())
    assert out["order_flow"]["approx"] is True
    assert "tape" in out["order_flow"]["note"]


# =====================================================================================
# what comes back
# =====================================================================================


def test_the_answer_is_cut_down_to_the_shape_asked_for():
    """A small model adds keys, drops keys and occasionally writes an essay. None of that reaches a
    page otherwise made of checkable numbers."""
    out = tbai.clean({**ANSWER, "extra": "ignore me", "summary": "x" * 5000,
                      "levels": ["a", "b", "c", "d", "e"], "watch": "not a list"})
    assert set(out) == {"headline", "state", "summary", "levels", "flow", "watch", "confidence"}
    assert len(out["summary"]) <= 800
    assert len(out["levels"]) == 3
    assert out["watch"] == []


@pytest.mark.parametrize("value", ["bullish", "", None, 42, "TRENDING UP"])
def test_an_unknown_state_becomes_unclear_rather_than_reaching_the_page(value):
    assert tbai.clean({**ANSWER, "state": value})["state"] == "unclear"


@pytest.mark.parametrize("value", ["certain", "", None, 9])
def test_an_unknown_confidence_falls_to_low(value):
    """Falling to "low" rather than "high" is the whole point: a malformed answer must not read as
    a confident one."""
    assert tbai.clean({**ANSWER, "confidence": value})["confidence"] == "low"


def test_an_empty_answer_produces_an_empty_report_not_a_crash():
    out = tbai.clean({})
    assert out["headline"] == "" and out["state"] == "unclear" and out["confidence"] == "low"


# =====================================================================================
# when it runs, and when it must not
# =====================================================================================


async def test_it_does_nothing_when_switched_off(cfg, exchange, monkeypatch):
    from tests.test_pipeline import make

    system = await make(cfg, exchange)
    called = []
    monkeypatch.setattr("tradebuddy.mistral.complete", lambda *a, **k: called.append(a))
    assert system.settings.ai_enabled is False
    assert await system._write_summary("BTCUSD") is False or not called
    assert called == []


async def test_a_failed_attempt_is_stored_with_what_the_model_wrote(cfg, exchange, monkeypatch):
    """A summary that silently stops appearing is indistinguishable from a quiet market. The stored
    failure is the difference between "the key expired" and "it wrote prose instead of JSON"."""
    import dataclasses

    from tests.test_pipeline import make

    system = await make(dataclasses.replace(cfg, mistral_api_key="test-key"), exchange)
    await system.update_settings({"ai_enabled": True})
    system.analysis_latest["BTCUSD"] = {"context": context(bar_time=1_700_000_000), "insights": []}

    async def boom(*_a, **_k):
        raise MistralError("Mistral answered, but it was not the JSON asked for", 200, raw="Sure! Here is...")

    monkeypatch.setattr("tradebuddy.mistral.complete", boom)
    assert await system._write_summary("BTCUSD") is False

    [row] = system.store.ai_reports("BTCUSD")
    assert row["ok"] == 0
    assert "not the JSON" in row["error"]
    assert "Sure!" in row["raw"]
    assert system.store.latest_ai_report("BTCUSD") is None  # a failure is never shown as a summary


async def test_a_good_answer_is_cleaned_before_it_is_stored(cfg, exchange, monkeypatch):
    import dataclasses

    from tests.test_pipeline import make

    system = await make(dataclasses.replace(cfg, mistral_api_key="test-key"), exchange)
    await system.update_settings({"ai_enabled": True})
    system.analysis_latest["BTCUSD"] = {"context": context(bar_time=1_700_000_000), "insights": []}

    sent = {}

    async def answer(key, model, system_prompt, payload, **_k):
        sent["payload"] = payload
        sent["key"] = key
        return {**ANSWER, "extra": "dropped"}, {"model": model, "ms": 12, "usage": {"total_tokens": 300}}

    monkeypatch.setattr("tradebuddy.mistral.complete", answer)
    assert await system._write_summary("BTCUSD") is True

    stored = system.store.latest_ai_report("BTCUSD")
    assert "extra" not in stored["report"]
    assert stored["report"]["state"] == "trending up"
    assert stored["tokens"] == 300
    # And the thing that actually went out carried only allowlisted fields.
    assert set(sent["payload"]) <= tbai.ALLOWED


async def test_nothing_is_sent_for_a_symbol_with_no_measured_price(cfg, exchange, monkeypatch):
    """The model writes up numbers. With no numbers there is nothing to write up, and asking it
    anyway is how a page gets a confident paragraph about a market nobody measured."""
    import dataclasses

    from tests.test_pipeline import make

    system = await make(dataclasses.replace(cfg, mistral_api_key="test-key"), exchange)
    await system.update_settings({"ai_enabled": True})
    called = []
    monkeypatch.setattr("tradebuddy.mistral.complete", lambda *a, **k: called.append(a))
    assert await system._write_summary("BTCUSD") is False
    assert called == []


# =====================================================================================
# how the page is told what this is
# =====================================================================================


def test_the_written_source_is_the_weakest_and_says_why():
    legend = provenance.legend()
    assert [s["key"] for s in legend][-1] == provenance.WRITTEN  # ordered most dependable first
    written = provenance.of("written")
    assert written["badge"] == "WRITTEN"
    assert "Mistral" in written["depends_on"]
    assert "cannot be rechecked" in written["detail"]


def test_the_key_is_never_returned_by_an_endpoint(cfg, exchange):
    """Invariant 9. The key buys someone else's compute, and it lives in the environment precisely
    so that nothing can read it back out of the database."""
    import asyncio
    import dataclasses

    from tradebuddy.api import Api
    from tradebuddy.system import System

    from .conftest import IdleStream

    secret = "sk-do-not-leak"
    system = System(dataclasses.replace(cfg, mistral_api_key=secret), strategies=[],
                    stream_factory=IdleStream, client_factory=exchange.client)
    for view in (asyncio.run(Api(system).ai_report("BTCUSD")), asyncio.run(Api(system).settings())):
        assert secret not in json.dumps(view)
    assert asyncio.run(Api(system).ai_report("BTCUSD"))["has_key"] is True  # said, never shown
