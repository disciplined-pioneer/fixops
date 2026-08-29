import os
from typing import Tuple
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

_REPO_ROOT = os.path.dirname(os.path.abspath(__file__))

class AnalysisConfig(BaseSettings):
    """Конфигурация для анализатора ошибок."""

    # В Pydantic v2 значение по умолчанию передаётся через default,
    # а имя переменной из .env — через validation_alias
    # Теперь пути задаются динамически в analyze_error.py
    EXTRA_IGNORE_DIRS: Tuple[str, ...] = ()
    LOG_TAIL_LINES: int = 50
    REQUIRED_ERROR_KEYS: Tuple[str, ...] = ("file", "function", "line", "error")
    
    # Современный способ задания настроек источника конфигурации
    model_config = SettingsConfigDict(
        env_file=".env",
        extra="ignore",
        env_file_encoding="utf-8",
    )


class Settings:
    analysis = AnalysisConfig()


settings = Settings()
