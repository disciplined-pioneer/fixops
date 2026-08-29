from uuid import uuid4
from datetime import datetime
from typing import Optional

from typing_extensions import Self
from aredis_om import Field, HashModel
from aredis_om.model.model import RedisOmConfig

from core.redis import redis_conn as conn
from db.redis.models.mapped_columns import now_moscow


class CustomRedisOmConfig(RedisOmConfig):
    """Кастомный конфиг с правильными именами атрибутов Pydantic v1, используемыми в aredis_om."""
    orm_mode = True
    arbitrary_types_allowed = True


class ModelAdmin(HashModel):
    """Базовый класс с CRUD-операциями для моделей Redis-OM."""

    @classmethod
    async def create(cls, ttl: int | None = None, **kwargs) -> Self:
        """
        Создает и сохраняет новый объект модели в БД.

        :param ttl: Время жизни объекта в секундах (TTL). Если None, то бессрочно.
        :param kwargs: Атрибуты для инициализации полей модели.
        :return: Созданный экземпляр модели.
        """
        model = cls(**kwargs)
        await model.save()

        if ttl:
            await model.expire(ttl)

        return model

    async def update(self, **kwargs) -> None:
        """
        Обновляет значения полей текущего объекта и сохраняет изменения в БД.

        :param kwargs: Именованные аргументы (поле=значение) для обновления.
        """
        for key, value in kwargs.items():
            if hasattr(self, key):
                setattr(self, key, value)

        await self.save()

    async def delete_instance(self) -> None:
        """
        Удаляет текущий экземпляр модели из базы данных по его первичный ключу (PK).
        """
        if self.pk is not None:
            await super().delete(self.pk)

    @classmethod
    async def get_by_pk(cls, pk: str) -> Optional[Self]:
        """
        Находит и возвращает один объект модели по его первичному ключу.

        :param pk: Первичный ключ (PK) объекта.
        :return: Экземпляр модели или None, если объект не найден / произошла ошибка.
        """
        try:
            res = await super().get(pk)
            return res  # type: ignore
        except Exception:
            return None

    @classmethod
    async def filter(cls, **kwargs) -> list[Self]:
        """
        Ищет и фильтрует объекты модели по совпадению указанных атрибутов.

        :param kwargs: Условия фильтрации (поле=требуемое_значение).
        :return: Список найденных объектов, соответствующих всем условиям.
        """
        all_pks = await cls.all_pks()
        result = []

        async for pk in all_pks:
            obj = await cls.get_by_pk(pk)
            if obj and all(getattr(obj, k, None) == v for k, v in kwargs.items()):
                result.append(obj)
        return result

    async def set_ttl(self, ttl: int) -> bool:
        """
        Устанавливает или обновляет время жизни (TTL) для текущего объекта.

        :param ttl: Время жизни в секундах.
        :return: True, если TTL успешно установлен, иначе False.
        """
        res = await self.expire(ttl)
        return bool(res)


class MessageAI(ModelAdmin):
    """Хранение сообщений с ИИ."""

    pk: str | None = Field(default_factory=lambda: str(uuid4()))
    session_id: str = Field(index=True, description="ID чата или сессии")
    role: str = Field(index=True, description="Роль отправителя: user, assistant, system")
    content: str = Field(description="Текст сообщения")
    created_at: datetime = Field(
        default_factory=now_moscow(),
        index=True,
        description="Время создания сообщения (московское)"
    )
    model_config = CustomRedisOmConfig()

    class Meta:
        database = conn

    @classmethod
    async def delete_all_for_session(cls, session_id: str | int):
        msgs = await cls.filter(session_id=str(session_id))
        for msg in msgs:
            await msg.delete_instance()
