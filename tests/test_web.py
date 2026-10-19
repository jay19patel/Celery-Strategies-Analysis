"""Pages, the settings API and broker routes. The lifespan (and so the stream) stays off."""

import dataclasses
import pathlib
import re
import time

import pytest
from fastapi.testclient import TestClient

from tradebuddy.app import PAGES, local_app
from tradebuddy.events import Tick
from tradebuddy.settings import LIVE_CONFIRM_PHRASE
from tradebuddy.system import System

from .conftest import IdleStream
from .test_pipeline import AlwaysBuy


def make(cfg, exchange, token=""):
    system = System(dataclasses.replace(cfg, api_token=token), strategies=[AlwaysBuy()], stream_factory=IdleStream, client_factory=exchange.client)
    return system, TestClient(local_app(system))


@pytest.mark.parametrize(("page_id", "path", "title"), [(p[0], p[1], p[3]) for p in PAGES])
def test_every_page_renders(cfg, exchange, page_id, path, title):
    _, client = make(cfg, exchange)
    res = client.get(path)
    assert res.status_code == 200
    assert f"<title>{title} · TradeBuddy</title>" in res.text
    assert 'class="nav-link active' in res.text


def test_static_assets_are_served(cfg, exchange):
    _, client = make(cfg, exchange)
    assert client.get("/static/js/app.js").status_code == 200
    assert client.get("/static/css/app.css").status_code == 200


def test_settings_api_masks_secrets(cfg, exchange):
    _, client = make(cfg, exchange)
    res = client.put("/api/settings", json={"changes": {"delta_api_key": "abcdefghijKEY9", "delta_api_secret": "topsecret"}})
    assert res.status_code == 200
    for body in (res.text, client.get("/api/settings").text):
        assert "topsecret" not in body and "abcdefghij" not in body


def test_settings_api_refuses_live_without_phrase(cfg, exchange):
    system, client = make(cfg, exchange)
    bad = client.put("/api/settings", json={"changes": {"delta_active": True, "delta_env": "live"}})
    assert bad.status_code == 400 and system.settings.active_brokers == ["paper"]
    ok = client.put("/api/settings", json={"changes": {"delta_active": True, "delta_env": "live"}, "confirm": LIVE_CONFIRM_PHRASE})
    assert ok.status_code == 200 and ok.json()["is_real_money"] is True


def test_token_protects_every_write(cfg, exchange):
    _, client = make(cfg, exchange, token="sekret")
    writes = [
        ("put", "/api/settings", {"changes": {"stop_loss_pct": 2}}),
        ("post", "/api/settings/clear-credentials", {}),
        ("post", "/api/settings/test-delta", {}),
        ("post", "/api/toggles", {"key": "trading:paper", "enabled": True}),
        ("post", "/api/close-all", {}),
        ("post", "/api/paper/reset", {}),
        ("post", "/api/positions/paper/BTCUSD/close", {}),
    ]
    for method, url, body in writes:
        assert getattr(client, method)(url, json=body).status_code == 401, url
    token = {"X-API-Token": "sekret"}
    assert client.put("/api/settings", json={"changes": {"stop_loss_pct": 2}}, headers=token).status_code == 200
    # Reads need it too. Keying the gate on the PIN alone meant AUTH_PIN="" with a token set served
    # the balance and every open position to anyone who asked.
    assert client.get("/api/header").status_code == 401
    assert client.get("/api/header", headers=token).status_code == 200


def test_broker_routes(cfg, exchange):
    _, client = make(cfg, exchange)
    assert client.get("/api/account?broker=paper").json()["broker"] == "paper"
    assert client.get("/api/account?broker=delta").status_code == 502  # no keys yet
    assert client.get("/api/positions?broker=binance").status_code == 404
    assert client.post("/api/positions/paper/BTCUSD/close").status_code == 502  # nothing open


async def test_close_position_route_closes_paper(cfg, exchange):
    system, client = make(cfg, exchange)
    system.bus.start()
    system.bus.publish(Tick(symbol="BTCUSD", price=100_000))
    await system.bus.drain()
    await system.paper.place_order("BTCUSD", "buy", 1, "c1")
    assert client.post("/api/positions/paper/BTCUSD/close").json()["closed"] == ["BTCUSD"]
    assert client.get("/api/positions?broker=paper").json() == []
    await system.bus.stop()


def test_market_data_is_the_home_page(cfg, exchange):
    """The market comes before anything done about it, and Monitoring leads the sidebar."""
    _, client = make(cfg, exchange)
    home = client.get("/").text
    assert "Market Data · TradeBuddy" in home
    assert home.index("Monitoring") < home.index("Portfolio")  # groups follow the order of PAGES
    assert "Account · TradeBuddy" in client.get("/account").text


def test_the_old_paths_still_lead_somewhere(cfg, exchange):
    """Bookmarks from before Market Data moved to the root, and from before Overview was removed."""
    _, client = make(cfg, exchange)
    for path, to in (("/market", "/"), ("/overview", "/"), ("/paper", "/account?broker=paper")):
        res = client.get(path, follow_redirects=False)
        assert res.status_code in (302, 307) and res.headers["location"] == to, path


async def test_inactive_broker_is_hidden(cfg, exchange):
    system, client = make(cfg, exchange)
    await system.update_settings({"delta_active": True, "paper_active": False})
    page = client.get("/positions").text
    assert 'data-broker="delta"' in page and 'data-broker="paper"' not in page
    assert 'href="/paper"' not in client.get("/").text
    assert [b["name"] for b in client.get("/api/header").json()["brokers"]] == ["delta"]


def test_header_lists_each_active_broker_with_its_switch(cfg, exchange):
    _, client = make(cfg, exchange)
    [paper] = client.get("/api/header").json()["brokers"]
    assert (paper["name"], paper["trading"], paper["not_ready"]) == ("paper", True, "")


def test_close_all_stops_every_broker(cfg, exchange):
    system, client = make(cfg, exchange)
    assert client.post("/api/close-all").json() == {"closed": [], "errors": []}
    assert system.trading_on("paper") is False and system.trading_on("delta") is False


def test_the_system_page_draws_process_load_and_event_rates(cfg, exchange):
    """A number stored and never drawn is a number nobody reads. The Processes table is one
    instant; these are the same three numbers over time, from process_health."""
    _, client = make(cfg, exchange)
    page = client.get("/system").text
    assert all(f'id="{c}"' in page for c in ("cpuChart", "memChart", "lagChart", "eventBars"))
    # The kit has to be parsed before the inline script, which draws on load.
    assert page.index("/static/js/chart.js") < page.index("loadHealth")
    assert client.get("/static/js/chart.js").status_code == 200


async def test_the_health_route_answers_the_system_page(cfg, exchange):
    system, client = make(cfg, exchange)
    now = time.time()
    for i in range(5):
        system.store.record_health("all", now - i * 300, 3.0 + i, 120.0, 1.5)
        system.store.record_health("analyst", now - i * 300, 9.0, 105.0, 7.0)
    body = client.get("/api/health?hours=48").json()
    assert set(body["processes"]) == {"all", "analyst"}
    assert [p["cpu_pct"] for p in body["processes"]["all"]] == [7.0, 6.0, 5.0, 4.0, 3.0]  # oldest first


def test_static_urls_carry_a_content_version(cfg, exchange):
    _, client = make(cfg, exchange)
    page = client.get("/market").text
    assert re.search(r'/static/js/app\.js\?v=[0-9a-f]{12}"', page) and re.search(r'/static/css/app\.css\?v=[0-9a-f]{12}"', page)


async def test_saved_ai_reports_open_by_id(cfg, exchange):
    from tradebuddy.events import AIReport

    system, client = make(cfg, exchange)
    await system.on_ai_report(AIReport(ok=True, model="ministral-3b-2512", report={"headline": "open me", "health": "good"}))
    [row] = client.get("/api/ai/history", params={"start": 0, "ok_only": True}).json()
    assert client.get(f"/api/ai/reports/{row['id']}").json()["report"]["headline"] == "open me"
    assert client.get(f"/api/ai/reports/{row['id'] + 1}").status_code == 404
    assert 'id="day"' in client.get("/ai").text


def test_the_risk_view_tells_the_dashboard_how_trades_are_sized(cfg, exchange):
    """The Overview explains the sizing rules in words; it must read them, not hard-code them."""
    _, client = make(cfg, exchange)
    risk = client.get("/api/risk").json()
    assert risk["sizing"]["risk_per_trade_pct"] > 0
    assert set(risk["sizing"]) == {"risk_per_trade_pct", "trade_margin_pct", "min_edge_multiple"}
    assert set(risk["protection"]) == {"atr_protection", "atr_stop_mult", "atr_target_mult", "stop_loss_pct", "take_profit_pct"}


# -- the dashboard has to fit on a phone ---------------------------------------------

TEMPLATES = pathlib.Path(__file__).resolve().parent.parent / "tradebuddy" / "templates"
APP_CSS = pathlib.Path(__file__).resolve().parent.parent / "tradebuddy" / "static" / "css" / "app.css"


def test_every_table_can_scroll_on_its_own():
    """`.tbl` cells are `white-space: nowrap`, so a table with no scrolling ancestor makes the whole
    page scroll sideways on a narrow screen. Measured at 320-1280px: every page fits."""
    loose = []
    for f in sorted(TEMPLATES.glob("*.html")):
        html = f.read_text()
        for m in re.finditer(r'<table class="tbl"', html):
            section = html[max(0, m.start() - 600):m.start()].split("<section")[-1]
            if 'class="scroll' not in section and "overflow-x-auto" not in section:
                loose.append(f"{f.name}:{html[:m.start()].count(chr(10)) + 1}")
    assert loose == [], f"tables with no scrolling ancestor: {loose}"


def test_no_table_is_wrapped_in_two_scroll_boxes():
    """A scroll box inside a scroll box gives two scrollbars on the same axis."""
    nested = []
    for f in sorted(TEMPLATES.glob("*.html")):
        html = f.read_text()
        for m in re.finditer(r'<div class="scroll[^"]*">', html):
            if re.match(r'\s*<div class="scroll', html[m.end():m.end() + 120]):
                nested.append(f"{f.name}:{html[:m.start()].count(chr(10)) + 1}")
    assert nested == [], f"nested scroll containers: {nested}"


def test_card_headers_cannot_force_the_page_wider():
    """A flex item defaults to min-width:auto, so a long title, subtitle or path would set the card's
    minimum width and push the page sideways instead of wrapping."""
    css = APP_CSS.read_text()
    head = next(line for line in css.splitlines() if line.startswith(".card-head {"))
    title = next(line for line in css.splitlines() if line.startswith(".card-title {"))
    sub = next(line for line in css.splitlines() if line.startswith(".card-sub {"))
    assert "flex-wrap: wrap" in head and "min-width: 0" in head
    assert "flex-wrap: wrap" in title and "min-width: 0" in title
    assert "overflow-wrap: anywhere" in sub and "min-width: 0" in sub


# Browser globals a page script must not shadow. Not every property of `window` — `status`, `name`
# and `event` are deprecated and are ordinary variable names — only the ones whose shadowing
# silently breaks something. `history` is here because it did: a local named `history` for the past
# readings turned `history.replaceState` in the tab switcher into a call on an array.
BROWSER_GLOBALS = {
    "history", "location", "document", "window", "navigator", "console", "screen", "parent", "top",
    "frames", "origin", "crypto", "performance", "localStorage", "sessionStorage", "fetch",
    "URL", "URLSearchParams", "Date", "Math", "JSON", "Promise", "Array", "Object",
}


def page_scripts() -> dict[str, str]:
    """The inline script of every template, by filename."""
    here = pathlib.Path(__file__).resolve().parent.parent / "tradebuddy" / "templates"
    return {
        path.name: "\n".join(re.findall(r"<script>(.*?)</script>", path.read_text(), re.S))
        for path in sorted(here.glob("*.html"))
    }


def _split_declarators(text: str) -> list[str]:
    """Split a declaration list on its own commas — not the ones inside `Math.min(a, b)`.

    A plain `split(",")` is what made the first attempt at this check useless: it saw a comma in
    every initialiser and called the next token a declaration, so half the pages "declared" Math.
    """
    out, buf, depth, quote = [], [], 0, ""
    for ch in text:
        if quote:
            buf.append(ch)
            if ch == quote:
                quote = ""
            continue
        if ch in "\"'`":
            quote = ch
        elif ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        if ch == "," and depth == 0:
            out.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
    out.append("".join(buf))
    return out


def _leading_group(text: str) -> str:
    """The balanced `{...}` or `[...]` the text starts with, and nothing after it."""
    depth = 0
    for i, ch in enumerate(text):
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
            if depth == 0:
                return text[: i + 1]
    return text


def declared_names(script: str) -> set[str]:
    """Every name a `let`/`const`/`var`/`function` binds.

    The commas are the whole point: the bug this guards against was `let outlook = null,
    record = {}, history = []`, where only the first name follows the keyword. A check that
    stopped at `outlook` would have passed the very line it exists to catch.
    """
    names: set[str] = set()
    for body in re.findall(r"\b(?:let|const|var)\s+([^;]{0,400})", script):
        for part in _split_declarators(body):
            target = part.split("=", 1)[0].strip()
            if target.startswith(("{", "[")):
                # Only the pattern itself. `for (const [k, v] of Object.entries(x))` has no `=`, so
                # the rest of the line rides along — and Object is not being declared here.
                names |= set(re.findall(r"([A-Za-z_$][\w$]*)", _leading_group(target)))
            elif (m := re.match(r"([A-Za-z_$][\w$]*)$", target)):
                names.add(m.group(1))
    names |= set(re.findall(r"\bfunction\s+([A-Za-z_$][\w$]*)", script))
    return names


@pytest.mark.parametrize("name", sorted(page_scripts()))
def test_no_page_script_shadows_a_browser_global(name):
    """Shadowing one of these fails at the call site, far from the declaration, and only when that
    path runs — so it reaches the browser rather than the test suite."""
    clashes = sorted(BROWSER_GLOBALS.intersection(declared_names(page_scripts()[name])))
    assert not clashes, f"{name} declares {clashes}, shadowing a browser global"


def test_the_shadowing_check_catches_the_bug_it_was_written_for():
    """The detector, against the exact line that broke the Market page's tabs — and against the
    shapes that made the first two attempts at this check useless."""
    broken = "let outlook = null, record = {}, history = [], structFrame = '15m', barsCache = {};"
    found = declared_names(broken)
    assert found == {"outlook", "record", "history", "structFrame", "barsCache"}
    assert BROWSER_GLOBALS & found == {"history"}
    # Destructuring binds just as thoroughly.
    assert "location" in declared_names("const { $, esc, location } = TB;")

    # And the three ways it cried wolf before: a comma inside an initialiser is not a declarator,
    # and `for (const [k, v] of Object.entries(x))` does not declare Object.
    for innocent in (
        "const WINDOW = 60, samples = [];",
        "const used = Math.min(100, r.used_pct || 0);",
        "const [a, risk] = await Promise.all([f(), g()]);",
        "const by = Object.fromEntries((l || []).map((v) => [v.key, v]));",
        "for (const [tab, key] of Object.entries(TAB_VERDICT)) {",
    ):
        assert not BROWSER_GLOBALS & declared_names(innocent), innocent


def page_locals(script: str) -> tuple[set[str], set[str]]:
    """(defined, called) for the page's own `render*` / `load*` helpers.

    Narrow on purpose. Those two prefixes are the convention for a page's own functions, so a call
    to one that nothing defines is always a bug — and it is the bug that actually happened: an edit
    that replaced one block silently took `renderSummary` with it, the page threw on first paint,
    and every panel stayed empty.
    """
    defined = set(re.findall(r"\bfunction\s+((?:render|load)[A-Za-z0-9_]*)", script))
    defined |= set(re.findall(r"\b(?:const|let|var)\s+((?:render|load)[A-Za-z0-9_]*)\s*=", script))
    # Not `TB.loadHeader()`: a method on the shared object is someone else's to define.
    called = set(re.findall(r"(?<![.\w])((?:render|load)[A-Za-z0-9_]*)\s*\(", script))
    return defined, called - defined


@pytest.mark.parametrize("name", sorted(page_scripts()))
def test_every_render_and_load_helper_a_page_calls_is_defined(name):
    _, missing = page_locals(page_scripts()[name])
    assert not missing, f"{name} calls {sorted(missing)}, which nothing defines"


def test_the_missing_helper_check_catches_the_bug_it_was_written_for():
    """The detector, against the shape of the edit that caused it."""
    broken = "function renderOutlook() { renderSummary(v); }"
    assert page_locals(broken)[1] == {"renderSummary"}
    whole = "function renderSummary(v) {} function renderOutlook() { renderSummary(v); }"
    assert page_locals(whole)[1] == set()
    # An arrow function assigned to a const counts as defined.
    assert page_locals("const loadThing = async () => {}; loadThing();")[1] == set()
    # A method on the shared TB object is someone else's to define, and is not a page helper.
    assert page_locals("await TB.loadHeader();")[1] == set()
