import time
from typing import Callable, Tuple, Type


class RetryPolicyEngine:
    """Retry engine with exponential backoff and deterministic jitter."""

    def __init__(
        self,
        base_delay: float = 1.0,
        max_retries: int = 3,
        retryable_exceptions: Tuple[Type[Exception], ...] = (Exception,),
        jitter: float = 0.5,
    ) -> None:
        self.base_delay = base_delay
        self.max_retries = max_retries
        self.retryable_exceptions = retryable_exceptions
        self.jitter = jitter
        self._sleep: Callable[[float], None] = time.sleep

    def _calculate_delay(self, attempt: int) -> float:
        """Compute delay for a given attempt: base_delay * 2^attempt + jitter."""
        return self.base_delay * (2 ** attempt) + self.jitter

    def execute(
        self,
        operation: Callable[[], None],
        max_retries: int | None = None,
    ) -> None:
        """Run *operation* up to *max_retries* times on retryable exceptions."""
        retries = max_retries if max_retries is not None else self.max_retries
        for attempt in range(retries):
            try:
                operation()
                return
            except self.retryable_exceptions:
                if attempt < retries - 1:
                    delay = self._calculate_delay(attempt)
                    self._sleep(delay)
                else:
                    raise

    @property
    def sleep(self) -> Callable[[float], None]:
        return self._sleep

    @sleep.setter
    def sleep(self, value: Callable[[float], None]) -> None:
        self._sleep = value
