#!/usr/bin/env python3
"""
Centralized Professional Logging System for Stock Analysis
Provides consistent logging across all modules with detailed information
"""

import logging
import logging.handlers
import os
import sys
from datetime import datetime
from typing import Optional
from pathlib import Path


class StockAnalysisLogger:
    """
    Professional logging system with detailed information including:
    - Timestamp
    - File name
    - Function name
    - Line number
    - Log level
    - Message
    - Error details (if applicable)
    """
    
    _instance: Optional['StockAnalysisLogger'] = None
    _initialized: bool = False
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance
    
    def __init__(self):
        if not self._initialized:
            self._setup_logging()
            StockAnalysisLogger._initialized = True
    
    def _setup_logging(self):
        """Setup centralized logging configuration"""
        # Create logs directory
        self.log_dir = Path(__file__).parent.parent.parent / "logs"
        self.log_dir.mkdir(exist_ok=True)
        
        # Create main logger
        self.logger = logging.getLogger('stockanalysis')
        self.logger.setLevel(logging.DEBUG)
        
        # Clear any existing handlers to avoid duplicates
        self.logger.handlers.clear()
        
        # Create formatters
        self.detailed_formatter = logging.Formatter(
            fmt='%(asctime)s | %(filename)s:%(lineno)d | %(funcName)s() | %(levelname)s | %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )
        
        self.simple_formatter = logging.Formatter(
            fmt='%(asctime)s | %(levelname)s | %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )
        
        # Setup file handlers
        self._setup_file_handlers()
        
        # Setup console handler
        self._setup_console_handler()
        
        # Prevent propagation to root logger
        self.logger.propagate = False
        
        # Log initialization (Debug only to reduce noise in workers)
        self.logger.debug("StockAnalysisLogger initialized successfully")
    
    def _setup_file_handlers(self):
        """Setup file handlers for main and error logs"""
        # Main log file handler (all levels)
        main_file_handler = logging.handlers.RotatingFileHandler(
            self.log_dir / "stockanalysis_app.log",
            maxBytes=10 * 1024 * 1024,  # 10MB
            backupCount=5,
            encoding='utf-8'
        )
        main_file_handler.setLevel(logging.DEBUG)
        main_file_handler.setFormatter(self.detailed_formatter)
        self.logger.addHandler(main_file_handler)
        
        # Error log file handler (errors only)
        error_file_handler = logging.handlers.RotatingFileHandler(
            self.log_dir / "stockanalysis_app_errors.log",
            maxBytes=10 * 1024 * 1024,  # 10MB
            backupCount=5,
            encoding='utf-8'
        )
        error_file_handler.setLevel(logging.ERROR)
        error_file_handler.setFormatter(self.detailed_formatter)
        self.logger.addHandler(error_file_handler)
    
    def _setup_console_handler(self):
        """Setup console handler for real-time monitoring"""
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(logging.INFO)
        console_handler.setFormatter(self.simple_formatter)
        self.logger.addHandler(console_handler)
    
    def get_logger(self, name: str = None) -> logging.Logger:
        """
        Get logger instance for a specific module
        
        Args:
            name: Module name (optional, uses calling module if not provided)
            
        Returns:
            Logger instance configured for the module
        """
        if name:
            return self.logger.getChild(name)
        return self.logger
    
    def log_function_entry(self, func_name: str, **kwargs):
        """Log function entry with parameters"""
        params = ', '.join([f"{k}={v}" for k, v in kwargs.items()])
        self.logger.debug(f"Entering {func_name}({params})")
    
    def log_function_exit(self, func_name: str, result=None):
        """Log function exit with result"""
        if result is not None:
            self.logger.debug(f"Exiting {func_name}() -> {result}")
        else:
            self.logger.debug(f"Exiting {func_name}()")
    
    def log_api_call(self, method: str, endpoint: str, params: dict = None, response: dict = None):
        """Log API calls with detailed information"""
        param_str = f" | Params: {params}" if params else ""
        response_str = f" | Response: {response}" if response else ""
        self.logger.info(f"API Call: {method} {endpoint}{param_str}{response_str}")
    
    def log_database_operation(self, operation: str, collection: str, query: dict = None, result: dict = None):
        """Log database operations with detailed information"""
        query_str = f" | Query: {query}" if query else ""
        result_str = f" | Result: {result}" if result else ""
        self.logger.info(f"DB {operation}: {collection}{query_str}{result_str}")
    
    def log_redis_event(self, event_type: str, data: dict = None):
        """Log Redis events with data"""
        data_str = f" | Data: {data}" if data else ""
        self.logger.info(f"Redis {event_type}{data_str}")
    
    def log_strategy_event(self, event_type: str, symbol: str, details: dict = None):
        """Log strategy execution events with symbol and details"""
        details_str = f" | Details: {details}" if details else ""
        self.logger.info(f"Strategy {event_type}: {symbol}{details_str}")
    
    def log_error_with_context(self, error: Exception, context: str = "", **kwargs):
        """Log errors with additional context information"""
        context_str = f" | Context: {context}" if context else ""
        kwargs_str = f" | Additional Info: {kwargs}" if kwargs else ""
        self.logger.error(f"Error: {str(error)}{context_str}{kwargs_str}", exc_info=True)
    
    def log_performance(self, operation: str, duration: float, **kwargs):
        """Log performance metrics"""
        kwargs_str = f" | Details: {kwargs}" if kwargs else ""
        self.logger.info(f"Performance: {operation} took {duration:.3f}s{kwargs_str}")


# Global logger instance
logger_instance = StockAnalysisLogger()

def get_logger(name: str = None) -> logging.Logger:
    """
    Convenience function to get logger instance
    
    Args:
        name: Module name (optional)
        
    Returns:
        Logger instance
    """
    return logger_instance.get_logger(name)


# Module-specific logger getters for convenience
def get_data_provider_logger():
    """Get logger for data provider module"""
    return get_logger('data_provider')

def get_mongodb_logger():
    """Get logger for MongoDB operations"""
    return get_logger('mongodb')

def get_redis_logger():
    """Get logger for Redis operations"""
    return get_logger('redis')

def get_celery_logger():
    """Get logger for Celery tasks"""
    return get_logger('celery')

def get_strategies_logger():
    """Get logger for strategies"""
    return get_logger('strategies')

def get_main_logger():
    """Get main application logger"""
    return get_logger('main')

