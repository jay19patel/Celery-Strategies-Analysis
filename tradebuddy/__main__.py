"""python -m tradebuddy"""

import logging

import uvicorn

from tradebuddy.app import create_app
from tradebuddy.config import load_config
from tradebuddy.system import System


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    cfg = load_config()
    uvicorn.run(create_app(System(cfg)), host=cfg.host, port=cfg.port, log_level="info")


if __name__ == "__main__":
    main()
