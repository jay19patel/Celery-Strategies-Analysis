import json
from datetime import datetime

from core.strategy_manager import StrategyManager


def main() -> None:
    symbols = ["AAPL", "GOOG", "MSFT"]
    strategies = [
        "strategies.ema_strategy.EMAStrategy",
        "strategies.rsi_strategy.RSIStrategy",
        "strategies.custom_strategy.CustomStrategy",
    ]

    manager = StrategyManager()
    manager.add_symbols(symbols)
    manager.add_strategies(strategies)

    result = manager.run_all()

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_file = f"results_{timestamp}.json"
    with open(output_file, "w") as f:
        json.dump(result, f, indent=2)

    print(f"Saved results to {output_file}")


if __name__ == "__main__":
    main()


