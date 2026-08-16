"""
reproduce.py — воспроизводит реальную ошибку тестового проекта.

Запуск:  python sample_app/reproduce.py

Выполняет CheckoutService().checkout() с неизвестным SKU, ловит настоящую
ошибку и печатает её в формате лога (тот же, что лежит в errors/error.json).
"""

import os
import sys
import traceback

SAMPLE_APP = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SAMPLE_APP)

from api.checkout import CheckoutService


def main():
    order = {"items": [{"sku": "SKU-999", "qty": 2}]}
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
        print(error_log)
        raise


if __name__ == "__main__":
    main()