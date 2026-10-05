from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable, Tuple, Type


@dataclass
class RetryOptions:
    """Configuration for retry behavior.

    Attributes:
        max_retries: Maximum number of retry attempts.
        sleep: Callable used to sleep between retries (defaults to time.sleep).
        backoff_factor: Multiplier applied to sleep duration on each retry.
        retryable_exceptions: Tuple of exception types that trigger a retry.
        timeout: Per-attempt timeout in seconds.
        retry_on_exception: Whether to retry when an exception occurs.
    """

    max_retries: int = 3
    sleep: Callable[[float], None] = field(default_factory=lambda: time.sleep)
    backoff_factor: float = 1.0
    retryable_exceptions: Tuple[Type[BaseException], ...] = field(
        default_factory=lambda: (Exception,)
    )
    timeout: float = 0.0
    retry_on_exception: bool = True
