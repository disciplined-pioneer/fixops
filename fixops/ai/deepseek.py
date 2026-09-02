"""
Обработчик для модели DeepSeek (чистый диалоговый режим)
"""

from openai import AsyncOpenAI

from config import settings
from .base import AIHandler
from .history_manager import HistoryManager


class DeepSeekHandler(AIHandler):
    """
    Обработчик для прямого общения с DeepSeek.
    """

    def __init__(self, session_id: str, model_name: str = "deepseek-v4-flash"):
        super().__init__(session_id)
        self.api_key = settings.deepseek.TOKEN
        self.model_name = model_name

        self.client = AsyncOpenAI(
            api_key=self.api_key,
            base_url="https://api.deepseek.com"
        )

    async def get_history(self) -> list[dict]:
        """
        Получает историю переписки для текущего чата.
        """
        return await HistoryManager.get_history(
            session_id=self.session_id,
            format_type="openai"
        )

    async def generate_response(self, user_message: str) -> str:
        """
        Отправляет сообщение пользователя в DeepSeek и возвращает ответ.
        """
        history = await self.get_history()
        prompt = await self.load_prompt()

        messages = []
        if prompt:
            messages.append({"role": "system", "content": prompt})

        messages.extend(history)
        messages.append({"role": "user", "content": user_message})

        response = await self.client.chat.completions.create(
            model=self.model_name,
            messages=messages, # type: ignore
            stream=False
        )

        # Безопасная извлечение содержимого (защита от None)
        raw_content = response.choices[0].message.content
        reply = raw_content.strip() if raw_content else ""

        # Сохраняем шаг диалога в историю
        await HistoryManager.save_message(self.session_id, role="user", content=user_message)
        await HistoryManager.save_message(self.session_id, role="assistant", content=reply)

        return reply
