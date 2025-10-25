from abc import ABC, abstractmethod
import time
from app.models.strategy_models import StrategyResult

class BaseStrategy(ABC):
    def __init__(self, name: str):
        self.name = name

    @abstractmethod
    def execute(self, symbol: str) -> StrategyResult:
        """
        Abstract method that must be implemented by all strategy classes.
        Returns StrategyResult with signal, confidence, and execution time.
        """
        pass

    def _measure_execution_time(self, func, *args, **kwargs):
        """Helper method to measure execution time of a function"""
        start_time = time.time()
        result = func(*args, **kwargs)
        execution_time = time.time() - start_time
        return result, execution_time