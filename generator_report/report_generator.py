import json
import logging
from core.decorators import log_execution
from core.logging import app_logger


@log_execution(event="report.load_sales")
def load_sales_data(path):
    app_logger.info("Загрузка данных из %s", path)
    with open(path, encoding="utf-8") as f:
        return json.load(f)


@log_execution(event="report.calculate_totals")
def calculate_daily_totals(sales):
    totals = {}
    for entry in sales:
        day = entry["date"]
        totals[day] = totals.get(day, 0) + entry["amount"]
    return totals


@log_execution(event="report.calculate_growth")
def calculate_growth(daily_totals):
    """Считает прирост выручки день ко дню (today -> next day)."""
    days = sorted(daily_totals.keys())
    growth = {}
    for i in range(len(days)):
        today = days[i]
        next_day = days[i + 1]  # BUG: на последней итерации i+1 выходит за границы списка
        growth[today] = daily_totals[next_day] - daily_totals[today]
    return growth


@log_execution(event="report.build")
def build_report(path):
    sales = load_sales_data(path)
    totals = calculate_daily_totals(sales)
    app_logger.info("Дневные суммы посчитаны: %s", totals)
    growth = calculate_growth(totals)
    app_logger.info("Прирост по дням: %s", growth)
    return growth
