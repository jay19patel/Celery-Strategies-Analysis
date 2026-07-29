#!/usr/bin/env python3
"""
Centralized Professional Logging System for Stock Analysis
Provides consistent logging across all modules with detailed information
"""

import logging
import logging.handlers
import os
import sys
import threading
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
    _lock = threading.Lock()
    _initialized: bool = False
    
    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance
    
    def __init__(self):
        if not self._initialized:
            with self._lock:
                if not self._initialized:
                    self._setup_logging()
                    self._initialized = True
    
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
        """Setup file handlers for multiple specialized logs"""
        
        class NonErrorFilter(logging.Filter):
            def filter(self, record):
                return record.levelno < logging.ERROR
                
        # 1. Success / Main log file handler
        main_file_handler = logging.handlers.RotatingFileHandler(
            self.log_dir / "success.log",
            maxBytes=10 * 1024 * 1024,  # 10MB
            backupCount=5,
            encoding='utf-8'
        )
        main_file_handler.setLevel(logging.DEBUG)
        main_file_handler.addFilter(NonErrorFilter())
        main_file_handler.setFormatter(self.detailed_formatter)
        self.logger.addHandler(main_file_handler)
        
        # 2. Error log file handler
        error_file_handler = logging.handlers.RotatingFileHandler(
            self.log_dir / "errors.log",
            maxBytes=10 * 1024 * 1024,  # 10MB
            backupCount=5,
            encoding='utf-8'
        )
        error_file_handler.setLevel(logging.ERROR)
        error_file_handler.setFormatter(self.detailed_formatter)
        self.logger.addHandler(error_file_handler)
        
        # 3. Signals logger setup
        self.signals_logger = logging.getLogger('signals')
        self.signals_logger.setLevel(logging.INFO)
        self.signals_logger.propagate = False
        signals_handler = logging.handlers.RotatingFileHandler(
            self.log_dir / "signals.log",
            maxBytes=10 * 1024 * 1024,
            backupCount=5,
            encoding='utf-8'
        )
        signals_handler.setFormatter(self.simple_formatter)
        self.signals_logger.addHandler(signals_handler)
        # 4. Performance logger setup
        self.performance_logger = logging.getLogger('performance')
        self.performance_logger.setLevel(logging.INFO)
        self.performance_logger.propagate = False
        performance_handler = logging.handlers.RotatingFileHandler(
            self.log_dir / "performance.log",
            maxBytes=10 * 1024 * 1024,
            backupCount=5,
            encoding='utf-8'
        )
        performance_handler.setFormatter(self.simple_formatter)
        self.performance_logger.addHandler(performance_handler)
    
    def _setup_console_handler(self):
        """Setup console handler for real-time monitoring"""
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(logging.INFO)
        console_handler.setFormatter(self.simple_formatter)
        self.logger.addHandler(console_handler)
        self.signals_logger.addHandler(console_handler)
        self.performance_logger.addHandler(console_handler)
    
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

def get_signals_logger():
    """Get logger that writes to logs/signals.log (Algo Signals dashboard panel)"""
    return logger_instance.signals_logger

def get_performance_logger():
    """Get logger that writes to logs/performance.log (Performance & Statistics dashboard panel)"""
    return logger_instance.performance_logger

