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


class ConfigurationError(NorthstarError, ValueError):
    """Raised when a configuration or policy file is missing or invalid."""


class PolicyNotFoundError(NorthstarError, LookupError):
    """Raised when no policy version applies to the requested date or key.

    The caller must not fall back to a default (CONTRIBUTING.md W2).
    """


class CalendarCoverageError(NorthstarError, ValueError):
    """Raised when a date falls outside the period a calendar is verified for.

    Holidays outside the coverage window are unknown, so the calendar refuses
    to answer rather than guess.
    """


class NotATradingDayError(NorthstarError, ValueError):
    """Raised when a trade date is not a trading day of the instrument's market."""


class UnbalancedJournalError(NorthstarError, ValueError):
    """Raised when a journal batch's debits and credits differ in a currency (INV-01).

    An unbalanced batch is rejected. It is never dropped silently or posted.
    """


class ScenarioError(NorthstarError, ValueError):
    """Raised when a simulation scenario describes something the platform cannot do."""


class PublicationBlockedError(NorthstarError):
    """Raised when a snapshot fails its publication gate and stays DRAFT (ADR 006, §7.4)."""
