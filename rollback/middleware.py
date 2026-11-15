import asyncio
import functools
import inspect
from contextlib import asynccontextmanager, contextmanager
from typing import Any, AsyncGenerator, Callable, Generator, List, Optional
from .core import Checkpoint, RollbackSession, create_session
from .diff import DiffSummary, compute_diff


@contextmanager
def transactional_step(
    session: Optional[RollbackSession] = None,
    label: str = "agent_step",
    auto_rollback_on_error: bool = True,
    target_files: Optional[List[str]] = None,
) -> Generator[RollbackSession, None, None]:
    """
    Synchronous context manager that establishes a transactional savepoint before an agent step executes.
    If an unhandled exception occurs, disk mutations are automatically rolled back atomically.
    """
    sess = session or create_session()
    cp = sess.checkpoint(label=label, target_files=target_files)

    try:
        yield sess
    except Exception as exc:
        if auto_rollback_on_error:
            sess.revert(cp.id)
        raise exc


@asynccontextmanager
async def async_transactional_step(
    session: Optional[RollbackSession] = None,
    label: str = "agent_async_step",
    auto_rollback_on_error: bool = True,
    target_files: Optional[List[str]] = None,
) -> AsyncGenerator[RollbackSession, None]:
    """
    Asynchronous context manager that establishes a transactional savepoint before an async agent step executes.
    If an unhandled exception occurs, disk mutations are automatically rolled back atomically.
    """
    sess = session or create_session()
    cp = sess.checkpoint(label=label, target_files=target_files)

    try:
        yield sess
    except Exception as exc:
        if auto_rollback_on_error:
            sess.revert(cp.id)
        raise exc


def transactional_tool(
    session: Optional[RollbackSession] = None,
    label: Optional[str] = None,
    auto_rollback_on_error: bool = True,
    target_files: Optional[List[str]] = None,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """
    Universal decorator for AI agent tool calling functions.
    Supports both synchronous and asynchronous (`async def`) agent tools.
    Wraps tool calls in an atomic micro-checkpoint and automatically reverts disk on error.
    """

    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        tool_name = label or func.__name__

        if inspect.iscoroutinefunction(func):

            @functools.wraps(func)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                sess = session or create_session()
                cp = sess.checkpoint(label=f"tool:{tool_name}", target_files=target_files)

                try:
                    result = await func(*args, **kwargs)
                    return result
                except Exception as e:
                    if auto_rollback_on_error:
                        sess.revert(cp.id)
                    raise e

            return async_wrapper

        else:

            @functools.wraps(func)
            def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
                sess = session or create_session()
                cp = sess.checkpoint(label=f"tool:{tool_name}", target_files=target_files)

                try:
                    result = func(*args, **kwargs)
                    return result
                except Exception as e:
                    if auto_rollback_on_error:
                        sess.revert(cp.id)
                    raise e

            return sync_wrapper

    return decorator
