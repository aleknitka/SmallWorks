from dataclasses import dataclass
from typing import Optional, Tuple, Type


@dataclass
class RetryOptions:
    """Configuration for retry behavior."""

    max_retries: int = 3
    retry_delay: float = 1.0
    backoff_multiplier: float = 2.0
    retryable_exceptions: Tuple[Type[Exception], ...] = ()
    jitter: bool = False

    def __post_init__(self) -> None:
        if self.max_retries < 0:
            raise ValueError("max_retries must be non-negative")
        if self.retry_delay < 0:
            raise ValueError("retry_delay must be non-negative")
        if self.backoff_multiplier < 1.0:
            raise ValueError("backoff_multiplier must be >= 1.0")