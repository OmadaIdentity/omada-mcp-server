# logging_config.py
"""Logging setup and with_function_logging decorator.

The decorator uses a contextvars.ContextVar to carry each async task's effective
log level rather than mutating the global logger.  This prevents concurrent MCP
tool calls from interfering with each other's log-level overrides.
"""
import asyncio
import logging
import os
from contextvars import ContextVar

# ── Per-async-context log-level override ──────────────────────────────────────
# Each task sets this at entry and resets it in finally.  Because asyncio copies
# the context when creating a new task, concurrent tool calls are fully isolated.
_context_log_level: ContextVar[int] = ContextVar("omada_log_level", default=logging.NOTSET)


class ContextAwareLevelFilter(logging.Filter):
    """
    A logging filter that enforces a per-async-context effective level.

    When a context-specific override is active (set via _context_log_level),
    only records at or above that level pass.  When no override is active
    (NOTSET), all records pass through — the handler's own level is the gate.
    """

    def __init__(self, default_level: int = logging.INFO):
        super().__init__()
        self.default_level = default_level

    def filter(self, record: logging.LogRecord) -> bool:
        context_level = _context_log_level.get()
        effective = context_level if context_level != logging.NOTSET else self.default_level
        return record.levelno >= effective


def setup_logging():
    """Configure logging with both file and console handlers."""
    log_level_str = os.getenv("LOG_LEVEL", "INFO").upper()
    log_level = getattr(logging, log_level_str, logging.INFO)
    log_file = os.getenv("LOG_FILE", "omada_mcp_server.log")

    # Convert to absolute path if relative path provided
    if not os.path.isabs(log_file):
        script_dir = os.path.dirname(os.path.abspath(__file__))
        log_file = os.path.join(script_dir, log_file)

    # Create logs directory if it doesn't exist
    log_dir = os.path.dirname(log_file)
    if log_dir and not os.path.exists(log_dir):
        os.makedirs(log_dir)

    # One shared filter instance — carries the default level for non-function contexts.
    level_filter = ContextAwareLevelFilter(default_level=log_level)

    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    stream_handler = logging.StreamHandler()

    fmt = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
    for handler in (file_handler, stream_handler):
        # Set handler level to DEBUG so records are not dropped before the filter runs.
        # ContextAwareLevelFilter is the sole gate for per-function level overrides.
        handler.setLevel(logging.DEBUG)
        handler.setFormatter(fmt)
        handler.addFilter(level_filter)

    root_logger = logging.getLogger()
    # Set root to DEBUG — the filter above controls effective output per context.
    root_logger.setLevel(logging.DEBUG)
    # Clear any handlers added by a previous basicConfig call (e.g., in tests).
    root_logger.handlers.clear()
    root_logger.addHandler(file_handler)
    root_logger.addHandler(stream_handler)

    # Configure cache logger — propagate to root so the same handlers apply.
    cache_logger = logging.getLogger("cache")
    cache_logger.setLevel(logging.DEBUG)
    cache_logger.propagate = True

    init_logger = logging.getLogger(__name__)
    init_logger.info(f"Logging initialized. Writing logs to: {os.path.abspath(log_file)}")
    init_logger.info(f"Log level: {log_level_str} (per-function overrides via LOG_LEVEL_<name>)")


# Module-level logger for use by the decorator
logger = logging.getLogger("server")


def get_function_log_level(function_name: str) -> int:
    """
    Get the log level for a specific function, falling back to global LOG_LEVEL.

    Args:
        function_name: Name of the function to get log level for

    Returns:
        logging level constant (e.g., logging.DEBUG, logging.INFO)
    """
    global_level_str = os.getenv("LOG_LEVEL", "INFO").upper()
    func_level_str = os.getenv(f"LOG_LEVEL_{function_name}", "").upper()
    level_str = func_level_str if func_level_str else global_level_str
    return getattr(logging, level_str, logging.INFO)


def with_function_logging(func):
    """
    Decorator that sets a per-context log level for each tool call.

    Uses contextvars.ContextVar so concurrent async tool calls cannot
    interfere with each other's effective log level.

    Usage:
        @with_function_logging
        @mcp.tool()
        async def my_function():
            pass
    """
    if asyncio.iscoroutinefunction(func):

        async def async_wrapper(*args, **kwargs):
            level = get_function_log_level(func.__name__)
            token = _context_log_level.set(level)
            logger.info(f"ENTERING function: {func.__name__}")
            try:
                result = await func(*args, **kwargs)
                logger.info(f"EXITING function: {func.__name__}")
                return result
            except Exception as e:
                logger.info(
                    f"EXITING function: {func.__name__} with error: {type(e).__name__}"
                )
                raise
            finally:
                _context_log_level.reset(token)

        # Manually preserve metadata without setting __wrapped__
        # (__wrapped__ causes FastMCP to bypass the decorator)
        async_wrapper.__name__ = func.__name__
        async_wrapper.__doc__ = func.__doc__
        async_wrapper.__module__ = func.__module__
        async_wrapper.__qualname__ = func.__qualname__
        async_wrapper.__annotations__ = func.__annotations__
        async_wrapper.__dict__.update(func.__dict__)
        return async_wrapper

    else:

        def sync_wrapper(*args, **kwargs):
            level = get_function_log_level(func.__name__)
            token = _context_log_level.set(level)
            logger.info(f"ENTERING function: {func.__name__}")
            try:
                result = func(*args, **kwargs)
                logger.info(f"EXITING function: {func.__name__}")
                return result
            except Exception as e:
                logger.info(
                    f"EXITING function: {func.__name__} with error: {type(e).__name__}"
                )
                raise
            finally:
                _context_log_level.reset(token)

        # Manually preserve metadata without setting __wrapped__
        sync_wrapper.__name__ = func.__name__
        sync_wrapper.__doc__ = func.__doc__
        sync_wrapper.__module__ = func.__module__
        sync_wrapper.__qualname__ = func.__qualname__
        sync_wrapper.__annotations__ = func.__annotations__
        sync_wrapper.__dict__.update(func.__dict__)
        return sync_wrapper
