"""python -m tradebuddy {feed|engine|web|worker|analyst|train} — normally started by docker-compose.

    train   download history and fit the market forecaster: python -m tradebuddy train --symbols BTCUSD ETHUSD --days 365
"""

import argparse
import asyncio
import logging

from tradebuddy import roles
from tradebuddy.config import load_config


def main() -> None:
    parser = argparse.ArgumentParser(prog="tradebuddy", description=roles.__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("role", choices=["feed", "engine", "web", "worker", "analyst", "train"])
    parser.add_argument("--concurrency", type=int, default=4, help="Celery worker processes (worker)")
    parser.add_argument("--symbols", nargs="+", default=["BTCUSD", "ETHUSD"], help="symbols to train (train)")
    parser.add_argument("--days", type=int, default=365, help="days of 15m history to train on (train)")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format=f"%(asctime)s %(levelname)s [{args.role}] %(name)s %(message)s")
    cfg = load_config()

    if args.role == "feed":
        asyncio.run(roles.feed(cfg))
    elif args.role == "engine":
        asyncio.run(roles.engine(cfg))
    elif args.role == "web":
        roles.serve(roles.web_app(cfg), cfg)
    elif args.role == "analyst":
        asyncio.run(roles.analyst(cfg))
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
