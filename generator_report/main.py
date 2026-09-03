import logging
from pathlib import Path

from core.logging import app_logger
from report_generator import build_report


def main():
    app_logger.info("Запуск формирования отчёта по продажам")
    data_path = Path(__file__).parent / "data" / "sales_data.json"
    try:
        result = build_report(data_path)
        app_logger.info("Отчёт успешно сформирован: %s", result)
    except Exception:
        # Ловим исключение здесь специально, чтобы полный traceback
        # гарантированно попал в лог-файл, а не только в stderr.
        app_logger.exception("Ошибка при формировании отчёта")


if __name__ == "__main__":
    main()
