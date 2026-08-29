
import pytz
from datetime import datetime

MOSCOW_TZ = pytz.timezone("Europe/Moscow")

def now_moscow() -> datetime:
    """Возвращает текущее московское время без tzinfo (наивное datetime)."""
    return datetime.now(MOSCOW_TZ).replace(tzinfo=None)