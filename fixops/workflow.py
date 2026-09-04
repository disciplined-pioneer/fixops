import os
import sys
import json
import asyncio
import subprocess

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
from ai.groq import GroqHandler
from code_intel.executor import FixExecutor

from config import settings
from core.logging import app_logger


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
    reproduction_passed: bool  # Ergebnis запуска reproduce.py
    test_return_code: int | None
    test_stdout: str
    test_stderr: str
    test_result_type: str | None

    # Настройки количества попыток
    fix_attempt: int
    max_fix_attempts: int


# Индексация файлов и структуры проекта
@log_execution(event="workflow_step", operation="indexer")
async def indexer_node(state: FixOpsState):

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

    handler = GroqHandler(session_id=session_id)
    try:
        content = await handler.generate_response(user_message=state["llm_prompt"])
    except Exception as e:
        app_logger.bind(event="workflow_step", operation="llm").error(f"LLM request failed: {e}")
        return {
            "session_id": session_id,
            "llm_response": json.dumps({"error": str(e)}),
        }

    return {
        "session_id": session_id,
        "llm_response": content,
    }


# Применяет исправление из ответа LLM
@log_execution(event="workflow_step", operation="apply_fix")
async def apply_fix_node(state: FixOpsState):

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

    # Привязываем контекст event и operation к логгеру, чтобы не провоцировать KeyError
    logger = app_logger.bind(event="workflow_step", operation="run_tests")

    if result.success:
        logger.info(f"TESTS PASSED: {result.stdout.splitlines()[-1] if result.stdout else 'All tests passed'}")
    else:
        logger.error(f"TESTS FAILED: \n{result.stderr or result.stdout}")

    # Запуск скрипта воспроизведения (reproduce.py)
    repro_passed = False
    repro_script = os.path.join(state["project_root"], "reproduce.py")
    if os.path.exists(repro_script):
        proc = await asyncio.to_thread(
            subprocess.run,
            [sys.executable, repro_script],
            cwd=state["project_root"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace"
        )
        repro_passed = (proc.returncode == 0)
        if repro_passed:
            logger.info("REPRODUCTION PASSED")
        else:
            logger.error(f"REPRODUCTION FAILED: \n{proc.stderr or proc.stdout}")

    return {
        "tests_passed": result.success,
        "reproduction_passed": repro_passed,
        "test_return_code": result.return_code,
        "test_stdout": result.stdout,
        "test_stderr": result.stderr,
        "test_result_type": result.result_type,
    }


# Формирует prompt для повторного исправления
@log_execution(event="workflow_step", operation="prepare_retry")
async def prepare_retry_node(state: FixOpsState):

    attempt = state.get("fix_attempt", 0) + 1
    prompt = f"""
        Предыдущее исправление не прошло тесты.
        Попытка: {attempt}
        Файл: {state.get("fixed_file")}

        Результат тестов:
        STDOUT:
        {state.get("test_stdout")}
        STDERR:
        {state.get("test_stderr")}

        ВАЖНО: Если ошибка в тестах связана с тем, что код теперь корректно выбрасывает исключение (например, ValueError, KeyError, NotFound), ты ОБЯЗАН обновить тест, используя конструкцию `with pytest.raises(ОжидаемоеИсключение):`.
        Не пытайся "починить" тест, удаляя выброс исключения из основного кода, если это соответствует бизнес-логике.

        Верни исправление строго в ДВУХ блоках:
        ```fix
        FILE: <путь_к_файлу>
        <<<<<<< SEARCH
        <старый код>
        =======
        <новый код>
        >>>>>>> REPLACE
        ```
        ```test
        FILE: <путь_к_файлу_теста>
        import pytest
        # твой обновленный тест, проверяющий как успешный сценарий, так и ожидаемое исключение через pytest.raises
        ```
    """

    prompt_path = os.path.join(state["logs_dir"], f"llm_prompt_{attempt}.md")
    with open(prompt_path, "w", encoding="utf-8") as f:
        f.write(prompt)

    return {
        "fix_attempt": attempt,
        "llm_prompt": prompt,
    }


# --- Роутер ---
def should_continue_to_context(state: FixOpsState):

    if state["analysis_result"].get("resolved_node") is None:
        return "end"

    return "build_context"


def should_run_tests(state: FixOpsState):

    if not state.get("fix_applied", False):
        attempt = state.get("fix_attempt", 0)
        max_attempts = state.get("max_fix_attempts", settings.analysis.MAX_FIX_ATTEMPTS)
        if attempt >= max_attempts:
            return "failed"
        return "fix_error"
    return "run_tests"


def should_retry(state: FixOpsState):

    if state.get("tests_passed", False) and state.get("reproduction_passed", False):
        return "success"

    if state.get("test_result_type") == "INFRA_FAILURE":
        app_logger.bind(event="workflow_step", operation="router").error("Infrastructure failure detected, stopping.")
        return "failed"

    attempt = state.get("fix_attempt", 0)
    max_attempts = state.get("max_fix_attempts", 5)

    if attempt >= max_attempts:
        return "failed"

    return "retry"


# --- Создание графа ---
def create_workflow():

    workflow = StateGraph(FixOpsState)

    workflow.add_node("indexer", indexer_node)
    workflow.add_node("graph_builder", graph_builder_node)
    workflow.add_node("error_analyzer", error_analyzer_node)
    workflow.add_node("context_builder", context_builder_node)
    workflow.add_node("llm", handle_fix_request)
    workflow.add_node("apply_fix", apply_fix_node)
    workflow.add_node("run_tests", run_tests_node)
    workflow.add_node("prepare_retry", prepare_retry_node)

    workflow.set_entry_point("indexer")

    workflow.add_edge("indexer", "graph_builder")
    workflow.add_edge("graph_builder", "error_analyzer")

    workflow.add_conditional_edges(
        "error_analyzer",
        should_continue_to_context,
        {
            "build_context": "context_builder",
            "end": END,
        },
    )

    workflow.add_edge("context_builder", "llm")
    workflow.add_edge("llm", "apply_fix")

    workflow.add_conditional_edges(
        "apply_fix",
        should_run_tests,
        {
            "run_tests": "run_tests",
            "fix_error": "prepare_retry",
            "failed": END,
        },
    )

    workflow.add_conditional_edges(
        "run_tests",
        should_retry,
        {
            "success": END,
            "retry": "prepare_retry",
            "failed": END,
        },
    )

    workflow.add_edge("prepare_retry", "llm")

    return workflow.compile()
