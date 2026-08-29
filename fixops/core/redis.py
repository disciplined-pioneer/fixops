from config import settings
from aredis_om import get_redis_connection

redis_conn = get_redis_connection(
    url=settings.redis.URL,
    decode_responses=True,
    encoding='utf-8',
    max_connections=50,
    health_check_interval=30
)
