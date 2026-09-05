import inspect
import time
from functools import wraps

from core.logging import get_logger


SENSITIVE_FIELDS = {
    "password",
    "token",
    "access_token",
    "refresh_token",
    "authorization",
    "api_key",
    "secret",
}


def sanitize(value):
    """
    Удаляет потенциально чувствительные данные
    перед записью в лог.
    """

    if isinstance(value, dict):

        return {
            key: "***"
            if key.lower() in SENSITIVE_FIELDS
            else sanitize(val)
            for key, val in value.items()
        }

    if isinstance(value, (list, tuple)):

        return [
            sanitize(item)
            for item in value
        ]

    # Не пытаемся сериализовать огромные/сложные объекты
    if isinstance(value, (str, int, float, bool, type(None))):
        return value

    return f"<{type(value).__name__}>"


def log_execution(
    event: str,
    operation: str | None = None,
):
    """
    Логирует выполнение sync/async функции.

    Записывает:

    - event
    - operation
    - function
    - module
    - file
    - line
    - status
    - duration
    - arguments
    - exception
    - traceback
    """

    def decorator(func):

        operation_name = operation or func.__name__

        # ====================================================
        # ASYNC
        # ====================================================

        if inspect.iscoroutinefunction(func):

            @wraps(func)
            async def async_wrapper(*args, **kwargs):

                start = time.perf_counter()

                log = get_logger(
                    event=event,
                    operation=operation_name,
                    function=func.__qualname__,
                    module=func.__module__,
                )

                log.debug(
                    "Function started"
                )

                try:

                    result = await func(
                        *args,
                        **kwargs,
                    )

                    duration_ms = (
                        time.perf_counter() - start
                    ) * 1000

                    log.bind(
                        status="success",
                        severity="INFO",
                        duration_ms=round(
                            duration_ms,
                            2,
                        ),
                    ).info(
                        "Function completed"
                    )

                    return result

                except Exception as exc:

                    duration_ms = (
                        time.perf_counter() - start
                    ) * 1000

                    log.bind(
                        status="error",
                        severity="ERROR",
                        duration_ms=round(
                            duration_ms,
                            2,
                        ),
                        error={
                            "type": type(exc).__name__,
                            "message": str(exc),
                        },
                        arguments={
                            "args": sanitize(args),
                            "kwargs": sanitize(kwargs),
                        },
                    ).exception(
                        "Function failed"
                    )

                    raise

            return async_wrapper

        # ====================================================
        # SYNC
        # ====================================================

        @wraps(func)
        def sync_wrapper(*args, **kwargs):

            start = time.perf_counter()

            log = get_logger(
                event=event,
                operation=operation_name,
                function=func.__qualname__,
                module=func.__module__,
            )

            log.debug(
                "Function started"
            )

            try:

                result = func(
                    *args,
                    **kwargs,
                )

                duration_ms = (
                    time.perf_counter() - start
                ) * 1000

                log.bind(
                    status="success",
                    severity="INFO",
                    duration_ms=round(
                        duration_ms,
                        2,
                    ),
                ).info(
                    "Function completed"
                )

                return result

            except Exception as exc:

                duration_ms = (
                    time.perf_counter() - start
                ) * 1000

                log.bind(
                    status="error",
                    severity="ERROR",
                    duration_ms=round(
                        duration_ms,
                        2,
                    ),
                    error={
                        "type": type(exc).__name__,
                        "message": str(exc),
                    },
                    arguments={
                        "args": sanitize(args),
                        "kwargs": sanitize(kwargs),
                    },
                ).exception(
                    "Function failed"
                )

                raise

        return sync_wrapper

    return decorator