import os
import json

from uuid import uuid4
from core.decorators import log_execution

from typing import TypedDict, Dict, Any
from langgraph.graph import StateGraph, END

from code_intel.indexer import ProjectIndexer
from code_intel.graph import GraphBuilder
from code_intel.error_analyzer import ErrorAnalyzer
from code_intel.context_builder import ContextBuilder
from code_intel.resolver import ProjectIndex, CallResolver

from ai.deepseek import DeepSeekHandler
from code_intel.executor import FixExecutor



# Общее состояние, которое передаётся между узлами графа
class FixOpsState(TypedDict):

    # Информация о проекте
    project_root: str
    error_log: dict
    logs_dir: str
    extra_ignore_dirs: tuple

    # Данные сессии LLM
    session_id: str | None

    # Данные анализа проекта
    modules: Any
    indexer: Any
    index: Any
    graph: Any
    analysis_result: Dict

    # Контекст и данные LLM
    llm_context: Any
    llm_prompt: str
    llm_response: str

    # Результат исправления
    fixed_file: str | None
    fix_applied: bool
    fix_error: str | None

    # Результат тестирования
    test_command: list[str]
    tests_passed: bool
    test_return_code: int | None
    test_stdout: str
    test_stderr: str

    # Настройки количества попыток
    fix_attempt: int
    max_fix_attempts: int


# Индексация файлов и структуры проекта
@log_execution(event="workflow_step", operation="indexer")
async def indexer_node(state: FixOpsState):

    # Объединяем стандартные и дополнительные директории для игнорирования
    indexer = ProjectIndexer()
    ignore_dirs = ProjectIndexer.IGNORE_DIRS + state["extra_ignore_dirs"]

    modules = await indexer.scan(
        state["project_root"],
        ignore_dirs=ignore_dirs
    )

    index = indexer.to_dict(modules)
    os.makedirs(state["logs_dir"], exist_ok=True)
    with open(os.path.join(state["logs_dir"], "index.json"), "w", encoding="utf-8") as f:
        json.dump(index, f, ensure_ascii=False, indent=2)

    return {
        "modules": modules,
        "indexer": indexer
    }


# Построение индекса проекта и графа вызовов
@log_execution(event="workflow_step", operation="graph_builder")
async def graph_builder_node(state: FixOpsState):
    idx = ProjectIndex(state["modules"])
    graph = await GraphBuilder(CallResolver(idx)).build(idx)

    with open(os.path.join(state["logs_dir"], "graph.json"), "w", encoding="utf-8") as f:
        json.dump(graph.to_dict(), f, ensure_ascii=False, indent=2)

    return {
        "index": idx,
        "graph": graph
    }


# Поиск проблемного участка кода по логу ошибки
@log_execution(event="workflow_step", operation="error_analyzer")
async def error_analyzer_node(state: FixOpsState):

    analyzer = ErrorAnalyzer(
        state["index"],
        state["graph"]
    )
    result = await analyzer.analyze_error(state["error_log"])

    return {
        "analysis_result": result
    }


# Подготовка релевантного контекста и prompt для LLM
@log_execution(event="workflow_step", operation="context_builder")
async def context_builder_node(state: FixOpsState):

    ctx = await ContextBuilder(
        state["project_root"]
    ).build_llm_context(
        state["index"],
        state["analysis_result"]
    )

    prompt = ContextBuilder.render_llm_prompt(ctx)

    # Сохраняем начальный промпт
    prompt_path = os.path.join(state["logs_dir"], "llm_prompt.md")
    with open(prompt_path, "w", encoding="utf-8") as f:
        f.write(prompt)

    return {
        "llm_context": ctx,
        "llm_prompt": prompt
    }


# Отправляет prompt в LLM
@log_execution(event="workflow_step", operation="llm")
async def handle_fix_request(state: FixOpsState):

    session_id = state.get("session_id")
    if session_id is None:
        session_id = uuid4().hex

    # handler = DeepSeekHandler(session_id=session_id)
    #response = await handler.generate_response(user_message=state["llm_prompt"])
    from pathlib import Path
    content = Path(r"D:\Programs\fixops-code-intel\fix.txt").read_text(encoding="utf-8")
    response = json.dumps({
        "id": "24778070-1c36-4ae0-a4bd-870afc7fc13e",
        "object": "chat.completion",
        "created": 1753000000,
        "model": "deepseek-v4-flash",
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": content
                },
                "logprobs": None,
                "finish_reason": "stop"
            }
        ],
        "usage": {
            "prompt_tokens": 22,
            "completion_tokens": 29,
            "total_tokens": 51,
            "prompt_cache_hit_tokens": 0,
            "prompt_cache_miss_tokens": 22
        }
    })

    return {
        "session_id": session_id,
        "llm_response": response,
    }


    # Применяет исправление из ответа LLM
@log_execution(event="workflow_step", operation="apply_fix")
async def apply_fix_node(state: FixOpsState):

    # Пытаемся распарсить JSON, если LLM вернула ответ в формате OpenAI
    try:
        data = json.loads(state['llm_response'])
        if "choices" in data:
            content = data["choices"][0]["message"]["content"]
        else:
            content = state['llm_response']
    except json.JSONDecodeError:
        content = state['llm_response']

    executor = FixExecutor(project_root=state["project_root"])
    try:
        file_path, changed = executor.apply_fix(content)

        return {
            "fixed_file": file_path,
            "fix_applied": changed,
            "fix_error": None,
        }

    except Exception as e:
        return {
            "fix_applied": False,
            "fix_error": str(e),
        }


# Запускает тесты проекта
@log_execution(event="workflow_step", operation="run_tests")
async def run_tests_node(state: FixOpsState):

    command = state.get("test_command") or ["pytest"]
    executor = FixExecutor(project_root=state["project_root"])
    result = executor.run_tests(command=command)

    return {
        "tests_passed": result.success,
        "test_return_code": result.return_code,
        "test_stdout": result.stdout,
        "test_stderr": result.stderr,
    }


# Формирует prompt для повторного исправления
@log_execution(event="workflow_step", operation="prepare_retry")
async def prepare_retry_node(state: FixOpsState):
    attempt = state.get("fix_attempt", 0) + 1
    prompt = f"""
        Предыдущее исправление не прошло тесты.
        Попытка: {attempt}
        Файл:
        {state.get("fixed_file")}
        Результат тестов:
        STDOUT:
        {state.get("test_stdout")}
        STDERR:
        {state.get("test_stderr")}
        Код завершения:
        {state.get("test_return_code")}
        Проанализируй ошибку тестов и предложи новое исправление.
        Верни исправление строго в формате:

        ```fix
        FILE: path/to/file.py
        <<<<<<< SEARCH
        старый код
        =======
        новый код
        >>>>>>> REPLACE
    """

    # Сохраняем промпт в уникальный файл
    prompt_path = os.path.join(state["logs_dir"], f"llm_prompt_{attempt}.md")
    with open(prompt_path, "w", encoding="utf-8") as f:
        f.write(prompt)

    return {
        "fix_attempt": attempt,
        "llm_prompt": prompt,
    }


# --- Роутер ---
# Решает, продолжать ли workflow после анализа ошибки
def should_continue_to_context(state: FixOpsState):

    if state["analysis_result"].get("resolved_node") is None:
        return "end"

    return "build_context"


# Решает, нужно ли запускать тесты
def should_run_tests(state: FixOpsState):

    if not state.get("fix_applied", False):
        attempt = state.get("fix_attempt", 0)
        max_attempts = state.get("max_fix_attempts", 3)
        if attempt >= max_attempts:
            return "failed"
        return "fix_error"
    return "run_tests"


# Решает, завершить работу или повторить исправление
def should_retry(state: FixOpsState):

    # Если тесты прошли
    if state.get("tests_passed", False):
        return "success"

    attempt = state.get("fix_attempt", 0)
    max_attempts = state.get("max_fix_attempts", 3)

    # Если достигнут лимит попыток
    if attempt >= max_attempts:
        return "failed"

    return "retry"


# --- Создание графа ---
def create_workflow():

    workflow = StateGraph(FixOpsState)

    # Регистрируем узлы
    workflow.add_node("indexer", indexer_node)
    workflow.add_node("graph_builder", graph_builder_node)
    workflow.add_node("error_analyzer", error_analyzer_node)
    workflow.add_node("context_builder", context_builder_node)
    workflow.add_node("llm", handle_fix_request)
    workflow.add_node("apply_fix", apply_fix_node)
    workflow.add_node("run_tests", run_tests_node)
    workflow.add_node("prepare_retry", prepare_retry_node)

    # Задаём начальный узел
    workflow.set_entry_point("indexer")

    # Основной pipeline анализа
    workflow.add_edge("indexer", "graph_builder")
    workflow.add_edge("graph_builder", "error_analyzer")

    # Проверяем результат анализа
    workflow.add_conditional_edges(
        "error_analyzer",
        should_continue_to_context,
        {
            "build_context": "context_builder",
            "end": END,
        },
    )

    # Передаём контекст в LLM
    workflow.add_edge("context_builder", "llm")

    # Применяем исправление после ответа LLM
    workflow.add_edge("llm", "apply_fix")

    # После исправления запускаем тесты
    workflow.add_conditional_edges(
        "apply_fix",
        should_run_tests,
        {
            "run_tests": "run_tests",
            "fix_error": "prepare_retry",
            "failed": END,
        },
    )

    # Проверяем результат тестов
    workflow.add_conditional_edges(
        "run_tests",
        should_retry,
        {
            "success": END,
            "retry": "prepare_retry",
            "failed": END,
        },
    )

    # После ошибки повторно отправляем запрос LLM
    workflow.add_edge("prepare_retry", "llm")

    return workflow.compile()
