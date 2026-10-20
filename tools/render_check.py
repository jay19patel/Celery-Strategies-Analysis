"""Capture a dashboard page and its API payloads, then run the page's own JavaScript over them.

    python -m tools.render_check                      # the Market page, against data/demo.db
    python -m tools.render_check --page system --db data/tradebuddy.db

Two bugs reached the browser that `node --check` and the test suite both passed: a local named
`history` that shadowed window.history, and an edit that deleted a function nothing called until
first paint. Each left every panel blank. This runs the page the way a browser would and prints how
many bytes each panel received, so an empty card shows up here instead of on the screen.

Needs node on PATH. It is a development tool and nothing in the image depends on it.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import pathlib
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
# What each page asks for. A page that fetches something not listed here fails loudly in the
# harness rather than silently rendering an empty panel.
PAGE_CALLS = {
    "market": [
        "/api/header", "/api/analysis", "/api/outlook?symbol=BTCUSD", "/api/verdicts?symbol=BTCUSD&days=30",
        "/api/bars?symbol=BTCUSD&interval=15m&days=3",
        "/api/options/history?symbol=BTCUSD&hours=24",
    ],
    "system": ["/api/metrics", "/api/database", "/api/health?hours=48", "/api/events?type=CandleClosed&limit=100"],
    "journal": ["/api/journal/month", "/api/journal/summary"],
}
PATHS = {"market": "/", "system": "/system", "journal": "/journal"}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--page", default="market", choices=sorted(PAGE_CALLS))
    parser.add_argument("--db", default="data/demo.db", help="database to render against")
    args = parser.parse_args()

    sys.path.insert(0, str(ROOT))
    from fastapi.testclient import TestClient

    from tests.conftest import FakeExchange, IdleStream
    from tradebuddy.app import local_app
    from tradebuddy.config import load_config
    from tradebuddy.strategies import discover
    from tradebuddy.system import System

    if not pathlib.Path(args.db).exists():
        print(f"no such database: {args.db} — run `python -m tradebuddy demo` first", file=sys.stderr)
        return 2

    pin = "242425"
    # A fixed secret: this signs a cookie for an in-process test client that never leaves the box.
    cfg = dataclasses.replace(load_config({"AUTH_PIN": pin}), db_path=args.db, auth_secret="render-check-secret")  # noqa: S106
    system = System(cfg, strategies=discover(), stream_factory=IdleStream, client_factory=FakeExchange().client)
    client = TestClient(local_app(system))
    client.post("/api/auth/login", json={"pin": pin})

    payloads = {}
    for url in PAGE_CALLS[args.page]:
        res = client.get(url)
        if res.status_code != 200:
            print(f"{url} -> {res.status_code}: {res.text[:120]}", file=sys.stderr)
            return 1
        payloads[url] = res.json()
    page = client.get(PATHS[args.page])
    if page.status_code != 200:
        print(f"{PATHS[args.page]} -> {page.status_code}", file=sys.stderr)
        return 1

    with tempfile.TemporaryDirectory() as tmp:
        html = pathlib.Path(tmp, "page.html")
        data = pathlib.Path(tmp, "payloads.json")
        html.write_text(page.text)
        data.write_text(json.dumps(payloads))
        print(f"rendering {args.page} against {args.db} ({len(payloads)} payloads)")
        return subprocess.run(  # noqa: S603 - fixed argv, paths we just wrote
            ["node", str(ROOT / "tools" / "render_check.js"), str(html), str(data)],  # noqa: S607 - node from PATH, a dev tool
            cwd=ROOT, check=False,
        ).returncode


if __name__ == "__main__":
    raise SystemExit(main())
