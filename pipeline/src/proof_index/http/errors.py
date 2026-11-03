"""HTTP failure types. All map to exit code 3 (provider/network failure) unless
the runner converts ``BudgetExhausted`` into a resumable checkpoint (exit 0)."""

from __future__ import annotations

from proof_index.errors import ProviderError


class HttpStatusError(ProviderError):
    code = "http_status"

    def __init__(self, status: int, url: str, attempts: int) -> None:
        super().__init__(f"HTTP {status} from {url} after {attempts} attempt(s)")
        self.status = status
        self.url = url
        self.attempts = attempts


class TransportFailure(ProviderError):
    code = "transport_failure"


class ResponseTooLarge(ProviderError):
    code = "response_too_large"


class BudgetExhausted(ProviderError):
    """The next required wait does not fit in the job's remaining time budget.

    Callers checkpoint and exit resumably; this is not a data failure.
    """

    code = "budget_exhausted"

    def __init__(self, required_wait_s: float, remaining_s: float) -> None:
        super().__init__(
            f"required wait {required_wait_s:.1f}s exceeds remaining budget {remaining_s:.1f}s"
        )
        self.required_wait_s = required_wait_s
        self.remaining_s = remaining_s
