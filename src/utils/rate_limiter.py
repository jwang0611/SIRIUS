"""
Rate limiter utility for controlling API request frequency.

Includes:
- RateLimiter: Simple sequential rate limiter
- TokenBucketRateLimiter: Thread-safe rate limiter for concurrent access
"""

import threading
import time

from ..models.sdtm_models import RateLimitConfig


class TokenBucketRateLimiter:
    """
    线程安全的令牌桶速率限制器，支持并发访问。

    令牌桶算法允许一定程度的突发请求，同时维持长期的平均速率。
    """

    def __init__(
        self,
        config: RateLimitConfig | None = None,
        burst_size: int | None = None,
    ):
        """
        初始化令牌桶速率限制器。

        Args:
            config: 速率限制配置
            burst_size: 允许的突发请求数（默认为 requests_per_minute / 12，即5秒的量）
        """
        self.config = config or RateLimitConfig()
        self.requests_per_minute = max(1, self.config.requests_per_minute)

        # 每秒补充的令牌数
        self.rate = self.requests_per_minute / 60.0

        # 桶容量（突发大小）
        if burst_size is not None:
            self.capacity = max(1, burst_size)
        else:
            # 默认允许 5 秒的突发量
            self.capacity = max(1, int(self.requests_per_minute / 12))

        # 当前令牌数
        self.tokens = float(self.capacity)

        # 上次更新时间
        self.last_update = time.time()

        # 线程锁
        self._lock = threading.Lock()

        # 统计信息
        self._total_acquired = 0
        self._total_waited = 0.0

    def _refill(self) -> None:
        """补充令牌（必须在锁内调用）"""
        now = time.time()
        elapsed = now - self.last_update
        # 根据经过的时间补充令牌
        self.tokens = min(self.capacity, self.tokens + elapsed * self.rate)
        self.last_update = now

    def acquire(self, timeout: float = 60.0) -> bool:
        """
        获取一个令牌（阻塞直到获取成功或超时）。

        Args:
            timeout: 最大等待时间（秒）

        Returns:
            是否成功获取令牌
        """
        deadline = time.time() + timeout
        total_waited = 0.0

        while True:
            with self._lock:
                self._refill()

                if self.tokens >= 1.0:
                    self.tokens -= 1.0
                    self._total_acquired += 1
                    self._total_waited += total_waited
                    return True

                # 计算需要等待的时间才能获得一个令牌
                wait_time = (1.0 - self.tokens) / self.rate

            # 检查是否会超时
            if time.time() + wait_time > deadline:
                return False

            # 在锁外等待（允许其他线程操作）
            actual_wait = min(wait_time, 0.1)  # 最多等待 0.1 秒后重试
            time.sleep(actual_wait)
            total_waited += actual_wait

    def try_acquire(self) -> bool:
        """
        尝试获取一个令牌（非阻塞）。

        Returns:
            是否成功获取令牌
        """
        with self._lock:
            self._refill()

            if self.tokens >= 1.0:
                self.tokens -= 1.0
                self._total_acquired += 1
                return True

            return False

    def wait(self) -> float:
        """
        等待并获取一个令牌（兼容旧 API）。

        Returns:
            等待的秒数
        """
        start = time.time()
        self.acquire(timeout=120.0)  # 最多等待 2 分钟
        return time.time() - start

    def get_stats(self) -> dict:
        """获取统计信息"""
        with self._lock:
            return {
                "total_acquired": self._total_acquired,
                "total_waited_seconds": self._total_waited,
                "avg_wait_seconds": self._total_waited / max(1, self._total_acquired),
                "current_tokens": self.tokens,
                "capacity": self.capacity,
                "rate_per_second": self.rate,
            }

    def reset(self) -> None:
        """重置速率限制器状态"""
        with self._lock:
            self.tokens = float(self.capacity)
            self.last_update = time.time()
            self._total_acquired = 0
            self._total_waited = 0.0


class RateLimiter:
    """
    Simple thread-safe rate limiter to control the frequency of API requests.
    """

    def __init__(self, config: RateLimitConfig | None = None):
        self.config = config or RateLimitConfig()
        self.requests_per_minute = max(1, self.config.requests_per_minute)
        self.min_interval = 60.0 / self.requests_per_minute
        self.last_request_time = 0.0
        self._lock = threading.Lock()

    def wait(self) -> float:
        """
        Wait if needed to maintain the rate limit.

        Returns:
            Number of seconds waited
        """
        with self._lock:
            if self.last_request_time == 0:
                self.last_request_time = time.time()
                return 0

            current_time = time.time()
            elapsed = current_time - self.last_request_time
            wait_time = max(0, self.min_interval - elapsed)

        if wait_time > 0:
            time.sleep(wait_time)

        with self._lock:
            self.last_request_time = time.time()
        return wait_time

    def reset(self):
        """Reset the rate limiter state."""
        with self._lock:
            self.last_request_time = 0.0
