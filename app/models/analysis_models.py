"""
Pydantic models for strategy analysis and result processing.
Contains models for analyzing strategy performance and ranking.
"""

from typing import List, Dict, Any, Optional
from pydantic import BaseModel, Field

class StrategyAnalysis(BaseModel):
    """Analysis result for a single strategy across all symbols."""

    strategy_name: str = Field(..., description="Name of the strategy")
    average_confidence: float = Field(..., ge=0, le=100, description="Average confidence percentage across all executions")
    total_executions: int = Field(..., ge=0, description="Total number of executions attempted")
    successful_executions: int = Field(..., ge=0, description="Number of successful executions")
    success_rate: float = Field(..., ge=0, le=100, description="Success rate percentage")
    best_symbol: str = Field(..., description="Symbol with highest confidence for this strategy")
    best_confidence: float = Field(..., ge=0, le=100, description="Highest confidence achieved")
    execution_details: List[Dict[str, Any]] = Field(default_factory=list, description="Detailed execution results")

    class Config:
        """Pydantic configuration."""
        use_enum_values = True
        validate_assignment = True
        extra = "forbid"

    def get_performance_grade(self) -> str:
        """Get performance grade based on average confidence."""
        if self.average_confidence >= 90:
            return "A+"
        elif self.average_confidence >= 80:
            return "A"
        elif self.average_confidence >= 70:
            return "B"
        elif self.average_confidence >= 60:
            return "C"
        else:
            return "D"

    def get_top_symbols(self, limit: int = 3) -> List[Dict[str, Any]]:
        """Get top performing symbols for this strategy."""
        if not self.execution_details:
            return []

        sorted_results = sorted(
            self.execution_details,
            key=lambda x: float(x.get('confidence', 0)),
            reverse=True
        )
        return sorted_results[:limit]

class ProcessedResults(BaseModel):
    """Complete analysis of all strategy results from batch execution."""

    best_strategy: Optional[StrategyAnalysis] = Field(None, description="Strategy with highest average confidence")
    strategies_by_confidence: List[StrategyAnalysis] = Field(default_factory=list, description="All strategies ranked by confidence (high to low)")
    total_symbols: int = Field(..., ge=0, description="Total number of symbols processed")
    total_strategies: int = Field(..., ge=0, description="Total number of strategies executed")
    overall_success_rate: float = Field(..., ge=0, le=100, description="Overall success rate across all executions")
    batch_summary: Dict[str, Any] = Field(default_factory=dict, description="Original batch execution summary")

    class Config:
        """Pydantic configuration."""
        use_enum_values = True
        validate_assignment = True
        extra = "forbid"

    def get_strategy_by_name(self, strategy_name: str) -> Optional[StrategyAnalysis]:
        """Get strategy analysis by name."""
        for strategy in self.strategies_by_confidence:
            if strategy.strategy_name == strategy_name:
                return strategy
        return None

    def get_top_strategies(self, limit: int = 3) -> List[StrategyAnalysis]:
        """Get top N strategies by confidence."""
        return self.strategies_by_confidence[:limit]

    def get_performance_summary(self) -> Dict[str, Any]:
        """Get summary of performance metrics."""
        if not self.strategies_by_confidence:
            return {}

        confidences = [s.average_confidence for s in self.strategies_by_confidence]
        return {
            "total_strategies": self.total_strategies,
            "best_confidence": max(confidences) if confidences else 0,
            "worst_confidence": min(confidences) if confidences else 0,
            "average_confidence": sum(confidences) / len(confidences) if confidences else 0,
            "confidence_spread": max(confidences) - min(confidences) if confidences else 0,
            "overall_success_rate": self.overall_success_rate
        }