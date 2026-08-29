import os
from typing import Tuple
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

_REPO_ROOT = os.path.dirname(os.path.abspath(__file__))


class AnalysisConfig(BaseSettings):
    """Конфигурация для анализатора ошибок."""

    # В Pydantic v2 значение по умолчанию передаётся через default,
    # а имя переменной из .env — через validation_alias
    RAW_PROJECT_PATH: str = Field(
        default="sample_app", validation_alias="ANALYSIS_PROJECT_PATH"
    )
    RAW_ERROR_LOG_PATH: str = Field(
        default="sample_app/logs/app.log",
        validation_alias="ANALYSIS_ERROR_LOG_PATH",
    )
    RAW_LOGS_DIR: str = Field(
        default="logs", validation_alias="ANALYSIS_LOGS_DIR"
    )

    EXTRA_IGNORE_DIRS: Tuple[str, ...] = ()
    LOG_TAIL_LINES: int = 50
    REQUIRED_ERROR_KEYS: Tuple[str, ...] = ("file", "function", "line", "error")

    # Современный способ задания настроек источника конфигурации
    model_config = SettingsConfigDict(
        env_file=".env",
        extra="ignore",
        env_file_encoding="utf-8",
    )

    @property
    def PROJECT_PATH(self) -> str:
        return os.path.join(_REPO_ROOT, self.RAW_PROJECT_PATH)

    @property
    def ERROR_LOG_PATH(self) -> str:
        return os.path.join(_REPO_ROOT, self.RAW_ERROR_LOG_PATH)

    @property
    def LOGS_DIR(self) -> str:
        return os.path.join(_REPO_ROOT, self.RAW_LOGS_DIR)


class Settings:
    analysis = AnalysisConfig()


settings = Settings()
