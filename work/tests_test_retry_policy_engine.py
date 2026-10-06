import pytest
from src.smallworks.retry_policy_engine import RetryPolicyEngine


def test_exponential_backoff():
    engine = RetryPolicyEngine(base_delay=1.0, jitter=0.0)
    delays = [engine._calculate_delay(i) for i in range(3)]
    assert delays == [1.0, 2.0, 4.0]


def test_deterministic_jitter():
    engine = RetryPolicyEngine(base_delay=1.0, jitter=0.5)
    d1 = engine._calculate_delay(0)
    d2 = engine._calculate_delay(0)
    assert d1 == d2
    assert d1 == 1.5


def test_retryable_exceptions():
    engine = RetryPolicyEngine(retryable_exceptions=(ValueError,))
    call_count = [0]

    def op():
        call_count[0] += 1
        if call_count[0] < 3:
            raise ValueError("fail")

    engine.execute(op, max_retries=3)
    assert call_count[0] == 3


def test_injectable_sleep():
    engine = RetryPolicyEngine()
    sleep_calls = []

    def mock_sleep(duration):
        sleep_calls.append(duration)

    engine.sleep = mock_sleep

    def op():
        raise ValueError("fail")

    engine.execute(op, max_retries=2)
    assert sleep_calls == [1.0, 2.0]


def test_run_until_success():
    engine = RetryPolicyEngine(base_delay=1.0, jitter=0.0)
    call_count = [0]

    def op():
        call_count[0] += 1
        if call_count[0] == 1:
            raise ValueError("fail")

    engine.execute(op, max_retries=3)
    assert call_count[0] == 1
