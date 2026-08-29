"""
Абстрактный базовый класс для AI обработчиков
"""

from abc import ABC, abstractmethod
from pathlib import Path


class AIHandler(ABC):
    """
    Абстрактный обработчик для работы с AI моделями.

    Определяет интерфейс и общую логику для всех AI провайдеров.
    """

    def __init__(self, session_id: str | int):
        self.session_id = str(session_id)
        self.prompt_path = Path("data/prompt.txt")

    async def load_prompt(self) -> str:
        """
        Загружает системный prompt из файла.

        :return: Текст prompt'а или пустая строка, если файл не существует
        """
        if self.prompt_path.exists():
            return self.prompt_path.read_text(encoding="utf-8")
        return ""

    @abstractmethod
    async def get_history(self) -> list[dict]:
        """
        Получает историю сообщений в формате конкретной модели.

        :return: Список сообщений
        """
        pass

    @abstractmethod
    async def generate_response(self, user_message: str) -> str:
        """
        Генерирует ответ от ИИ модели на основе входящего сообщения.

        :param user_message: Сообщение пользователя
        :return: Текст ответа модели
        """
        pass
