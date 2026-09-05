import os
import traceback
from pathlib import Path

from core.logging import app_logger
from report_generator import build_report


def main():
    app_logger.info("Запуск формирования отчёта по продажам")
    data_path = Path(__file__).parent / "sales_data.json" # Путь из листинга папки
    
    try:
        result = build_report(data_path)
        app_logger.info("Отчёт успешно сформирован: %s", result)
    except Exception as e:
        # Извлекаем данные об ошибке для FixOps
        tb = traceback.extract_tb(e.__traceback__)
        last = tb[-1]
        
        # Логируем с привязкой метаданных в extra
        app_logger.bind(
            file=os.path.relpath(last.filename, Path(__file__).parent).replace(os.sep, "/"),
            function=last.name,
            line=last.lineno,
            error=f"{type(e).__name__}: {e}"
        ).exception("Ошибка при формировании отчёта")


if __name__ == "__main__":
    main()

