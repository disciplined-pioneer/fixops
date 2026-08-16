"""
code_intel — FixOps Code Intelligence.

Публичный ООП-интерфейс пайплайна:

  indexer           ProjectIndexer   — AST-индексация проекта
  resolver          ProjectIndex, CallResolver — резолвинг вызовов
  graph             CallGraph, GraphBuilder — граф вызовов
  tracer            RuntimeTracer   — runtime-трассировка
  error_analyzer    ErrorAnalyzer   — ошибка из лога -> цепочка причинности
  context_builder   ContextBuilder  — координаты -> реальный код -> промпт LLM
"""

from .indexer import (
    ProjectIndexer,
    CallSite,
    FunctionInfo,
    ClassInfo,
    ModuleInfo,
    scan_project,
    to_dict,
)
from .resolver import (
    ProjectIndex,
    CallResolver,
    resolve_call,
)
from .graph import (
    Edge,
    CallGraph,
    GraphBuilder,
    build_graph,
    merge_runtime_edges,
)
from .tracer import RuntimeTracer
from .error_analyzer import ErrorAnalyzer, analyze_error, render_chain_text
from .context_builder import ContextBuilder, build_llm_context, render_llm_prompt

__all__ = [
    "ProjectIndexer",
    "CallSite",
    "FunctionInfo",
    "ClassInfo",
    "ModuleInfo",
    "scan_project",
    "to_dict",
    "ProjectIndex",
    "CallResolver",
    "resolve_call",
    "Edge",
    "CallGraph",
    "GraphBuilder",
    "build_graph",
    "merge_runtime_edges",
    "RuntimeTracer",
    "ErrorAnalyzer",
    "analyze_error",
    "render_chain_text",
    "ContextBuilder",
    "build_llm_context",
    "render_llm_prompt",
]