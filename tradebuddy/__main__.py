"""python -m tradebuddy {feed|engine|web|worker} — normally started by docker-compose."""

import argparse
import asyncio
import logging

from tradebuddy import roles
from tradebuddy.config import load_config


def main() -> None:
    parser = argparse.ArgumentParser(prog="tradebuddy", description=roles.__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("role", choices=["feed", "engine", "web", "worker"])
    parser.add_argument("--concurrency", type=int, default=4, help="Celery worker processes (worker)")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format=f"%(asctime)s %(levelname)s [{args.role}] %(name)s %(message)s")
    cfg = load_config()

    if args.role == "feed":
        asyncio.run(roles.feed(cfg))
    elif args.role == "engine":
        asyncio.run(roles.engine(cfg))
    elif args.role == "web":
        roles.serve(roles.web_app(cfg), cfg)
    else:
        roles.run_worker(args.concurrency)


if __name__ == "__main__":
    main()
