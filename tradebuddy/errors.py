"""Errors every broker raises. The split between them decides how an order is recovered."""


class BrokerError(Exception):
    """The broker answered and refused."""


class BrokerTimeout(BrokerError):
    """No clear answer: the order may or may not exist. Never resend — look it up by client_order_id."""
