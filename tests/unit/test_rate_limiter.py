"""Unit tests for :mod:`src.utils.rate_limiter`."""

from __future__ import annotations

import threading
import time

import pytest

from src.models.sdtm_models import RateLimitConfig
from src.utils.rate_limiter import RateLimiter, TokenBucketRateLimiter


class TestTokenBucketConstruction:
    def test_default_config(self):
        limiter = TokenBucketRateLimiter()
        assert limiter.requests_per_minute >= 1
        assert limiter.rate > 0
        assert limiter.capacity >= 1
        assert limiter.tokens == float(limiter.capacity)

    def test_custom_burst_size(self):
        limiter = TokenBucketRateLimiter(
            config=RateLimitConfig(requests_per_minute=60),
            burst_size=10,
        )
        assert limiter.capacity == 10
        assert limiter.tokens == 10.0

    def test_custom_rate(self):
        limiter = TokenBucketRateLimiter(config=RateLimitConfig(requests_per_minute=120))
        assert limiter.rate == pytest.approx(2.0)

    def test_minimum_capacity(self):
        limiter = TokenBucketRateLimiter(
            config=RateLimitConfig(requests_per_minute=1),
            burst_size=0,
        )
        assert limiter.capacity == 1


class TestTokenBucketAcquire:
    def test_try_acquire_succeeds_when_tokens_available(self):
        limiter = TokenBucketRateLimiter(burst_size=5)
        assert limiter.try_acquire() is True
        assert limiter.tokens == 4.0

    def test_try_acquire_fails_when_empty(self):
        limiter = TokenBucketRateLimiter(
            config=RateLimitConfig(requests_per_minute=60),
            burst_size=1,
        )
        assert limiter.try_acquire() is True
        # Second immediate call fails (no refill yet)
        assert limiter.try_acquire() is False

    def test_acquire_returns_true_with_available_tokens(self):
        limiter = TokenBucketRateLimiter(burst_size=3)
        assert limiter.acquire(timeout=1.0) is True
        assert limiter.acquire(timeout=1.0) is True

    def test_stats_tracked(self):
        limiter = TokenBucketRateLimiter(burst_size=5)
        for _ in range(3):
            limiter.try_acquire()
        stats = limiter.get_stats()
        assert stats["total_acquired"] == 3
        assert stats["capacity"] == 5
        assert stats["current_tokens"] == pytest.approx(2.0, abs=0.01)

    def test_reset_restores_full_bucket(self):
        limiter = TokenBucketRateLimiter(burst_size=5)
        limiter.try_acquire()
        limiter.try_acquire()
        limiter.reset()
        stats = limiter.get_stats()
        assert stats["total_acquired"] == 0
        assert stats["current_tokens"] == 5.0

    def test_acquire_timeout_returns_false(self):
        # Very low rate + already-drained bucket + short timeout.
        limiter = TokenBucketRateLimiter(
            config=RateLimitConfig(requests_per_minute=1),
            burst_size=1,
        )
        assert limiter.try_acquire() is True  # drain
        assert limiter.acquire(timeout=0.01) is False


class TestTokenBucketRefill:
    def test_refill_adds_tokens_over_time(self, monkeypatch):
        clock = [1000.0]
        monkeypatch.setattr(time, "time", lambda: clock[0])

        limiter = TokenBucketRateLimiter(
            config=RateLimitConfig(requests_per_minute=60),
            burst_size=10,
        )
        # Drain bucket
        for _ in range(10):
            assert limiter.try_acquire() is True
        assert limiter.tokens == pytest.approx(0.0, abs=1e-9)

        # Advance clock by 3 seconds -> 3 tokens at rate 1/s
        clock[0] += 3.0
        assert limiter.try_acquire() is True
        assert limiter.tokens == pytest.approx(2.0, abs=1e-6)

    def test_refill_caps_at_capacity(self, monkeypatch):
        clock = [1000.0]
        monkeypatch.setattr(time, "time", lambda: clock[0])

        limiter = TokenBucketRateLimiter(
            config=RateLimitConfig(requests_per_minute=60),
            burst_size=5,
        )
        clock[0] += 3600.0  # enough time for huge refill
        assert limiter.try_acquire() is True
        assert limiter.tokens <= 5.0


class TestTokenBucketConcurrency:
    def test_thread_safe_under_contention(self):
        limiter = TokenBucketRateLimiter(burst_size=20)
        results = []

        def worker():
            results.append(limiter.try_acquire())

        threads = [threading.Thread(target=worker) for _ in range(30)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        successes = sum(1 for r in results if r)
        assert successes <= 20
        assert successes >= 15  # most should succeed


class TestSimpleRateLimiter:
    def test_first_call_no_wait(self):
        limiter = RateLimiter(config=RateLimitConfig(requests_per_minute=60))
        assert limiter.wait() == 0

    def test_second_immediate_call_waits(self, monkeypatch):
        clock = [1000.0]
        sleeps = []
        monkeypatch.setattr(time, "time", lambda: clock[0])
        monkeypatch.setattr(time, "sleep", lambda s: (sleeps.append(s), clock.__setitem__(0, clock[0] + s)))

        limiter = RateLimiter(config=RateLimitConfig(requests_per_minute=60))
        assert limiter.wait() == 0  # first call; last_request_time set
        # Now immediately call again; min_interval is 1.0s
        waited = limiter.wait()
        assert waited == pytest.approx(1.0, abs=1e-3)
        assert sleeps and sleeps[0] == pytest.approx(1.0, abs=1e-3)

    def test_reset_clears_state(self):
        limiter = RateLimiter()
        limiter.wait()
        assert limiter.last_request_time > 0
        limiter.reset()
        assert limiter.last_request_time == 0.0

    def test_min_interval_calculation(self):
        limiter = RateLimiter(config=RateLimitConfig(requests_per_minute=30))
        assert limiter.min_interval == pytest.approx(2.0)
