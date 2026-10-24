"""Custom exception hierarchy.

Every error the application can raise has a named class so callers
catch exactly what they expect and nothing slips through silently.

Hierarchy:
    AppError
    ├── NotFoundError
    ├── ValidationError
    ├── AuthenticationError
    ├── AuthorisationError
    ├── BrokerError
    │   └── InsufficientFundsError
    ├── SettingsError
    ├── TradeRefusedError
    └── RpcError
"""

from __future__ import annotations


class AppError(Exception):
    """Base class for all application errors.

    Every domain exception inherits from this, so the API layer can
    catch ``AppError`` as a backstop and return a structured JSON error
    instead of a bare 500.
    """

    def __init__(self, message: str = "", *, code: str = "app_error") -> None:
        self.code = code
        super().__init__(message)


class NotFoundError(AppError):
    """The requested resource does not exist."""

    def __init__(self, message: str = "Resource not found") -> None:
        super().__init__(message, code="not_found")


class ValidationError(AppError):
    """Input data failed validation rules."""

    def __init__(self, message: str = "Validation failed") -> None:
        super().__init__(message, code="validation_error")


class AuthenticationError(AppError):
    """Caller did not provide valid credentials."""

    def __init__(self, message: str = "Authentication required") -> None:
        super().__init__(message, code="auth_required")


class AuthorisationError(AppError):
    """Caller is authenticated but not allowed to do this."""

    def __init__(self, message: str = "Not authorised") -> None:
        super().__init__(message, code="forbidden")


class BrokerError(AppError):
    """Something went wrong talking to an execution venue."""

    def __init__(self, message: str = "Broker error") -> None:
        super().__init__(message, code="broker_error")


class InsufficientFundsError(BrokerError):
    """Account does not have enough balance for the requested trade."""

    def __init__(self, message: str = "Insufficient funds") -> None:
        super().__init__(message)
        self.code = "insufficient_funds"


class SettingsError(AppError):
    """A settings change was refused by validation rules."""

    def __init__(self, message: str = "Invalid settings") -> None:
        super().__init__(message, code="settings_error")


class TradeRefusedError(AppError):
    """The system refused to execute a trade (risk, toggle, duplicate)."""

    def __init__(self, message: str = "Trade refused") -> None:
        super().__init__(message, code="trade_refused")


class RpcError(AppError):
    """A ZeroMQ RPC call to the engine failed or timed out."""

    def __init__(self, message: str = "RPC error") -> None:
        super().__init__(message, code="rpc_error")
