"""The service's errors. Clients match on `code`, so every error class has its own."""


class InvoicingError(Exception):
    """Base class for every error the service raises."""

    code = "E000"


class ValidationError(InvoicingError, ValueError):
    """The input is malformed or out of range."""

    code = "E100"


class NotFound(InvoicingError, KeyError):
    """No record with that id."""

    code = "E404"


class Conflict(InvoicingError, ValueError):
    """The record's current state doesn't allow the action."""

    code = "E409"
