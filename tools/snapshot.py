"""Photograph a dashboard page the way a browser actually draws it.

    python -m tools.snapshot --page market --db data/tradebuddy.db --out /tmp/market.png

`render_check` proves every panel received bytes, against a stub DOM. That is the right tool for
"did a loader throw", and the wrong one for "does it look right": it cannot run a canvas charting
library at all, and a screenshot of its output shows the stub rather than the chart.

So this does not use the stub. It takes the real page, the real scripts and the real vendored
library, swaps only `TB.get` for a lookup into payloads captured from the real API, and lets
Chrome run the lot. What comes out is the page, not an impression of it.

A development tool: it shells out to whatever Chrome is installed, and nothing in the image
depends on it.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
CHROME = [
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    shutil.which("google-chrome") or "", shutil.which("chromium") or "",
]

# Served from file://, so every fetch the page makes has to be answered from memory. Only `get` is
# replaced: everything else — the library, the renderers, the layout — is the real thing.
OFFLINE = """
<script>
(() => {
  const PAYLOADS = __PAYLOADS__;
  const wait = [];
  TB.get = (url) => {
    const hit = PAYLOADS[url] ?? PAYLOADS[url.split("?")[0]] ?? null;
    const p = Promise.resolve(hit ?? {});
    wait.push(p);
    if (hit === null) console.warn("no captured payload for", url);
    return p;
  };
  TB.post = async () => ({});
  TB.every = () => {};          // no polling in a photograph
  TB.on = () => {};             // no websocket
  window.__ready = () => Promise.all(wait);
})();
</script>
"""


def chrome() -> str:
    found = next((c for c in CHROME if c and pathlib.Path(c).exists()), None)
    if not found:
        sys.exit("no Chrome or Chromium found")
    return found


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--page", default="market")
    parser.add_argument("--db", default="data/demo.db")
    parser.add_argument("--out", default=str(pathlib.Path(tempfile.gettempdir()) / "dashboard.png"))
    parser.add_argument("--width", type=int, default=1500)
    parser.add_argument("--height", type=int, default=2000)
    parser.add_argument("--settle", type=int, default=6000, help="ms of virtual time to let the page finish")
    parser.add_argument("--html", action="store_true", help="keep the built HTML beside the png")
    args = parser.parse_args()

    sys.path.insert(0, str(ROOT))
    from tools.render_check import build

    page, payloads = build(args.page, args.db)

    # Absolute file URLs: the page asks for /static/..., which means nothing to file://.
    page = re.sub(r'(href|src)="/static/([^"?]+)(\?[^"]*)?"',
                  rf'\1="file://{ROOT}/tradebuddy/static/\2"', page)
    # The shim goes after app.js (which defines TB) and before the page's own inline script.
    marker = page.index("<script>", page.index("app.js"))
    page = page[:marker] + OFFLINE.replace("__PAYLOADS__", json.dumps(payloads)) + page[marker:]

    with tempfile.TemporaryDirectory() as tmp:
        built = pathlib.Path(args.out).with_suffix(".html") if args.html else pathlib.Path(tmp) / "page.html"
        built.write_text(page)
        subprocess.run(  # noqa: S603
            [chrome(), "--headless", "--disable-gpu", "--hide-scrollbars", "--allow-file-access-from-files",
             f"--screenshot={args.out}", f"--window-size={args.width},{args.height}",
             f"--virtual-time-budget={args.settle}", f"file://{built}"],
            check=False, capture_output=True,
        )
        out = pathlib.Path(args.out)
        if not out.exists():
            print("chrome produced no image", file=sys.stderr)
            return 1
        print(f"{out}  ({out.stat().st_size // 1024} KB, {args.width}x{args.height})")
        if args.html:
            print(built)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
