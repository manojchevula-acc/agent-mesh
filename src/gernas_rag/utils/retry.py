"""Async retry decorator with exponential backoff."""

import asyncio
import functools
from collections.abc import Awaitable, Callable
from typing import TypeVar

from .logging import get_logger

T = TypeVar("T")
logger = get_logger(__name__)


def async_retry(
    max_attempts: int = 3,
    backoff_factor: float = 2.0,
    exceptions: tuple[type[Exception], ...] = (Exception,),
) -> Callable[[Callable[..., Awaitable[T]]], Callable[..., Awaitable[T]]]:
    """Decorator for async functions.

    Retries on the specified exceptions with exponential backoff:
    1s, 2s, 4s, ... up to ``max_attempts``.
    """

    def decorator(func: Callable[..., Awaitable[T]]) -> Callable[..., Awaitable[T]]:
        @functools.wraps(func)
        async def wrapper(*args: object, **kwargs: object) -> T:
            last_exc: Exception | None = None
            for attempt in range(max_attempts):
                try:
                    return await func(*args, **kwargs)
                except exceptions as exc:
                    last_exc = exc
                    if attempt == max_attempts - 1:
                        raise
                    wait = backoff_factor ** attempt
                    logger.warning(
                        "Retrying after error",
                        func=func.__name__,
                        error=str(exc),
                        attempt=attempt + 1,
                        wait=wait,
                    )
                    await asyncio.sleep(wait)
            # Unreachable, but keeps type checkers satisfied.
            assert last_exc is not None
            raise last_exc

        return wrapper

    return decorator
