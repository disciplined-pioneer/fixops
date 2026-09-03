"""
core/logging.py — настройка логирования примера проекта через loguru.
"""

import os
import sys
import uuid
from pathlib import Path
from typing import Any, cast

from loguru import logger
from config import settings, _REPO_ROOT

# Исправление 1: Принудительный UTF-8 для stdout/stderr на Windows
# Cast к Any убирает ошибку "Cannot access attribute reconfigure for class TextIO"
if sys.platform == "win32":
    if hasattr(sys.stdout, "reconfigure"):
        cast(Any, sys.stdout).reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        cast(Any, sys.stderr).reconfigure(encoding="utf-8")

# Директория логов: по умолчанию <корень проекта>/logs
LOGS_DIR = Path(settings.logging.LOG_DIR)
if not LOGS_DIR.is_absolute():
    LOGS_DIR = _REPO_ROOT / LOGS_DIR
os.makedirs(LOGS_DIR, exist_ok=True)

# Сбрасываем стандартные хэндлеры
logger.remove()

# Исправление 2: Убран параметр encoding из logger.add(sys.stdout)
if settings.logging.ENV == "production":
    # Production: JSON -> stdout
    logger.add(
        sys.stdout,
        serialize=True,
        level="INFO",
        enqueue=True,
        backtrace=True,
        diagnose=False,
    )
else:
    # Development: Красивый цветной лог для консоли
    logger.add(
        sys.stdout,
        format=(
            "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | "
            "<level>{level: <8}</level> | "
            "<magenta>{extra[event]: <22}</magenta> | "
            "<cyan>{name}:{function}:{line}</cyan> | "
            "<level>{message}</level>"
        ),
        colorize=True,
        level="DEBUG",
        backtrace=True,
        diagnose=True,
    )

# Для файлового sink параметр encoding корректен
logger.add(
    str(LOGS_DIR / "app.log"),
    serialize=True,
    rotation="10 MB",
    retention="7 days",
    compression="gz",
    enqueue=True,
    level="INFO",
    backtrace=True,
    diagnose=False,
    encoding="utf-8",
)

# Базовый логгер
app_logger = logger.bind(
    service=settings.logging.SERVICE_NAME,
    environment=settings.logging.ENV,
)


def get_logger(event: str, **context: Any) -> Any:
    """Создаёт logger с контекстом события."""
    return app_logger.bind(
        event=event,
        event_id=str(uuid.uuid4()),
        **context,
    )
