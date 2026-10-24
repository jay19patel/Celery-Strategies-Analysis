"""Errors that cross module boundaries. Each maps to one HTTP status in `app.main`."""

from __future__ import annotations


class AppError(Exception):
    status = 400


class NotFound(AppError):
    status = 404


class Refused(AppError):
    """A trade or action the rules will not allow. The message says which rule."""

    status = 409


class SettingsError(AppError):
    status = 422


class BrokerError(AppError):
    """The broker answered and said no, or could not be reached for a read."""

    status = 502


class BrokerTimeout(BrokerError):
    """No clear answer: an order may or may not exist. Never resend — look it up by client_order_id."""
