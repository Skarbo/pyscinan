"""pyscinan exceptions."""


class ScinanError(Exception):
    """Base class for all pyscinan errors."""


class ScinanConnectionError(ScinanError):
    """Could not reach the device or API, or the connection dropped."""


class ScinanTimeoutError(ScinanConnectionError):
    """The device or API did not answer in time."""


class ScinanProtocolError(ScinanError):
    """Got data that could not be parsed."""


class ScinanAuthFailed(ScinanError):
    """Cloud login failed (wrong credentials, blocked account, ...)."""


class ScinanInvalidTokenError(ScinanError):
    """Cloud token missing or expired."""


class ScinanResponseError(ScinanError):
    """Cloud API returned an error."""
