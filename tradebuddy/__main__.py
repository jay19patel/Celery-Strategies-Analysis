"""python -m tradebuddy {feed|engine|web|worker|analyst|train|backfill} — normally started by docker-compose.

    backfill  fill the bars table with past candles, OI and funding: python -m tradebuddy backfill --days 365
    demo      write a market that never happened, so the pages can be read: python -m tradebuddy demo --days 120
    train     download history and fit the market forecaster: python -m tradebuddy train --symbols BTCUSD ETHUSD --days 365
"""

import argparse
import asyncio
import logging
import os

from tradebuddy import roles
from tradebuddy.config import load_config


def main() -> None:
    parser = argparse.ArgumentParser(prog="tradebuddy", description=roles.__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("role", choices=["feed", "engine", "web", "worker", "analyst", "train", "backfill", "demo"])
    parser.add_argument("--concurrency", type=int, default=4, help="Celery worker processes (worker)")
    parser.add_argument("--symbols", nargs="+", default=["BTCUSD", "ETHUSD"], help="symbols (train, backfill)")
    parser.add_argument("--days", type=int, default=365, help="days of 15m history (train, backfill, demo)")
    parser.add_argument("--db", default="", help="database to write (demo); defaults to data/demo.db")
    parser.add_argument("--force", action="store_true", help="write demo data into a database that has real orders")
    args = parser.parse_args()

    # Stdout only: Docker rotates it (10 MB x 3) and nothing here writes a log file. LOG_LEVEL=WARNING
    # on a long-running server; INFO while watching a deploy.
    level = os.getenv("LOG_LEVEL", "INFO").strip().upper()
    logging.basicConfig(
        level=getattr(logging, level, logging.INFO),
        format=f"%(asctime)s %(levelname)s [{args.role}] %(name)s %(message)s",
    )
    cfg = load_config()

    if args.role == "feed":
        asyncio.run(roles.feed(cfg))
    elif args.role == "engine":
        asyncio.run(roles.engine(cfg))
    elif args.role == "web":
        roles.serve(roles.web_app(cfg), cfg)
    elif args.role == "analyst":
        asyncio.run(roles.analyst(cfg))
    elif args.role == "demo":
        from tradebuddy.demo import DEFAULT_PATH, Refused, seed

        try:
            out = seed(args.db or DEFAULT_PATH, days=min(args.days, 365), force=args.force)
        except Refused as exc:
            raise SystemExit(f"refused: {exc}") from exc
        print(f"wrote {out['days']} days of demo market to {out['path']}")
        for table, n in out["written"].items():
            print(f"  {table}: {n:,} rows")
        print(f"\nLook at it with:  DB_PATH={out['path']} docker compose up")
        print("Nothing it wrote is money — no orders, trades or settings were touched.")
    elif args.role == "backfill":
        from tradebuddy.analyst import backfill

        for symbol, n in asyncio.run(backfill(cfg.db_path, args.symbols, args.days)).items():
            print(f"{symbol}: {n} new bars")
    elif args.role == "train":
        from tradebuddy.analyst import train

        for card in asyncio.run(train(cfg.db_path, args.symbols, args.days)):
            print(card["symbol"], "skill:", card["skill"])
            print("  holdout:", card["metrics"])
            print("  baseline:", card["baselines"])
    else:
        roles.run_worker(args.concurrency)


if __name__ == "__main__":
    main()
