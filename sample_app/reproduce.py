"""
reproduce.py — воспроизводит реальную ошибку тестового проекта.

Запуск:  python sample_app/reproduce.py

Выполняет CheckoutService().checkout() с неизвестным SKU, ловит настоящую
ошибку и печатает её в формате лога. Логирование — инфраструктура из
core/logging.py (loguru): события падают в консоль и в лог-файл проекта
(<sample_app>/logs/app.log). Последняя ERROR-запись этого файла — источник
ошибки для analyze_error.py.
"""

import os
import sys
import traceback

SAMPLE_APP = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(SAMPLE_APP)
sys.path.insert(0, SAMPLE_APP)
sys.path.insert(0, REPO_ROOT)

# Логи sample_app пишем внутрь проекта, в sample_app/logs (до импорта core.logging).
os.environ.setdefault("LOG_DIR", os.path.join(SAMPLE_APP, "logs"))

from sample_app.core.logging import get_logger  # noqa: E402

from api.checkout import CheckoutService  # noqa: E402

LOG = get_logger(event="reproduce.run")


def main():
    order = {"items": [{"sku": "SKU-999", "qty": 2}]}
    LOG.info("Reproducing error", order=order)
    try:
        CheckoutService().checkout(order)
    except Exception as e:
        tb = traceback.extract_tb(sys.exc_info()[2])
        last = tb[-1]
        error_log = {
            "file": os.path.relpath(last.filename, SAMPLE_APP).replace(os.sep, "/"),
            "line": last.lineno,
            "function": last.name,
            "error": f"{type(e).__name__}: {e}",
        }
        LOG.bind(**error_log).error("Checkout failed")
        print(f"LOG: {error_log}")
        raise


if __name__ == "__main__":
    main()