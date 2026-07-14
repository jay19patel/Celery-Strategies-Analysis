from abc import ABC, abstractmethod
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