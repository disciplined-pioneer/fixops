import os
from typing import Tuple
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

class DeepSeekConfig(BaseSettings):
    """Конфигурация для DeepSeek API."""

    TOKEN: str = ''

    model_config = SettingsConfigDict(
        env_prefix="DEEPSEEK_",
        env_file=".env",
        extra="ignore"
    )

class RedisConfig(BaseSettings):
    """Конфигурация для Redis."""

    HOST: str = "localhost"
    PORT: int = 6379
    PASSWORD: str = ""
    NAME: str = "0"  # В Redis имя базы — это число от 0 до 15

    model_config = SettingsConfigDict(
        env_prefix="REDIS_",
        env_file=".env",
        extra="ignore",
        env_file_encoding="utf-8",
    )

    @property
    def URL(self) -> str:
        # Если пароль есть — формируем URL с ним, если нет — без авторизации
        if self.PASSWORD:
            return f"redis://:{self.PASSWORD}@{self.HOST}:{self.PORT}/{self.NAME}"
        return f"redis://{self.HOST}:{self.PORT}/{self.NAME}"

class Settings:
    analysis = AnalysisConfig()
    deepseek = DeepSeekConfig()
    redis = RedisConfig()


settings = Settings()
