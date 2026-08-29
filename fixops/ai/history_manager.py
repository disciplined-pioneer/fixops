"""
Менеджер для работы с историей сообщений
"""

from typing import Literal, Optional
from db.redis.models.models import MessageAI


class HistoryManager:
    """
    Менеджер для получения и сохранения истории сообщений в разных форматах.

    Поддерживает следующие форматы в зависимости от API модели:
    - openai: {"role": "user|assistant", "content": "..."}
    - gemini: {"role": "user|model", "parts": [{"text": "..."}]}
    """

    @staticmethod
    async def get_history(
        session_id: str | int,
        format_type: Literal["openai", "gemini"] = "openai",
        limit: Optional[int] = None
    ) -> list[dict]:
        """
        Получает историю сообщений для указанной сессии в нужном формате.

        :param session_id: Идентификатор чата/сессии/пользователя
        :param format_type: Формат ("openai" или "gemini")
        :param limit: Ограничение количества последних сообщений (опционально)
        :return: Список сообщений в формате модели
        """
        # Запрашиваем сообщения по session_id
        messages = await MessageAI.filter(session_id=str(session_id))

        # Сортируем по хронологии
        messages.sort(key=lambda x: x.created_at)

        if limit:
            messages = messages[-limit:]

        history = []
        for msg in messages:
            if not msg.content:
                continue

            if format_type == "openai":
                history.append(HistoryManager._format_openai(msg))
            elif format_type == "gemini":
                history.append(HistoryManager._format_gemini(msg))

        return history

    @staticmethod
    async def save_message(session_id: str | int, role: str, content: str) -> MessageAI:
        """
        Сохраняет сообщение в историю.

        :param session_id: Идентификатор чата/сессии
        :param role: Роль ("user" или "assistant"/"model")
        :param content: Текст сообщения
        :return: Созданная запись MessageAI
        """
        return await MessageAI.create(
            session_id=str(session_id),
            role=role,
            content=content
        )

    @staticmethod
    def _format_openai(msg: MessageAI) -> dict:
        """Форматирует сообщение для OpenAI / DeepSeek API"""
        role = "assistant" if msg.role in ("assistant", "model") else "user"
        return {
            "role": role,
            "content": msg.content
        }

    @staticmethod
    def _format_gemini(msg: MessageAI) -> dict:
        """Форматирует сообщение для Google Gemini API"""
        role = "model" if msg.role in ("assistant", "model") else "user"
        return {
            "role": role,
            "parts": [{"text": msg.content}]
        }
