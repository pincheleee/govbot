"""Shared helpers: retry, backoff, rate limiting."""

import asyncio
import logging
import functools
from typing import TypeVar, Callable, Any

logger = logging.getLogger(__name__)

T = TypeVar("T")


def retry_async(max_retries: int = 3, base_delay: float = 1.0, max_delay: float = 30.0):
    """Exponential backoff retry decorator for async functions."""
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        async def wrapper(*args, **kwargs) -> Any:
            last_error = None
            for attempt in range(max_retries + 1):
                try:
                    return await func(*args, **kwargs)
                except Exception as e:
                    last_error = e
                    if attempt < max_retries:
                        delay = min(base_delay * (2 ** attempt), max_delay)
                        logger.warning(
                            f"{func.__name__} attempt {attempt + 1} failed: {e}. "
                            f"Retrying in {delay:.1f}s..."
                        )
                        await asyncio.sleep(delay)
                    else:
                        logger.error(f"{func.__name__} failed after {max_retries + 1} attempts: {e}")
            raise last_error
        return wrapper
    return decorator


class RateLimiter:
    """Simple token-bucket rate limiter."""

    def __init__(self, max_calls: int, period_seconds: float):
        self.max_calls = max_calls
        self.period = period_seconds
        self._semaphore = asyncio.Semaphore(max_calls)
        self._timestamps: list[float] = []

    async def acquire(self):
        await self._semaphore.acquire()
        loop = asyncio.get_event_loop()
        now = loop.time()
        self._timestamps.append(now)
        # Clean old timestamps
        cutoff = now - self.period
        self._timestamps = [t for t in self._timestamps if t > cutoff]

        if len(self._timestamps) >= self.max_calls:
            oldest = self._timestamps[0]
            wait_time = self.period - (now - oldest)
            if wait_time > 0:
                await asyncio.sleep(wait_time)

        # Schedule release after period
        asyncio.get_event_loop().call_later(self.period, self._semaphore.release)


def format_currency(amount: float) -> str:
    if abs(amount) >= 1_000_000:
        return f"${amount / 1_000_000:,.1f}M"
    elif abs(amount) >= 1_000:
        return f"${amount / 1_000:,.1f}K"
    return f"${amount:,.2f}"


def format_pct(value: float) -> str:
    return f"{value * 100:.1f}%"
