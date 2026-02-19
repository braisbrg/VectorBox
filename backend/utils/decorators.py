import functools
import logging
import asyncio
from typing import Any, Callable, TypeVar, Union, List, Optional
from inspect import iscoroutinefunction

logger = logging.getLogger(__name__)

# Generic type for return value
R = TypeVar("R")

def safe_execution(
    fallback_return: Any = None,
    log_level: int = logging.ERROR,
    exclude_exceptions: tuple = (asyncio.CancelledError,)
):
    """
    Decorator to wrap async methods with a try-except block.
    
    Args:
        fallback_return: Value to return if exception occurs (default: None).
        log_level: Logging level for the caught exception (default: ERROR).
        exclude_exceptions: Tuple of exceptions to re-raise/ignore (e.g. CancelledError).
    
    Usage:
        @safe_execution(fallback_return=[])
        async def get_items(self, user_id):
            ...
    """
    def decorator(func: Callable[..., R]) -> Callable[..., R]:
        @functools.wraps(func)
        async def wrapper(*args, **kwargs) -> R:
            try:
                return await func(*args, **kwargs)
            except exclude_exceptions:
                # Re-raise excluded exceptions (e.g. task cancellation)
                raise
            except Exception as e:
                # Extract context if present (e.g. user_id in args)
                # Convention: wrapper fits instance methods (self, ...) or functions
                
                context = {}
                # Try to find user_id in kwargs or args
                if "user_id" in kwargs:
                    context["user_id"] = kwargs["user_id"]
                
                func_name = getattr(func, "__qualname__", func.__name__)
                
                # Log the error
                logger.log(
                    log_level, 
                    f"SafeExecution caught error in {func_name}: {e}", 
                    exc_info=True,
                    extra=context
                )
                
                return fallback_return
        
        if not iscoroutinefunction(func):
             # For sync functions? We focus on async for now as primarily needed for services.
             # But let's support sync just in case to be robust.
             @functools.wraps(func)
             def sync_wrapper(*args, **kwargs) -> R:
                try:
                    return func(*args, **kwargs)
                except exclude_exceptions:
                    raise
                except Exception as e:
                    func_name = getattr(func, "__qualname__", func.__name__)
                    logger.log(
                        log_level, 
                        f"SafeExecution caught error in {func_name}: {e}", 
                        exc_info=True
                    )
                    return fallback_return
             return sync_wrapper

        return wrapper
    return decorator
