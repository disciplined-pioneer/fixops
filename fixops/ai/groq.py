"""
Обработчик для моделей Groq (чистый диалоговый режим)
"""

from groq import AsyncGroq

from config import settings
from .base import AIHandler
from .history_manager import HistoryManager


class GroqHandler(AIHandler):
    """
    Обработчик для прямого общения с моделями Groq.
    """

    def __init__(self, session_id: str, model_name: str = "openai/gpt-oss-120b"):
        super().__init__(session_id)
        self.api_key = settings.groq.TOKEN
        self.model_name = model_name

        self.client = AsyncGroq(
            api_key=self.api_key,
        )

    async def get_history(self) -> list[dict]:
        """
        Получает историю переписки для текущего чата.
        """
        return await HistoryManager.get_history(
            session_id=self.session_id,
            format_type="openai",
        )

    async def generate_response(self, user_message: str) -> str:
        """
        Отправляет сообщение пользователя в Groq
        и возвращает ответ модели.
        """
        history = await self.get_history()
        prompt = await self.load_prompt()

        messages = []

        if prompt:
            messages.append({
                "role": "system",
                "content": prompt,
            })

        messages.extend(history)

        messages.append({
            "role": "user",
            "content": user_message,
        })

        response = await self.client.chat.completions.create(
            model=self.model_name,
            messages=messages,  # type: ignore
            stream=False,
        )

        raw_content = response.choices[0].message.content
        reply = raw_content.strip() if raw_content else ""

        await HistoryManager.save_message(
            self.session_id,
            role="user",
            content=user_message,
        )

        await HistoryManager.save_message(
            self.session_id,
            role="assistant",
            content=reply,
        )

        return reply
