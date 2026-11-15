import functools
from contextlib import contextmanager
from typing import Any, Callable, Generator, List, Optional
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
    Context manager that establishes a transactional savepoint before an agent step executes.
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
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """
    Decorator for AI agent tool calling functions.
    Wraps tool calls in an atomic micro-checkpoint.
    """

    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            tool_name = label or func.__name__
            sess = session or create_session()
            cp = sess.checkpoint(label=f"tool:{tool_name}")

            try:
                result = func(*args, **kwargs)
                return result
            except Exception as e:
                if auto_rollback_on_error:
                    sess.revert(cp.id)
                raise e

        return wrapper

    return decorator
