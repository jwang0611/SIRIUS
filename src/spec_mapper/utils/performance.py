"""Performance monitoring utilities.

Tracks execution time and memory usage for performance analysis.
"""

import logging
import time
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

import psutil

logger = logging.getLogger(__name__)


@dataclass
class PerformanceMetrics:
    """Performance metrics for a single operation or entire program.

    Attributes:
        name: Name of the operation
        start_time: Start timestamp
        end_time: End timestamp
        start_memory_mb: Memory usage at start (MB)
        peak_memory_mb: Peak memory usage (MB)
        end_memory_mb: Memory usage at end (MB)
        duration_seconds: Total duration in seconds
    """

    name: str
    start_time: float = 0.0
    end_time: float = 0.0
    start_memory_mb: float = 0.0
    peak_memory_mb: float = 0.0
    end_memory_mb: float = 0.0
    duration_seconds: float = 0.0

    def __str__(self) -> str:
        """String representation of metrics."""
        return (
            f"{self.name}: {self.duration_seconds:.2f}s, "
            f"Memory: {self.start_memory_mb:.1f}MB → {self.peak_memory_mb:.1f}MB (peak) → "
            f"{self.end_memory_mb:.1f}MB, "
            f"Δ{self.end_memory_mb - self.start_memory_mb:+.1f}MB"
        )


class PerformanceMonitor:
    """Monitor and track performance metrics for different operations.

    This class provides context managers for tracking execution time and memory
    usage of different operations. It maintains a history of all tracked operations
    and can generate a summary report.

    Examples:
        >>> monitor = PerformanceMonitor()
        >>> with monitor.track("read_excel"):
        ...     # Your code here
        ...     pass
        >>> monitor.log_summary()
    """

    def __init__(self) -> None:
        """Initialize performance monitor."""
        self.metrics: list[PerformanceMetrics] = []
        self.process = psutil.Process()
        self.global_start_time: float = 0.0
        self.global_start_memory_mb: float = 0.0
        self.global_peak_memory_mb: float = 0.0

    def start_global_monitoring(self) -> None:
        """Start global monitoring for entire program execution."""
        self.global_start_time = time.time()
        self.global_start_memory_mb = self._get_memory_usage_mb()
        self.global_peak_memory_mb = self.global_start_memory_mb
        logger.debug("Global performance monitoring started")

    def _get_memory_usage_mb(self) -> float:
        """Get current memory usage in MB.

        Returns:
            Current RSS (Resident Set Size) memory in MB
        """
        return self.process.memory_info().rss / 1024 / 1024

    def _update_peak_memory(self) -> None:
        """Update global peak memory if current usage is higher."""
        current = self._get_memory_usage_mb()
        if current > self.global_peak_memory_mb:
            self.global_peak_memory_mb = current

    @contextmanager
    def track(self, operation_name: str):
        """Context manager to track performance of an operation.

        Args:
            operation_name: Name of the operation being tracked

        Yields:
            PerformanceMetrics object for this operation

        Examples:
            >>> monitor = PerformanceMonitor()
            >>> with monitor.track("read_excel") as metrics:
            ...     # Your code here
            ...     pass
            >>> print(f"Operation took {metrics.duration_seconds:.2f}s")
        """
        metrics = PerformanceMetrics(name=operation_name)

        # Record start state
        metrics.start_time = time.time()
        metrics.start_memory_mb = self._get_memory_usage_mb()
        metrics.peak_memory_mb = metrics.start_memory_mb

        logger.debug(f"⏱️  Starting: {operation_name}")

        try:
            yield metrics
        finally:
            # Record end state
            metrics.end_time = time.time()
            metrics.end_memory_mb = self._get_memory_usage_mb()
            metrics.duration_seconds = metrics.end_time - metrics.start_time

            # Update peak memory (check during operation)
            self._update_peak_memory()
            metrics.peak_memory_mb = max(metrics.peak_memory_mb, self._get_memory_usage_mb())

            # Store metrics
            self.metrics.append(metrics)

            # Log completion
            logger.info(
                f"✓ {operation_name}: {metrics.duration_seconds:.2f}s "
                f"(Memory: {metrics.start_memory_mb:.1f}MB → {metrics.end_memory_mb:.1f}MB)"
            )

    def get_total_duration(self) -> float:
        """Get total duration since global monitoring started.

        Returns:
            Total duration in seconds
        """
        if self.global_start_time == 0.0:
            return 0.0
        return time.time() - self.global_start_time

    def get_summary_dict(self) -> dict[str, Any]:
        """Get summary of all performance metrics as dictionary.

        Returns:
            Dictionary containing summary statistics
        """
        total_duration = self.get_total_duration()
        current_memory = self._get_memory_usage_mb()

        return {
            "total_duration_seconds": total_duration,
            "start_memory_mb": self.global_start_memory_mb,
            "peak_memory_mb": self.global_peak_memory_mb,
            "end_memory_mb": current_memory,
            "memory_delta_mb": current_memory - self.global_start_memory_mb,
            "operations": [
                {
                    "name": m.name,
                    "duration_seconds": m.duration_seconds,
                    "memory_delta_mb": m.end_memory_mb - m.start_memory_mb,
                }
                for m in self.metrics
            ],
        }

    def log_summary(self, logger_instance: logging.Logger | None = None) -> None:
        """Log performance summary.

        Args:
            logger_instance: Logger to use (defaults to module logger)
        """
        log = logger_instance or logger

        total_duration = self.get_total_duration()
        current_memory = self._get_memory_usage_mb()
        memory_delta = current_memory - self.global_start_memory_mb

        log.info("")
        log.info("=" * 80)
        log.info("PERFORMANCE SUMMARY")
        log.info("=" * 80)
        log.info(f"Total Duration:     {total_duration:.2f}s ({total_duration / 60:.2f}m)")
        log.info(f"Start Memory:       {self.global_start_memory_mb:.1f} MB")
        log.info(f"Peak Memory:        {self.global_peak_memory_mb:.1f} MB")
        log.info(f"End Memory:         {current_memory:.1f} MB")
        log.info(f"Memory Delta:       {memory_delta:+.1f} MB")
        log.info("")

        if self.metrics:
            log.info("Operation Breakdown:")
            log.info("-" * 80)

            # Calculate percentages
            total_tracked = sum(m.duration_seconds for m in self.metrics)

            for metrics in self.metrics:
                percentage = (metrics.duration_seconds / total_duration * 100) if total_duration > 0 else 0
                mem_delta = metrics.end_memory_mb - metrics.start_memory_mb

                log.info(
                    f"  {metrics.name:.<40} "
                    f"{metrics.duration_seconds:>6.2f}s ({percentage:>5.1f}%) "
                    f"[Memory: {mem_delta:+.1f}MB]"
                )

            # Show untracked time
            untracked = total_duration - total_tracked
            if untracked > 0.1:  # Only show if significant
                untracked_pct = untracked / total_duration * 100
                log.info(f"  {'(Other/Overhead)':.<40} {untracked:>6.2f}s ({untracked_pct:>5.1f}%)")

        log.info("=" * 80)
        log.info("")


# Global instance for convenience
_global_monitor: PerformanceMonitor | None = None


def get_monitor() -> PerformanceMonitor:
    """Get or create the global performance monitor.

    Returns:
        Global PerformanceMonitor instance
    """
    global _global_monitor
    if _global_monitor is None:
        _global_monitor = PerformanceMonitor()
    return _global_monitor


def reset_monitor() -> None:
    """Reset the global performance monitor."""
    global _global_monitor
    _global_monitor = None
