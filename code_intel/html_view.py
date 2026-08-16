"""
html_view.py — рендер интерактивной HTML-визуализации графа.

Берёт шаблон code_intel/graph_view.template.html и подставляет в него
реальные данные графа и анализа вместо плейсхолдеров:
  - __GRAPH_JSON__      -> graph.to_dict()
  - __ANALYSIS_JSON__   -> результат analyze_error()

В результате получается автономный HTML-файл (можно открыть в браузере
без сервера), как исходный graph_view.html, но с данными текущего анализа.
"""

from __future__ import annotations

import json
import os

_TEMPLATE_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "graph_view.template.html"
)


def _to_js(value) -> str:
    """Сериализация в JS-совместимый JSON (безопасно для вставки в <script>)."""
    dumped = json.dumps(value, ensure_ascii=False, indent=2)
    return dumped.replace("</", "<\\/")


def render_html_view(graph: dict, analysis: dict) -> str:
    """Собирает HTML-визуализацию из данных графа и результата анализа."""
    with open(_TEMPLATE_PATH, "r", encoding="utf-8") as f:
        html = f.read()

    html = html.replace("__GRAPH_JSON__", _to_js(graph))
    html = html.replace("__ANALYSIS_JSON__", _to_js(analysis))
    if "__GRAPH_JSON__" in html or "__ANALYSIS_JSON__" in html:
        raise RuntimeError("Не удалось подставить данные в шаблон HTML-визуализации")

    return html


def save_html_view(graph: dict, analysis: dict, output_path: str) -> str:
    """Сохраняет HTML-визуализацию в output_path и возвращает путь."""
    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(render_html_view(graph, analysis))
    return output_path
