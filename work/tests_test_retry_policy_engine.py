import typing
from dataclasses import fields
from src.smallworks.retry_policy_engine import RetryOptions


def test_retry_options_type_hints() -> None:
    hints = typing.get_type_hints(RetryOptions)
    assert hints["max_retries"] is int
    assert hints["retry_delay"] is float
    assert hints["backoff_multiplier"] is float
    assert hints["retryable_exceptions"] is tuple
    assert hints["jitter"] is bool


def test_retry_options_default_values() -> None:
    opts = RetryOptions()
    assert opts.max_retries == 3
    assert opts.retry_delay == 1.0
    assert opts.backoff_multiplier == 2.0
    assert opts.retryable_exceptions == ()
    assert opts.jitter is False
