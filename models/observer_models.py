"""
Pydantic models for Observer pattern task monitoring.
"""

from pydantic import BaseModel, Field
from typing import Dict, Any, Optional, Literal
from datetime import datetime


class TaskEvent(BaseModel):
    """Event data structure for task lifecycle events."""
    task_id: str = Field(..., description="Unique identifier for the task")
    symbol: str = Field(..., description="Stock symbol being processed")
    strategy_name: str = Field(..., description="Name of the strategy being executed")
    batch_id: str = Field(..., description="Identifier for the batch this task belongs to")
    event_type: Literal['created', 'completed'] = Field(..., description="Type of event")
    timestamp: float = Field(..., description="Unix timestamp when event occurred")
    result: Optional[Dict[str, Any]] = Field(None, description="Task result data for completed events")

    class Config:
        """Pydantic configuration."""
        json_encoders = {
            datetime: lambda dt: dt.isoformat()
        }


class TaskResult(BaseModel):
    """Task execution result model."""
    task_id: str = Field(..., description="Unique identifier for the task")
    symbol: str = Field(..., description="Stock symbol that was processed")
    strategy_name: str = Field(..., description="Name of the strategy that was executed")
    confidence: float = Field(..., ge=0, le=1, description="Confidence score between 0 and 1")
    signal: str = Field(..., description="Trading signal generated")
    success: bool = Field(..., description="Whether task completed successfully")
    batch_id: str = Field(..., description="Identifier for the batch this task belongs to")
    error: Optional[str] = Field(None, description="Error message if task failed")
    execution_time: Optional[float] = Field(None, description="Task execution time in seconds")

    class Config:
        """Pydantic configuration."""
        validate_assignment = True


class BatchStatistics(BaseModel):
    """Statistics for a single batch."""
    batch_id: str = Field(..., description="Batch identifier")
    created_count: int = Field(0, ge=0, description="Number of tasks created")
    completed_count: int = Field(0, ge=0, description="Number of tasks completed")
    success_count: int = Field(0, ge=0, description="Number of successful tasks")
    failure_count: int = Field(0, ge=0, description="Number of failed tasks")
    execution_time: Optional[float] = Field(None, description="Total batch execution time")

    class Config:
        """Pydantic configuration."""
        validate_assignment = True


class MonitoringSummary(BaseModel):
    """Complete monitoring summary model."""
    total_created: int = Field(0, ge=0, description="Total tasks created across all batches")
    total_completed: int = Field(0, ge=0, description="Total tasks completed across all batches")
    total_successful: int = Field(0, ge=0, description="Total successful tasks")
    total_failed: int = Field(0, ge=0, description="Total failed tasks")
    batch_statistics: Dict[str, BatchStatistics] = Field(default_factory=dict, description="Per-batch statistics")
    results: list[TaskResult] = Field(default_factory=list, description="All task results")
    total_execution_time: Optional[float] = Field(None, description="Total execution time for all batches")

    class Config:
        """Pydantic configuration."""
        validate_assignment = True