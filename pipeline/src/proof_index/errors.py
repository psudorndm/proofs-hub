"""Error types that map onto the CLI exit-code contract (spec section 4).

Exit codes:
  0 success (including a safely checkpointed bounded slice)
  2 invalid configuration or input
  3 provider/network failure
  4 data validation failure
  5 writer/provider lock already held
  6 database/storage failure
"""

from __future__ import annotations


class ProofIndexError(Exception):
    """Base error. ``exit_code`` is what the CLI returns."""

    exit_code: int = 1
    code: str = "error"


class ConfigError(ProofIndexError):
    exit_code = 2
    code = "invalid_configuration"


class InputError(ProofIndexError):
    exit_code = 2
    code = "invalid_input"


class ProviderError(ProofIndexError):
    exit_code = 3
    code = "provider_failure"


class DataValidationError(ProofIndexError):
    exit_code = 4
    code = "data_validation_failure"


class LockHeldError(ProofIndexError):
    exit_code = 5
    code = "lock_held"


class StorageError(ProofIndexError):
    exit_code = 6
    code = "storage_failure"
