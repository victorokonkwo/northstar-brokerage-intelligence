"""Domain exception hierarchy shared by every Northstar layer.

Raise the most specific subclass. Callers at process boundaries may catch
``NorthstarError`` to log context and convert the failure into a run status.
"""


class NorthstarError(Exception):
    """Base class for every error raised deliberately by Northstar code."""


class CurrencyMismatchError(NorthstarError, ValueError):
    """Raised when an operation combines amounts in different currencies.

    Amounts in different currencies can only be combined after an explicit,
    dated FX conversion (ADR 004, CONTRIBUTING.md W2).
    """
