from typing import List, Dict, Any


class StrategyManager:
    """
    Singleton StrategyManager class
    Multiple objects create karne par bhi same instance milega
    """
    _instance = None
    _initialized = False
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance
    
    def __init__(self):
        # Initialize only once
        if not StrategyManager._initialized:
            self._strategy_class_paths: List[str] = []
            self._symbols: List[str] = []
            StrategyManager._initialized = True
    
    def add_strategies(self, strategy_class_paths: List[str]) -> None:
        self._strategy_class_paths.extend(strategy_class_paths)
    
    def add_symbols(self, symbols: List[str]) -> None:
        self._symbols.extend(symbols)
    
    def create_task_signatures_with_numbering(self) -> List[Any]:
        """
        Creates numbered task signatures for better tracking in logs
        """
        from app.core.tasks import execute_strategy_task
        signatures = []
        task_number = 1
        total_tasks = len(self._symbols) * len(self._strategy_class_paths)
        
        for symbol in self._symbols:
            for strategy_path in self._strategy_class_paths:
                signatures.append(
                    execute_strategy_task.s(strategy_path, symbol, task_number, total_tasks)
                )
                task_number += 1
        
        return signatures
    
    def aggregate_results(self, flat_results: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Aggregates flat list of results into structured format
        Groups by symbol and includes all strategy results
        """
        aggregated: Dict[str, Dict[str, Any]] = {}
        
        # Filter out None results from failed tasks
        valid_results = [r for r in flat_results if r]
        
        # Group by symbol
        for item in valid_results:
            symbol = item.get("symbol")
            if not symbol:
                continue
            
            if symbol not in aggregated:
                aggregated[symbol] = {
                    "symbol": symbol, 
                    "strategies": []
                }
            
            aggregated[symbol]["strategies"].append(item)
        
        # Calculate summary statistics
        unique_symbols = set(r["symbol"] for r in valid_results if "symbol" in r)
        
        summary = {
            "total_symbols": len(unique_symbols),
            "total_strategies": len(self._strategy_class_paths),
            "total_results": len(valid_results),
            "expected_results": len(self._symbols) * len(self._strategy_class_paths),
            "failed_results": (len(self._symbols) * len(self._strategy_class_paths)) - len(valid_results)
        }
        
        return {
            "summary": summary,
            "results": list(aggregated.values()),
        }
    
    @classmethod
    def reset_instance(cls):
        """
        Testing ke liye instance reset karne ka method
        Production mein use na karein
        """
        cls._instance = None
        cls._initialized = False


# Usage Example:
if __name__ == "__main__":
    # Multiple objects create karne par bhi same instance milega
    manager1 = StrategyManager()
    manager1.add_symbols(["AAPL", "GOOGL"])
    
    manager2 = StrategyManager()
    manager2.add_strategies(["strategy.momentum", "strategy.mean_reversion"])
    
    manager3 = StrategyManager()
    
    # Sab same instance hain
    print(f"manager1 is manager2: {manager1 is manager2}")  # True
    print(f"manager2 is manager3: {manager2 is manager3}")  # True
    print(f"manager1 is manager3: {manager1 is manager3}")  # True
    
    # Data bhi shared hai
    print(f"\nSymbols in manager3: {manager3._symbols}")  # ['AAPL', 'GOOGL']
    print(f"Strategies in manager1: {manager1._strategy_class_paths}")  # ['strategy.momentum', 'strategy.mean_reversion']